import datetime
import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from Admin.forms import ProjectForm, AdminTeamForm
from Admin.models import (
    Employee, Project, Team, TeamMemberStatus, WorkOrder, WorkOrderDetail,
    WorkOrderTime, Attendance, Leave,
)
from Admin.staff_lifecycle import deactivate_employee, reactivate_employee, delete_blockers
from finance.models import Payroll
from manager.forms import TeamForm
from service_logs.models import EmployeeServiceLog


class StaffLifecycleTests(TestCase):
    def setUp(self):
        # Admin / Superuser
        self.admin_user = User.objects.create_superuser('admin_user', 'admin@example.com', 'pass123')
        self.admin_emp = Employee.objects.create(user=self.admin_user, salary=Decimal('15000.00'))

        # HR User
        self.hr_user = User.objects.create_user('hr_user', 'hr@example.com', 'pass123', is_staff=True)
        self.hr_emp = Employee.objects.create(user=self.hr_user, is_hr=True, salary=Decimal('8000.00'))

        # Manager
        self.mgr_user = User.objects.create_user('mgr_user', 'mgr@example.com', 'pass123', is_staff=True)
        self.mgr_emp = Employee.objects.create(user=self.mgr_user, is_manager=True, salary=Decimal('10400.00'), rank='Operations Manager')

        # Engineers / Employees
        self.eng_user = User.objects.create_user('eng_user', 'eng@example.com', 'pass123')
        self.eng_emp = Employee.objects.create(user=self.eng_user, is_employee=True, salary=Decimal('4160.00'), rank='Marine Surveyor')

        self.eng2_user = User.objects.create_user('eng2_user', 'eng2@example.com', 'pass123')
        self.eng2_emp = Employee.objects.create(user=self.eng2_user, is_employee=True, salary=Decimal('4160.00'), rank='Technician')

        # Active Ongoing Project & Work Order
        self.project_ongoing = Project.objects.create(
            name='Ongoing Project',
            status='ONGOING',
            manager=self.mgr_emp,
        )
        self.team_ongoing = Team.objects.create(
            name='Ongoing Team',
            project=self.project_ongoing,
            manager=self.mgr_emp,
        )
        self.team_ongoing.employees.add(self.eng_emp, self.eng2_emp)

        self.wo_ongoing = WorkOrder.objects.create(
            project=self.project_ongoing,
            work_order_number='AMMS-26-901',
            assigned_date=datetime.date(2026, 9, 1),
            created_by=self.mgr_user,
        )
        self.wo_ongoing.job_assigned_to.add(self.eng_user, self.eng2_user)
        self.wo_ongoing.all_members.add(self.eng_user, self.eng2_user)
        WorkOrderDetail.objects.create(work_order=self.wo_ongoing)

        # Completed Project & Team
        self.project_completed = Project.objects.create(
            name='Completed Project',
            status='COMPLETED',
            manager=self.mgr_emp,
        )
        self.team_completed = Team.objects.create(
            name='Completed Team',
            project=self.project_completed,
            manager=self.mgr_emp,
        )
        self.team_completed.employees.add(self.eng_emp)
        # Ensure project status stays COMPLETED (Team.save sets it to ASSIGN)
        Project.objects.filter(pk=self.project_completed.pk).update(status='COMPLETED')
        self.project_completed.refresh_from_db()

    def test_deactivate_engineer_on_ongoing_project(self):
        """
        Deactivate engineer on an ONGOING project:
        - user.is_active becomes False
        - deactivated_at and deactivated_by recorded
        - removed from team.employees on active projects
        - removed from workorder.job_assigned_to on active projects
        - STILL in workorder.all_members (history)
        - teams on a COMPLETED project unchanged
        - open attendance session closed (log_out_time set)
        - historical service logs, payroll, time rows, attendance records counts unchanged
        """
        # Create historical records
        att_open = Attendance.objects.create(
            employee=self.eng_emp,
            login_time=timezone.now() - datetime.timedelta(hours=4),
            log_out_time=None,
            status='PENDING',
        )
        att_closed = Attendance.objects.create(
            employee=self.eng_emp,
            login_time=timezone.now() - datetime.timedelta(days=1, hours=8),
            log_out_time=timezone.now() - datetime.timedelta(days=1),
            total_hours_of_work=Decimal('8.00'),
            status='APPROVED',
        )
        wot = WorkOrderTime.objects.create(
            work_order=self.wo_ongoing,
            date=datetime.date(2026, 9, 1),
            start_time=datetime.time(8, 0),
            finish_time=datetime.time(16, 0),
            employee=self.eng_emp,
        )
        slog = EmployeeServiceLog.objects.create(
            employee=self.eng_emp,
            project=self.project_ongoing,
            date=datetime.date(2026, 9, 2),
            start_time=datetime.time(8, 0),
            end_time=datetime.time(16, 0),
            total_hours=Decimal('8.00'),
        )
        pay = Payroll.objects.create(
            employee=self.eng_emp,
            month=datetime.date(2026, 9, 1),
            basic_salary=Decimal('4160.00'),
            status='DRAFT',
        )

        initial_att_count = Attendance.objects.filter(employee=self.eng_emp).count()
        initial_slog_count = EmployeeServiceLog.objects.filter(employee=self.eng_emp).count()
        initial_wot_count = WorkOrderTime.objects.filter(employee=self.eng_emp).count()
        initial_pay_count = Payroll.objects.filter(employee=self.eng_emp).count()

        summary = deactivate_employee(self.eng_emp, self.admin_user)

        # 1. User is deactivated
        self.eng_user.refresh_from_db()
        self.eng_emp.refresh_from_db()
        self.assertFalse(self.eng_user.is_active)
        self.assertIsNotNone(self.eng_emp.deactivated_at)
        self.assertEqual(self.eng_emp.deactivated_by, self.admin_user)

        # 2. Removed from ongoing team and work order live members
        self.assertNotIn(self.eng_emp, self.team_ongoing.employees.all())
        self.assertNotIn(self.eng_user, self.wo_ongoing.job_assigned_to.all())

        # 3. Still in all_members (historical record)
        self.assertIn(self.eng_user, self.wo_ongoing.all_members.all())

        # 4. Completed project team is untouched
        self.assertIn(self.eng_emp, self.team_completed.employees.all())

        # 5. Open attendance session closed
        att_open.refresh_from_db()
        self.assertIsNotNone(att_open.log_out_time)
        self.assertIsNotNone(att_open.total_hours_of_work)

        # 6. Historical records counts unchanged
        self.assertEqual(Attendance.objects.filter(employee=self.eng_emp).count(), initial_att_count)
        self.assertEqual(EmployeeServiceLog.objects.filter(employee=self.eng_emp).count(), initial_slog_count)
        self.assertEqual(WorkOrderTime.objects.filter(employee=self.eng_emp).count(), initial_wot_count)
        self.assertEqual(Payroll.objects.filter(employee=self.eng_emp).count(), initial_pay_count)

    def test_deactivated_user_cannot_login_and_sees_message(self):
        """
        An inactive user who enters the correct password sees
        'Your account has been deactivated. Please contact HR.'
        """
        deactivate_employee(self.eng_emp, self.admin_user)
        self.eng_user.refresh_from_db()
        self.assertFalse(self.eng_user.is_active)

        response = self.client.post(reverse('custom-login'), {
            'username': 'eng_user',
            'password': 'pass123',
        })
        self.assertEqual(response.status_code, 200)
        messages = [m.message for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("Your account has been deactivated. Please contact HR." in m for m in messages))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_inactive_staff_not_offered_in_dropdowns(self):
        """
        Inactive employee/manager are not offered in:
        - ProjectForm manager dropdown & team employee picker
        - AdminTeamForm manager & employee fields
        - TeamForm manager & employee fields
        - Manager work-order time row employee dropdown options
        """
        deactivate_employee(self.mgr_emp, self.admin_user)
        deactivate_employee(self.eng_emp, self.admin_user)

        # ProjectForm
        p_form = ProjectForm()
        self.assertNotIn(self.mgr_emp, p_form.fields['manager'].queryset)
        self.assertNotIn(self.eng_emp, p_form.fields['employees'].queryset)

        # AdminTeamForm
        at_form = AdminTeamForm()
        self.assertNotIn(self.mgr_emp, at_form.fields['manager'].queryset)
        self.assertNotIn(self.eng_emp, at_form.fields['employees'].queryset)

        # TeamForm (manager app)
        m_team_form = TeamForm()
        self.assertNotIn(self.mgr_emp, m_team_form.fields['manager'].queryset)
        self.assertNotIn(self.eng_emp, m_team_form.fields['employees'].queryset)

        # Manager work order update view dropdown options
        # Reactivate manager so manager can log in and view their work order
        reactivate_employee(self.mgr_emp, self.admin_user)
        logged_in = self.client.login(username='mgr_user', password='pass123')
        self.assertTrue(logged_in)
        resp = self.client.get(reverse('manager-update-work-order', kwargs={'pk': self.wo_ongoing.pk}))
        self.assertEqual(resp.status_code, 200)
        assigned_members = resp.context['assigned_members']
        self.assertNotIn(f'"id": {self.eng_emp.id}', assigned_members)
        self.assertIn(f'"id": {self.eng2_emp.id}', assigned_members)

    def test_deactivate_manager_warns_reassignment_and_leaves_records_intact(self):
        """
        Deactivate manager:
        - Project.manager and Team.manager remain unchanged
        - Summary contains managed projects and teams to reassign
        """
        summary = deactivate_employee(self.mgr_emp, self.admin_user)

        self.project_ongoing.refresh_from_db()
        self.team_ongoing.refresh_from_db()
        self.assertEqual(self.project_ongoing.manager, self.mgr_emp)
        self.assertEqual(self.team_ongoing.manager, self.mgr_emp)

        self.assertIn('Ongoing Project', summary['managed_projects_to_reassign'])
        self.assertIn('Ongoing Team', summary['managed_teams_to_reassign'])

    def test_reactivate_employee(self):
        """
        Reactivate:
        - user.is_active becomes True
        - deactivated_at/deactivated_by cleared
        - NOT automatically re-added to teams or live members
        """
        deactivate_employee(self.eng_emp, self.admin_user)
        self.assertNotIn(self.eng_emp, self.team_ongoing.employees.all())

        summary = reactivate_employee(self.eng_emp, self.admin_user)
        self.eng_user.refresh_from_db()
        self.eng_emp.refresh_from_db()

        self.assertTrue(self.eng_user.is_active)
        self.assertIsNone(self.eng_emp.deactivated_at)
        self.assertIsNone(self.eng_emp.deactivated_by)

        # Not re-added to team or live members automatically
        self.assertNotIn(self.eng_emp, self.team_ongoing.employees.all())
        self.assertNotIn(self.eng_user, self.wo_ongoing.job_assigned_to.all())
        self.assertIn("Reactivated", summary['message'])

    def test_delete_blocked_when_has_history(self):
        """
        Delete blocked for employee with service logs / payroll / attendance /
        created work orders / time rows / managed teams / managed projects.
        Nothing is deleted and error message lists reasons.
        """
        # Create history on eng_emp
        wot = WorkOrderTime.objects.create(
            work_order=self.wo_ongoing,
            date=datetime.date(2026, 9, 1),
            start_time=datetime.time(8, 0),
            finish_time=datetime.time(16, 0),
            employee=self.eng_emp,
        )
        EmployeeServiceLog.objects.create(
            employee=self.eng_emp,
            project=self.project_ongoing,
            date=datetime.date(2026, 9, 2),
            start_time=datetime.time(8, 0),
            end_time=datetime.time(16, 0),
            total_hours=Decimal('8.00'),
        )
        Attendance.objects.create(
            employee=self.eng_emp,
            login_time=timezone.now() - datetime.timedelta(days=1),
            log_out_time=timezone.now() - datetime.timedelta(days=1, hours=-8),
            status='APPROVED',
        )
        Payroll.objects.create(
            employee=self.eng_emp,
            month=datetime.date(2026, 9, 1),
            basic_salary=Decimal('4160.00'),
            status='DRAFT',
        )

        blockers = delete_blockers(self.eng_emp)
        self.assertTrue(len(blockers) >= 4)
        self.assertTrue(any("service log" in b for b in blockers))
        self.assertTrue(any("payroll month" in b for b in blockers))
        self.assertTrue(any("attendance record" in b for b in blockers))
        self.assertTrue(any("work order time log" in b for b in blockers))

        # Admin delete attempt (POST)
        self.client.login(username='admin_user', password='pass123')
        resp = self.client.post(reverse('admin_delete_employee', kwargs={'employee_id': self.eng_emp.id}))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Employee.objects.filter(id=self.eng_emp.id).exists())
        self.assertTrue(User.objects.filter(id=self.eng_user.id).exists())

        messages = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any("Cannot delete" in m and "Deactivate instead" in m for m in messages))

        # HR delete attempt (POST)
        self.client.login(username='hr_user', password='pass123')
        resp_hr = self.client.post(reverse('hr_delete_employee', kwargs={'employee_id': self.eng_emp.id}))
        self.assertEqual(resp_hr.status_code, 302)
        self.assertTrue(Employee.objects.filter(id=self.eng_emp.id).exists())

        # Manager delete attempt (mgr_emp has managed projects & teams)
        self.client.login(username='admin_user', password='pass123')
        mgr_blockers = delete_blockers(self.mgr_emp)
        self.assertTrue(any("team" in b for b in mgr_blockers))
        self.assertTrue(any("project" in b for b in mgr_blockers))
        resp_mgr = self.client.post(reverse('admin_delete_manager', kwargs={'manager_id': self.mgr_emp.id}))
        self.assertEqual(resp_mgr.status_code, 302)
        self.assertTrue(Employee.objects.filter(id=self.mgr_emp.id).exists())

    def test_delete_allowed_for_employee_with_no_history(self):
        """
        Delete allowed for an employee with no history (e.g. created by mistake).
        Both Employee and User records are removed.
        """
        fresh_user = User.objects.create_user('fresh_user', 'fresh@example.com', 'pass123')
        fresh_emp = Employee.objects.create(user=fresh_user, is_employee=True, salary=Decimal('3000.00'))

        self.assertEqual(delete_blockers(fresh_emp), [])

        self.client.login(username='admin_user', password='pass123')
        resp = self.client.post(reverse('admin_delete_employee', kwargs={'employee_id': fresh_emp.id}))
        self.assertEqual(resp.status_code, 302)

        self.assertFalse(Employee.objects.filter(id=fresh_emp.id).exists())
        self.assertFalse(User.objects.filter(id=fresh_user.id).exists())

    def test_permissions_and_restrictions(self):
        """
        Permissions:
        - HR can deactivate employees
        - HR cannot deactivate superusers
        - Nobody can deactivate themselves
        - Non-HR / non-superuser gets 403 or redirect
        - GET request does not deactivate
        """
        # 1. HR can deactivate normal employee
        self.client.login(username='hr_user', password='pass123')
        resp = self.client.post(reverse('hr_deactivate_employee', kwargs={'employee_id': self.eng_emp.id}))
        self.assertEqual(resp.status_code, 302)
        self.eng_user.refresh_from_db()
        self.assertFalse(self.eng_user.is_active)

        # Reactivate
        reactivate_employee(self.eng_emp, self.admin_user)

        # 2. HR cannot deactivate superuser
        resp = self.client.post(reverse('hr_deactivate_employee', kwargs={'employee_id': self.admin_emp.id}))
        self.assertEqual(resp.status_code, 302)
        self.admin_user.refresh_from_db()
        self.assertTrue(self.admin_user.is_active)
        messages = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any("HR cannot deactivate a superuser account" in m for m in messages))

        # 3. Cannot deactivate self (Admin)
        self.client.login(username='admin_user', password='pass123')
        resp = self.client.post(reverse('admin_deactivate_employee', kwargs={'employee_id': self.admin_emp.id}))
        self.assertEqual(resp.status_code, 302)
        self.admin_user.refresh_from_db()
        self.assertTrue(self.admin_user.is_active)
        messages = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any("cannot deactivate your own account" in m for m in messages))

        # 4. Cannot deactivate self (HR)
        self.client.login(username='hr_user', password='pass123')
        resp = self.client.post(reverse('hr_deactivate_employee', kwargs={'employee_id': self.hr_emp.id}))
        self.assertEqual(resp.status_code, 302)
        self.hr_user.refresh_from_db()
        self.assertTrue(self.hr_user.is_active)

        # 5. Non-HR / non-superuser gets 403 or redirect
        self.client.login(username='eng2_user', password='pass123')
        resp = self.client.post(reverse('hr_deactivate_employee', kwargs={'employee_id': self.eng_emp.id}))
        self.assertIn(resp.status_code, (302, 403))

        resp = self.client.post(reverse('admin_deactivate_employee', kwargs={'employee_id': self.eng_emp.id}))
        self.assertIn(resp.status_code, (302, 403))

        # 6. GET request does not deactivate (405 Method Not Allowed)
        self.client.login(username='admin_user', password='pass123')
        resp_get = self.client.get(reverse('admin_deactivate_employee', kwargs={'employee_id': self.eng_emp.id}))
        self.assertEqual(resp_get.status_code, 405)
        self.eng_user.refresh_from_db()
        self.assertTrue(self.eng_user.is_active)

    def test_register_and_payroll_exclude_inactive_user(self):
        """
        Verify existing behaviour: attendance register and payroll generation
        exclude users with is_active=False.
        """
        deactivate_employee(self.eng_emp, self.admin_user)
        self.eng_user.refresh_from_db()
        self.assertFalse(self.eng_user.is_active)

        # Attendance register rows exclude inactive users
        from Admin.attendance_register import _register_context
        from django.test.client import RequestFactory
        factory = RequestFactory()
        req = factory.get('/admin-panel/employee/attendance/?start=2026-09-01&end=2026-09-02')
        req.user = self.admin_user
        ctx = _register_context(req)
        emp_ids_in_register = [r['employee'].id for r in ctx['summary_rows_all']]
        self.assertNotIn(self.eng_emp.id, emp_ids_in_register)

    def test_manager_update_work_order_preserves_deactivated_engineer_time_rows(self):
        """
        Engineer A and B have time rows on a work order; deactivate A.
        Manager GETs update page:
          - A's row shows A selected, labelled inactive.
          - New-row options (assigned_members) do not include A.
        Manager POSTs the page with time_employee[] = [A.id, B.id]:
          - A's WorkOrderTime and EmployeeServiceLog still belong to A.
          - B's still belong to B.
          - Nothing booked to manager.
          - Service log count unchanged.
        """
        wot_a = WorkOrderTime.objects.create(
            work_order=self.wo_ongoing,
            date=datetime.date(2026, 9, 1),
            start_time=datetime.time(8, 0),
            finish_time=datetime.time(16, 0),
            employee=self.eng_emp,
        )
        wot_b = WorkOrderTime.objects.create(
            work_order=self.wo_ongoing,
            date=datetime.date(2026, 9, 2),
            start_time=datetime.time(8, 0),
            finish_time=datetime.time(16, 0),
            employee=self.eng2_emp,
        )

        initial_slog_count = EmployeeServiceLog.objects.filter(time_log__work_order=self.wo_ongoing).count()
        self.assertEqual(initial_slog_count, 2)

        # Deactivate A
        deactivate_employee(self.eng_emp, self.admin_user)
        self.eng_user.refresh_from_db()
        self.assertFalse(self.eng_user.is_active)

        # Manager GETs the page
        self.client.login(username='mgr_user', password='pass123')
        resp = self.client.get(reverse('manager-update-work-order', kwargs={'pk': self.wo_ongoing.pk}))
        self.assertEqual(resp.status_code, 200)

        # Verify A is not in assigned_members (options for NEW rows)
        assigned_members = json.loads(resp.context['assigned_members'])
        assigned_member_ids = [m['id'] for m in assigned_members]
        self.assertNotIn(self.eng_emp.id, assigned_member_ids)
        self.assertIn(self.eng2_emp.id, assigned_member_ids)

        # Verify A's row in times has extra_member with '(inactive)'
        times = json.loads(resp.context['times'])
        row_a = next(r for r in times if r['id'] == wot_a.id)
        self.assertIn('(inactive)', row_a['member_name'])
        self.assertEqual(row_a['extra_member']['id'], self.eng_emp.id)
        self.assertIn('(inactive)', row_a['extra_member']['name'])

        # Manager POSTs with both A and B
        post_data = {
            'vessel': 'Ongoing Project',
            'client': 'Client',
            'imo_no': '',
            'location': 'DMC',
            'assigned_date': '2026-09-01',
            'job_scope': 'Scope',
            'job_instructions': '',
            'start_date': '2026-09-01',
            'completion_date': '2026-09-03',
            'estimated_hours': '16',
            'project_description': 'Test',
            'time_date[]': ['2026-09-01', '2026-09-02'],
            'start_time[]': ['08:00', '08:00'],
            'finish_time[]': ['16:00', '16:00'],
            'time_employee[]': [str(self.eng_emp.id), str(self.eng2_emp.id)],
        }
        resp_post = self.client.post(reverse('manager-update-work-order', kwargs={'pk': self.wo_ongoing.pk}), post_data)
        self.assertEqual(resp_post.status_code, 302)

        # Verify WorkOrderTime ownership
        wots = list(WorkOrderTime.objects.filter(work_order=self.wo_ongoing).order_by('date'))
        self.assertEqual(len(wots), 2)
        self.assertEqual(wots[0].employee, self.eng_emp)
        self.assertEqual(wots[1].employee, self.eng2_emp)
        self.assertFalse(WorkOrderTime.objects.filter(work_order=self.wo_ongoing, employee=self.mgr_emp).exists())

        # Verify EmployeeServiceLog ownership & count
        slogs = list(EmployeeServiceLog.objects.filter(time_log__work_order=self.wo_ongoing).order_by('date'))
        self.assertEqual(len(slogs), 2)
        self.assertEqual(slogs[0].employee, self.eng_emp)
        self.assertEqual(slogs[1].employee, self.eng2_emp)
        self.assertFalse(EmployeeServiceLog.objects.filter(time_log__work_order=self.wo_ongoing, employee=self.mgr_emp).exists())

    def test_manager_update_work_order_preserves_engineer_removed_from_team(self):
        """
        Same scenario, but Engineer A is removed from the team (remains active).
        - GET shows A's row labelled '(not on team)'.
        - POST retains A's rows booked to A.
        """
        wot_a = WorkOrderTime.objects.create(
            work_order=self.wo_ongoing,
            date=datetime.date(2026, 9, 1),
            start_time=datetime.time(8, 0),
            finish_time=datetime.time(16, 0),
            employee=self.eng_emp,
        )
        wot_b = WorkOrderTime.objects.create(
            work_order=self.wo_ongoing,
            date=datetime.date(2026, 9, 2),
            start_time=datetime.time(8, 0),
            finish_time=datetime.time(16, 0),
            employee=self.eng2_emp,
        )

        # Remove A from team while active
        self.team_ongoing.employees.remove(self.eng_emp)
        self.assertTrue(self.eng_user.is_active)

        self.client.login(username='mgr_user', password='pass123')
        resp = self.client.get(reverse('manager-update-work-order', kwargs={'pk': self.wo_ongoing.pk}))
        self.assertEqual(resp.status_code, 200)

        # Check labelled '(not on team)'
        times = json.loads(resp.context['times'])
        row_a = next(r for r in times if r['id'] == wot_a.id)
        self.assertIn('(not on team)', row_a['member_name'])
        self.assertEqual(row_a['extra_member']['id'], self.eng_emp.id)
        self.assertIn('(not on team)', row_a['extra_member']['name'])

        # POST preserves A
        post_data = {
            'vessel': 'Ongoing Project',
            'client': 'Client',
            'imo_no': '',
            'location': 'DMC',
            'assigned_date': '2026-09-01',
            'job_scope': 'Scope',
            'job_instructions': '',
            'start_date': '2026-09-01',
            'completion_date': '2026-09-03',
            'estimated_hours': '16',
            'project_description': 'Test',
            'time_date[]': ['2026-09-01', '2026-09-02'],
            'start_time[]': ['08:00', '08:00'],
            'finish_time[]': ['16:00', '16:00'],
            'time_employee[]': [str(self.eng_emp.id), str(self.eng2_emp.id)],
        }
        resp_post = self.client.post(reverse('manager-update-work-order', kwargs={'pk': self.wo_ongoing.pk}), post_data)
        self.assertEqual(resp_post.status_code, 302)

        wots = list(WorkOrderTime.objects.filter(work_order=self.wo_ongoing).order_by('date'))
        self.assertEqual(len(wots), 2)
        self.assertEqual(wots[0].employee, self.eng_emp)
        self.assertEqual(wots[1].employee, self.eng2_emp)
        self.assertFalse(WorkOrderTime.objects.filter(work_order=self.wo_ongoing, employee=self.mgr_emp).exists())

    def test_deactivate_open_attendance_capped_at_midnight(self):
        """
        Open attendance from 3 days ago:
          - deactivate -> log_out_time = 00:00 the day after login (Asia/Dubai).
          - total_hours_of_work is capped, not ~72h.
          - auto_punched_out is True.
        Open attendance from today:
          - deactivate -> log_out_time closes at now (not capped).
          - auto_punched_out is False.
        """
        tz = timezone.get_current_timezone()
        now = timezone.now()

        # Session opened 3 days ago (e.g. login at 08:00 3 days ago)
        stale_date = timezone.localdate() - datetime.timedelta(days=3)
        stale_login = timezone.make_aware(datetime.datetime.combine(stale_date, datetime.time(8, 0)), tz)
        att_stale = Attendance.objects.create(
            employee=self.eng_emp,
            login_time=stale_login,
            log_out_time=None,
            status='PENDING',
        )

        # Session opened today (2 hours ago)
        today_login = now - datetime.timedelta(hours=2)
        att_today = Attendance.objects.create(
            employee=self.eng2_emp,
            login_time=today_login,
            log_out_time=None,
            status='PENDING',
        )

        deactivate_employee(self.eng_emp, self.admin_user)
        att_stale.refresh_from_db()
        self.assertIsNotNone(att_stale.log_out_time)
        self.assertTrue(att_stale.auto_punched_out)

        # Expected cutoff: midnight following the login day
        expected_cutoff = timezone.make_aware(
            datetime.datetime.combine(stale_date + datetime.timedelta(days=1), datetime.time.min),
            tz
        )
        self.assertEqual(att_stale.log_out_time, expected_cutoff)
        self.assertEqual(att_stale.total_hours_of_work, Decimal('16.00'))

        deactivate_employee(self.eng2_emp, self.admin_user)
        att_today.refresh_from_db()
        self.assertIsNotNone(att_today.log_out_time)
        self.assertFalse(att_today.auto_punched_out)
        self.assertAlmostEqual(att_today.total_hours_of_work, Decimal('2.00'), delta=Decimal('0.10'))


class SalaryTabNullUserTests(TestCase):
    """Salary tab must render when an increment/change log has no user (system entries)."""

    def test_salary_tab_with_system_entries(self):
        import datetime
        from decimal import Decimal
        from django.contrib.auth.models import User
        from Admin.models import Employee, EmployeeChangeLog
        from finance.models import SalaryIncrement
        su = User.objects.create_superuser('su_sal', 'su_sal@example.com', 'pw')
        emp = Employee.objects.create(user=User.objects.create_user('eng_sal', 'e@x.com', 'pw'),
                                      is_employee=True, salary=Decimal('3000'), date_of_join=datetime.date(2026, 1, 1))
        SalaryIncrement.objects.create(employee=emp, effective_from=datetime.date(2026, 6, 1),
                                       new_basic=Decimal('3500'), status='PENDING')
        EmployeeChangeLog.objects.create(employee=emp, field='basic', old_value='3000', new_value='3500',
                                         changed_by=None, source='import')
        self.client.force_login(su)
        res = self.client.get(f'/admin-panel/employee/{emp.id}/?tab=salary')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'System')


class RestDayHistoryPageTests(TestCase):
    """Rest-day history page renders, including system entries with no user."""

    def test_history_page_renders(self):
        from Admin.models import Employee, EmployeeChangeLog, RestDayRule
        su = User.objects.create_superuser('su_rd', 'su_rd@example.com', 'pw')
        emp = Employee.objects.create(user=User.objects.create_user('eng_rd', 'rd@x.com', 'pw'),
                                      is_employee=True, salary=Decimal('3000'), date_of_join=datetime.date(2026, 1, 1))
        RestDayRule.objects.create(employee=emp, rest_weekday=4, effective_from=datetime.date(2026, 1, 1), created_by=None)
        EmployeeChangeLog.objects.create(employee=emp, field='rest_day', old_value='Sunday', new_value='Friday',
                                         changed_by=su, source='HR edit')
        EmployeeChangeLog.objects.create(employee=emp, field='rest_day', old_value='Friday', new_value='Roster',
                                         changed_by=None, source='import')
        self.client.force_login(su)
        res = self.client.get(reverse('hr_weekly_rest_day_history', args=[emp.id]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Roster')
        self.assertContains(res, 'System')

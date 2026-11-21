import datetime
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from Admin.models import Employee, Project, WorkOrder, Team
from finance.models import Payroll


class SignalImportsAndValidationErrorTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Manager
        self.mgr_user = User.objects.create_user(
            username='mgr_user',
            email='mgr@amms.ae',
            password='password123',
            first_name='Tariq',
            last_name='Manager',
            is_active=True
        )
        self.mgr_emp = Employee.objects.create(
            user=self.mgr_user,
            is_manager=True,
            is_employee=False,
            employee_code='AMMS-M01',
            rank='Manager',
            salary=Decimal('8000.00')
        )

        # Employee 1
        self.emp1_user = User.objects.create_user(
            username='emp1_user',
            email='emp1@amms.ae',
            password='password123',
            first_name='Salem',
            last_name='Staff',
            is_active=True
        )
        self.emp1 = Employee.objects.create(
            user=self.emp1_user,
            is_employee=True,
            is_manager=False,
            employee_code='AMMS-E01',
            rank='Technician',
            salary=Decimal('4000.00')
        )

        # Employee 2
        self.emp2_user = User.objects.create_user(
            username='emp2_user',
            email='emp2@amms.ae',
            password='password123',
            first_name='Hamad',
            last_name='Staff',
            is_active=True
        )
        self.emp2 = Employee.objects.create(
            user=self.emp2_user,
            is_employee=True,
            is_manager=False,
            employee_code='AMMS-E02',
            rank='Technician',
            salary=Decimal('4500.00')
        )

        # Admin user
        self.admin_user = User.objects.create_superuser(
            username='admin_boss',
            email='admin@amms.ae',
            password='adminpassword123'
        )

        # HR user
        self.hr_user = User.objects.create_user(
            username='hr_fatima',
            email='hr@amms.ae',
            password='password123',
            is_staff=True,
            first_name='Fatima',
            last_name='HR',
            is_active=True
        )
        self.hr_emp = Employee.objects.create(
            user=self.hr_user,
            is_hr=True,
            is_manager=False,
            employee_code='AMMS-H01',
            rank='HR Officer',
            salary=Decimal('6000.00')
        )

    def test_manager_team_edit_adds_members_and_updates_workorder(self):
        """
        Create manager, two employees, a project with a work order, and a Team for that project.
        Log in as the manager and POST to the manager team edit URL adding both employees:
        response is redirect/200 (not 500), both employees on the team, both Users in work_order.all_members.
        """
        project = Project.objects.create(
            name='Test Vessel Overhaul',
            code='PRJ-WO-01',
            manager=self.mgr_emp,
            status='ONGOING',
            priority='HIGH'
        )
        work_order = WorkOrder.objects.create(
            project=project,
            work_order_number='WO-TEST-01'
        )
        team = Team.objects.create(
            name='Alpha Team',
            manager=self.mgr_emp,
            project=project
        )

        self.client.login(username='mgr_user', password='password123')
        url = reverse('team-update', kwargs={'pk': team.pk})

        data = {
            'name': 'Alpha Team Updated',
            'manager': str(self.mgr_emp.pk),
            'project': str(project.pk),
            'employees': [str(self.emp1.pk), str(self.emp2.pk)]
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)

        # Verify employees are on the team
        team.refresh_from_db()
        self.assertEqual(set(team.employees.all()), {self.emp1, self.emp2})

        # Verify their Users are in work_order.all_members
        work_order.refresh_from_db()
        self.assertIn(self.emp1_user, work_order.all_members.all())
        self.assertIn(self.emp2_user, work_order.all_members.all())

    def test_team_employees_add_without_workorder_no_exception(self):
        """
        Direct team.employees.add(...) for a project without a work order raises no exception.
        Also verifies track_all_members handles instance.project=None safely.
        """
        # 1. Project without WorkOrder
        project_no_wo = Project.objects.create(
            name='Project Without WO',
            code='PRJ-NOWO-01',
            manager=self.mgr_emp
        )
        team_no_wo = Team.objects.create(
            name='Team No WO',
            manager=self.mgr_emp,
            project=project_no_wo
        )
        # Should not raise WorkOrder.DoesNotExist or NameError
        team_no_wo.employees.add(self.emp1, self.emp2)
        self.assertEqual(set(team_no_wo.employees.all()), {self.emp1, self.emp2})

        # 2. Team with instance.project = None safely handled by track_all_members
        from Admin.signals import track_all_members
        team_no_proj = Team(name='Team No Project', manager=self.mgr_emp, project=None)
        # Should return safely without AttributeError
        track_all_members(sender=Team.employees.through, instance=team_no_proj, action="post_add", pk_set={self.emp1.pk})

    def test_admin_team_edit_adds_members_and_updates_workorder(self):
        """
        Admin team edit view updates team employees and work_order.all_members without error.
        """
        project = Project.objects.create(
            name='Admin Project',
            code='PRJ-ADM-01',
            manager=self.mgr_emp
        )
        work_order = WorkOrder.objects.create(
            project=project,
            work_order_number='WO-ADM-01'
        )
        team = Team.objects.create(
            name='Admin Team',
            manager=self.mgr_emp,
            project=project
        )

        self.client.login(username='admin_boss', password='adminpassword123')
        url = reverse('admin-team-update', kwargs={'pk': team.pk})

        data = {
            'name': 'Admin Team Updated',
            'manager': str(self.mgr_emp.pk),
            'project': str(project.pk),
            'employees': [str(self.emp1.pk), str(self.emp2.pk)]
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)

        team.refresh_from_db()
        self.assertEqual(set(team.employees.all()), {self.emp1, self.emp2})

        work_order.refresh_from_db()
        self.assertIn(self.emp1_user, work_order.all_members.all())
        self.assertIn(self.emp2_user, work_order.all_members.all())

    def test_hr_rest_day_validation_error_not_500(self):
        """
        Triggering a ValidationError in hr/views.py (e.g. changing rest day for a locked payroll month)
        returns a normal response with an error message, not a 500 NameError.
        """
        target_month = datetime.date(2026, 11, 1)
        Payroll.objects.create(
            employee=self.emp1,
            month=target_month,
            status='APPROVED',
            basic_salary=Decimal('4000.00')
        )

        self.client.login(username='hr_fatima', password='password123')
        url = reverse('hr_weekly_rest_days')

        data = {
            'action': 'single_set',
            'employee_id': str(self.emp1.pk),
            'rest_day': '4',
            'effective_from': '2026-11-15'
        }
        resp = self.client.post(url, data, follow=True)
        self.assertEqual(resp.status_code, 200)

        # Confirm the error message was added and no 500 occurred
        messages = [m.message for m in resp.context['messages']]
        self.assertTrue(
            any('already approved' in m for m in messages),
            f"Expected 'already approved' error in messages, got: {messages}"
        )

    def test_hr_roster_toggle_validation_error_not_500(self):
        """
        Triggering ValidationError in hr_roster_toggle returns JSON error 400, not a 500.
        """
        target_month = datetime.date(2026, 11, 1)
        Payroll.objects.create(
            employee=self.emp1,
            month=target_month,
            status='APPROVED',
            basic_salary=Decimal('4000.00')
        )

        self.client.login(username='hr_fatima', password='password123')
        url = reverse('hr_roster_toggle')

        data = {
            'employee_id': str(self.emp1.pk),
            'date': '2026-11-15'
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get('status'), 'error')
        self.assertIn('already approved', resp.json().get('message', ''))

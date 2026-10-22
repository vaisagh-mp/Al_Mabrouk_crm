from datetime import date, time
from decimal import Decimal
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from Admin.models import Employee, Project, WorkOrder, WorkOrderDetail, WorkOrderTime
from service_logs.models import EmployeeServiceLog
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.template.loader import render_to_string


class ManagerUpdateWorkOrderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='test_manager', password='password123')
        self.employee = Employee.objects.create(
            user=self.user,
            is_manager=True,
            is_employee=True,
            salary=Decimal('5000.00'),
        )
        self.project = Project.objects.create(
            name='Test Project',
            client_name='Test Client',
            vessel_name='MV Test Vessel',
            category='AT_BERTH',
            status='ONGOING',
            manager=self.employee,
        )
        self.work_order = WorkOrder.objects.create(
            project=self.project,
            work_order_number='AMMS-26-001',
            client='Test Client',
            vessel='MV Test Vessel',
            assigned_date=date(2026, 6, 1),
        )
        WorkOrderDetail.objects.create(work_order=self.work_order)

    def test_manager_update_work_order_with_time_log(self):
        self.client.force_login(self.user)
        url = reverse('manager-update-work-order', kwargs={'pk': self.work_order.pk})
        post_data = {
            'vessel': 'MV Test Vessel',
            'client': 'Test Client',
            'imo_no': '9123456',
            'location': 'Dubai Drydocks',
            'assigned_date': '2026-06-01',
            'job_scope': 'Overhaul engine',
            'job_instructions': 'Standard procedure',
            'project_description': 'Test project desc',
            'start_date': '2026-06-01',
            'completion_date': '2026-06-02',
            'estimated_hours': '10',
            'time_date[]': ['2026-06-01'],
            'start_time[]': ['08:00'],
            'finish_time[]': ['17:00'],
        }
        response = self.client.post(url, post_data)

        # 1. Redirect to view_work_order
        expected_redirect = reverse('view_work_order', kwargs={'pk': self.work_order.pk})
        self.assertRedirects(response, expected_redirect)

        # 2. No error message
        messages = list(get_messages(response.wsgi_request))
        error_messages = [m.message for m in messages if m.level_tag == 'error']
        self.assertEqual(error_messages, [], f"Unexpected error messages: {error_messages}")

        # 3. WorkOrderTime created
        time_logs = WorkOrderTime.objects.filter(work_order=self.work_order)
        self.assertEqual(time_logs.count(), 1)
        time_log = time_logs.first()
        self.assertEqual(time_log.date, date(2026, 6, 1))
        self.assertEqual(time_log.employee, self.employee)

        # 4. EmployeeServiceLog created for it
        service_logs = EmployeeServiceLog.objects.filter(time_log=time_log)
        self.assertEqual(service_logs.count(), 1)
        service_log = service_logs.first()
        self.assertEqual(service_log.employee, self.employee)
        self.assertEqual(service_log.project, self.project)

    def test_manager_update_work_order_without_time_logs(self):
        self.client.force_login(self.user)
        url = reverse('manager-update-work-order', kwargs={'pk': self.work_order.pk})
        post_data = {
            'vessel': 'MV Test Vessel',
            'client': 'Test Client',
            'assigned_date': '2026-06-01',
            'job_scope': 'Inspection only',
            'start_date': '2026-06-01',
            'completion_date': '2026-06-02',
        }
        response = self.client.post(url, post_data)
        expected_redirect = reverse('view_work_order', kwargs={'pk': self.work_order.pk})
        self.assertRedirects(response, expected_redirect)

        messages = list(get_messages(response.wsgi_request))
        error_messages = [m.message for m in messages if m.level_tag == 'error']
        self.assertEqual(error_messages, [])
        self.assertEqual(WorkOrderTime.objects.filter(work_order=self.work_order).count(), 0)


class WorkOrderTimeTableTests(TestCase):
    def setUp(self):
        # Manager user
        self.manager_user = User.objects.create_user(
            username='mgr_user', password='password123',
            first_name='Marge', last_name='Simpson'
        )
        self.manager_emp = Employee.objects.create(
            user=self.manager_user,
            is_manager=True,
            is_employee=True,
            salary=Decimal('6000.00'),
        )
        # Employee A
        self.user_a = User.objects.create_user(
            username='emp_a_user', password='password123',
            first_name='Ahmed', last_name='Khan'
        )
        self.emp_a = Employee.objects.create(
            user=self.user_a,
            is_employee=True,
            salary=Decimal('4000.00'),
        )
        # Employee B
        self.user_b = User.objects.create_user(
            username='emp_b_user', password='password123',
            first_name='Bob', last_name='Smith'
        )
        self.emp_b = Employee.objects.create(
            user=self.user_b,
            is_employee=True,
            salary=Decimal('4200.00'),
        )

        self.project = Project.objects.create(
            name='Test Marine Refit',
            client_name='Test Client Corp',
            vessel_name='MV Oceanic',
            category='AT_BERTH',
            status='ONGOING',
            manager=self.manager_emp,
        )
        self.work_order = WorkOrder.objects.create(
            project=self.project,
            work_order_number='AMMS-26-099',
            client='Test Client Corp',
            vessel='MV Oceanic',
            assigned_date=date(2026, 6, 1),
        )
        self.work_order.job_assigned_to.add(self.user_a, self.user_b)
        self.work_order.all_members.add(self.user_a, self.user_b)
        self.work_order_detail = WorkOrderDetail.objects.create(work_order=self.work_order)

    def test_two_members_time_rows_and_subtotals(self):
        # Two members on one work order, each with a time row
        WorkOrderTime.objects.create(
            work_order=self.work_order,
            date=date(2026, 6, 1),
            start_time=time(8, 0),
            finish_time=time(12, 0),
            employee=self.emp_a,
        )
        WorkOrderTime.objects.create(
            work_order=self.work_order,
            date=date(2026, 6, 1),
            start_time=time(13, 0),
            finish_time=time(17, 0),
            employee=self.emp_b,
        )
        self.client.force_login(self.manager_user)
        response = self.client.get(reverse('view_work_order', kwargs={'pk': self.work_order.pk}))
        self.assertEqual(response.status_code, 200)

        # Manager view shows both names in the correct rows
        content = response.content.decode('utf-8')
        self.assertIn("Ahmed Khan", content)
        self.assertIn("Bob Smith", content)

        # Plus correct per-member subtotals: "Ahmed Khan — 4.00 h" and "Bob Smith — 4.00 h"
        self.assertIn("Ahmed Khan — 4.00 h", content)
        self.assertIn("Bob Smith — 4.00 h", content)
        self.assertIn("TOTAL HOURS:", content)
        self.assertIn("8.00 hours", content)

    def test_employee_a_saves_preserves_employee_b_rows_and_service_logs(self):
        # Initial row and auto service log for B
        tl_b = WorkOrderTime.objects.create(
            work_order=self.work_order,
            date=date(2026, 6, 1),
            start_time=time(8, 0),
            finish_time=time(12, 0),
            employee=self.emp_b,
        )
        self.assertEqual(EmployeeServiceLog.objects.filter(time_log=tl_b, employee=self.emp_b).count(), 1)

        # Initial row and auto service log for A
        tl_a = WorkOrderTime.objects.create(
            work_order=self.work_order,
            date=date(2026, 6, 1),
            start_time=time(13, 0),
            finish_time=time(15, 0),
            employee=self.emp_a,
        )
        self.assertEqual(EmployeeServiceLog.objects.filter(time_log=tl_a, employee=self.emp_a).count(), 1)

        # Employee A saves their time via employee update
        self.client.force_login(self.user_a)
        url = reverse('employee-update-work-order', kwargs={'pk': self.work_order.pk})
        post_data = {
            'start_date': '2026-06-01',
            'completion_date': '2026-06-02',
            'project_description': 'Updated by A',
            'time_date[]': ['2026-06-02'],
            'start_time[]': ['09:00'],
            'finish_time[]': ['17:00'],
        }
        response = self.client.post(url, post_data)
        self.assertRedirects(response, reverse('employee-view-work-order', kwargs={'pk': self.work_order.pk}))

        # Employee B's rows and service logs still exist
        self.assertEqual(WorkOrderTime.objects.filter(work_order=self.work_order, employee=self.emp_b).count(), 1)
        self.assertEqual(EmployeeServiceLog.objects.filter(time_log=tl_b, employee=self.emp_b).count(), 1)

        # Employee A's row was replaced with the new row
        self.assertEqual(WorkOrderTime.objects.filter(work_order=self.work_order, employee=self.emp_a).count(), 1)
        new_tl_a = WorkOrderTime.objects.filter(work_order=self.work_order, employee=self.emp_a).first()
        self.assertEqual(new_tl_a.date, date(2026, 6, 2))
        self.assertEqual(EmployeeServiceLog.objects.filter(time_log=new_tl_a, employee=self.emp_a).count(), 1)

    def test_manager_saves_with_member_select_b(self):
        self.client.force_login(self.manager_user)
        url = reverse('manager-update-work-order', kwargs={'pk': self.work_order.pk})
        post_data = {
            'vessel': 'MV Oceanic',
            'client': 'Test Client Corp',
            'assigned_date': '2026-06-01',
            'job_scope': 'Repair pumps',
            'start_date': '2026-06-01',
            'completion_date': '2026-06-02',
            'time_date[]': ['2026-06-01'],
            'start_time[]': ['10:00'],
            'finish_time[]': ['16:00'],
            'time_employee[]': [str(self.emp_b.id)],
        }
        response = self.client.post(url, post_data)
        self.assertRedirects(response, reverse('view_work_order', kwargs={'pk': self.work_order.pk}))

        # The row and its auto service log belong to B
        time_logs = WorkOrderTime.objects.filter(work_order=self.work_order)
        self.assertEqual(time_logs.count(), 1)
        tl = time_logs.first()
        self.assertEqual(tl.employee, self.emp_b)

        service_logs = EmployeeServiceLog.objects.filter(time_log=tl)
        self.assertEqual(service_logs.count(), 1)
        sl = service_logs.first()
        self.assertEqual(sl.employee, self.emp_b)

    def test_query_count_for_view_is_constant(self):
        # Create work order 1 with 3 rows (using separate projects since project is OneToOneField)
        proj_3 = Project.objects.create(name='Project 3', manager=self.manager_emp)
        wo_3 = WorkOrder.objects.create(
            project=proj_3,
            work_order_number='AMMS-26-101',
            assigned_date=date(2026, 6, 1),
        )
        for i in range(3):
            WorkOrderTime.objects.create(
                work_order=wo_3,
                date=date(2026, 6, 1),
                start_time=time(8 + i, 0),
                finish_time=time(9 + i, 0),
                employee=self.emp_a if i % 2 == 0 else self.emp_b,
            )

        # Create work order 2 with 10 rows
        proj_10 = Project.objects.create(name='Project 10', manager=self.manager_emp)
        wo_10 = WorkOrder.objects.create(
            project=proj_10,
            work_order_number='AMMS-26-102',
            assigned_date=date(2026, 6, 1),
        )
        for i in range(10):
            WorkOrderTime.objects.create(
                work_order=wo_10,
                date=date(2026, 6, 1),
                start_time=time(8, 0),
                finish_time=time(9, 0),
                employee=self.emp_a if i % 2 == 0 else self.emp_b,
            )

        self.client.force_login(self.manager_user)

        # Measure queries for 3 rows
        with CaptureQueriesContext(connection) as ctx_3:
            res_3 = self.client.get(reverse('view_work_order', kwargs={'pk': wo_3.pk}))
            self.assertEqual(res_3.status_code, 200)

        # Measure queries for 10 rows
        with CaptureQueriesContext(connection) as ctx_10:
            res_10 = self.client.get(reverse('view_work_order', kwargs={'pk': wo_10.pk}))
            self.assertEqual(res_10.status_code, 200)

        self.assertEqual(
            len(ctx_3.captured_queries),
            len(ctx_10.captured_queries),
            f"Query count changed: 3 rows={len(ctx_3.captured_queries)} vs 10 rows={len(ctx_10.captured_queries)}"
        )

    def test_pdf_view_renders_member_column(self):
        WorkOrderTime.objects.create(
            work_order=self.work_order,
            date=date(2026, 6, 1),
            start_time=time(8, 0),
            finish_time=time(12, 0),
            employee=self.emp_a,
        )
        self.client.force_login(self.manager_user)
        response = self.client.get(reverse('download-work-order-pdf', kwargs={'pk': self.work_order.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')

        # Also test the template rendering directly to verify MEMBER header and Ahmed Khan in the table
        time_entries = self.work_order.times.select_related('employee__user').order_by('date', 'employee__user__first_name', 'start_time')
        member_totals = [{'name': 'Ahmed Khan', 'hours': Decimal('4.00')}]
        html = render_to_string('Manager/work_order_pdf.html', {
            'work_order': self.work_order,
            'work_order_detail': self.work_order_detail,
            'time_entries': time_entries,
            'member_totals': member_totals,
            'calculated_hours': 4.0,
        })
        self.assertIn('<th>MEMBER</th>', html)
        self.assertIn('Ahmed Khan', html)
        self.assertIn('Ahmed Khan — 4.00 h', html)

    def test_backfill_work_order_time_employee_command(self):
        from django.core.management import call_command
        from io import StringIO

        # Work order with exactly 1 assigned user
        wo_single = WorkOrder.objects.create(
            project=Project.objects.create(name='Single Proj', manager=self.manager_emp),
            work_order_number='AMMS-26-901',
            assigned_date=date(2026, 6, 1),
        )
        wo_single.job_assigned_to.add(self.user_a)
        tl_single = WorkOrderTime.objects.create(
            work_order=wo_single,
            date=date(2026, 6, 1),
            start_time=time(9, 0),
            finish_time=time(11, 0),
            employee=None,
        )

        # Work order with multiple assigned users
        wo_multi = WorkOrder.objects.create(
            project=Project.objects.create(name='Multi Proj', manager=self.manager_emp),
            work_order_number='AMMS-26-902',
            assigned_date=date(2026, 6, 1),
        )
        wo_multi.job_assigned_to.add(self.user_a, self.user_b)
        tl_multi = WorkOrderTime.objects.create(
            work_order=wo_multi,
            date=date(2026, 6, 1),
            start_time=time(9, 0),
            finish_time=time(11, 0),
            employee=None,
        )

        out = StringIO()
        call_command('backfill_work_order_time_employee', stdout=out)
        output_str = out.getvalue()
        self.assertIn('1 filled', output_str)
        self.assertIn('1 stay unknown', output_str)

        tl_single.refresh_from_db()
        self.assertEqual(tl_single.employee, self.emp_a)

        tl_multi.refresh_from_db()
        self.assertIsNone(tl_multi.employee)

    def test_manager_get_update_page_select_options(self):
        import json
        self.client.force_login(self.manager_user)
        url = reverse('manager-update-work-order', kwargs={'pk': self.work_order.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        assigned = json.loads(response.context['assigned_members'])
        # Manager listed first with "(Manager)" label
        self.assertEqual(assigned[0]['id'], self.manager_emp.id)
        self.assertEqual(assigned[0]['name'], f"{self.manager_user.get_full_name()} (Manager)")

        # Followed by assigned members
        assigned_ids = [m['id'] for m in assigned]
        self.assertIn(self.emp_a.id, assigned_ids)
        self.assertIn(self.emp_b.id, assigned_ids)
        self.assertEqual(len(assigned), 3)

        # Manager was NOT added to job_assigned_to
        self.assertNotIn(self.manager_user, self.work_order.job_assigned_to.all())

        # Check HTML contains Manager label
        self.assertIn(f"{self.manager_user.get_full_name()} (Manager)", response.content.decode('utf-8'))

    def test_manager_saves_row_for_himself_and_member_b(self):
        self.client.force_login(self.manager_user)
        url = reverse('manager-update-work-order', kwargs={'pk': self.work_order.pk})
        post_data = {
            'vessel': 'MV Oceanic',
            'client': 'Test Client Corp',
            'assigned_date': '2026-06-01',
            'job_scope': 'Overhaul',
            'start_date': '2026-06-01',
            'completion_date': '2026-06-02',
            'time_date[]': ['2026-06-01', '2026-06-01'],
            'start_time[]': ['08:00', '13:00'],
            'finish_time[]': ['12:00', '17:00'],
            'time_employee[]': [str(self.manager_emp.id), str(self.emp_b.id)],
        }
        response = self.client.post(url, post_data)
        self.assertRedirects(response, reverse('view_work_order', kwargs={'pk': self.work_order.pk}))

        # 2 WorkOrderTime rows with the correct employees
        times = WorkOrderTime.objects.filter(work_order=self.work_order).order_by('start_time')
        self.assertEqual(times.count(), 2)
        tl_mgr = times[0]
        tl_b = times[1]
        self.assertEqual(tl_mgr.employee, self.manager_emp)
        self.assertEqual(tl_b.employee, self.emp_b)

        # 2 auto service logs
        sl_mgr = EmployeeServiceLog.objects.get(time_log=tl_mgr)
        self.assertEqual(sl_mgr.employee, self.manager_emp)
        self.assertTrue(sl_mgr.is_auto_created)

        sl_b = EmployeeServiceLog.objects.get(time_log=tl_b)
        self.assertEqual(sl_b.employee, self.emp_b)
        self.assertTrue(sl_b.is_auto_created)

        # Manager is NOT added to job_assigned_to automatically
        self.assertNotIn(self.manager_user, self.work_order.job_assigned_to.all())

    def test_view_page_shows_manager_subtotal(self):
        # Work order has manager row (4 hrs) and B row (5 hrs)
        WorkOrderTime.objects.create(
            work_order=self.work_order,
            date=date(2026, 6, 1),
            start_time=time(8, 0),
            finish_time=time(12, 0),
            employee=self.manager_emp,
        )
        WorkOrderTime.objects.create(
            work_order=self.work_order,
            date=date(2026, 6, 1),
            start_time=time(13, 0),
            finish_time=time(18, 0),
            employee=self.emp_b,
        )
        self.client.force_login(self.manager_user)
        response = self.client.get(reverse('view_work_order', kwargs={'pk': self.work_order.pk}))
        self.assertEqual(response.status_code, 200)

        content = response.content.decode('utf-8')
        # Shows manager name in row and subtotal
        self.assertIn("Marge Simpson — 4.00 h", content)
        self.assertIn("Bob Smith — 5.00 h", content)
        self.assertIn("TOTAL HOURS:", content)
        self.assertIn("9.00 hours", content)

    def test_posting_unassigned_employee_falls_back_to_manager(self):
        outsider_user = User.objects.create_user(username='outsider', password='password123')
        outsider_emp = Employee.objects.create(user=outsider_user)

        self.client.force_login(self.manager_user)
        url = reverse('manager-update-work-order', kwargs={'pk': self.work_order.pk})
        post_data = {
            'vessel': 'MV Oceanic',
            'client': 'Test Client Corp',
            'assigned_date': '2026-06-01',
            'job_scope': 'Overhaul',
            'start_date': '2026-06-01',
            'completion_date': '2026-06-02',
            'time_date[]': ['2026-06-01'],
            'start_time[]': ['08:00'],
            'finish_time[]': ['12:00'],
            'time_employee[]': [str(outsider_emp.id)],
        }
        response = self.client.post(url, post_data)
        self.assertRedirects(response, reverse('view_work_order', kwargs={'pk': self.work_order.pk}))

        # Row fell back to manager
        tl = WorkOrderTime.objects.filter(work_order=self.work_order).first()
        self.assertEqual(tl.employee, self.manager_emp)

        # Service log belongs to manager
        sl = EmployeeServiceLog.objects.get(time_log=tl)
        self.assertEqual(sl.employee, self.manager_emp)

    def test_manager_and_employee_updates_do_not_change_employee_count(self):
        user_no_profile = User.objects.create_user(username='no_profile_user', password='password123')
        self.work_order.job_assigned_to.add(user_no_profile)

        initial_count = Employee.objects.count()

        # Manager GET
        self.client.force_login(self.manager_user)
        response_get = self.client.get(reverse('manager-update-work-order', kwargs={'pk': self.work_order.pk}))
        self.assertEqual(response_get.status_code, 200)
        self.assertEqual(Employee.objects.count(), initial_count)

        # Manager POST
        post_data_manager = {
            'vessel': 'MV Oceanic',
            'client': 'Test Client Corp',
            'assigned_date': '2026-06-01',
            'job_scope': 'Overhaul',
            'start_date': '2026-06-01',
            'completion_date': '2026-06-02',
            'time_date[]': ['2026-06-01'],
            'start_time[]': ['08:00'],
            'finish_time[]': ['12:00'],
            'time_employee[]': [str(self.emp_b.id)],
        }
        response_post_mgr = self.client.post(
            reverse('manager-update-work-order', kwargs={'pk': self.work_order.pk}),
            post_data_manager
        )
        self.assertRedirects(response_post_mgr, reverse('view_work_order', kwargs={'pk': self.work_order.pk}))
        self.assertEqual(Employee.objects.count(), initial_count)

        # Employee POST
        self.client.force_login(self.emp_b.user)
        post_data_emp = {
            'start_date': '2026-06-01',
            'completion_date': '2026-06-02',
            'estimated_hours': '4',
            'project_description': 'Assisting overhaul',
            'time_date[]': ['2026-06-01'],
            'start_time[]': ['08:00'],
            'finish_time[]': ['12:00'],
        }
        response_post_emp = self.client.post(
            reverse('employee-update-work-order', kwargs={'pk': self.work_order.pk}),
            post_data_emp
        )
        self.assertRedirects(response_post_emp, reverse('employee-view-work-order', kwargs={'pk': self.work_order.pk}))
        self.assertEqual(Employee.objects.count(), initial_count)

    def test_member_name_containing_html_is_escaped(self):
        import json
        html_user = User.objects.create_user(
            username='html_user',
            first_name='Alice <b>x</b>',
            last_name='Smith',
            password='password123'
        )
        html_emp = Employee.objects.create(user=html_user, is_employee=True, salary=Decimal('3000.00'))
        self.work_order.job_assigned_to.add(html_user)

        self.client.force_login(self.manager_user)
        response = self.client.get(reverse('manager-update-work-order', kwargs={'pk': self.work_order.pk}))
        self.assertEqual(response.status_code, 200)

        # The JSON stays valid and parses cleanly
        members_json = response.context['assigned_members']
        parsed = json.loads(members_json)
        self.assertTrue(any(m['name'] == 'Alice <b>x</b> Smith' for m in parsed))

        # Check view_work_order template output
        WorkOrderTime.objects.create(
            work_order=self.work_order,
            date=date(2026, 6, 1),
            start_time=time(8, 0),
            finish_time=time(12, 0),
            employee=html_emp
        )
        response_view = self.client.get(reverse('view_work_order', kwargs={'pk': self.work_order.pk}))
        self.assertEqual(response_view.status_code, 200)
        view_content = response_view.content.decode('utf-8')
        # Escaped by template, no raw <b>
        self.assertIn('Alice &lt;b&gt;x&lt;/b&gt; Smith', view_content)
        self.assertNotIn('<td>Alice <b>x</b> Smith</td>', view_content)

    def test_member_name_with_script_tag_is_escaped_in_script_context(self):
        import json
        script_user = User.objects.create_user(
            username='script_user',
            first_name='</script><b>x',
            last_name='',
            password='password123'
        )
        script_emp = Employee.objects.create(user=script_user, is_employee=True, salary=Decimal('3000.00'))
        self.work_order.job_assigned_to.add(script_user)

        self.client.force_login(self.manager_user)
        response = self.client.get(reverse('manager-update-work-order', kwargs={'pk': self.work_order.pk}))
        self.assertEqual(response.status_code, 200)

        html_content = response.content.decode('utf-8')
        # Manager update page HTML does not contain '</script><b>x'
        self.assertNotIn('</script><b>x', html_content)
        # And json.loads(response.context['assigned_members']) still returns the original name
        parsed = json.loads(response.context['assigned_members'])
        self.assertTrue(any(m['name'] == '</script><b>x' for m in parsed))

    def test_manager_without_employee_profile_post_redirects_with_error_and_creates_no_time(self):
        mgr_no_profile = User.objects.create_user(
            username='mgr_no_profile',
            password='password123',
            is_staff=True
        )
        self.client.force_login(mgr_no_profile)
        initial_times = WorkOrderTime.objects.filter(work_order=self.work_order).count()

        post_data = {
            'vessel': 'MV Oceanic',
            'client': 'Test Client Corp',
            'assigned_date': '2026-06-01',
            'job_scope': 'Overhaul',
            'start_date': '2026-06-01',
            'completion_date': '2026-06-02',
            'time_date[]': ['2026-06-01'],
            'start_time[]': ['08:00'],
            'finish_time[]': ['12:00'],
            'time_employee[]': ['1'],
        }
        response = self.client.post(
            reverse('manager-update-work-order', kwargs={'pk': self.work_order.pk}),
            post_data
        )
        self.assertRedirects(response, reverse('view_work_order', kwargs={'pk': self.work_order.pk}))

        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any("Your user has no employee profile — ask Admin to create one." in m.message for m in messages)
        )
        self.assertEqual(
            WorkOrderTime.objects.filter(work_order=self.work_order).count(),
            initial_times
        )
        self.assertFalse(Employee.objects.filter(user=mgr_no_profile).exists())


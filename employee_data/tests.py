from datetime import date, time
from decimal import Decimal
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from Admin.models import Employee, Project, WorkOrder, WorkOrderDetail, WorkOrderTime
from service_logs.models import EmployeeServiceLog


class EmployeeUpdateWorkOrderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='technician_user', password='password123')
        self.employee = Employee.objects.create(
            user=self.user,
            is_employee=True,
            salary=Decimal('4500.00'),
        )
        self.project = Project.objects.create(
            name='Test Marine Project',
            client_name='Test Marine Client',
            vessel_name='MV Sea Explorer',
            category='AT_BERTH',
            status='ONGOING',
            service_reference='SR-2026-001',
        )
        self.work_order = WorkOrder.objects.create(
            project=self.project,
            work_order_number='WO-2026-001',
            client='Test Marine Client',
            vessel='MV Sea Explorer',
            assigned_date=date(2026, 6, 1),
        )
        self.work_order.job_assigned_to.add(self.user)
        self.work_order_detail = WorkOrderDetail.objects.create(work_order=self.work_order)

    def test_employee_update_work_order_with_time_log(self):
        self.client.force_login(self.user)
        url = reverse('employee-update-work-order', kwargs={'pk': self.work_order.pk})

        post_data = {
            'start_date': '2026-06-01',
            'completion_date': '2026-06-02',
            'estimated_hours': '8',
            'project_description': 'Maintenance on auxiliary engine',
            'time_date[]': ['2026-06-01'],
            'start_time[]': ['08:00'],
            'finish_time[]': ['16:00'],
        }

        response = self.client.post(url, post_data)

        # 1. Assert redirect (no "Error saving work order" message)
        expected_redirect = reverse('employee-view-work-order', kwargs={'pk': self.work_order.pk})
        self.assertRedirects(response, expected_redirect)

        messages = list(get_messages(response.wsgi_request))
        error_messages = [m.message for m in messages if 'Error saving work order' in m.message or m.level_tag == 'error']
        self.assertEqual(error_messages, [], f"Unexpected error messages: {error_messages}")

        # 2. Assert WorkOrderTime rows were created
        time_logs = WorkOrderTime.objects.filter(work_order=self.work_order)
        self.assertEqual(time_logs.count(), 1)
        time_log = time_logs.first()
        self.assertEqual(time_log.date, date(2026, 6, 1))
        self.assertEqual(time_log.start_time, time(8, 0))
        self.assertEqual(time_log.finish_time, time(16, 0))
        self.assertEqual(time_log.employee, self.employee)

        # 3. Assert auto-created EmployeeServiceLog rows exist
        service_logs = EmployeeServiceLog.objects.filter(time_log=time_log)
        self.assertEqual(service_logs.count(), 1)
        service_log = service_logs.first()
        self.assertEqual(service_log.employee, self.employee)
        self.assertEqual(service_log.project, self.project)
        self.assertTrue(service_log.is_auto_created)
        self.assertEqual(service_log.date, date(2026, 6, 1))
        self.assertEqual(service_log.start_time, time(8, 0))
        self.assertEqual(service_log.end_time, time(16, 0))

        # Check detail dates were updated
        self.work_order_detail.refresh_from_db()
        self.assertEqual(self.work_order_detail.start_date, date(2026, 6, 1))
        self.assertEqual(self.work_order_detail.completion_date, date(2026, 6, 2))

    def test_employee_without_employee_profile_post_redirects_and_creates_no_time(self):
        emp_user_no_profile = User.objects.create_user(username='emp_no_profile_u', password='password123')
        self.work_order.job_assigned_to.add(emp_user_no_profile)

        self.client.force_login(emp_user_no_profile)
        url = reverse('employee-update-work-order', kwargs={'pk': self.work_order.pk})
        post_data = {
            'start_date': '2026-06-01',
            'completion_date': '2026-06-02',
            'time_date[]': ['2026-06-01'],
            'start_time[]': ['08:00'],
            'finish_time[]': ['16:00'],
        }
        response = self.client.post(url, post_data)
        self.assertRedirects(response, reverse('employee-view-work-order', kwargs={'pk': self.work_order.pk}))

        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any("Your user has no employee profile — ask Admin to create one." in m.message for m in messages)
        )
        self.assertFalse(Employee.objects.filter(user=emp_user_no_profile).exists())

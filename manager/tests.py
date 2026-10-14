from datetime import date, time
from decimal import Decimal
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from Admin.models import Employee, Project, WorkOrder, WorkOrderDetail, WorkOrderTime
from service_logs.models import EmployeeServiceLog


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


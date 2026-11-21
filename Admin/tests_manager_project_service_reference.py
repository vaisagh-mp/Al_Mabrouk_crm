from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from Admin.models import Employee, Project


class ManagerProjectServiceReferenceTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Manager 1
        self.user_m1 = User.objects.create_user(
            username='mgr_tariq',
            email='tariq@amms.ae',
            password='password123',
            first_name='Tariq',
            last_name='Manager',
            is_active=True
        )
        self.emp_m1 = Employee.objects.create(
            user=self.user_m1,
            is_manager=True,
            is_employee=False,
            employee_code='AMMS-M01',
            rank='Manager',
            salary=Decimal('8000.00')
        )

        # Manager 2
        self.user_m2 = User.objects.create_user(
            username='mgr_other',
            email='other@amms.ae',
            password='password123',
            first_name='Other',
            last_name='Manager',
            is_active=True
        )
        self.emp_m2 = Employee.objects.create(
            user=self.user_m2,
            is_manager=True,
            is_employee=False,
            employee_code='AMMS-M02',
            rank='Manager',
            salary=Decimal('8000.00')
        )

        # Project 1 owned by Manager 1
        self.project1 = Project.objects.create(
            name='Alpha Project',
            code='PRJ-M1-001',
            manager=self.emp_m1,
            service_reference='SR-KEEP',
            currency_code='AED',
            status='ONGOING',
            priority='HIGH',
            purchase_and_expenses=Decimal('500.00'),
            invoice_amount=Decimal('2000.00')
        )

        # Project 2 owned by Manager 2
        self.project2 = Project.objects.create(
            name='Beta Project',
            code='PRJ-M2-002',
            manager=self.emp_m2,
            service_reference='SR-M2',
            currency_code='AED',
            status='ONGOING',
            priority='LOW',
            purchase_and_expenses=Decimal('300.00'),
            invoice_amount=Decimal('1500.00')
        )

    def _base_post_data(self, **extra):
        data = {
            'name': 'Test Project',
            'code': 'PRJ-TEST-999',
            'service_reference': '',
            'client_name': 'Test Client Ltd',
            'vessel_name': 'MV Ocean Test',
            'purchase_and_expenses': '1000',
            'invoice_amount': '3000',
            'currency_code': 'AED',
            'status': 'ONGOING',
            'category': 'ANCHORAGE',
            'manager': str(self.emp_m1.id),
            'priority': 'HIGH',
            'job_description': 'Test job description.',
            'deadline_date': '2026-12-31',
        }
        data.update(extra)
        return data

    def test_get_manager_pages_contain_service_reference(self):
        """
        GET both Manager pages as a manager: response contains name="service_reference".
        """
        self.client.login(username='mgr_tariq', password='password123')

        # 1. Add Project page
        resp_add = self.client.get(reverse('manager-add-project'))
        self.assertEqual(resp_add.status_code, 200)
        self.assertContains(resp_add, 'name="service_reference"')
        self.assertContains(resp_add, 'id="id_service_reference"')

        # 2. Edit Project page
        resp_edit = self.client.get(reverse('manager-edit-project', args=[self.project1.id]))
        self.assertEqual(resp_edit.status_code, 200)
        self.assertContains(resp_edit, 'name="service_reference"')
        self.assertContains(resp_edit, 'id="id_service_reference"')
        self.assertContains(resp_edit, 'value="SR-KEEP"')

    def test_manager_creates_project_with_service_reference(self):
        """
        Manager creates a project with service_reference "SR-TEST-01": saved on the Project.
        """
        self.client.login(username='mgr_tariq', password='password123')

        data = self._base_post_data(
            name='Created Project',
            code='PRJ-SR-01',
            service_reference='SR-TEST-01'
        )
        resp = self.client.post(reverse('manager-add-project'), data)
        self.assertEqual(resp.status_code, 302)

        created_project = Project.objects.get(code='PRJ-SR-01')
        self.assertEqual(created_project.service_reference, 'SR-TEST-01')
        self.assertEqual(created_project.manager, self.emp_m1)

    def test_project_edited_by_manager_keeps_unchanged_service_reference(self):
        """
        Project with service_reference "SR-KEEP" edited by its manager
        (posting the field back unchanged) keeps "SR-KEEP".
        """
        self.client.login(username='mgr_tariq', password='password123')

        data = self._base_post_data(
            name='Alpha Project Updated',
            code=self.project1.code,
            service_reference='SR-KEEP'
        )
        resp = self.client.post(reverse('manager-edit-project', args=[self.project1.id]), data)
        self.assertEqual(resp.status_code, 302)

        self.project1.refresh_from_db()
        self.assertEqual(self.project1.service_reference, 'SR-KEEP')
        self.assertEqual(self.project1.name, 'Alpha Project Updated')

    def test_manager_changes_service_reference_and_detail_shows_it(self):
        """
        Manager changes service_reference to "SR-NEW": saved, and detail page shows it.
        """
        self.client.login(username='mgr_tariq', password='password123')

        data = self._base_post_data(
            name='Alpha Project Renamed',
            code=self.project1.code,
            service_reference='SR-NEW'
        )
        resp = self.client.post(reverse('manager-edit-project', args=[self.project1.id]), data)
        self.assertEqual(resp.status_code, 302)

        self.project1.refresh_from_db()
        self.assertEqual(self.project1.service_reference, 'SR-NEW')

        # Check Manager project detail page shows the saved value
        resp_detail = self.client.get(reverse('project-summary-view', args=[self.project1.id]))
        self.assertEqual(resp_detail.status_code, 200)
        self.assertContains(resp_detail, 'SR-NEW')

    def test_manager_cannot_edit_another_managers_project(self):
        """
        Manager cannot edit another manager's project (existing 404 behaviour stays).
        """
        self.client.login(username='mgr_tariq', password='password123')

        # GET request returns 404
        resp_get = self.client.get(reverse('manager-edit-project', args=[self.project2.id]))
        self.assertEqual(resp_get.status_code, 404)

        # POST request returns 404 and does not mutate project
        data = self._base_post_data(
            name='Hacked Name',
            code=self.project2.code,
            service_reference='SR-HACK'
        )
        resp_post = self.client.post(reverse('manager-edit-project', args=[self.project2.id]), data)
        self.assertEqual(resp_post.status_code, 404)

        self.project2.refresh_from_db()
        self.assertEqual(self.project2.service_reference, 'SR-M2')
        self.assertEqual(self.project2.name, 'Beta Project')

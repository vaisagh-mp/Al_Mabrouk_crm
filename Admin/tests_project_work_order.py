import datetime
from io import StringIO
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.core.management import call_command
from django.contrib.auth.models import User
from Admin.models import (
    Project, WorkOrder, WorkOrderDetail, WorkOrderTime, Spare, Team, Employee
)
from service_logs.models import EmployeeServiceLog


class BaseProjectWorkOrderTestCase(TestCase):
    def setUp(self):
        # Admin
        self.admin_user = User.objects.create_superuser('pwo_admin', 'admin@example.com', 'pass123')
        self.admin_emp = Employee.objects.create(
            user=self.admin_user, is_employee=False, is_manager=False, salary=Decimal('10000.00')
        )

        # Manager
        self.manager_user = User.objects.create_user('pwo_manager', 'mgr@example.com', 'pass123')
        self.manager_emp = Employee.objects.create(
            user=self.manager_user, is_manager=True, is_employee=False, salary=Decimal('8000.00')
        )

        # Administration
        self.admstrn_user = User.objects.create_user('pwo_admstrn', 'admstrn@example.com', 'pass123')
        self.admstrn_emp = Employee.objects.create(
            user=self.admstrn_user, is_administration=True, is_employee=False, salary=Decimal('6000.00')
        )

        # Engineers / Employees
        self.engineer_user_a = User.objects.create_user('pwo_eng_a', 'enga@example.com', 'pass123')
        self.engineer_emp_a = Employee.objects.create(
            user=self.engineer_user_a, is_employee=True, salary=Decimal('4000.00')
        )

        self.engineer_user_b = User.objects.create_user('pwo_eng_b', 'engb@example.com', 'pass123')
        self.engineer_emp_b = Employee.objects.create(
            user=self.engineer_user_b, is_employee=True, salary=Decimal('4000.00')
        )

    def base_project_data(self, **extra):
        data = {
            'name': 'Test Cargo Overhaul',
            'code': 'PRJ-2026-001',
            'service_reference': 'SR-001',
            'client_name': 'Global Shipping Ltd',
            'vessel_name': 'MV Star Horizon',
            'purchase_and_expenses': '1500',
            'invoice_amount': '5000',
            'currency_code': 'AED',
            'status': 'ONGOING',
            'category': 'ANCHORAGE',
            'manager': str(self.manager_emp.id),
            'priority': 'HIGH',
            'job_description': 'Overhaul main generator and check valves.',
            'deadline_date': '2026-12-31',
            # Work Order Fields
            'imo_no': 'IMO-9876543',
            'location': 'Dubai Maritime City',
            'assigned_date': '2026-11-05',
            'job_scope': 'Disassemble, inspect, replace worn seals, test run.',
            'job_instructions': 'Follow maker manual specifications.',
        }
        data.update(extra)
        return data


class AdminProjectWorkOrderTests(BaseProjectWorkOrderTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)

    def test_admin_create_project_auto_creates_work_order(self):
        data = self.base_project_data()
        resp = self.client.post(reverse('add_project'), data)
        self.assertEqual(resp.status_code, 302)

        project = Project.objects.get(code='PRJ-2026-001')
        self.assertIsNotNone(project)

        # Exactly one WorkOrder exists for this project
        self.assertEqual(WorkOrder.objects.filter(project=project).count(), 1)
        wo = project.workorder

        # Header fields copied directly from project
        self.assertEqual(wo.vessel, 'MV Star Horizon')
        self.assertEqual(wo.client, 'Global Shipping Ltd')
        self.assertEqual(wo.project_description, 'Overhaul main generator and check valves.')

        # 5 Work Order fields saved
        self.assertEqual(wo.imo_no, 'IMO-9876543')
        self.assertEqual(wo.location, 'Dubai Maritime City')
        self.assertEqual(wo.assigned_date, datetime.date(2026, 11, 5))
        self.assertEqual(wo.job_scope, 'Disassemble, inspect, replace worn seals, test run.')
        self.assertEqual(wo.job_instructions, 'Follow maker manual specifications.')

        # Metadata
        self.assertEqual(wo.created_by, self.admin_user)
        self.assertTrue(wo.work_order_number.startswith('AMMS-'))
        self.assertTrue(WorkOrderDetail.objects.filter(work_order=wo).exists())

    def test_admin_create_project_with_team_syncs_members(self):
        data = self.base_project_data(
            code='PRJ-TEAM-01',
            team_name='Alpha Tech Team',
            employees=[str(self.engineer_emp_a.id), str(self.engineer_emp_b.id)],
        )
        resp = self.client.post(reverse('add_project'), data)
        self.assertEqual(resp.status_code, 302)

        project = Project.objects.get(code='PRJ-TEAM-01')
        wo = project.workorder

        assigned_pks = set(wo.job_assigned_to.values_list('pk', flat=True))
        all_pks = set(wo.all_members.values_list('pk', flat=True))

        expected_pks = {self.engineer_user_a.pk, self.engineer_user_b.pk}
        self.assertEqual(assigned_pks, expected_pks)
        self.assertEqual(all_pks, expected_pks)

    def test_admin_create_project_missing_assigned_date_defaults_to_today(self):
        data = self.base_project_data(code='PRJ-DATE-DEF', assigned_date='')
        resp = self.client.post(reverse('add_project'), data)
        self.assertEqual(resp.status_code, 302)

        project = Project.objects.get(code='PRJ-DATE-DEF')
        wo = project.workorder
        self.assertEqual(wo.assigned_date, timezone.now().date())

    def test_admin_create_project_invalid_assigned_date_fails_form(self):
        data = self.base_project_data(code='PRJ-DATE-INV', assigned_date='2026-99-99')
        resp = self.client.post(reverse('add_project'), data)
        self.assertEqual(resp.status_code, 200)  # Re-renders form with errors

        self.assertFalse(Project.objects.filter(code='PRJ-DATE-INV').exists())
        self.assertFalse(WorkOrder.objects.filter(imo_no='IMO-9876543', project__code='PRJ-DATE-INV').exists())

    def test_admin_edit_project_updates_work_order_preserves_time_and_spares(self):
        # 1. Create project
        self.client.post(reverse('add_project'), self.base_project_data(code='PRJ-EDIT-01'))
        project = Project.objects.get(code='PRJ-EDIT-01')
        wo = project.workorder

        # Add existing time rows, service log, and spare
        tl = WorkOrderTime.objects.create(
            work_order=wo, date=datetime.date(2026, 11, 6),
            start_time=datetime.time(8), finish_time=datetime.time(16),
            employee=self.engineer_emp_a
        )
        spare = Spare.objects.create(work_order=wo, name='Gasket 40mm', unit='pcs', quantity=4)

        # 2. Edit project (vessel, client, job description, imo_no, job_scope)
        edit_data = self.base_project_data(
            code='PRJ-EDIT-01',
            name='Updated Cargo Overhaul',
            client_name='Oceanic Fleet Corp',
            vessel_name='MV Oceanic Blue',
            job_description='Full overhaul including auxiliary pumps.',
            imo_no='IMO-1122334',
            job_scope='Overhaul auxiliary pumps and valves.',
        )
        resp = self.client.post(reverse('edit_project', kwargs={'project_id': project.id}), edit_data)
        self.assertEqual(resp.status_code, 302)

        wo.refresh_from_db()
        self.assertEqual(wo.client, 'Oceanic Fleet Corp')
        self.assertEqual(wo.vessel, 'MV Oceanic Blue')
        self.assertEqual(wo.project_description, 'Full overhaul including auxiliary pumps.')
        self.assertEqual(wo.imo_no, 'IMO-1122334')
        self.assertEqual(wo.job_scope, 'Overhaul auxiliary pumps and valves.')

        # Preserved
        self.assertEqual(WorkOrderTime.objects.filter(work_order=wo).count(), 1)
        self.assertEqual(EmployeeServiceLog.objects.filter(time_log__work_order=wo).count(), 1)
        self.assertEqual(Spare.objects.filter(work_order=wo).count(), 1)

    def test_old_create_url_redirects_appropriately(self):
        # Case A: Project with work order -> redirect to view
        self.client.post(reverse('add_project'), self.base_project_data(code='PRJ-OLD-WO'))
        proj_with_wo = Project.objects.get(code='PRJ-OLD-WO')
        resp = self.client.get(reverse('admin-create-work-order', kwargs={'project_id': proj_with_wo.id}))
        self.assertRedirects(resp, reverse('admin_view_work_order', kwargs={'pk': proj_with_wo.workorder.pk}))

        # Case B: Project without work order -> auto-creates and redirects to update
        proj_no_wo = Project.objects.create(
            name='Orphan Project', client_name='Client X', vessel_name='MV X', manager=self.manager_emp
        )
        self.assertFalse(hasattr(proj_no_wo, 'workorder'))
        resp = self.client.get(reverse('admin-create-work-order', kwargs={'project_id': proj_no_wo.id}))
        proj_no_wo.refresh_from_db()
        self.assertTrue(hasattr(proj_no_wo, 'workorder'))
        self.assertRedirects(resp, reverse('admin-update-work-order', kwargs={'pk': proj_no_wo.workorder.pk}))

    def test_admin_work_order_update_post_ignores_header_fields(self):
        self.client.post(reverse('add_project'), self.base_project_data(
            code='PRJ-UP-POST',
            team_name='Team A',
            employees=[str(self.engineer_emp_a.id)],
        ))
        proj = Project.objects.get(code='PRJ-UP-POST')
        wo = proj.workorder

        # POST to update page trying to change client and imo_no, while adding time row
        post_data = {
            'client': 'HACKED CLIENT',
            'imo_no': 'HACKED IMO',
            'vessel': 'HACKED VESSEL',
            'assigned_date': '2030-01-01',
            'time_date[]': ['2026-11-05'],
            'start_time[]': ['08:00'],
            'finish_time[]': ['16:00'],
            'time_employee[]': [str(self.engineer_emp_a.id)],
        }
        resp = self.client.post(reverse('admin-update-work-order', kwargs={'pk': wo.pk}), post_data)
        self.assertEqual(resp.status_code, 302)

        wo.refresh_from_db()
        # Stored header values unchanged
        self.assertEqual(wo.client, 'Global Shipping Ltd')
        self.assertEqual(wo.vessel, 'MV Star Horizon')
        self.assertEqual(wo.imo_no, 'IMO-9876543')
        self.assertEqual(wo.assigned_date, datetime.date(2026, 11, 5))

        # Time row saved
        self.assertEqual(WorkOrderTime.objects.filter(work_order=wo).count(), 1)
        self.assertEqual(WorkOrderTime.objects.get(work_order=wo).employee, self.engineer_emp_a)


class ManagerProjectWorkOrderTests(BaseProjectWorkOrderTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.manager_user)

    def test_manager_create_project_auto_creates_work_order(self):
        data = self.base_project_data(code='MGR-PRJ-01')
        resp = self.client.post(reverse('manager-add-project'), data)
        self.assertEqual(resp.status_code, 302)

        project = Project.objects.get(code='MGR-PRJ-01')
        self.assertEqual(WorkOrder.objects.filter(project=project).count(), 1)
        wo = project.workorder

        self.assertEqual(wo.vessel, 'MV Star Horizon')
        self.assertEqual(wo.client, 'Global Shipping Ltd')
        self.assertEqual(wo.created_by, self.manager_user)
        self.assertEqual(wo.imo_no, 'IMO-9876543')
        self.assertTrue(WorkOrderDetail.objects.filter(work_order=wo).exists())

    def test_manager_create_project_with_team_syncs_members(self):
        data = self.base_project_data(
            code='MGR-TEAM-01',
            team_name='Manager Team Beta',
            employees=[str(self.engineer_emp_a.id)],
        )
        resp = self.client.post(reverse('manager-add-project'), data)
        self.assertEqual(resp.status_code, 302)

        project = Project.objects.get(code='MGR-TEAM-01')
        wo = project.workorder
        self.assertIn(self.engineer_user_a, wo.job_assigned_to.all())
        self.assertIn(self.engineer_user_a, wo.all_members.all())

    def test_old_create_url_for_manager(self):
        self.client.post(reverse('manager-add-project'), self.base_project_data(code='MGR-OLD-01'))
        proj = Project.objects.get(code='MGR-OLD-01')
        resp = self.client.get(reverse('create_work_order', kwargs={'project_id': proj.id}))
        self.assertRedirects(resp, reverse('view_work_order', kwargs={'pk': proj.workorder.pk}))

        # Project without work order
        proj_no_wo = Project.objects.create(
            name='No WO Mgr', client_name='Client', vessel_name='Vessel', manager=self.manager_emp
        )
        resp2 = self.client.get(reverse('create_work_order', kwargs={'project_id': proj_no_wo.id}))
        proj_no_wo.refresh_from_db()
        self.assertTrue(hasattr(proj_no_wo, 'workorder'))
        self.assertRedirects(resp2, reverse('manager-update-work-order', kwargs={'pk': proj_no_wo.workorder.pk}))


class AdministrationProjectWorkOrderTests(BaseProjectWorkOrderTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admstrn_user)

    def test_administration_create_and_edit_project_creates_work_order(self):
        data = self.base_project_data(code='ADMSTRN-PRJ-01')
        resp = self.client.post(reverse('admstrn_add_project'), data)
        self.assertEqual(resp.status_code, 302)

        project = Project.objects.get(code='ADMSTRN-PRJ-01')
        self.assertEqual(WorkOrder.objects.filter(project=project).count(), 1)
        wo = project.workorder

        self.assertEqual(wo.vessel, 'MV Star Horizon')
        self.assertEqual(wo.client, 'Global Shipping Ltd')
        self.assertEqual(wo.created_by, self.admstrn_user)
        self.assertEqual(wo.imo_no, 'IMO-9876543')

        # Edit project via administration
        edit_data = self.base_project_data(
            code='ADMSTRN-PRJ-01',
            client_name='New Admstrn Client',
            vessel_name='MV New Admstrn Vessel',
            imo_no='IMO-ADMSTRN-88',
        )
        resp2 = self.client.post(reverse('admstrn_edit_project', kwargs={'project_id': project.id}), edit_data)
        self.assertEqual(resp2.status_code, 302)

        wo.refresh_from_db()
        self.assertEqual(wo.client, 'New Admstrn Client')
        self.assertEqual(wo.vessel, 'MV New Admstrn Vessel')
        self.assertEqual(wo.imo_no, 'IMO-ADMSTRN-88')


class CreateMissingWorkOrdersCommandTests(BaseProjectWorkOrderTestCase):
    def test_dry_run_and_execution(self):
        # Create an ongoing project without work order
        p_open = Project.objects.create(
            name='Open Project Without WO',
            client_name='Open Client',
            vessel_name='MV Open',
            manager=self.manager_emp,
            status='ONGOING',
        )
        # Create a closed project without work order
        p_closed = Project.objects.create(
            name='Completed Project Without WO',
            client_name='Closed Client',
            vessel_name='MV Closed',
            manager=self.manager_emp,
            status='COMPLETED',
        )

        # 1. Dry run
        out = StringIO()
        call_command('create_missing_work_orders', '--dry-run', stdout=out)
        output = out.getvalue()
        self.assertIn('[Dry Run]', output)
        self.assertIn('Open Project Without WO', output)
        self.assertNotIn('Completed Project Without WO', output)
        self.assertFalse(WorkOrder.objects.filter(project=p_open).exists())
        self.assertFalse(WorkOrder.objects.filter(project=p_closed).exists())

        # 2. Run without --dry-run (default skips COMPLETED/CANCELLED)
        out2 = StringIO()
        call_command('create_missing_work_orders', stdout=out2)
        output2 = out2.getvalue()
        self.assertIn('Created WorkOrder', output2)
        self.assertTrue(WorkOrder.objects.filter(project=p_open).exists())
        self.assertFalse(WorkOrder.objects.filter(project=p_closed).exists())
        wo_open = p_open.workorder
        self.assertEqual(wo_open.vessel, 'MV Open')
        self.assertEqual(wo_open.client, 'Open Client')
        self.assertEqual(wo_open.created_by, self.manager_user)

        # 3. Running again creates no duplicates
        out3 = StringIO()
        call_command('create_missing_work_orders', stdout=out3)
        self.assertEqual(WorkOrder.objects.filter(project=p_open).count(), 1)

        # 4. Include closed creates work order for closed project
        out4 = StringIO()
        call_command('create_missing_work_orders', '--include-closed', stdout=out4)
        self.assertTrue(WorkOrder.objects.filter(project=p_closed).exists())
        self.assertEqual(WorkOrder.objects.filter(project=p_closed).count(), 1)

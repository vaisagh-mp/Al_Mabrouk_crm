import datetime
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.template.loader import render_to_string
from django.contrib.auth.models import User
from Admin.models import Project, WorkOrder, WorkOrderDetail, Employee, Team
from Admin.templatetags.text_tags import plain_text


class WorkOrderHeaderAndBackButtonsTestCase(TestCase):
    def setUp(self):
        # Admin superuser
        self.admin_user = User.objects.create_superuser('wo_admin', 'admin@example.com', 'pass123')
        self.admin_emp = Employee.objects.create(
            user=self.admin_user, is_employee=False, is_manager=False, salary=Decimal('10000.00')
        )

        # Manager user
        self.manager_user = User.objects.create_user('wo_mgr', 'mgr@example.com', 'pass123')
        self.manager_emp = Employee.objects.create(
            user=self.manager_user, is_manager=True, is_employee=False, salary=Decimal('8000.00')
        )

        # Administration user
        self.admstrn_user = User.objects.create_user('wo_adm', 'adm@example.com', 'pass123')
        self.admstrn_emp = Employee.objects.create(
            user=self.admstrn_user, is_administration=True, is_employee=False, salary=Decimal('6000.00')
        )

        # Employee user
        self.employee_user = User.objects.create_user('wo_eng', 'eng@example.com', 'pass123')
        self.employee_emp = Employee.objects.create(
            user=self.employee_user, is_employee=True, salary=Decimal('4000.00')
        )

        # Create project
        self.project = Project.objects.create(
            name='Test Project FXD',
            code='PRJ-2026-FXD',
            service_reference='FXD-EUR',
            client_name='FX DEMO Client EUR',
            vessel_name='MV FX DEMO EUR',
            status='IN_PROGRESS',
            manager=self.manager_emp,
        )

        # Create work order
        self.work_order = WorkOrder.objects.create(
            project=self.project,
            work_order_number='AMMS-26-009',
            vessel='MV FX DEMO EUR',
            client='FX DEMO Client EUR',
            imo_no=None,
            location='DMC',
            assigned_date=datetime.date(2026, 11, 3),
            job_scope='Main engine overhaul',
            job_instructions='Check fuel pumps',
            project_description='Full overhaul scope',
        )
        self.work_order.job_assigned_to.add(self.employee_user)

        # Create work order detail with start_date and completion_date
        self.work_order_detail = WorkOrderDetail.objects.create(
            work_order=self.work_order,
            start_date=datetime.date(2026, 11, 2),
            completion_date=datetime.date(2026, 11, 5),
        )

        # Clients for each role
        self.admin_client = Client()
        self.admin_client.force_login(self.admin_user)

        self.mgr_client = Client()
        self.mgr_client.force_login(self.manager_user)

        self.emp_client = Client()
        self.emp_client.force_login(self.employee_user)

        self.adm_client = Client()
        self.adm_client.force_login(self.admstrn_user)

    def test_plain_text_filter_unit(self):
        """Unit test for plain_text filter."""
        self.assertEqual(plain_text(None), "—")
        self.assertEqual(plain_text(""), "—")
        self.assertEqual(plain_text("None"), "—")
        self.assertEqual(plain_text("none"), "—")
        self.assertEqual(plain_text("<script>alert(1)</script>"), "alert(1)")
        self.assertEqual(plain_text("Check&nbsp;alarm<br>panel"), "Check alarm<br>panel")
        self.assertEqual(plain_text("Line 1\n\n\n\nLine 2"), "Line 1<br><br>Line 2")
        self.assertEqual(plain_text("Plain multi-line\ntext"), "Plain multi-line<br>text")

    def test_work_order_header_on_view_pages(self):
        """Test the 2-column, 4-row header table on Admin, Manager, and Employee view pages."""
        endpoints = [
            (self.admin_client, reverse('admin_view_work_order', args=[self.work_order.id])),
            (self.mgr_client, reverse('view_work_order', args=[self.work_order.id])),
            (self.emp_client, reverse('employee-view-work-order', args=[self.work_order.id])),
        ]

        for client, url in endpoints:
            response = client.get(url)
            self.assertEqual(response.status_code, 200)
            content = response.content.decode('utf-8')

            # Verify header labels
            self.assertIn('Work Order Number:', content)
            self.assertIn('Service Reference:', content)
            self.assertIn('Vessel:', content)
            self.assertIn('Client:', content)
            self.assertIn('IMO No:', content)
            self.assertIn('Location:', content)
            self.assertIn('Assigned Date:', content)
            self.assertIn('Completed Date:', content)

            # Verify values
            self.assertIn('AMMS-26-009', content)
            self.assertIn('FXD-EUR', content)
            self.assertIn('MV FX DEMO EUR', content)
            self.assertIn('FX DEMO Client EUR', content)
            self.assertIn('DMC', content)
            self.assertIn('03/11/2026', content)
            self.assertIn('05/11/2026', content)

            # IMO No is None: must display '—' and never 'None'
            self.assertIn('<strong>IMO No:</strong> —', content)

    def test_work_order_pdf_header(self):
        """Test the 2-column, 4-row header table rendered in the PDF template."""
        rendered = render_to_string('Manager/work_order_pdf.html', {
            'work_order': self.work_order,
            'work_order_detail': self.work_order_detail,
            'live_members': [self.employee_user],
            'all_members': [self.employee_user],
            'time_entries': [],
            'member_totals': {},
            'calculated_hours': 0,
            'image_groups': {},
        })

        self.assertIn('Work Order Number:', rendered)
        self.assertIn('Service Reference:', rendered)
        self.assertIn('Vessel:', rendered)
        self.assertIn('Client:', rendered)
        self.assertIn('IMO No:', rendered)
        self.assertIn('Location:', rendered)
        self.assertIn('Assigned Date:', rendered)
        self.assertIn('Completed Date:', rendered)

        self.assertIn('AMMS-26-009', rendered)
        self.assertIn('FXD-EUR', rendered)
        self.assertIn('MV FX DEMO EUR', rendered)
        self.assertIn('FX DEMO Client EUR', rendered)
        self.assertIn('DMC', rendered)
        self.assertIn('03/11/2026', rendered)
        self.assertIn('05/11/2026', rendered)
        self.assertIn('<strong>IMO No:</strong> —', rendered)

    def test_details_table_dates_format(self):
        """Start Date and Completion Date in the details table must be dd/mm/YYYY."""
        endpoints = [
            (self.admin_client, reverse('admin_view_work_order', args=[self.work_order.id])),
            (self.mgr_client, reverse('view_work_order', args=[self.work_order.id])),
            (self.emp_client, reverse('employee-view-work-order', args=[self.work_order.id])),
        ]

        for client, url in endpoints:
            response = client.get(url)
            self.assertEqual(response.status_code, 200)
            content = response.content.decode('utf-8')

            # Formatted in dd/mm/YYYY
            self.assertIn('02/11/2026', content)
            self.assertIn('05/11/2026', content)
            # Not Django default month name like "Nov."
            self.assertNotIn('Nov.', content)
            self.assertNotIn('--/--/--', content)

        # PDF template
        rendered = render_to_string('Manager/work_order_pdf.html', {
            'work_order': self.work_order,
            'work_order_detail': self.work_order_detail,
            'live_members': [self.employee_user],
            'all_members': [self.employee_user],
            'time_entries': [],
            'member_totals': {},
            'calculated_hours': 0,
            'image_groups': {},
        })
        self.assertIn('02/11/2026', rendered)
        self.assertIn('05/11/2026', rendered)
        self.assertNotIn('--/--/--', rendered)

    def test_work_order_with_none_fields_has_no_none_anywhere(self):
        """When job_scope, job_instructions, etc. are None, no 'None' appears anywhere on the 3 view pages or PDF."""
        proj_none = Project.objects.create(
            name='None Proj',
            code='PRJ-NONE',
            client_name='FX DEMO Client EUR',
            vessel_name='MV FX DEMO EUR',
        )
        wo_none = WorkOrder.objects.create(
            project=proj_none,
            work_order_number='AMMS-26-EMPTY',
            vessel='MV EMPTY',
            client='FX DEMO Client EUR',
            imo_no=None,
            location=None,
            assigned_date=None,
            job_scope=None,
            job_instructions=None,
            project_description=None,
        )
        wo_none.job_assigned_to.add(self.employee_user)

        wod_none = WorkOrderDetail.objects.create(
            work_order=wo_none,
            start_date=None,
            completion_date=None,
        )

        endpoints = [
            (self.admin_client, reverse('admin_view_work_order', args=[wo_none.id])),
            (self.mgr_client, reverse('view_work_order', args=[wo_none.id])),
            (self.emp_client, reverse('employee-view-work-order', args=[wo_none.id])),
        ]

        for client, url in endpoints:
            response = client.get(url)
            self.assertEqual(response.status_code, 200)
            content = response.content.decode('utf-8')

            # Must NOT contain the word 'None' anywhere
            self.assertNotIn('None', content)

        # Check PDF template
        rendered = render_to_string('Manager/work_order_pdf.html', {
            'work_order': wo_none,
            'work_order_detail': wod_none,
            'live_members': [self.employee_user],
            'all_members': [self.employee_user],
            'time_entries': [],
            'member_totals': {},
            'calculated_hours': 0,
            'image_groups': {},
        })
        self.assertNotIn('None', rendered)

    def test_xss_protection_and_html_cleaning_on_pages_and_pdf(self):
        """<script>alert(1)</script> never appears unescaped on any page/PDF; Check&nbsp;alarm<br>panel renders properly."""
        self.work_order.job_instructions = "<script>alert(1)</script>"
        self.work_order.job_scope = "Check&nbsp;alarm<br>panel"
        self.work_order.project_description = "Step 1\nStep 2\n\n\n\nStep 3"
        self.work_order.save()

        endpoints = [
            (self.admin_client, reverse('admin_view_work_order', args=[self.work_order.id])),
            (self.mgr_client, reverse('view_work_order', args=[self.work_order.id])),
            (self.emp_client, reverse('employee-view-work-order', args=[self.work_order.id])),
        ]

        for client, url in endpoints:
            response = client.get(url)
            self.assertEqual(response.status_code, 200)
            content = response.content.decode('utf-8')

            # <script>alert(1)</script> must never appear unescaped
            self.assertNotIn('<script>alert(1)</script>', content)
            self.assertNotIn('<script>alert', content)

            # "Check&nbsp;alarm<br>panel" renders as "Check alarm" + line break + "panel"
            self.assertIn('Check alarm<br>panel', content)

            # Multi-line text collapses 3+ blank lines to 1
            self.assertIn('Step 1<br>Step 2<br><br>Step 3', content)

        # PDF template
        rendered = render_to_string('Manager/work_order_pdf.html', {
            'work_order': self.work_order,
            'work_order_detail': self.work_order_detail,
            'live_members': [self.employee_user],
            'all_members': [self.employee_user],
            'time_entries': [],
            'member_totals': {},
            'calculated_hours': 0,
            'image_groups': {},
        })
        self.assertNotIn('<script>alert(1)</script>', rendered)
        self.assertNotIn('<script>alert', rendered)
        self.assertIn('Check alarm<br>panel', rendered)
        self.assertIn('Step 1<br>Step 2<br><br>Step 3', rendered)

    def test_text_none_string_fallback(self):
        """When fields literally contain string 'None', they show '—'."""
        self.work_order.job_scope = 'None'
        self.work_order.job_instructions = 'None'
        self.work_order.project_description = 'None'
        self.work_order.imo_no = 'None'
        self.work_order.save()

        response = self.admin_client.get(reverse('admin_view_work_order', args=[self.work_order.id]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        self.assertNotIn('None', content)

    def test_imo_and_completion_date_fallback(self):
        """Test IMO with value and empty completion_date fallback to '—'."""
        self.work_order.imo_no = '9485019'
        self.work_order.save()
        self.work_order_detail.completion_date = None
        self.work_order_detail.save()

        response = self.admin_client.get(reverse('admin_view_work_order', args=[self.work_order.id]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        self.assertIn('<strong>IMO No:</strong> 9485019', content)
        self.assertIn('<strong>Completed Date:</strong> —', content)

    def test_back_buttons_on_project_pages(self):
        """Test back buttons on all 4 project summary pages."""
        # 1. Admin project page -> project-list
        resp = self.admin_client.get(reverse('project-summary', args=[self.project.id]))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        expected_url = reverse('project-list')
        self.assertIn(f'href="{expected_url}"', content)
        self.assertIn('Back', content)

        # 2. Manager project page -> project_list
        resp = self.mgr_client.get(reverse('project-summary-view', args=[self.project.id]))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        expected_url = reverse('project_list')
        self.assertIn(f'href="{expected_url}"', content)
        self.assertIn('Back', content)

        # 3. Employee project page -> projects
        resp = self.emp_client.get(reverse('project_details', args=[self.project.id]))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        expected_url = reverse('projects')
        self.assertIn(f'href="{expected_url}"', content)
        self.assertIn('Back', content)

        # 4. Administration project page -> admstrn_project_list_view
        resp = self.adm_client.get(reverse('admstrn_project_summary_view', args=[self.project.id]))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        expected_url = reverse('admstrn_project_list_view')
        self.assertIn(f'href="{expected_url}"', content)
        self.assertIn('Back', content)

    def test_back_buttons_on_work_order_view_pages(self):
        """Test 'Back to Project' buttons on all 3 work order view pages."""
        # 1. Admin view -> project-summary
        resp = self.admin_client.get(reverse('admin_view_work_order', args=[self.work_order.id]))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        expected_url = reverse('project-summary', args=[self.project.id])
        self.assertIn(f'href="{expected_url}"', content)
        self.assertIn('Back to Project', content)

        # 2. Manager view -> project-summary-view
        resp = self.mgr_client.get(reverse('view_work_order', args=[self.work_order.id]))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        expected_url = reverse('project-summary-view', args=[self.project.id])
        self.assertIn(f'href="{expected_url}"', content)
        self.assertIn('Back to Project', content)

        # 3. Employee view -> project_details
        resp = self.emp_client.get(reverse('employee-view-work-order', args=[self.work_order.id]))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        expected_url = reverse('project_details', args=[self.project.id])
        self.assertIn(f'href="{expected_url}"', content)
        self.assertIn('Back to Project', content)

    def test_back_button_when_work_order_has_no_project(self):
        """If work order is unlinked from project, it falls back to the role's project list."""
        unlinked_wo = WorkOrder.objects.create(
            project=None,
            work_order_number='AMMS-26-ORPHAN',
            vessel='Vessel Standalone',
            client='Client Standalone',
        )
        unlinked_wo.job_assigned_to.add(self.employee_user)

        resp = self.admin_client.get(reverse('admin_view_work_order', args=[unlinked_wo.id]))
        self.assertIn(f'href="{reverse("project-list")}"', resp.content.decode('utf-8'))

        resp = self.mgr_client.get(reverse('view_work_order', args=[unlinked_wo.id]))
        self.assertIn(f'href="{reverse("project_list")}"', resp.content.decode('utf-8'))

        resp = self.emp_client.get(reverse('employee-view-work-order', args=[unlinked_wo.id]))
        self.assertIn(f'href="{reverse("projects")}"', resp.content.decode('utf-8'))

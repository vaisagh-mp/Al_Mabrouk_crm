import datetime
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.template.loader import render_to_string
from django.contrib.auth.models import User
from Admin.models import Project, WorkOrder, WorkOrderDetail, Employee, Team, ActivityLog
from Admin.forms import ProjectForm
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


class WorkOrderJobScopeAndInstructionsTestCase(TestCase):
    def setUp(self):
        # Admin superuser
        self.admin_user = User.objects.create_superuser('wo_admin2', 'admin2@example.com', 'pass123')
        self.admin_emp = Employee.objects.create(
            user=self.admin_user, is_employee=False, is_manager=False, salary=Decimal('10000.00')
        )

        # Manager user
        self.manager_user = User.objects.create_user('wo_mgr2', 'mgr2@example.com', 'pass123')
        self.manager_emp = Employee.objects.create(
            user=self.manager_user, is_manager=True, is_employee=False, salary=Decimal('8000.00')
        )

        # Employee user
        self.employee_user = User.objects.create_user('wo_eng2', 'eng2@example.com', 'pass123')
        self.employee_emp = Employee.objects.create(
            user=self.employee_user, is_employee=True, salary=Decimal('4000.00')
        )

        # Create project
        self.project = Project.objects.create(
            name='Test Project Edit Scope',
            code='PRJ-2026-SCOPE',
            service_reference='SR-SCOPE-01',
            client_name='Scope Client LLC',
            vessel_name='MV Scope Vessel',
            status='IN_PROGRESS',
            manager=self.manager_emp,
        )

        # Create work order
        self.work_order = WorkOrder.objects.create(
            project=self.project,
            work_order_number='AMMS-26-SCOPE-01',
            vessel='MV Scope Vessel',
            client='Scope Client LLC',
            imo_no='9123456',
            location='Drydock 1',
            assigned_date=datetime.date(2026, 11, 4),
            job_scope='Initial Job Scope',
            job_instructions='Initial Job Instructions',
            project_description='Initial Project Description',
        )
        self.work_order.job_assigned_to.add(self.employee_user)

        self.work_order_detail = WorkOrderDetail.objects.create(
            work_order=self.work_order,
            start_date=datetime.date(2026, 11, 4),
            completion_date=datetime.date(2026, 11, 8),
        )

        # Clients
        self.admin_client = Client()
        self.admin_client.force_login(self.admin_user)

        self.mgr_client = Client()
        self.mgr_client.force_login(self.manager_user)

        self.emp_client = Client()
        self.emp_client.force_login(self.employee_user)

    def test_admin_and_manager_post_new_scope_and_instruction_saved_headers_unchanged(self):
        """Admin and Manager POST new scope/instruction -> saved; other header fields unchanged even if posted."""
        # 1. Admin updates scope and instructions, also trying to hack vessel and assigned_date
        resp = self.admin_client.post(
            reverse('admin-update-work-order', args=[self.work_order.id]),
            {
                'job_scope': 'New Admin Job Scope',
                'job_instructions': 'New Admin Instructions',
                'vessel': 'Hacked Vessel Name',
                'client': 'Hacked Client Name',
                'imo_no': '9999999',
                'location': 'Hacked Location',
                'assigned_date': '2020-01-01',
            }
        )
        self.assertEqual(resp.status_code, 302)
        self.work_order.refresh_from_db()
        # Scope and instructions updated
        self.assertEqual(self.work_order.job_scope, 'New Admin Job Scope')
        self.assertEqual(self.work_order.job_instructions, 'New Admin Instructions')
        # Other headers remain unchanged
        self.assertEqual(self.work_order.vessel, 'MV Scope Vessel')
        self.assertEqual(self.work_order.client, 'Scope Client LLC')
        self.assertEqual(self.work_order.imo_no, '9123456')
        self.assertEqual(self.work_order.location, 'Drydock 1')
        self.assertEqual(self.work_order.assigned_date, datetime.date(2026, 11, 4))

        # 2. Manager updates scope and instructions, also trying to hack vessel
        resp = self.mgr_client.post(
            reverse('manager-update-work-order', args=[self.work_order.id]),
            {
                'job_scope': 'New Manager Job Scope',
                'job_instructions': 'New Manager Instructions',
                'vessel': 'Another Hacked Vessel',
                'client': 'Another Hacked Client',
            }
        )
        self.assertEqual(resp.status_code, 302)
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.job_scope, 'New Manager Job Scope')
        self.assertEqual(self.work_order.job_instructions, 'New Manager Instructions')
        self.assertEqual(self.work_order.vessel, 'MV Scope Vessel')
        self.assertEqual(self.work_order.client, 'Scope Client LLC')

    def test_admin_and_manager_edit_project_description_saved_on_work_order_and_project(self):
        """Project Description edited on the work order is saved on both the work order and the project."""
        self.project.job_description = 'Initial Project Description'
        self.project.save()
        for client, url_name, text in (
            (self.admin_client, 'admin-update-work-order', 'Admin edited description'),
            (self.mgr_client, 'manager-update-work-order', 'Manager edited description'),
        ):
            resp = client.post(reverse(url_name, args=[self.work_order.id]), {'project_description': text})
            self.assertEqual(resp.status_code, 302)
            self.work_order.refresh_from_db()
            self.project.refresh_from_db()
            self.assertEqual(self.work_order.project_description, text)
            self.assertEqual(self.project.job_description, text)
            self.assertTrue(ActivityLog.objects.filter(project=self.project, new_status='Project Description Updated').exists())
        # The update page shows it in an editable box
        resp = self.admin_client.get(reverse('admin-update-work-order', args=[self.work_order.id]))
        self.assertContains(resp, 'name="project_description"')
        self.assertContains(resp, 'Manager edited description')
        # A post without the field leaves it alone
        self.mgr_client.post(reverse('manager-update-work-order', args=[self.work_order.id]), {})
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.project_description, 'Manager edited description')

    def test_post_without_fields_existing_values_unchanged(self):
        """POST without job_scope / job_instructions keys does not blank them out."""
        # Admin POST without job_scope / job_instructions
        resp = self.admin_client.post(
            reverse('admin-update-work-order', args=[self.work_order.id]),
            {}
        )
        self.assertEqual(resp.status_code, 302)
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.job_scope, 'Initial Job Scope')
        self.assertEqual(self.work_order.job_instructions, 'Initial Job Instructions')

        # Manager POST without job_scope / job_instructions
        resp = self.mgr_client.post(
            reverse('manager-update-work-order', args=[self.work_order.id]),
            {}
        )
        self.assertEqual(resp.status_code, 302)
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.job_scope, 'Initial Job Scope')
        self.assertEqual(self.work_order.job_instructions, 'Initial Job Instructions')

    def test_edited_value_appears_on_project_edit_form_view_work_order_and_pdf(self):
        """Edited value appears on project edit form, view_work_order, and PDF."""
        self.work_order.job_scope = 'Overhaul Auxiliary Engine 2'
        self.work_order.job_instructions = 'Tighten cylinder head bolts to 450 Nm'
        self.work_order.save()

        # 1. ProjectForm instance pre-fill
        form = ProjectForm(instance=self.project)
        self.assertEqual(form.fields['job_scope'].initial, 'Overhaul Auxiliary Engine 2')
        self.assertEqual(form.fields['job_instructions'].initial, 'Tighten cylinder head bolts to 450 Nm')

        # 2. Admin project edit GET page
        resp = self.admin_client.get(reverse('edit_project', args=[self.project.id]))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        self.assertIn('Overhaul Auxiliary Engine 2', content)
        self.assertIn('Tighten cylinder head bolts to 450 Nm', content)

        # 3. Manager project edit GET page
        resp = self.mgr_client.get(reverse('manager-edit-project', args=[self.project.id]))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        self.assertIn('Overhaul Auxiliary Engine 2', content)
        self.assertIn('Tighten cylinder head bolts to 450 Nm', content)

        # 4. View work order pages
        for client, url in [
            (self.admin_client, reverse('admin_view_work_order', args=[self.work_order.id])),
            (self.mgr_client, reverse('view_work_order', args=[self.work_order.id])),
            (self.emp_client, reverse('employee-view-work-order', args=[self.work_order.id])),
        ]:
            resp = client.get(url)
            self.assertEqual(resp.status_code, 200)
            c = resp.content.decode('utf-8')
            self.assertIn('Overhaul Auxiliary Engine 2', c)
            self.assertIn('Tighten cylinder head bolts to 450 Nm', c)

        # 5. Work Order PDF
        rendered_pdf = render_to_string('Manager/work_order_pdf.html', {
            'work_order': self.work_order,
            'work_order_detail': self.work_order_detail,
            'live_members': [self.employee_user],
            'all_members': [self.employee_user],
            'time_entries': [],
            'member_totals': {},
            'calculated_hours': 0,
            'image_groups': {},
        })
        self.assertIn('Overhaul Auxiliary Engine 2', rendered_pdf)
        self.assertIn('Tighten cylinder head bolts to 450 Nm', rendered_pdf)

    def test_xss_protection_in_scope_and_instructions(self):
        """<script>alert(1)</script> saved and rendered escaped in view_work_order and PDF."""
        resp = self.admin_client.post(
            reverse('admin-update-work-order', args=[self.work_order.id]),
            {
                'job_scope': '<script>alert(1)</script>',
                'job_instructions': '<script>alert(2)</script>',
            }
        )
        self.assertEqual(resp.status_code, 302)
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.job_scope, '<script>alert(1)</script>')
        self.assertEqual(self.work_order.job_instructions, '<script>alert(2)</script>')

        # Check view pages: never unescaped script tags
        for client, url in [
            (self.admin_client, reverse('admin_view_work_order', args=[self.work_order.id])),
            (self.mgr_client, reverse('view_work_order', args=[self.work_order.id])),
            (self.emp_client, reverse('employee-view-work-order', args=[self.work_order.id])),
        ]:
            r = client.get(url)
            c = r.content.decode('utf-8')
            self.assertNotIn('<script>alert(1)</script>', c)
            self.assertNotIn('<script>alert(2)</script>', c)
            self.assertNotIn('<script>alert', c)

        # Check PDF template
        rendered_pdf = render_to_string('Manager/work_order_pdf.html', {
            'work_order': self.work_order,
            'work_order_detail': self.work_order_detail,
            'live_members': [self.employee_user],
            'all_members': [self.employee_user],
            'time_entries': [],
            'member_totals': {},
            'calculated_hours': 0,
            'image_groups': {},
        })
        self.assertNotIn('<script>alert(1)</script>', rendered_pdf)
        self.assertNotIn('<script>alert(2)</script>', rendered_pdf)
        self.assertNotIn('<script>', rendered_pdf)

    def test_activity_log_created_only_when_value_changes(self):
        """ActivityLog created only when job_scope or job_instructions changes."""
        ActivityLog.objects.filter(project=self.project).delete()

        # 1. POST identical values -> NO activity log
        self.admin_client.post(
            reverse('admin-update-work-order', args=[self.work_order.id]),
            {
                'job_scope': 'Initial Job Scope',
                'job_instructions': 'Initial Job Instructions',
            }
        )
        self.assertEqual(ActivityLog.objects.filter(project=self.project).count(), 0)

        # 2. POST changed scope only -> 1 activity log
        self.admin_client.post(
            reverse('admin-update-work-order', args=[self.work_order.id]),
            {
                'job_scope': 'Updated Job Scope V2',
                'job_instructions': 'Initial Job Instructions',
            }
        )
        self.assertEqual(ActivityLog.objects.filter(project=self.project).count(), 1)
        log1 = ActivityLog.objects.filter(project=self.project).first()
        self.assertIn('Job Scope updated on work order by', log1.notes)
        self.assertIn(self.admin_user.username, log1.notes)

        # 3. Manager POST changed instructions only -> 1 more activity log
        self.mgr_client.post(
            reverse('manager-update-work-order', args=[self.work_order.id]),
            {
                'job_scope': 'Updated Job Scope V2',
                'job_instructions': 'Updated Job Instructions V2',
            }
        )
        self.assertEqual(ActivityLog.objects.filter(project=self.project).count(), 2)
        log2 = ActivityLog.objects.filter(project=self.project).last()
        self.assertIn('Job Instruction updated on work order by', log2.notes)
        self.assertIn(self.manager_user.username, log2.notes)

    def test_engineer_post_with_job_scope_ignored(self):
        """Engineer POST with job_scope / job_instructions is ignored."""
        resp = self.emp_client.post(
            reverse('employee-update-work-order', args=[self.work_order.id]),
            {
                'job_scope': 'Hacked by Engineer',
                'job_instructions': 'Hacked instructions by Engineer',
            }
        )
        self.assertEqual(resp.status_code, 302)
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.job_scope, 'Initial Job Scope')
        self.assertEqual(self.work_order.job_instructions, 'Initial Job Instructions')

    def test_update_work_order_templates_have_editable_textareas(self):
        """Admin and Manager update work order pages render editable textareas with name and rows=4."""
        # Admin page
        resp = self.admin_client.get(reverse('admin-update-work-order', args=[self.work_order.id]))
        self.assertEqual(resp.status_code, 200)
        c = resp.content.decode('utf-8')
        self.assertIn('name="job_scope"', c)
        self.assertIn('name="job_instructions"', c)
        self.assertIn('rows="4"', c)

        # Manager page
        resp = self.mgr_client.get(reverse('manager-update-work-order', args=[self.work_order.id]))
        self.assertEqual(resp.status_code, 200)
        c = resp.content.decode('utf-8')
        self.assertIn('name="job_scope"', c)
        self.assertIn('name="job_instructions"', c)
        self.assertIn('rows="4"', c)

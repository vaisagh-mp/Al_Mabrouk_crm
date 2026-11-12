import datetime
from decimal import Decimal
import io
import json
import os
import openpyxl

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from Admin.models import (
    Employee, Department, Branch, DutySchedule,
    DocumentType, EmployeeDocument, CompanyDocument,
    DocumentReminderLog, Notification, EmployeeChangeLog,
    SalaryStructure, AttendanceCorrection, Leave
)
from Admin.forms import EmployeeCreationForm, ManagerEmployeeUpdateForm
from Admin.permissions import can_view_salary, can_view_document
from Admin.document_helpers import (
    validate_document_file, get_document_status,
    get_document_expiry_counts, get_employee_overall_doc_badge
)


class HRPhase6ModelAndFormTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="Operations", sort_order=1)
        self.branch = Branch.objects.create(name="Dubai", sort_order=1)
        self.user = User.objects.create_user(
            'john_doe', 'john@example.com', 'pass123',
            first_name="John", last_name="Doe"
        )
        self.employee = Employee.objects.create(
            user=self.user,
            employee_code="AMMS-1001",
            department=self.dept,
            branch=self.branch,
            is_employee=True,
            employment_status='ACTIVE',
            date_of_join=datetime.date(2023, 1, 1),
            contract_start_date=datetime.date(2023, 1, 1),
            contract_end_date=datetime.date(2025, 1, 1),
        )

    def test_notice_date_validation_on_model_clean(self):
        # 1. last_working_day < notice_start_date raises ValidationError
        self.employee.notice_start_date = datetime.date(2024, 6, 1)
        self.employee.last_working_day = datetime.date(2024, 5, 1)
        with self.assertRaises(ValidationError) as ctx:
            self.employee.clean()
        self.assertIn("last_working_day", ctx.exception.message_dict)

        # 2. NOTICE_PERIOD requires notice_start_date
        self.employee.notice_start_date = None
        self.employee.last_working_day = None
        self.employee.employment_status = 'NOTICE_PERIOD'
        with self.assertRaises(ValidationError) as ctx:
            self.employee.clean()
        self.assertIn("notice_start_date", ctx.exception.message_dict)

        # 3. RESIGNED requires last_working_day
        self.employee.employment_status = 'RESIGNED'
        with self.assertRaises(ValidationError) as ctx:
            self.employee.clean()
        self.assertIn("last_working_day", ctx.exception.message_dict)

        # 4. Valid combination clean passes
        self.employee.notice_start_date = datetime.date(2024, 6, 1)
        self.employee.last_working_day = datetime.date(2024, 6, 30)
        self.employee.clean()  # Should not raise

    def test_manager_employee_update_form_and_changelog(self):
        admin_user = User.objects.create_superuser('admin_audit', 'admin@example.com', 'pass123')
        form_data = {
            'first_name': 'John',
            'last_name': 'Doe',
            'personal_email': 'john@example.com',
            'employment_status': 'NOTICE_PERIOD',
            'work_type': 'OFFICE',
            'department': self.dept.id,
            'branch': self.branch.id,
            'notice_start_date': '2024-06-01',
            'last_working_day': '2024-07-01',
            'is_active': True,
            'is_active_present': '1',
        }
        form = ManagerEmployeeUpdateForm(data=form_data, instance=self.employee, user=admin_user)
        self.assertTrue(form.is_valid(), form.errors)
        form.save(changed_by=admin_user, source="HR Edit Test")

        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employment_status, 'NOTICE_PERIOD')
        self.assertEqual(self.employee.notice_start_date, datetime.date(2024, 6, 1))
        self.assertEqual(self.employee.last_working_day, datetime.date(2024, 7, 1))

        # Change logs should record both fields
        notice_log = EmployeeChangeLog.objects.filter(employee=self.employee, field='notice_start_date').first()
        self.assertIsNotNone(notice_log)
        self.assertEqual(notice_log.new_value, '2024-06-01')

        lwd_log = EmployeeChangeLog.objects.filter(employee=self.employee, field='last_working_day').first()
        self.assertIsNotNone(lwd_log)
        self.assertEqual(lwd_log.new_value, '2024-07-01')


class HRPhase6DocumentStorageAndExpiryTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin_doc', 'admin@example.com', 'pass123')
        self.hr_user = User.objects.create_user('hr_doc', 'hr@example.com', 'pass123')
        self.mgr_user = User.objects.create_user('mgr_doc', 'mgr@example.com', 'pass123')
        self.other_mgr_user = User.objects.create_user('other_mgr_doc', 'other@example.com', 'pass123')
        self.emp_user = User.objects.create_user('emp_doc', 'emp@example.com', 'pass123')
        self.accountant_user = User.objects.create_user('acc_doc', 'acc@example.com', 'pass123')
        self.admin_staff_user = User.objects.create_user('adminstaff_doc', 'adminstaff@example.com', 'pass123')
        self.other_emp_user = User.objects.create_user('other_emp_doc', 'otheremp@example.com', 'pass123')

        self.dept = Department.objects.create(name="Fleet", sort_order=1)
        self.branch = Branch.objects.create(name="Sharjah", sort_order=1)

        self.hr_emp = Employee.objects.create(user=self.hr_user, is_hr=True, employee_code="AMMS-0002")
        self.mgr_emp = Employee.objects.create(user=self.mgr_user, is_manager=True, employee_code="AMMS-0003")
        self.other_mgr_emp = Employee.objects.create(user=self.other_mgr_user, is_manager=True, employee_code="AMMS-0004")
        self.accountant_emp = Employee.objects.create(user=self.accountant_user, is_accountant=True, employee_code="AMMS-0006")
        self.admin_staff_emp = Employee.objects.create(user=self.admin_staff_user, is_administration=True, employee_code="AMMS-0007")
        self.other_emp = Employee.objects.create(user=self.other_emp_user, is_employee=True, employee_code="AMMS-0008")

        self.employee = Employee.objects.create(
            user=self.emp_user,
            is_employee=True,
            employee_code="AMMS-0005",
            department=self.dept,
            branch=self.branch,
            reporting_manager=self.mgr_emp,
        )

        self.passport_type = DocumentType.objects.get(name="Passport")
        self.trade_license_type = DocumentType.objects.get(name="Trade License")

    def test_file_validation_allowed_extensions_and_size(self):
        # Valid PDF
        valid_pdf = SimpleUploadedFile("test.pdf", b"%PDF-1.4 ...", content_type="application/pdf")
        validate_document_file(valid_pdf)  # No error

        # Valid PNG
        valid_png = SimpleUploadedFile("test.png", b"\x89PNG\r\n\x1a\n", content_type="image/png")
        validate_document_file(valid_png)  # No error

        # Invalid extension (.exe, .docx)
        invalid_file = SimpleUploadedFile("test.exe", b"binary", content_type="application/octet-stream")
        with self.assertRaises(ValidationError):
            validate_document_file(invalid_file)

        # File > 10MB
        large_content = b"0" * (10 * 1024 * 1024 + 1)
        large_file = SimpleUploadedFile("big.pdf", large_content, content_type="application/pdf")
        with self.assertRaises(ValidationError):
            validate_document_file(large_file)

    def test_private_media_storage_location_and_permissions(self):
        content = b"%PDF-1.4 confidential doc"
        up_file = SimpleUploadedFile("passport.pdf", content, content_type="application/pdf")

        doc = EmployeeDocument.objects.create(
            employee=self.employee,
            doc_type=self.passport_type,
            document_number="A12345678",
            expiry_date=datetime.date(2027, 1, 1),
            file=up_file,
            created_by=self.admin,
        )

        # 1. File path must be inside PRIVATE_MEDIA_ROOT, not MEDIA_ROOT
        self.assertTrue(doc.file.path.startswith(str(settings.PRIVATE_MEDIA_ROOT)))
        self.assertFalse(doc.file.path.startswith(str(settings.MEDIA_ROOT)))

        # 2. Permissions check
        self.assertTrue(can_view_document(self.admin, doc))
        self.assertTrue(can_view_document(self.hr_user, doc))
        self.assertTrue(can_view_document(self.emp_user, doc))
        self.assertFalse(can_view_document(self.mgr_user, doc))  # Manager cannot download
        self.assertFalse(can_view_document(self.accountant_user, doc))  # Accountant cannot download
        self.assertFalse(can_view_document(self.other_emp_user, doc))  # Other employee cannot download
        self.assertFalse(can_view_document(self.admin_staff_user, doc))  # Administration cannot download

        # 3. Download view: HR 200, superuser 200, owner 200, manager 403, accountant 403, other employee 403, administration 403
        client = Client()

        client.force_login(self.hr_user)
        self.assertEqual(client.get(reverse('document_download', args=['employee', doc.id])).status_code, 200)

        client.force_login(self.admin)
        self.assertEqual(client.get(reverse('document_download', args=['employee', doc.id])).status_code, 200)

        client.force_login(self.emp_user)
        self.assertEqual(client.get(reverse('document_download', args=['employee', doc.id])).status_code, 200)

        client.force_login(self.mgr_user)
        self.assertEqual(client.get(reverse('document_download', args=['employee', doc.id])).status_code, 403)

        client.force_login(self.accountant_user)
        self.assertEqual(client.get(reverse('document_download', args=['employee', doc.id])).status_code, 403)

        client.force_login(self.other_emp_user)
        self.assertEqual(client.get(reverse('document_download', args=['employee', doc.id])).status_code, 403)

        client.force_login(self.admin_staff_user)
        self.assertEqual(client.get(reverse('document_download', args=['employee', doc.id])).status_code, 403)

        # Company document permissions: superuser 200, HR 200, others 403
        comp_file = SimpleUploadedFile("trade_license.pdf", b"%PDF-1.4 company license", content_type="application/pdf")
        comp_doc = CompanyDocument.objects.create(
            title="Commercial Trade License",
            doc_type=self.trade_license_type,
            file=comp_file,
            created_by=self.admin,
        )
        client.force_login(self.admin)
        self.assertEqual(client.get(reverse('document_download', args=['company', comp_doc.id])).status_code, 200)

        client.force_login(self.hr_user)
        self.assertEqual(client.get(reverse('document_download', args=['company', comp_doc.id])).status_code, 200)

        client.force_login(self.mgr_user)
        self.assertEqual(client.get(reverse('document_download', args=['company', comp_doc.id])).status_code, 403)

        client.force_login(self.accountant_user)
        self.assertEqual(client.get(reverse('document_download', args=['company', comp_doc.id])).status_code, 403)

        # Manager's profile Documents tab shows metadata without download links
        client.force_login(self.mgr_user)
        prof_resp = client.get(reverse('manager_employee_profile', args=[self.employee.id]) + '?tab=documents')
        self.assertEqual(prof_resp.status_code, 200)
        self.assertContains(prof_resp, "Passport")
        self.assertContains(prof_resp, "A12345678")
        self.assertNotContains(prof_resp, "document_download")

    def test_document_renewal_versioning_and_reminder_reset(self):
        up_file1 = SimpleUploadedFile("doc_v1.pdf", b"v1 content", content_type="application/pdf")
        doc_v1 = EmployeeDocument.objects.create(
            employee=self.employee,
            doc_type=self.passport_type,
            document_number="V1-NUM",
            expiry_date=datetime.date(2026, 12, 1),
            file=up_file1,
            is_current=True,
            version=1,
            created_by=self.admin,
        )
        # Create reminder log for v1
        DocumentReminderLog.objects.create(
            document_type_category='EMPLOYEE',
            employee_document=doc_v1,
            threshold_days=30,
            recipient_count=2,
        )

        client = Client()
        client.force_login(self.hr_user)

        up_file2 = SimpleUploadedFile("doc_v2.pdf", b"v2 content", content_type="application/pdf")
        renew_url = reverse('document_renew', args=['employee', doc_v1.id])
        post_data = {
            'document_number': 'V2-NUM',
            'issue_date': '2026-11-01',
            'expiry_date': '2036-11-01',
            'issuing_authority': 'UAE FAIC',
            'file': up_file2,
        }
        resp = client.post(renew_url, post_data)
        self.assertEqual(resp.status_code, 302)

        doc_v1.refresh_from_db()
        self.assertFalse(doc_v1.is_current)

        doc_v2 = EmployeeDocument.objects.get(employee=self.employee, doc_type=self.passport_type, is_current=True)
        self.assertEqual(doc_v2.version, 2)
        self.assertEqual(doc_v2.previous, doc_v1)
        self.assertEqual(doc_v2.document_number, 'V2-NUM')

        # Reminders reset: doc_v2 has no entries in DocumentReminderLog
        self.assertFalse(DocumentReminderLog.objects.filter(employee_document=doc_v2).exists())

    def test_computed_status_buckets(self):
        ref_today = datetime.date(2026, 11, 9)

        # 91d -> Valid
        d91 = EmployeeDocument(doc_type=self.passport_type, expiry_date=ref_today + datetime.timedelta(days=91))
        st91 = get_document_status(d91, as_of=ref_today)
        self.assertEqual(st91['bucket'], 'valid')
        self.assertEqual(st91['code'], 'VALID')

        # 90d -> <=90
        d90 = EmployeeDocument(doc_type=self.passport_type, expiry_date=ref_today + datetime.timedelta(days=90))
        st90 = get_document_status(d90, as_of=ref_today)
        self.assertEqual(st90['bucket'], '90')
        self.assertEqual(st90['code'], 'EXPIRING_90')

        # 60d -> <=60
        d60 = EmployeeDocument(doc_type=self.passport_type, expiry_date=ref_today + datetime.timedelta(days=60))
        st60 = get_document_status(d60, as_of=ref_today)
        self.assertEqual(st60['bucket'], '60')
        self.assertEqual(st60['code'], 'EXPIRING_60')

        # 31d -> <=60
        d31 = EmployeeDocument(doc_type=self.passport_type, expiry_date=ref_today + datetime.timedelta(days=31))
        st31 = get_document_status(d31, as_of=ref_today)
        self.assertEqual(st31['bucket'], '60')

        # 30d -> <=30
        d30 = EmployeeDocument(doc_type=self.passport_type, expiry_date=ref_today + datetime.timedelta(days=30))
        st30 = get_document_status(d30, as_of=ref_today)
        self.assertEqual(st30['bucket'], '30')
        self.assertEqual(st30['code'], 'EXPIRING_30')

        # 0d -> <=30
        d0 = EmployeeDocument(doc_type=self.passport_type, expiry_date=ref_today)
        st0 = get_document_status(d0, as_of=ref_today)
        self.assertEqual(st0['bucket'], '30')

        # -1d -> Expired
        dm1 = EmployeeDocument(doc_type=self.passport_type, expiry_date=ref_today - datetime.timedelta(days=1))
        stm1 = get_document_status(dm1, as_of=ref_today)
        self.assertEqual(stm1['bucket'], 'expired')
        self.assertEqual(stm1['code'], 'EXPIRED')

    def test_send_document_reminders_command_idempotency_and_dry_run(self):
        ref_today = datetime.date(2026, 11, 9)
        # Create an expiring doc (expires in 15 days, <= 30 threshold)
        doc = EmployeeDocument.objects.create(
            employee=self.employee,
            doc_type=self.passport_type,
            expiry_date=ref_today + datetime.timedelta(days=15),
            is_current=True,
            created_by=self.admin,
        )

        # 1. Dry run should NOT create notifications or logs
        call_command('send_document_reminders', '--dry-run', f'--as-of={ref_today.isoformat()}')
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(DocumentReminderLog.objects.count(), 0)

        # 2. Real run creates notifications and log
        call_command('send_document_reminders', f'--as-of={ref_today.isoformat()}')
        self.assertEqual(DocumentReminderLog.objects.filter(employee_document=doc).count(), 1)
        initial_notif_count = Notification.objects.count()
        self.assertGreater(initial_notif_count, 0)

        # 3. Idempotent: second run does not duplicate notifications or logs
        call_command('send_document_reminders', f'--as-of={ref_today.isoformat()}')
        self.assertEqual(DocumentReminderLog.objects.filter(employee_document=doc).count(), 1)
        self.assertEqual(Notification.objects.count(), initial_notif_count)

    def test_send_document_reminders_timeline_and_expired_threshold(self):
        # Expiry 01/12/2026:
        # 02/09 (90), 02/10 (60), 01/11 (30), 01/12 (expired) fire once each; 05/12 nothing more.
        expiry_date = datetime.date(2026, 12, 1)
        doc = EmployeeDocument.objects.create(
            employee=self.employee,
            doc_type=self.passport_type,
            expiry_date=expiry_date,
            is_current=True,
            created_by=self.admin,
        )

        # 02/09/2026 (90 days left)
        d90 = datetime.date(2026, 9, 2)
        call_command('send_document_reminders', f'--as-of={d90.isoformat()}')
        self.assertTrue(DocumentReminderLog.objects.filter(employee_document=doc, threshold_days=90).exists())
        self.assertEqual(DocumentReminderLog.objects.filter(employee_document=doc).count(), 1)

        # 02/10/2026 (60 days left)
        d60 = datetime.date(2026, 10, 2)
        call_command('send_document_reminders', f'--as-of={d60.isoformat()}')
        self.assertTrue(DocumentReminderLog.objects.filter(employee_document=doc, threshold_days=60).exists())
        self.assertEqual(DocumentReminderLog.objects.filter(employee_document=doc).count(), 2)

        # 01/11/2026 (30 days left)
        d30 = datetime.date(2026, 11, 1)
        call_command('send_document_reminders', f'--as-of={d30.isoformat()}')
        self.assertTrue(DocumentReminderLog.objects.filter(employee_document=doc, threshold_days=30).exists())
        self.assertEqual(DocumentReminderLog.objects.filter(employee_document=doc).count(), 3)

        # 01/12/2026 (expired, 0 days left)
        d0 = datetime.date(2026, 12, 1)
        call_command('send_document_reminders', f'--as-of={d0.isoformat()}')
        self.assertTrue(DocumentReminderLog.objects.filter(employee_document=doc, threshold_days=0).exists())
        self.assertEqual(DocumentReminderLog.objects.filter(employee_document=doc).count(), 4)
        latest_notif = Notification.objects.order_by('-id').first()
        self.assertIn("Document EXPIRED", latest_notif.message)

        # 05/12/2026 (4 days after expiry) - nothing more
        d_after = datetime.date(2026, 12, 5)
        call_command('send_document_reminders', f'--as-of={d_after.isoformat()}')
        self.assertEqual(DocumentReminderLog.objects.filter(employee_document=doc).count(), 4)

        # Check DocumentType.get_reminder_days_list always includes 0 and is deduped
        self.assertIn(0, self.passport_type.get_reminder_days_list())
        self.passport_type.reminder_days = "90,60,30,0"
        self.assertEqual(self.passport_type.get_reminder_days_list(), [90, 60, 30, 0])
        self.passport_type.reminder_days = "45, 15"
        self.assertEqual(self.passport_type.get_reminder_days_list(), [45, 15, 0])

    def test_document_import_template_preview_and_confirm(self):
        client = Client()
        client.force_login(self.hr_user)

        # 1. Template download
        resp = client.get(f"{reverse('document_import')}?action=template")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/vnd.openxmlformats", resp['Content-Type'])

        # 2. Create an in-memory workbook for import preview
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Employee Code", "Document Type", "Document Number", "Issue Date (YYYY-MM-DD)", "Expiry Date (YYYY-MM-DD)", "Issuing Authority", "Notes"])
        ws.append([self.employee.employee_code, "Passport", "P98765432", "2024-01-01", "2034-01-01", "Embassy", "Bulk import"])
        ws.append(["INVALID-CODE", "Passport", "P000", "2024-01-01", "2034-01-01", "Embassy", "Bad code"])

        out = io.BytesIO()
        wb.save(out)
        out.seek(0)
        upload = SimpleUploadedFile("import.xlsx", out.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        resp = client.post(reverse('document_import'), {'file': upload})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("preview_rows", resp.context)
        self.assertTrue(resp.context['has_errors'])  # Because of INVALID-CODE

        # 3. Confirm valid row import
        valid_row_json = json.dumps([{
            'row_idx': 2,
            'emp_code': self.employee.employee_code,
            'doc_type_name': 'Passport',
            'document_number': 'P98765432',
            'issue_date': '2024-01-01',
            'expiry_date': '2034-01-01',
            'issuing_authority': 'Embassy',
            'notes': 'Bulk confirmed',
        }])
        resp = client.post(reverse('document_import'), {'action': 'confirm', 'rows_json': valid_row_json})
        self.assertEqual(resp.status_code, 302)

        imported_doc = EmployeeDocument.objects.filter(employee=self.employee, document_number='P98765432').first()
        self.assertIsNotNone(imported_doc)
        self.assertFalse(bool(imported_doc.file))  # File missing status


class HRPhase6ListAndProfileViewsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin_v', 'admin@example.com', 'pass123')
        self.hr_user = User.objects.create_user('hr_v', 'hr@example.com', 'pass123')
        self.mgr_user = User.objects.create_user('mgr_v', 'mgr@example.com', 'pass123')
        self.other_mgr_user = User.objects.create_user('other_mgr_v', 'other@example.com', 'pass123')
        self.emp_user = User.objects.create_user('emp_v', 'emp@example.com', 'pass123')

        self.dept = Department.objects.create(name="Engineering", sort_order=1)
        self.branch = Branch.objects.create(name="Dubai Port", sort_order=1)

        self.hr_emp = Employee.objects.create(user=self.hr_user, is_hr=True, employee_code="AMMS-0010")
        self.mgr_emp = Employee.objects.create(user=self.mgr_user, is_manager=True, employee_code="AMMS-0020")
        self.other_mgr_emp = Employee.objects.create(user=self.other_mgr_user, is_manager=True, employee_code="AMMS-0030")

        self.report_emp = Employee.objects.create(
            user=self.emp_user,
            employee_code="AMMS-0040",
            department=self.dept,
            branch=self.branch,
            is_employee=True,
            rank="Senior Technician",
            reporting_manager=self.mgr_emp,
            date_of_join=datetime.date(2022, 5, 1),
            salary=Decimal("12000.00"),
        )
        # Create salary structure
        SalaryStructure.objects.create(
            employee=self.report_emp,
            basic=Decimal("7000.00"),
            housing_allowance=Decimal("3000.00"),
            transport_allowance=Decimal("2000.00"),
            other_allowances=Decimal("0.00"),
            total=Decimal("12000.00"),
            effective_from=datetime.date(2022, 5, 1),
            created_by=self.admin,
        )

    def test_manager_employee_list_scoping(self):
        client = Client()
        client.force_login(self.mgr_user)

        resp = client.get(reverse('manager_employee_list'))
        self.assertEqual(resp.status_code, 200)
        employees_in_list = list(resp.context['page_obj'])
        self.assertIn(self.report_emp, employees_in_list)

        # Other manager's list should not include report_emp
        client.force_login(self.other_mgr_user)
        resp2 = client.get(reverse('manager_employee_list'))
        self.assertEqual(resp2.status_code, 200)
        self.assertNotIn(self.report_emp, list(resp2.context['page_obj']))

    def test_employee_profile_manager_scoping_and_forbidden(self):
        client = Client()

        # Direct manager accessing report_emp -> 200
        client.force_login(self.mgr_user)
        resp = client.get(reverse('employee_profile_view', args=[self.report_emp.id]))
        self.assertEqual(resp.status_code, 200)

        # Other manager accessing report_emp -> 403 Forbidden
        client.force_login(self.other_mgr_user)
        resp_forbidden = client.get(reverse('employee_profile_view', args=[self.report_emp.id]))
        self.assertEqual(resp_forbidden.status_code, 403)

    def test_salary_tab_strict_permission_and_context_gating(self):
        client = Client()

        # Manager requests ?tab=salary directly -> STRICT 403 Forbidden
        client.force_login(self.mgr_user)
        resp = client.get(f"{reverse('employee_profile_view', args=[self.report_emp.id])}?tab=salary")
        self.assertEqual(resp.status_code, 403)

        # Manager requests ?tab=overview -> 200, context['can_view_salary'] is False, no salary in context
        resp_ov = client.get(f"{reverse('employee_profile_view', args=[self.report_emp.id])}?tab=overview")
        self.assertEqual(resp_ov.status_code, 200)
        self.assertFalse(resp_ov.context['can_view_salary'])
        self.assertNotIn('current_salary', resp_ov.context)

        # HR requests ?tab=salary -> 200, receives salary details
        client.force_login(self.hr_user)
        resp_hr = client.get(f"{reverse('hr_employee_profile', args=[self.report_emp.id])}?tab=salary")
        self.assertEqual(resp_hr.status_code, 200)
        self.assertTrue(resp_hr.context['can_view_salary'])
        self.assertIn('current_salary', resp_hr.context)

    def test_employee_profile_all_seven_tabs(self):
        client = Client()
        client.force_login(self.hr_user)
        base_url = reverse('hr_employee_profile', args=[self.report_emp.id])

        for tab in ['overview', 'employment', 'attendance', 'leave', 'salary', 'documents', 'history']:
            resp = client.get(f"{base_url}?tab={tab}")
            self.assertEqual(resp.status_code, 200, f"Tab {tab} failed with {resp.status_code}")

    def test_employee_list_exports_salary_gating(self):
        client = Client()

        # 1. HR Excel export contains salary columns
        client.force_login(self.hr_user)
        resp_hr_excel = client.get(f"{reverse('hr_employee_list')}?export=excel")
        self.assertEqual(resp_hr_excel.status_code, 200)
        wb_hr = openpyxl.load_workbook(io.BytesIO(resp_hr_excel.content))
        ws_hr = wb_hr.active
        headers_hr = [cell.value for cell in ws_hr[1]]
        self.assertIn("Basic", headers_hr)
        self.assertIn("Total Salary", headers_hr)

        # 2. PDF export does not contain salary
        resp_pdf = client.get(f"{reverse('hr_employee_list')}?export=pdf")
        self.assertEqual(resp_pdf.status_code, 200)
        self.assertEqual(resp_pdf['Content-Type'], 'application/pdf')


import datetime
from decimal import Decimal
import io
import openpyxl

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from Admin.models import (
    Employee, Department, Branch, Attendance, DailyAttendanceStatus,
    AttendanceCorrection, Notification, EmployeeChangeLog, Holiday
)
from Admin.attendance_register import (
    build_attendance_register, build_attendance_summary,
    build_monthly_calendar_matrix
)
from Admin.attendance_correction import (
    submit_correction_request, approve_correction, reject_correction,
    cancel_correction, can_decide_correction
)
from finance.models import Payroll
from service_logs.models import EmployeeServiceLog


class HRPhase3CorrectionEngineTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Dubai Marine Base", sort_order=1)
        self.dept = Department.objects.create(name="Technical Services", sort_order=1)

        # Manager user & employee
        self.mgr_user = User.objects.create_user('mgr_usr', 'mgr@amms.ae', 'pass123', first_name='Tariq', last_name='Manager')
        self.mgr_emp = Employee.objects.create(
            user=self.mgr_user,
            employee_code='AMMS-0001',
            salary=Decimal('15000.00'),
            date_of_join=datetime.date(2024, 1, 1),
            branch=self.branch,
            department=self.dept,
            is_manager=True
        )

        # Other Manager
        self.other_mgr_user = User.objects.create_user('oth_mgr', 'oth@amms.ae', 'pass123')
        self.other_mgr_emp = Employee.objects.create(
            user=self.other_mgr_user,
            employee_code='AMMS-0002',
            salary=Decimal('14000.00'),
            date_of_join=datetime.date(2024, 1, 1),
            branch=self.branch,
            department=self.dept,
            is_manager=True
        )

        # Staff employee reporting to mgr_emp
        self.staff_user = User.objects.create_user('staff_usr', 'staff@amms.ae', 'pass123', first_name='Salem', last_name='Staff')
        self.staff_emp = Employee.objects.create(
            user=self.staff_user,
            employee_code='AMMS-0003',
            salary=Decimal('8000.00'),
            date_of_join=datetime.date(2025, 1, 1),
            branch=self.branch,
            department=self.dept,
            reporting_manager=self.mgr_emp,
            is_employee=True
        )

        # HR user & employee
        self.hr_user = User.objects.create_user('hr_usr', 'hr@amms.ae', 'pass123', first_name='Hessa', last_name='HR')
        self.hr_emp = Employee.objects.create(
            user=self.hr_user,
            employee_code='AMMS-0004',
            salary=Decimal('12000.00'),
            date_of_join=datetime.date(2024, 5, 1),
            branch=self.branch,
            department=self.dept,
            is_hr=True
        )

        # Admin superuser
        self.admin_user = User.objects.create_superuser('admin_usr', 'admin@amms.ae', 'pass123')
        self.admin_emp = Employee.objects.create(
            user=self.admin_user,
            employee_code='AMMS-0005',
            salary=Decimal('20000.00'),
            date_of_join=datetime.date(2023, 1, 1),
            branch=self.branch,
            department=self.dept,
            is_hr=True
        )

        self.tz = timezone.get_current_timezone()

    def test_01_submit_correction_types_and_defaults(self):
        """Submit each correction type successfully with proper defaults and notifications."""
        work_date = datetime.date(2026, 6, 1)
        in_time = timezone.make_aware(datetime.datetime(2026, 6, 1, 8, 0), self.tz)
        out_time = timezone.make_aware(datetime.datetime(2026, 6, 1, 17, 0), self.tz)

        types = ['FORGOT_IN', 'FORGOT_OUT', 'NO_PUNCH', 'WRONG_TIME']
        for i, c_type in enumerate(types, 1):
            d = datetime.date(2026, 6, i)
            corr = submit_correction_request(
                employee=self.staff_emp,
                date=d,
                correction_type=c_type,
                requested_login=in_time,
                requested_logout=out_time,
                location='BDO',
                reason=f"Forgot punch on day {i}",
                submitted_by=self.staff_user
            )
            self.assertEqual(corr.status, 'PENDING')
            self.assertEqual(corr.approver, self.mgr_emp)
            self.assertEqual(corr.correction_type, c_type)

            # Notification sent to reporting manager
            notif = Notification.objects.filter(recipient=self.mgr_user).order_by('-created_at').first()
            self.assertIsNotNone(notif)
            self.assertIn("Attendance correction request submitted", notif.message)

    def test_02_validations_future_join_date_order(self):
        """Validations: future date, date before date of join, logout before login, window cutoff."""
        today = timezone.localdate()
        future_date = today + datetime.timedelta(days=2)
        before_join = datetime.date(2024, 12, 31)  # joined 2025-01-01

        in_time = timezone.make_aware(datetime.datetime(today.year, today.month, 1, 8, 0), self.tz)
        out_time = timezone.make_aware(datetime.datetime(today.year, today.month, 1, 17, 0), self.tz)

        # 1. Future date rejected
        corr_future = AttendanceCorrection(
            employee=self.staff_emp, date=future_date, correction_type='NO_PUNCH',
            requested_login=in_time, requested_logout=out_time, location='BDO',
            reason="Worked future day"
        )
        with self.assertRaises(ValidationError) as cm:
            corr_future.full_clean()
        self.assertIn('date', cm.exception.message_dict)

        # 2. Before date of join rejected
        corr_before = AttendanceCorrection(
            employee=self.staff_emp, date=before_join, correction_type='NO_PUNCH',
            requested_login=in_time, requested_logout=out_time, location='BDO',
            reason="Worked before joined"
        )
        with self.assertRaises(ValidationError) as cm:
            corr_before.full_clean()
        self.assertIn('date', cm.exception.message_dict)

        # 3. Logout <= login rejected
        corr_order = AttendanceCorrection(
            employee=self.staff_emp, date=today, correction_type='NO_PUNCH',
            requested_login=out_time, requested_logout=in_time, location='BDO',
            reason="Invalid time order"
        )
        with self.assertRaises(ValidationError) as cm:
            corr_order.full_clean()
        self.assertIn('requested_logout', cm.exception.message_dict)

        # 4. Beyond session cutoff rejected
        far_out = in_time + datetime.timedelta(hours=20)
        corr_far = AttendanceCorrection(
            employee=self.staff_emp, date=today, correction_type='NO_PUNCH',
            requested_login=in_time, requested_logout=far_out, location='BDO',
            reason="Too long window"
        )
        with self.assertRaises(ValidationError) as cm:
            corr_far.full_clean()
        self.assertIn('requested_logout', cm.exception.message_dict)

    def test_03_duplicate_pending_rejected(self):
        """Only one PENDING correction allowed per employee + date."""
        work_date = datetime.date(2026, 6, 10)
        in_time = timezone.make_aware(datetime.datetime(2026, 6, 10, 8, 0), self.tz)
        out_time = timezone.make_aware(datetime.datetime(2026, 6, 10, 17, 0), self.tz)

        submit_correction_request(
            employee=self.staff_emp, date=work_date, correction_type='NO_PUNCH',
            requested_login=in_time, requested_logout=out_time, location='BDO',
            reason="First request"
        )

        corr_dup = AttendanceCorrection(
            employee=self.staff_emp, date=work_date, correction_type='NO_PUNCH',
            requested_login=in_time, requested_logout=out_time, location='BDO',
            reason="Second duplicate request"
        )
        with self.assertRaises(ValidationError) as cm:
            corr_dup.full_clean()
        self.assertIn("already a pending", str(cm.exception).lower())

    def test_04_payroll_lock_rejects_correction(self):
        """Correction cannot be submitted inside a month with APPROVED or PAID payroll."""
        Payroll.objects.create(
            employee=self.staff_emp,
            month=datetime.date(2026, 5, 1),
            basic_salary=Decimal('8000.00'),
            status='APPROVED'
        )

        locked_date = datetime.date(2026, 5, 15)
        in_time = timezone.make_aware(datetime.datetime(2026, 5, 15, 8, 0), self.tz)
        out_time = timezone.make_aware(datetime.datetime(2026, 5, 15, 17, 0), self.tz)

        corr_locked = AttendanceCorrection(
            employee=self.staff_emp, date=locked_date, correction_type='NO_PUNCH',
            requested_login=in_time, requested_logout=out_time, location='BDO',
            reason="Locked month request"
        )
        with self.assertRaises(ValidationError) as cm:
            corr_locked.full_clean()
        self.assertIn("already approved", str(cm.exception))

    def test_05_attachment_validation(self):
        """Attachment must be <= 5MB and allowed extension (pdf, jpg, jpeg, png)."""
        work_date = datetime.date(2026, 6, 12)
        in_time = timezone.make_aware(datetime.datetime(2026, 6, 12, 8, 0), self.tz)
        out_time = timezone.make_aware(datetime.datetime(2026, 6, 12, 17, 0), self.tz)

        # Invalid extension
        bad_file = SimpleUploadedFile("malware.exe", b"binary content", content_type="application/octet-stream")
        corr_bad_ext = AttendanceCorrection(
            employee=self.staff_emp, date=work_date, correction_type='NO_PUNCH',
            requested_login=in_time, requested_logout=out_time, location='BDO',
            reason="Bad extension", attachment=bad_file
        )
        with self.assertRaises(ValidationError) as cm:
            corr_bad_ext.full_clean()
        self.assertIn('attachment', cm.exception.message_dict)

        # Exceeds 5MB
        big_bytes = b"0" * (5 * 1024 * 1024 + 10)
        big_file = SimpleUploadedFile("big.pdf", big_bytes, content_type="application/pdf")
        corr_big = AttendanceCorrection(
            employee=self.staff_emp, date=work_date, correction_type='NO_PUNCH',
            requested_login=in_time, requested_logout=out_time, location='BDO',
            reason="Too large", attachment=big_file
        )
        with self.assertRaises(ValidationError) as cm:
            corr_big.full_clean()
        self.assertIn('attachment', cm.exception.message_dict)

    def test_06_approver_permissions_d12(self):
        """Approval permissions: reporting manager can decide, other manager cannot, no self-approval, HR/Admin override."""
        work_date = datetime.date(2026, 6, 15)
        in_time = timezone.make_aware(datetime.datetime(2026, 6, 15, 8, 0), self.tz)
        out_time = timezone.make_aware(datetime.datetime(2026, 6, 15, 17, 0), self.tz)

        corr = submit_correction_request(
            employee=self.staff_emp, date=work_date, correction_type='NO_PUNCH',
            requested_login=in_time, requested_logout=out_time, location='BDO',
            reason="Approval test"
        )

        # Reporting manager: True
        self.assertTrue(can_decide_correction(self.mgr_user, corr))

        # Other manager: False
        self.assertFalse(can_decide_correction(self.other_mgr_user, corr))

        # Self approval (staff user): False
        self.assertFalse(can_decide_correction(self.staff_user, corr))

        # HR override: True
        self.assertTrue(can_decide_correction(self.hr_user, corr))

        # Admin override: True
        self.assertTrue(can_decide_correction(self.admin_user, corr))

        # Other manager cannot approve -> PermissionDenied
        with self.assertRaises(PermissionDenied):
            approve_correction(self.other_mgr_user, corr, remarks="Unauthorized")

    def test_07_approve_forgot_out(self):
        """Approve FORGOT_OUT: updates existing punch, recalculates OT, records changelog, notifies."""
        work_date = datetime.date(2026, 6, 16)
        in_time = timezone.make_aware(datetime.datetime(2026, 6, 16, 8, 0), self.tz)
        # Stale punch without logout
        att = Attendance.objects.create(
            employee=self.staff_emp,
            login_time=in_time,
            log_out_time=None,
            location='BDO',
            attendance_status='PRESENT',
            status='APPROVED',
            auto_punched_out=True,
            missing_punch_resolved=False
        )

        corr_out = in_time + datetime.timedelta(hours=9)
        corr = submit_correction_request(
            employee=self.staff_emp, date=work_date, correction_type='FORGOT_OUT',
            requested_login=in_time, requested_logout=corr_out, location='BDO',
            reason="Forgot to punch out at 5pm", attendance=att, submitted_by=self.staff_user
        )
        self.assertEqual(corr.original_login, in_time)
        self.assertIsNone(corr.original_logout)

        approve_correction(self.mgr_user, corr, remarks="Confirmed worked until 17:00")

        att.refresh_from_db()
        self.assertEqual(att.log_out_time, corr_out)
        self.assertTrue(att.missing_punch_resolved)
        self.assertFalse(att.auto_punched_out)

        corr.refresh_from_db()
        self.assertEqual(corr.status, 'APPROVED')
        self.assertEqual(corr.decided_by, self.mgr_user)
        self.assertEqual(corr.remarks, "Confirmed worked until 17:00")

        # EmployeeChangeLog written
        log = EmployeeChangeLog.objects.filter(employee=self.staff_emp, field='attendance_punch').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.changed_by, self.mgr_user)

        # Notification sent to staff
        notif = Notification.objects.filter(recipient=self.staff_user).order_by('-created_at').first()
        self.assertIsNotNone(notif)
        self.assertIn("approved", notif.message)

    def test_08_approve_no_punch(self):
        """Approve NO_PUNCH: creates new Attendance, register shows OFFICE."""
        work_date = datetime.date(2026, 6, 17)
        in_time = timezone.make_aware(datetime.datetime(2026, 6, 17, 8, 0), self.tz)
        out_time = timezone.make_aware(datetime.datetime(2026, 6, 17, 16, 0), self.tz)

        corr = submit_correction_request(
            employee=self.staff_emp, date=work_date, correction_type='NO_PUNCH',
            requested_login=in_time, requested_logout=out_time, location='BDO',
            reason="Forgot badge at home"
        )

        approve_correction(self.mgr_user, corr, remarks="Manager approved")

        corr.refresh_from_db()
        self.assertEqual(corr.status, 'APPROVED')
        self.assertIsNotNone(corr.attendance)
        self.assertEqual(corr.attendance.attendance_status, 'PRESENT')
        self.assertTrue(corr.attendance.missing_punch_resolved)

        rows = build_attendance_register(work_date, work_date)
        emp_row = next(r for r in rows if r['employee'].id == self.staff_emp.id)
        self.assertEqual(emp_row['status_code'], 'OFFICE')
        self.assertEqual(emp_row['total_hours'], Decimal('8.00'))

    def test_09_reject_no_punch_creates_absent_override(self):
        """Reject NO_PUNCH requires remarks and creates DailyAttendanceStatus ABSENT override."""
        work_date = datetime.date(2026, 6, 18)
        in_time = timezone.make_aware(datetime.datetime(2026, 6, 18, 8, 0), self.tz)
        out_time = timezone.make_aware(datetime.datetime(2026, 6, 18, 17, 0), self.tz)

        corr = submit_correction_request(
            employee=self.staff_emp, date=work_date, correction_type='NO_PUNCH',
            requested_login=in_time, requested_logout=out_time, location='BDO',
            reason="Did not show up"
        )

        # Empty remarks rejected
        with self.assertRaises(ValidationError):
            reject_correction(self.mgr_user, corr, remarks="   ")

        # Valid reject with remarks
        reject_correction(self.mgr_user, corr, remarks="Employee was not seen at the office today")

        corr.refresh_from_db()
        self.assertEqual(corr.status, 'REJECTED')
        self.assertEqual(corr.remarks, "Employee was not seen at the office today")

        # DailyAttendanceStatus ABSENT created
        override = DailyAttendanceStatus.objects.filter(employee=self.staff_emp, date=work_date).first()
        self.assertIsNotNone(override)
        self.assertEqual(override.status, 'ABSENT')

        # Register shows Absent
        rows = build_attendance_register(work_date, work_date)
        emp_row = next(r for r in rows if r['employee'].id == self.staff_emp.id)
        self.assertEqual(emp_row['status_code'], 'ABSENT')

    def test_10_reject_incomplete_punch_resolves_missing_punch(self):
        """Reject incomplete punch (FORGOT_OUT) sets missing_punch_resolved=True so day leaves MISSING_PUNCH."""
        work_date = datetime.date(2026, 6, 19)
        in_time = timezone.make_aware(datetime.datetime(2026, 6, 19, 8, 0), self.tz)
        att = Attendance.objects.create(
            employee=self.staff_emp, login_time=in_time, log_out_time=None,
            location='BDO', attendance_status='PRESENT', status='APPROVED',
            auto_punched_out=True, missing_punch_resolved=False
        )

        corr_out = in_time + datetime.timedelta(hours=10)
        corr = submit_correction_request(
            employee=self.staff_emp, date=work_date, correction_type='FORGOT_OUT',
            requested_login=in_time, requested_logout=corr_out, location='BDO',
            reason="Left early without badge", attendance=att
        )

        with override_settings(HR_ATTENDANCE_ENGINE_V2=True):
            # Before rejection: V2 register shows MISSING_PUNCH
            rows_before = build_attendance_register(work_date, work_date)
            row_before = next(r for r in rows_before if r['employee'].id == self.staff_emp.id)
            self.assertEqual(row_before['status_code'], 'MISSING_PUNCH')

            reject_correction(self.mgr_user, corr, remarks="Cannot verify overtime claimed")

            att.refresh_from_db()
            self.assertTrue(att.missing_punch_resolved)

            # After rejection: day falls through to work with recorded times, unblocking payroll
            rows_after = build_attendance_register(work_date, work_date)
            row_after = next(r for r in rows_after if r['employee'].id == self.staff_emp.id)
            self.assertNotEqual(row_after['status_code'], 'MISSING_PUNCH')

    def test_11_cancel_correction(self):
        """Employee can cancel their own pending correction; others cannot."""
        work_date = datetime.date(2026, 6, 20)
        in_time = timezone.make_aware(datetime.datetime(2026, 6, 20, 8, 0), self.tz)
        out_time = timezone.make_aware(datetime.datetime(2026, 6, 20, 17, 0), self.tz)

        corr = submit_correction_request(
            employee=self.staff_emp, date=work_date, correction_type='NO_PUNCH',
            requested_login=in_time, requested_logout=out_time, location='BDO',
            reason="Submitted by mistake"
        )

        # Other user cannot cancel
        with self.assertRaises(PermissionDenied):
            cancel_correction(self.other_mgr_user, corr)

        # Submitter cancels
        cancel_correction(self.staff_user, corr)
        corr.refresh_from_db()
        self.assertEqual(corr.status, 'CANCELLED')

        # Cannot cancel non-pending
        with self.assertRaises(ValidationError):
            cancel_correction(self.staff_user, corr)

    def test_12_attendance_status_view_tabs_and_contracts(self):
        """Attendance Status view across roles returns HTTP 200 with required template contracts."""
        client = Client()

        # Staff user on employee attendance list
        client.force_login(self.staff_user)
        resp_staff = client.get(reverse('attendance_list_view'))
        self.assertEqual(resp_staff.status_code, 200)
        self.assertContains(resp_staff, 'Attendance Status')
        self.assertContains(resp_staff, 'My Attendance')
        self.assertContains(resp_staff, 'My Requests')

        # Manager user has Team Requests tab
        client.force_login(self.mgr_user)
        resp_mgr = client.get(reverse('attendance_status'))
        self.assertEqual(resp_mgr.status_code, 200)
        self.assertContains(resp_mgr, 'Team Requests')

        # HR user has Team Requests tab
        client.force_login(self.hr_user)
        resp_hr = client.get(reverse('hr_attendance_status'))
        self.assertEqual(resp_hr.status_code, 200)
        self.assertContains(resp_hr, 'Team Requests')

    def test_13_employee_attendance_monthly_calendar_and_exports(self):
        """Employee Attendance monthly calendar matrix, PDF export, and openpyxl Excel export."""
        client = Client()
        client.force_login(self.admin_user)

        # 1. Monthly Calendar tab on admin register
        cal_url = f"{reverse('attendance_list_adminview')}?tab=calendar&calendar_month=2026-06"
        resp_cal = client.get(cal_url)
        self.assertEqual(resp_cal.status_code, 200)
        self.assertContains(resp_cal, 'Monthly Calendar')
        self.assertContains(resp_cal, 'matrix-table')

        # 2. Per-employee month view HTML
        emp_month_url = reverse('employee_attendance_month', kwargs={'employee_id': self.staff_emp.id, 'month': '2026-06'})
        resp_emp_month = client.get(emp_month_url)
        self.assertEqual(resp_emp_month.status_code, 200)
        self.assertContains(resp_emp_month, 'Download PDF')
        self.assertContains(resp_emp_month, 'Export Excel')

        # 3. Per-employee month view PDF export
        resp_pdf = client.get(f"{emp_month_url}?format=pdf")
        self.assertEqual(resp_pdf.status_code, 200)
        self.assertEqual(resp_pdf['Content-Type'], 'application/pdf')
        self.assertTrue(resp_pdf.content.startswith(b'%PDF'))

        # 4. Per-employee month view Excel export
        resp_excel = client.get(f"{emp_month_url}?format=excel")
        self.assertEqual(resp_excel.status_code, 200)
        self.assertEqual(resp_excel['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        wb = openpyxl.load_workbook(io.BytesIO(resp_excel.content))
        self.assertIn("Attendance 2026-06", wb.sheetnames)

    def test_14_summary_metrics_rest_day_working_and_attendance_pct(self):
        """Summary table accurately computes scheduled days, present, and attendance % with off-day working."""
        # 3 days: Day 1 (Office), Day 2 (Sunday worked), Day 3 (Sunday rest)
        d1 = datetime.date(2026, 6, 1)  # Mon
        d2 = datetime.date(2026, 6, 7)  # Sun (off-day)
        d3 = datetime.date(2026, 6, 14) # Sun (off-day)

        in_time1 = timezone.make_aware(datetime.datetime(2026, 6, 1, 8, 0), self.tz)
        out_time1 = timezone.make_aware(datetime.datetime(2026, 6, 1, 16, 0), self.tz)
        Attendance.objects.create(
            employee=self.staff_emp, login_time=in_time1, log_out_time=out_time1,
            location='BDO', attendance_status='PRESENT', status='APPROVED'
        )

        in_time2 = timezone.make_aware(datetime.datetime(2026, 6, 7, 8, 0), self.tz)
        out_time2 = timezone.make_aware(datetime.datetime(2026, 6, 7, 16, 0), self.tz)
        Attendance.objects.create(
            employee=self.staff_emp, login_time=in_time2, log_out_time=out_time2,
            location='BDO', attendance_status='PRESENT', status='APPROVED'
        )

        rows = build_attendance_register(d1, d3)
        emp_rows = [r for r in rows if r['employee'].id == self.staff_emp.id]
        summary_rows, totals = build_attendance_summary(emp_rows)
        s = summary_rows[0]

        # Scheduled working days excludes Sunday
        self.assertGreater(s['scheduled_working_days'], 0)
        # Worked Sunday counted in off_day_worked, NOT present_days (stays in off day)
        self.assertEqual(s['present_days'], 1)
        self.assertEqual(s['off_day_worked'], 1)
        self.assertLessEqual(s['attendance_pct'], 100.0)

    def test_15_legacy_redirect_and_manage_attendance(self):
        """Old submit attendance request redirects, legacy pending attendance remains approvable."""
        client = Client()
        client.force_login(self.staff_user)

        resp_redir = client.get(reverse('submit_attendance_request'))
        self.assertEqual(resp_redir.status_code, 302)
        self.assertIn("attendance-list/?tab=my_requests&action=new", resp_redir.url)

        # Legacy pending attendance
        att = Attendance.objects.create(
            employee=self.staff_emp,
            login_time=timezone.make_aware(datetime.datetime(2026, 6, 21, 8, 0), self.tz),
            log_out_time=timezone.make_aware(datetime.datetime(2026, 6, 21, 17, 0), self.tz),
            location='BDO',
            attendance_status='PRESENT',
            status='PENDING'
        )
        client.force_login(self.admin_user)
        resp_manage = client.post(reverse('manage_attendance'), {
            'attendance_id': att.id,
            'action': 'APPROVE'
        })
        self.assertEqual(resp_manage.status_code, 200)
        att.refresh_from_db()
        self.assertEqual(att.status, 'APPROVED')

    def test_16_employee_without_reporting_manager_renders_tabs_200(self):
        """Employee with no reporting manager submits request, all tabs render 200 with HR Queue."""
        unmgr_user = User.objects.create_user('unmgr_usr', 'unmgr@amms.ae', 'pass123', first_name='Solo', last_name='Worker')
        unmgr_emp = Employee.objects.create(
            user=unmgr_user,
            employee_code='AMMS-0099',
            salary=Decimal('8000.00'),
            date_of_join=datetime.date(2024, 1, 1),
            branch=None,
            department=None,
            reporting_manager=None,
            is_employee=True
        )
        req_date = datetime.date(2026, 6, 2)
        in_time = timezone.make_aware(datetime.datetime(2026, 6, 2, 8, 0), self.tz)
        out_time = timezone.make_aware(datetime.datetime(2026, 6, 2, 17, 0), self.tz)

        corr = submit_correction_request(
            employee=unmgr_emp,
            date=req_date,
            correction_type='NO_PUNCH',
            requested_login=in_time,
            requested_logout=out_time,
            location='BDO',
            reason='Solo worker missed punch',
            submitted_by=unmgr_user
        )
        self.assertIsNone(corr.approver)

        client = Client()
        client.force_login(unmgr_user)
        url = reverse('attendance_list_view')

        # Tab 1: My Attendance
        r1 = client.get(f"{url}?tab=my_attendance")
        self.assertEqual(r1.status_code, 200)

        # Tab 2: My Requests (must display HR Queue without crashing)
        r2 = client.get(f"{url}?tab=my_requests")
        self.assertEqual(r2.status_code, 200)
        self.assertContains(r2, "HR Queue")

        # Tab 3: Team Requests
        r3 = client.get(f"{url}?tab=team_requests")
        self.assertEqual(r3.status_code, 200)

    def test_17_location_default_bdo_and_makemigrations_clean(self):
        """AttendanceCorrection location defaults to BDO and makemigrations reports no pending changes."""
        loc_field = AttendanceCorrection._meta.get_field('location')
        self.assertEqual(loc_field.default, 'BDO')

        # Test submit default location
        req_date = datetime.date(2026, 6, 3)
        in_time = timezone.make_aware(datetime.datetime(2026, 6, 3, 8, 0), self.tz)
        out_time = timezone.make_aware(datetime.datetime(2026, 6, 3, 17, 0), self.tz)

        corr = submit_correction_request(
            employee=self.staff_emp,
            date=req_date,
            correction_type='NO_PUNCH',
            requested_login=in_time,
            requested_logout=out_time,
            reason='Default location test'
        )
        self.assertEqual(corr.location, 'BDO')

        # Verify no migrations pending
        out = io.StringIO()
        call_command('makemigrations', '--check', stdout=out)
        self.assertIn("No changes detected", out.getvalue())

    def test_18_no_punch_rejected_when_punch_exists(self):
        """NO_PUNCH on a day that already has an Attendance must be rejected in clean and approve."""
        punch_date = datetime.date(2026, 10, 12)
        in_time = timezone.make_aware(datetime.datetime(2026, 10, 12, 8, 0), self.tz)
        out_time = timezone.make_aware(datetime.datetime(2026, 10, 12, 17, 0), self.tz)

        # Create punch on 12/10/2026
        Attendance.objects.create(
            employee=self.staff_emp,
            login_time=in_time,
            log_out_time=out_time,
            location='BDO',
            attendance_status='PRESENT',
            status='APPROVED'
        )

        # 1. Clean rejection
        corr = AttendanceCorrection(
            employee=self.staff_emp,
            date=punch_date,
            correction_type='NO_PUNCH',
            requested_login=in_time,
            requested_logout=out_time,
            location='BDO',
            reason='Tried NO_PUNCH on punched day'
        )
        with self.assertRaises(ValidationError) as ctx:
            corr.clean()
        self.assertIn("A punch already exists for 12/10/2026 — use Forgot punch-out or Wrong time instead.", str(ctx.exception))

        # Still only one attendance
        self.assertEqual(
            Attendance.objects.filter(employee=self.staff_emp, login_time__date=punch_date).count(),
            1
        )

    def test_19_register_filters_department_branch_work_type(self):
        """build_attendance_register filters by department, branch, and work_type."""
        dept2 = Department.objects.create(name="Operations Marine", sort_order=2)
        branch2 = Branch.objects.create(name="Fujairah Port Branch", sort_order=2)

        emp2_user = User.objects.create_user('emp2_flt', 'e2@amms.ae', 'pass123')
        emp2 = Employee.objects.create(
            user=emp2_user,
            employee_code='AMMS-0088',
            salary=Decimal('7000.00'),
            date_of_join=datetime.date(2024, 1, 1),
            branch=branch2,
            department=dept2,
            work_type='VESSEL',
            is_employee=True
        )

        test_date = datetime.date(2026, 6, 3)
        in_t = timezone.make_aware(datetime.datetime(2026, 6, 3, 8, 0), self.tz)
        out_t = timezone.make_aware(datetime.datetime(2026, 6, 3, 17, 0), self.tz)

        # Both have attendance on 2026-06-03
        Attendance.objects.create(
            employee=self.staff_emp, login_time=in_t, log_out_time=out_t,
            location='BDO', attendance_status='PRESENT', status='APPROVED'
        )
        Attendance.objects.create(
            employee=emp2, login_time=in_t, log_out_time=out_t,
            location='FUJ', attendance_status='PRESENT', status='APPROVED'
        )

        # Filter by department
        rows_d1 = build_attendance_register(test_date, test_date, department=self.dept.id)
        self.assertTrue(all(r['employee'].department_id == self.dept.id for r in rows_d1))
        self.assertIn(self.staff_emp.id, [r['employee'].id for r in rows_d1])
        self.assertNotIn(emp2.id, [r['employee'].id for r in rows_d1])

        # Filter by branch
        rows_b2 = build_attendance_register(test_date, test_date, branch=branch2.id)
        self.assertTrue(all(r['employee'].branch_id == branch2.id for r in rows_b2))
        self.assertIn(emp2.id, [r['employee'].id for r in rows_b2])

        # Filter by work_type
        rows_vessel = build_attendance_register(test_date, test_date, work_type='VESSEL')
        self.assertTrue(all(r['employee'].work_type == 'VESSEL' for r in rows_vessel))
        self.assertIn(emp2.id, [r['employee'].id for r in rows_vessel])

        # HTTP Admin view with filters for dept2 (Operations Marine / Fujairah Port / VESSEL)
        client = Client()
        client.force_login(self.admin_user)
        resp2 = client.get(
            f"{reverse('attendance_list_adminview')}?start=2026-06-03&end=2026-06-03&department={dept2.id}&branch={branch2.id}&work_type=VESSEL"
        )
        self.assertEqual(resp2.status_code, 200)
        sum_rows2 = resp2.context['summary_rows']
        self.assertEqual(len(sum_rows2), 1)
        self.assertEqual(sum_rows2[0]['employee'].id, emp2.id)

        # HTTP Admin view with filters for dept1
        resp1 = client.get(
            f"{reverse('attendance_list_adminview')}?start=2026-06-03&end=2026-06-03&department={self.dept.id}&branch={self.branch.id}&work_type=OFFICE"
        )
        self.assertEqual(resp1.status_code, 200)
        sum_rows1 = resp1.context['summary_rows']
        self.assertTrue(all(r['employee'].department_id == self.dept.id for r in sum_rows1))
        self.assertNotIn(emp2.id, [r['employee'].id for r in sum_rows1])

        # Excel export with filters
        resp_excel = client.get(
            f"{reverse('attendance_list_adminview')}?start=2026-06-03&end=2026-06-03&department={self.dept.id}&export=summary_excel"
        )
        self.assertEqual(resp_excel.status_code, 200)
        self.assertEqual(resp_excel['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def test_20_attendance_percentage_never_exceeds_100_and_offday_in_off_day_worked(self):
        """Attendance % = present days on scheduled working days / scheduled working days (<= 100%). Off-day in off_day_worked."""
        # 5 scheduled working days (Mon-Fri) + 2 off-days (Sat-Sun)
        # Employee works all 5 weekdays PLUS Sunday (off-day)
        emp_rows = [
            {'employee': self.staff_emp, 'name': 'Salem', 'status_code': 'OFFICE', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False, 'date': datetime.date(2026, 6, 1)},
            {'employee': self.staff_emp, 'name': 'Salem', 'status_code': 'OFFICE', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False, 'date': datetime.date(2026, 6, 2)},
            {'employee': self.staff_emp, 'name': 'Salem', 'status_code': 'OFFICE', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False, 'date': datetime.date(2026, 6, 3)},
            {'employee': self.staff_emp, 'name': 'Salem', 'status_code': 'OFFICE', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False, 'date': datetime.date(2026, 6, 4)},
            {'employee': self.staff_emp, 'name': 'Salem', 'status_code': 'OFFICE', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False, 'date': datetime.date(2026, 6, 5)},
            # Off day unworked (Sat)
            {'employee': self.staff_emp, 'name': 'Salem', 'status_code': 'REST_DAY', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': True, 'date': datetime.date(2026, 6, 6)},
            # Off day worked (Sun)
            {'employee': self.staff_emp, 'name': 'Salem', 'status_code': 'OFFICE', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': True, 'date': datetime.date(2026, 6, 7)},
        ]
        summary_rows, totals = build_attendance_summary(emp_rows)
        s = summary_rows[0]
        self.assertEqual(s['scheduled_working_days'], 5)
        # Worked on off day stays in off_day_worked, NOT in present_days
        self.assertEqual(s['present_days'], 5)
        self.assertEqual(s['off_day_worked'], 1)
        self.assertEqual(s['attendance_pct'], 100.0)
        self.assertLessEqual(s['attendance_pct'], 100.0)

    def test_21_monthly_calendar_matrix_only_built_when_tab_monthly(self):
        """Monthly calendar matrix is only computed when tab=calendar or tab=monthly."""
        from django.test.client import RequestFactory
        from Admin.attendance_register import _register_context

        factory = RequestFactory()

        # Tab daily -> matrix is empty
        req_daily = factory.get(reverse('attendance_list_adminview'), {'tab': 'daily'})
        req_daily.user = self.admin_user
        ctx_daily = _register_context(req_daily)
        self.assertEqual(ctx_daily['calendar_matrix_rows'], [])

        # Tab calendar -> matrix is populated
        req_cal = factory.get(reverse('attendance_list_adminview'), {'tab': 'calendar', 'calendar_month': '2026-06'})
        req_cal.user = self.admin_user
        ctx_cal = _register_context(req_cal)
        self.assertGreater(len(ctx_cal['calendar_matrix_rows']), 0)

        # Tab monthly -> matrix is populated
        req_month = factory.get(reverse('attendance_list_adminview'), {'tab': 'monthly', 'calendar_month': '2026-06'})
        req_month.user = self.admin_user
        ctx_month = _register_context(req_month)
        self.assertGreater(len(ctx_month['calendar_matrix_rows']), 0)

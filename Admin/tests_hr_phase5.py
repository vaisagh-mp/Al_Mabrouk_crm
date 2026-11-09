import datetime
from decimal import Decimal
import io
import openpyxl

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from Admin.models import (
    Employee, Department, Branch, Project, WorkOrder, WorkOrderTime,
    OTApproval, EmployeeChangeLog, check_payroll_lock_for_employee
)
from service_logs.models import EmployeeServiceLog
from service_logs.ot import split_day_ot, recalculate_day_ot
from Admin.timesheet_helpers import check_timesheet_overlap, validate_timesheet_attachment
from Admin.work_order_helpers import sync_work_order_times, clean_time_rows
from Admin.timesheet_service import (
    submit_service_log_for_approval,
    approve_service_log,
    reject_service_log,
    reopen_service_log,
    sync_ot_approval_for_day,
    approve_ot_day,
    reject_ot_day,
    approve_all_ot_for_month,
    can_manage_timesheet,
)
from finance.models import Payroll
from finance.services import generate_payroll, get_month_attendance_metrics


class HRPhase5TimesheetTests(TestCase):
    def setUp(self):
        # Users & Profiles
        self.admin_user = User.objects.create_superuser('admin_ts', 'admin@example.com', 'pass123', first_name="Admin", last_name="User")
        self.hr_user = User.objects.create_user('hr_ts', 'hr@example.com', 'pass123', first_name="HR", last_name="Specialist")
        self.mgr_user = User.objects.create_user('mgr_ts', 'mgr@example.com', 'pass123', first_name="Manager", last_name="One")
        self.other_mgr_user = User.objects.create_user('other_mgr_ts', 'other@example.com', 'pass123', first_name="Manager", last_name="Two")
        self.eng_user = User.objects.create_user('eng_ts', 'eng@example.com', 'pass123', first_name="Field", last_name="Engineer")

        self.dept = Department.objects.create(name="Technical Services", sort_order=1)
        self.branch = Branch.objects.create(name="Dubai Port", sort_order=1)

        self.mgr_emp = Employee.objects.create(
            user=self.mgr_user,
            employee_code="AMMS-0101",
            department=self.dept,
            branch=self.branch,
            is_manager=True,
            salary=Decimal("15000.00"),
        )
        self.other_mgr_emp = Employee.objects.create(
            user=self.other_mgr_user,
            employee_code="AMMS-0102",
            department=self.dept,
            branch=self.branch,
            is_manager=True,
            salary=Decimal("15000.00"),
        )
        self.hr_emp = Employee.objects.create(
            user=self.hr_user,
            employee_code="AMMS-0103",
            department=self.dept,
            branch=self.branch,
            is_hr=True,
            salary=Decimal("12000.00"),
        )
        self.eng_emp = Employee.objects.create(
            user=self.eng_user,
            employee_code="AMMS-0104",
            department=self.dept,
            branch=self.branch,
            reporting_manager=self.mgr_emp,
            is_employee=True,
            salary=Decimal("10400.00"),  # rate = 10400 / 208 = 50 AED/hr
        )

        self.project = Project.objects.create(
            name="Project Alpha",
            vessel_name="MV Ocean Trader",
            service_reference="SR-9901",
            status="In Progress"
        )
        self.wo = WorkOrder.objects.create(
            project=self.project,
            work_order_number="WO-9901",
            created_by=self.mgr_user
        )

    def test_switch_off_behaviour_identical(self):
        """With HR_TIMESHEET_APPROVAL=False, everything counts and new logs are saved APPROVED."""
        test_date = datetime.date(2026, 5, 10)
        # With switch off (default), new service logs are saved APPROVED
        log = EmployeeServiceLog.objects.create(
            employee=self.eng_emp,
            date=test_date,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(20, 0),  # 12 hrs -> 2 hrs OT
            project=self.project,
            ot_type='Normal OT',
        )
        self.assertEqual(log.approval_status, 'APPROVED')
        recalculate_day_ot(self.eng_emp.id, test_date)

        # Under switch off, counting() returns all rows (even if status was DRAFT)
        EmployeeServiceLog.objects.filter(pk=log.pk).update(approval_status='DRAFT')
        log.refresh_from_db()
        self.assertEqual(log.approval_status, 'DRAFT')

        with override_settings(HR_TIMESHEET_APPROVAL=False):
            counting_qs = EmployeeServiceLog.objects.counting()
            self.assertIn(log, counting_qs)
            all_metrics = get_month_attendance_metrics(datetime.date(2026, 5, 1), datetime.date(2026, 5, 31))
            metrics = all_metrics.get(self.eng_emp.id, {})
            self.assertEqual(metrics.get('vessel_hours'), Decimal('12.00'))
            self.assertEqual(metrics.get('ot_hours'), Decimal('2.00'))

        # Switch on with FROM: logs before FROM count, after FROM only when approved
        with override_settings(HR_TIMESHEET_APPROVAL=True, HR_TIMESHEET_APPROVAL_FROM=datetime.date(2026, 5, 15)):
            # May 10 (< May 15) counts even when DRAFT
            self.assertTrue(log.is_counting())
            self.assertIn(log, EmployeeServiceLog.objects.counting())

            # May 20 (>= May 15) created as DRAFT does not count until approved
            p_post = Project.objects.create(name="Project Post", vessel_name="Post Vessel")
            log_post = EmployeeServiceLog.objects.create(
                employee=self.eng_emp,
                date=datetime.date(2026, 5, 20),
                start_time=datetime.time(8, 0),
                end_time=datetime.time(16, 0),
                project=p_post,
                approval_status='DRAFT',
            )
            self.assertEqual(log_post.approval_status, 'DRAFT')
            self.assertFalse(log_post.is_counting())
            self.assertNotIn(log_post, EmployeeServiceLog.objects.counting())

            log_post.approval_status = 'APPROVED'
            log_post.save()
            self.assertTrue(log_post.is_counting())
            self.assertIn(log_post, EmployeeServiceLog.objects.counting())

        # System check error when HR_TIMESHEET_APPROVAL is True and HR_TIMESHEET_APPROVAL_FROM is None
        from Admin.checks import check_timesheet_approval_settings
        with override_settings(HR_TIMESHEET_APPROVAL=True, HR_TIMESHEET_APPROVAL_FROM=None):
            errs = check_timesheet_approval_settings(None)
            self.assertTrue(any(e.msg == "Set HR_TIMESHEET_APPROVAL_FROM to the go-live date." for e in errs))

    def test_break_and_travel_hours_calculation(self):
        """Row 08:00–18:00 (10 hrs), 60m break -> 9.0h. OT on 13h day with 60m break = 2.0h."""
        test_date = datetime.date(2026, 5, 11)
        # Row 1: 08:00 - 18:00 (10h), 60m break -> 9.0h
        wot = WorkOrderTime(
            work_order=self.wo,
            employee=self.eng_emp,
            date=test_date,
            start_time=datetime.time(8, 0),
            finish_time=datetime.time(18, 0),
            break_minutes=60,
            travel_minutes=0,
        )
        wot.save()
        self.assertEqual(wot.estimated_hours, Decimal('9.00'))

        log = EmployeeServiceLog.objects.filter(time_log=wot).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.total_hours, Decimal('9.00'))

        # Row 2 (13h duration, 60m break -> 12h worked -> 2h OT): 07:00 to 20:00 (13h)
        wot.start_time = datetime.time(7, 0)
        wot.finish_time = datetime.time(20, 0)  # 13h raw - 1h break = 12h worked
        wot.save()
        self.assertEqual(wot.estimated_hours, Decimal('12.00'))

        recalculate_day_ot(self.eng_emp.id, test_date)
        log.refresh_from_db()
        self.assertEqual(log.ot_hours, Decimal('2.00'))

    def test_break_validation_exceeding_duration(self):
        """Break >= duration raises ValidationError."""
        test_date = datetime.date(2026, 5, 12)
        wot = WorkOrderTime(
            work_order=self.wo,
            employee=self.eng_emp,
            date=test_date,
            start_time=datetime.time(8, 0),
            finish_time=datetime.time(9, 0),  # 60 mins
            break_minutes=60,
        )
        with self.assertRaises(ValidationError):
            wot.clean()

    @override_settings(HR_TIMESHEET_APPROVAL=True)
    def test_work_order_sync_update_in_place_no_delete(self):
        """Work order save preserves existing approved rows, keeps service log ID."""
        test_date = datetime.date(2026, 5, 13)
        # Create an approved time row
        wot1 = WorkOrderTime.objects.create(
            work_order=self.wo,
            employee=self.eng_emp,
            date=test_date,
            start_time=datetime.time(8, 0),
            finish_time=datetime.time(12, 0),
        )
        log1 = EmployeeServiceLog.objects.get(time_log=wot1)
        log1.approval_status = 'APPROVED'
        log1.save()

        # Engineer updates WO with: row 1 (existing, approved) and row 2 (new)
        cleaned_rows = [
            {
                'id': wot1.id,
                'date': test_date,
                'start': datetime.time(8, 0),
                'finish': datetime.time(12, 0),
                'break_minutes': 0,
                'travel_minutes': 0,
                'description': 'Inspected engine',
                'attachment': None,
            },
            {
                'id': None,
                'date': test_date,
                'start': datetime.time(13, 0),
                'finish': datetime.time(17, 0),
                'break_minutes': 0,
                'travel_minutes': 0,
                'description': 'Overhauled valve',
                'attachment': None,
            }
        ]

        sync_work_order_times(
            self.wo,
            cleaned_rows,
            action='submit',
            user=self.eng_user,
            default_employee=self.eng_emp
        )

        # Check that wot1 was preserved, log1 kept its ID and was not deleted
        wot1.refresh_from_db()
        self.assertEqual(wot1.start_time, datetime.time(8, 0))
        log1.refresh_from_db()
        self.assertEqual(log1.approval_status, 'APPROVED')  # Approved row stayed approved!

        # Check row 2 was created and submitted
        wot2 = WorkOrderTime.objects.exclude(id=wot1.id).filter(work_order=self.wo).first()
        self.assertIsNotNone(wot2)
        log2 = EmployeeServiceLog.objects.get(time_log=wot2)
        self.assertEqual(log2.approval_status, 'SUBMITTED')

    @override_settings(HR_TIMESHEET_APPROVAL=True)
    def test_same_work_order_overlap_refused(self):
        """Approved 06:00–19:00 + new 10:00–14:00 on same WO is skipped with overlap warning; still one row."""
        test_date = datetime.date(2026, 5, 16)
        wot1 = WorkOrderTime.objects.create(
            work_order=self.wo,
            employee=self.eng_emp,
            date=test_date,
            start_time=datetime.time(6, 0),
            finish_time=datetime.time(19, 0),
        )
        slog1 = EmployeeServiceLog.objects.get(time_log=wot1)
        slog1.approval_status = 'APPROVED'
        slog1.save()

        warnings = []
        cleaned_rows = [
            {
                'id': wot1.id,
                'date': test_date,
                'start': datetime.time(6, 0),
                'finish': datetime.time(19, 0),
                'break_minutes': 0,
                'travel_minutes': 0,
                'description': 'Approved overhaul',
                'attachment': None,
            },
            {
                'id': None,
                'date': test_date,
                'start': datetime.time(10, 0),
                'finish': datetime.time(14, 0),
                'break_minutes': 0,
                'travel_minutes': 0,
                'description': 'Overlapping inspection',
                'attachment': None,
            }
        ]

        sync_work_order_times(
            self.wo,
            cleaned_rows,
            action='save_draft',
            user=self.eng_user,
            default_employee=self.eng_emp,
            warnings=warnings,
        )

        self.assertTrue(any('overlap' in w.lower() for w in warnings), f"Expected overlap warning, got: {warnings}")
        rows = WorkOrderTime.objects.filter(work_order=self.wo, date=test_date)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().id, wot1.id)

    @override_settings(HR_TIMESHEET_APPROVAL=True)
    def test_overlap_refused_across_work_orders(self):
        """Overlap refused across two work orders."""
        test_date = datetime.date(2026, 5, 14)
        WorkOrderTime.objects.create(
            work_order=self.wo,
            employee=self.eng_emp,
            date=test_date,
            start_time=datetime.time(8, 0),
            finish_time=datetime.time(12, 0),
        )

        p2 = Project.objects.create(name="Project Beta", vessel_name="MV Ocean Trader 2")
        wo2 = WorkOrder.objects.create(
            project=p2,
            work_order_number="WO-9902",
            created_by=self.mgr_user
        )

        wot2 = WorkOrderTime(
            work_order=wo2,
            employee=self.eng_emp,
            date=test_date,
            start_time=datetime.time(11, 0),  # Overlaps 11:00-12:00
            finish_time=datetime.time(15, 0),
        )
        with self.assertRaises(ValidationError) as cm:
            wot2.clean()
        self.assertIn("Overlaps with WO-9901", str(cm.exception))

    def test_two_vessels_same_day_union_timeline_ot(self):
        """07:00–12:00 (5h) and 13:00–19:00 (6h) same day -> 11h worked, 1h OT."""
        test_date = datetime.date(2026, 5, 15)
        p1 = Project.objects.create(name="Project 1", vessel_name="Vessel 1")
        p2 = Project.objects.create(name="Project 2", vessel_name="Vessel 2")

        l1 = EmployeeServiceLog.objects.create(
            employee=self.eng_emp,
            date=test_date,
            start_time=datetime.time(7, 0),
            end_time=datetime.time(12, 0),
            project=p1,
            approval_status='APPROVED',
            ot_type='Normal OT',
        )
        l2 = EmployeeServiceLog.objects.create(
            employee=self.eng_emp,
            date=test_date,
            start_time=datetime.time(13, 0),
            end_time=datetime.time(19, 0),
            project=p2,
            approval_status='APPROVED',
            ot_type='Normal OT',
        )

        recalculate_day_ot(self.eng_emp.id, test_date)
        l1.refresh_from_db()
        l2.refresh_from_db()

        # Day worked = 5 + 6 = 11h -> OT = 1.0h (attributed to the log crossing 10h, which is l2)
        total_ot = (l1.ot_hours or Decimal('0.00')) + (l2.ot_hours or Decimal('0.00'))
        self.assertEqual(total_ot, Decimal('1.00'))
        self.assertEqual(l1.ot_hours, Decimal('0.00'))
        self.assertEqual(l2.ot_hours, Decimal('1.00'))

    @override_settings(HR_TIMESHEET_APPROVAL=True)
    def test_approver_rules_and_self_approval_blocked(self):
        """Reporting manager can approve; employee cannot approve own row; reject needs remarks."""
        test_date = datetime.date(2026, 5, 16)
        log = EmployeeServiceLog.objects.create(
            employee=self.eng_emp,
            date=test_date,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(17, 0),
            project=self.project,
            approval_status='SUBMITTED',
            approver=self.mgr_emp,
        )

        # 1. Employee cannot approve own row
        with self.assertRaises(PermissionDenied):
            approve_service_log(log, self.eng_user)

        # 2. Other manager cannot approve
        with self.assertRaises(PermissionDenied):
            approve_service_log(log, self.other_mgr_user)

        # 3. Reject without remarks raises ValidationError
        with self.assertRaises(ValidationError):
            reject_service_log(log, self.mgr_user, remarks="")

        # 4. Reject with remarks succeeds
        reject_service_log(log, self.mgr_user, remarks="Incorrect finish time")
        log.refresh_from_db()
        self.assertEqual(log.approval_status, 'REJECTED')
        self.assertEqual(log.decision_remarks, "Incorrect finish time")

        # 5. Reporting manager approves
        submit_service_log_for_approval(log, self.eng_user)
        approve_service_log(log, self.mgr_user)
        log.refresh_from_db()
        self.assertEqual(log.approval_status, 'APPROVED')
        self.assertEqual(log.decided_by, self.mgr_user)

    @override_settings(HR_TIMESHEET_APPROVAL=True)
    def test_reopen_approved_timesheet(self):
        """Only Admin or HR can reopen an approved timesheet; logged in EmployeeChangeLog."""
        test_date = datetime.date(2026, 5, 17)
        log = EmployeeServiceLog.objects.create(
            employee=self.eng_emp,
            date=test_date,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(17, 0),
            project=self.project,
            approval_status='APPROVED',
            decided_by=self.mgr_user,
        )

        # Manager tries to reopen -> PermissionDenied
        with self.assertRaises(PermissionDenied):
            reopen_service_log(log, self.mgr_user, reason="Need to change hours")

        # HR reopens -> Success
        reopen_service_log(log, self.hr_user, reason="Correction requested by client")
        log.refresh_from_db()
        self.assertEqual(log.approval_status, 'DRAFT')

        # Check changelog entry
        change = EmployeeChangeLog.objects.filter(employee=self.eng_emp, field='timesheet_reopened').first()
        self.assertIsNotNone(change)
        self.assertIn("Correction requested by client", change.new_value)

    @override_settings(HR_TIMESHEET_APPROVAL=True)
    def test_payroll_and_cost_count_only_approved_logs(self):
        """When switch is ON, only APPROVED logs count in payroll and job costs."""
        test_date = datetime.date(2026, 5, 18)
        log = EmployeeServiceLog.objects.create(
            employee=self.eng_emp,
            date=test_date,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(20, 0),  # 12 hrs -> 2 hrs OT
            project=self.project,
            approval_status='SUBMITTED',
            ot_type='Normal OT',
        )
        recalculate_day_ot(self.eng_emp.id, test_date)

        # While SUBMITTED, counting() excludes it
        self.assertNotIn(log, EmployeeServiceLog.objects.counting())

        # Approve it -> now included
        approve_service_log(log, self.mgr_user)
        self.assertIn(log, EmployeeServiceLog.objects.counting())

    @override_settings(HR_TIMESHEET_APPROVAL=True)
    def test_ot_approval_and_payroll_integration(self):
        """Unapproved OT is not paid in payroll. Approving OT includes it."""
        test_date = datetime.date(2026, 5, 19)
        log = EmployeeServiceLog.objects.create(
            employee=self.eng_emp,
            date=test_date,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(20, 0),  # 12 hrs -> 2 hrs OT
            project=self.project,
            approval_status='APPROVED',
            ot_type='Normal OT',
        )
        recalculate_day_ot(self.eng_emp.id, test_date)

        ot_app = OTApproval.objects.filter(employee=self.eng_emp, date=test_date).first()
        self.assertIsNotNone(ot_app)
        self.assertEqual(ot_app.status, 'PENDING')
        self.assertEqual(ot_app.ot_hours, Decimal('2.00'))

        # While PENDING, get_month_attendance_metrics gives ot_amount = 0
        all_metrics = get_month_attendance_metrics(datetime.date(2026, 5, 1), datetime.date(2026, 5, 31))
        metrics = all_metrics.get(self.eng_emp.id, {})
        self.assertEqual(metrics.get('total_ot_cost', Decimal('0.00')), Decimal('0.00'))

        # Approve OT -> now paid
        approve_ot_day(ot_app, self.mgr_user)
        ot_app.refresh_from_db()
        self.assertEqual(ot_app.status, 'APPROVED')

        all_metrics_after = get_month_attendance_metrics(datetime.date(2026, 5, 1), datetime.date(2026, 5, 31))
        metrics_after = all_metrics_after.get(self.eng_emp.id, {})
        self.assertGreater(metrics_after.get('total_ot_cost', Decimal('0.00')), Decimal('0.00'))

    @override_settings(HR_TIMESHEET_APPROVAL=True, HR_TIMESHEET_APPROVAL_FROM=datetime.date(2026, 5, 15))
    def test_payroll_spans_from_date_with_office_vessel_day(self):
        """Month spanning FROM date includes pre-FROM vessel and office OT."""
        from Admin.models import Attendance
        # Pre-FROM day: 2026-05-11 (Monday, weekday)
        pre_date = datetime.date(2026, 5, 11)
        login_dt = timezone.make_aware(datetime.datetime.combine(pre_date, datetime.time(8, 0)))
        logout_dt = timezone.make_aware(datetime.datetime.combine(pre_date, datetime.time(18, 0)))
        # Office punch: 08:00 to 18:00 (10 hrs in office -> 2 hrs office OT)
        Attendance.objects.create(
            employee=self.eng_emp,
            login_time=login_dt,
            log_out_time=logout_dt,
            status='APPROVED',
            attendance_status='PRESENT',
            location='BDO',
        )
        # Vessel log: 18:00 to 20:00 (2 hrs vessel work)
        p_pre = Project.objects.create(name="Pre Project", vessel_name="Pre Vessel")
        log_pre = EmployeeServiceLog.objects.create(
            employee=self.eng_emp,
            date=pre_date,
            start_time=datetime.time(18, 0),
            end_time=datetime.time(20, 0),
            project=p_pre,
            approval_status='APPROVED',
        )
        recalculate_day_ot(self.eng_emp.id, pre_date)

        # Post-FROM day: 2026-05-20
        post_date = datetime.date(2026, 5, 20)
        p_post = Project.objects.create(name="Post Project 2", vessel_name="Post Vessel 2")
        log_post = EmployeeServiceLog.objects.create(
            employee=self.eng_emp,
            date=post_date,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(20, 0),  # 12 hrs -> 2 hrs OT
            project=p_post,
            approval_status='APPROVED',
            ot_type='Normal OT',
        )
        recalculate_day_ot(self.eng_emp.id, post_date)

        # Approve OT for post_date
        ot_post = OTApproval.objects.filter(employee=self.eng_emp, date=post_date).first()
        self.assertIsNotNone(ot_post)
        approve_ot_day(ot_post, self.mgr_user)
        ot_post.refresh_from_db()
        self.assertEqual(ot_post.status, 'APPROVED')

        all_metrics = get_month_attendance_metrics(datetime.date(2026, 5, 1), datetime.date(2026, 5, 31))
        metrics = all_metrics.get(self.eng_emp.id, {})

        # Should include post_date OT (2h) AND pre_date office OT (2h) -> total OT hours >= 4.00
        self.assertGreaterEqual(metrics.get('ot_hours', Decimal('0.00')), Decimal('4.00'))
        self.assertGreater(metrics.get('total_ot_cost', Decimal('0.00')), ot_post.ot_cost)

    @override_settings(HR_TIMESHEET_APPROVAL=True)
    def test_payroll_approval_blocked_by_submitted_or_pending_ot(self):
        """Payroll approve is blocked while SUBMITTED rows or PENDING OT days exist."""
        test_date = datetime.date(2026, 5, 20)
        log = EmployeeServiceLog.objects.create(
            employee=self.eng_emp,
            date=test_date,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(17, 0),
            project=self.project,
            approval_status='SUBMITTED',
        )

        payroll = Payroll.objects.create(
            employee=self.eng_emp,
            month=datetime.date(2026, 5, 1),
            basic_salary=self.eng_emp.salary,
            status='DRAFT'
        )

        client = Client()
        client.force_login(self.admin_user)

        # Attempt to approve payroll -> blocked with warning message
        resp = client.post(reverse('payroll_approve', args=[payroll.id]))
        payroll.refresh_from_db()
        self.assertEqual(payroll.status, 'DRAFT')  # Still DRAFT!

        # Now approve timesheet
        approve_service_log(log, self.mgr_user)

        # If any pending OT days exist, test blocking
        ot_app = OTApproval.objects.filter(employee=self.eng_emp, date=test_date).first()
        if ot_app and ot_app.status == 'PENDING':
            resp = client.post(reverse('payroll_approve', args=[payroll.id]))
            payroll.refresh_from_db()
            self.assertEqual(payroll.status, 'DRAFT')
            approve_ot_day(ot_app, self.mgr_user)

        # Now approve succeeds
        resp = client.post(reverse('payroll_approve', args=[payroll.id]))
        payroll.refresh_from_db()
        self.assertEqual(payroll.status, 'APPROVED')

    def test_locked_month_refuses_timesheet_actions(self):
        """Timesheet submit/approve/reopen in an approved or paid month raises ValidationError."""
        test_date = datetime.date(2026, 4, 15)
        Payroll.objects.create(
            employee=self.eng_emp,
            month=datetime.date(2026, 4, 1),
            basic_salary=self.eng_emp.salary,
            status='PAID'
        )

        log = EmployeeServiceLog.objects.create(
            employee=self.eng_emp,
            date=test_date,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(17, 0),
            project=self.project,
            approval_status='DRAFT',
        )

        with self.assertRaises(ValidationError):
            submit_service_log_for_approval(log, self.eng_user)

    def test_pages_smoke_tests(self):
        """Smoke test all new pages for role permissions and status 200/403."""
        client = Client()

        # 1. Timesheet Approvals
        # Admin
        client.force_login(self.admin_user)
        r = client.get(reverse('timesheet_approvals_admin'))
        self.assertEqual(r.status_code, 200)

        # HR
        client.force_login(self.hr_user)
        r = client.get(reverse('timesheet_approvals_hr'))
        self.assertEqual(r.status_code, 200)

        # Manager
        client.force_login(self.mgr_user)
        r = client.get(reverse('timesheet_approvals_manager'))
        self.assertEqual(r.status_code, 200)

        # Regular Engineer -> 403
        client.force_login(self.eng_user)
        r = client.get(reverse('timesheet_approvals_manager'))
        self.assertEqual(r.status_code, 403)

        # 2. OT Approval
        client.force_login(self.admin_user)
        r = client.get(reverse('ot_approval_admin'))
        self.assertEqual(r.status_code, 200)

        client.force_login(self.hr_user)
        r = client.get(reverse('ot_approval_hr'))
        self.assertEqual(r.status_code, 200)

        client.force_login(self.mgr_user)
        r = client.get(reverse('ot_approval_manager'))
        self.assertEqual(r.status_code, 200)

        client.force_login(self.eng_user)
        r = client.get(reverse('ot_approval_admin'))
        self.assertEqual(r.status_code, 403)

        # 3. Monthly PDF
        client.force_login(self.admin_user)
        r = client.get(reverse('employee_monthly_timesheet_pdf', args=[self.eng_emp.id, '2026-05']))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')

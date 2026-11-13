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
    Employee, Department, Branch, Leave, LeaveType, LeavePolicy,
    LeaveLedger, LeaveYearReview, Notification, EmployeeChangeLog, Holiday,
    DutySchedule, RestDayRule, EmployeeSchedule
)
from Admin.leave_service import (
    is_leave_v2_active,
    add_months,
    calculate_leave_working_days,
    get_employee_leave_balance,
    get_employee_pending_leave_days,
    get_employee_available_leave,
    get_employee_leave_summary,
    can_decide_leave,
    submit_leave_request,
    approve_leave_request,
    reject_leave_request,
    request_leave_clarification,
    respond_leave_clarification,
    cancel_leave_request,
    accrue_leave_for_month,
    close_leave_year_for_all,
    generate_opening_balances_template,
    process_opening_balances_excel,
    export_leave_list_excel,
    export_leave_statement_excel,
)
from Admin.attendance_register import (
    build_attendance_register, build_attendance_summary
)
from finance.models import Payroll
from finance.services import get_month_attendance_metrics, generate_payroll


class HRPhase4LeaveEngineTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Dubai Marine Base", sort_order=1)
        self.dept = Department.objects.create(name="Technical Services", sort_order=1)

        # Duty schedule: Sunday to Thursday or Monday to Friday
        self.schedule = DutySchedule.objects.create(
            name="Standard Office",
            start_time=datetime.time(8, 0),
            end_time=datetime.time(17, 0),
            break_minutes=60,
            sort_order=1
        )

        # Superuser / Admin
        self.admin_user = User.objects.create_superuser('admin_usr', 'admin@amms.ae', 'pass123')

        # HR user & employee
        self.hr_user = User.objects.create_user('hr_usr', 'hr@amms.ae', 'pass123', first_name='Fatima', last_name='HR')
        self.hr_emp = Employee.objects.create(
            user=self.hr_user,
            employee_code='AMMS-0001',
            salary=Decimal('18000.00'),
            date_of_join=datetime.date(2023, 1, 1),
            branch=self.branch,
            department=self.dept,
            is_hr=True
        )

        # Manager user & employee
        self.mgr_user = User.objects.create_user('mgr_usr', 'mgr@amms.ae', 'pass123', first_name='Tariq', last_name='Manager')
        self.mgr_emp = Employee.objects.create(
            user=self.mgr_user,
            employee_code='AMMS-0002',
            salary=Decimal('15000.00'),
            date_of_join=datetime.date(2023, 1, 1),
            branch=self.branch,
            department=self.dept,
            is_manager=True
        )

        # Other Manager
        self.other_mgr_user = User.objects.create_user('oth_mgr', 'oth@amms.ae', 'pass123')
        self.other_mgr_emp = Employee.objects.create(
            user=self.other_mgr_user,
            employee_code='AMMS-0003',
            salary=Decimal('14000.00'),
            date_of_join=datetime.date(2023, 1, 1),
            branch=self.branch,
            department=self.dept,
            is_manager=True
        )

        # Senior Staff employee (service > 1 year) reporting to mgr_emp
        self.staff_user = User.objects.create_user('staff_usr', 'staff@amms.ae', 'pass123', first_name='Salem', last_name='Staff')
        self.staff_emp = Employee.objects.create(
            user=self.staff_user,
            employee_code='AMMS-0004',
            salary=Decimal('10400.00'),
            date_of_join=datetime.date(2023, 1, 1),
            branch=self.branch,
            department=self.dept,
            reporting_manager=self.mgr_emp,
            is_employee=True
        )

        # Probation Staff employee (joined 2 months ago)
        self.probation_user = User.objects.create_user('prob_usr', 'prob@amms.ae', 'pass123', first_name='New', last_name='Joiner')
        self.probation_emp = Employee.objects.create(
            user=self.probation_user,
            employee_code='AMMS-0005',
            salary=Decimal('8000.00'),
            date_of_join=datetime.date(2026, 1, 15),
            branch=self.branch,
            department=self.dept,
            reporting_manager=self.mgr_emp,
            is_employee=True
        )

        # Accountant user & employee
        self.acct_user = User.objects.create_user('acct_usr', 'acct@amms.ae', 'pass123', first_name='Adil', last_name='Accountant')
        self.acct_emp = Employee.objects.create(
            user=self.acct_user,
            employee_code='AMMS-0006',
            salary=Decimal('12000.00'),
            date_of_join=datetime.date(2023, 1, 1),
            branch=self.branch,
            department=self.dept,
            is_accountant=True
        )

        # Administration user & employee
        self.adm_user = User.objects.create_user('adm_usr', 'adm@amms.ae', 'pass123', first_name='Amna', last_name='AdminStaff')
        self.adm_emp = Employee.objects.create(
            user=self.adm_user,
            employee_code='AMMS-0007',
            salary=Decimal('11000.00'),
            date_of_join=datetime.date(2023, 1, 1),
            branch=self.branch,
            department=self.dept,
            is_administration=True
        )

        # Schedule assignments & rest day rules (Sunday = rest day 6)
        RestDayRule.objects.create(
            employee=self.staff_emp,
            rest_weekday=6, # Sunday
            effective_from=datetime.date(2023, 1, 1)
        )
        EmployeeSchedule.objects.create(
            employee=self.staff_emp,
            duty_schedule=self.schedule,
            effective_from=datetime.date(2023, 1, 1)
        )

        # Ensure seed leave types exist
        self.annual_type, _ = LeaveType.objects.get_or_create(
            code='ANNUAL',
            defaults={
                'name': 'Annual Leave',
                'uses_balance': True,
                'allows_half_day': True,
                'is_active': True,
                'sort_order': 10
            }
        )
        self.annual_type.uses_balance = True
        self.annual_type.save()
        LeavePolicy.objects.get_or_create(
            leave_type=self.annual_type,
            defaults={
                'accrual_rule': 'UAE_ANNUAL',
                'yearly_entitlement': Decimal('30.00'),
                'carry_forward_max': Decimal('5.00')
            }
        )

        self.sick_type, _ = LeaveType.objects.get_or_create(
            code='SICK',
            defaults={
                'name': 'Sick Leave',
                'uses_balance': True,
                'allows_half_day': True,
                'certificate_required_after_days': 1,
                'is_active': True,
                'sort_order': 20
            }
        )
        self.sick_type.uses_balance = True
        self.sick_type.save()
        LeavePolicy.objects.get_or_create(
            leave_type=self.sick_type,
            defaults={
                'accrual_rule': '',
                'yearly_entitlement': Decimal('15.00'),
                'carry_forward_max': Decimal('0.00')
            }
        )

        self.unpaid_type, _ = LeaveType.objects.get_or_create(
            code='UNPAID',
            defaults={
                'name': 'Unpaid Leave',
                'uses_balance': False,
                'allows_half_day': True,
                'is_active': True,
                'sort_order': 30
            }
        )

        # Seed initial annual leave balance for staff_emp (10 days)
        LeaveLedger.objects.create(
            employee=self.staff_emp,
            leave_type=self.annual_type,
            date=datetime.date(2026, 1, 1),
            entry_type='OPENING',
            days=Decimal('10.00'),
            note='Initial opening balance',
            created_by=self.admin_user
        )

        # Seed initial annual leave balance for mgr_emp (10 days)
        LeaveLedger.objects.create(
            employee=self.mgr_emp,
            leave_type=self.annual_type,
            date=datetime.date(2026, 1, 1),
            entry_type='OPENING',
            days=Decimal('10.00'),
            note='Initial balance for manager',
            created_by=self.admin_user
        )

        self.client = Client()

    def test_01_leave_v2_off_legacy_behaviour_untouched(self):
        """With HR_LEAVE_V2=False (default), is_leave_v2_active is False and legacy views are active."""
        self.assertFalse(is_leave_v2_active())

        self.client.login(username='staff_usr', password='pass123')
        resp = self.client.get(reverse('my_leave'))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'employee/leavestatus.html')

        resp_apply = self.client.get(reverse('apply_leave'))
        self.assertEqual(resp_apply.status_code, 200)
        self.assertTemplateUsed(resp_apply, 'employee/leave.html')

    @override_settings(HR_LEAVE_V2=True, HR_ATTENDANCE_ENGINE_V2=True)
    def test_02_working_days_calculation_excludes_rest_days_and_holidays(self):
        """
        Scheduled working days calculation (D8):
        Working days exclude rest days (Sunday) and branch public holidays.
        """
        # Create a branch public holiday on Tuesday 2026-03-10
        Holiday.objects.create(
            date=datetime.date(2026, 3, 10),
            description="Branch Holiday",
            branch=self.branch
        )

        # Range: 2026-03-08 (Sunday) to 2026-03-12 (Thursday) = 5 calendar days
        # Sunday 2026-03-08: Rest day (excluded)
        # Monday 2026-03-09: Working day
        # Tuesday 2026-03-10: Public Holiday (excluded)
        # Wednesday 2026-03-11: Working day
        # Thursday 2026-03-12: Working day
        # Total working days = 3.0, excluded = 2 days
        from_d = datetime.date(2026, 3, 8)
        to_d = datetime.date(2026, 3, 12)

        working_days, excluded = calculate_leave_working_days(
            self.staff_emp, from_d, to_d, is_half_day=False, leave_type=self.annual_type
        )
        self.assertEqual(working_days, Decimal('3.00'))
        self.assertEqual(len(excluded), 2)
        self.assertTrue(any('08/03/2026' in s for s in excluded))
        self.assertTrue(any('10/03/2026' in s for s in excluded))

        # Range entirely on a rest day yields 0 working days
        working_days_rest, excluded_rest = calculate_leave_working_days(
            self.staff_emp, datetime.date(2026, 3, 8), datetime.date(2026, 3, 8),
            is_half_day=False, leave_type=self.annual_type
        )
        self.assertEqual(working_days_rest, Decimal('0.00'))
        self.assertEqual(len(excluded_rest), 1)

    @override_settings(HR_LEAVE_V2=True, HR_ATTENDANCE_ENGINE_V2=True)
    def test_03_half_day_leave_validation(self):
        """
        Half-day rules:
        - Single day only (from_date == to_date).
        - Disallowed on leave types with allows_half_day=False (e.g. SICK).
        - Half day on a working day = 0.50.
        - Half day on a rest day = 0.00.
        """
        # Half day on Monday 2026-03-09 (working day) -> 0.50
        work_days, _ = calculate_leave_working_days(
            self.staff_emp, datetime.date(2026, 3, 9), datetime.date(2026, 3, 9),
            is_half_day=True, leave_type=self.annual_type
        )
        self.assertEqual(work_days, Decimal('0.50'))

        # Half day on Sunday 2026-03-08 (rest day) -> 0.00
        rest_half, _ = calculate_leave_working_days(
            self.staff_emp, datetime.date(2026, 3, 8), datetime.date(2026, 3, 8),
            is_half_day=True, leave_type=self.annual_type
        )
        self.assertEqual(rest_half, Decimal('0.00'))

        # Half day spanning multi-day raises ValidationError
        with self.assertRaises(ValidationError) as ctx:
            calculate_leave_working_days(
                self.staff_emp, datetime.date(2026, 3, 9), datetime.date(2026, 3, 10),
                is_half_day=True, leave_type=self.annual_type
            )
        self.assertIn("Half-day leave must be on a single date", str(ctx.exception))

        # Half day on leave type that does not allow half day raises ValidationError on submit
        no_hd_type = LeaveType.objects.create(
            code='NO_HD',
            name='No Half Day Leave',
            allows_half_day=False,
            uses_balance=False,
            is_active=True
        )
        with self.assertRaises(ValidationError) as ctx:
            submit_leave_request(
                employee=self.staff_emp,
                leave_type_ref=no_hd_type,
                from_date=datetime.date(2026, 3, 9),
                to_date=datetime.date(2026, 3, 9),
                is_half_day=True,
                half_day_part='AM',
                reason="Half day disallowed",
                submitted_by=self.staff_user
            )
        self.assertIn("Half-day leave is not allowed", str(ctx.exception))

    @override_settings(HR_LEAVE_V2=True, HR_ATTENDANCE_ENGINE_V2=True)
    def test_04_probation_rule_annual_leave(self):
        """
        Probation rule (D7):
        Employees during the first 6 months of service cannot apply for Annual Leave.
        They must apply as Unpaid Leave.
        """
        # probation_emp joined 2026-01-15 -> 6 months ends 2026-07-15
        from_d = datetime.date(2026, 3, 9)
        to_d = datetime.date(2026, 3, 11)

        # Applying for Annual Leave during probation is rejected
        with self.assertRaises(ValidationError) as ctx:
            submit_leave_request(
                employee=self.probation_emp,
                leave_type_ref=self.annual_type,
                from_date=from_d,
                to_date=to_d,
                reason="Vacation",
                submitted_by=self.probation_user
            )
        expected_msg = "Annual leave is available after 6 months of service — apply as Unpaid Leave (needs management approval)."
        self.assertIn(expected_msg, str(ctx.exception))

        # Applying for Unpaid Leave during probation is permitted
        unpaid_req = submit_leave_request(
            employee=self.probation_emp,
            leave_type_ref=self.unpaid_type,
            from_date=from_d,
            to_date=to_d,
            reason="Personal urgent matters",
            submitted_by=self.probation_user
        )
        self.assertEqual(unpaid_req.approval_status, 'PENDING')
        self.assertEqual(unpaid_req.leave_type_ref, self.unpaid_type)

    @override_settings(HR_LEAVE_V2=True, HR_ATTENDANCE_ENGINE_V2=True)
    def test_05_insufficient_balance_and_hr_override(self):
        """
        Insufficient balance is blocked for employees, but HR / Admin can submit with override.
        """
        # staff_emp has 10 days balance. Applying for 15 working days is blocked.
        # Monday 2026-04-06 to Friday 2026-04-24
        from_d = datetime.date(2026, 4, 6)
        to_d = datetime.date(2026, 4, 24)

        with self.assertRaises(ValidationError) as ctx:
            submit_leave_request(
                employee=self.staff_emp,
                leave_type_ref=self.annual_type,
                from_date=from_d,
                to_date=to_d,
                reason="Long holiday",
                submitted_by=self.staff_user
            )
        self.assertIn("Insufficient leave balance", str(ctx.exception))

        # HR submits with override and reason
        override_req = submit_leave_request(
            employee=self.staff_emp,
            leave_type_ref=self.annual_type,
            from_date=from_d,
            to_date=to_d,
            reason="Long holiday approved by MD",
            hr_override=True,
            hr_override_reason="Managing Director special pre-approval",
            submitted_by=self.hr_user
        )
        self.assertIsNotNone(override_req.pk)

        # Verify audit log in EmployeeChangeLog
        log_entry = EmployeeChangeLog.objects.filter(
            employee=self.staff_emp, field='leave_balance_override'
        ).first()
        self.assertIsNotNone(log_entry)
        self.assertIn("requested", log_entry.new_value)

    @override_settings(HR_LEAVE_V2=True, HR_ATTENDANCE_ENGINE_V2=True)
    def test_06_overlap_detection(self):
        """Overlapping leaves with PENDING, CLARIFICATION, or APPROVED are rejected."""
        # Create an initial approved leave
        submit_leave_request(
            employee=self.staff_emp,
            leave_type_ref=self.annual_type,
            from_date=datetime.date(2026, 5, 4),
            to_date=datetime.date(2026, 5, 8),
            reason="Trip",
            submitted_by=self.staff_user
        )

        # Attempt to submit overlapping leave: 2026-05-06 to 2026-05-12
        with self.assertRaises(ValidationError) as ctx:
            submit_leave_request(
                employee=self.staff_emp,
                leave_type_ref=self.annual_type,
                from_date=datetime.date(2026, 5, 6),
                to_date=datetime.date(2026, 5, 12),
                reason="Overlapping Trip",
                submitted_by=self.staff_user
            )
        self.assertIn("Leave overlaps with existing", str(ctx.exception))

    @override_settings(HR_LEAVE_V2=True, HR_ATTENDANCE_ENGINE_V2=True)
    def test_07_approver_permissions_and_self_approval_blocked(self):
        """
        Approver rules (D12):
        - Direct reporting manager can decide.
        - Self-approval is strictly blocked.
        - Other manager cannot decide unless HR/Admin.
        - Superuser and HR can override and decide.
        """
        req = submit_leave_request(
            employee=self.staff_emp,
            leave_type_ref=self.annual_type,
            from_date=datetime.date(2026, 6, 1),
            to_date=datetime.date(2026, 6, 3),
            reason="Personal",
            submitted_by=self.staff_user
        )

        # Direct manager can decide
        can_mgr = can_decide_leave(self.mgr_user, req)
        self.assertTrue(can_mgr)

        # Other manager cannot decide
        can_oth = can_decide_leave(self.other_mgr_user, req)
        self.assertFalse(can_oth)

        # Self-approval blocked: manager submits own leave, cannot approve it
        mgr_req = submit_leave_request(
            employee=self.mgr_emp,
            leave_type_ref=self.annual_type,
            from_date=datetime.date(2026, 6, 1),
            to_date=datetime.date(2026, 6, 2),
            reason="Manager vacation",
            submitted_by=self.mgr_user
        )
        can_self = can_decide_leave(self.mgr_user, mgr_req)
        self.assertFalse(can_self)

        # Approving own leave raises PermissionDenied
        with self.assertRaises(PermissionDenied):
            approve_leave_request(mgr_req, self.mgr_user, remarks="Approving myself")

        # HR and Admin can decide manager's request
        can_hr = can_decide_leave(self.hr_user, mgr_req)
        self.assertTrue(can_hr)
        can_adm = can_decide_leave(self.admin_user, mgr_req)
        self.assertTrue(can_adm)

    @override_settings(HR_LEAVE_V2=True, HR_ATTENDANCE_ENGINE_V2=True)
    def test_08_clarification_roundtrip(self):
        """Manager requests clarification -> CLARIFICATION. Employee responds -> PENDING."""
        req = submit_leave_request(
            employee=self.staff_emp,
            leave_type_ref=self.annual_type,
            from_date=datetime.date(2026, 7, 6),
            to_date=datetime.date(2026, 7, 8),
            reason="Event",
            submitted_by=self.staff_user
        )

        # Manager requests clarification
        request_leave_clarification(req, self.mgr_user, "Please provide handover notes.")
        req.refresh_from_db()
        self.assertEqual(req.approval_status, 'CLARIFICATION')
        self.assertEqual(req.clarification_request, "Please provide handover notes.")

        # Notification sent to employee
        notif = Notification.objects.filter(recipient=self.staff_user).order_by('-created_at').first()
        self.assertIsNotNone(notif)
        self.assertIn("Clarification requested", notif.message)

        # Employee responds to clarification
        respond_leave_clarification(req, self.staff_user, "Handover given to Tariq.")
        req.refresh_from_db()
        self.assertEqual(req.approval_status, 'PENDING')
        self.assertEqual(req.clarification_response, "Handover given to Tariq.")

    @override_settings(HR_LEAVE_V2=True, HR_ATTENDANCE_ENGINE_V2=True)
    def test_09_rejection_requires_remarks(self):
        """Rejection requires remarks. On rejection, approval_status becomes REJECTED."""
        req = submit_leave_request(
            employee=self.staff_emp,
            leave_type_ref=self.annual_type,
            from_date=datetime.date(2026, 8, 3),
            to_date=datetime.date(2026, 8, 5),
            reason="Short trip",
            submitted_by=self.staff_user
        )

        # Empty rejection remarks raise ValidationError
        with self.assertRaises(ValidationError) as ctx:
            reject_leave_request(req, self.mgr_user, remarks="")
        self.assertIn("Remarks are required when rejecting", str(ctx.exception))

        # Successful rejection
        reject_leave_request(req, self.mgr_user, remarks="High project workload this week.")
        req.refresh_from_db()
        self.assertEqual(req.approval_status, 'REJECTED')
        self.assertEqual(req.decision_remarks, "High project workload this week.")
        self.assertEqual(req.decided_by, self.mgr_user)

    @override_settings(HR_LEAVE_V2=True, HR_ATTENDANCE_ENGINE_V2=True)
    def test_10_cancellation_rules_and_reversal_ledger(self):
        """
        Cancellation:
        - Employee can cancel PENDING or CLARIFICATION leave.
        - Employee cannot cancel APPROVED leave (PermissionDenied).
        - HR/Admin can cancel APPROVED leave -> creates REVERSAL ledger entry restoring balance.
        """
        req = submit_leave_request(
            employee=self.staff_emp,
            leave_type_ref=self.annual_type,
            from_date=datetime.date(2026, 9, 7),
            to_date=datetime.date(2026, 9, 9),
            reason="Family trip",
            submitted_by=self.staff_user
        )
        # Working days = 3.0
        bal_before = get_employee_leave_balance(self.staff_emp, self.annual_type)

        # Manager approves
        approve_leave_request(req, self.mgr_user, remarks="Approved enjoy")
        req.refresh_from_db()
        self.assertEqual(req.approval_status, 'APPROVED')

        bal_after_approval = get_employee_leave_balance(self.staff_emp, self.annual_type)
        self.assertEqual(bal_after_approval, bal_before - Decimal('3.00'))

        # Employee attempts to cancel approved leave -> PermissionDenied
        with self.assertRaises(PermissionDenied):
            cancel_leave_request(req, self.staff_user, remarks="Cancel please")

        # HR cancels approved leave
        cancel_leave_request(req, self.hr_user, remarks="Cancelled upon client project requirement")
        req.refresh_from_db()
        self.assertEqual(req.approval_status, 'CANCELLED')

        # Balance restored via REVERSAL ledger entry
        bal_restored = get_employee_leave_balance(self.staff_emp, self.annual_type)
        self.assertEqual(bal_restored, bal_before)

        reversal_entry = LeaveLedger.objects.filter(
            leave=req, entry_type='REVERSAL'
        ).first()
        self.assertIsNotNone(reversal_entry)
        self.assertEqual(reversal_entry.days, Decimal('3.00'))

    @override_settings(HR_LEAVE_V2=True)
    def test_11_accrue_leave_command(self):
        """
        Management command accrue_leave:
        - Month 1-6 service: 0 days
        - Month 7-12 service: 2.0 days
        - Month 13+ service: 2.5 days
        - Idempotent: running twice does not double-accrue.
        - --dry-run: no database changes.
        """
        # Dry run first
        call_command('accrue_leave', '--month', '2026-03', '--dry-run')
        self.assertFalse(LeaveLedger.objects.filter(date__year=2026, date__month=3, entry_type='ACCRUAL').exists())

        # Real accrual run
        call_command('accrue_leave', '--month', '2026-03')

        # staff_emp should have 2.5 days accrued
        staff_accrual = LeaveLedger.objects.filter(
            employee=self.staff_emp, date__year=2026, date__month=3, entry_type='ACCRUAL'
        ).first()
        self.assertIsNotNone(staff_accrual)
        self.assertEqual(staff_accrual.days, Decimal('2.50'))

        # probation_emp should have 0.00 days accrued (probation)
        prob_accrual = LeaveLedger.objects.filter(
            employee=self.probation_emp, date__year=2026, date__month=3, entry_type='ACCRUAL'
        ).first()
        self.assertIsNotNone(prob_accrual)
        self.assertEqual(prob_accrual.days, Decimal('0.00'))

        # Idempotency check: run again for 2026-03, should skip
        call_command('accrue_leave', '--month', '2026-03')
        accruals_count = LeaveLedger.objects.filter(
            employee=self.staff_emp, date__year=2026, date__month=3, entry_type='ACCRUAL'
        ).count()
        self.assertEqual(accruals_count, 1)

    @override_settings(HR_LEAVE_V2=True)
    def test_12_close_leave_year_command(self):
        """
        Management command close_leave_year:
        - Max 5 days carried forward.
        - Excess (> 5 days) creates YEAR_END_EXCESS entry and PENDING LeaveYearReview.
        - Idempotent.
        """
        # staff_emp has 10 days balance in 2026. Let's close 2026.
        # Dry run first
        call_command('close_leave_year', '--year', '2026', '--dry-run')
        self.assertFalse(LeaveYearReview.objects.filter(year=2026).exists())

        # Real close year
        call_command('close_leave_year', '--year', '2026')

        # 5 days carry forward into 2027-01-01
        cf_entry = LeaveLedger.objects.filter(
            employee=self.staff_emp, date=datetime.date(2027, 1, 1), entry_type='CARRY_FORWARD'
        ).first()
        self.assertIsNotNone(cf_entry)
        self.assertEqual(cf_entry.days, Decimal('5.00'))

        # 5 days excess on 2026-12-31
        exp_entry = LeaveLedger.objects.filter(
            employee=self.staff_emp, date=datetime.date(2026, 12, 31), entry_type='YEAR_END_EXCESS'
        ).first()
        self.assertIsNotNone(exp_entry)
        self.assertEqual(exp_entry.days, Decimal('-5.00'))

        # 5 days close on 2026-12-31 to prevent double count
        close_entry = LeaveLedger.objects.filter(
            employee=self.staff_emp, date=datetime.date(2026, 12, 31), entry_type='YEAR_END_CLOSE'
        ).first()
        self.assertIsNotNone(close_entry)
        self.assertEqual(close_entry.days, Decimal('-5.00'))

        # Balance on 2027-01-01 is exactly 5.00
        bal_2027 = get_employee_leave_balance(self.staff_emp, self.annual_type, as_of_date=datetime.date(2027, 1, 1))
        self.assertEqual(bal_2027, Decimal('5.00'))

        # Review created for excess 5 days
        review = LeaveYearReview.objects.filter(
            employee=self.staff_emp, year=2026
        ).first()
        self.assertIsNotNone(review)
        self.assertEqual(review.excess_days, Decimal('5.00'))
        self.assertEqual(review.status, 'PENDING')

        # Idempotency: run again, no duplicate entries
        call_command('close_leave_year', '--year', '2026')
        reviews_count = LeaveYearReview.objects.filter(employee=self.staff_emp, year=2026).count()
        self.assertEqual(reviews_count, 1)

    @override_settings(HR_LEAVE_V2=True)
    def test_13_opening_balances_import(self):
        """
        Opening balances Excel template download, preview validation and confirmation import.
        """
        # 1. Generate template
        stream = generate_opening_balances_template()
        wb = openpyxl.load_workbook(stream)
        ws = wb.active
        self.assertEqual(ws.cell(row=1, column=1).value, "Employee Code")

        # 2. Build test Excel file with valid & invalid rows
        test_wb = openpyxl.Workbook()
        test_ws = test_wb.active
        test_ws.append(["Employee Code", "Employee Name", "Leave Type", "Effective Date (YYYY-MM-DD)", "Days", "Note"])
        test_ws.append(["AMMS-0004", "Salem Staff", "ANNUAL", "2026-01-01", 14.5, "Import test"])
        test_ws.append(["AMMS-INVALID", "Unknown", "ANNUAL", "2026-01-01", 5.0, "Invalid code"])

        out_stream = io.BytesIO()
        test_wb.save(out_stream)
        out_stream.seek(0)
        uploaded = SimpleUploadedFile("opening.xlsx", out_stream.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        # Only one opening balance per employee/leave type is allowed: clear the one seeded in setUp
        LeaveLedger.objects.filter(employee=self.staff_emp, leave_type=self.annual_type, entry_type='OPENING').delete()

        # Preview (dry-run)
        preview_res = process_opening_balances_excel(uploaded, user=self.admin_user, dry_run=True)
        self.assertEqual(preview_res['total_count'], 2)
        self.assertEqual(preview_res['valid_count'], 1)
        self.assertEqual(preview_res['error_count'], 1)

        # Confirm import with valid rows only
        valid_wb = openpyxl.Workbook()
        valid_ws = valid_wb.active
        valid_ws.append(["Employee Code", "Employee Name", "Leave Type", "Effective Date (YYYY-MM-DD)", "Days", "Note"])
        valid_ws.append(["AMMS-0004", "Salem Staff", "ANNUAL", "2026-01-01", 14.5, "Import test"])
        valid_stream = io.BytesIO()
        valid_wb.save(valid_stream)
        valid_stream.seek(0)
        valid_uploaded = SimpleUploadedFile("opening_valid.xlsx", valid_stream.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        import_res = process_opening_balances_excel(valid_uploaded, user=self.admin_user, dry_run=False)
        self.assertEqual(import_res['valid_count'], 1)

        # Verify entry created in ledger
        entry = LeaveLedger.objects.filter(
            employee=self.staff_emp, entry_type='OPENING', days=Decimal('14.50')
        ).first()
        self.assertIsNotNone(entry)

    @override_settings(HR_LEAVE_V2=True, HR_ATTENDANCE_ENGINE_V2=True)
    def test_14_attendance_register_v2_status_and_summary(self):
        """
        Attendance register status mapping (D8) & summary:
        - Approved Annual Leave maps to AL.
        - Approved Unpaid Leave maps to UL.
        - Half-day Annual Leave maps to 0.5 AL.
        - Rest day inside leave range is rendered as REST_DAY (OFF), not leave.
        - Unpaid leave days tracked in summary.
        """
        # Approved Unpaid Leave on Wednesday 2026-03-04
        submit_leave_request(
            employee=self.staff_emp,
            leave_type_ref=self.unpaid_type,
            from_date=datetime.date(2026, 3, 4),
            to_date=datetime.date(2026, 3, 4),
            reason="Unpaid emergency",
            submitted_by=self.staff_user
        )
        unpaid_req = Leave.objects.filter(employee=self.staff_emp, from_date=datetime.date(2026, 3, 4)).first()
        approve_leave_request(unpaid_req, self.mgr_user, remarks="Approved unpaid")

        # Approved Half-day Annual Leave on Thursday 2026-03-05
        submit_leave_request(
            employee=self.staff_emp,
            leave_type_ref=self.annual_type,
            from_date=datetime.date(2026, 3, 5),
            to_date=datetime.date(2026, 3, 5),
            is_half_day=True,
            half_day_part='AM',
            reason="Half day dentist",
            submitted_by=self.staff_user
        )
        half_req = Leave.objects.filter(employee=self.staff_emp, from_date=datetime.date(2026, 3, 5)).first()
        approve_leave_request(half_req, self.mgr_user, remarks="Approved half day")

        # Range leave spanning Sunday 2026-03-08 to Monday 2026-03-09
        submit_leave_request(
            employee=self.staff_emp,
            leave_type_ref=self.annual_type,
            from_date=datetime.date(2026, 3, 8),
            to_date=datetime.date(2026, 3, 9),
            reason="Trip",
            submitted_by=self.staff_user
        )
        span_req = Leave.objects.filter(employee=self.staff_emp, from_date=datetime.date(2026, 3, 8)).first()
        approve_leave_request(span_req, self.mgr_user, remarks="Approved")

        # Build attendance register for March 2026
        reg = build_attendance_register(
            datetime.date(2026, 3, 1), datetime.date(2026, 3, 31),
            search='staff_usr'
        )
        day_map = {row['date'].day: row for row in reg if row['employee'] == self.staff_emp}

        # 2026-03-04: Unpaid Leave -> status code 'UNPAID_LEAVE'
        self.assertEqual(day_map[4]['status_code'], 'UNPAID_LEAVE')
        self.assertEqual(day_map[4]['status_label'], 'Unpaid Leave')

        # 2026-03-05: Half-day Annual Leave -> status label contains 'Half day'
        self.assertIn('Half day', day_map[5]['status_label'])
        self.assertEqual(day_map[5]['status_code'], 'ANNUAL_LEAVE')

        # 2026-03-08 (Sunday): Rest Day inside range -> status code 'REST_DAY'
        self.assertEqual(day_map[8]['status_code'], 'REST_DAY')

        # 2026-03-09 (Monday): Working day inside range -> status code 'ANNUAL_LEAVE'
        self.assertEqual(day_map[9]['status_code'], 'ANNUAL_LEAVE')

        # Attendance summary unpaid_leave_days
        summary_rows, totals = build_attendance_summary(reg)
        emp_summary = summary_rows[0]
        self.assertEqual(emp_summary['unpaid_leave_days'], Decimal('1.00'))

    @override_settings(HR_LEAVE_V2=True, HR_ATTENDANCE_ENGINE_V2=True)
    def test_15_payroll_deduction_for_unpaid_leave(self):
        """
        Unpaid leave days are deducted from salary at pay_base_salary / 26 per day.
        Annual & Sick leave do not reduce payroll.
        """
        # Approved 1 full day Unpaid Leave (2026-03-04)
        submit_leave_request(
            employee=self.staff_emp,
            leave_type_ref=self.unpaid_type,
            from_date=datetime.date(2026, 3, 4),
            to_date=datetime.date(2026, 3, 4),
            reason="Unpaid emergency",
            submitted_by=self.staff_user
        )
        req = Leave.objects.filter(employee=self.staff_emp, from_date=datetime.date(2026, 3, 4)).first()
        approve_leave_request(req, self.mgr_user, remarks="Approved unpaid")

        # Get monthly attendance metrics
        first_d = datetime.date(2026, 3, 1)
        last_d = datetime.date(2026, 3, 31)
        metrics = get_month_attendance_metrics(first_d, last_d)
        emp_m = metrics.get(self.staff_emp.id, {})

        # Unpaid days must include the 1.0 day of unpaid leave
        self.assertGreaterEqual(emp_m.get('unpaid_days', 0), Decimal('1.00'))

        # Generate payroll for March 2026
        generate_payroll(first_d, user=self.admin_user)
        pr = Payroll.objects.filter(employee=self.staff_emp, month=first_d).first()
        self.assertIsNotNone(pr)
        self.assertGreaterEqual(pr.absent_days_unpaid, Decimal('1.00'))

        # Daily rate = 10400 / 26 = 400.00
        expected_deduction = Decimal('400.00')
        self.assertEqual(pr.absence_deduction, expected_deduction)

    @override_settings(HR_LEAVE_V2=True)
    def test_16_leave_statement_and_list_exports(self):
        """Excel and PDF exports for Leave Statement and Leave List return 200 with correct content-type."""
        # 1. Leave list Excel export
        leaves_qs = Leave.objects.filter(employee=self.staff_emp)
        excel_stream = export_leave_list_excel(leaves_qs)
        self.assertIsInstance(excel_stream, io.BytesIO)
        self.assertGreater(excel_stream.getbuffer().nbytes, 100)

        # 2. Leave statement Excel export
        summary = get_employee_leave_summary(self.staff_emp, year=2026)
        entries = [{'entry': e, 'running_balance': e.days} for e in LeaveLedger.objects.filter(employee=self.staff_emp)]
        stmt_stream = export_leave_statement_excel(self.staff_emp, 2026, summary, entries)
        self.assertIsInstance(stmt_stream, io.BytesIO)
        self.assertGreater(stmt_stream.getbuffer().nbytes, 100)

        # 3. HTTP View GET test with export=excel and export=pdf
        self.client.login(username='admin_usr', password='pass123')
        resp_list_xl = self.client.get(reverse('employee_leave_list') + '?export=excel')
        self.assertEqual(resp_list_xl.status_code, 200)
        self.assertEqual(resp_list_xl['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

        resp_stmt_xl = self.client.get(reverse('admin_leave_statement') + f'?employee={self.staff_emp.id}&year=2026&export=excel')
        self.assertEqual(resp_stmt_xl.status_code, 200)
        self.assertEqual(resp_stmt_xl['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    @override_settings(HR_LEAVE_V2=True)
    def test_17_payroll_lock_prevents_leave_modification(self):
        """
        check_payroll_lock_for_employee:
        Cannot approve, reject, or cancel leave in a month that has APPROVED or PAID payroll.
        """
        # Create an APPROVED payroll for staff_emp in March 2026
        Payroll.objects.create(
            employee=self.staff_emp,
            month=datetime.date(2026, 3, 1),
            status='APPROVED',
            basic_salary=Decimal('10400.00'),
        )

        # Create a pending leave in March 2026
        req = Leave.objects.create(
            employee=self.staff_emp,
            user=self.staff_user,
            leave_type='ANNUAL LEAVE',
            leave_type_ref=self.annual_type,
            from_date=datetime.date(2026, 3, 16),
            to_date=datetime.date(2026, 3, 18),
            no_of_days=3,
            days=Decimal('3.00'),
            approval_status='PENDING'
        )

        # Approving should be blocked by payroll lock
        with self.assertRaises(ValidationError) as ctx:
            approve_leave_request(req, self.mgr_user, remarks="Approve")
        self.assertIn("is already approved", str(ctx.exception))

        # Cancelling should be blocked by payroll lock
        with self.assertRaises(ValidationError) as ctx:
            cancel_leave_request(req, self.hr_user, remarks="Cancel")
        self.assertIn("is already approved", str(ctx.exception))

    @override_settings(HR_LEAVE_V2=True)
    def test_18_year_end_close_no_double_count_and_real_ledger(self):
        """
        Issue 1:
        OPENING 8 -> YEAR_END_EXCESS -3 (31/12) + YEAR_END_CLOSE -5 (31/12) + CARRY_FORWARD +5 (01/01)
        -> Balance 5 on 01/01.
        Review shows unused 8 / carried 5 / excess 3.
        Statement for the new year shows 'Carried forward 5'.
        Re-run is a no-op.
        """
        u = User.objects.create_user('ye_usr', 'ye@amms.ae', 'pass123', first_name='Year', last_name='End')
        emp = Employee.objects.create(
            user=u, employee_code='AMMS-0008', salary=Decimal('10000.00'),
            date_of_join=datetime.date(2025, 1, 1), branch=self.branch, is_employee=True
        )
        LeaveLedger.objects.create(
            employee=emp, leave_type=self.annual_type, date=datetime.date(2026, 1, 1),
            entry_type='OPENING', days=Decimal('8.00'), note='Opening balance 8 days'
        )

        # Run close_leave_year for 2026
        close_leave_year_for_all(2026)

        # 31/12 entries
        excess_entry = LeaveLedger.objects.filter(employee=emp, date=datetime.date(2026, 12, 31), entry_type='YEAR_END_EXCESS').first()
        close_entry = LeaveLedger.objects.filter(employee=emp, date=datetime.date(2026, 12, 31), entry_type='YEAR_END_CLOSE').first()
        self.assertIsNotNone(excess_entry)
        self.assertEqual(excess_entry.days, Decimal('-3.00'))
        self.assertIsNotNone(close_entry)
        self.assertEqual(close_entry.days, Decimal('-5.00'))

        # 01/01 entry
        cf_entry = LeaveLedger.objects.filter(employee=emp, date=datetime.date(2027, 1, 1), entry_type='CARRY_FORWARD').first()
        self.assertIsNotNone(cf_entry)
        self.assertEqual(cf_entry.days, Decimal('5.00'))

        # Net balance on 01/01 is 5.00
        bal_0101 = get_employee_leave_balance(emp, self.annual_type, as_of_date=datetime.date(2027, 1, 1))
        self.assertEqual(bal_0101, Decimal('5.00'))

        # Review record shows unused 8, carried 5, excess 3
        rev = LeaveYearReview.objects.filter(employee=emp, year=2026).first()
        self.assertIsNotNone(rev)
        self.assertEqual(rev.unused_days, Decimal('8.00'))
        self.assertEqual(rev.carried_days, Decimal('5.00'))
        self.assertEqual(rev.excess_days, Decimal('3.00'))

        # Statement for 2027 shows Carried forward 5
        summary = get_employee_leave_summary(emp, year=2027)
        ann_summary = [s for s in summary if s['code'] == 'ANNUAL'][0]
        self.assertEqual(ann_summary['carried_days'], Decimal('5.00'))
        self.assertEqual(ann_summary['net_balance'], Decimal('5.00'))

        # Re-run is a no-op
        initial_entries_count = LeaveLedger.objects.filter(employee=emp).count()
        close_leave_year_for_all(2026)
        self.assertEqual(LeaveLedger.objects.filter(employee=emp).count(), initial_entries_count)
        self.assertEqual(get_employee_leave_balance(emp, self.annual_type, as_of_date=datetime.date(2027, 1, 1)), Decimal('5.00'))

    @override_settings(HR_LEAVE_V2=True)
    def test_19_sick_leave_entitlement_and_expiry(self):
        """
        Issue 2:
        - Full yearly_entitlement granted on 01/01 (period=01/01, idempotent).
        - Mid-year joiner gets it pro-rated from date_of_join.
        - Take 2 days; year end expires remaining to 0 (no review needed).
        - Next year grants 15 again.
        - Backfill command: accrue_leave --entitlements-for 2026.
        """
        u = User.objects.create_user('sk_usr', 'sk@amms.ae', 'pass123', first_name='Sick', last_name='Staff')
        emp = Employee.objects.create(
            user=u, employee_code='AMMS-0009', salary=Decimal('10000.00'),
            date_of_join=datetime.date(2025, 1, 1), branch=self.branch, reporting_manager=self.mgr_emp, is_employee=True
        )

        # 1. Run backfill command: accrue_leave --entitlements-for 2026
        call_command('accrue_leave', '--entitlements-for', '2026')
        ent_entry = LeaveLedger.objects.filter(employee=emp, leave_type=self.sick_type, entry_type='ENTITLEMENT', period=datetime.date(2026, 1, 1)).first()
        self.assertIsNotNone(ent_entry)
        self.assertEqual(ent_entry.days, Decimal('15.00'))
        self.assertEqual(get_employee_leave_balance(emp, self.sick_type, as_of_date=datetime.date(2026, 1, 1)), Decimal('15.00'))

        # 2. Take 2 days sick leave in 2026 (requires attachment for >= 2 days)
        from django.core.files.uploadedfile import SimpleUploadedFile
        cert = SimpleUploadedFile("cert.pdf", b"%PDF-1.4 sample medical cert", content_type="application/pdf")
        req = submit_leave_request(emp, self.sick_type, datetime.date(2026, 4, 1), datetime.date(2026, 4, 2), "Flu", attachment=cert, submitted_by=u)
        approve_leave_request(req, self.mgr_user, "Approved sick leave")
        self.assertEqual(get_employee_leave_balance(emp, self.sick_type, as_of_date=datetime.date(2026, 4, 3)), Decimal('13.00'))

        # 3. Year-end close for 2026: expires remaining 13 days at 31/12
        close_leave_year_for_all(2026)
        exp_entry = LeaveLedger.objects.filter(employee=emp, leave_type=self.sick_type, entry_type='EXPIRED', date=datetime.date(2026, 12, 31)).first()
        self.assertIsNotNone(exp_entry)
        self.assertEqual(exp_entry.days, Decimal('-13.00'))
        self.assertEqual(get_employee_leave_balance(emp, self.sick_type, as_of_date=datetime.date(2026, 12, 31)), Decimal('0.00'))
        # No review record for Sick leave
        self.assertFalse(LeaveYearReview.objects.filter(employee=emp, year=2026).exists())

        # 4. Next year 2027: grant 15 again
        call_command('accrue_leave', '--entitlements-for', '2027')
        ent_2027 = LeaveLedger.objects.filter(employee=emp, leave_type=self.sick_type, entry_type='ENTITLEMENT', period=datetime.date(2027, 1, 1)).first()
        self.assertIsNotNone(ent_2027)
        self.assertEqual(ent_2027.days, Decimal('15.00'))
        self.assertEqual(get_employee_leave_balance(emp, self.sick_type, as_of_date=datetime.date(2027, 1, 1)), Decimal('15.00'))

        # 5. Mid-year joiner: joins 15/07/2026 (6 remaining months in 2026: Jul-Dec)
        # Pro-rata: 15 * 6 / 12 = 7.5 days
        u_mid = User.objects.create_user('mid_usr', 'mid@amms.ae', 'pass123', first_name='Mid', last_name='Joiner')
        emp_mid = Employee.objects.create(
            user=u_mid, employee_code='AMMS-0010', salary=Decimal('9000.00'),
            date_of_join=datetime.date(2026, 7, 15), branch=self.branch, is_employee=True
        )
        call_command('accrue_leave', '--entitlements-for', '2026')
        mid_entry = LeaveLedger.objects.filter(employee=emp_mid, leave_type=self.sick_type, entry_type='ENTITLEMENT').first()
        self.assertIsNotNone(mid_entry)
        self.assertEqual(mid_entry.date, datetime.date(2026, 7, 15))
        self.assertEqual(mid_entry.days, Decimal('7.50'))

    @override_settings(HR_LEAVE_V2=True)
    def test_20_accrual_month_calculation_exact_timeline(self):
        """
        Issue 3:
        completed_months <= 6 -> 0; 7-12 -> 2.0; >= 13 -> 2.5.
        Joined 15/03/2026: Apr-Sep 0, first +2 in October (month 7 completes 14/10),
        total 12 for the first year (Oct-Mar), then 2.5.
        """
        u = User.objects.create_user('acc_usr', 'acc@amms.ae', 'pass123', first_name='Acc', last_name='Timeline')
        emp = Employee.objects.create(
            user=u, employee_code='AMMS-0011', salary=Decimal('9000.00'),
            date_of_join=datetime.date(2026, 3, 15), branch=self.branch, is_employee=True
        )

        expected_accruals = {
            (2026, 3): Decimal('0.00'),
            (2026, 4): Decimal('0.00'),
            (2026, 5): Decimal('0.00'),
            (2026, 6): Decimal('0.00'),
            (2026, 7): Decimal('0.00'),
            (2026, 8): Decimal('0.00'),
            (2026, 9): Decimal('0.00'),  # completed_months = 6 <= 6
            (2026, 10): Decimal('2.00'), # month 7 completes on 14/10 -> first +2.0
            (2026, 11): Decimal('2.00'),
            (2026, 12): Decimal('2.00'),
            (2027, 1): Decimal('2.00'),
            (2027, 2): Decimal('2.00'),
            (2027, 3): Decimal('2.00'),  # month 12 completes -> total 12 days for first year
            (2027, 4): Decimal('2.50'),  # month 13 completes -> 2.5
        }

        total_first_year = Decimal('0.00')
        for (y, m), expected in expected_accruals.items():
            accrue_leave_for_month(y, m)
            entry = LeaveLedger.objects.filter(employee=emp, leave_type=self.annual_type, entry_type='ACCRUAL', date__year=y, date__month=m).first()
            self.assertIsNotNone(entry, f"Missing accrual entry for {y}-{m:02d}")
            self.assertEqual(entry.days, expected, f"Failed accrual for {y}-{m:02d}: expected {expected}, got {entry.days}")
            if (y == 2026 and m >= 10) or (y == 2027 and m <= 3):
                total_first_year += entry.days

        self.assertEqual(total_first_year, Decimal('12.00'))

    @override_settings(HR_LEAVE_V2=True)
    def test_21_role_leave_urls_smoke_tests(self):
        """
        Issue 4:
        With HR_LEAVE_V2=True, GETs every leave URL for each role
        (employee, manager, HR, accountant, administration, superuser: apply, status, records,
        manage, list, statement, import, year-end reviews, calculate-days API) and asserts 200
        (or 403 where the role must not see it).
        """
        roles = [
            {
                'name': 'Employee',
                'user': self.staff_user,
                'allowed': [
                    reverse('apply_leave'),
                    reverse('my_leave'),
                    reverse('leave_records'),
                    reverse('api_calculate_leave_days') + '?from_date=2026-05-01&to_date=2026-05-02',
                ],
                'forbidden': [
                    reverse('manage_leave'),
                    reverse('leave_list'),
                    reverse('hr_employee_manage_leave'),
                    reverse('hr_employee_leave_list'),
                    reverse('hr_import_opening_balances'),
                    reverse('hr_leave_year_reviews'),
                    reverse('admin_import_opening_balances'),
                    reverse('admin_leave_year_reviews'),
                    reverse('employee_leave_list'),
                    reverse('employee_manage_leave'),
                ]
            },
            {
                'name': 'Manager',
                'user': self.mgr_user,
                'allowed': [
                    reverse('manager_apply_leave'),
                    reverse('manager_leave_status'),
                    reverse('manager_leave_records'),
                    reverse('manage_leave'),
                    reverse('leave_list'),
                    reverse('api_calculate_leave_days') + '?from_date=2026-05-01&to_date=2026-05-02',
                ],
                'forbidden': [
                    reverse('hr_import_opening_balances'),
                    reverse('hr_leave_year_reviews'),
                    reverse('admin_import_opening_balances'),
                    reverse('admin_leave_year_reviews'),
                    reverse('employee_leave_list'),
                    reverse('employee_manage_leave'),
                ]
            },
            {
                'name': 'HR',
                'user': self.hr_user,
                'allowed': [
                    reverse('hr_apply_leave'),
                    reverse('hr_leave_status'),
                    reverse('hr_leave_records'),
                    reverse('hr_employee_manage_leave'),
                    reverse('hr_employee_leave_list'),
                    reverse('hr_leave_statement'),
                    reverse('hr_import_opening_balances'),
                    reverse('hr_leave_year_reviews'),
                    reverse('api_calculate_leave_days') + '?from_date=2026-05-01&to_date=2026-05-02',
                ],
                'forbidden': [
                    reverse('admin_import_opening_balances'),
                    reverse('admin_leave_year_reviews'),
                    reverse('employee_leave_list'),
                    reverse('employee_manage_leave'),
                ]
            },
            {
                'name': 'Accountant',
                'user': self.acct_user,
                'allowed': [
                    reverse('fin_apply_leave'),
                    reverse('fin_leave_status'),
                    reverse('fin_leave_records'),
                    reverse('api_calculate_leave_days') + '?from_date=2026-05-01&to_date=2026-05-02',
                ],
                'forbidden': [
                    reverse('manage_leave'),
                    reverse('hr_employee_manage_leave'),
                    reverse('hr_import_opening_balances'),
                    reverse('hr_leave_year_reviews'),
                    reverse('admin_import_opening_balances'),
                    reverse('admin_leave_year_reviews'),
                    reverse('employee_leave_list'),
                    reverse('employee_manage_leave'),
                ]
            },
            {
                'name': 'Administration',
                'user': self.adm_user,
                'allowed': [
                    reverse('admstrn_apply_leave'),
                    reverse('admstrn_leave_status'),
                    reverse('admstrn_leave_records'),
                    reverse('api_calculate_leave_days') + '?from_date=2026-05-01&to_date=2026-05-02',
                ],
                'forbidden': [
                    reverse('manage_leave'),
                    reverse('hr_employee_manage_leave'),
                    reverse('hr_import_opening_balances'),
                    reverse('hr_leave_year_reviews'),
                    reverse('admin_import_opening_balances'),
                    reverse('admin_leave_year_reviews'),
                    reverse('employee_leave_list'),
                    reverse('employee_manage_leave'),
                ]
            },
            {
                'name': 'Superuser',
                'user': self.admin_user,
                'allowed': [
                    reverse('hr_apply_leave'),
                    reverse('hr_leave_status'),
                    reverse('hr_leave_records'),
                    reverse('employee_leave_list'),
                    reverse('manager_leave_list'),
                    reverse('employee_manage_leave'),
                    reverse('manager_manage_leave'),
                    reverse('admin_leave_statement'),
                    reverse('admin_import_opening_balances'),
                    reverse('admin_leave_year_reviews'),
                    reverse('api_calculate_leave_days') + '?from_date=2026-05-01&to_date=2026-05-02',
                ],
                'forbidden': []
            },
        ]

        for role_info in roles:
            self.client.force_login(role_info['user'])
            # Test allowed URLs return 200
            for url in role_info['allowed']:
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200, f"Role {role_info['name']} failed to GET {url}: status {resp.status_code}")

            # Test forbidden URLs return 403
            for url in role_info['forbidden']:
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 403, f"Role {role_info['name']} expected 403 for {url}: got {resp.status_code}")


class OpeningBalanceImportViewTests(TestCase):
    """Upload -> preview -> Confirm through the real page saves the ledger rows (the Confirm form has no file)."""

    def _xlsx(self, rows):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Employee Code", "Employee Name", "Leave Type", "Effective Date (YYYY-MM-DD)", "Days", "Note"])
        for r in rows:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        return SimpleUploadedFile("opening.xlsx", buf.getvalue(),
                                  content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def setUp(self):
        self.hr_user = User.objects.create_user('hr_ob', 'hr_ob@x.com', 'pw')
        Employee.objects.create(user=self.hr_user, is_hr=True, salary=Decimal('1000'),
                                date_of_join=datetime.date(2025, 1, 1), employee_code='AMMS-9001')
        self.emp = Employee.objects.create(user=User.objects.create_user('eng_ob', 'e_ob@x.com', 'pw'),
                                           is_employee=True, salary=Decimal('1000'),
                                           date_of_join=datetime.date(2025, 1, 1), employee_code='AMMS-9002')
        self.client.force_login(self.hr_user)
        self.url = reverse('hr_import_opening_balances')

    def test_preview_then_confirm_saves(self):
        res = self.client.post(self.url, {'file': self._xlsx([["AMMS-9002", "eng_ob", "ANNUAL", "2026-11-13", 12, "Opening"]])})
        self.assertEqual(res.status_code, 200)
        token = res.context['upload_token']
        self.assertTrue(token)
        self.assertFalse(LeaveLedger.objects.filter(employee=self.emp, entry_type='OPENING').exists())
        # Confirm button posts only confirm + token (no file)
        res = self.client.post(self.url, {'confirm': '1', 'upload_token': token})
        self.assertRedirects(res, self.url)
        entry = LeaveLedger.objects.get(employee=self.emp, entry_type='OPENING')
        self.assertEqual(entry.days, Decimal('12'))
        # token is single use
        res = self.client.post(self.url, {'confirm': '1', 'upload_token': token})
        self.assertEqual(LeaveLedger.objects.filter(employee=self.emp, entry_type='OPENING').count(), 1)

    def test_second_import_and_duplicate_rows_are_blocked(self):
        res = self.client.post(self.url, {'file': self._xlsx([["AMMS-9002", "eng_ob", "ANNUAL", "2026-11-13", 12, ""],
                                                              ["AMMS-9002", "eng_ob", "ANNUAL", "2026-11-13", 3, ""]])})
        self.assertTrue(res.context['preview']['has_errors'])
        self.assertEqual(res.context['upload_token'], '')
        res = self.client.post(self.url, {'file': self._xlsx([["AMMS-9002", "eng_ob", "ANNUAL", "2026-11-13", 12, ""]])})
        self.client.post(self.url, {'confirm': '1', 'upload_token': res.context['upload_token']})
        res = self.client.post(self.url, {'file': self._xlsx([["AMMS-9002", "eng_ob", "ANNUAL", "2026-11-13", 12, ""]])})
        self.assertTrue(res.context['preview']['has_errors'])
        self.assertIn('already has an opening balance', str(res.context['preview']['rows'][0]['errors']))
        self.assertEqual(LeaveLedger.objects.filter(employee=self.emp, entry_type='OPENING').count(), 1)


@override_settings(HR_LEAVE_V2=True)
class LeaveClarificationRoundTripTests(TestCase):
    """HR asks a question, the employee answers: both pages show the Q&A and the queue marks it."""

    def test_round_trip(self):
        from Admin.leave_service import request_leave_clarification, respond_leave_clarification
        hr_user = User.objects.create_user('hr_cl', 'hr_cl@x.com', 'pw')
        Employee.objects.create(user=hr_user, is_hr=True, salary=Decimal('1000'), date_of_join=datetime.date(2025, 1, 1))
        emp_user = User.objects.create_user('eng_cl', 'e_cl@x.com', 'pw', first_name='James', last_name='Joe')
        emp = Employee.objects.create(user=emp_user, is_employee=True, salary=Decimal('1000'),
                                      date_of_join=datetime.date(2025, 1, 1))
        annual = LeaveType.objects.get(code='ANNUAL')
        leave = Leave.objects.create(user=emp_user, employee=emp, leave_type='ANNUAL LEAVE', leave_type_ref=annual,
                                     from_date=datetime.date(2026, 12, 2), to_date=datetime.date(2026, 12, 2),
                                     days=Decimal('1.00'), reason='test', approval_status='PENDING')
        request_leave_clarification(leave, hr_user, 'Why this day?')
        respond_leave_clarification(Leave.objects.get(pk=leave.pk), emp_user, 'Bank Visit')

        self.client.force_login(hr_user)
        res = self.client.get(reverse('hr_employee_manage_leave') + '?status=PENDING')
        self.assertContains(res, 'Why this day?')
        self.assertContains(res, 'Bank Visit')
        self.assertContains(res, 'Response received')
        self.assertContains(res, 'data-bs-dismiss="modal"')
        html = res.content.decode()
        self.assertEqual(html.count(' data-dismiss="modal"'), html.count('data-bs-dismiss="modal" data-dismiss="modal"'))

        self.client.force_login(emp_user)
        res = self.client.get(reverse('my_leave'))
        self.assertContains(res, 'Bank Visit')


class OpeningBalanceImportMatchingTests(TestCase):
    """Rows match on Employee Code only (never the internal id), and the file name must be the same person."""

    def _xlsx(self, rows):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Employee Code", "Employee Name", "Leave Type", "Effective Date (YYYY-MM-DD)", "Days", "Note"])
        for r in rows:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    def test_codes_not_matched_by_internal_id(self):
        # Make internal ids and codes disagree, like on live data
        people = []
        for code, first in (('AMMS-0003', 'Ananda'), ('AMMS-0001', 'Mahesh'), ('AMMS-0002', 'Shiva')):
            u = User.objects.create_user(f'u_{code}', f'{code}@x.com', 'pw', first_name=first, last_name='K')
            people.append(Employee.objects.create(user=u, is_employee=True, salary=Decimal('1000'),
                                                  date_of_join=datetime.date(2025, 1, 1), employee_code=code))
        rows = [[e.employee_code, e.name, "ANNUAL", "2026-01-01", 24, ""] for e in people]
        res = process_opening_balances_excel(self._xlsx(rows[:3]), dry_run=True)
        self.assertEqual(res['error_count'], 0, [r['errors'] for r in res['rows']])
        for r, e in zip(res['rows'], people):
            self.assertEqual(r['employee'], e)

    def test_name_mismatch_is_an_error(self):
        u = User.objects.create_user('u_mm', 'mm@x.com', 'pw', first_name='Mahadhir', last_name='Mohamed')
        Employee.objects.create(user=u, is_employee=True, salary=Decimal('1000'),
                                date_of_join=datetime.date(2025, 1, 1), employee_code='AMMS-0106')
        res = process_opening_balances_excel(self._xlsx([["AMMS-0106", "Manager Manager", "ANNUAL", "2026-01-01", 24, ""]]), dry_run=True)
        self.assertEqual(res['error_count'], 1)
        self.assertIn("belongs to 'Mahadhir Mohamed'", res['rows'][0]['errors'][0])
        # same name with different spacing/case is fine; blank name skips the check
        res = process_opening_balances_excel(self._xlsx([["amms-0106", "  MAHADHIR   mohamed ", "ANNUAL", "2026-01-01", 24, ""]]), dry_run=True)
        self.assertEqual(res['error_count'], 0)
        res = process_opening_balances_excel(self._xlsx([["AMMS-0106", None, "ANNUAL", "2026-01-01", 24, ""]]), dry_run=True)
        self.assertEqual(res['error_count'], 0)

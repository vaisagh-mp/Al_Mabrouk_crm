import datetime
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from Admin.models import (
    Employee, Department, Branch, DutySchedule, EmployeeSchedule,
    RestDayRule, RosterDay, Holiday, DailyAttendanceStatus,
    SalaryStructure, Attendance, update_employee_rest_day_rule,
    update_employee_schedule, check_payroll_lock_for_employee,
    check_payroll_lock_for_holiday
)
from Admin.attendance_register import build_attendance_register, build_attendance_summary
from Admin.attendance_engine import (
    is_rest_day, rest_day_map, public_holiday_map, pay_base_salary
)
from service_logs.ot import ot_multiplier, ot_type_for, calculate_office_ot_cost
from finance.models import Payroll
from finance.services import generate_payroll, get_month_attendance_metrics


class HRPhase2StatusEngineTests(TestCase):
    def setUp(self):
        self.branch_a = Branch.objects.create(name="Dubai Main", sort_order=1)
        self.branch_b = Branch.objects.create(name="Abu Dhabi Port", sort_order=2)
        self.dept = Department.objects.create(name="Operations", sort_order=1)

        # Standard employee
        self.user1 = User.objects.create_user('emp1', 'emp1@amms.ae', 'pass1', first_name='Ahmed', last_name='Ali')
        self.emp1 = Employee.objects.create(
            user=self.user1,
            employee_code='AMMS-0001',
            salary=Decimal('10000.00'),
            date_of_join=datetime.date(2025, 1, 1),
            branch=self.branch_a,
            department=self.dept,
            is_employee=True
        )

        self.admin_user = User.objects.create_superuser('admin_phase2', 'admin_p2@amms.ae', 'pass')

        # Second employee in Branch B
        self.user2 = User.objects.create_user('emp2', 'emp2@amms.ae', 'pass2', first_name='Bilal', last_name='Khan')
        self.emp2 = Employee.objects.create(
            user=self.user2,
            employee_code='AMMS-0002',
            salary=Decimal('8000.00'),
            date_of_join=datetime.date(2025, 6, 1),
            branch=self.branch_b,
            department=self.dept,
            is_employee=True
        )

    def test_1_v2_off_identical_behavior(self):
        """
        1. V2 off: register rows/payroll/OT identical to current behavior.
        """
        with override_settings(HR_ATTENDANCE_ENGINE_V2=False):
            # November 2026: 01/11/2026 is a Sunday
            rows = build_attendance_register(datetime.date(2026, 11, 1), datetime.date(2026, 11, 30))
            nov1 = next(r for r in rows if r['employee'].id == self.emp1.id and r['date'] == datetime.date(2026, 11, 1))
            self.assertEqual(nov1['status_code'], 'SUNDAY')
            self.assertEqual(nov1['status_label'], 'Sunday')

            # OT multipliers with V2 off
            sunday = datetime.date(2026, 11, 1) # Sunday
            friday = datetime.date(2026, 11, 6) # Friday
            self.assertEqual(ot_multiplier(sunday, is_public_holiday=False), Decimal('1.5'))
            self.assertEqual(ot_multiplier(friday, is_public_holiday=False), Decimal('1.25'))
            self.assertEqual(ot_multiplier(friday, is_public_holiday=True), Decimal('2.0'))

            # Missing punch is not created when V2 is off
            # Nov 2 (Monday) has no punch, status should not be MISSING_PUNCH in V1
            nov2 = next(r for r in rows if r['employee'].id == self.emp1.id and r['date'] == datetime.date(2026, 11, 2))
            self.assertNotEqual(nov2['status_code'], 'MISSING_PUNCH')

    def test_2_friday_rest_day_from_november(self):
        """
        2. Friday rest day from 01/11: Fridays REST_DAY, Sundays working days (MISSING_PUNCH if no work), October unchanged.
        """
        # Set rest day to Friday (rest_weekday=4) effective 2026-11-01
        update_employee_rest_day_rule(
            self.emp1,
            rest_weekday=4,
            uses_roster=False,
            effective_from=datetime.date(2026, 11, 1)
        )

        with override_settings(HR_ATTENDANCE_ENGINE_V2=True):
            # In November 2026:
            rows_nov = build_attendance_register(datetime.date(2026, 11, 1), datetime.date(2026, 11, 30))
            # Nov 6 is Friday -> REST_DAY
            nov6 = next(r for r in rows_nov if r['employee'].id == self.emp1.id and r['date'] == datetime.date(2026, 11, 6))
            self.assertEqual(nov6['status_code'], 'REST_DAY')

            # Nov 1 is Sunday -> working day, past date with no punch -> MISSING_PUNCH
            nov1 = next(r for r in rows_nov if r['employee'].id == self.emp1.id and r['date'] == datetime.date(2026, 11, 1))
            self.assertEqual(nov1['status_code'], 'MISSING_PUNCH')
            self.assertEqual(nov1['missing_punch_type'], 'NO_PUNCH')

            # In October 2026 (before rule start):
            rows_oct = build_attendance_register(datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))
            # Oct 4 is Sunday -> default rest day (REST_DAY)
            oct4 = next(r for r in rows_oct if r['employee'].id == self.emp1.id and r['date'] == datetime.date(2026, 10, 4))
            self.assertEqual(oct4['status_code'], 'REST_DAY')
            # Oct 2 is Friday -> working day
            oct2 = next(r for r in rows_oct if r['employee'].id == self.emp1.id and r['date'] == datetime.date(2026, 10, 2))
            self.assertEqual(oct2['status_code'], 'MISSING_PUNCH')

    def test_3_rest_day_change_mid_month_and_payroll_lock(self):
        """
        3. Rest day change mid-month splits correctly; rejected inside approved month.
        """
        # Sunday rule from 01/11/2026
        update_employee_rest_day_rule(
            self.emp1,
            rest_weekday=6,
            effective_from=datetime.date(2026, 11, 1)
        )
        # Split mid-month on 15/11/2026 to Friday (weekday=4)
        update_employee_rest_day_rule(
            self.emp1,
            rest_weekday=4,
            effective_from=datetime.date(2026, 11, 15)
        )

        # Check rules
        rules = list(self.emp1.rest_day_rules.all().order_by('effective_from'))
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0].effective_from, datetime.date(2026, 11, 1))
        self.assertEqual(rules[0].effective_to, datetime.date(2026, 11, 14))
        self.assertEqual(rules[0].rest_weekday, 6)

        self.assertEqual(rules[1].effective_from, datetime.date(2026, 11, 15))
        self.assertIsNone(rules[1].effective_to)
        self.assertEqual(rules[1].rest_weekday, 4)

        # Now approve payroll for November 2026
        Payroll.objects.create(
            employee=self.emp1,
            month=datetime.date(2026, 11, 1),
            basic_salary=Decimal('10000.00'),
            status='APPROVED'
        )

        # Attempting to change rest day inside November 2026 must be rejected with exact error message
        with self.assertRaises(ValidationError) as cm:
            update_employee_rest_day_rule(
                self.emp1,
                rest_weekday=0,
                effective_from=datetime.date(2026, 11, 20)
            )
        self.assertIn("Payroll for Nov 2026 is already approved; changes must start from 01/12/2026.", str(cm.exception))

    def test_4_roster_employee_handling(self):
        """
        4. Roster employee: RosterDay REST -> REST_DAY; WORK on Sunday -> working day.
        """
        update_employee_rest_day_rule(
            self.emp1,
            uses_roster=True,
            effective_from=datetime.date(2026, 11, 1)
        )

        # 04/11/2026 (Wednesday) is set to REST
        RosterDay.objects.create(
            employee=self.emp1,
            date=datetime.date(2026, 11, 4),
            day_type='REST'
        )
        # 01/11/2026 (Sunday) is set to WORK
        RosterDay.objects.create(
            employee=self.emp1,
            date=datetime.date(2026, 11, 1),
            day_type='WORK'
        )

        with override_settings(HR_ATTENDANCE_ENGINE_V2=True):
            self.assertTrue(is_rest_day(self.emp1, datetime.date(2026, 11, 4)))
            self.assertFalse(is_rest_day(self.emp1, datetime.date(2026, 11, 1)))

            rows = build_attendance_register(datetime.date(2026, 11, 1), datetime.date(2026, 11, 30))
            nov4 = next(r for r in rows if r['employee'].id == self.emp1.id and r['date'] == datetime.date(2026, 11, 4))
            self.assertEqual(nov4['status_code'], 'REST_DAY')

            nov1 = next(r for r in rows if r['employee'].id == self.emp1.id and r['date'] == datetime.date(2026, 11, 1))
            self.assertEqual(nov1['status_code'], 'MISSING_PUNCH')

    def test_5_branch_specific_and_inactive_holidays(self):
        """
        5. Holiday branch-specific vs all branches; inactive ignored.
        """
        # All branches holiday
        h_all = Holiday.objects.create(
            date=datetime.date(2026, 11, 18),
            description="National Day All",
            branch=None,
            is_active=True
        )
        # Branch A only holiday
        h_a = Holiday.objects.create(
            date=datetime.date(2026, 11, 19),
            description="Dubai Local Holiday",
            branch=self.branch_a,
            is_active=True
        )
        # Inactive holiday
        h_inact = Holiday.objects.create(
            date=datetime.date(2026, 11, 20),
            description="Cancelled Holiday",
            branch=None,
            is_active=False
        )

        with override_settings(HR_ATTENDANCE_ENGINE_V2=True):
            h_map = public_holiday_map([self.emp1, self.emp2], datetime.date(2026, 11, 1), datetime.date(2026, 11, 30))
            
            # 18th is holiday for both
            self.assertTrue(h_map.get((self.emp1.id, datetime.date(2026, 11, 18))))
            self.assertTrue(h_map.get((self.emp2.id, datetime.date(2026, 11, 18))))

            # 19th is holiday ONLY for emp1 (Branch A), not emp2 (Branch B)
            self.assertTrue(h_map.get((self.emp1.id, datetime.date(2026, 11, 19))))
            self.assertFalse(h_map.get((self.emp2.id, datetime.date(2026, 11, 19))))

            # 20th is inactive -> neither
            self.assertFalse(h_map.get((self.emp1.id, datetime.date(2026, 11, 20))))
            self.assertFalse(h_map.get((self.emp2.id, datetime.date(2026, 11, 20))))

    def test_6_incomplete_punch_retains_hours(self):
        """
        6. Incomplete punch (no logout) and auto_punched_out -> MISSING_PUNCH INCOMPLETE, hours kept.
        """
        tz = timezone.get_current_timezone()
        login_dt = timezone.make_aware(datetime.datetime(2026, 10, 12, 8, 0, 0), tz)

        # Login without logout
        att = Attendance.objects.create(
            employee=self.emp1,
            login_time=login_dt,
            log_out_time=None,
            total_hours_of_work="0.00"
        )

        with override_settings(HR_ATTENDANCE_ENGINE_V2=True):
            rows = build_attendance_register(datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))
            oct12 = next(r for r in rows if r['employee'].id == self.emp1.id and r['date'] == datetime.date(2026, 10, 12))
            self.assertEqual(oct12['status_code'], 'MISSING_PUNCH')
            self.assertEqual(oct12['missing_punch_type'], 'INCOMPLETE')

        # Auto punched out session with hours
        logout_dt = timezone.make_aware(datetime.datetime(2026, 10, 13, 17, 0, 0), tz)
        att2 = Attendance.objects.create(
            employee=self.emp1,
            login_time=timezone.make_aware(datetime.datetime(2026, 10, 13, 8, 0, 0), tz),
            log_out_time=logout_dt,
            auto_punched_out=True,
            total_hours_of_work="9.00"
        )

        with override_settings(HR_ATTENDANCE_ENGINE_V2=True):
            rows = build_attendance_register(datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))
            oct13 = next(r for r in rows if r['employee'].id == self.emp1.id and r['date'] == datetime.date(2026, 10, 13))
            self.assertEqual(oct13['status_code'], 'MISSING_PUNCH')
            self.assertEqual(oct13['missing_punch_type'], 'INCOMPLETE')
            self.assertEqual(oct13['total_hours'], Decimal('9.00'))

    def test_7_no_punch_and_override_absent(self):
        """
        7. No punch on working day -> MISSING_PUNCH; override ABSENT -> ABSENT and deducted.
        """
        test_day = datetime.date(2026, 10, 14) # Wednesday

        with override_settings(HR_ATTENDANCE_ENGINE_V2=True):
            # No record -> MISSING_PUNCH
            rows = build_attendance_register(datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))
            row = next(r for r in rows if r['employee'].id == self.emp1.id and r['date'] == test_day)
            self.assertEqual(row['status_code'], 'MISSING_PUNCH')
            self.assertEqual(row['missing_punch_type'], 'NO_PUNCH')

            # Check metrics: missing punch is NOT deducted
            all_metrics = get_month_attendance_metrics(datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))
            metrics = all_metrics[self.emp1.id]
            self.assertIn(test_day, metrics['missing_punch_dates'])
            self.assertEqual(metrics['unpaid_days'], 0)

            # Manual override to ABSENT
            DailyAttendanceStatus.objects.create(
                employee=self.emp1,
                date=test_day,
                status='ABSENT'
            )

            rows_after = build_attendance_register(datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))
            row_after = next(r for r in rows_after if r['employee'].id == self.emp1.id and r['date'] == test_day)
            self.assertEqual(row_after['status_code'], 'ABSENT')

            metrics_after = get_month_attendance_metrics(datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))[self.emp1.id]
            self.assertNotIn(test_day, metrics_after['missing_punch_dates'])
            self.assertEqual(metrics_after['unpaid_days'], 1)

    def test_8_payroll_missing_punch_blocks_approval(self):
        """
        8. Payroll: MISSING_PUNCH not deducted; approve-all blocked until resolved; approved rows never recalculated.
        """
        # User client login as superuser
        client = Client()
        admin_user = User.objects.create_superuser('admin', 'admin@amms.ae', 'adminpass')
        client.force_login(admin_user)

        with override_settings(HR_ATTENDANCE_ENGINE_V2=True):
            # Generate draft payroll for October 2026
            generate_payroll(datetime.date(2026, 10, 1))
            p = Payroll.objects.get(employee=self.emp1, month=datetime.date(2026, 10, 1))
            self.assertEqual(p.status, 'DRAFT')

            # Attempting approve-all must be blocked due to missing punch days in October
            url_all = reverse('fin_payroll_approve_all')
            res = client.post(url_all, {'month': '2026-10'}, follow=True)
            p.refresh_from_db()
            self.assertEqual(p.status, 'DRAFT')
            self.assertContains(res, "Cannot approve payroll")
            p.refresh_from_db()
            self.assertEqual(p.status, 'DRAFT')
            self.assertContains(res, "Cannot approve payroll")

            # Single approve should also be blocked
            url_single = reverse('payroll_approve', args=[p.id])
            res_single = client.post(url_single, follow=True)
            p.refresh_from_db()
            self.assertEqual(p.status, 'DRAFT')

            # Approved row is never recalculated
            p.status = 'APPROVED'
            p.basic_salary = Decimal('9999.00')
            p.save()
            generate_payroll(datetime.date(2026, 10, 1))
            p.refresh_from_db()
            self.assertEqual(p.status, 'APPROVED')
            self.assertEqual(p.basic_salary, Decimal('9999.00'))

    def test_9_overtime_multipliers(self):
        """
        9. OT: Friday rest day work -> 1.5x, Sunday -> 1.25x, Holiday -> 2.0x; V2 off -> Sunday 1.5x.
        """
        friday = datetime.date(2026, 11, 6)
        sunday = datetime.date(2026, 11, 8)

        # When V2 is ON and Friday is rest day (rest_flag=True), Sunday is working day (rest_flag=False):
        self.assertEqual(ot_multiplier(friday, is_public_holiday=False, is_rest_day=True), Decimal('1.5'))
        self.assertEqual(ot_multiplier(sunday, is_public_holiday=False, is_rest_day=False), Decimal('1.25'))
        self.assertEqual(ot_multiplier(friday, is_public_holiday=True, is_rest_day=True), Decimal('2.0'))

        self.assertEqual(ot_type_for(friday, is_public_holiday=False, is_rest_day=True), 'SUNDAY') # Stored as SUNDAY, displayed as Rest Day
        self.assertEqual(ot_type_for(sunday, is_public_holiday=False, is_rest_day=False), 'WEEKDAY')
        self.assertEqual(ot_type_for(friday, is_public_holiday=True, is_rest_day=True), 'HOLIDAY')

        # When V2 is OFF (rest_flag=None):
        self.assertEqual(ot_multiplier(friday, is_public_holiday=False, is_rest_day=None), Decimal('1.25'))
        self.assertEqual(ot_multiplier(sunday, is_public_holiday=False, is_rest_day=None), Decimal('1.5'))

    def test_10_basic_salary_ot_and_absence_deduction(self):
        """
        10. Basic salary: total 10k / basic 6k with HR_BASIC_SALARY_FROM=2026-11-01;
            November OT rate = 6k/208, deduction = 6k/26; no structure -> salary fallback.
        """
        # Create salary structure: basic=6000, total=10000
        SalaryStructure.objects.create(
            employee=self.emp1,
            basic=Decimal('6000.00'),
            housing_allowance=Decimal('2000.00'),
            transport_allowance=Decimal('1000.00'),
            other_allowances=Decimal('1000.00'),
            total=Decimal('10000.00'),
            effective_from=datetime.date(2026, 11, 1)
        )

        with override_settings(HR_ATTENDANCE_ENGINE_V2=True, HR_BASIC_SALARY_FROM=datetime.date(2026, 11, 1)):
            # November: uses basic salary = 6000
            nov_base = pay_base_salary(self.emp1, datetime.date(2026, 11, 15))
            self.assertEqual(nov_base, Decimal('6000.00'))

            # October (before HR_BASIC_SALARY_FROM): uses employee.salary = 10000
            oct_base = pay_base_salary(self.emp1, datetime.date(2026, 10, 15))
            self.assertEqual(oct_base, Decimal('10000.00'))

            # Employee 2 has no SalaryStructure: falls back to employee.salary = 8000
            emp2_base = pay_base_salary(self.emp2, datetime.date(2026, 11, 15))
            self.assertEqual(emp2_base, Decimal('8000.00'))

    def test_11_permissions_shifts_and_holidays(self):
        """
        11. Permissions: Shifts & Holidays pages return 403 for manager/employee/accountant, 200 for HR and Superuser.
        """
        client = Client()

        # Regular employee
        client.force_login(self.user1)
        urls = [
            reverse('hr_duty_schedules'),
            reverse('hr_weekly_rest_days'),
            reverse('hr_roster'),
            reverse('hr_public_holidays'),
        ]
        for u in urls:
            res = client.get(u)
            self.assertEqual(res.status_code, 403, f"Expected 403 for employee on {u}")

        # Accountant user
        acct_user = User.objects.create_user('accountant1', 'acct@amms.ae', 'pass')
        Employee.objects.create(user=acct_user, is_accountant=True)
        client.force_login(acct_user)
        for u in urls:
            res = client.get(u)
            self.assertEqual(res.status_code, 403, f"Expected 403 for accountant on {u}")

        # HR user
        hr_user = User.objects.create_user('hr1', 'hr@amms.ae', 'pass')
        Employee.objects.create(user=hr_user, is_hr=True)
        client.force_login(hr_user)
        for u in urls:
            res = client.get(u)
            self.assertEqual(res.status_code, 200, f"Expected 200 for HR on {u}")

        # Superuser
        su = User.objects.create_superuser('su1', 'su@amms.ae', 'pass')
        client.force_login(su)
        for u in urls:
            res = client.get(u)
            self.assertEqual(res.status_code, 200, f"Expected 200 for Superuser on {u}")

    def test_12_compare_attendance_engine_command(self):
        """
        12. compare_attendance_engine: read-only, row counts unchanged, prints both columns.
        """
        out = StringIO()
        # Count rows before
        att_count = Attendance.objects.count()
        hol_count = Holiday.objects.count()
        rule_count = RestDayRule.objects.count()

        call_command('compare_attendance_engine', month='2026-10', stdout=out)
        output = out.getvalue()

        self.assertIn("ATTENDANCE ENGINE COMPARISON", output)
        self.assertIn("Strictly Read-Only Analysis", output)
        self.assertIn("AMMS-0001", output)

        # Assert no database modification occurred
        self.assertEqual(Attendance.objects.count(), att_count)
        self.assertEqual(Holiday.objects.count(), hol_count)
        self.assertEqual(RestDayRule.objects.count(), rule_count)

    def test_13_mark_paid_only_approves_approved_rows_and_warns_drafts(self):
        """
        Fix 1: fin_payroll_mark_paid updates APPROVED -> PAID only.
        DRAFT rows are skipped with message 'N draft rows must be approved first'.
        """
        from finance.models import Payroll
        acct_user = User.objects.create_user('acct_mp', 'acct_mp@amms.ae', 'pass')
        Employee.objects.create(user=acct_user, is_accountant=True)
        self.client.force_login(acct_user)

        m_date = datetime.date(2026, 7, 1)
        p_appr = Payroll.objects.create(
            employee=self.emp1,
            month=m_date,
            basic_salary=Decimal('5000.00'),
            status='APPROVED'
        )
        p_draft = Payroll.objects.create(
            employee=self.emp2,
            month=m_date,
            basic_salary=Decimal('4000.00'),
            status='DRAFT'
        )

        res = self.client.post(reverse('fin_payroll_mark_paid'), {'month': '2026-07'}, follow=True)
        self.assertEqual(res.status_code, 200)

        p_appr.refresh_from_db()
        p_draft.refresh_from_db()
        self.assertEqual(p_appr.status, 'PAID')
        self.assertEqual(p_appr.paid_on, timezone.localdate())
        self.assertEqual(p_draft.status, 'DRAFT')

        messages = list(res.context['messages'])
        msg_texts = [str(m) for m in messages]
        self.assertTrue(any('1 draft rows must be approved first' in t for t in msg_texts))

    def test_14_hr_basic_salary_from_normal_cost_vs_ot_cost(self):
        """
        Fix 2: In recalculate_day_ot, normal_cost keeps using employee.salary (total) / 208;
        only ot_cost uses pay_base_salary(...).
        total 10,400, basic 6,240, basic-from active:
        12 h vessel weekday -> ot_cost 75.00, normal_cost 500.00.
        """
        from service_logs.models import EmployeeServiceLog
        from service_logs.ot import recalculate_day_ot

        user = User.objects.create_user('emp_split_sal', 'split@amms.ae', 'pass')
        emp = Employee.objects.create(
            user=user,
            employee_code='AMMS-0099',
            salary=Decimal('10400.00'),
            date_of_join=datetime.date(2025, 1, 1),
            branch=self.branch_a,
            department=self.dept,
            is_employee=True
        )
        SalaryStructure.objects.create(
            employee=emp,
            basic=Decimal('6240.00'),
            housing_allowance=Decimal('2080.00'),
            transport_allowance=Decimal('2080.00'),
            other_allowances=Decimal('0.00'),
            total=Decimal('10400.00'),
            effective_from=datetime.date(2025, 1, 1),
            created_by=self.admin_user
        )

        # Wednesday weekday
        test_day = datetime.date(2026, 6, 10)
        log = EmployeeServiceLog.objects.create(
            employee=emp,
            date=test_day,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(20, 0),  # 12 hours
            service_type='VESSEL'
        )

        with override_settings(HR_ATTENDANCE_ENGINE_V2=True, HR_BASIC_SALARY_FROM=datetime.date(2025, 1, 1)):
            recalculate_day_ot(emp.id, test_day)
            log.refresh_from_db()
            self.assertEqual(log.total_hours, Decimal('12.00'))
            self.assertEqual(log.ot_hours, Decimal('2.00'))
            self.assertEqual(log.normal_cost, Decimal('500.00'))
            self.assertEqual(log.ot_cost, Decimal('75.00'))

    def test_15_payroll_lock_for_open_ended_rules(self):
        """
        Fix 3: Open-ended rules reject if month >= effective_from's month has APPROVED/PAID payroll.
        Nov approved, rest day from 20/10 -> rejected, from 01/12 -> allowed.
        """
        from finance.models import Payroll
        Payroll.objects.create(
            employee=self.emp1,
            month=datetime.date(2026, 11, 1),
            basic_salary=Decimal('10000.00'),
            status='APPROVED'
        )

        # 20/10/2026 is before Nov 2026 -> rejected with error showing Nov 2026 and 01/12/2026
        with self.assertRaises(ValidationError) as ctx:
            update_employee_rest_day_rule(
                self.emp1,
                rest_weekday=4,
                effective_from=datetime.date(2026, 10, 20),
                user=self.admin_user
            )
        self.assertIn("Payroll for Nov 2026 is already approved", str(ctx.exception))
        self.assertIn("changes must start from 01/12/2026", str(ctx.exception))

        sched = DutySchedule.objects.create(name='Standard 8-5', start_time=datetime.time(8, 0), end_time=datetime.time(17, 0), break_minutes=60)
        # Schedule update from 20/10/2026 also rejected
        with self.assertRaises(ValidationError) as ctx:
            update_employee_schedule(
                self.emp1,
                duty_schedule=sched,
                effective_from=datetime.date(2026, 10, 20),
                user=self.admin_user
            )
        self.assertIn("Payroll for Nov 2026 is already approved", str(ctx.exception))

        # 01/12/2026 is after Nov 2026 -> allowed
        rule = update_employee_rest_day_rule(
            self.emp1,
            rest_weekday=4,
            effective_from=datetime.date(2026, 12, 1),
            user=self.admin_user
        )
        self.assertEqual(rule.effective_from, datetime.date(2026, 12, 1))

        # RosterDay on 20/10/2026 is single-date: no approved payroll for Oct 2026 -> allowed
        check_payroll_lock_for_employee(self.emp1, datetime.date(2026, 10, 20), is_open_ended=False)

    def test_16_rest_day_in_bradford_and_utilization(self):
        """
        Fix 4: REST_DAY treated like SUNDAY in Bradford spell and utilization is_working.
        """
        from Admin.attendance_analytics import build_attendance_analytics
        from decimal import Decimal

        # Test Bradford spell: ABSENT on Thursday, REST_DAY on Friday, ABSENT on Saturday
        # REST_DAY should not break the spell
        start_date = datetime.date(2026, 5, 1)
        end_date = datetime.date(2026, 5, 31)

        d_thu = datetime.date(2026, 5, 7)
        d_fri = datetime.date(2026, 5, 8)
        d_sat = datetime.date(2026, 5, 9)

        rows = [
            {'employee': self.emp1, 'date': d_thu, 'status_code': 'ABSENT', 'total_hours': Decimal('0'), 'ot_hours': Decimal('0'), 'is_off_day': False},
            {'employee': self.emp1, 'date': d_fri, 'status_code': 'REST_DAY', 'total_hours': Decimal('0'), 'ot_hours': Decimal('0'), 'is_off_day': True},
            {'employee': self.emp1, 'date': d_sat, 'status_code': 'ABSENT', 'total_hours': Decimal('0'), 'ot_hours': Decimal('0'), 'is_off_day': False},
        ]

        data = build_attendance_analytics(rows, [], [self.emp1.id], start_date, end_date)
        # 1 spell of 2 absent days -> Bradford = (1^2) * 2 = 2
        self.assertEqual(len(data['watchlist']), 1)
        self.assertEqual(data['watchlist'][0]['spells'], 1)
        self.assertEqual(data['watchlist'][0]['longest_spell'], 2)
        self.assertEqual(data['watchlist'][0]['bradford_factor'], 2)

    def test_17_calculate_office_ot_cost_passes_employee_for_branch_holiday(self):
        """
        Fix 5: calculate_office_ot_cost passes employee so branch/active holiday filter applies.
        """
        from service_logs.ot import calculate_office_ot_cost

        # Branch B holiday on 2026-06-15
        h_date = datetime.date(2026, 6, 15)
        Holiday.objects.create(
            description="Abu Dhabi Day",
            date=h_date,
            branch=self.branch_b,
            is_active=True
        )

        rows = [{
            'date': h_date,
            'office_ot_hours': Decimal('2.00'),
            'employee': self.emp1,  # in Branch A
        }]

        self.emp1.salary = Decimal('10400.00')
        self.emp1.save()
        self.emp2.salary = Decimal('10400.00')
        self.emp2.save()

        with override_settings(HR_ATTENDANCE_ENGINE_V2=True):
            # For emp1 (Branch A), this is NOT a holiday -> regular 1.25x
            cost_a = calculate_office_ot_cost(Decimal('10400.00'), rows, [], employee=self.emp1)
            # rate = 10400 / 208 = 50.00. 2h * 50 * 1.25 = 125.00
            self.assertEqual(cost_a, Decimal('125.00'))

            # For emp2 (Branch B), this IS a holiday -> 2.0x
            rows_b = [{
                'date': h_date,
                'office_ot_hours': Decimal('2.00'),
                'employee': self.emp2,
            }]
            cost_b = calculate_office_ot_cost(Decimal('10400.00'), rows_b, [], employee=self.emp2)
            # 2h * 50 * 2.0 = 200.00
            self.assertEqual(cost_b, Decimal('200.00'))

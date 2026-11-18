"""
Tests for Automatic Missing Punch notifications, reminders, resolution,
and the one-click correction link:
1. NO_PUNCH day -> employee gets MISSING_PUNCH notification with ?correct= link; approver gets combined notification.
2. INCOMPLETE day -> notification message contains punch-in time.
3. Idempotency -> running twice on the same day sends nothing new.
4. Reminders -> after --reminder-days sends reminder; stops after --max-reminders; approver notified on last reminder.
5. Resolution -> approved correction or leave resolves alert without sending notification.
6. Skips -> rest day, holiday, leave, timesheet, pending correction, inactive user, before date_of_join.
7. Employee without reporting manager -> HR users get combined notification.
8. --dry-run -> no records written.
9. HR_ATTENDANCE_ENGINE_V2 off -> command does nothing.
10. GET Attendance Status page with ?correct=<date> -> 200 and calls openCorrectionModalForDate or shows info message.
11. Query count stays flat for 5 vs 20 employees (no N+1).
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone

from Admin.models import (
    Employee, Department, Branch, Attendance, AttendanceCorrection,
    DutySchedule, RestDayRule, EmployeeSchedule, Holiday, Leave, LeaveType,
    MissingPunchAlert, Notification
)


@override_settings(HR_ATTENDANCE_ENGINE_V2=True)
class MissingPunchAlertsCommandTests(TestCase):
    def setUp(self):
        self.tz = timezone.get_current_timezone()
        self.branch = Branch.objects.create(name="Dubai Main", sort_order=1)
        self.dept = Department.objects.create(name="Technical", sort_order=1)

        self.schedule = DutySchedule.objects.create(
            name="Standard Office",
            start_time=datetime.time(8, 0),
            end_time=datetime.time(17, 0),
            break_minutes=60,
            sort_order=1
        )

        # 1. Superuser
        self.admin_user = User.objects.create_superuser('admin_usr', 'admin@example.com', 'pass123')

        # 2. HR User
        self.hr_user = User.objects.create_user('hr_usr', 'hr@example.com', 'pass123', first_name='Fatima', last_name='HR')
        self.hr_emp = Employee.objects.create(
            user=self.hr_user,
            employee_code='AMMS-0001',
            date_of_join=datetime.date(2023, 1, 1),
            branch=self.branch,
            department=self.dept,
            is_hr=True
        )

        # 3. Manager
        self.mgr_user = User.objects.create_user('mgr_usr', 'mgr@example.com', 'pass123', first_name='Tariq', last_name='Manager')
        self.mgr_emp = Employee.objects.create(
            user=self.mgr_user,
            employee_code='AMMS-0002',
            date_of_join=datetime.date(2023, 1, 1),
            branch=self.branch,
            department=self.dept,
            is_manager=True
        )

        # 4. Staff Employee (reports to Tariq)
        self.staff_user = User.objects.create_user('staff_usr', 'staff@example.com', 'pass123', first_name='Salem', last_name='Staff')
        self.staff_emp = Employee.objects.create(
            user=self.staff_user,
            employee_code='AMMS-0003',
            date_of_join=datetime.date(2023, 1, 1),
            branch=self.branch,
            department=self.dept,
            reporting_manager=self.mgr_emp,
            is_employee=True
        )

        # Schedule assignments & rest days (Sunday=6 rest day)
        for emp in (self.hr_emp, self.mgr_emp, self.staff_emp):
            RestDayRule.objects.create(employee=emp, rest_weekday=6, effective_from=datetime.date(2023, 1, 1))
            EmployeeSchedule.objects.create(employee=emp, duty_schedule=self.schedule, effective_from=datetime.date(2023, 1, 1))

        # Target Monday: 2026-11-16
        self.target_date = datetime.date(2026, 11, 16)
        self.target_date_str = '2026-11-16'

        # Record normal attendance for HR and Manager on the target date so only staff_emp has a missing punch
        for emp in (self.hr_emp, self.mgr_emp):
            Attendance.objects.create(
                employee=emp,
                login_time=timezone.make_aware(datetime.datetime.combine(self.target_date, datetime.time(8, 0)), self.tz),
                log_out_time=timezone.make_aware(datetime.datetime.combine(self.target_date, datetime.time(17, 0)), self.tz),
                status='APPROVED',
                location='BDO'
            )

        self.client = Client()

    def test_01_no_punch_first_alert(self):
        """
        NO_PUNCH day: Employee gets MISSING_PUNCH notification with ?correct= link;
        Manager gets combined notification naming employee; MissingPunchAlert is created.
        """
        call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1)

        # 1. Alert created
        alert = MissingPunchAlert.objects.filter(employee=self.staff_emp, date=self.target_date).first()
        self.assertIsNotNone(alert)
        self.assertEqual(alert.punch_type, 'NO_PUNCH')
        self.assertEqual(alert.reminder_count, 0)
        self.assertIsNone(alert.resolved_at)

        # 2. Employee notification
        emp_notif = Notification.objects.filter(recipient=self.staff_user, type='MISSING_PUNCH').first()
        self.assertIsNotNone(emp_notif)
        self.assertIn("No attendance recorded for Mon 16/11/2026", emp_notif.message)
        self.assertIn(f"?correct={self.target_date_str}", emp_notif.link)

        # 3. Manager notification
        mgr_notif = Notification.objects.filter(recipient=self.mgr_user, type='MISSING_PUNCH').first()
        self.assertIsNotNone(mgr_notif)
        self.assertIn("Salem Staff", mgr_notif.message)
        self.assertIn("16/11/2026", mgr_notif.message)
        self.assertIn("MISSING_PUNCH", mgr_notif.link)

    def test_02_incomplete_punch_alert(self):
        """
        INCOMPLETE day (punch in without punch out): message includes punch-in time.
        """
        login_dt = datetime.datetime.combine(self.target_date, datetime.time(8, 2))
        login_aware = timezone.make_aware(login_dt, self.tz)
        Attendance.objects.create(
            employee=self.staff_emp,
            login_time=login_aware,
            status='APPROVED',
            location='BDO'
        )

        call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1)

        alert = MissingPunchAlert.objects.filter(employee=self.staff_emp, date=self.target_date).first()
        self.assertIsNotNone(alert)
        self.assertEqual(alert.punch_type, 'INCOMPLETE')

        emp_notif = Notification.objects.filter(recipient=self.staff_user, type='MISSING_PUNCH').first()
        self.assertIsNotNone(emp_notif)
        self.assertIn("You punched in at 08:02 on 16/11/2026 but did not punch out", emp_notif.message)

    def test_03_idempotency_run_twice(self):
        """
        Running the command twice on the same day sends nothing new.
        """
        call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1)
        count_alerts_1 = MissingPunchAlert.objects.count()
        count_notifs_1 = Notification.objects.count()

        # Run again
        call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1)
        self.assertEqual(MissingPunchAlert.objects.count(), count_alerts_1)
        self.assertEqual(Notification.objects.count(), count_notifs_1)

    def test_04_reminders_and_last_reminder_approver_alert(self):
        """
        Reminders sent after --reminder-days; stops after --max-reminders;
        approver is notified on the last reminder.
        """
        # Run 1: First alert
        call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1, reminder_days=2, max_reminders=2)
        alert = MissingPunchAlert.objects.get(employee=self.staff_emp, date=self.target_date)
        self.assertEqual(alert.reminder_count, 0)
        Notification.objects.all().delete()

        # Move last_alert_at back 3 days
        alert.last_alert_at = timezone.now() - datetime.timedelta(days=3)
        alert.save(update_fields=['last_alert_at'])

        # Run 2: First reminder (reminder_count becomes 1 < max_reminders 2, approver NOT notified)
        call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1, reminder_days=2, max_reminders=2)
        alert.refresh_from_db()
        self.assertEqual(alert.reminder_count, 1)

        emp_rem1 = Notification.objects.filter(recipient=self.staff_user, type='MISSING_PUNCH').first()
        self.assertIsNotNone(emp_rem1)
        self.assertIn("Reminder: missing punch on 16/11/2026 is still not corrected", emp_rem1.message)
        # Approver should NOT be notified on first reminder
        self.assertFalse(Notification.objects.filter(recipient=self.mgr_user).exists())

        Notification.objects.all().delete()

        # Move last_alert_at back 3 days again
        alert.last_alert_at = timezone.now() - datetime.timedelta(days=3)
        alert.save(update_fields=['last_alert_at'])

        # Run 3: Second reminder (reminder_count becomes 2 == max_reminders 2 -> LAST reminder, approver notified!)
        call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1, reminder_days=2, max_reminders=2)
        alert.refresh_from_db()
        self.assertEqual(alert.reminder_count, 2)

        self.assertTrue(Notification.objects.filter(recipient=self.staff_user, type='MISSING_PUNCH').exists())
        self.assertTrue(Notification.objects.filter(recipient=self.mgr_user, type='MISSING_PUNCH').exists())

        Notification.objects.all().delete()

        # Move last_alert_at back 3 days again
        alert.last_alert_at = timezone.now() - datetime.timedelta(days=3)
        alert.save(update_fields=['last_alert_at'])

        # Run 4: Max reminders exceeded -> nothing sent
        call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1, reminder_days=2, max_reminders=2)
        self.assertEqual(Notification.objects.count(), 0)

    def test_05_resolution(self):
        """
        When missing punch day is corrected or excused, next run sets resolved_at and sends nothing.
        """
        # Create alert first
        alert = MissingPunchAlert.objects.create(
            employee=self.staff_emp,
            date=self.target_date,
            punch_type='NO_PUNCH',
            first_alert_at=timezone.now(),
            last_alert_at=timezone.now(),
            reminder_count=0,
        )
        Notification.objects.all().delete()

        # Resolve the missing punch by recording an approved attendance
        login_t = timezone.make_aware(datetime.datetime.combine(self.target_date, datetime.time(8, 0)), self.tz)
        logout_t = timezone.make_aware(datetime.datetime.combine(self.target_date, datetime.time(17, 0)), self.tz)
        Attendance.objects.create(
            employee=self.staff_emp,
            login_time=login_t,
            log_out_time=logout_t,
            status='APPROVED',
            location='BDO'
        )

        call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1)

        alert.refresh_from_db()
        self.assertIsNotNone(alert.resolved_at)
        self.assertEqual(Notification.objects.count(), 0)

    def test_06_skips(self):
        """
        Skips rest day, holiday, approved leave, timesheet, pending correction, inactive user, before date_of_join.
        """
        # A. Rest day (Sunday 2026-11-15)
        call_command('send_missing_punch_alerts', date='2026-11-15', lookback=1)
        self.assertFalse(MissingPunchAlert.objects.filter(date=datetime.date(2026, 11, 15)).exists())

        # B. Public holiday
        Holiday.objects.create(date=self.target_date, description="National Holiday", branch=self.branch)
        call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1)
        self.assertFalse(MissingPunchAlert.objects.filter(employee=self.staff_emp, date=self.target_date).exists())
        Holiday.objects.filter(date=self.target_date).delete()

        # C. Approved leave
        al_type = LeaveType.objects.create(code='AL_TEST', name='Annual Test', is_active=True, sort_order=1)
        Leave.objects.create(
            user=self.staff_user,
            employee=self.staff_emp,
            leave_type='Annual Leave',
            leave_type_ref=al_type,
            from_date=self.target_date,
            to_date=self.target_date,
            days=Decimal('1.00'),
            approval_status='APPROVED'
        )
        call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1)
        self.assertFalse(MissingPunchAlert.objects.filter(employee=self.staff_emp, date=self.target_date).exists())
        Leave.objects.all().delete()

        # D. Pending correction
        AttendanceCorrection.objects.create(
            employee=self.staff_emp,
            date=self.target_date,
            correction_type='NO_PUNCH',
            requested_login=timezone.now(),
            requested_logout=timezone.now(),
            status='PENDING',
            submitted_by=self.staff_user
        )
        call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1)
        self.assertFalse(MissingPunchAlert.objects.filter(employee=self.staff_emp, date=self.target_date).exists())
        AttendanceCorrection.objects.all().delete()

        # E. Inactive employee
        self.staff_user.is_active = False
        self.staff_user.save()
        call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1)
        self.assertFalse(MissingPunchAlert.objects.filter(employee=self.staff_emp, date=self.target_date).exists())
        self.staff_user.is_active = True
        self.staff_user.save()

        # F. Date before date of join
        self.staff_emp.date_of_join = datetime.date(2026, 12, 1)
        self.staff_emp.save()
        call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1)
        self.assertFalse(MissingPunchAlert.objects.filter(employee=self.staff_emp, date=self.target_date).exists())

    def test_07_employee_without_reporting_manager_notifies_hr(self):
        """
        Employee without reporting manager -> HR users receive the combined notification.
        """
        self.staff_emp.reporting_manager = None
        self.staff_emp.save()

        call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1)

        hr_notif = Notification.objects.filter(recipient=self.hr_user, type='MISSING_PUNCH').first()
        self.assertIsNotNone(hr_notif)
        self.assertIn("Salem Staff", hr_notif.message)
        self.assertIn("/hr/employee/attendance/", hr_notif.link)

    def test_08_dry_run_writes_nothing(self):
        """
        --dry-run writes no Notification and no MissingPunchAlert rows.
        """
        call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1, dry_run=True)
        self.assertEqual(MissingPunchAlert.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)

    @override_settings(HR_ATTENDANCE_ENGINE_V2=False)
    def test_09_engine_v2_off_does_nothing(self):
        """
        When HR_ATTENDANCE_ENGINE_V2 is False, command does nothing.
        """
        call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1)
        self.assertEqual(MissingPunchAlert.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_10_attendance_status_page_one_click_link(self):
        """
        GET /employee/attendance-list/?correct=<date> opens modal for correctable day,
        or shows info message for non-correctable date.
        """
        self.client.force_login(self.staff_user)

        # 1. Correctable date (missing punch day)
        res = self.client.get(f"/employee/attendance-list/?correct={self.target_date_str}")
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn("openCorrectionModalForDate", content)
        self.assertIn(self.target_date_str, content)

        # 2. Non-correctable date (future date)
        future_date = (timezone.localdate() + datetime.timedelta(days=10)).strftime('%Y-%m-%d')
        res_future = self.client.get(f"/employee/attendance-list/?correct={future_date}")
        self.assertEqual(res_future.status_code, 200)
        content_future = res_future.content.decode('utf-8')
        self.assertIn("cannot be corrected", content_future)

    def test_11_query_count_stays_flat_no_n_plus_one(self):
        """
        Query count of the command stays flat for 5 vs 20 employees.
        """
        total_spawned = [0]

        def spawn_employees(n):
            emps = []
            for _ in range(n):
                idx = total_spawned[0]
                total_spawned[0] += 1
                u = User.objects.create_user(f'batch_u_{idx}', f'batch_{idx}@test.com', 'pass123', first_name=f'Batch{idx}', last_name='Emp')
                e = Employee.objects.create(
                    user=u,
                    employee_code=f'AMMS-B{idx:03d}',
                    date_of_join=datetime.date(2023, 1, 1),
                    branch=self.branch,
                    department=self.dept,
                    reporting_manager=self.mgr_emp,
                    is_employee=True
                )
                RestDayRule.objects.create(employee=e, rest_weekday=6, effective_from=datetime.date(2023, 1, 1))
                EmployeeSchedule.objects.create(employee=e, duty_schedule=self.schedule, effective_from=datetime.date(2023, 1, 1))
                emps.append(e)
            return emps

        # Measure 5 employees
        spawn_employees(5)
        with CaptureQueriesContext(connection) as ctx_5:
            call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1)
        q_count_5 = len(ctx_5)

        # Clean up alerts and notifications for round 2
        MissingPunchAlert.objects.all().delete()
        Notification.objects.all().delete()

        # Add 15 more employees (total 20 batch employees)
        spawn_employees(15)
        with CaptureQueriesContext(connection) as ctx_20:
            call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1)
        q_count_20 = len(ctx_20)

        # Query count for 20 employees should not grow with N (stays flat)
        self.assertEqual(q_count_5, q_count_20, f"Query count changed: {q_count_5} queries for 5 emps vs {q_count_20} for 20 emps")

    def test_12_multi_day_first_run_sends_one_grouped_notification(self):
        """
        First run over a 3-day lookback (Thu-Sat, all missing): the employee gets ONE grouped
        notification listing every day (no flood), but one MissingPunchAlert per day.
        """
        call_command('send_missing_punch_alerts', date='2026-11-14', lookback=3)
        self.assertEqual(MissingPunchAlert.objects.filter(employee=self.staff_emp).count(), 3)
        notifs = Notification.objects.filter(recipient=self.staff_user, type='MISSING_PUNCH')
        self.assertEqual(notifs.count(), 1)
        msg = notifs.first().message
        self.assertIn("3 days", msg)
        for day in ("Thu 12/11", "Fri 13/11", "Sat 14/11"):
            self.assertIn(day, msg)
        self.assertIn("?correct=2026-11-12", notifs.first().link)

    def test_13_incomplete_without_known_time_does_not_invent_one(self):
        """INCOMPLETE with no usable punch-in time must not show a made-up time like 08:00."""
        from unittest import mock
        from Admin.management.commands import send_missing_punch_alerts as cmd_mod
        real = cmd_mod.build_attendance_register

        def fake(*a, **kw):
            res = real(*a, **kw)
            rows = res.get('rows', res) if isinstance(res, dict) else res
            for r in rows:
                if r.get('status_code') == 'MISSING_PUNCH':
                    r['missing_punch_type'] = 'INCOMPLETE'
                    r['start'] = None
                    r['login_time'] = None
            return res

        with mock.patch.object(cmd_mod, 'build_attendance_register', side_effect=fake):
            call_command('send_missing_punch_alerts', date=self.target_date_str, lookback=1)
        n = Notification.objects.filter(recipient=self.staff_user, type='MISSING_PUNCH').first()
        self.assertIsNotNone(n)
        self.assertIn("You punched in on 16/11/2026 but did not punch out", n.message)
        self.assertNotIn("08:00", n.message)

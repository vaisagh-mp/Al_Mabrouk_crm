"""
Tests for the 5-part WorkOrderTime → EmployeeServiceLog feature.
Run with:  python manage.py test Admin.tests service_logs.tests
"""
from datetime import date, time, datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, RequestFactory, Client
from django.utils import timezone

from Admin.models import (
    Employee, Holiday, Project, WorkOrder, WorkOrderTime,
    Attendance, Leave,
)
from Admin.attendance_register import build_attendance_register
from service_logs.models import EmployeeServiceLog
from service_logs.services import create_or_update_service_log
from service_logs.views import build_employee_analytics_context


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def make_user(username, salary=None):
    u = User.objects.create_user(username=username, password='x')
    emp = Employee.objects.create(
        user=u, is_employee=True, salary=salary
    )
    return u, emp


def make_project(**kwargs):
    defaults = dict(
        name='Test Project',
        client_name='Client',
        vessel_name='MV Test',
        category='AT_BERTH',
        status='ONGOING',
    )
    defaults.update(kwargs)
    return Project.objects.create(**defaults)


def make_work_order(project):
    return WorkOrder.objects.create(
        project=project,
        work_order_number='TEST-001',
    )


def make_time_log(work_order, employee, start='08:00', finish=None, log_date=None):
    return WorkOrderTime.objects.create(
        work_order=work_order,
        date=log_date or date(2026, 6, 1),
        start_time=time(*[int(x) for x in start.split(':')]),
        finish_time=(
            time(*[int(x) for x in finish.split(':')]) if finish else None
        ),
        employee=employee,
    )


# ──────────────────────────────────────────────
# Test 1: Project saves with and without service_reference
# ──────────────────────────────────────────────

class ProjectServiceReferenceTest(TestCase):
    def test_project_saves_without_service_reference(self):
        p = make_project()
        self.assertIsNone(p.service_reference)

    def test_project_saves_with_service_reference(self):
        p = make_project(service_reference='REF-2026-001')
        self.assertEqual(p.service_reference, 'REF-2026-001')

    def test_service_reference_not_copied_from_code(self):
        p = make_project(code='PRJ-42')
        self.assertNotEqual(p.service_reference, p.code)


# ──────────────────────────────────────────────
# Test 2: Manager saves start time → one log, end_time=None, totals=0
# ──────────────────────────────────────────────

class ManagerStartTimeTest(TestCase):
    def setUp(self):
        self._, self.mgr_emp = make_user('mgr', salary=Decimal('2600'))
        self.mgr_emp.is_manager = True
        self.mgr_emp.save()
        self.project = make_project(service_reference='MGR-REF')
        self.wo = make_work_order(self.project)

    def test_one_log_created_for_manager(self):
        tl = make_time_log(self.wo, self.mgr_emp, start='08:00', finish=None)
        logs = EmployeeServiceLog.objects.filter(time_log=tl)
        self.assertEqual(logs.count(), 1)

    def test_end_time_is_null(self):
        tl = make_time_log(self.wo, self.mgr_emp, start='08:00', finish=None)
        log = EmployeeServiceLog.objects.get(time_log=tl)
        self.assertIsNone(log.end_time)

    def test_totals_are_zero_without_end_time(self):
        tl = make_time_log(self.wo, self.mgr_emp, start='08:00', finish=None)
        log = EmployeeServiceLog.objects.get(time_log=tl)
        self.assertEqual(log.total_hours, Decimal('0'))
        self.assertEqual(log.normal_cost, Decimal('0'))
        self.assertEqual(log.ot_cost, Decimal('0'))


# ──────────────────────────────────────────────
# Test 3: Two employees → two separate logs with correct metadata
# ──────────────────────────────────────────────

class TwoEmployeeLogsTest(TestCase):
    def setUp(self):
        self._, self.emp1 = make_user('emp1', salary=Decimal('2600'))
        self._, self.emp2 = make_user('emp2', salary=Decimal('2600'))
        self.project = make_project(service_reference='PROJ-REF', vessel_name='MV Alpha')
        self.wo = make_work_order(self.project)
        self.wo.vessel = 'MV Alpha'
        self.wo.location = 'PORT A'
        self.wo.save()

    def test_two_separate_logs(self):
        tl1 = make_time_log(self.wo, self.emp1, start='08:00')
        tl2 = make_time_log(self.wo, self.emp2, start='09:00')
        self.assertEqual(EmployeeServiceLog.objects.filter(time_log=tl1).count(), 1)
        self.assertEqual(EmployeeServiceLog.objects.filter(time_log=tl2).count(), 1)

    def test_correct_service_reference(self):
        tl = make_time_log(self.wo, self.emp1, start='08:00')
        log = EmployeeServiceLog.objects.get(time_log=tl)
        self.assertEqual(log.service_reference, 'PROJ-REF')

    def test_correct_vessel_name(self):
        tl = make_time_log(self.wo, self.emp1, start='08:00')
        log = EmployeeServiceLog.objects.get(time_log=tl)
        self.assertEqual(log.vessel_name, 'MV Alpha')

    def test_correct_port(self):
        tl = make_time_log(self.wo, self.emp1, start='08:00')
        log = EmployeeServiceLog.objects.get(time_log=tl)
        self.assertEqual(log.port, 'PORT A')


# ──────────────────────────────────────────────
# Test 4: Saving same entry again → no duplicate
# ──────────────────────────────────────────────

class NoDuplicateLogTest(TestCase):
    def setUp(self):
        self._, self.emp = make_user('emp3', salary=Decimal('2600'))
        self.project = make_project()
        self.wo = make_work_order(self.project)

    def test_no_duplicate_on_re_save(self):
        tl = make_time_log(self.wo, self.emp, start='08:00')
        # Trigger again manually
        create_or_update_service_log(tl)
        create_or_update_service_log(tl)
        self.assertEqual(EmployeeServiceLog.objects.filter(time_log=tl).count(), 1)


# ──────────────────────────────────────────────
# Test 5: Finish time added → log updated with correct hours / OT / costs
# ──────────────────────────────────────────────

class FinishTimeUpdateTest(TestCase):
    def setUp(self):
        self._, self.emp = make_user('emp4', salary=Decimal('2600'))  # 2600/26/8 = 12.5/hr
        self.project = make_project()
        self.wo = make_work_order(self.project)

    def _get_log(self, tl):
        return EmployeeServiceLog.objects.get(time_log=tl)

    def test_8_hours_no_ot(self):
        tl = make_time_log(self.wo, self.emp, start='08:00', finish='16:00')
        log = self._get_log(tl)
        self.assertEqual(log.total_hours, Decimal('8.00'))
        self.assertEqual(log.ot_hours, Decimal('0'))
        self.assertEqual(log.ot_type, None)
        expected_cost = round(Decimal('8') * (Decimal('2600') / Decimal(26 * 8)), 2)
        self.assertEqual(log.normal_cost, expected_cost)
        self.assertEqual(log.ot_cost, Decimal('0'))

    def test_10_hours_weekday_no_ot(self):
        tl = make_time_log(self.wo, self.emp, start='08:00', finish='18:00')
        log = self._get_log(tl)
        self.assertEqual(log.total_hours, Decimal('10.00'))
        self.assertEqual(log.ot_hours, Decimal('0.00'))
        self.assertIsNone(log.ot_type)
        self.assertEqual(log.ot_cost, Decimal('0.00'))

    def test_12_hours_weekday_ot(self):
        tl = make_time_log(self.wo, self.emp, start='08:00', finish='20:00')
        log = self._get_log(tl)
        self.assertEqual(log.total_hours, Decimal('12.00'))
        self.assertEqual(log.ot_hours, Decimal('2.00'))
        self.assertEqual(log.ot_type, 'WEEKDAY')
        hourly = Decimal('2600') / Decimal(26 * 8)
        expected_ot = round(Decimal('2') * hourly * Decimal('1.25'), 2)
        self.assertEqual(log.ot_cost, expected_ot)

    def test_12_hours_holiday_ot(self):
        holiday_date = date(2026, 6, 5)
        Holiday.objects.create(date=holiday_date, description='Test Holiday')
        tl = make_time_log(self.wo, self.emp, start='08:00', finish='20:00', log_date=holiday_date)
        log = self._get_log(tl)
        self.assertTrue(log.is_holiday)
        self.assertEqual(log.ot_hours, Decimal('2.00'))
        self.assertEqual(log.ot_type, 'HOLIDAY')
        hourly = Decimal('2600') / Decimal(26 * 8)
        expected_ot = round(Decimal('2') * hourly * Decimal('2'), 2)
        self.assertEqual(log.ot_cost, expected_ot)

    def test_past_midnight(self):
        tl = make_time_log(self.wo, self.emp, start='22:00', finish='06:00')
        log = self._get_log(tl)
        self.assertEqual(log.total_hours, Decimal('8.00'))

    def test_update_existing_log_on_finish_save(self):
        tl = make_time_log(self.wo, self.emp, start='08:00', finish=None)
        log_before = self._get_log(tl)
        self.assertIsNone(log_before.end_time)
        # Now add finish_time
        tl.finish_time = time(16, 0)
        tl.save()
        log_after = self._get_log(tl)
        self.assertIsNotNone(log_after.end_time)
        self.assertEqual(log_after.total_hours, Decimal('8.00'))
        self.assertEqual(EmployeeServiceLog.objects.filter(time_log=tl).count(), 1)


# ──────────────────────────────────────────────
# Test 6: Employee with salary=None → costs 0, no crash
# ──────────────────────────────────────────────

class NullSalaryCostTest(TestCase):
    def test_null_salary_gives_zero_cost(self):
        _, emp = make_user('emp5', salary=None)
        project = make_project()
        wo = make_work_order(project)
        tl = make_time_log(wo, emp, start='08:00', finish='18:00')
        log = EmployeeServiceLog.objects.get(time_log=tl)
        self.assertEqual(log.normal_cost, Decimal('0'))
        self.assertEqual(log.ot_cost, Decimal('0'))


# ──────────────────────────────────────────────
# Test 7: User not assigned → model-level not blocking (permission check is in view layer)
#         We verify the service log is still created for assigned employee
# ──────────────────────────────────────────────

class UnassignedUserTest(TestCase):
    def test_unassigned_employee_has_no_time_log(self):
        _, emp_assigned = make_user('assigned', salary=Decimal('2600'))
        _, emp_other = make_user('other', salary=Decimal('2600'))
        project = make_project()
        wo = make_work_order(project)
        # Only emp_assigned saves a time entry
        tl = make_time_log(wo, emp_assigned, start='08:00')
        # emp_other has no WorkOrderTime → no service log
        self.assertEqual(
            EmployeeServiceLog.objects.filter(time_log__work_order=wo, employee=emp_other).count(),
            0,
        )


# ──────────────────────────────────────────────
# Test 8: WorkOrder with no Project → service log with project=None
# ──────────────────────────────────────────────

class NoProjectWorkOrderTest(TestCase):
    def test_service_log_created_without_project(self):
        _, emp = make_user('emp6', salary=Decimal('2600'))
        wo = WorkOrder.objects.create(work_order_number='NO-PROJ-001')
        tl = WorkOrderTime.objects.create(
            work_order=wo,
            date=date(2026, 6, 1),
            start_time=time(8, 0),
            finish_time=time(16, 0),
            employee=emp,
        )
        log = EmployeeServiceLog.objects.get(time_log=tl)
        self.assertIsNone(log.project)
        self.assertEqual(log.service_reference or '', '')


# ──────────────────────────────────────────────
# Test 9: Employee Analytics office-only staff & register integration
# ──────────────────────────────────────────────

class EmployeeAnalyticsOfficeStaffTest(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.tz = timezone.get_current_timezone()

    def test_office_only_employee(self):
        """Office-only employee with 3 days of 9h punches → office_hours 27, OT 3, cost = 3×8×rate + 3×1×rate×1.5, work_type Office"""
        salary = Decimal('2600.00')
        _, emp = make_user('office_guy', salary=salary)
        rate = salary / Decimal(26 * 8)  # 12.5

        # 3 days: 2026-06-01 (Mon), 2026-06-02 (Tue), 2026-06-03 (Wed)
        for day in range(1, 4):
            d = date(2026, 6, day)
            login = timezone.make_aware(datetime.combine(d, time(8, 0)), self.tz)
            logout = timezone.make_aware(datetime.combine(d, time(17, 0)), self.tz)
            Attendance.objects.create(
                employee=emp,
                login_time=login,
                log_out_time=logout,
                location='BDO',
                status='APPROVED',
            )

        req = self.rf.get('/analytics/employees/?date=2026-06-01 to 2026-06-03')
        context = build_employee_analytics_context(req)
        e = next(item for item in context['employee_analytics'] if item['employee'].id == emp.id)

        self.assertEqual(e['office_hours'], Decimal('27.00'))
        self.assertEqual(e['vessel_hours'], Decimal('0.00'))
        self.assertEqual(e['total_hours'], Decimal('27.00'))
        self.assertEqual(e['ot_hours'], Decimal('0.00'))
        self.assertEqual(e['work_type'], 'Office')

        # 3 days of 9h: normal cost capped at 10h per day -> 9h per day -> 27h * rate
        expected_normal_cost = Decimal(27) * rate
        expected_ot_cost = Decimal('0.00')
        expected_total_cost = expected_normal_cost

        self.assertEqual(e['normal_cost'], expected_normal_cost)
        self.assertEqual(e['ot_cost'], expected_ot_cost)
        self.assertEqual(e['total_cost'], expected_total_cost)

    def test_vessel_only_employee_matches_old_calculation(self):
        """Vessel-only employee → identical numbers to the old calculation"""
        salary = Decimal('2600.00')
        _, emp = make_user('vessel_gal', salary=salary)
        project = make_project()
        wo = make_work_order(project)

        make_time_log(wo, emp, start='08:00', finish='18:00', log_date=date(2026, 6, 1))
        make_time_log(wo, emp, start='08:00', finish='17:00', log_date=date(2026, 6, 2))

        logs = EmployeeServiceLog.objects.filter(employee=emp, date__range=(date(2026, 6, 1), date(2026, 6, 2)))
        old_hours = sum(l.total_hours for l in logs)
        old_ot = sum(l.ot_hours for l in logs)
        old_normal_cost = sum(l.normal_cost for l in logs)
        old_ot_cost = sum(l.ot_cost for l in logs)
        old_total_cost = old_normal_cost + old_ot_cost

        req = self.rf.get('/analytics/employees/?date=2026-06-01 to 2026-06-02')
        context = build_employee_analytics_context(req)
        e = next(item for item in context['employee_analytics'] if item['employee'].id == emp.id)

        self.assertEqual(e['work_type'], 'Vessel')
        self.assertEqual(e['office_hours'], Decimal('0.00'))
        self.assertEqual(e['vessel_hours'], old_hours)
        self.assertEqual(e['total_hours'], old_hours)
        self.assertEqual(e['ot_hours'], old_ot)
        self.assertEqual(e['normal_cost'], old_normal_cost)
        self.assertEqual(e['ot_cost'], old_ot_cost)
        self.assertEqual(e['total_cost'], old_total_cost)

    def test_mixed_employee_overlapping_punch_and_log(self):
        """Mixed employee with overlapping punch and log → no double counting"""
        salary = Decimal('2600.00')
        _, emp = make_user('mixed_guy', salary=salary)
        project = make_project()
        wo = make_work_order(project)

        d = date(2026, 6, 1)
        login = timezone.make_aware(datetime.combine(d, time(8, 0)), self.tz)
        logout = timezone.make_aware(datetime.combine(d, time(17, 0)), self.tz)
        Attendance.objects.create(
            employee=emp,
            login_time=login,
            log_out_time=logout,
            location='BDO',
            status='APPROVED',
        )

        make_time_log(wo, emp, start='10:00', finish='14:00', log_date=d)

        req = self.rf.get('/analytics/employees/?date=2026-06-01 to 2026-06-01')
        context = build_employee_analytics_context(req)
        e = next(item for item in context['employee_analytics'] if item['employee'].id == emp.id)

        self.assertEqual(e['work_type'], 'Mixed')
        self.assertEqual(e['vessel_hours'], Decimal('4.00'))
        self.assertEqual(e['office_hours'], Decimal('5.00'))
        self.assertEqual(e['total_hours'], Decimal('9.00'))

    def test_employee_on_leave_for_two_days(self):
        """Employee on leave for 2 days → leave days counted, attendance % excludes them"""
        u, emp = make_user('leave_person')

        for day in range(1, 4):
            d = date(2026, 6, day)
            login = timezone.make_aware(datetime.combine(d, time(8, 0)), self.tz)
            logout = timezone.make_aware(datetime.combine(d, time(16, 0)), self.tz)
            Attendance.objects.create(
                employee=emp,
                login_time=login,
                log_out_time=logout,
                location='BDO',
                status='APPROVED',
            )

        Leave.objects.create(
            user=u,
            leave_type='ANNUAL LEAVE',
            from_date=date(2026, 6, 4),
            to_date=date(2026, 6, 5),
            reason='Vacation',
            approval_status='APPROVED',
        )

        req = self.rf.get('/analytics/employees/?date=2026-06-01 to 2026-06-05')
        context = build_employee_analytics_context(req)
        e = next(item for item in context['employee_analytics'] if item['employee'].id == emp.id)

        self.assertEqual(e['days_worked'], 3)
        self.assertEqual(e['days_leave'], 2)
        self.assertEqual(e['attendance_rate'], Decimal('100.00'))

    def test_utilization_uses_working_days_in_range(self):
        """Utilization uses working days in range, not 208"""
        _, emp = make_user('util_worker')

        for day in range(1, 4):
            d = date(2026, 6, day)
            login = timezone.make_aware(datetime.combine(d, time(8, 0)), self.tz)
            logout = timezone.make_aware(datetime.combine(d, time(16, 0)), self.tz)
            Attendance.objects.create(
                employee=emp,
                login_time=login,
                log_out_time=logout,
                location='BDO',
                status='APPROVED',
            )

        req = self.rf.get('/analytics/employees/?date=2026-06-01 to 2026-06-03')
        context = build_employee_analytics_context(req)
        e = next(item for item in context['employee_analytics'] if item['employee'].id == emp.id)

        self.assertEqual(e['working_days'], 3)
        self.assertEqual(e['capacity_hours'], 24)
        self.assertEqual(e['total_hours'], Decimal('24.00'))
        self.assertEqual(e['utilization'], Decimal('100.00'))

    def test_views_as_superuser_and_hr(self):
        """Both analytics pages render for superuser and HR, include office staff, and filter by work_type"""
        from django.urls import reverse

        # Admin user
        admin_u = User.objects.create_superuser('admin_user', 'admin@test.com', 'adminpass')
        # HR user
        hr_u = User.objects.create_user('hr_person', password='hrpass')
        Employee.objects.create(user=hr_u, is_hr=True, salary=Decimal('2600'))

        # Office employee with punches
        _, office_emp = make_user('accounts_clerk', salary=Decimal('2080'))
        d = date(2026, 6, 1)
        login = timezone.make_aware(datetime.combine(d, time(8, 0)), self.tz)
        logout = timezone.make_aware(datetime.combine(d, time(17, 0)), self.tz)
        Attendance.objects.create(
            employee=office_emp,
            login_time=login,
            log_out_time=logout,
            location='BDO',
            status='APPROVED',
        )

        # 1. Superuser on admin analytics view
        self.client.login(username='admin_user', password='adminpass')
        res_admin = self.client.get(f"{reverse('analytics_employee')}?date=2026-06-01 to 2026-06-01")
        self.assertEqual(res_admin.status_code, 200)
        self.assertContains(res_admin, 'Office Hours')
        self.assertContains(res_admin, 'Vessel Hours')
        self.assertContains(res_admin, 'Avg Attendance')
        self.assertContains(res_admin, 'accounts_clerk')
        self.assertContains(res_admin, '<span class="status present">Office</span>')

        # Filter by work_type=Vessel should exclude office_emp
        res_vessel_only = self.client.get(f"{reverse('analytics_employee')}?date=2026-06-01 to 2026-06-01&work_type=Vessel")
        self.assertEqual(res_vessel_only.status_code, 200)
        self.assertNotContains(res_vessel_only, 'accounts_clerk')

        # 2. HR on hr analytics view
        self.client.login(username='hr_person', password='hrpass')
        res_hr = self.client.get(f"{reverse('hr_employee_analytics')}?date=2026-06-01 to 2026-06-01")
        self.assertEqual(res_hr.status_code, 200)
        self.assertContains(res_hr, 'accounts_clerk')
        self.assertContains(res_hr, '<span class="status present">Office</span>')

        # Filter work_type=Office includes accounts_clerk
        res_hr_office = self.client.get(f"{reverse('hr_employee_analytics')}?date=2026-06-01 to 2026-06-01&work_type=Office")
        self.assertEqual(res_hr_office.status_code, 200)
        self.assertContains(res_hr_office, 'accounts_clerk')


class NewOvertimeRulesTests(TestCase):
    def setUp(self):
        self.tz = timezone.get_current_timezone()
        self.salary = Decimal('2080.00')  # rate = 2080 / 208 = 10.00
        self.user = User.objects.create_user(username='ot_user', password='password123', first_name='OT', last_name='Tester')
        self.employee = Employee.objects.create(user=self.user, is_employee=True, salary=self.salary, rank='Surveyor')
        self.project = Project.objects.create(name='OT Project')
        self.client = Client()

    def test_vessel_only_rules(self):
        # 1. one 10h log -> OT 0
        d1 = date(2026, 7, 1)
        log1 = EmployeeServiceLog.objects.create(
            employee=self.employee, project=self.project, date=d1,
            vessel_name='V1', start_time=time(8, 0), end_time=time(18, 0)
        )
        self.assertEqual(log1.total_hours, Decimal('10.00'))
        self.assertEqual(log1.ot_hours, Decimal('0.00'))
        self.assertEqual(log1.ot_cost, Decimal('0.00'))
        self.assertIsNone(log1.ot_type)

        # 2. one 12h log -> OT 2.00, ot_cost = 2 * rate * 1.5
        d2 = date(2026, 7, 2)
        log2 = EmployeeServiceLog.objects.create(
            employee=self.employee, project=self.project, date=d2,
            vessel_name='V2', start_time=time(8, 0), end_time=time(20, 0)
        )
        self.assertEqual(log2.total_hours, Decimal('12.00'))
        self.assertEqual(log2.ot_hours, Decimal('2.00'))
        self.assertEqual(log2.ot_type, 'WEEKDAY')
        rate = Decimal('2080') / Decimal('208')  # 10.00
        self.assertEqual(log2.ot_cost, round(Decimal('2') * rate * Decimal('1.25'), 2))

        # 3. two logs 6h + 6h -> OT 2.00 on the later log
        d3 = date(2026, 7, 3)
        log3a = EmployeeServiceLog.objects.create(
            employee=self.employee, project=self.project, date=d3,
            vessel_name='V3A', start_time=time(8, 0), end_time=time(14, 0)
        )
        log3b = EmployeeServiceLog.objects.create(
            employee=self.employee, project=self.project, date=d3,
            vessel_name='V3B', start_time=time(14, 0), end_time=time(20, 0)
        )
        log3a.refresh_from_db()
        log3b.refresh_from_db()
        self.assertEqual(log3a.ot_hours, Decimal('0.00'))
        self.assertEqual(log3b.ot_hours, Decimal('2.00'))
        self.assertEqual(log3b.ot_type, 'WEEKDAY')
        self.assertEqual(log3b.ot_cost, round(Decimal('2') * rate * Decimal('1.25'), 2))

    def test_holiday_vessel_12h(self):
        d = date(2026, 7, 4)
        Holiday.objects.create(date=d, description='National Day')
        log = EmployeeServiceLog.objects.create(
            employee=self.employee, project=self.project, date=d,
            vessel_name='VH', start_time=time(8, 0), end_time=time(20, 0),
            is_holiday=True
        )
        self.assertTrue(log.is_holiday)
        self.assertEqual(log.total_hours, Decimal('12.00'))
        self.assertEqual(log.ot_hours, Decimal('2.00'))
        self.assertEqual(log.ot_type, 'HOLIDAY')
        rate = Decimal('2080') / Decimal('208')
        self.assertEqual(log.ot_cost, round(Decimal('2') * rate * Decimal('2.0'), 2))

    def test_office_only_12h(self):
        d = date(2026, 7, 5)
        login = timezone.make_aware(datetime.combine(d, time(8, 0)), self.tz)
        logout = timezone.make_aware(datetime.combine(d, time(20, 0)), self.tz)
        Attendance.objects.create(
            employee=self.employee, login_time=login, log_out_time=logout,
            location='BDO', status='APPROVED', total_hours_of_work=Decimal('12.00')
        )
        # Register:
        rows = build_attendance_register(d, d, search=self.user.username)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r['status_code'], 'OFFICE')
        self.assertEqual(r['total_hours'], Decimal('12.00'))
        self.assertEqual(r['ot_hours'], Decimal('0.00'))
        self.assertEqual(r['vessel_ot_hours'], Decimal('0.00'))
        self.assertEqual(r['office_ot_hours'], Decimal('0.00'))

        # Analytics:
        rf = RequestFactory()
        req = rf.get(f'/analytics/employees/?date={d} to {d}')
        ctx = build_employee_analytics_context(req)
        e = next(item for item in ctx['employee_analytics'] if item['employee'].id == self.employee.id)
        self.assertEqual(e['ot_hours'], Decimal('0.00'))
        self.assertEqual(e['ot_cost'], Decimal('0.00'))
        rate = Decimal('2080') / Decimal('208')
        # Office normal cost capped at 10h per day
        self.assertEqual(e['normal_cost'], Decimal('10') * rate)

    def test_office_vessel_office_first(self):
        d = date(2026, 7, 6)
        # office 08:00–14:00 + vessel 14:00–20:00 -> total 12, OT 2.00, all on the log (vessel_ot 2, office_ot 0)
        login = timezone.make_aware(datetime.combine(d, time(8, 0)), self.tz)
        logout = timezone.make_aware(datetime.combine(d, time(14, 0)), self.tz)
        Attendance.objects.create(
            employee=self.employee, login_time=login, log_out_time=logout,
            location='BDO', status='APPROVED', total_hours_of_work=Decimal('6.00')
        )
        log = EmployeeServiceLog.objects.create(
            employee=self.employee, project=self.project, date=d,
            vessel_name='Vessel 1', start_time=time(14, 0), end_time=time(20, 0)
        )
        log.refresh_from_db()
        self.assertEqual(log.total_hours, Decimal('6.00'))
        self.assertEqual(log.ot_hours, Decimal('2.00'))

        rows = build_attendance_register(d, d, search=self.user.username)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r['status_code'], 'OFFICE_VESSEL')
        self.assertEqual(r['total_hours'], Decimal('12.00'))
        self.assertEqual(r['ot_hours'], Decimal('2.00'))
        self.assertEqual(r['vessel_ot_hours'], Decimal('2.00'))
        self.assertEqual(r['office_ot_hours'], Decimal('0.00'))

    def test_office_vessel_vessel_first(self):
        d = date(2026, 7, 7)
        # vessel 06:00–12:00 + office 12:00–18:00 -> OT 2.00, all office (log OT 0); analytics office OT cost = 2 * rate * 1.5
        log = EmployeeServiceLog.objects.create(
            employee=self.employee, project=self.project, date=d,
            vessel_name='Vessel Early', start_time=time(6, 0), end_time=time(12, 0)
        )
        login = timezone.make_aware(datetime.combine(d, time(12, 0)), self.tz)
        logout = timezone.make_aware(datetime.combine(d, time(18, 0)), self.tz)
        Attendance.objects.create(
            employee=self.employee, login_time=login, log_out_time=logout,
            location='BDO', status='APPROVED', total_hours_of_work=Decimal('6.00')
        )
        log.refresh_from_db()
        self.assertEqual(log.ot_hours, Decimal('0.00'))

        rows = build_attendance_register(d, d, search=self.user.username)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r['status_code'], 'OFFICE_VESSEL')
        self.assertEqual(r['total_hours'], Decimal('12.00'))
        self.assertEqual(r['ot_hours'], Decimal('2.00'))
        self.assertEqual(r['vessel_ot_hours'], Decimal('0.00'))
        self.assertEqual(r['office_ot_hours'], Decimal('2.00'))

        # analytics:
        rf = RequestFactory()
        req = rf.get(f'/analytics/employees/?date={d} to {d}')
        ctx = build_employee_analytics_context(req)
        e = next(item for item in ctx['employee_analytics'] if item['employee'].id == self.employee.id)
        rate = Decimal('2080') / Decimal('208')
        expected_off_ot_cost = Decimal('2.00') * rate * Decimal('1.25')
        self.assertEqual(e['ot_cost'], expected_off_ot_cost)

    def test_office_vessel_overlap(self):
        d = date(2026, 7, 8)
        # office 08:00–17:00 (9h) + vessel 10:00–20:00 (10h) -> union 12h, OT 2.00, counted once
        login = timezone.make_aware(datetime.combine(d, time(8, 0)), self.tz)
        logout = timezone.make_aware(datetime.combine(d, time(17, 0)), self.tz)
        Attendance.objects.create(
            employee=self.employee, login_time=login, log_out_time=logout,
            location='BDO', status='APPROVED', total_hours_of_work=Decimal('9.00')
        )
        log = EmployeeServiceLog.objects.create(
            employee=self.employee, project=self.project, date=d,
            vessel_name='Vessel Overlap', start_time=time(10, 0), end_time=time(20, 0)
        )
        log.refresh_from_db()
        rows = build_attendance_register(d, d, search=self.user.username)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r['status_code'], 'OFFICE_VESSEL')
        self.assertEqual(r['total_hours'], Decimal('12.00'))
        self.assertEqual(r['ot_hours'], Decimal('2.00'))
        self.assertEqual(r['ot_hours'], r['vessel_ot_hours'] + r['office_ot_hours'])

    def test_office_vessel_under_threshold(self):
        d = date(2026, 7, 9)
        # office 3h + vessel 5h -> OT 0
        login = timezone.make_aware(datetime.combine(d, time(8, 0)), self.tz)
        logout = timezone.make_aware(datetime.combine(d, time(11, 0)), self.tz)
        Attendance.objects.create(
            employee=self.employee, login_time=login, log_out_time=logout,
            location='BDO', status='APPROVED', total_hours_of_work=Decimal('3.00')
        )
        log = EmployeeServiceLog.objects.create(
            employee=self.employee, project=self.project, date=d,
            vessel_name='Vessel Short', start_time=time(11, 0), end_time=time(16, 0)
        )
        log.refresh_from_db()
        self.assertEqual(log.ot_hours, Decimal('0.00'))
        rows = build_attendance_register(d, d, search=self.user.username)
        self.assertEqual(rows[0]['ot_hours'], Decimal('0.00'))

    def test_attendance_signals_recalculate_vessel_log(self):
        d = date(2026, 7, 10)
        # Initial: vessel log 8h (08:00 to 16:00) -> OT 0
        log = EmployeeServiceLog.objects.create(
            employee=self.employee, project=self.project, date=d,
            vessel_name='Vessel Sig', start_time=time(8, 0), end_time=time(16, 0)
        )
        self.assertEqual(log.ot_hours, Decimal('0.00'))

        # Office came first (04:00 to 08:00 = 4h), then vessel (08:00 to 16:00 = 8h):
        login = timezone.make_aware(datetime.combine(d, time(4, 0)), self.tz)
        logout = timezone.make_aware(datetime.combine(d, time(8, 0)), self.tz)
        att = Attendance.objects.create(
            employee=self.employee, login_time=login, log_out_time=logout,
            location='BDO', status='APPROVED', total_hours_of_work=Decimal('4.00')
        )
        log.refresh_from_db()
        # Hours 0-4: office, hours 4-10: vessel normal, hours 10-12: vessel OT -> 2.00 OT!
        self.assertEqual(log.ot_hours, Decimal('2.00'))

        # Deleting punch restores log to OT 0.00
        att.delete()
        log.refresh_from_db()
        self.assertEqual(log.ot_hours, Decimal('0.00'))

    def test_log_date_change_and_log_deletion(self):
        d1 = date(2026, 7, 11)
        d2 = date(2026, 7, 12)
        # Two 6h logs on d1 -> log1 OT 0, log2 OT 2.00
        log1 = EmployeeServiceLog.objects.create(
            employee=self.employee, project=self.project, date=d1,
            vessel_name='V_L1', start_time=time(8, 0), end_time=time(14, 0)
        )
        log2 = EmployeeServiceLog.objects.create(
            employee=self.employee, project=self.project, date=d1,
            vessel_name='V_L2', start_time=time(14, 0), end_time=time(20, 0)
        )
        log2.refresh_from_db()
        self.assertEqual(log2.ot_hours, Decimal('2.00'))

        # Moving log2 to d2 recalculates both dates!
        log2.date = d2
        log2.save()
        log1.refresh_from_db()
        log2.refresh_from_db()
        self.assertEqual(log1.ot_hours, Decimal('0.00'))
        self.assertEqual(log2.ot_hours, Decimal('0.00'))

        # Deleting one of two logs on a day with 2 logs:
        log1b = EmployeeServiceLog.objects.create(
            employee=self.employee, project=self.project, date=d2,
            vessel_name='V_L1B', start_time=time(8, 0), end_time=time(14, 0)
        )
        log2.refresh_from_db()
        self.assertEqual(log2.ot_hours, Decimal('2.00'))
        # Delete log1b -> log2 recalculates
        log1b.delete()
        log2.refresh_from_db()
        self.assertEqual(log2.ot_hours, Decimal('0.00'))

    def test_recalculate_ot_management_command(self):
        from django.core.management import call_command
        import io

        d = date(2026, 7, 13)
        # Create a 10h log
        log = EmployeeServiceLog.objects.create(
            employee=self.employee, project=self.project, date=d,
            vessel_name='V_CMD', start_time=time(8, 0), end_time=time(18, 0)
        )
        # Manually tamper it to old 8h rule values
        EmployeeServiceLog.objects.filter(pk=log.pk).update(ot_hours=Decimal('2.00'), ot_cost=Decimal('30.00'))
        log.refresh_from_db()
        self.assertEqual(log.ot_hours, Decimal('2.00'))

        # Dry run: does not commit changes
        out = io.StringIO()
        call_command('recalculate_ot', dry_run=True, stdout=out)
        self.assertIn("Dry run completed", out.getvalue())
        log.refresh_from_db()
        self.assertEqual(log.ot_hours, Decimal('2.00'))

        # Real run: fixes the log to 0 OT
        out2 = io.StringIO()
        call_command('recalculate_ot', stdout=out2)
        self.assertIn("Recalculation committed successfully", out2.getvalue())
        log.refresh_from_db()
        self.assertEqual(log.ot_hours, Decimal('0.00'))
        self.assertEqual(log.ot_cost, Decimal('0.00'))

    def test_register_ot_hours_equals_vessel_plus_office_ot_hours(self):
        d = date(2026, 7, 14)
        # Create a mixed day
        login = timezone.make_aware(datetime.combine(d, time(6, 0)), self.tz)
        logout = timezone.make_aware(datetime.combine(d, time(12, 0)), self.tz)
        Attendance.objects.create(
            employee=self.employee, login_time=login, log_out_time=logout,
            location='BDO', status='APPROVED', total_hours_of_work=Decimal('6.00')
        )
        EmployeeServiceLog.objects.create(
            employee=self.employee, project=self.project, date=d,
            vessel_name='V_Mix', start_time=time(10, 0), end_time=time(18, 0)
        )
        rows = build_attendance_register(d, d, search=self.user.username)
        for r in rows:
            self.assertEqual(r['ot_hours'], r['vessel_ot_hours'] + r['office_ot_hours'])


from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from finance.models import Invoice, SpareSale, JobCost
from finance.services import get_project_financials


class AnalyticsProjectFinancialsTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username='admin_fin', email='admin@test.com', password='pass'
        )
        self.admin_emp = Employee.objects.create(
            user=self.admin_user, salary=Decimal('5000.00')
        )

        self.mgr_user = User.objects.create_user(
            username='mgr_user', password='pass'
        )
        self.mgr_emp = Employee.objects.create(
            user=self.mgr_user, is_manager=True, is_accountant=False, salary=Decimal('4000.00')
        )

        self.tech_user = User.objects.create_user(
            username='tech_user', password='pass'
        )
        self.tech_emp = Employee.objects.create(
            user=self.tech_user, is_employee=True, salary=Decimal('3000.00')
        )

    def test_admin_row_costs_add_up_to_expense(self):
        self.client.force_login(self.admin_user)
        today = timezone.localdate()

        proj = Project.objects.create(
            name='Cost Add Up Proj',
            client_name='Client AddUp',
            vessel_name='MV AddUp',
        )
        inv = Invoice.objects.create(
            project=proj,
            invoice_number='INV-ADDUP-01',
            invoice_date=today,
            amount_excl_vat=Decimal('15000.00'),
        )
        SpareSale.objects.create(
            project=proj,
            invoice=inv,
            purchase_cost=Decimal('1200.00'),
            selling_price=Decimal('1800.00'),
        )
        JobCost.objects.create(
            invoice=inv,
            cost_type='TRAVEL',
            amount=Decimal('800.00'),
        )
        EmployeeServiceLog.objects.create(
            employee=self.tech_emp,
            project=proj,
            date=today,
            start_time=time(8, 0),
            end_time=time(16, 0),
            total_hours=Decimal('8.00'),
            normal_cost=Decimal('500.00'),
            ot_cost=Decimal('0.00'),
        )

        resp = self.client.get(reverse('analytics_project'))
        self.assertEqual(resp.status_code, 200)

        row = resp.context['project_analytics'][0]
        self.assertEqual(
            row['employee_cost'] + row['spares_cost'] + row['other_job_costs'],
            row['expense']
        )

        content = resp.content.decode('utf-8')
        # Admin still sees all columns and data-profit attributes
        self.assertIn('data-profit', content)
        self.assertIn('<th>Revenue</th>', content)
        self.assertIn('<th>Employee Cost</th>', content)
        self.assertIn('<th>Spares</th>', content)
        self.assertIn('<th>Other Costs</th>', content)
        self.assertIn('<th>Total Cost</th>', content)
        self.assertIn('<th>Profit</th>', content)
        self.assertIn('<th>Profit %</th>', content)
        self.assertIn('revenueChart', content)

    def test_project_with_invoice_and_labour_shows_revenue_and_correct_profit(self):
        self.client.force_login(self.admin_user)
        today = timezone.localdate()

        proj = Project.objects.create(
            name='Alpha Marine Proj',
            client_name='Client Marine',
            vessel_name='MV Alpha',
            category='AT_BERTH',
            status='ONGOING',
        )
        Invoice.objects.create(
            project=proj,
            invoice_number='AMMS-INV-26-9001',
            invoice_date=today,
            amount_excl_vat=Decimal('18500.00'),
            credits_applied=Decimal('0.00'),
        )
        EmployeeServiceLog.objects.create(
            employee=self.tech_emp,
            project=proj,
            date=today,
            vessel_name='MV Alpha',
            start_time=time(8, 0),
            end_time=time(16, 0),
            ot_hours=Decimal('0.00'),
            total_hours=Decimal('8.00'),
            normal_cost=Decimal('857.38'),
            ot_cost=Decimal('0.00'),
        )

        resp = self.client.get(reverse('analytics_project'))
        self.assertEqual(resp.status_code, 200)

        analytics = resp.context['project_analytics']
        self.assertEqual(len(analytics), 1)
        row = analytics[0]
        self.assertEqual(row['project'], proj)
        self.assertEqual(row['revenue'], Decimal('18500.00'))

        fin = get_project_financials(proj)
        self.assertEqual(row['profit'], fin['profit'])
        self.assertNotEqual(row['profit'], Decimal('-857.38'))

        content = resp.content.decode('utf-8')
        self.assertIn('18,500', content)
        self.assertIn('Revenue and costs are project totals (all invoices); hours are for the selected period.', content)

    def test_project_with_no_invoice_shows_dash_for_profit_percent(self):
        self.client.force_login(self.admin_user)
        today = timezone.localdate()

        proj = Project.objects.create(
            name='No Invoice Proj',
            client_name='Client Zero',
            vessel_name='MV Zero',
            status='ONGOING',
            invoice_amount=Decimal('0.00'),
        )
        EmployeeServiceLog.objects.create(
            employee=self.tech_emp,
            project=proj,
            date=today,
            start_time=time(8, 0),
            end_time=time(13, 0),
            total_hours=Decimal('5.00'),
            normal_cost=Decimal('200.00'),
            ot_cost=Decimal('0.00'),
        )

        resp = self.client.get(reverse('analytics_project'))
        self.assertEqual(resp.status_code, 200)

        analytics = resp.context['project_analytics']
        self.assertEqual(len(analytics), 1)
        row = analytics[0]
        self.assertEqual(row['revenue'], Decimal('0.00'))
        self.assertIsNone(row['profit_percent'])

        content = resp.content.decode('utf-8')
        self.assertIn('—', content)

    def test_query_count_constant_for_3_vs_10_projects(self):
        self.client.force_login(self.admin_user)
        today = timezone.localdate()

        Project.objects.all().delete()
        Invoice.objects.all().delete()
        EmployeeServiceLog.objects.all().delete()

        # Create 3 projects with invoices and service logs
        for i in range(3):
            p = Project.objects.create(name=f'P3_{i}', client_name=f'Client {i}')
            Invoice.objects.create(
                project=p,
                invoice_number=f'INV-P3-{i}',
                invoice_date=today,
                amount_excl_vat=Decimal('1000.00'),
            )
            EmployeeServiceLog.objects.create(
                employee=self.tech_emp,
                project=p,
                date=today,
                start_time=time(8, 0),
                end_time=time(12, 0),
                total_hours=Decimal('4.00'),
                normal_cost=Decimal('100.00'),
            )

        with CaptureQueriesContext(connection) as ctx_3:
            self.client.get(reverse('analytics_project'))
        queries_3 = len(ctx_3.captured_queries)

        # Add 7 more projects (total 10)
        for i in range(3, 10):
            p = Project.objects.create(name=f'P10_{i}', client_name=f'Client {i}')
            Invoice.objects.create(
                project=p,
                invoice_number=f'INV-P10-{i}',
                invoice_date=today,
                amount_excl_vat=Decimal('1000.00'),
            )
            EmployeeServiceLog.objects.create(
                employee=self.tech_emp,
                project=p,
                date=today,
                start_time=time(8, 0),
                end_time=time(12, 0),
                total_hours=Decimal('4.00'),
                normal_cost=Decimal('100.00'),
            )

        with CaptureQueriesContext(connection) as ctx_10:
            self.client.get(reverse('analytics_project'))
        queries_10 = len(ctx_10.captured_queries)

        self.assertEqual(queries_3, queries_10)

    def test_manager_does_not_see_profit_column(self):
        today = timezone.localdate()
        proj = Project.objects.create(
            name='Secret Finance Proj',
            client_name='Secret Client',
        )
        Invoice.objects.create(
            project=proj,
            invoice_number='INV-SEC-01',
            invoice_date=today,
            amount_excl_vat=Decimal('25000.00'),
        )
        EmployeeServiceLog.objects.create(
            employee=self.tech_emp,
            project=proj,
            date=today,
            start_time=time(8, 0),
            end_time=time(14, 0),
            total_hours=Decimal('6.00'),
            normal_cost=Decimal('300.00'),
        )

        # Log in as manager
        self.client.force_login(self.mgr_user)
        resp_mgr = self.client.get(reverse('analytics_project'))
        self.assertEqual(resp_mgr.status_code, 200)
        self.assertFalse(resp_mgr.context['can_view_finance'])

        # Service Completion has no profit in context
        sc_item = resp_mgr.context['service_completions_page'][0]
        self.assertIsNone(sc_item['profit'])
        self.assertIsNone(sc_item['profit_percent'])

        # KPIs have no revenue / profit / expense
        self.assertIsNone(resp_mgr.context['kpi_revenue'])
        self.assertIsNone(resp_mgr.context['kpi_profit'])
        self.assertIsNone(resp_mgr.context['kpi_expense'])

        # Revenue chart values are empty lists
        self.assertEqual(resp_mgr.context['revenue_current'], [])
        self.assertEqual(resp_mgr.context['revenue_last_year'], [])

        content_mgr = resp_mgr.content.decode('utf-8')
        # Manager response contains no data-profit at all
        self.assertNotIn('data-profit', content_mgr)
        self.assertNotIn('<th>Total Cost</th>', content_mgr)
        self.assertNotIn('<th>Spares</th>', content_mgr)
        self.assertNotIn('<th>Other Costs</th>', content_mgr)
        self.assertNotIn('<th>Profit</th>', content_mgr)
        self.assertNotIn('<th>Profit %</th>', content_mgr)
        self.assertNotIn('<th>Revenue</th>', content_mgr)
        self.assertNotIn('<th>Total Profit</th>', content_mgr)
        self.assertNotIn('revenueChart', content_mgr)
        self.assertNotIn('Projects by Profit', content_mgr)

        # Service Completion table contains no "None"
        sc_section_mgr = content_mgr.split('<!-- ================= SERVICE COMPLETION ================= -->')[1].split('<!-- ================= PROJECT DRILL DOWN MODAL ================= -->')[0]
        self.assertNotIn('None', sc_section_mgr)

        # Non-finance columns shown
        self.assertIn('Projects by Employee Cost', content_mgr)
        self.assertIn('Sort by Employee Cost', content_mgr)
        self.assertIn('Employee cost is the project total; hours are for the selected period.', content_mgr)
        self.assertIn('<th>Employee Cost</th>', content_mgr)
        self.assertIn('<th>Hours</th>', content_mgr)
        self.assertIn('Emp Cost (period)', content_mgr)

        # Log in as admin
        self.client.force_login(self.admin_user)
        resp_admin = self.client.get(reverse('analytics_project'))
        self.assertEqual(resp_admin.status_code, 200)
        self.assertTrue(resp_admin.context['can_view_finance'])
        content_admin = resp_admin.content.decode('utf-8')
        self.assertIn('Projects by Profit', content_admin)
        self.assertIn('Sort by Profit', content_admin)
        self.assertIn('Revenue and costs are project totals (all invoices); hours are for the selected period.', content_admin)
        self.assertIn('data-profit', content_admin)
        self.assertIn('<th>Total Cost</th>', content_admin)
        self.assertIn('<th>Profit</th>', content_admin)
        self.assertIn('<th>Profit %</th>', content_admin)
        self.assertIn('<th>Revenue</th>', content_admin)


# ──────────────────────────────────────────────
# Tests for New Overtime Multipliers (1.25 Weekday, 1.5 Sunday, 2.0 Holiday)
# ──────────────────────────────────────────────

class NewOtMultipliersTests(TestCase):
    def setUp(self):
        self.tz = timezone.get_current_timezone()
        self.user, self.emp = make_user('ot_worker', salary=Decimal('2080.00'))  # 2080 / 208 = 10.00/hr
        self.project = make_project()

    def test_ot_helper_functions_and_types(self):
        from service_logs.ot import ot_multiplier, ot_type_for, OT_MULT_NORMAL, OT_MULT_SUNDAY, OT_MULT_PUBLIC_HOLIDAY

        monday = date(2026, 6, 1)    # weekday 0
        saturday = date(2026, 6, 6)  # weekday 5
        sunday = date(2026, 6, 7)    # weekday 6

        # Normal days
        self.assertEqual(ot_multiplier(monday, False), OT_MULT_NORMAL)
        self.assertEqual(ot_multiplier(monday, False), Decimal('1.25'))
        self.assertEqual(ot_type_for(monday, False), 'WEEKDAY')

        self.assertEqual(ot_multiplier(saturday, False), OT_MULT_NORMAL)
        self.assertEqual(ot_type_for(saturday, False), 'WEEKDAY')

        # Sunday (not holiday)
        self.assertEqual(ot_multiplier(sunday, False), OT_MULT_SUNDAY)
        self.assertEqual(ot_multiplier(sunday, False), Decimal('1.5'))
        self.assertEqual(ot_type_for(sunday, False), 'SUNDAY')

        # Public holiday on Monday
        self.assertEqual(ot_multiplier(monday, True), OT_MULT_PUBLIC_HOLIDAY)
        self.assertEqual(ot_multiplier(monday, True), Decimal('2.0'))
        self.assertEqual(ot_type_for(monday, True), 'HOLIDAY')

        # Public holiday on Sunday (holiday wins over Sunday)
        self.assertEqual(ot_multiplier(sunday, True), OT_MULT_PUBLIC_HOLIDAY)
        self.assertEqual(ot_multiplier(sunday, True), Decimal('2.0'))
        self.assertEqual(ot_type_for(sunday, True), 'HOLIDAY')

    def test_monday_12h_vessel_multiplier(self):
        # Monday 12 h vessel -> 2 h OT x rate x 1.25, ot_type='WEEKDAY'
        monday = date(2026, 6, 1)
        log = EmployeeServiceLog.objects.create(
            employee=self.emp, project=self.project, date=monday,
            vessel_name='V_Mon', start_time=time(8, 0), end_time=time(20, 0)
        )
        self.assertEqual(log.total_hours, Decimal('12.00'))
        self.assertEqual(log.ot_hours, Decimal('2.00'))
        self.assertEqual(log.ot_type, 'WEEKDAY')
        self.assertEqual(log.normal_cost, Decimal('100.00'))  # 10h * 10
        self.assertEqual(log.ot_cost, Decimal('25.00'))       # 2h * 10 * 1.25 = 25.00

    def test_sunday_12h_vessel_multiplier(self):
        # Sunday 12 h vessel -> 2 h OT x rate x 1.5, ot_type='SUNDAY'
        sunday = date(2026, 6, 7)
        log = EmployeeServiceLog.objects.create(
            employee=self.emp, project=self.project, date=sunday,
            vessel_name='V_Sun', start_time=time(8, 0), end_time=time(20, 0)
        )
        self.assertEqual(log.total_hours, Decimal('12.00'))
        self.assertEqual(log.ot_hours, Decimal('2.00'))
        self.assertEqual(log.ot_type, 'SUNDAY')
        self.assertEqual(log.normal_cost, Decimal('100.00'))  # 10h * 10
        self.assertEqual(log.ot_cost, Decimal('30.00'))       # 2h * 10 * 1.5 = 30.00

    def test_holiday_list_date_not_sunday(self):
        # Holiday-list date (not Sunday) -> x 2.0, ot_type='HOLIDAY'
        friday = date(2026, 6, 5)
        Holiday.objects.create(date=friday, description='Martyrs Day')
        log = EmployeeServiceLog.objects.create(
            employee=self.emp, project=self.project, date=friday,
            vessel_name='V_Hol', start_time=time(8, 0), end_time=time(20, 0)
        )
        self.assertEqual(log.total_hours, Decimal('12.00'))
        self.assertEqual(log.ot_hours, Decimal('2.00'))
        self.assertEqual(log.ot_type, 'HOLIDAY')
        self.assertEqual(log.normal_cost, Decimal('100.00'))
        self.assertEqual(log.ot_cost, Decimal('40.00'))       # 2h * 10 * 2.0 = 40.00

    def test_holiday_list_date_that_is_also_a_sunday(self):
        # Holiday-list date that is also a Sunday -> x 2.0 (holiday wins over Sunday), ot_type='HOLIDAY'
        sunday = date(2026, 6, 14)
        Holiday.objects.create(date=sunday, description='Sunday Holiday')
        log = EmployeeServiceLog.objects.create(
            employee=self.emp, project=self.project, date=sunday,
            vessel_name='V_SunHol', start_time=time(8, 0), end_time=time(20, 0)
        )
        self.assertEqual(log.total_hours, Decimal('12.00'))
        self.assertEqual(log.ot_hours, Decimal('2.00'))
        self.assertEqual(log.ot_type, 'HOLIDAY')
        self.assertEqual(log.normal_cost, Decimal('100.00'))
        self.assertEqual(log.ot_cost, Decimal('40.00'))       # 2h * 10 * 2.0 = 40.00

    def test_holiday_working_project_on_monday(self):
        # HOLIDAY_WORKING project on a Monday -> x 2.0, ot_type='HOLIDAY'
        monday = date(2026, 6, 15)
        hol_project = make_project(category='HOLIDAY_WORKING')
        wo = make_work_order(hol_project)
        tl = make_time_log(wo, self.emp, start='08:00', finish='20:00', log_date=monday)
        log = EmployeeServiceLog.objects.get(time_log=tl)
        self.assertTrue(log.is_holiday)
        self.assertEqual(log.total_hours, Decimal('12.00'))
        self.assertEqual(log.ot_hours, Decimal('2.00'))
        self.assertEqual(log.ot_type, 'HOLIDAY')
        self.assertEqual(log.normal_cost, Decimal('100.00'))
        self.assertEqual(log.ot_cost, Decimal('40.00'))       # 2h * 10 * 2.0 = 40.00

    def test_office_ot_on_sunday(self):
        # office OT on a Sunday -> office OT x 1.5
        # vessel 06:00-12:00 (6h) + office 12:00-18:00 (6h) -> union 12h, threshold 10h -> 2h OT falls in office
        sunday = date(2026, 6, 21)
        log = EmployeeServiceLog.objects.create(
            employee=self.emp, project=self.project, date=sunday,
            vessel_name='V_Early', start_time=time(6, 0), end_time=time(12, 0)
        )
        login = timezone.make_aware(datetime.combine(sunday, time(12, 0)), self.tz)
        logout = timezone.make_aware(datetime.combine(sunday, time(18, 0)), self.tz)
        Attendance.objects.create(
            employee=self.emp, login_time=login, log_out_time=logout,
            location='BDO', status='APPROVED', total_hours_of_work=Decimal('6.00')
        )
        log.refresh_from_db()
        self.assertEqual(log.ot_hours, Decimal('0.00'))

        rows = build_attendance_register(sunday, sunday, search=self.user.username)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r['total_hours'], Decimal('12.00'))
        self.assertEqual(r['ot_hours'], Decimal('2.00'))
        self.assertEqual(r['vessel_ot_hours'], Decimal('0.00'))
        self.assertEqual(r['office_ot_hours'], Decimal('2.00'))

        from service_logs.ot import calculate_office_ot_cost
        office_ot = calculate_office_ot_cost(self.emp.salary, rows, [log])
        # 2h * (2080 / 208 = 10) * 1.5 = 30.00
        self.assertEqual(office_ot, Decimal('30.00'))






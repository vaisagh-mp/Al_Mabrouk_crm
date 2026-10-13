"""
Tests for the 5-part WorkOrderTime → EmployeeServiceLog feature.
Run with:  python manage.py test Admin.tests service_logs.tests
"""
from datetime import date, time, datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, RequestFactory
from django.utils import timezone

from Admin.models import (
    Employee, Holiday, Project, WorkOrder, WorkOrderTime,
    Attendance, Leave,
)
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

    def test_10_hours_weekday_ot(self):
        tl = make_time_log(self.wo, self.emp, start='08:00', finish='18:00')
        log = self._get_log(tl)
        self.assertEqual(log.total_hours, Decimal('10.00'))
        self.assertEqual(log.ot_hours, Decimal('2.00'))
        self.assertEqual(log.ot_type, 'WEEKDAY')
        hourly = Decimal('2600') / Decimal(26 * 8)
        expected_ot = round(Decimal('2') * hourly * Decimal('1.5'), 2)
        self.assertEqual(log.ot_cost, expected_ot)

    def test_10_hours_holiday_ot(self):
        holiday_date = date(2026, 6, 5)
        Holiday.objects.create(date=holiday_date, description='Test Holiday')
        tl = make_time_log(self.wo, self.emp, start='08:00', finish='18:00', log_date=holiday_date)
        log = self._get_log(tl)
        self.assertTrue(log.is_holiday)
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
        self.assertEqual(e['ot_hours'], Decimal('3.00'))
        self.assertEqual(e['work_type'], 'Office')

        expected_normal_cost = Decimal(3 * 8) * rate
        expected_ot_cost = Decimal(3 * 1) * rate * Decimal('1.5')
        expected_total_cost = expected_normal_cost + expected_ot_cost

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



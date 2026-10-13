from datetime import date, datetime, time, timedelta
from decimal import Decimal
import pytz

from django.test import TestCase, Client
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.contrib.auth.models import User
from django.utils import timezone
from django.urls import reverse

from Admin.models import (
    Employee, Attendance, Leave, Holiday, DailyAttendanceStatus,
    Project, WorkOrder, WorkOrderTime, Vessel
)
from Admin.attendance_register import (
    build_attendance_register, _wo_number, build_attendance_summary,
    summary_csv_response, summary_excel_response, STANDARD_DAY_HOURS
)
from Admin.attendance_analytics import (
    build_attendance_analytics, OT_ALERT_HOURS_PER_30_DAYS,
    BRADFORD_MONITOR, BRADFORD_ACTION
)
from service_logs.models import EmployeeServiceLog


class AttendanceRegisterTests(TestCase):
    def setUp(self):
        self.dubai_tz = pytz.timezone('Asia/Dubai')
        self.user = User.objects.create_user(username='john_doe', first_name='John', last_name='Doe', password='pass')
        self.employee = Employee.objects.create(
            user=self.user,
            is_employee=True,
            rank='Senior Technician'
        )

    def test_office_punch_only(self):
        """office punch only → Office, Office location, hours and OT from attendance"""
        test_date = date(2026, 6, 2)  # Tuesday
        login_dt = self.dubai_tz.localize(datetime.combine(test_date, time(9, 0)))
        logout_dt = self.dubai_tz.localize(datetime.combine(test_date, time(18, 0)))

        Attendance.objects.create(
            employee=self.employee,
            login_time=login_dt,
            log_out_time=logout_dt,
            total_hours_of_work=Decimal('9.00'),
            location='BDO',
            status='APPROVED'
        )

        rows = build_attendance_register(test_date, test_date)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['status_label'], 'Office')
        self.assertEqual(row['location_label'], 'Office')
        self.assertEqual(row['total_hours'], Decimal('9.00'))
        self.assertEqual(row['ot_hours'], Decimal('1.00'))
        self.assertEqual(row['rank'], 'Senior Technician')
        self.assertEqual(row['start'], time(9, 0))
        self.assertEqual(row['end'], time(18, 0))

    def test_service_log_only(self):
        """service log only (ANCHORAGE, SAILING, AT_BERTH project) → Vessel with Anchorage / Sailing / Port, WO through time_log.work_order"""
        p_anchorage = Project.objects.create(name='P Anchorage', category='ANCHORAGE')
        p_sailing = Project.objects.create(name='P Sailing', category='ANCHORAGE')
        p_at_berth = Project.objects.create(name='P Berth', category='AT_BERTH')

        wo = WorkOrder.objects.create(project=p_anchorage, work_order_number='WO-ANC-101')
        tl = WorkOrderTime.objects.create(
            work_order=wo,
            employee=self.employee,
            date=date(2026, 6, 2),
            start_time=time(8, 0),
            finish_time=time(16, 0)
        )

        # 1. Anchorage log (auto-created by WorkOrderTime signal, now customize fields)
        log1 = EmployeeServiceLog.objects.get(time_log=tl)
        log1.vessel_name = 'MV Ocean 1'
        log1.service_reference = 'SR-01'
        log1.location_type = 'ANCHORAGE'
        log1.save()

        # 2. Sailing log
        log2 = EmployeeServiceLog.objects.create(
            employee=self.employee,
            project=p_sailing,
            date=date(2026, 6, 3),
            vessel_name='MV Sea 2',
            service_reference='SR-02',
            start_time=time(10, 0),
            end_time=time(18, 0),
            total_hours=Decimal('8.00'),
            location_type='SAILING'
        )

        # 3. AT_BERTH project log
        log3 = EmployeeServiceLog.objects.create(
            employee=self.employee,
            project=p_at_berth,
            date=date(2026, 6, 4),
            vessel_name='MV Port 3',
            service_reference='SR-03',
            start_time=time(7, 0),
            end_time=time(15, 0),
            total_hours=Decimal('8.00'),
            location_type='ANCHORAGE'
        )

        rows = build_attendance_register(date(2026, 6, 2), date(2026, 6, 4))
        # rows sorted by date descending: 6/4, 6/3, 6/2
        row_map = {r['date']: r for r in rows}

        r1 = row_map[date(2026, 6, 2)]
        self.assertEqual(r1['status_label'], 'Vessel')
        self.assertEqual(r1['location_label'], 'Anchorage')
        self.assertEqual(r1['vessel_job'], ['MV Ocean 1'])
        self.assertEqual(r1['service_ref'], ['SR-01'])
        self.assertEqual(r1['wo_number'], ['WO-ANC-101'])

        r2 = row_map[date(2026, 6, 3)]
        self.assertEqual(r2['status_label'], 'Vessel')
        self.assertEqual(r2['location_label'], 'Sailing')

        r3 = row_map[date(2026, 6, 4)]
        self.assertEqual(r3['status_label'], 'Vessel')
        self.assertEqual(r3['location_label'], 'Port')

    def test_attendance_plus_overlapping_service_log(self):
        """attendance plus an overlapping service log → Office+Vessel, total = union, not the sum"""
        test_date = date(2026, 6, 2)
        login_dt = self.dubai_tz.localize(datetime.combine(test_date, time(8, 0)))
        logout_dt = self.dubai_tz.localize(datetime.combine(test_date, time(17, 0)))

        Attendance.objects.create(
            employee=self.employee,
            login_time=login_dt,
            log_out_time=logout_dt,
            total_hours_of_work=Decimal('9.00'),
            location='BDO',
            status='APPROVED'
        )

        p = Project.objects.create(name='Project Overlap')
        EmployeeServiceLog.objects.create(
            employee=self.employee,
            project=p,
            date=test_date,
            vessel_name='MV Overlap',
            start_time=time(13, 0),
            end_time=time(18, 0),
            total_hours=Decimal('5.00')
        )

        rows = build_attendance_register(test_date, test_date)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['status_label'], 'Office+Vessel')
        # Union from 08:00 to 18:00 = 10.00 hours (sum would be 9 + 5 = 14)
        self.assertEqual(row['total_hours'], Decimal('10.00'))
        self.assertEqual(row['ot_hours'], Decimal('2.00'))
        self.assertEqual(row['start'], time(8, 0))
        self.assertEqual(row['end'], time(18, 0))

    def test_two_service_logs_in_one_day(self):
        """two service logs in one day → both vessels and refs listed, hours summed"""
        test_date = date(2026, 6, 2)
        p = Project.objects.create(name='P Dual')

        EmployeeServiceLog.objects.create(
            employee=self.employee,
            project=p,
            date=test_date,
            vessel_name='Vessel Alpha',
            service_reference='REF-A',
            start_time=time(8, 0),
            end_time=time(12, 0),
            total_hours=Decimal('4.00'),
            ot_hours=Decimal('0.00')
        )

        EmployeeServiceLog.objects.create(
            employee=self.employee,
            project=p,
            date=test_date,
            vessel_name='Vessel Beta',
            service_reference='REF-B',
            start_time=time(13, 0),
            end_time=time(22, 0)
        )

        rows = build_attendance_register(test_date, test_date)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['status_label'], 'Vessel')
        self.assertEqual(row['vessel_job'], ['Vessel Alpha', 'Vessel Beta'])
        self.assertEqual(row['service_ref'], ['REF-A', 'REF-B'])
        self.assertEqual(row['total_hours'], Decimal('13.00'))
        self.assertEqual(row['ot_hours'], Decimal('1.00'))

    def test_approved_leaves_across_three_days(self):
        """approved Annual / Sick / Personal leave across 3 days → 3 rows with the right status"""
        Leave.objects.create(
            user=self.user,
            leave_type='ANNUAL LEAVE',
            from_date=date(2026, 6, 1),
            to_date=date(2026, 6, 1),
            approval_status='APPROVED',
            reason='Annual vacation',
            no_of_days=1
        )
        Leave.objects.create(
            user=self.user,
            leave_type='SICK LEAVE',
            from_date=date(2026, 6, 2),
            to_date=date(2026, 6, 2),
            approval_status='APPROVED',
            reason='Flu',
            no_of_days=1
        )
        Leave.objects.create(
            user=self.user,
            leave_type='PERSONAL LEAVE',
            from_date=date(2026, 6, 3),
            to_date=date(2026, 6, 3),
            approval_status='APPROVED',
            reason='Family event',
            no_of_days=1
        )

        rows = build_attendance_register(date(2026, 6, 1), date(2026, 6, 3))
        row_map = {r['date']: r for r in rows}

        self.assertEqual(row_map[date(2026, 6, 1)]['status_label'], 'Annual Leave')
        self.assertEqual(row_map[date(2026, 6, 2)]['status_label'], 'Sick Leave')
        self.assertEqual(row_map[date(2026, 6, 3)]['status_label'], 'Personal Leave')
        for r in rows:
            self.assertEqual(r['location_label'], '')
            self.assertEqual(r['total_hours'], Decimal('0.00'))

    def test_holiday_and_sunday_with_no_work(self):
        """a Holiday date and a Sunday with no work → Holiday / Sunday, location Holiday"""
        # 2026-06-07 is Sunday
        sunday_date = date(2026, 6, 7)
        # 2026-06-08 is Monday holiday
        holiday_date = date(2026, 6, 8)
        Holiday.objects.create(date=holiday_date, description='Public Holiday')

        rows = build_attendance_register(date(2026, 6, 7), date(2026, 6, 8))
        row_map = {r['date']: r for r in rows}

        sun_row = row_map[sunday_date]
        self.assertEqual(sun_row['status_label'], 'Sunday')
        self.assertEqual(sun_row['location_label'], 'Holiday')

        hol_row = row_map[holiday_date]
        self.assertEqual(hol_row['status_label'], 'Holiday')
        self.assertEqual(hol_row['location_label'], 'Holiday')

    def test_absent_and_future_day_skipped(self):
        """a past working day with nothing → Absent; a future date → no row"""
        today = timezone.localtime(timezone.now()).date()
        # Find a past working day (Monday - Saturday)
        past_date = today - timedelta(days=7)
        while past_date.weekday() == 6:  # avoid Sunday
            past_date -= timedelta(days=1)

        # Future working day
        future_date = today + timedelta(days=7)
        while future_date.weekday() == 6:  # avoid Sunday
            future_date += timedelta(days=1)

        past_rows = build_attendance_register(past_date, past_date)
        self.assertEqual(len(past_rows), 1)
        self.assertEqual(past_rows[0]['status_label'], 'Absent')
        self.assertEqual(past_rows[0]['location_label'], '')

        future_rows = build_attendance_register(future_date, future_date)
        self.assertEqual(len(future_rows), 0)

    def test_daily_attendance_status_training_with_attendance(self):
        """a DailyAttendanceStatus TRAINING on a day with attendance → status Training, times still filled"""
        test_date = date(2026, 6, 2)
        login_dt = self.dubai_tz.localize(datetime.combine(test_date, time(9, 0)))
        logout_dt = self.dubai_tz.localize(datetime.combine(test_date, time(17, 0)))

        Attendance.objects.create(
            employee=self.employee,
            login_time=login_dt,
            log_out_time=logout_dt,
            total_hours_of_work=Decimal('8.00'),
            location='BDO',
            status='APPROVED'
        )

        override = DailyAttendanceStatus.objects.create(
            employee=self.employee,
            date=test_date,
            status='TRAINING',
            location='OFFICE',
            note='Safety induction'
        )

        rows = build_attendance_register(test_date, test_date)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['status_label'], 'Training')
        self.assertEqual(row['location_label'], 'Office')
        self.assertEqual(row['start'], time(9, 0))
        self.assertEqual(row['end'], time(17, 0))
        self.assertEqual(row['total_hours'], Decimal('8.00'))
        self.assertEqual(row['override_id'], override.id)

    def test_rejected_ignored_and_pending_badge(self):
        """REJECTED attendance is ignored; PENDING sets has_pending"""
        day1 = date(2026, 6, 2)
        day2 = date(2026, 6, 3)

        login1 = self.dubai_tz.localize(datetime.combine(day1, time(9, 0)))
        logout1 = self.dubai_tz.localize(datetime.combine(day1, time(18, 0)))

        # Rejected attendance on day 1
        Attendance.objects.create(
            employee=self.employee,
            login_time=login1,
            log_out_time=logout1,
            total_hours_of_work=Decimal('9.00'),
            status='REJECTED'
        )

        # Pending attendance on day 2
        login2 = self.dubai_tz.localize(datetime.combine(day2, time(9, 0)))
        logout2 = self.dubai_tz.localize(datetime.combine(day2, time(18, 0)))
        Attendance.objects.create(
            employee=self.employee,
            login_time=login2,
            log_out_time=logout2,
            total_hours_of_work=Decimal('9.00'),
            status='PENDING'
        )

        rows = build_attendance_register(day1, day2)
        row_map = {r['date']: r for r in rows}

        # Day 1 ignored rejected -> Absent
        self.assertEqual(row_map[day1]['status_label'], 'Absent')
        self.assertFalse(row_map[day1]['has_pending'])

        # Day 2 pending -> Office with has_pending=True
        self.assertEqual(row_map[day2]['status_label'], 'Office')
        self.assertTrue(row_map[day2]['has_pending'])

    def test_project_with_no_work_order(self):
        """a project with no WorkOrder doesn't crash (WO shows "-")"""
        test_date = date(2026, 6, 2)
        p = Project.objects.create(name='P Without WO')
        # Check helper directly
        self.assertIsNone(_wo_number(p))

        EmployeeServiceLog.objects.create(
            employee=self.employee,
            project=p,
            date=test_date,
            vessel_name='Vessel No WO',
            start_time=time(8, 0),
            end_time=time(16, 0),
            total_hours=Decimal('8.00')
        )

        rows = build_attendance_register(test_date, test_date)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['wo_number'], ['-'])

    def test_punch_in_at_0100_dubai_time(self):
        """a punch-in at 01:00 Dubai time lands on the correct local date"""
        test_date = date(2026, 6, 2)
        login_dt = self.dubai_tz.localize(datetime.combine(test_date, time(1, 0)))
        logout_dt = self.dubai_tz.localize(datetime.combine(test_date, time(9, 0)))

        Attendance.objects.create(
            employee=self.employee,
            login_time=login_dt,
            log_out_time=logout_dt,
            total_hours_of_work=Decimal('8.00'),
            location='BDO',
            status='APPROVED'
        )

        rows = build_attendance_register(test_date, test_date)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['date'], test_date)
        self.assertEqual(row['start'], time(1, 0))

    def test_query_count_stays_constant(self):
        """Use CaptureQueriesContext to show query count for a 31-day range with 6 employees stays constant"""
        # Create 5 additional employees
        for i in range(2, 7):
            u = User.objects.create_user(username=f'emp_{i}', password='p')
            Employee.objects.create(user=u, is_employee=True)

        start_date = date(2026, 5, 1)
        end_date = date(2026, 5, 31)

        with CaptureQueriesContext(connection) as ctx:
            rows = build_attendance_register(start_date, end_date)

        # Fixed queries: employees, attendances, service logs, leaves, holidays, daily statuses = 6 queries
        self.assertLessEqual(len(ctx), 6)


class AttendanceViewAndPermissionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.superuser = User.objects.create_superuser(username='admin_user', password='password123', email='admin@test.com')
        self.hr_user = User.objects.create_user(username='hr_user', password='password123')
        self.hr_emp = Employee.objects.create(user=self.hr_user, is_hr=True)

        self.normal_user = User.objects.create_user(username='normal_user', password='password123')
        self.normal_emp = Employee.objects.create(user=self.normal_user, is_employee=True)

    def test_admin_attendance_list_permissions(self):
        url = reverse('attendance_list_adminview')
        # Normal user -> 403 Forbidden
        self.client.login(username='normal_user', password='password123')
        res = self.client.get(url)
        self.assertEqual(res.status_code, 403)

        # Superuser -> 200 OK
        self.client.login(username='admin_user', password='password123')
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

    def test_hr_attendance_list_permissions(self):
        url = reverse('hr_attendance_list_view')
        # Normal user -> 403 Forbidden
        self.client.login(username='normal_user', password='password123')
        res = self.client.get(url)
        self.assertEqual(res.status_code, 403)

        # HR user -> 200 OK
        self.client.login(username='hr_user', password='password123')
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

    def test_set_day_status_save_and_clear(self):
        self.client.login(username='admin_user', password='password123')
        url = reverse('admin_set_day_status', kwargs={'employee_id': self.normal_emp.id, 'date': '2026-06-02'})

        # GET form
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        # POST save status
        post_data = {
            'status': 'TRAINING',
            'location': 'OFFICE',
            'note': 'Company Seminar',
            'next': '/admin-panel/employee/attendance/'
        }
        res = self.client.post(url, post_data)
        self.assertEqual(res.status_code, 302)

        override = DailyAttendanceStatus.objects.get(employee=self.normal_emp, date=date(2026, 6, 2))
        self.assertEqual(override.status, 'TRAINING')
        self.assertEqual(override.location, 'OFFICE')
        self.assertEqual(override.note, 'Company Seminar')

        # POST clear status
        clear_data = {
            'action': 'clear',
            'next': '/admin-panel/employee/attendance/'
        }
        res = self.client.post(url, clear_data)
        self.assertEqual(res.status_code, 302)
        self.assertFalse(DailyAttendanceStatus.objects.filter(employee=self.normal_emp, date=date(2026, 6, 2)).exists())

    def test_admin_delete_attendance_requires_post(self):
        self.client.login(username='admin_user', password='password123')
        att = Attendance.objects.create(employee=self.normal_emp, status='APPROVED')
        del_url = reverse('delete_attendance', kwargs={'attendance_id': att.id})

        # GET request is rejected (405 Method Not Allowed)
        res = self.client.get(del_url)
        self.assertEqual(res.status_code, 405)

        # POST request succeeds
        res = self.client.post(del_url)
        self.assertEqual(res.status_code, 302)
        self.assertFalse(Attendance.objects.filter(id=att.id).exists())

    def test_hr_wrench_hidden_and_admin_visible(self):
        """HR attendance page hides wrench icon; Admin attendance page shows it for service logs"""
        test_date = date(2026, 6, 2)
        p = Project.objects.create(name='P Wrench', category='ANCHORAGE')
        log = EmployeeServiceLog.objects.create(
            employee=self.normal_emp,
            project=p,
            date=test_date,
            vessel_name='MV Wrench',
            start_time=time(8, 0),
            end_time=time(16, 0),
            total_hours=Decimal('8.00')
        )

        # Admin view contains service_log_edit
        self.client.login(username='admin_user', password='password123')
        res_admin = self.client.get(f"{reverse('attendance_list_adminview')}?start=2026-06-02&end=2026-06-02")
        self.assertContains(res_admin, f"/service-logs/edit/{log.id}/")

        # HR view does NOT contain service_log_edit
        self.client.login(username='hr_user', password='password123')
        res_hr = self.client.get(f"{reverse('hr_attendance_list_view')}?start=2026-06-02&end=2026-06-02")
        self.assertNotContains(res_hr, f"/service-logs/edit/{log.id}/")

    def test_hr_edit_attendance_blank_dates(self):
        """HR edit attendance with empty/blank datetime fields does not raise ValidationError"""
        att = Attendance.objects.create(
            employee=self.normal_emp,
            status='PENDING'
        )
        self.client.login(username='hr_user', password='password123')
        url = reverse('hr_edit_attendance', kwargs={'attendance_id': att.id})

        post_data = {
            'employee': str(self.normal_emp.id),
            'project': '',
            'login_time': '2026-10-07T09:00',
            'log_out_time': '',
            'location': 'BDO',
            'attendance_status': 'PRESENT',
            'status': 'APPROVED',
            'rejection_reason': '',
        }
        res = self.client.post(url, post_data)
        self.assertEqual(res.status_code, 302)

        att.refresh_from_db()
        self.assertEqual(att.status, 'APPROVED')
        self.assertIsNotNone(att.login_time)
        self.assertIsNone(att.log_out_time)
        self.assertIsNone(att.project)

    def test_hr_edit_attendance_project_options(self):
        """HR edit attendance page renders '-- No Project --' selected when attendance has no project, and selects project when it does."""
        proj = Project.objects.create(name="Project Alpha")
        att_no_proj = Attendance.objects.create(
            employee=self.normal_emp,
            status='PENDING'
        )
        self.client.login(username='hr_user', password='password123')
        url = reverse('hr_edit_attendance', kwargs={'attendance_id': att_no_proj.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, '<option value="" selected>-- No Project --</option>')

        # With project assigned
        att_no_proj.project = proj
        att_no_proj.save()
        res_with_proj = self.client.get(url)
        self.assertEqual(res_with_proj.status_code, 200)
        self.assertContains(res_with_proj, '<option value="" >-- No Project --</option>')
        self.assertContains(res_with_proj, f'<option value="{proj.id}" selected>')

    def test_attendance_register_page_size_ten(self):
        """Attendance register table paginates at 10 records per page"""
        self.client.login(username='admin_user', password='password123')
        test_date = date(2026, 6, 1)
        for i in range(15):
            d = test_date + timedelta(days=i)
            login_dt = pytz.timezone('Asia/Dubai').localize(datetime.combine(d, time(9, 0)))
            logout_dt = pytz.timezone('Asia/Dubai').localize(datetime.combine(d, time(18, 0)))
            Attendance.objects.create(
                employee=self.normal_emp,
                login_time=login_dt,
                log_out_time=logout_dt,
                total_hours_of_work=Decimal('9.00'),
                location='BDO',
                status='APPROVED'
            )
        res = self.client.get(f"{reverse('attendance_list_adminview')}?start=2026-06-01&end=2026-06-15")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context['page_obj'].paginator.per_page, 10)
        self.assertEqual(len(res.context['rows']), 10)



class AttendanceSummaryTests(TestCase):
    def setUp(self):
        self.dubai_tz = pytz.timezone('Asia/Dubai')
        self.client = Client()

        self.superuser = User.objects.create_superuser(username='admin_sum_user', password='password123', email='admin_sum@test.com')
        self.hr_user = User.objects.create_user(username='hr_sum_user', password='password123')
        self.hr_emp = Employee.objects.create(user=self.hr_user, is_hr=True)

        self.normal_user = User.objects.create_user(username='normal_sum_user', password='password123')
        self.normal_emp = Employee.objects.create(user=self.normal_user, is_employee=True, rank='Marine Surveyor')

        self.user2 = User.objects.create_user(username='alice_sum_user', first_name='Alice', last_name='Smith', password='password123')
        self.emp2 = Employee.objects.create(user=self.user2, is_employee=True, rank='Inspector')

    def test_summary_work_counts(self):
        """an employee with 2 Office, 1 Vessel, 1 Office+Vessel day → counts 2/1/1"""
        rows = [
            {'employee': self.normal_emp, 'name': 'Normal User', 'status_code': 'OFFICE', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.normal_emp, 'name': 'Normal User', 'status_code': 'OFFICE', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.normal_emp, 'name': 'Normal User', 'status_code': 'VESSEL', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.normal_emp, 'name': 'Normal User', 'status_code': 'OFFICE_VESSEL', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
        ]
        summary_rows, totals = build_attendance_summary(rows)
        self.assertEqual(len(summary_rows), 1)
        r = summary_rows[0]
        self.assertEqual(r['office_days'], 2)
        self.assertEqual(r['vessel_days'], 1)
        self.assertEqual(r['office_vessel_days'], 1)

    def test_summary_leave_counts(self):
        """2 Annual + 1 Sick + 1 Personal → total_leave_days 4; REST and Training not counted as leave"""
        rows = [
            {'employee': self.normal_emp, 'name': 'Normal User', 'status_code': 'ANNUAL_LEAVE', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.normal_emp, 'name': 'Normal User', 'status_code': 'ANNUAL_LEAVE', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.normal_emp, 'name': 'Normal User', 'status_code': 'SICK_LEAVE', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.normal_emp, 'name': 'Normal User', 'status_code': 'PERSONAL_LEAVE', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.normal_emp, 'name': 'Normal User', 'status_code': 'REST', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.normal_emp, 'name': 'Normal User', 'status_code': 'TRAINING', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
        ]
        summary_rows, totals = build_attendance_summary(rows)
        self.assertEqual(len(summary_rows), 1)
        r = summary_rows[0]
        self.assertEqual(r['annual_leave_days'], 2)
        self.assertEqual(r['sick_leave_days'], 1)
        self.assertEqual(r['personal_leave_days'], 1)
        self.assertEqual(r['rest_days'], 1)
        self.assertEqual(r['training_days'], 1)
        self.assertEqual(r['total_leave_days'], 4)

    def test_summary_sunday_worked_and_unworked(self):
        """a Sunday not worked → sunday_days 1; a Sunday worked → office_days +1, off_day_worked 1, sunday_days unchanged"""
        rows = [
            {'employee': self.normal_emp, 'name': 'Normal User', 'status_code': 'SUNDAY', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': True},
            {'employee': self.normal_emp, 'name': 'Normal User', 'status_code': 'OFFICE', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': True},
        ]
        summary_rows, totals = build_attendance_summary(rows)
        self.assertEqual(len(summary_rows), 1)
        r = summary_rows[0]
        self.assertEqual(r['sunday_days'], 1)
        self.assertEqual(r['office_days'], 1)
        self.assertEqual(r['off_day_worked'], 1)

    def test_summary_overtime_calculation(self):
        """OT of 3h + 7h → gross_ot_hours 10.00, gross_ot_days 1.25"""
        rows = [
            {'employee': self.normal_emp, 'name': 'Normal User', 'status_code': 'OFFICE', 'total_hours': Decimal('11.00'), 'ot_hours': Decimal('3.00'), 'is_off_day': False},
            {'employee': self.normal_emp, 'name': 'Normal User', 'status_code': 'OFFICE', 'total_hours': Decimal('15.00'), 'ot_hours': Decimal('7.00'), 'is_off_day': False},
        ]
        summary_rows, totals = build_attendance_summary(rows)
        r = summary_rows[0]
        self.assertEqual(r['gross_ot_hours'], Decimal('10.00'))
        self.assertEqual(r['gross_ot_days'], Decimal('1.25'))

    def test_summary_absence_calculation(self):
        """2 absent days → absent_hours 16.00"""
        rows = [
            {'employee': self.normal_emp, 'name': 'Normal User', 'status_code': 'ABSENT', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.normal_emp, 'name': 'Normal User', 'status_code': 'ABSENT', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
        ]
        summary_rows, totals = build_attendance_summary(rows)
        r = summary_rows[0]
        self.assertEqual(r['absent_days'], 2)
        self.assertEqual(r['absent_hours'], Decimal('16.00'))

    def test_summary_manual_rest_override(self):
        """a manual REST override on a punched day → counted as REST, not Office"""
        test_date = date(2026, 6, 2)  # Tuesday
        login_dt = self.dubai_tz.localize(datetime.combine(test_date, time(9, 0)))
        logout_dt = self.dubai_tz.localize(datetime.combine(test_date, time(17, 0)))

        Attendance.objects.create(
            employee=self.normal_emp,
            login_time=login_dt,
            log_out_time=logout_dt,
            total_hours_of_work=Decimal('8.00'),
            location='BDO',
            status='APPROVED'
        )
        DailyAttendanceStatus.objects.create(
            employee=self.normal_emp,
            date=test_date,
            status='REST',
            note='Emergency rest granted'
        )

        rows = build_attendance_register(test_date, test_date, search=self.normal_emp.user.username)
        summary_rows, totals = build_attendance_summary(rows)
        self.assertEqual(len(summary_rows), 1)
        r = summary_rows[0]
        self.assertEqual(r['rest_days'], 1)
        self.assertEqual(r['office_days'], 0)

    def test_summary_totals_equal_column_sums(self):
        """totals row equals the column sums"""
        rows = [
            {'employee': self.normal_emp, 'name': 'Normal User', 'status_code': 'OFFICE', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('2.00'), 'is_off_day': False},
            {'employee': self.normal_emp, 'name': 'Normal User', 'status_code': 'VESSEL', 'total_hours': Decimal('9.00'), 'ot_hours': Decimal('1.00'), 'is_off_day': False},
            {'employee': self.emp2, 'name': 'Alice Smith', 'status_code': 'ANNUAL_LEAVE', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.emp2, 'name': 'Alice Smith', 'status_code': 'ABSENT', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
        ]
        summary_rows, totals = build_attendance_summary(rows)
        self.assertEqual(len(summary_rows), 2)
        self.assertEqual(totals['name'], 'TOTAL')
        self.assertEqual(totals['designation'], '')
        self.assertEqual(totals['office_days'], sum(r['office_days'] for r in summary_rows))
        self.assertEqual(totals['vessel_days'], sum(r['vessel_days'] for r in summary_rows))
        self.assertEqual(totals['office_vessel_days'], sum(r['office_vessel_days'] for r in summary_rows))
        self.assertEqual(totals['annual_leave_days'], sum(r['annual_leave_days'] for r in summary_rows))
        self.assertEqual(totals['total_leave_days'], sum(r['total_leave_days'] for r in summary_rows))
        self.assertEqual(totals['total_hours'], sum((r['total_hours'] for r in summary_rows), Decimal('0.00')))
        self.assertEqual(totals['gross_ot_hours'], sum((r['gross_ot_hours'] for r in summary_rows), Decimal('0.00')))
        self.assertEqual(totals['gross_ot_days'], sum((r['gross_ot_days'] for r in summary_rows), Decimal('0.00')))
        self.assertEqual(totals['absent_days'], sum(r['absent_days'] for r in summary_rows))
        self.assertEqual(totals['absent_hours'], sum((r['absent_hours'] for r in summary_rows), Decimal('0.00')))

    def test_summary_status_filter_and_search(self):
        """the Status filter doesn't change the summary; Search does"""
        test_date = date(2026, 6, 2)
        Attendance.objects.create(
            employee=self.normal_emp,
            login_time=self.dubai_tz.localize(datetime.combine(test_date, time(9, 0))),
            log_out_time=self.dubai_tz.localize(datetime.combine(test_date, time(17, 0))),
            total_hours_of_work=Decimal('8.00'),
            location='BDO',
            status='APPROVED'
        )

        self.client.login(username='admin_sum_user', password='password123')
        base_url = reverse('attendance_list_adminview')

        # 1. Base query
        res_all = self.client.get(f"{base_url}?start=2026-06-02&end=2026-06-02")
        all_summary = res_all.context['summary_rows']

        # 2. Status filter query (e.g. status=Absent)
        res_status = self.client.get(f"{base_url}?start=2026-06-02&end=2026-06-02&status=Absent")
        status_summary = res_status.context['summary_rows']
        # Summary rows must be identical despite status filter
        self.assertEqual(len(all_summary), len(status_summary))
        self.assertEqual([s['office_days'] for s in all_summary], [s['office_days'] for s in status_summary])
        self.assertContains(res_status, "Summary shows all statuses for the selected dates.")

        # 3. Search query
        res_search = self.client.get(f"{base_url}?start=2026-06-02&end=2026-06-02&search={self.normal_emp.user.username}")
        search_summary = res_search.context['summary_rows']
        self.assertEqual(len(search_summary), 1)
        self.assertEqual(search_summary[0]['name'], self.normal_emp.user.get_full_name() or self.normal_emp.user.username)

    def test_summary_excel_export_permissions_and_headers(self):
        """the Excel export returns 200 with the right headers for admin/HR and 403 for a normal employee"""
        import io
        import openpyxl

        admin_url_excel = f"{reverse('attendance_list_adminview')}?start=2026-06-02&end=2026-06-02&export=summary_excel"
        admin_url_csv = f"{reverse('attendance_list_adminview')}?start=2026-06-02&end=2026-06-02&export=summary_csv"
        hr_url_excel = f"{reverse('hr_attendance_list_view')}?start=2026-06-02&end=2026-06-02&export=summary_excel"
        hr_url_csv = f"{reverse('hr_attendance_list_view')}?start=2026-06-02&end=2026-06-02&export=summary_csv"

        # Normal user -> 403 Forbidden
        self.client.login(username='normal_sum_user', password='password123')
        res_norm_admin = self.client.get(admin_url_excel)
        self.assertEqual(res_norm_admin.status_code, 403)
        res_norm_hr = self.client.get(hr_url_excel)
        self.assertEqual(res_norm_hr.status_code, 403)

        # Admin user -> 200 Excel (.xlsx)
        self.client.login(username='admin_sum_user', password='password123')
        res_admin = self.client.get(admin_url_excel)
        self.assertEqual(res_admin.status_code, 200)
        self.assertEqual(res_admin['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('attendance_summary_2026-06-02_2026-06-02.xlsx', res_admin['Content-Disposition'])

        # Verify workbook content
        wb = openpyxl.load_workbook(io.BytesIO(res_admin.content))
        self.assertEqual(wb.active.title, "Attendance Summary")
        ws = wb.active
        self.assertEqual(ws["A1"].value, "Employee")
        self.assertEqual(ws["B1"].value, "Designation")
        self.assertEqual(ws["C1"].value, "Work")

        # Admin user via ?export=summary_csv also returns Excel
        res_admin_csv = self.client.get(admin_url_csv)
        self.assertEqual(res_admin_csv.status_code, 200)
        self.assertEqual(res_admin_csv['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

        # HR user -> 200 Excel (.xlsx)
        self.client.login(username='hr_sum_user', password='password123')
        res_hr = self.client.get(hr_url_excel)
        self.assertEqual(res_hr.status_code, 200)
        self.assertEqual(res_hr['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('attendance_summary_2026-06-02_2026-06-02.xlsx', res_hr['Content-Disposition'])

        res_hr_csv = self.client.get(hr_url_csv)
        self.assertEqual(res_hr_csv.status_code, 200)
        self.assertEqual(res_hr_csv['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def test_summary_no_extra_queries(self):
        """No extra queries: build_attendance_summary makes 0 database queries"""
        test_date = date(2026, 6, 2)
        all_rows = build_attendance_register(test_date, test_date)
        with CaptureQueriesContext(connection) as ctx:
            build_attendance_summary(all_rows)
        self.assertEqual(len(ctx), 0)


class AttendanceAnalyticsTests(TestCase):
    def setUp(self):
        self.dubai_tz = pytz.timezone('Asia/Dubai')
        self.client = Client()

        self.superuser = User.objects.create_superuser(username='super_an_user', password='password123', email='super_an@test.com')
        self.hr_user = User.objects.create_user(username='hr_an_user', password='password123')
        self.hr_emp = Employee.objects.create(user=self.hr_user, is_hr=True)

        self.manager_user = User.objects.create_user(username='mgr_an_user', password='password123')
        self.manager_emp = Employee.objects.create(user=self.manager_user, is_manager=True, rank='Operations Manager')

        self.emp1 = Employee.objects.create(
            user=User.objects.create_user(username='emp1_user', first_name='John', last_name='Doe', password='password123'),
            is_employee=True, rank='Marine Surveyor'
        )
        self.emp2 = Employee.objects.create(
            user=User.objects.create_user(username='emp2_user', first_name='Jane', last_name='Smith', password='password123'),
            is_employee=True, rank='Inspector'
        )

    def test_kpi_attendance_rate_matches_formula(self):
        """KPI attendance % equals the existing per-employee formula for the same rows"""
        start_d = date(2026, 6, 1)  # Monday
        end_d = date(2026, 6, 5)    # Friday

        # emp1: 2 OFFICE, 1 ANNUAL_LEAVE, 1 TRAINING, 1 SUNDAY
        # working_days: 4 (SUNDAY is off), leave: 1, training: 1 => denom = 4 - 1 - 1 = 2. worked = 2.
        # emp2: 1 VESSEL, 1 ABSENT, 1 OFFICE_VESSEL, 1 REST, 1 SUNDAY
        # working_days: 4, leave: 0, training: 0 => denom = 4. worked = 2 (VESSEL, OFFICE_VESSEL).
        # Total expected days = 2 + 4 = 6. Total worked days = 4. Rate = (4 / 6) * 100 = 66.67%.
        rows = [
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 6, 1), 'status_code': 'OFFICE', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 6, 2), 'status_code': 'OFFICE', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 6, 3), 'status_code': 'ANNUAL_LEAVE', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 6, 4), 'status_code': 'TRAINING', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 6, 5), 'status_code': 'SUNDAY', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},

            {'employee': self.emp2, 'name': 'Jane Smith', 'date': date(2026, 6, 1), 'status_code': 'VESSEL', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.emp2, 'name': 'Jane Smith', 'date': date(2026, 6, 2), 'status_code': 'ABSENT', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.emp2, 'name': 'Jane Smith', 'date': date(2026, 6, 3), 'status_code': 'OFFICE_VESSEL', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.emp2, 'name': 'Jane Smith', 'date': date(2026, 6, 4), 'status_code': 'REST', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.emp2, 'name': 'Jane Smith', 'date': date(2026, 6, 5), 'status_code': 'SUNDAY', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
        ]

        att = build_attendance_analytics(rows, [], None, start_d, end_d)
        kpi_att_rate = att['kpis']['attendance_rate']['value']
        self.assertEqual(kpi_att_rate, Decimal('66.67'))
        self.assertEqual(att['kpis']['absenteeism_rate']['value'], Decimal('16.67'))

    def test_deltas_vs_previous_period(self):
        """deltas vs a previous period, including the None case when the previous value is 0"""
        start_d = date(2026, 6, 1)
        end_d = date(2026, 6, 2)

        curr_rows = [
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 6, 1), 'status_code': 'OFFICE', 'total_hours': Decimal('10.00'), 'ot_hours': Decimal('2.00'), 'is_off_day': False},
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 6, 2), 'status_code': 'OFFICE', 'total_hours': Decimal('10.00'), 'ot_hours': Decimal('2.00'), 'is_off_day': False},
        ]
        # Prev period: 1 office (8h, 0 OT), 1 absent (0h)
        prev_rows = [
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 5, 30), 'status_code': 'OFFICE', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 5, 31), 'status_code': 'ABSENT', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00'), 'is_off_day': False},
        ]

        att = build_attendance_analytics(curr_rows, prev_rows, None, start_d, end_d)
        # Attendance: curr=100.0%, prev=50.0% => delta = +50.0%
        self.assertEqual(att['kpis']['attendance_rate']['delta'], Decimal('50.0'))
        self.assertTrue(att['kpis']['attendance_rate']['is_good'])

        # Absenteeism: curr=0.0%, prev=50.0% => delta = -50.0% (good because lower is better)
        self.assertEqual(att['kpis']['absenteeism_rate']['delta'], Decimal('-50.0'))
        self.assertTrue(att['kpis']['absenteeism_rate']['is_good'])

        # Gross OT hours: curr=4.0, prev=0.0 => delta is None because previous value was 0
        self.assertIsNone(att['kpis']['gross_ot_hours']['delta'])
        self.assertIsNone(att['kpis']['gross_ot_hours']['is_good'])

    def test_daily_mix_sums_to_employee_count(self):
        """the daily mix counts per date sum to the number of employees that day"""
        start_d = date(2026, 6, 1)
        end_d = date(2026, 6, 3)

        rows = [
            # Day 1: emp1 Office, emp2 Vessel -> 2 employees
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 6, 1), 'status_code': 'OFFICE'},
            {'employee': self.emp2, 'name': 'Jane Smith', 'date': date(2026, 6, 1), 'status_code': 'VESSEL'},

            # Day 2: emp1 Leave, emp2 Absent -> 2 employees
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 6, 2), 'status_code': 'ANNUAL_LEAVE'},
            {'employee': self.emp2, 'name': 'Jane Smith', 'date': date(2026, 6, 2), 'status_code': 'ABSENT'},

            # Day 3: emp1 Training, emp2 REST -> 2 employees
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 6, 3), 'status_code': 'TRAINING'},
            {'employee': self.emp2, 'name': 'Jane Smith', 'date': date(2026, 6, 3), 'status_code': 'REST'},
        ]

        att = build_attendance_analytics(rows, [], None, start_d, end_d)
        mix = att['daily_mix']
        self.assertEqual(len(mix['labels']), 3)

        for i in range(3):
            total_mix_count = (
                mix['office'][i] + mix['vessel'][i] + mix['office_vessel'][i] +
                mix['leave'][i] + mix['absent'][i] + mix['rest'][i] + mix['training'][i]
            )
            self.assertEqual(total_mix_count, 2)

    def test_heatmap_none_over_62_days(self):
        """the heatmap is None for a range over 62 days"""
        # 63 days
        start_d = date(2026, 1, 1)
        end_d = date(2026, 3, 5)  # 31 + 28 + 5 = 64 days > 62
        att_long = build_attendance_analytics([], [], None, start_d, end_d)
        self.assertIsNone(att_long['heatmap'])

        # 62 days
        end_d_62 = start_d + timedelta(days=61)  # 62 days inclusive
        att_short = build_attendance_analytics([], [], None, start_d, end_d_62)
        self.assertIsNotNone(att_short['heatmap'])

    def test_ot_leaderboard_and_scaling(self):
        """an OT leaderboard with 12 people returns 10, sorted; ot_alert scales with the range length"""
        start_d = date(2026, 6, 1)
        end_d_30 = start_d + timedelta(days=29)  # 30 days

        # Create 12 employees with differing OT hours
        rows = []
        for i in range(1, 13):
            u = User.objects.create_user(username=f'ot_user_{i}', password='password123')
            emp = Employee.objects.create(user=u, is_employee=True, rank='Technician')
            # Give OT hours: i * 10 hrs (10, 20, 30, ..., 120)
            rows.append({
                'employee': emp,
                'name': f'OT User {i}',
                'date': start_d,
                'status_code': 'OFFICE',
                'total_hours': Decimal(i * 10),
                'ot_hours': Decimal(i * 10),
                'is_off_day': False
            })

        # 30 days: threshold = 40 * (30/30) = 40 hours. Alert for OT > 40.
        att_30 = build_attendance_analytics(rows, [], None, start_d, end_d_30)
        lb = att_30['leaderboard']
        self.assertEqual(len(lb), 10)
        # Sorted descending: top is 120, 2nd is 110, ..., 10th is 30.
        self.assertEqual(lb[0]['ot_hours'], Decimal('120'))
        self.assertEqual(lb[9]['ot_hours'], Decimal('30'))

        # Emp with 50 hrs has alert True (> 40)
        item_50 = next(item for item in lb if item['ot_hours'] == Decimal('50'))
        self.assertTrue(item_50['ot_alert'])

        # Emp with 30 hrs has alert False (<= 40)
        item_30 = next(item for item in lb if item['ot_hours'] == Decimal('30'))
        self.assertFalse(item_30['ot_alert'])

        # 60 days: threshold = 40 * (60/30) = 80 hours. Alert for OT > 80.
        end_d_60 = start_d + timedelta(days=59)
        att_60 = build_attendance_analytics(rows, [], None, start_d, end_d_60)
        lb_60 = att_60['leaderboard']
        # Emp with 80 hrs has alert False (not > 80)
        item_80 = next(item for item in lb_60 if item['ot_hours'] == Decimal('80'))
        self.assertFalse(item_80['ot_alert'])
        # Emp with 90 hrs has alert True (> 80)
        item_90 = next(item for item in lb_60 if item['ot_hours'] == Decimal('90'))
        self.assertTrue(item_90['ot_alert'])

    def test_absence_spells_and_bradford(self):
        """spells: ABSENT, SUNDAY, ABSENT counts as 1 spell of 2 days; ABSENT, OFFICE, ABSENT counts as 2 spells; Bradford = spells² × days"""
        start_d = date(2026, 6, 1)
        end_d = date(2026, 6, 3)

        # emp1: ABSENT, SUNDAY, ABSENT
        # emp2: ABSENT, OFFICE, ABSENT
        rows = [
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 6, 1), 'status_code': 'ABSENT'},
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 6, 2), 'status_code': 'SUNDAY'},
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 6, 3), 'status_code': 'ABSENT'},

            {'employee': self.emp2, 'name': 'Jane Smith', 'date': date(2026, 6, 1), 'status_code': 'ABSENT'},
            {'employee': self.emp2, 'name': 'Jane Smith', 'date': date(2026, 6, 2), 'status_code': 'OFFICE'},
            {'employee': self.emp2, 'name': 'Jane Smith', 'date': date(2026, 6, 3), 'status_code': 'ABSENT'},
        ]

        att = build_attendance_analytics(rows, [], None, start_d, end_d)
        wl = att['watchlist']
        self.assertEqual(len(wl), 2)

        wl_emp1 = next(item for item in wl if item['employee'].id == self.emp1.id)
        self.assertEqual(wl_emp1['absent_days'], 2)
        self.assertEqual(wl_emp1['spells'], 1)
        self.assertEqual(wl_emp1['longest_spell'], 2)
        self.assertEqual(wl_emp1['bradford_factor'], 2)  # 1^2 * 2 = 2

        wl_emp2 = next(item for item in wl if item['employee'].id == self.emp2.id)
        self.assertEqual(wl_emp2['absent_days'], 2)
        self.assertEqual(wl_emp2['spells'], 2)
        self.assertEqual(wl_emp2['longest_spell'], 1)
        self.assertEqual(wl_emp2['bradford_factor'], 8)  # 2^2 * 2 = 8

    def test_work_type_filter_limits_all_sections(self):
        """the work_type filter limits all sections"""
        start_d = date(2026, 6, 1)
        end_d = date(2026, 6, 2)

        rows = [
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 6, 1), 'status_code': 'OFFICE', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('2.00')},
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 6, 2), 'status_code': 'OFFICE', 'total_hours': Decimal('8.00'), 'ot_hours': Decimal('2.00')},

            {'employee': self.emp2, 'name': 'Jane Smith', 'date': date(2026, 6, 1), 'status_code': 'ABSENT', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00')},
            {'employee': self.emp2, 'name': 'Jane Smith', 'date': date(2026, 6, 2), 'status_code': 'ABSENT', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00')},
        ]

        # Filter strictly by emp1
        att = build_attendance_analytics(rows, [], [self.emp1.id], start_d, end_d)
        # Heatmap only has emp1
        self.assertEqual(len(att['heatmap']['rows']), 1)
        self.assertEqual(att['heatmap']['rows'][0]['employee'].id, self.emp1.id)
        # Leaderboard only has emp1
        self.assertEqual(len(att['leaderboard']), 1)
        self.assertEqual(att['leaderboard'][0]['employee'].id, self.emp1.id)
        # Watchlist should be empty because emp1 has 0 absences
        self.assertEqual(len(att['watchlist']), 0)
        # Daily mix should only count emp1 (Office count 1 on both days, Absent count 0)
        self.assertEqual(att['daily_mix']['office'], [1, 1])
        self.assertEqual(att['daily_mix']['absent'], [0, 0])

    def test_attendance_tab_rendering_and_roles(self):
        """tab=attendance renders for superuser, HR and manager; for a manager, register_url is None and no register links render"""
        url_admin = f"{reverse('analytics_employee')}?tab=attendance"
        url_hr = f"{reverse('hr_employee_analytics')}?tab=attendance"

        # Superuser -> 200, register_url is attendance_list_adminview
        self.client.login(username='super_an_user', password='password123')
        res_super = self.client.get(url_admin)
        self.assertEqual(res_super.status_code, 200)
        self.assertIn('register_url', res_super.context)
        self.assertEqual(res_super.context['register_url'], reverse('attendance_list_adminview'))
        self.assertContains(res_super, f"{reverse('attendance_list_adminview')}?start=")
        self.assertContains(res_super, 'class="att-an-emp-link"')

        # HR -> 200, register_url is hr_attendance_list_view
        self.client.login(username='hr_an_user', password='password123')
        res_hr = self.client.get(url_hr)
        self.assertEqual(res_hr.status_code, 200)
        self.assertIn('register_url', res_hr.context)
        self.assertEqual(res_hr.context['register_url'], reverse('hr_attendance_list_view'))
        self.assertContains(res_hr, f"{reverse('hr_attendance_list_view')}?start=")
        self.assertContains(res_hr, 'class="att-an-emp-link"')

        # Manager -> 200 on service-logs view, register_url is None, no register links render
        self.client.login(username='mgr_an_user', password='password123')
        res_mgr = self.client.get(url_admin)
        self.assertEqual(res_mgr.status_code, 200)
        self.assertIn('register_url', res_mgr.context)
        self.assertIsNone(res_mgr.context['register_url'])
        self.assertNotContains(res_mgr, f"{reverse('attendance_list_adminview')}?start=")
        self.assertNotContains(res_mgr, f"{reverse('hr_attendance_list_view')}?start=")
        self.assertNotContains(res_mgr, 'class="att-an-emp-link"')

    def test_future_dates_produce_none_status_and_no_link(self):
        """Analytics range that ends after today -> future dates produce status_code 'NONE',
        are not counted in KPIs, and the rendered page contains no register link for those cells."""
        today = timezone.localdate()
        start_d = today - timedelta(days=2)
        end_d = today + timedelta(days=2)

        # 1. Unit test on build_attendance_analytics
        rows = [
            {
                'employee': self.emp1,
                'name': 'John Doe',
                'date': today,
                'status_code': 'OFFICE',
                'total_hours': Decimal('8.00'),
                'ot_hours': Decimal('0.00'),
                'is_off_day': False,
            }
        ]
        att = build_attendance_analytics(rows, [], None, start_d, end_d)
        emp1_row = next(r for r in att['heatmap']['rows'] if r['employee'].id == self.emp1.id)
        future_cells = [c for c in emp1_row['cells'] if c['date'] > today]
        self.assertTrue(len(future_cells) > 0)
        for cell in future_cells:
            self.assertEqual(cell['status_code'], 'NONE')
            self.assertEqual(cell['status_label'], 'Upcoming')
            self.assertFalse(cell['is_clickable'])

        # Check KPIs: NONE cells must not be counted in expected working days or worked days
        self.assertEqual(att['kpis']['attendance_rate']['value'], Decimal('100.00'))

        # 2. Rendered page integration check (superuser who normally has register links)
        self.client.login(username='super_an_user', password='password123')
        date_param = f"{start_d.strftime('%Y-%m-%d')} to {end_d.strftime('%Y-%m-%d')}"
        res = self.client.get(f"{reverse('analytics_employee')}?tab=attendance&date={date_param}")
        self.assertEqual(res.status_code, 200)

        # The rendered HTML should contain .att-an-st-none spans without <a> tags for future dates
        self.assertContains(res, 'att-an-st-none')
        for cell in future_cells:
            cell_date_str = cell['date'].strftime('%Y-%m-%d')
            self.assertNotContains(res, f"start={cell_date_str}&end={cell_date_str}")

    def test_total_leave_days_delta_is_good_none(self):
        """total_leave_days KPI delta has is_good None (neutral color)"""
        start_d = date(2026, 6, 1)
        end_d = date(2026, 6, 5)

        curr_rows = [
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 6, 1), 'status_code': 'ANNUAL_LEAVE', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00')},
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 6, 2), 'status_code': 'ANNUAL_LEAVE', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00')},
        ]
        prev_rows = [
            {'employee': self.emp1, 'name': 'John Doe', 'date': date(2026, 5, 25), 'status_code': 'ANNUAL_LEAVE', 'total_hours': Decimal('0.00'), 'ot_hours': Decimal('0.00')},
        ]

        att = build_attendance_analytics(curr_rows, prev_rows, None, start_d, end_d)
        leave_kpi = att['kpis']['total_leave_days']
        self.assertEqual(leave_kpi['value'], 2)
        self.assertEqual(leave_kpi['prev_value'], 1)
        self.assertEqual(leave_kpi['delta'], Decimal('100.0'))
        self.assertIsNone(leave_kpi['is_good'])




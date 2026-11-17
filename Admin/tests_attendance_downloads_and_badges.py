import io
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import openpyxl

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from Admin.models import (
    Branch, Department, DutySchedule, Employee, EmployeeSchedule,
    RestDayRule, Attendance, DailyAttendanceStatus
)
from Admin.reports_center import build_report_data


class AttendanceDownloadsAndBadgesTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.today = date(2026, 10, 15)

        self.dept = Department.objects.create(name='Operations')
        self.branch = Branch.objects.create(name='Dubai')

        self.su_user = User.objects.create_superuser('su_att', 'su@test.com', 'pass123')

        self.hr_user = User.objects.create_user('hr_att', 'hr@test.com', 'pass123', first_name='Fatima', last_name='HR')
        self.hr_emp = Employee.objects.create(
            user=self.hr_user, is_hr=True, department=self.dept, branch=self.branch,
            salary=Decimal('10000.00'), date_of_join=date(2024, 1, 1), employee_code='AMMS-0001',
            work_type='OFFICE'
        )

        self.vessel_user = User.objects.create_user('vessel_tech', 'vt@test.com', 'pass123', first_name='John', last_name='Doe')
        self.vessel_emp = Employee.objects.create(
            user=self.vessel_user, is_employee=True, department=self.dept, branch=self.branch,
            salary=Decimal('7000.00'), date_of_join=date(2024, 1, 1), employee_code='AMMS-0002',
            work_type='VESSEL'
        )

        self.normal_user = User.objects.create_user('norm_user', 'nu@test.com', 'pass123', first_name='Normal', last_name='Staff')
        self.norm_emp = Employee.objects.create(
            user=self.normal_user, is_employee=True, department=self.dept, branch=self.branch,
            salary=Decimal('4000.00'), date_of_join=date(2024, 1, 1), employee_code='AMMS-0003',
            work_type='OFFICE'
        )

        sched, _ = DutySchedule.objects.get_or_create(
            name='Standard Schedule',
            defaults={'start_time': time(8, 0), 'end_time': time(17, 0), 'break_minutes': 60}
        )
        for e in (self.hr_emp, self.vessel_emp, self.norm_emp):
            EmployeeSchedule.objects.create(employee=e, duty_schedule=sched, effective_from=date(2024, 1, 1))
            RestDayRule.objects.create(employee=e, rest_weekday=6, effective_from=date(2024, 1, 1))

        # Create attendance for John Doe
        tz = timezone.get_current_timezone()
        login_dt = datetime.combine(date(2026, 10, 5), time(8, 0)).replace(tzinfo=tz)
        logout_dt = datetime.combine(date(2026, 10, 5), time(17, 0)).replace(tzinfo=tz)
        Attendance.objects.create(
            employee=self.vessel_emp,
            login_time=login_dt,
            log_out_time=logout_dt,
            total_hours_of_work=Decimal('8.00'),
            location='DMC'
        )

    def test_all_six_exports_admin(self):
        """Admin can download daily_excel, daily_pdf, calendar_excel, calendar_pdf, summary_excel, summary_pdf with 200 and proper filenames."""
        self.client.login(username='su_att', password='pass123')
        base_url = reverse('attendance_list_adminview')
        params = "?start=2026-10-01&end=2026-10-15&calendar_month=2026-10"

        # 1. Daily Excel
        res = self.client.get(f"{base_url}{params}&tab=daily&export=daily_excel")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('attendance_daily_2026-10-01_2026-10-15.xlsx', res['Content-Disposition'])

        # 2. Daily PDF
        res = self.client.get(f"{base_url}{params}&tab=daily&export=daily_pdf")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertIn('attendance_daily_2026-10-01_2026-10-15.pdf', res['Content-Disposition'])

        # 3. Calendar Excel
        res = self.client.get(f"{base_url}{params}&tab=calendar&export=calendar_excel")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('attendance_month_2026-10.xlsx', res['Content-Disposition'])

        # 4. Calendar PDF
        res = self.client.get(f"{base_url}{params}&tab=calendar&export=calendar_pdf")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertIn('attendance_month_2026-10.pdf', res['Content-Disposition'])

        # 5. Summary Excel
        res = self.client.get(f"{base_url}{params}&tab=summary&export=summary_excel")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('attendance_summary_2026-10-01_2026-10-15.xlsx', res['Content-Disposition'])

        # 6. Summary PDF
        res = self.client.get(f"{base_url}{params}&tab=summary&export=summary_pdf")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertIn('attendance_summary_2026-10-01_2026-10-15.pdf', res['Content-Disposition'])

    def test_all_six_exports_hr(self):
        """HR user can download all 6 exports via hr_attendance_list_view with 200."""
        self.client.login(username='hr_att', password='pass123')
        base_url = reverse('hr_attendance_list_view')
        params = "?start=2026-10-01&end=2026-10-15&calendar_month=2026-10"

        for exp, ext, ctype in [
            ('daily_excel', '.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('daily_pdf', '.pdf', 'application/pdf'),
            ('calendar_excel', '.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('calendar_pdf', '.pdf', 'application/pdf'),
            ('summary_excel', '.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('summary_pdf', '.pdf', 'application/pdf'),
        ]:
            res = self.client.get(f"{base_url}{params}&export={exp}")
            self.assertEqual(res.status_code, 200, f"Failed on export={exp}")
            self.assertEqual(res['Content-Type'], ctype)
            self.assertIn(ext, res['Content-Disposition'])

    def test_normal_employee_forbidden(self):
        """Standard employees get 403 Forbidden on attendance list exports."""
        self.client.login(username='norm_user', password='pass123')
        admin_url = reverse('attendance_list_adminview')
        hr_url = reverse('hr_attendance_list_view')

        for exp in ('daily_excel', 'daily_pdf', 'calendar_excel', 'calendar_pdf', 'summary_excel', 'summary_pdf'):
            res_a = self.client.get(f"{admin_url}?start=2026-10-01&end=2026-10-15&export={exp}")
            self.assertEqual(res_a.status_code, 403)
            res_h = self.client.get(f"{hr_url}?start=2026-10-01&end=2026-10-15&export={exp}")
            self.assertEqual(res_h.status_code, 403)

    def test_daily_excel_non_paginated_and_status_filtered(self):
        """Daily Excel exports ALL rows matching the filter (>10 rows), not just paginated first 10 rows."""
        self.client.login(username='su_att', password='pass123')

        # Create 14 DailyAttendanceStatus overrides with MISSING_PUNCH
        for i in range(1, 15):
            d = date(2026, 10, i)
            DailyAttendanceStatus.objects.create(
                employee=self.vessel_emp,
                date=d,
                status='MISSING_PUNCH',
                note='Test missing punch'
            )

        base_url = reverse('attendance_list_adminview')
        # Filter for MISSING_PUNCH across 14 days
        res = self.client.get(f"{base_url}?start=2026-10-01&end=2026-10-14&status=Missing+Punch&tab=daily&export=daily_excel")
        self.assertEqual(res.status_code, 200)

        wb = openpyxl.load_workbook(io.BytesIO(res.content))
        ws = wb.active
        self.assertEqual(ws.title, "Daily Attendance")

        # Row 1 is headers. Data rows start at Row 2.
        # Exactly 14 data rows + 1 TOTAL row = 16 rows
        data_rows = [r for r in ws.iter_rows(min_row=2, values_only=True) if r[0] and r[0] != 'TOTAL']
        self.assertEqual(len(data_rows), 14)
        for r in data_rows:
            self.assertEqual(r[1], 'John Doe')
            self.assertEqual(r[8], 'Missing Punch')

    def test_single_employee_summary_filename_and_title(self):
        """Filtering to a single employee via search includes employee's name in filename and title."""
        self.client.login(username='su_att', password='pass123')
        base_url = reverse('attendance_list_adminview')

        # Search for 'vessel_tech' (John Doe)
        res_pdf = self.client.get(f"{base_url}?start=2026-10-01&end=2026-10-15&search=vessel_tech&tab=summary&export=summary_pdf")
        self.assertEqual(res_pdf.status_code, 200)
        self.assertIn('attendance_summary_john_doe_2026-10-01_2026-10-15.pdf', res_pdf['Content-Disposition'])

        res_excel = self.client.get(f"{base_url}?start=2026-10-01&end=2026-10-15&search=vessel_tech&tab=summary&export=summary_excel")
        self.assertEqual(res_excel.status_code, 200)
        self.assertIn('attendance_summary_john_doe_2026-10-01_2026-10-15.xlsx', res_excel['Content-Disposition'])

    def test_range_warning_alert(self):
        """When date range > 31 days, Daily tab shows friendly warning."""
        self.client.login(username='su_att', password='pass123')
        base_url = reverse('attendance_list_adminview')

        # 35 days range
        res_warn = self.client.get(f"{base_url}?start=2026-09-01&end=2026-10-05&tab=daily")
        self.assertContains(res_warn, "Daily attendance is best viewed for up to 31 days. Large date ranges may take longer to download.")

        # 15 days range
        res_ok = self.client.get(f"{base_url}?start=2026-10-01&end=2026-10-15&tab=daily")
        self.assertNotContains(res_ok, "Daily attendance is best viewed for up to 31 days. Large date ranges may take longer to download.")

    def test_reports_center_work_type_filter(self):
        """Reports Center with work_type=VESSEL excludes OFFICE employees from daily_attendance report."""
        self.client.login(username='su_att', password='pass123')
        reports_url = reverse('admin_reports_center')
        res = self.client.get(f"{reports_url}?report=daily_attendance&start=2026-10-05&end=2026-10-05&work_type=VESSEL")
        self.assertEqual(res.status_code, 200)

        # Check preview rows in context or page
        rows = res.context['report_data']['rows']
        emp_names = [r[2] for r in rows]
        self.assertIn('John Doe', emp_names)
        self.assertNotIn('Fatima HR', emp_names)
        self.assertNotIn('Normal Staff', emp_names)

    def test_monthly_attendance_page_badges_and_location(self):
        """Monthly attendance page renders status badges using _status_badge.html and displays '-' for rest day location."""
        self.client.login(username='su_att', password='pass123')
        month_url = reverse('employee_attendance_month', args=[self.vessel_emp.id, '2026-10'])
        res = self.client.get(month_url)
        self.assertEqual(res.status_code, 200)

        # Check badge rendering
        self.assertContains(res, 'class="status')

        # Check that location for a rest day shows '-' rather than 'Holiday'
        # 11-Oct-2026 is a Sunday (rest day for vessel_emp)
        self.assertContains(res, '11/10/2026')
        # Check style.css contains .status.att-reg-missing-punch with background-color
        with open('Al_Mabrouk_crm/static/assets/css/style.css', 'r', encoding='utf-8') as f:
            css = f.read()
        self.assertIn('.status.att-reg-missing-punch', css)
        self.assertIn('background-color: #fee2e2;', css)

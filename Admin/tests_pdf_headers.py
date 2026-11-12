"""PDF headers use the company logo; timesheet exports; HR dashboard location; document badges."""
import datetime
import io
from decimal import Decimal

import openpyxl
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import TestCase
from django.utils import timezone

from Admin.models import Employee, Project, WorkOrder, WorkOrderTime
from Admin.templatetags.pdf_tags import logo_base64
from service_logs.models import EmployeeServiceLog

PDF_TEMPLATES = [
    'attendance/employee_attendance_pdf.html',
    'employees/employee_roster_pdf.html',
    'timesheets/timesheets_pdf.html',
    'timesheets/employee_monthly_timesheet_pdf.html',
    'payroll/payslip_pdf.html',
    'leave/leave_list_pdf.html',
    'leave/leave_statement_pdf.html',
    'reports/report_pdf.html',
]


class PdfHeaderLogoTests(TestCase):
    def test_logo_file_found(self):
        self.assertTrue(logo_base64(), 'assets/images/reportlogo.png not found by staticfiles')

    def test_every_pdf_header_uses_logo_not_company_text(self):
        import re
        from django.conf import settings
        import os
        for name in PDF_TEMPLATES:
            path = os.path.join(settings.BASE_DIR, 'templates', name)
            src = open(path, encoding='utf-8').read()
            self.assertIn('{% pdf_logo', src, name)
            # no hard-coded company name as a heading (h1 / *-title / company-name)
            self.assertIsNone(
                re.search(r'(<h1[^>]*>|class="(company-title|company-name|title)">)\s*Al Mabrouk', src, re.I),
                f'{name} still has the company name heading')

    def test_payslip_number_has_no_version(self):
        src = open('templates/payroll/payslip_pdf.html', encoding='utf-8').read()
        self.assertNotIn('(v{{ payslip.version }})', src)


class TimesheetExportTests(TestCase):
    def setUp(self):
        self.su = User.objects.create_superuser('su_ts', 'su_ts@example.com', 'pw')
        self.emp = Employee.objects.create(user=User.objects.create_user('eng_ts', 'ts@x.com', 'pw', first_name='Eng'),
                                           is_employee=True, salary=Decimal('3000'), date_of_join=datetime.date(2026, 1, 1))
        project = Project.objects.create(name='TS Project', status='ONGOING')
        wo = WorkOrder.objects.create(project=project, work_order_number='AMMS-26-777',
                                      assigned_date=datetime.date(2026, 9, 1), created_by=self.su)
        today = timezone.localdate()
        wot = WorkOrderTime.objects.create(work_order=wo, date=today, start_time=datetime.time(8, 0),
                                           finish_time=datetime.time(16, 0), employee=self.emp,
                                           description='Main engine overhaul')
        # the time row may already have made its service log; make sure one is linked
        if not EmployeeServiceLog.objects.filter(time_log=wot).exists():
            EmployeeServiceLog.objects.create(employee=self.emp, project=project, date=today, time_log=wot,
                                              start_time=datetime.time(8, 0), end_time=datetime.time(16, 0),
                                              total_hours=Decimal('8.00'))
        # and one with no work-order time row at all
        EmployeeServiceLog.objects.create(employee=self.emp, project=project, date=today,
                                          start_time=datetime.time(17, 0), end_time=datetime.time(18, 0),
                                          total_hours=Decimal('1.00'))
        self.client.force_login(self.su)

    def test_excel_export(self):
        for url in ('/hr/timesheet-approvals/?&export=excel', '/admin-panel/timesheet-approvals/?export=excel'):
            res = self.client.get(url)
            self.assertEqual(res.status_code, 200, url)
            ws = openpyxl.load_workbook(io.BytesIO(res.content)).active
            values = [c.value for row in ws.iter_rows() for c in row]
            self.assertIn('Main engine overhaul', values)

    def test_pdf_export(self):
        res = self.client.get('/hr/timesheet-approvals/?&export=pdf')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')

    def test_screen_shows_description(self):
        res = self.client.get('/hr/timesheet-approvals/')
        self.assertContains(res, 'Main engine overhaul')


class DocumentBadgeCssTests(TestCase):
    def test_bootstrap4_badge_classes_have_colours(self):
        from django.contrib.staticfiles import finders
        css = open(finders.find('assets/css/style.css'), encoding='utf-8').read()
        for cls in ('badge-danger', 'badge-warning', 'badge-info', 'badge-success', 'badge-secondary'):
            self.assertIn(f'.badge.{cls}', css)


class HrDashboardLocationTests(TestCase):
    def test_holiday_row_location_is_blank_not_holiday(self):
        from Admin.models import Holiday
        su = User.objects.create_superuser('su_hd', 'su_hd@example.com', 'pw')
        Employee.objects.create(user=User.objects.create_user('eng_hd', 'hd@x.com', 'pw', first_name='Eng'),
                                is_employee=True, salary=Decimal('3000'), date_of_join=datetime.date(2026, 1, 1))
        day = timezone.localdate()
        Holiday.objects.create(date=day, description='Test Holiday')
        self.client.force_login(su)
        res = self.client.get(f'/hr/dashboard/?date={day:%Y-%m-%d}&export=excel')
        self.assertEqual(res.status_code, 200)
        ws = openpyxl.load_workbook(io.BytesIO(res.content)).active
        header = [c.value for c in ws[4]]
        loc_col = header.index('Location')
        status_col = header.index('Status')
        rows = [r for r in ws.iter_rows(min_row=5, values_only=True) if r[1]]
        self.assertTrue(rows)
        for r in rows:
            if r[status_col] in ('Holiday', 'Rest Day', 'Sunday'):
                self.assertEqual(r[loc_col], '-')
        self.assertTrue(any(r[status_col] == 'Holiday' for r in rows))

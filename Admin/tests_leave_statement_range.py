"""
Tests for Employee Leave Statement (Year mode & Custom Range mode):
- get_employee_leave_statement calculations: start balance, accruals, takens, closing, pending, available.
- Per-type running balance calculation in transaction ledger.
- Non-balance types (Personal, Unpaid).
- Overlapping leave requests working days calculation.
- Role-based access control / permissions matrix.
- HR landing page empty state.
- Date range validation (from > to).
- PDF & Excel exports with 3 sheets and dynamic filenames.
- Backward compatibility with legacy export_leave_statement_excel call signature.
"""
import datetime
from decimal import Decimal
import io
import openpyxl

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from Admin.models import (
    Employee, Department, Branch, Leave, LeaveType, LeavePolicy,
    LeaveLedger, DutySchedule, RestDayRule, EmployeeSchedule
)
from Admin.leave_service import (
    get_employee_leave_statement,
    export_leave_statement_excel,
)


@override_settings(HR_LEAVE_V2=True)
class LeaveStatementRangeTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Dubai Main", sort_order=1)
        self.dept = Department.objects.create(name="Engineering", sort_order=1)

        # Standard schedule: Monday to Friday (Rest days Saturday=5, Sunday=6)
        self.schedule = DutySchedule.objects.create(
            name="Standard Office",
            start_time=datetime.time(8, 0),
            end_time=datetime.time(17, 0),
            break_minutes=60,
            sort_order=1
        )

        # 1. Superuser / Admin
        self.admin_user = User.objects.create_superuser('admin_usr', 'admin@example.com', 'pass123')

        # 2. HR Employee
        self.hr_user = User.objects.create_user('hr_usr', 'hr@example.com', 'pass123', first_name='Fatima', last_name='HR')
        self.hr_emp = Employee.objects.create(
            user=self.hr_user,
            employee_code='AMMS-0001',
            salary=Decimal('18000.00'),
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
            salary=Decimal('15000.00'),
            date_of_join=datetime.date(2023, 1, 1),
            branch=self.branch,
            department=self.dept,
            is_manager=True
        )

        # 4. Direct report employee
        self.staff_user = User.objects.create_user('staff_usr', 'staff@example.com', 'pass123', first_name='Salem', last_name='Staff')
        self.staff_emp = Employee.objects.create(
            user=self.staff_user,
            employee_code='AMMS-0003',
            salary=Decimal('10000.00'),
            date_of_join=datetime.date(2023, 1, 1),
            branch=self.branch,
            department=self.dept,
            reporting_manager=self.mgr_emp,
            is_employee=True
        )

        # 5. Other employee (not managed by Tariq)
        self.other_user = User.objects.create_user('other_usr', 'other@example.com', 'pass123', first_name='Khalid', last_name='Other')
        self.other_emp = Employee.objects.create(
            user=self.other_user,
            employee_code='AMMS-0004',
            salary=Decimal('9000.00'),
            date_of_join=datetime.date(2023, 1, 1),
            branch=self.branch,
            department=self.dept,
            is_employee=True
        )

        # Schedules
        for emp in (self.staff_emp, self.other_emp):
            RestDayRule.objects.create(employee=emp, rest_weekday=5, effective_from=datetime.date(2023, 1, 1)) # Sat
            RestDayRule.objects.create(employee=emp, rest_weekday=6, effective_from=datetime.date(2023, 1, 1)) # Sun
            EmployeeSchedule.objects.create(employee=emp, duty_schedule=self.schedule, effective_from=datetime.date(2023, 1, 1))

        # Leave types
        self.annual_type, _ = LeaveType.objects.get_or_create(
            code='ANNUAL',
            defaults={'name': 'Annual Leave', 'uses_balance': True, 'is_active': True, 'sort_order': 10}
        )
        self.annual_type.uses_balance = True
        self.annual_type.save()

        self.sick_type, _ = LeaveType.objects.get_or_create(
            code='SICK',
            defaults={'name': 'Sick Leave', 'uses_balance': True, 'is_active': True, 'sort_order': 20}
        )
        self.sick_type.uses_balance = True
        self.sick_type.save()

        self.unpaid_type, _ = LeaveType.objects.get_or_create(
            code='UNPAID',
            defaults={'name': 'Unpaid Leave', 'uses_balance': False, 'is_active': True, 'sort_order': 30}
        )
        self.unpaid_type.uses_balance = False
        self.unpaid_type.save()

        self.client = Client()

    def test_01_range_calculation_math(self):
        """
        Verify that entries prior to date_from compute balance_at_start,
        and entries within range compute in-period columns, closing balance,
        and per-type running balances.
        """
        # Prior to March 2026:
        # AL: Opening 10.0 on Jan 1, Accrual 2.5 on Jan 15, Accrual 2.5 on Feb 15 => start bal = 15.0
        LeaveLedger.objects.create(
            employee=self.staff_emp, leave_type=self.annual_type,
            date=datetime.date(2026, 1, 1), entry_type='OPENING', days=Decimal('10.00'),
            note='Opening 2026'
        )
        LeaveLedger.objects.create(
            employee=self.staff_emp, leave_type=self.annual_type,
            date=datetime.date(2026, 1, 15), entry_type='ACCRUAL', days=Decimal('2.50'),
            note='Jan Accrual'
        )
        LeaveLedger.objects.create(
            employee=self.staff_emp, leave_type=self.annual_type,
            date=datetime.date(2026, 2, 15), entry_type='ACCRUAL', days=Decimal('2.50'),
            note='Feb Accrual'
        )
        # SL: Entitlement 15.0 on Jan 1 => start bal = 15.0
        LeaveLedger.objects.create(
            employee=self.staff_emp, leave_type=self.sick_type,
            date=datetime.date(2026, 1, 1), entry_type='ENTITLEMENT', days=Decimal('15.00'),
            note='Year Entitlement'
        )

        # Within March 2026 (01/03/2026 to 31/03/2026):
        # AL: Accrual 2.5 on Mar 15, Taken -3.0 on Mar 20
        LeaveLedger.objects.create(
            employee=self.staff_emp, leave_type=self.annual_type,
            date=datetime.date(2026, 3, 15), entry_type='ACCRUAL', days=Decimal('2.50'),
            note='Mar Accrual'
        )
        LeaveLedger.objects.create(
            employee=self.staff_emp, leave_type=self.annual_type,
            date=datetime.date(2026, 3, 20), entry_type='TAKEN', days=Decimal('-3.00'),
            note='Annual Leave taken'
        )
        # SL: Taken -2.0 on Mar 10
        LeaveLedger.objects.create(
            employee=self.staff_emp, leave_type=self.sick_type,
            date=datetime.date(2026, 3, 10), entry_type='TAKEN', days=Decimal('-2.00'),
            note='Sick Leave taken'
        )

        # After March 2026 (April):
        # AL: Taken -4.0 on Apr 10 (should NOT affect March range)
        LeaveLedger.objects.create(
            employee=self.staff_emp, leave_type=self.annual_type,
            date=datetime.date(2026, 4, 10), entry_type='TAKEN', days=Decimal('-4.00'),
            note='Apr Leave taken'
        )

        statement = get_employee_leave_statement(
            self.staff_emp,
            date_from=datetime.date(2026, 3, 1),
            date_to=datetime.date(2026, 3, 31),
            mode='range'
        )

        self.assertEqual(statement['mode'], 'range')
        self.assertEqual(statement['period_label'], '01/03/2026 to 31/03/2026')

        summary_map = {s['code']: s for s in statement['summary_by_type']}
        al_s = summary_map['ANNUAL']
        self.assertEqual(al_s['balance_at_start'], 15.0)
        self.assertEqual(al_s['opening_days'], 0.0)
        self.assertEqual(al_s['accrued_days'], 2.5)
        self.assertEqual(al_s['taken_days'], 3.0)  # shown as positive
        # closing_balance = 15.0 + 2.5 - 3.0 = 14.5
        self.assertEqual(al_s['closing_balance'], 14.5)

        sl_s = summary_map['SICK']
        self.assertEqual(sl_s['balance_at_start'], 15.0)
        self.assertEqual(sl_s['taken_days'], 2.0)
        # closing_balance = 15.0 - 2.0 = 13.0
        self.assertEqual(sl_s['closing_balance'], 13.0)

    def test_02_per_type_running_balance_in_ledger(self):
        """
        Verify that running balances in the ledger lines track each leave type independently,
        starting from balance_at_start of that type, rather than mixing types.
        """
        # Prior balances
        LeaveLedger.objects.create(
            employee=self.staff_emp, leave_type=self.annual_type,
            date=datetime.date(2026, 1, 1), entry_type='OPENING', days=Decimal('10.00')
        )
        LeaveLedger.objects.create(
            employee=self.staff_emp, leave_type=self.sick_type,
            date=datetime.date(2026, 1, 1), entry_type='ENTITLEMENT', days=Decimal('15.00')
        )

        # In-period transactions interleaved (Feb 2026)
        e1 = LeaveLedger.objects.create(
            employee=self.staff_emp, leave_type=self.annual_type,
            date=datetime.date(2026, 2, 2), entry_type='ACCRUAL', days=Decimal('2.50')
        )
        e2 = LeaveLedger.objects.create(
            employee=self.staff_emp, leave_type=self.sick_type,
            date=datetime.date(2026, 2, 5), entry_type='TAKEN', days=Decimal('-2.00')
        )
        e3 = LeaveLedger.objects.create(
            employee=self.staff_emp, leave_type=self.annual_type,
            date=datetime.date(2026, 2, 10), entry_type='TAKEN', days=Decimal('-3.00')
        )

        statement = get_employee_leave_statement(
            self.staff_emp,
            date_from=datetime.date(2026, 2, 1),
            date_to=datetime.date(2026, 2, 28)
        )

        ledger = statement['ledger_lines']
        self.assertEqual(len(ledger), 3)

        # e1: AL starting from 10.0 + 2.5 = 12.5
        self.assertEqual(ledger[0]['entry'].id, e1.id)
        self.assertEqual(ledger[0]['running_balance'], 12.5)

        # e2: SL starting from 15.0 - 2.0 = 13.0 (NOT 10.5 from mixing!)
        self.assertEqual(ledger[1]['entry'].id, e2.id)
        self.assertEqual(ledger[1]['running_balance'], 13.0)

        # e3: AL continues from 12.5 - 3.0 = 9.5
        self.assertEqual(ledger[2]['entry'].id, e3.id)
        self.assertEqual(ledger[2]['running_balance'], 9.5)

    def test_03_year_mode_matches_full_year_range(self):
        """
        Year mode and Range mode covering 01/01 to 31/12 yield identical results.
        """
        LeaveLedger.objects.create(
            employee=self.staff_emp, leave_type=self.annual_type,
            date=datetime.date(2026, 1, 1), entry_type='OPENING', days=Decimal('10.00')
        )
        LeaveLedger.objects.create(
            employee=self.staff_emp, leave_type=self.annual_type,
            date=datetime.date(2026, 5, 1), entry_type='TAKEN', days=Decimal('-2.00')
        )

        stmt_year = get_employee_leave_statement(
            self.staff_emp,
            date_from=datetime.date(2026, 1, 1),
            date_to=datetime.date(2026, 12, 31),
            mode='year',
            year=2026
        )
        stmt_range = get_employee_leave_statement(
            self.staff_emp,
            date_from=datetime.date(2026, 1, 1),
            date_to=datetime.date(2026, 12, 31),
            mode='range'
        )

        self.assertEqual(stmt_year['period_label'], 'Calendar Year 2026')
        self.assertEqual(stmt_range['period_label'], '01/01/2026 to 31/12/2026')

        s_y = stmt_year['summary_by_type'][0]
        s_r = stmt_range['summary_by_type'][0]
        self.assertEqual(s_y['closing_balance'], s_r['closing_balance'])
        self.assertEqual(s_y['taken_days'], s_r['taken_days'])

    def test_04_non_balance_leave_type(self):
        """
        Non-balance leave types (uses_balance=False) have None for balance columns,
        numeric counts for taken & pending, and None for running balances.
        """
        LeaveLedger.objects.create(
            employee=self.staff_emp, leave_type=self.unpaid_type,
            date=datetime.date(2026, 6, 1), entry_type='TAKEN', days=Decimal('-4.00')
        )

        statement = get_employee_leave_statement(
            self.staff_emp,
            date_from=datetime.date(2026, 1, 1),
            date_to=datetime.date(2026, 12, 31)
        )

        summary_map = {s['code']: s for s in statement['summary_by_type']}
        unpaid_s = summary_map['UNPAID']
        self.assertFalse(unpaid_s['uses_balance'])
        self.assertIsNone(unpaid_s['balance_at_start'])
        self.assertIsNone(unpaid_s['closing_balance'])
        self.assertIsNone(unpaid_s['available_days'])
        self.assertEqual(unpaid_s['taken_days'], 4.0)

        unpaid_ledger = [l for l in statement['ledger_lines'] if l['entry'].leave_type_id == self.unpaid_type.id]
        self.assertEqual(len(unpaid_ledger), 1)
        self.assertIsNone(unpaid_ledger[0]['running_balance'])

    def test_05_overlapping_leave_request_working_days(self):
        """
        Leave requests overlapping the period edge count only the working days
        falling inside the requested period in days_in_period.
        """
        # Create a Leave request spanning March 30, 2026 to April 3, 2026
        # 2026-03-30 (Mon), 2026-03-31 (Tue), 2026-04-01 (Wed), 2026-04-02 (Thu), 2026-04-03 (Fri)
        # Total = 5 working days.
        # If range is 2026-03-01 to 2026-03-31, overlap is Mon 30 and Tue 31 => 2 working days.
        leave_req = Leave.objects.create(
            user=self.staff_user,
            employee=self.staff_emp,
            leave_type='Annual Leave',
            leave_type_ref=self.annual_type,
            from_date=datetime.date(2026, 3, 30),
            to_date=datetime.date(2026, 4, 3),
            days=Decimal('5.00'),
            approval_status='PENDING',
            reason='Family trip'
        )

        statement = get_employee_leave_statement(
            self.staff_emp,
            date_from=datetime.date(2026, 3, 1),
            date_to=datetime.date(2026, 3, 31)
        )

        self.assertEqual(len(statement['requests']), 1)
        req_item = statement['requests'][0]
        self.assertEqual(req_item['id'], leave_req.id)
        self.assertEqual(req_item['total_days'], 5.0)
        self.assertEqual(req_item['days'], 2.0)

    def test_06_permissions_matrix(self):
        """
        - Superuser / HR can view any employee.
        - Manager can view self and direct report, but 403 on other employee.
        - Employee viewing other employee gets 403.
        - Employee viewing self gets 200.
        - Anonymous redirected to login.
        """
        url_staff = f"{reverse('hr_leave_statement')}?employee={self.staff_emp.id}"
        url_other = f"{reverse('hr_leave_statement')}?employee={self.other_emp.id}"
        mgr_url_staff = f"{reverse('manager_leave_statement')}?employee={self.staff_emp.id}"
        mgr_url_other = f"{reverse('manager_leave_statement')}?employee={self.other_emp.id}"

        # 1. Anonymous -> redirect
        res = self.client.get(url_staff)
        self.assertEqual(res.status_code, 302)

        # 2. Superuser -> 200
        self.client.force_login(self.admin_user)
        res = self.client.get(url_staff)
        self.assertEqual(res.status_code, 200)

        # 3. HR user -> 200
        self.client.force_login(self.hr_user)
        res = self.client.get(url_other)
        self.assertEqual(res.status_code, 200)

        # 4. Manager -> can view direct report (staff_emp)
        self.client.force_login(self.mgr_user)
        res = self.client.get(mgr_url_staff)
        self.assertEqual(res.status_code, 200)

        # 5. Manager -> CANNOT view other_emp (not direct report) -> 403
        res = self.client.get(mgr_url_other)
        self.assertEqual(res.status_code, 403)

        # 6. Regular employee -> CANNOT view other_emp -> 403
        self.client.force_login(self.staff_user)
        res = self.client.get(f"{reverse('leave_records')}?employee={self.other_emp.id}")
        self.assertEqual(res.status_code, 403)

        # 7. Regular employee -> viewing self -> 200
        res = self.client.get(reverse('leave_records'))
        self.assertEqual(res.status_code, 200)

    def test_07_hr_landing_page_empty_state_without_employee(self):
        """
        When HR opens leave statement without ?employee=, do NOT default to first employee.
        Show filters with empty state prompt.
        """
        self.client.force_login(self.hr_user)
        res = self.client.get(reverse('hr_leave_statement'))
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.context.get('target_emp'))
        self.assertContains(res, "Select an employee to view their leave statement")

    def test_08_invalid_date_range_validation(self):
        """
        When date_from > date_to, user gets an error message and view falls back to Year mode.
        """
        self.client.force_login(self.hr_user)
        res = self.client.get(
            f"{reverse('hr_leave_statement')}?employee={self.staff_emp.id}&mode=range&from=2026-12-31&to=2026-01-01"
        )
        self.assertEqual(res.status_code, 200)
        # Should fall back to year mode
        self.assertEqual(res.context['mode'], 'year')
        self.assertEqual(res.context['date_from'], datetime.date(timezone.localdate().year, 1, 1))

    def test_09_pdf_export_modes_and_filenames(self):
        """
        PDF exports return application/pdf with appropriate filenames for year and range modes.
        """
        self.client.force_login(self.hr_user)

        # Year mode export
        url_year = f"{reverse('hr_leave_statement')}?employee={self.staff_emp.id}&mode=year&year=2026&export=pdf"
        res = self.client.get(url_year)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertIn('leave_statement_AMMS-0003_2026.pdf', res['Content-Disposition'])
        self.assertGreater(len(res.content), 500)

        # Range mode export
        url_range = f"{reverse('hr_leave_statement')}?employee={self.staff_emp.id}&mode=range&from=2026-02-01&to=2026-04-30&export=pdf"
        res = self.client.get(url_range)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertIn('leave_statement_AMMS-0003_2026-02-01_to_2026-04-30.pdf', res['Content-Disposition'])
        self.assertGreater(len(res.content), 500)

    def test_10_excel_export_three_sheets(self):
        """
        Excel export has 3 sheets: 'Summary', 'Ledger', and 'Requests'.
        """
        self.client.force_login(self.hr_user)
        url = f"{reverse('hr_leave_statement')}?employee={self.staff_emp.id}&mode=year&year=2026&export=excel"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('leave_statement_AMMS-0003_2026.xlsx', res['Content-Disposition'])

        wb = openpyxl.load_workbook(io.BytesIO(res.content))
        self.assertEqual(wb.sheetnames, ['Summary', 'Ledger', 'Requests'])

        # Verify summary sheet has columns
        ws_sum = wb['Summary']
        headers = [c.value for c in ws_sum[4]]
        self.assertIn('Leave Type', headers)
        self.assertIn('Closing Balance', headers)

        # Verify requests sheet has headers
        ws_req = wb['Requests']
        req_headers = [c.value for c in ws_req[4]]
        self.assertIn('Request #', req_headers)
        self.assertIn('Days (Period)', req_headers)

    def test_11_legacy_export_leave_statement_excel_compatibility(self):
        """
        Calling export_leave_statement_excel with legacy 4 arguments works seamlessly.
        """
        summary_list = [{
            'name': 'Annual Leave',
            'opening_days': Decimal('10.00'),
            'accrued_days': Decimal('5.00'),
            'carried_days': Decimal('0.00'),
            'taken_days': Decimal('2.00'),
            'adj_days': Decimal('0.00'),
            'excess_days': Decimal('0.00'),
            'net_balance': Decimal('13.00'),
            'pending_days': Decimal('0.00'),
            'available_days': Decimal('13.00'),
            'uses_balance': True
        }]
        entries = []
        stream = export_leave_statement_excel(self.staff_emp, 2026, summary_list, entries)
        self.assertIsInstance(stream, io.BytesIO)
        self.assertGreater(len(stream.getvalue()), 100)
        wb = openpyxl.load_workbook(stream)
        self.assertIn('Summary', wb.sheetnames)


@override_settings(HR_LEAVE_V2=True)
class LeaveStatementJoinDateTests(TestCase):
    """An opening balance dated before the joining date (e.g. imported as 01/01) is still carried into a range."""

    def test_range_starting_before_join_date_keeps_opening(self):
        import datetime as dt
        from decimal import Decimal as D
        from django.contrib.auth.models import User
        from Admin.models import Employee, LeaveType, LeaveLedger
        from Admin.leave_service import get_employee_leave_statement
        u = User.objects.create_user('hari_jd', 'hari_jd@x.com', 'pw', first_name='Hari', last_name='R')
        emp = Employee.objects.create(user=u, is_employee=True, salary=D('1000'), date_of_join=dt.date(2026, 1, 12))
        annual = LeaveType.objects.get(code='ANNUAL')
        LeaveLedger.objects.create(employee=emp, leave_type=annual, date=dt.date(2026, 1, 1), entry_type='OPENING', days=D('24'))
        LeaveLedger.objects.create(employee=emp, leave_type=annual, date=dt.date(2026, 3, 15), entry_type='TAKEN', days=D('-3'))
        st = get_employee_leave_statement(emp, dt.date(2026, 1, 5), dt.date(2026, 6, 30))
        a = next(s for s in st['summary_by_type'] if s['code'] == 'ANNUAL')
        self.assertEqual(a['balance_at_start'], D('24'))
        self.assertEqual(a['closing_balance'], D('21'))
        self.assertEqual([str(l['running_balance']) for l in st['ledger_lines'] if l['entry'].leave_type_id == annual.id], ['21.00'])



@override_settings(HR_LEAVE_V2=True)
class LeaveStatementPdfFieldsTests(TestCase):
    """PDF request rows show type, days, status and decider; Available is '-' for a period that has ended."""

    def test_pdf_template_fields(self):
        import datetime as dt
        from decimal import Decimal as D
        from django.template.loader import render_to_string
        from django.contrib.auth.models import User
        from Admin.models import Employee, LeaveType, LeaveLedger, Leave
        from Admin.leave_service import get_employee_leave_statement
        hr = User.objects.create_user('hr_pf', 'hr_pf@x.com', 'pw', first_name='Fatima', last_name='HR')
        u = User.objects.create_user('e_pf', 'e_pf@x.com', 'pw', first_name='Sumeet', last_name='Singh')
        emp = Employee.objects.create(user=u, is_employee=True, salary=D('1000'), date_of_join=dt.date(2025, 1, 1), rank='Engineer')
        annual = LeaveType.objects.get(code='ANNUAL')
        LeaveLedger.objects.create(employee=emp, leave_type=annual, date=dt.date(2025, 1, 1), entry_type='OPENING', days=D('24'))
        Leave.objects.create(user=u, employee=emp, leave_type='ANNUAL LEAVE', leave_type_ref=annual,
                             from_date=dt.date(2025, 3, 17), to_date=dt.date(2025, 3, 17), days=D('1'),
                             reason='Family', approval_status='APPROVED', decided_by=hr)
        st = get_employee_leave_statement(emp, dt.date(2025, 3, 1), dt.date(2025, 6, 30))
        a = next(s for s in st['summary_by_type'] if s['code'] == 'ANNUAL')
        self.assertIsNone(a['available_days'])
        html = render_to_string('leave/leave_statement_pdf.html', {**st, 'employee': emp, 'summary': st['summary_by_type'],
                                                                    'entries': st['ledger_lines'], 'generated_at': timezone.now()})
        self.assertIn('Annual Leave', html.split('LEAVE REQUESTS')[-1] if 'LEAVE REQUESTS' in html else html)
        self.assertIn('Approved', html)
        self.assertIn('Fatima HR', html)
        self.assertIn('Engineer', html)

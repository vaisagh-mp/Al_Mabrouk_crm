import calendar
import datetime
from decimal import Decimal
import json
import re
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase, Client
from django.urls import reverse, get_resolver
from django.utils import timezone

from unittest.mock import patch

from Admin.models import Employee, Project, Attendance, Leave, LeaveBalance, WorkOrder, SpareConsumed
from Admin.forms import ManagerEmployeeUpdateForm, EmployeeCreationForm
from service_logs.models import EmployeeServiceLog
from finance.models import Expense, ExpenseCategory, Payroll, Invoice, JobCost, SpareSale
from finance.services import (
    generate_payroll,
    calculate_employee_prorated_salary,
    pnl_for_range,
    get_projects_profitability,
    get_finance_start_month,
    get_attributed_revenue_by_employee,
    get_employee_cost_data,
    get_receivables_data,
    get_dashboard_data,
    import_finance_sheets_data,
    parse_amount,
    parse_sheet_date,
    compute_invoice_engineer_costs,
    get_invoice_costing_data,
    get_jobs_pnl_data,
    get_spares_accounting_data,
    pnl_excel_response,
    get_next_invoice_number,
    get_project_financials,
    engineer_cost_for_projects,
    get_projects_financials_map,
)
from django.db import connection
from django.test.utils import CaptureQueriesContext
from finance.forms import InvoiceForm
import openpyxl
import importlib
_migration_0004 = importlib.import_module('finance.migrations.0004_migrate_project_invoices')
migrate_projects_to_invoices = _migration_0004.migrate_projects_to_invoices
reverse_migrate_projects_to_invoices = _migration_0004.reverse_migrate_projects_to_invoices
from django.apps import apps as django_apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
import io





class RoleAndAccessTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Superuser
        self.admin_user = User.objects.create_superuser('admin', 'admin@example.com', 'pass1234')
        self.admin_emp = Employee.objects.create(
            user=self.admin_user,
            is_administration=True,
            salary=Decimal('10000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )

        # Accountant
        self.accountant_user = User.objects.create_user('accountant', 'acc@example.com', 'pass1234')
        self.accountant_emp = Employee.objects.create(
            user=self.accountant_user,
            is_accountant=True,
            salary=Decimal('5000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )

        # HR
        self.hr_user = User.objects.create_user('hruser', 'hr@example.com', 'pass1234')
        self.hr_emp = Employee.objects.create(
            user=self.hr_user,
            is_hr=True,
            salary=Decimal('4500.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )

        # Manager
        self.mgr_user = User.objects.create_user('mgruser', 'mgr@example.com', 'pass1234')
        self.mgr_emp = Employee.objects.create(
            user=self.mgr_user,
            is_manager=True,
            salary=Decimal('6000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )

        # Regular Employee
        self.reg_user = User.objects.create_user('reguser', 'reg@example.com', 'pass1234')
        self.reg_emp = Employee.objects.create(
            user=self.reg_user,
            is_employee=True,
            salary=Decimal('3000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )

        # Sample data for parameterized URLs
        self.project = Project.objects.create(name='Test Proj', invoice_amount=Decimal('1000.00'))
        self.category = ExpenseCategory.objects.create(name='Sample Cat', kind='OVERHEAD')
        self.expense = Expense.objects.create(
            date=datetime.date(2026, 6, 1), category=self.category, amount=Decimal('100.00'), created_by=self.admin_user
        )
        self.payroll = Payroll.objects.create(
            employee=self.accountant_emp, month=datetime.date(2026, 6, 1), basic_salary=Decimal('5000.00')
        )
        tz = timezone.get_current_timezone()
        login_dt = timezone.make_aware(datetime.datetime(2026, 6, 1, 8, 0), tz)
        self.attendance = Attendance.objects.create(employee=self.accountant_emp, login_time=login_dt, status='APPROVED')
        self.leave = Leave.objects.create(
            user=self.accountant_user, leave_type='ANNUAL LEAVE', from_date=datetime.date(2026, 6, 1), to_date=datetime.date(2026, 6, 2)
        )
        self.invoice = Invoice.objects.create(
            invoice_number='INV-ROLE-01',
            invoice_date=datetime.date(2026, 6, 1),
            amount_excl_vat=Decimal('1000.00'),
            project=self.project,
        )
        self.cost = JobCost.objects.create(
            invoice=self.invoice,
            cost_type='MATERIALS',
            amount=Decimal('150.00'),
            date=datetime.date(2026, 6, 2),
        )
        self.spare = SpareSale.objects.create(
            service_ref='SR-ROLE-01',
            purchase_cost=Decimal('200.00'),
            selling_price=Decimal('350.00'),
        )

    def _resolve_url(self, name):
        kwargs = {}
        if 'payroll_id' in name or name in ('fin_payroll_edit', 'fin_payroll_revert', 'fin_payroll_delete'):
            kwargs['payroll_id'] = self.payroll.id
        elif 'expense_id' in name or name in ('fin_expense_edit', 'fin_expense_delete'):
            kwargs['expense_id'] = self.expense.id
        elif 'project_id' in name or name in ('fin_project_invoice_edit', 'fin_project_detail'):
            kwargs['project_id'] = self.project.id
        elif 'invoice_id' in name or name in ('fin_invoice_edit', 'fin_invoice_record_payment', 'fin_invoice_apply_credit', 'fin_invoice_delete', 'fin_job_costs', 'fin_receivables_record_payment'):
            kwargs['invoice_id'] = self.invoice.id
        elif 'cost_id' in name or name in ('fin_job_cost_delete',):
            kwargs['cost_id'] = self.cost.id
        elif 'spare_id' in name or name in ('fin_spare_edit', 'fin_spare_delete'):
            kwargs['spare_id'] = self.spare.id
        elif 'category_id' in name or name in ('fin_category_edit', 'fin_category_toggle', 'fin_category_delete'):
            kwargs['category_id'] = self.category.id
        elif 'rate_id' in name or name in ('fin_exchange_rate_edit', 'fin_exchange_rate_toggle', 'fin_exchange_rate_delete'):
            from finance.models import ExchangeRate
            rate = ExchangeRate.objects.first()
            kwargs['rate_id'] = rate.id if rate else 1
        elif 'attendance_id' in name or name in ('fin_log_off', 'fin_update_travel_time'):
            kwargs['attendance_id'] = self.attendance.id
        elif 'leave_id' in name or name in ('fin_upload_medical_certificate',):
            kwargs['leave_id'] = self.leave.id
        return reverse(name, kwargs=kwargs)

    def test_login_redirect_for_accountant(self):
        resp = self.client.post(reverse('custom-login'), {'username': 'accountant', 'password': 'pass1234'})
        self.assertRedirects(resp, reverse('fin_dashboard'))

    def test_403_for_non_accountants_on_every_finance_url(self):
        resolver = get_resolver()
        fin_url_names = sorted(list(set(
            name for name in resolver.reverse_dict.keys()
            if isinstance(name, str) and name.startswith('fin_')
        )))

        non_accountants = [self.hr_user, self.mgr_user, self.reg_user]
        for user in non_accountants:
            self.client.force_login(user)
            for name in fin_url_names:
                url = self._resolve_url(name)
                # Test GET
                res = self.client.get(url)
                self.assertEqual(res.status_code, 403, f"Expected 403 for {user.username} on GET {name} ({url}), got {res.status_code}")
                # Test POST
                res_post = self.client.post(url)
                self.assertEqual(res_post.status_code, 403, f"Expected 403 for {user.username} on POST {name} ({url}), got {res_post.status_code}")

    def test_superuser_allowed_on_every_finance_url(self):
        resolver = get_resolver()
        fin_url_names = sorted(list(set(
            name for name in resolver.reverse_dict.keys()
            if isinstance(name, str) and name.startswith('fin_')
        )))

        self.client.force_login(self.admin_user)
        for name in fin_url_names:
            url = self._resolve_url(name)
            res = self.client.get(url)
            # Should NEVER be 403 Forbidden for superuser (may be 200, 302, or 405 if POST-only)
            self.assertNotEqual(res.status_code, 403, f"Superuser was unexpectedly forbidden (403) on {name} ({url})")

    def test_accountant_access_and_template_contract_on_finance_urls(self):
        resolver = get_resolver()
        fin_url_names = sorted(list(set(
            name for name in resolver.reverse_dict.keys()
            if isinstance(name, str) and name.startswith('fin_')
        )))

        post_only_or_redirect = {
            'fin_receivables_record_payment',
            'fin_invoice_record_payment',
            'fin_invoice_apply_credit',
            'fin_job_cost_delete',
            'fin_payroll_generate',
            'fin_payroll_approve_all',
            'fin_payroll_mark_paid',
            'fin_expense_delete',
            'fin_category_toggle',
            'fin_exchange_rate_toggle',
            'fin_log_in',
            'fin_log_off',
            'fin_upload_medical_certificate',
            'fin_mark_notifications_as_read',
            'fin_project_invoice_edit',
        }
        json_or_export = {
            'fin_fetch_notifications',
            'fin_api_exchange_rate',
            'fin_pnl_export_excel',
            'fin_projects_export_excel',
            'fin_invoices_export_excel',
            'fin_jobs_pnl_export_excel',
            'fin_spares_export_excel',
            'fin_receivables_export_excel',
            'fin_employee_cost_export_excel',
            'fin_payroll_export_excel',
            'fin_expense_export_excel',
        }

        admin_only_urls = {
            'fin_payroll_revert',
            'fin_payroll_delete',
            'fin_invoice_delete',
            'fin_spare_delete',
            'fin_category_delete',
            'fin_exchange_rate_delete',
        }

        self.client.force_login(self.accountant_user)
        for name in fin_url_names:
            url = self._resolve_url(name)
            res = self.client.get(url)
            if name in admin_only_urls:
                self.assertEqual(res.status_code, 403, f"Accountant was expected to be forbidden (403) on {name} ({url})")
                continue
            self.assertNotEqual(res.status_code, 403, f"Accountant was unexpectedly forbidden (403) on {name} ({url})")
            if name not in post_only_or_redirect and name not in json_or_export:
                self.assertEqual(res.status_code, 200, f"Expected 200 on {name} ({url}), got {res.status_code}")
                content = res.content.decode('utf-8')
                self.assertIn('almabrouk-logo.png', content, f"Missing almabrouk-logo.png in {name}")
                self.assertIn('notification-popup', content, f"Missing notification-popup in {name}")
                self.assertIn('pg-title', content, f"Missing pg-title in {name}")

    def test_employee_creation_and_update_form_accountant_role(self):
        form = ManagerEmployeeUpdateForm(
            data={
                'username': self.reg_user.username,
                'first_name': 'Reg',
                'last_name': 'User',
                'email': 'reg@example.com',
                'phone_number': '123456',
                'role': 'accountant',
                'rank': 'Accountant',
                'salary': '5500.00',
                'date_of_join': '2026-01-01',
            },
            instance=self.reg_emp
        )
        self.assertTrue(form.is_valid(), form.errors)
        updated_emp = form.save()
        self.assertTrue(updated_emp.is_accountant)
        self.assertFalse(updated_emp.is_employee)
        self.assertFalse(updated_emp.is_manager)
        self.assertFalse(updated_emp.is_hr)
        self.assertFalse(updated_emp.is_administration)
        self.assertEqual(updated_emp.get_role(), "Accountant")


class PayrollTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser('admin_p', 'admin_p@example.com', 'pass1234')
        self.accountant_user = User.objects.create_user('acc_payroll', 'acc@example.com', 'pass1234')
        self.accountant_emp = Employee.objects.create(
            user=self.accountant_user, is_accountant=True, salary=Decimal('5000.00'), date_of_join=datetime.date(2026, 1, 1)
        )

        # Employee 1: Full month joiner
        self.u1 = User.objects.create_user('full_month', 'u1@example.com', 'pass1234')
        self.emp1 = Employee.objects.create(
            user=self.u1,
            is_employee=True,
            salary=Decimal('6240.00'),  # 6240 / 208 = 30/hr
            date_of_join=datetime.date(2026, 1, 1),
        )

        # Employee 2: Mid-month joiner (June 16, 2026 -> 15 active days out of 30)
        self.u2 = User.objects.create_user('mid_month', 'u2@example.com', 'pass1234')
        self.emp2 = Employee.objects.create(
            user=self.u2,
            is_employee=True,
            salary=Decimal('6000.00'),
            date_of_join=datetime.date(2026, 6, 16),
        )

        # Employee 3: Joined after month (July 1, 2026)
        self.u3 = User.objects.create_user('after_month', 'u3@example.com', 'pass1234')
        self.emp3 = Employee.objects.create(
            user=self.u3,
            is_employee=True,
            salary=Decimal('5000.00'),
            date_of_join=datetime.date(2026, 7, 1),
        )

        # Project for logs
        self.project = Project.objects.create(
            name='Test Project',
            client_name='Test Client',
            vessel_name='Test Vessel',
            invoice_amount=Decimal('10000.00'),
        )

    def test_payroll_generation_and_proration(self):
        month_date = datetime.date(2026, 6, 1)

        # Add service log with OT for emp1 in June 2026
        EmployeeServiceLog.objects.create(
            employee=self.emp1,
            project=self.project,
            date=datetime.date(2026, 6, 10),
            start_time=datetime.time(8, 0),
            end_time=datetime.time(20, 0),
            total_hours=Decimal('12.00'),
            ot_hours=Decimal('2.00'),
            normal_cost=Decimal('300.00'),
            ot_cost=Decimal('75.00'),  # 2h * (6240/208=30) * 1.25 = 75
        )

        created, updated, skipped = generate_payroll(month_date, self.admin)
        self.assertGreaterEqual(created, 3)

        # Verify emp1 (full month)
        p1 = Payroll.objects.get(employee=self.emp1, month=datetime.date(2026, 6, 1))
        self.assertEqual(p1.basic_salary, Decimal('6240.00'))
        self.assertEqual(p1.ot_amount, Decimal('75.00'))
        self.assertEqual(p1.gross, Decimal('6315.00'))
        self.assertEqual(p1.absence_deduction, Decimal('6000.00'))
        self.assertEqual(p1.net_pay, Decimal('315.00'))
        p1.absence_waived = True
        self.assertEqual(p1.net_pay, Decimal('6315.00'))

        # Verify emp2 (joined June 16, 2026 -> 15 days out of 30 days = 50% = 3000.00)
        p2 = Payroll.objects.get(employee=self.emp2, month=datetime.date(2026, 6, 1))
        self.assertEqual(p2.basic_salary, Decimal('3000.00'))

        # Verify emp3 (joined after June -> no row created)
        self.assertFalse(Payroll.objects.filter(employee=self.emp3, month=datetime.date(2026, 6, 1)).exists())

        # Test APPROVED and PAID rows are not overwritten
        p1.status = 'APPROVED'
        p1.basic_salary = Decimal('7000.00')
        p1.save()

        p2.status = 'PAID'
        p2.basic_salary = Decimal('4000.00')
        p2.save()

        created, updated, skipped = generate_payroll(month_date, self.admin)
        self.assertGreaterEqual(skipped, 2)
        p1.refresh_from_db()
        p2.refresh_from_db()
        self.assertEqual(p1.basic_salary, Decimal('7000.00'))
        self.assertEqual(p1.status, 'APPROVED')
        self.assertEqual(p2.basic_salary, Decimal('4000.00'))
        self.assertEqual(p2.status, 'PAID')

    def test_payroll_gross_net_maths_and_clean(self):
        p = Payroll(
            employee=self.emp1,
            month=datetime.date(2026, 6, 1),
            basic_salary=Decimal('5000.00'),
            allowances=Decimal('500.00'),
            ot_amount=Decimal('200.00'),
            deductions=Decimal('150.00'),
            status='DRAFT'
        )
        p.full_clean()
        self.assertEqual(p.gross, Decimal('5700.00'))
        self.assertEqual(p.net_pay, Decimal('5550.00'))

        # Non-1st of month clean validation
        p.month = datetime.date(2026, 6, 15)
        with self.assertRaises(ValidationError):
            p.full_clean()

    def test_payroll_edit_blocked_for_approved_and_paid(self):
        self.client.force_login(self.accountant_user)
        p = Payroll.objects.create(
            employee=self.emp1,
            month=datetime.date(2026, 6, 1),
            basic_salary=Decimal('5000.00'),
            allowances=Decimal('0.00'),
            status='APPROVED'
        )
        # GET edit page should redirect
        res = self.client.get(reverse('fin_payroll_edit', kwargs={'payroll_id': p.id}))
        self.assertEqual(res.status_code, 302)

        # POST should not modify
        res_post = self.client.post(
            reverse('fin_payroll_edit', kwargs={'payroll_id': p.id}),
            {'allowances': '500.00', 'deductions': '0.00', 'notes': 'Test'}
        )
        self.assertEqual(res_post.status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.allowances, Decimal('0.00'))

    def test_mark_paid_sets_status_paid_and_paid_on(self):
        self.client.force_login(self.accountant_user)
        p = Payroll.objects.create(
            employee=self.emp1,
            month=datetime.date(2026, 6, 1),
            basic_salary=Decimal('5000.00'),
            status='APPROVED'
        )
        res = self.client.post(reverse('fin_payroll_mark_paid'), {'month': '2026-06'})
        self.assertEqual(res.status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.status, 'PAID')
        self.assertEqual(p.paid_on, timezone.localdate())

    def test_generate_payroll_query_count_constant_for_different_employee_counts(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        month_date = datetime.date(2026, 5, 1)

        # 3 employees case
        with CaptureQueriesContext(connection) as ctx_3:
            generate_payroll(month_date, self.admin)
        select_queries_3 = [q for q in ctx_3.captured_queries if q['sql'].strip().upper().startswith('SELECT')]

        # Clean up payroll rows from that month
        Payroll.objects.filter(month=month_date).delete()

        # Add 7 more employees (total 10+ employees)
        for i in range(7):
            u = User.objects.create_user(f'emp_extra_{i}', f'extra_{i}@example.com', 'pass1234')
            Employee.objects.create(
                user=u,
                is_employee=True,
                salary=Decimal('4000.00'),
                date_of_join=datetime.date(2026, 1, 1),
            )

        with CaptureQueriesContext(connection) as ctx_10:
            generate_payroll(month_date, self.admin)
        select_queries_10 = [q for q in ctx_10.captured_queries if q['sql'].strip().upper().startswith('SELECT')]

        self.assertEqual(len(select_queries_3), len(select_queries_10))



class ExpenseAndProjectTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser('admin_exp', 'admin_exp@example.com', 'pass1234')
        self.accountant_user = User.objects.create_user('acc_exp', 'acc@example.com', 'pass1234')
        self.accountant_emp = Employee.objects.create(
            user=self.accountant_user, is_accountant=True, salary=Decimal('5000.00')
        )

        self.cat_overhead = ExpenseCategory.objects.create(name='Office Supplies Overhead', kind='OVERHEAD')
        self.cat_project = ExpenseCategory.objects.create(name='Materials Project', kind='PROJECT')

        self.project = Project.objects.create(
            name='Engine Overhaul',
            client_name='Marine Corp',
            vessel_name='MV Al Mabrouk',
            invoice_amount=Decimal('20000.00'),
            amount_received=Decimal('5000.00'),
        )

    def test_project_category_requires_project(self):
        exp = Expense(
            date=datetime.date(2026, 6, 10),
            category=self.cat_project,
            amount=Decimal('1200.00'),
            created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            exp.full_clean()

        # Attaching project passes validation
        exp.project = self.project
        exp.full_clean()
        exp.save()
        self.assertIsNotNone(exp.id)

    def test_expense_delete_with_get_returns_405(self):
        self.client.force_login(self.accountant_user)
        exp = Expense.objects.create(
            date=datetime.date(2026, 6, 10),
            category=self.cat_overhead,
            amount=Decimal('150.00'),
            created_by=self.admin,
        )
        # GET should be 405 Method Not Allowed due to @require_POST
        res = self.client.get(reverse('fin_expense_delete', kwargs={'expense_id': exp.id}))
        self.assertEqual(res.status_code, 405)

        # POST should delete
        res_post = self.client.post(reverse('fin_expense_delete', kwargs={'expense_id': exp.id}))
        self.assertEqual(res_post.status_code, 302)
        self.assertFalse(Expense.objects.filter(id=exp.id).exists())

    def test_expenses_export_link_encodes_search_query(self):
        self.client.force_login(self.accountant_user)
        response = self.client.get(reverse('fin_expenses'), {'search': 'A & B'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'search=A+%26+B')

    def test_category_toggle_and_used_category_protection(self):
        self.client.force_login(self.accountant_user)
        cat = ExpenseCategory.objects.create(name='Tools Category', kind='OVERHEAD', is_active=True)
        # Toggle deactivates
        self.client.post(reverse('fin_category_toggle', kwargs={'category_id': cat.id}))
        cat.refresh_from_db()
        self.assertFalse(cat.is_active)
        # Toggle activates
        self.client.post(reverse('fin_category_toggle', kwargs={'category_id': cat.id}))
        cat.refresh_from_db()
        self.assertTrue(cat.is_active)

        # Used category cannot be deleted from database (ProtectedError)
        Expense.objects.create(
            date=datetime.date(2026, 6, 10),
            category=cat,
            amount=Decimal('200.00'),
            created_by=self.admin,
        )
        with self.assertRaises(ProtectedError):
            cat.delete()

    def test_project_invoice_fields_and_outstanding(self):
        self.assertEqual(self.project.outstanding_amount, Decimal('15000.00'))
        self.project.amount_received = Decimal('25000.00')
        self.assertEqual(self.project.outstanding_amount, Decimal('0.00'))


class AccountantAttendanceAndLeaveTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.accountant_user = User.objects.create_user('acc_att', 'acc@example.com', 'pass1234')
        self.accountant_emp = Employee.objects.create(
            user=self.accountant_user, is_accountant=True, salary=Decimal('5000.00')
        )
        self.admin = User.objects.create_superuser('admin_att', 'admin@example.com', 'pass1234')

    def test_accountant_punch_out_next_day_is_capped_at_midnight(self):
        self.client.force_login(self.accountant_user)
        tz = timezone.get_current_timezone()

        # Punch in on June 10, 2026 at 09:00 AM local time
        login_dt = timezone.make_aware(datetime.datetime(2026, 6, 10, 9, 0), tz)
        att = Attendance.objects.create(
            employee=self.accountant_emp,
            login_time=login_dt,
            status='APPROVED',
        )

        # Mock current time during logout to be next day (June 11, 2026 at 02:00 AM local)
        next_day_dt = timezone.make_aware(datetime.datetime(2026, 6, 11, 2, 0), tz)
        from unittest.mock import patch
        with patch('django.utils.timezone.now', return_value=next_day_dt):
            res = self.client.post(reverse('fin_log_off', kwargs={'attendance_id': att.id}))
            self.assertEqual(res.status_code, 302)

        att.refresh_from_db()
        self.assertTrue(att.auto_punched_out)
        self.assertIsNotNone(att.log_out_time)

        # Capped exactly at midnight 00:00:00 of June 11
        local_logout = timezone.localtime(att.log_out_time, tz)
        self.assertEqual(local_logout.date(), datetime.date(2026, 6, 11))
        self.assertEqual(local_logout.time(), datetime.time(0, 0, 0))
        # Total work hours: 09:00 to 00:00 = 15 hours
        self.assertEqual(att.total_hours_of_work, Decimal('15.00'))


class Phase2PnLAndProjectsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser('admin_p2', 'admin_p2@example.com', 'pass1234')
        self.accountant_user = User.objects.create_user('acc_p2', 'acc_p2@example.com', 'pass1234')
        self.accountant_emp = Employee.objects.create(
            user=self.accountant_user, is_accountant=True, salary=Decimal('5000.00'), date_of_join=datetime.date(2026, 1, 1)
        )
        self.emp = Employee.objects.create(
            user=User.objects.create_user('emp_p2', 'emp_p2@example.com', 'pass1234'),
            is_employee=True,
            salary=Decimal('6240.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )

        self.cat_overhead, _ = ExpenseCategory.objects.get_or_create(name='Office Rent', defaults={'kind': 'OVERHEAD'})
        self.cat_project, _ = ExpenseCategory.objects.get_or_create(name='Project Logistics', defaults={'kind': 'PROJECT'})
        self.initial_expense = Expense.objects.create(
            date=datetime.date(2026, 1, 1),
            category=self.cat_overhead,
            amount=Decimal('100.00'),
            created_by=self.admin
        )

    def test_pnl_month_with_known_figures(self):
        proj = Project.objects.create(
            name='Vessel Repair A',
            client_name='Alpha Marine',
            vessel_name='Alpha 1',
            invoice_amount=Decimal('50000.00'),
            purchase_and_expenses=Decimal('10000.00'),
            invoice_date=datetime.date(2026, 3, 15),
        )
        Payroll.objects.create(
            employee=self.emp,
            month=datetime.date(2026, 3, 1),
            basic_salary=Decimal('15000.00'),
            status='APPROVED',
        )
        Expense.objects.create(
            date=datetime.date(2026, 3, 10),
            category=self.cat_overhead,
            amount=Decimal('5000.00'),
            created_by=self.admin,
        )
        Expense.objects.create(
            date=datetime.date(2026, 3, 20),
            category=self.cat_project,
            project=proj,
            amount=Decimal('2000.00'),
            created_by=self.admin,
        )
        EmployeeServiceLog.objects.create(
            employee=self.emp,
            project=proj,
            date=datetime.date(2026, 3, 10),
            start_time=datetime.time(8, 0),
            end_time=datetime.time(17, 0),
            total_hours=Decimal('9.00'),
            normal_cost=Decimal('270.00'),
        )

        res = pnl_for_range(datetime.date(2026, 3, 1), datetime.date(2026, 3, 31), granularity='month')
        buckets = res['buckets']
        self.assertEqual(len(buckets), 1)
        b = buckets[0]

        self.assertEqual(b['revenue'], Decimal('50000.00'))
        self.assertEqual(b['payroll_cost'], Decimal('15000.00'))
        # project direct: 10000 purchases + 2000 expense = 12000
        self.assertEqual(b['project_direct_cost'], Decimal('12000.00'))
        self.assertEqual(b['overhead_cost'], Decimal('5000.00'))
        # total cost: 15000 + 12000 + 5000 = 32000
        self.assertEqual(b['total_cost'], Decimal('32000.00'))
        # profit: 50000 - 32000 = 18000
        self.assertEqual(b['profit'], Decimal('18000.00'))
        # margin: 18000 / 50000 * 100 = 36.0%
        self.assertEqual(b['margin_pct'], Decimal('36.0'))
        self.assertEqual(b['engineer_days'], 1)
        self.assertEqual(b['revenue_per_engineer_day'], Decimal('50000.00'))
        self.assertFalse(b['estimated'])

    def test_pnl_month_without_payroll_is_estimated(self):
        # Month 2026-04 has NO Payroll rows
        res = pnl_for_range(datetime.date(2026, 4, 1), datetime.date(2026, 4, 30), granularity='month')
        self.assertTrue(len(res['buckets']) >= 1)
        b = res['buckets'][0]
        self.assertTrue(b['estimated'])
        # Active employees are self.accountant_emp (5000) + self.emp (6240) = 11240.00
        self.assertEqual(b['payroll_cost'], Decimal('11240.00'))

    def test_estimated_month_equals_generate_payroll(self):
        month_date = datetime.date(2026, 2, 1)
        # Ensure no payroll rows exist for 2026-02
        Payroll.objects.filter(month=month_date).delete()

        # Add a service log with OT for emp in 2026-02
        proj = Project.objects.create(name='P2 Proj', invoice_amount=Decimal('1000.00'))
        EmployeeServiceLog.objects.create(
            employee=self.emp,
            project=proj,
            date=datetime.date(2026, 2, 10),
            start_time=datetime.time(8, 0),
            end_time=datetime.time(20, 0),
            total_hours=Decimal('12.00'),
            ot_hours=Decimal('2.00'),
            normal_cost=Decimal('300.00'),
            ot_cost=Decimal('90.00'),
        )

        res = pnl_for_range(datetime.date(2026, 2, 1), datetime.date(2026, 2, 28), granularity='month')
        est_payroll = res['buckets'][0]['payroll_cost']

        # Now generate payroll for that exact month
        generate_payroll(month_date, self.admin)
        gen_payroll = sum(p.gross for p in Payroll.objects.filter(month=month_date))

        self.assertEqual(est_payroll, gen_payroll)

    def test_project_without_invoice_date_excluded_from_pnl(self):
        Project.objects.create(
            name='No Invoice Date Proj',
            invoice_amount=Decimal('75000.00'),
            invoice_date=None,
        )
        res = pnl_for_range(datetime.date(2026, 1, 1), datetime.date(2026, 1, 31), granularity='month')
        self.assertEqual(res['totals'].get('revenue', Decimal('0.00')), Decimal('0.00'))

    def test_weekly_buckets_sum_to_monthly_total(self):
        # Full month March 2026
        month_res = pnl_for_range(datetime.date(2026, 3, 1), datetime.date(2026, 3, 31), granularity='month')
        month_payroll = month_res['buckets'][0]['payroll_cost']

        # Weekly buckets across March 2026
        week_res = pnl_for_range(datetime.date(2026, 3, 1), datetime.date(2026, 3, 31), granularity='week')
        weeks_payroll_sum = sum(b['payroll_cost'] for b in week_res['buckets'])

        # Daily proration across 31 days rounded weekly should sum within 1 AED of month payroll
        self.assertAlmostEqual(float(weeks_payroll_sum), float(month_payroll), delta=1.0)

    def test_future_months_not_shown_in_monthly_table(self):
        today = timezone.localdate()
        cur_month_start = today.replace(day=1)

        res = pnl_for_range(datetime.date(today.year, 1, 1), datetime.date(today.year, 12, 31), granularity='month')
        for b in res['buckets']:
            self.assertLessEqual(b['start_date'], cur_month_start)

    def test_12_month_pnl_attendance_register_query_count(self):
        import finance.services
        today = timezone.localdate()
        start = datetime.date(today.year, 1, 1)
        end = today

        with patch('finance.services.build_attendance_register', wraps=finance.services.build_attendance_register) as mock_reg:
            pnl_for_range(start, end, granularity='month')
            # build_attendance_register must be called at most ONCE for the whole 12-month range, NEVER per month
            self.assertLessEqual(mock_reg.call_count, 1)

    def test_query_count_constant_for_fin_pnl_and_fin_projects(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.client.force_login(self.accountant_user)

        # Baseline: 1 project
        Project.objects.all().delete()
        Project.objects.create(
            name='Base Project',
            client_name='Client A',
            invoice_amount=Decimal('10000.00'),
            invoice_date=datetime.date(2026, 1, 15),
        )

        with CaptureQueriesContext(connection) as ctx_pnl_1:
            self.client.get(reverse('fin_pnl'))
        pnl_queries_1 = len(ctx_pnl_1.captured_queries)

        with CaptureQueriesContext(connection) as ctx_proj_1:
            self.client.get(reverse('fin_projects'))
        proj_queries_1 = len(ctx_proj_1.captured_queries)

        # Add 19 more projects (total 20 projects)
        for i in range(19):
            Project.objects.create(
                name=f'Bulk Project {i}',
                client_name=f'Client {i % 3}',
                invoice_amount=Decimal('5000.00'),
                invoice_date=datetime.date(2026, 1, 15),
            )

        with CaptureQueriesContext(connection) as ctx_pnl_20:
            self.client.get(reverse('fin_pnl'))
        pnl_queries_20 = len(ctx_pnl_20.captured_queries)

        with CaptureQueriesContext(connection) as ctx_proj_20:
            self.client.get(reverse('fin_projects'))
        proj_queries_20 = len(ctx_proj_20.captured_queries)

        self.assertEqual(pnl_queries_1, pnl_queries_20)
        self.assertEqual(proj_queries_1, proj_queries_20)

    def test_margin_none_when_revenue_zero_and_engineer_days_distinct(self):
        p = Project.objects.create(
            name='Zero Rev Proj',
            invoice_amount=Decimal('0.00'),
            invoice_date=datetime.date(2026, 1, 10),
        )
        # Create two logs for same engineer on same date
        EmployeeServiceLog.objects.create(
            employee=self.emp,
            project=p,
            date=datetime.date(2026, 1, 10),
            start_time=datetime.time(8, 0),
            end_time=datetime.time(12, 0),
            total_hours=Decimal('4.00'),
        )
        EmployeeServiceLog.objects.create(
            employee=self.emp,
            project=p,
            date=datetime.date(2026, 1, 10),
            start_time=datetime.time(13, 0),
            end_time=datetime.time(17, 0),
            total_hours=Decimal('4.00'),
        )

        results, totals = get_projects_profitability({'client': ''})
        found = next(r for r in results if r['id'] == p.id)
        self.assertIsNone(found['margin_pct'])
        # Distinct engineer days for this project is 1
        self.assertEqual(found['engineer_days'], 1)

    def test_year_before_any_data_payroll_zero_no_data_true_no_register_call(self):
        import finance.services
        # Finance start date is 2026-01-01 (from self.initial_expense)
        with patch('finance.services.build_attendance_register', wraps=finance.services.build_attendance_register) as mock_reg:
            res = pnl_for_range(datetime.date(2025, 1, 1), datetime.date(2025, 12, 31), granularity='month')
            self.assertEqual(mock_reg.call_count, 0)
            self.assertTrue(len(res['buckets']) >= 12)
            for b in res['buckets']:
                self.assertEqual(b['payroll_cost'], Decimal('0.00'))
                self.assertTrue(b['no_data'])
                self.assertFalse(b['estimated'])

    def test_accountant_gets_fin_pnl_periods(self):
        self.client.force_login(self.accountant_user)
        for period in ['week', 'month', 'year']:
            resp = self.client.get(reverse('fin_pnl') + f'?period={period}')
            self.assertEqual(resp.status_code, 200, f"Expected 200 for period={period}, got {resp.status_code}")

    def test_yearly_chart_starts_at_first_data_year(self):
        self.client.force_login(self.accountant_user)
        # Finance start date currently 2026-01-01
        resp = self.client.get(reverse('fin_pnl') + '?period=year')
        self.assertEqual(resp.status_code, 200)
        chart_data = json.loads(resp.context['chart_json'])
        self.assertTrue(len(chart_data['labels']) >= 1)
        self.assertIn('2026', chart_data['labels'][0])

        # Add earlier expense in 2024
        Expense.objects.create(
            date=datetime.date(2024, 5, 10),
            category=self.cat_overhead,
            amount=Decimal('50.00'),
            created_by=self.admin
        )
        resp2 = self.client.get(reverse('fin_pnl') + '?period=year')
        chart_data2 = json.loads(resp2.context['chart_json'])
        self.assertTrue(len(chart_data2['labels']) >= 1)
        self.assertIn('2024', chart_data2['labels'][0])

    def test_yearly_chart_query_count(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.client.force_login(self.accountant_user)
        with CaptureQueriesContext(connection) as ctx1:
            self.client.get(reverse('fin_pnl') + '?period=year')
        q1 = len(ctx1.captured_queries)

        # Add earlier expense in 2023 (making range 4 years: 2023-2026)
        Expense.objects.create(
            date=datetime.date(2023, 1, 1),
            category=self.cat_overhead,
            amount=Decimal('10.00'),
            created_by=self.admin
        )
        with CaptureQueriesContext(connection) as ctx2:
            self.client.get(reverse('fin_pnl') + '?period=year')
        q2 = len(ctx2.captured_queries)

        # Query count is constant regardless of how many years the yearly chart covers
        self.assertEqual(q1, q2)
        self.assertLessEqual(q1, 35)

    @patch('django.utils.timezone.localdate')
    def test_current_month_payroll_is_prorated_mtd_and_weekly_sum_equals_monthly(self, mock_localdate):
        mock_localdate.return_value = datetime.date(2026, 10, 15)
        today = timezone.localdate()
        cur_first = today.replace(day=1)
        days_in_m = calendar.monthrange(today.year, today.month)[1]

        # Total active monthly payroll: accountant (5000) + emp (6240) = 11240.00
        full_payroll = Decimal('11240.00')
        expected_mtd = round(full_payroll * Decimal(today.day) / Decimal(days_in_m), 2)

        # Monthly granularity for current month
        month_res = pnl_for_range(cur_first, today, granularity='month')
        cur_b = month_res['buckets'][-1]
        self.assertTrue(cur_b['mtd'])
        self.assertEqual(cur_b['payroll_cost'], expected_mtd)

        # Custom date snapping for monthly granularity snaps to 1st and today for current month
        custom_res = pnl_for_range(cur_first + datetime.timedelta(days=2), today, granularity='month')
        self.assertEqual(custom_res['buckets'][-1]['payroll_cost'], expected_mtd)

        # Weekly buckets sum to monthly MTD value
        week_res = pnl_for_range(cur_first, today, granularity='week')
        weekly_sum = sum(b['payroll_cost'] for b in week_res['buckets'])
        self.assertAlmostEqual(float(weekly_sum), float(cur_b['payroll_cost']), delta=0.05)

    def test_ongoing_project_with_labour_cost_and_no_invoice(self):
        p = Project.objects.create(
            name='Ongoing Project',
            invoice_amount=Decimal('0.00'),
            invoice_date=None,
            invoice_number=None,
        )
        EmployeeServiceLog.objects.create(
            employee=self.emp,
            project=p,
            date=datetime.date(2026, 5, 10),
            start_time=datetime.time(8, 0),
            end_time=datetime.time(17, 0),
            total_hours=Decimal('9.00'),
            normal_cost=Decimal('500.00'),
        )

        results, totals = get_projects_profitability()
        p_row = next(r for r in results if r['id'] == p.id)

        # Ongoing project with no invoice -> not a loss, invoiced is False, margin is None, invoice_no is '—'
        self.assertFalse(p_row['invoiced'])
        self.assertFalse(p_row['is_loss'])
        self.assertIsNone(p_row['margin_pct'])
        self.assertEqual(p_row['invoice_no'], '—')

        # Excluded from loss-making only filter
        loss_results, _ = get_projects_profitability({'loss_only': True})
        self.assertNotIn(p.id, [r['id'] for r in loss_results])

        # Add an invoiced profitable project
        inv_p = Project.objects.create(
            name='Invoiced Profitable',
        )
        Invoice.objects.create(
            project=inv_p,
            invoice_number='INV-2026-999',
            invoice_date=datetime.date(2026, 5, 1),
            amount_excl_vat=Decimal('10000.00'),
        )
        results2, totals2 = get_projects_profitability()
        # Totals margin excludes the not-invoiced project (10000 rev / 10000 profit = 100.0%)
        self.assertEqual(totals2['margin_pct'], Decimal('100.0'))

        # Invoiced filter: 'invoiced'
        inv_results, _ = get_projects_profitability({'invoiced_status': 'invoiced'})
        self.assertIn(inv_p.id, [r['id'] for r in inv_results])
        self.assertNotIn(p.id, [r['id'] for r in inv_results])

        # Invoiced filter: 'not_invoiced'
        not_inv_results, _ = get_projects_profitability({'invoiced_status': 'not_invoiced'})
        self.assertIn(p.id, [r['id'] for r in not_inv_results])
        self.assertNotIn(inv_p.id, [r['id'] for r in not_inv_results])


class QueryStringPreservationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.accountant_user = User.objects.create_user('acc_qs', 'acc_qs@example.com', 'pass1234')
        self.accountant_emp = Employee.objects.create(
            user=self.accountant_user,
            is_accountant=True,
            salary=Decimal('5000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )
        self.client.force_login(self.accountant_user)

        # Create project with client containing special characters "A & B Marine"
        for i in range(30):
            Project.objects.create(
                name=f'Marine Project {i}',
                client_name='A & B Marine',
                invoice_amount=Decimal('500.00'),
                invoice_date=datetime.date(2026, 5, 1),
                invoice_number=f'INV-AB-{i}',
            )

    def test_client_special_char_survives_sort_page_export(self):
        url = reverse('fin_projects') + '?client=A+%26+B+Marine'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        # 1. Check context querystrings
        qs_no_sort = resp.context['querystring_without_sort']
        qs_no_page = resp.context['querystring_without_page']
        qs_export = resp.context['querystring_for_export']

        self.assertIn('client=A+%26+B+Marine', qs_no_sort)
        self.assertIn('client=A+%26+B+Marine', qs_no_page)
        self.assertIn('client=A+%26+B+Marine', qs_export)

        # 2. Check sort links in HTML
        content = resp.content.decode('utf-8')
        self.assertIn('?sort=name&client=A+%26+B+Marine', content)
        self.assertIn('?sort=revenue&client=A+%26+B+Marine', content)

        # 3. Check pagination links in HTML
        self.assertIn('?page=2&client=A+%26+B+Marine', content)

        # 4. Check export link in HTML
        self.assertIn('/finance/projects/export/?client=A+%26+B+Marine', content)

        # 5. Access page 2 and verify client filter survives
        page2_resp = self.client.get(reverse('fin_projects') + f'?page=2&{qs_no_page}')
        self.assertEqual(page2_resp.status_code, 200)
        self.assertEqual(page2_resp.context['page_obj'].number, 2)
        self.assertEqual(len(page2_resp.context['page_obj']), 10)

        # 10 per page: 30 projects -> 3 pages, last page has 10
        self.assertEqual(page2_resp.context['page_obj'].paginator.num_pages, 3)
        self.assertEqual(page2_resp.context['page_obj'].paginator.per_page, 10)

        # 6. Access export and verify it returns excel
        export_resp = self.client.get(reverse('fin_projects_export_excel') + f'?{qs_export}')
        self.assertEqual(export_resp.status_code, 200)
        self.assertEqual(export_resp['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


class PnlYearlyYtdTests(TestCase):
    def test_yearly_buckets_current_year_flagged_ytd_not_mtd(self):
        today = timezone.localdate()
        past_start = datetime.date(today.year - 1, 1, 1)
        curr_end = datetime.date(today.year, 12, 31)

        # Create an expense in current year and past year to avoid no_data
        admin = User.objects.create_superuser('adm_ytd', 'adm@example.com', 'pass1234')
        cat = ExpenseCategory.objects.create(name='Office YTD', kind='OVERHEAD')
        Expense.objects.create(date=past_start, category=cat, amount=Decimal('50.00'), created_by=admin)
        Expense.objects.create(date=datetime.date(today.year, 1, 1), category=cat, amount=Decimal('50.00'), created_by=admin)

        data = pnl_for_range(past_start, curr_end, granularity='year')
        buckets = data['buckets']
        self.assertTrue(len(buckets) >= 2)

        curr_bucket = [b for b in buckets if b['key'] == str(today.year)][0]
        past_bucket = [b for b in buckets if b['key'] == str(today.year - 1)][0]

        # Flag for current year must be 'ytd' (True), and not 'mtd'
        self.assertTrue(curr_bucket.get('ytd'))
        self.assertFalse(curr_bucket.get('mtd'))

        # Flag for past year is not ytd or mtd
        self.assertFalse(past_bucket.get('ytd'))
        self.assertFalse(past_bucket.get('mtd'))


class ReceivablesAndPaymentTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.accountant_user = User.objects.create_user('acc_rec', 'acc_rec@example.com', 'pass1234')
        self.accountant_emp = Employee.objects.create(
            user=self.accountant_user,
            is_accountant=True,
            salary=Decimal('5000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )
        self.client.force_login(self.accountant_user)
        self.today = timezone.localdate()

        # Project 1: invoiced 10 days ago (0-30 bucket)
        self.p_0_30 = Project.objects.create(
            name='Project 0-30',
            client_name='Client Alpha',
            invoice_number='INV-101',
            invoice_amount=Decimal('5000.00'),
            amount_received=Decimal('1000.00'),
            invoice_date=self.today - datetime.timedelta(days=10),
            payment_status='PARTIAL',
        )

        # Project 2: invoiced 45 days ago (31-60 bucket)
        self.p_31_60 = Project.objects.create(
            name='Project 31-60',
            client_name='Client Beta',
            invoice_number='INV-102',
            invoice_amount=Decimal('3000.00'),
            amount_received=Decimal('0.00'),
            invoice_date=self.today - datetime.timedelta(days=45),
            payment_status='UNPAID',
        )

        # Project 3: invoiced 75 days ago (61-90 bucket)
        self.p_61_90 = Project.objects.create(
            name='Project 61-90',
            client_name='Client Gamma',
            invoice_number='INV-103',
            invoice_amount=Decimal('2000.00'),
            amount_received=Decimal('0.00'),
            invoice_date=self.today - datetime.timedelta(days=75),
            payment_status='UNPAID',
        )

        # Project 4: invoiced 120 days ago (90+ bucket)
        self.p_90_plus = Project.objects.create(
            name='Project 90+',
            client_name='Client Delta',
            invoice_number='INV-104',
            invoice_amount=Decimal('4000.00'),
            amount_received=Decimal('0.00'),
            invoice_date=self.today - datetime.timedelta(days=120),
            payment_status='UNPAID',
        )

        # Fully paid project (outstanding 0 -> excluded from receivables)
        self.p_paid = Project.objects.create(
            name='Project Paid',
            client_name='Client Epsilon',
            invoice_number='INV-105',
            invoice_amount=Decimal('1500.00'),
            amount_received=Decimal('1500.00'),
            invoice_date=self.today - datetime.timedelta(days=5),
            payment_status='PAID',
        )

        migrate_projects_to_invoices(django_apps, None)
        self.inv_0_30 = Invoice.objects.get(project=self.p_0_30)
        self.inv_31_60 = Invoice.objects.get(project=self.p_31_60)
        self.inv_61_90 = Invoice.objects.get(project=self.p_61_90)
        self.inv_90_plus = Invoice.objects.get(project=self.p_90_plus)
        self.inv_paid = Invoice.objects.get(project=self.p_paid)

    def test_receivables_aging_buckets(self):
        receivables, summary = get_receivables_data()
        self.assertEqual(summary['total_count'], 4)
        self.assertEqual(summary['total_outstanding'], Decimal('13000.00'))  # 4000 + 3000 + 2000 + 4000
        self.assertEqual(summary['bucket_0_30']['count'], 1)
        self.assertEqual(summary['bucket_0_30']['amount'], Decimal('4000.00'))
        self.assertEqual(summary['bucket_31_60']['count'], 1)
        self.assertEqual(summary['bucket_31_60']['amount'], Decimal('3000.00'))
        self.assertEqual(summary['bucket_61_90']['count'], 1)
        self.assertEqual(summary['bucket_61_90']['amount'], Decimal('2000.00'))
        self.assertEqual(summary['bucket_90_plus']['count'], 1)
        self.assertEqual(summary['bucket_90_plus']['amount'], Decimal('4000.00'))
        self.assertEqual(summary['oldest_project'].id, self.inv_90_plus.id)

    def test_record_payment_validation_and_status_transitions(self):
        url = reverse('fin_receivables_record_payment', args=[self.inv_31_60.id])

        # 1. GET request rejected (POST only)
        resp_get = self.client.get(url)
        self.assertEqual(resp_get.status_code, 405)

        # 2. Amount <= 0 -> validation error
        resp_neg = self.client.post(url, {'amount': '0.00'})
        self.p_31_60.refresh_from_db()
        self.inv_31_60.refresh_from_db()
        self.assertEqual(self.inv_31_60.amount_received, Decimal('0.00'))
        self.assertEqual(self.p_31_60.amount_received, Decimal('0.00'))
        self.assertEqual(self.p_31_60.payment_status, 'UNPAID')

        # 3. Amount > invoice amount -> validation error
        resp_excess = self.client.post(url, {'amount': '3500.00'})
        self.p_31_60.refresh_from_db()
        self.inv_31_60.refresh_from_db()
        self.assertEqual(self.inv_31_60.amount_received, Decimal('0.00'))
        self.assertEqual(self.p_31_60.amount_received, Decimal('0.00'))
        self.assertEqual(self.p_31_60.payment_status, 'UNPAID')

        # 4. Partial payment (< invoice_amount) -> PARTIAL
        resp_partial = self.client.post(url, {'amount': '1000.00', 'payment_date': str(self.today)})
        self.assertEqual(resp_partial.status_code, 302)
        self.p_31_60.refresh_from_db()
        self.inv_31_60.refresh_from_db()
        self.assertEqual(self.inv_31_60.amount_received, Decimal('1000.00'))
        self.assertEqual(self.p_31_60.amount_received, Decimal('1000.00'))
        self.assertEqual(self.inv_31_60.status, 'OVERDUE')
        self.assertEqual(self.p_31_60.payment_status, 'PARTIAL')
        self.assertEqual(self.p_31_60.payment_received_date, self.today)

        # 5. Full payment (remainder == invoice_amount) -> PAID
        resp_full = self.client.post(url, {'amount': '2000.00', 'payment_date': str(self.today)})
        self.assertEqual(resp_full.status_code, 302)
        self.p_31_60.refresh_from_db()
        self.inv_31_60.refresh_from_db()
        self.assertEqual(self.inv_31_60.amount_received, Decimal('3000.00'))
        self.assertEqual(self.p_31_60.amount_received, Decimal('3000.00'))
        self.assertEqual(self.inv_31_60.status, 'PAID')
        self.assertEqual(self.p_31_60.payment_status, 'PAID')


class EmployeeCostAndAttributedRevenueTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.accountant_user = User.objects.create_user('acc_ec', 'acc_ec@example.com', 'pass1234')
        self.accountant_emp = Employee.objects.create(
            user=self.accountant_user,
            is_accountant=True,
            salary=Decimal('5000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )
        self.client.force_login(self.accountant_user)

        # Engineer 1
        self.eng1_user = User.objects.create_user('eng1', 'eng1@example.com', 'pass1234')
        self.eng1 = Employee.objects.create(
            user=self.eng1_user,
            rank='Lead Marine Engineer',
            salary=Decimal('8000.00'),
            date_of_join=datetime.date(2026, 1, 1),
            is_employee=True,
        )

        # Engineer 2
        self.eng2_user = User.objects.create_user('eng2', 'eng2@example.com', 'pass1234')
        self.eng2 = Employee.objects.create(
            user=self.eng2_user,
            rank='Automation Engineer',
            salary=Decimal('6000.00'),
            date_of_join=datetime.date(2026, 1, 1),
            is_employee=True,
        )

        # Engineer 3
        self.eng3_user = User.objects.create_user('eng3', 'eng3@example.com', 'pass1234')
        self.eng3 = Employee.objects.create(
            user=self.eng3_user,
            rank='Electrical Engineer',
            salary=Decimal('4000.00'),
            date_of_join=datetime.date(2026, 1, 1),
            is_employee=True,
        )

        # Office-only staff
        self.office_user = User.objects.create_user('office_staff', 'off@example.com', 'pass1234')
        self.office_emp = Employee.objects.create(
            user=self.office_user,
            rank='Admin Officer',
            salary=Decimal('3500.00'),
            date_of_join=datetime.date(2026, 1, 1),
            is_administration=True,
        )

    def test_attributed_revenue_exact_sum_and_remainder_allocation(self):
        """
        Attributed revenue uses INVOICED projects only (invoice_date in range),
        split by logged hours.
        Attributed revenue for each project must sum EXACTLY to its revenue,
        with any rounding remainder allocated to the employee with the largest share.
        Office-only staff show None ("—").
        """
        start = datetime.date(2026, 6, 1)
        end = datetime.date(2026, 6, 30)

        # Invoiced project with 100.00 revenue and 3 equal hours (1h, 1h, 1h) -> 33.33 each, remainder 0.01
        p_odd = Project.objects.create(
            name='Project Odd Split',
            invoice_amount=Decimal('100.00'),
            invoice_date=datetime.date(2026, 6, 15),
            invoice_number='INV-ODD-01',
        )
        EmployeeServiceLog.objects.create(
            employee=self.eng1, project=p_odd, date=datetime.date(2026, 6, 5),
            start_time=datetime.time(9, 0), end_time=datetime.time(10, 0),
        )
        EmployeeServiceLog.objects.create(
            employee=self.eng2, project=p_odd, date=datetime.date(2026, 6, 5),
            start_time=datetime.time(9, 0), end_time=datetime.time(10, 0),
        )
        EmployeeServiceLog.objects.create(
            employee=self.eng3, project=p_odd, date=datetime.date(2026, 6, 5),
            start_time=datetime.time(9, 0), end_time=datetime.time(10, 0),
        )

        # Invoiced project with 2500.55 revenue and unequal hours (10h, 5h)
        p_unequal = Project.objects.create(
            name='Project Unequal Split',
            invoice_amount=Decimal('2500.55'),
            invoice_date=datetime.date(2026, 6, 20),
            invoice_number='INV-UNEQUAL-01',
        )
        EmployeeServiceLog.objects.create(
            employee=self.eng1, project=p_unequal, date=datetime.date(2026, 6, 10),
            start_time=datetime.time(8, 0), end_time=datetime.time(18, 0),
        )
        EmployeeServiceLog.objects.create(
            employee=self.eng2, project=p_unequal, date=datetime.date(2026, 6, 10),
            start_time=datetime.time(8, 0), end_time=datetime.time(13, 0),
        )

        # Non-invoiced project (must contribute 0 to attributed revenue)
        p_not_inv = Project.objects.create(
            name='Ongoing Project Not Invoiced',
            invoice_amount=Decimal('5000.00'),
            invoice_date=None,
        )
        EmployeeServiceLog.objects.create(
            employee=self.eng1, project=p_not_inv, date=datetime.date(2026, 6, 12),
            start_time=datetime.time(8, 0), end_time=datetime.time(16, 0),
        )

        emp_attributed, proj_shares = get_attributed_revenue_by_employee(start, end)

        # Check project 1 shares sum exactly to 100.00
        shares1 = proj_shares[p_odd.id]
        self.assertEqual(sum(shares1.values()), Decimal('100.00'))

        # Check project 2 shares sum exactly to 2500.55
        shares2 = proj_shares[p_unequal.id]
        self.assertEqual(sum(shares2.values()), Decimal('2500.55'))

        # Check full employee cost rows
        rows, totals = get_employee_cost_data(start, end)
        rows_by_id = {r['id']: r for r in rows}

        # Office employee has 0 vessel hours -> attributed_revenue is None and contribution is None
        office_row = rows_by_id[self.office_emp.id]
        self.assertEqual(office_row['vessel_hours'], Decimal('0.00'))
        self.assertIsNone(office_row['attributed_revenue'])
        self.assertIsNone(office_row['contribution'])

        # Eng 1 attributed revenue = shares1[eng1] + shares2[eng1]
        eng1_row = rows_by_id[self.eng1.id]
        expected_eng1_rev = shares1[self.eng1.id] + shares2[self.eng1.id]
        self.assertEqual(eng1_row['attributed_revenue'], expected_eng1_rev)
        self.assertEqual(eng1_row['contribution'], expected_eng1_rev - eng1_row['payroll_cost'])

        # Total attributed revenue across all rows matches sum of invoiced projects exactly
        total_invoiced_in_range = p_odd.invoice_amount + p_unequal.invoice_amount
        self.assertEqual(totals['total_attributed_revenue'], total_invoiced_in_range)


class DashboardAnalyticsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.accountant_user = User.objects.create_user('acc_dash', 'acc_dash@example.com', 'pass1234')
        self.accountant_emp = Employee.objects.create(
            user=self.accountant_user,
            is_accountant=True,
            salary=Decimal('5000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )
        self.client.force_login(self.accountant_user)
        self.today = timezone.localdate()

    @patch('django.utils.timezone.localdate')
    def test_dashboard_kpis_and_panels(self, mock_localdate):
        mock_localdate.return_value = datetime.date(2026, 10, 15)
        self.today = timezone.localdate()
        # Create an invoiced project
        p = Project.objects.create(
            name='Dashboard Test Project',
            client_name='Mega Maritime',
            category='Main Engine Overhaul',
            invoice_amount=Decimal('15000.00'),
            amount_received=Decimal('5000.00'),
            invoice_date=datetime.date(self.today.year, self.today.month, 5),
            invoice_number='INV-DASH-01',
        )
        Invoice.objects.create(
            invoice_number='INV-DASH-01',
            invoice_date=datetime.date(self.today.year, self.today.month, 5),
            project=p,
            invoice_type='SERVICE_JOB',
            amount_excl_vat=Decimal('15000.00'),
            amount_received=Decimal('5000.00'),
        )

        data = get_dashboard_data(period='this_month')
        kpis = data['kpis']

        # Revenue matches project revenue
        self.assertEqual(kpis['revenue']['value'], Decimal('15000.00'))
        # Top client concentration: 100% since single client
        self.assertEqual(data['top_client_concentration'], Decimal('100.0'))
        self.assertTrue(data['has_high_client_concentration'])

        # Check alerts
        alert_ids = [a['id'] for a in data['alerts']]
        # Invoiced project with no service logs will trigger alert 3
        self.assertIn('invoiced_no_logs', alert_ids)

    @patch('django.utils.timezone.localdate')
    def test_exact_range_pnl_14_oct_daily_rate(self, mock_localdate):
        """
        On 14 Oct, this_month vs previous uses 14-day periods;
        payroll for both is 14 days of daily rate.
        """
        mock_localdate.return_value = datetime.date(2026, 10, 14)
        self.accountant_emp.salary = Decimal('0.00')
        self.accountant_emp.save()

        # Active employee with salary 3100 (in 31-day October -> 100/day)
        emp = Employee.objects.create(
            user=User.objects.create_user('eng_14oct', 'eng_14oct@example.com', 'pass1234'),
            is_employee=True,
            salary=Decimal('3100.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )

        # September has actual Payroll: 3000.00 (in 30-day September -> 100/day)
        Payroll.objects.create(
            employee=emp,
            month=datetime.date(2026, 9, 1),
            basic_salary=Decimal('3000.00'),
            allowances=Decimal('0.00'),
            ot_amount=Decimal('0.00'),
            status='APPROVED',
            created_by=self.accountant_user,
        )

        data = get_dashboard_data(period='this_month')
        self.assertEqual(data['start_date'], datetime.date(2026, 10, 1))
        self.assertEqual(data['end_date'], datetime.date(2026, 10, 14))

        curr_pnl = pnl_for_range(datetime.date(2026, 10, 1), datetime.date(2026, 10, 14), granularity='range')
        # 14 days of 100.00 = 1400.00
        self.assertEqual(curr_pnl['totals']['payroll_cost'], Decimal('1400.00'))

        # Previous period: 17 Sep - 30 Sep (14 days)
        prev_pnl = pnl_for_range(datetime.date(2026, 9, 17), datetime.date(2026, 9, 30), granularity='range')
        # 14 days of 100.00 = 1400.00
        self.assertEqual(prev_pnl['totals']['payroll_cost'], Decimal('1400.00'))

        # KPI cards use exact range
        kpis = data['kpis']
        self.assertEqual(kpis['total_cost']['value'], Decimal('1400.00'))
        self.assertEqual(kpis['total_cost']['prev'], Decimal('1400.00'))
        self.assertIsNone(kpis['payroll_pct_revenue']['value'])
        self.assertIsNone(kpis['rev_per_engineer_day']['value'])
        self.assertEqual(data['prev_start'], datetime.date(2026, 9, 17))
        self.assertEqual(data['prev_end'], datetime.date(2026, 9, 30))

    def test_dashboard_zero_revenue_rendering_and_invalid_date_validation(self):
        self.client.force_login(self.accountant_user)
        res = self.client.get(reverse('fin_dashboard'))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertNotIn('None%', content)
        self.assertNotIn('0.0%', content)
        self.assertIn('Revenue', content)
        self.assertIn('Payroll % of Revenue', content)

        # Test start_date > end_date validation fallback
        res_invalid = self.client.get(reverse('fin_dashboard') + '?period=custom&start_date=2026-10-20&end_date=2026-10-10')
        self.assertEqual(res_invalid.status_code, 200)
        content_invalid = res_invalid.content.decode('utf-8')
        self.assertIn('Start date cannot be after end date', content_invalid)

    def test_break_even_known_numbers_and_unreachable_margin(self):
        """
        Break-even:
        contribution_margin = (revenue − project_direct_cost) / revenue over valid months.
        break_even_revenue = avg_monthly_fixed / contribution_margin.
        Test with known numbers: fixed 100,000; revenue 300,000; direct 60,000 -> CM 80% -> 125,000.
        Also test contribution_margin <= 0 -> break_even_revenue is None.
        """
        # Set up a past full month with known numbers
        m_start = datetime.date(2026, 5, 1)
        m_end = datetime.date(2026, 5, 31)

        # Revenue 300,000
        Project.objects.create(
            name='Break Even Test Project',
            client_name='Test Marine',
            invoice_amount=Decimal('300000.00'),
            invoice_date=datetime.date(2026, 5, 10),
            invoice_number='INV-BE-01',
        )

        # Project direct expense: 60,000
        cat_proj = ExpenseCategory.objects.create(name='Direct Marine Work', kind='PROJECT')
        Expense.objects.create(
            date=datetime.date(2026, 5, 15),
            amount=Decimal('60000.00'),
            category=cat_proj,
            created_by=self.accountant_user,
        )

        # Fixed costs: Overhead 40,000 + Payroll 60,000 = 100,000
        cat_over = ExpenseCategory.objects.create(name='Office Rent BE', kind='OVERHEAD')
        Expense.objects.create(
            date=datetime.date(2026, 5, 15),
            amount=Decimal('40000.00'),
            category=cat_over,
            created_by=self.accountant_user,
        )

        emp_be = Employee.objects.create(
            user=User.objects.create_user('eng_be', 'eng_be@example.com', 'pass1234'),
            is_employee=True,
            salary=Decimal('60000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )
        Payroll.objects.create(
            employee=emp_be,
            month=m_start,
            basic_salary=Decimal('60000.00'),
            allowances=Decimal('0.00'),
            ot_amount=Decimal('0.00'),
            status='APPROVED',
            created_by=self.accountant_user,
        )

        # Call pnl_for_range for this valid month
        pnl = pnl_for_range(m_start, m_end, granularity='month')
        valid_b = pnl['buckets']
        self.assertEqual(len(valid_b), 1)
        b = valid_b[0]

        tot_rev = b['revenue']
        tot_dir = b['project_direct_cost']
        fixed_cost = b['overhead_cost'] + b['payroll_cost']

        self.assertEqual(tot_rev, Decimal('300000.00'))
        self.assertEqual(tot_dir, Decimal('60000.00'))
        self.assertEqual(fixed_cost, Decimal('100000.00'))

        cm = (tot_rev - tot_dir) / tot_rev
        self.assertEqual(cm, Decimal('0.8'))  # 80%

        be_rev = round(fixed_cost / cm, 2)
        self.assertEqual(be_rev, Decimal('125000.00'))

    def test_alerts_completed_project_and_paid_payroll(self):
        """
        - Completed alert: status='COMPLETED' (and link with status=COMPLETED).
        - Payroll alert: a month is OK if it has any Payroll with status in ('APPROVED', 'PAID').
          PAID month is not flagged.
        - Alert links are generated via reverse and urlencode.
        """
        # Completed project not invoiced
        Project.objects.create(
            name='Completed Uninvoiced',
            status='COMPLETED',
            invoice_amount=Decimal('0.00'),
        )

        # Paid payroll for past month
        past_month = datetime.date(2026, 1, 1)
        Payroll.objects.create(
            employee=self.accountant_emp,
            month=past_month,
            basic_salary=Decimal('5000.00'),
            allowances=Decimal('0.00'),
            ot_amount=Decimal('0.00'),
            status='PAID',
            created_by=self.accountant_user,
        )

        data = get_dashboard_data(period='this_month')
        alerts = {a['id']: a for a in data['alerts']}

        self.assertIn('completed_no_invoice', alerts)
        comp_alert = alerts['completed_no_invoice']
        self.assertEqual(comp_alert['link'], reverse('fin_invoices'))

        # Alert 5: since January 2026 has PAID payroll, it must not be in unapproved months
        if 'unapproved_payroll' in alerts:
            self.assertNotIn('2026-01', alerts['unapproved_payroll']['description'])

    def test_cost_doughnut_slices_sum_to_total_cost(self):
        """
        Cost doughnut chart slices must sum exactly to total_cost.
        """
        # Create expenses
        cat_over = ExpenseCategory.objects.create(name='General Utilities', kind='OVERHEAD')
        Expense.objects.create(
            date=datetime.date(self.today.year, self.today.month, 2),
            amount=Decimal('1250.50'),
            category=cat_over,
            created_by=self.accountant_user,
        )

        data = get_dashboard_data(period='this_month')
        doughnut = data['cost_doughnut']
        total_kpi_cost = float(data['kpis']['total_cost']['value'])

        doughnut_sum = sum(doughnut['values'])
        self.assertAlmostEqual(doughnut_sum, total_kpi_cost, places=2)

    def test_employee_cost_consistency_with_pnl_and_rejected_attendance(self):
        """
        - Attendance hours: exclude status='REJECTED'.
        - Σ payroll_cost on Employee Cost == P&L payroll_cost for the same range (full months).
        """
        m_start = datetime.date(2026, 7, 1)
        m_end = datetime.date(2026, 7, 31)

        eng = Employee.objects.create(
            user=User.objects.create_user('eng_july', 'eng_july@example.com', 'pass1234'),
            is_employee=True,
            salary=Decimal('7000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )

        # Rejected attendance
        Attendance.objects.create(
            employee=eng,
            login_time=timezone.make_aware(datetime.datetime(2026, 7, 5, 8, 0)),
            log_out_time=timezone.make_aware(datetime.datetime(2026, 7, 5, 17, 0)),
            total_hours_of_work=9.0,
            status='REJECTED',
        )

        # Approved attendance
        Attendance.objects.create(
            employee=eng,
            login_time=timezone.make_aware(datetime.datetime(2026, 7, 6, 8, 0)),
            log_out_time=timezone.make_aware(datetime.datetime(2026, 7, 6, 16, 0)),
            total_hours_of_work=8.0,
            status='APPROVED',
        )

        # Payroll row for July
        Payroll.objects.create(
            employee=eng,
            month=m_start,
            basic_salary=Decimal('7000.00'),
            allowances=Decimal('500.00'),
            ot_amount=Decimal('250.00'),
            status='APPROVED',
            created_by=self.accountant_user,
        )

        # Compare Employee Cost and P&L for full month
        pnl = pnl_for_range(m_start, m_end, granularity='month')
        rows, totals = get_employee_cost_data(m_start, m_end)

        pnl_payroll = pnl['totals']['payroll_cost']
        emp_cost_payroll = totals['total_payroll_cost']
        self.assertEqual(emp_cost_payroll, pnl_payroll)

        # Check rejected attendance excluded
        eng_row = next(r for r in rows if r['employee'].id == eng.id)
        # Only the 8.0 approved hours should be counted in attendance, not the 9.0 rejected
        self.assertEqual(eng_row['office_hours'], Decimal('8.00'))


class PhaseAInvoiceAndImportTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser('admin_a', 'admin_a@example.com', 'pass1234')
        self.accountant_user = User.objects.create_user('acc_a', 'acc_a@example.com', 'pass1234')
        self.accountant_emp = Employee.objects.create(
            user=self.accountant_user,
            is_accountant=True,
            salary=Decimal('5000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )
        self.today = timezone.localdate()

    def test_invoice_status_rules(self):
        # 1. OPEN: Not paid, due in future
        inv_open = Invoice.objects.create(
            invoice_number='INV-OPEN-1',
            invoice_date=self.today,
            due_date=self.today + datetime.timedelta(days=30),
            amount_excl_vat=Decimal('1000.00'),
            amount_received=Decimal('0.00'),
        )
        self.assertEqual(inv_open.status, 'OPEN')
        self.assertEqual(inv_open.outstanding, Decimal('1000.00'))

        # 2. PARTIAL: Partial payment received
        inv_partial = Invoice.objects.create(
            invoice_number='INV-PARTIAL-1',
            invoice_date=self.today,
            due_date=self.today + datetime.timedelta(days=30),
            amount_excl_vat=Decimal('1000.00'),
            amount_received=Decimal('350.00'),
        )
        self.assertEqual(inv_partial.status, 'PARTIAL')
        self.assertEqual(inv_partial.outstanding, Decimal('650.00'))

        # 3. PAID: Outstanding is 0
        inv_paid = Invoice.objects.create(
            invoice_number='INV-PAID-1',
            invoice_date=self.today,
            due_date=self.today + datetime.timedelta(days=30),
            amount_excl_vat=Decimal('1000.00'),
            amount_received=Decimal('1000.00'),
        )
        self.assertEqual(inv_paid.status, 'PAID')
        self.assertEqual(inv_paid.outstanding, Decimal('0.00'))

        # 4. PAID via credits applied
        inv_paid_credits = Invoice.objects.create(
            invoice_number='INV-PAID-CRED-1',
            invoice_date=self.today,
            due_date=self.today + datetime.timedelta(days=30),
            amount_excl_vat=Decimal('1000.00'),
            credits_applied=Decimal('400.00'),
            amount_received=Decimal('600.00'),
        )
        self.assertEqual(inv_paid_credits.collection_value, Decimal('600.00'))
        self.assertEqual(inv_paid_credits.outstanding, Decimal('0.00'))
        self.assertEqual(inv_paid_credits.status, 'PAID')

        # 5. OVERDUE: Today > due_date and outstanding > 0
        inv_overdue = Invoice.objects.create(
            invoice_number='INV-OVERDUE-1',
            invoice_date=self.today - datetime.timedelta(days=45),
            due_date=self.today - datetime.timedelta(days=15),
            amount_excl_vat=Decimal('1000.00'),
            amount_received=Decimal('200.00'),
        )
        self.assertEqual(inv_overdue.status, 'OVERDUE')
        self.assertEqual(inv_overdue.outstanding, Decimal('800.00'))

        # 6. CANCELLED: is_cancelled is True (takes precedence)
        inv_cancelled = Invoice.objects.create(
            invoice_number='INV-CANCELLED-1',
            invoice_date=self.today - datetime.timedelta(days=45),
            due_date=self.today - datetime.timedelta(days=15),
            amount_excl_vat=Decimal('1000.00'),
            amount_received=Decimal('0.00'),
            is_cancelled=True,
        )
        self.assertEqual(inv_cancelled.status, 'CANCELLED')

    def test_collection_value_and_outstanding(self):
        # As in prompt: 32,000.00 with 1,800 credit -> collection value 30,200.00
        inv = Invoice.objects.create(
            invoice_number='INV-CRED-TEST',
            invoice_date=self.today,
            amount_excl_vat=Decimal('32000.00'),
            credits_applied=Decimal('1800.00'),
            amount_received=Decimal('10000.00'),
        )
        self.assertEqual(inv.collection_value, Decimal('30200.00'))
        self.assertEqual(inv.outstanding, Decimal('20200.00'))

        # Amount received exceeds collection value -> outstanding cannot be negative
        inv.amount_received = Decimal('35000.00')
        inv.save()
        self.assertEqual(inv.outstanding, Decimal('0.00'))

    def test_spare_profit_markup_and_alerts(self):
        # Real example: MT TALIA: cost 2,340.00, selling 2,900.00 -> profit 560.00, markup 23.9%
        spare_normal = SpareSale.objects.create(
            service_ref='SR-TALIA',
            vessel_customer='MT TALIA',
            purchase_cost=Decimal('2340.00'),
            selling_price=Decimal('2900.00'),
        )
        self.assertEqual(spare_normal.profit, Decimal('560.00'))
        self.assertAlmostEqual(float(spare_normal.markup_pct), 23.9316, places=2)
        self.assertEqual(spare_normal.alerts, [])

        # Loss on spares
        spare_loss = SpareSale.objects.create(
            service_ref='SR-LOSS',
            purchase_cost=Decimal('500.00'),
            selling_price=Decimal('400.00'),
        )
        self.assertIn("Loss on spares", spare_loss.alerts)

        # Low markup (< 10%)
        spare_low = SpareSale.objects.create(
            service_ref='SR-LOW',
            purchase_cost=Decimal('1000.00'),
            selling_price=Decimal('1050.00'),
        )
        self.assertIn("Low markup", spare_low.alerts)

        # Check markup (> 200%)
        spare_high = SpareSale.objects.create(
            service_ref='SR-HIGH',
            purchase_cost=Decimal('100.00'),
            selling_price=Decimal('400.00'),
        )
        self.assertIn("Check markup", spare_high.alerts)

        # Missing costs
        spare_no_cost = SpareSale.objects.create(
            service_ref='SR-NO-COST',
            purchase_cost=Decimal('0.00'),
            selling_price=Decimal('200.00'),
        )
        self.assertIsNone(spare_no_cost.markup_pct)
        self.assertIn("Missing purchase cost", spare_no_cost.alerts)

        spare_no_price = SpareSale.objects.create(
            service_ref='SR-NO-PRICE',
            purchase_cost=Decimal('200.00'),
            selling_price=Decimal('0.00'),
        )
        self.assertIn("Missing selling price", spare_no_price.alerts)

        # Not invoiced (> 14 days ago)
        spare_old = SpareSale.objects.create(
            service_ref='SR-OLD',
            purchase_cost=Decimal('500.00'),
            selling_price=Decimal('700.00'),
        )
        # Mock created_at
        SpareSale.objects.filter(id=spare_old.id).update(
            created_at=timezone.now() - datetime.timedelta(days=20)
        )
        spare_old.refresh_from_db()
        self.assertIn("Not invoiced", spare_old.alerts)

        # Invoice spares mismatch
        inv_spares = Invoice.objects.create(
            invoice_number='INV-SPARES-1',
            invoice_date=self.today,
            invoice_type='SPARES_ONLY',
            amount_excl_vat=Decimal('1200.00'),
        )
        spare_linked = SpareSale.objects.create(
            service_ref='SR-SP-LINK',
            invoice=inv_spares,
            purchase_cost=Decimal('800.00'),
            selling_price=Decimal('1000.00'),  # 1000 != 1200
        )
        self.assertEqual(spare_linked.status, 'INVOICED')
        self.assertIn("Invoice spares mismatch", spare_linked.alerts)

    def test_data_migration_from_project(self):
        p1 = Project.objects.create(
            name='Legacy Project 1',
            client_name='Client Legacy',
            vessel_name='Vessel Legacy',
            service_reference='SR-LEG-01',
            invoice_amount=Decimal('15000.00'),
            amount_received=Decimal('5000.00'),
            purchase_and_expenses=Decimal('2500.00'),
            invoice_number='INV-LEG-001',
            invoice_date=datetime.date(2026, 2, 1),
            payment_received_date=datetime.date(2026, 2, 20),
        )
        p2 = Project.objects.create(
            name='Legacy Project 2 No Inv Num',
            client_name='Client 2',
            vessel_name='Vessel 2',
            service_reference='SR-LEG-02',
            invoice_amount=Decimal('8000.00'),
            amount_received=Decimal('0.00'),
            purchase_and_expenses=Decimal('0.00'),
            invoice_number='',
        )

        migrate_projects_to_invoices(django_apps, None)

        inv1 = Invoice.objects.get(invoice_number='INV-LEG-001')
        self.assertEqual(inv1.project, p1)
        self.assertEqual(inv1.amount_excl_vat, Decimal('15000.00'))
        self.assertEqual(inv1.amount_received, Decimal('5000.00'))
        self.assertEqual(inv1.payment_received_date, datetime.date(2026, 2, 20))
        self.assertEqual(inv1.service_ref, 'SR-LEG-01')
        self.assertEqual(inv1.vessel, 'Vessel Legacy')
        self.assertEqual(inv1.customer, 'Client Legacy')

        job_costs = inv1.job_costs.all()
        self.assertEqual(job_costs.count(), 1)
        jc = job_costs.first()
        self.assertEqual(jc.cost_type, 'OTHER')
        self.assertEqual(jc.amount, Decimal('2500.00'))
        self.assertEqual(jc.description, 'Legacy purchases')

        inv2 = Invoice.objects.get(invoice_number=f'LEGACY-{p2.id}')
        self.assertEqual(inv2.project, p2)
        self.assertEqual(inv2.amount_excl_vat, Decimal('8000.00'))
        self.assertEqual(inv2.job_costs.count(), 0)

    def test_import_dry_run_vs_real_run(self):
        invoices_csv = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Amount Without Tax,Credits Applied,Notes\n"
            "OPEN,2026-06-01,SR-DRY,Vessel Dry,INV-DRY-01,5000.00,200.00,Dry run invoice\n"
        )
        jobs_csv = (
            "Service Ref,Vessel,Invoice No,Invoice,Engineer,Spare Cost,Travel Port,Travel Cost,Flight,Misc Cost,Other,Notes\n"
            "SR-DRY,Vessel Dry,INV-DRY-01,4800.00,600.00,0.00,Khorfakkan,150.00,300.00,50.00,25.00,Dry job notes\n"
        )
        spares_csv = (
            "Service Ref,Vessel / Customer,Source,Purchase Cost,Actual Selling Price,Invoice No,Status,Notes\n"
            "SR-DRY,Vessel Dry / Client Dry,SERVICE_JOB,1000.00,1500.00,INV-DRY-01,INVOICED,Dry spare notes\n"
        )

        inv_file = io.StringIO(invoices_csv)
        jobs_file = io.StringIO(jobs_csv)
        spares_file = io.StringIO(spares_csv)

        # Dry run test
        res_dry = import_finance_sheets_data(
            invoices_file=inv_file,
            jobs_file=jobs_file,
            spares_file=spares_file,
            dry_run=True,
            user=self.accountant_user,
        )
        self.assertEqual(res_dry['created_invoices'], 1)
        self.assertEqual(res_dry['created_job_costs'], 4)
        self.assertEqual(res_dry['created_spares'], 1)

        # Database must NOT contain any rows because of rollback
        self.assertFalse(Invoice.objects.filter(invoice_number='INV-DRY-01').exists())
        self.assertFalse(JobCost.objects.filter(travel_port='Khorfakkan').exists())
        self.assertFalse(SpareSale.objects.filter(service_ref='SR-DRY').exists())

        # Real run test
        inv_file.seek(0)
        jobs_file.seek(0)
        spares_file.seek(0)

        res_real = import_finance_sheets_data(
            invoices_file=inv_file,
            jobs_file=jobs_file,
            spares_file=spares_file,
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res_real['created_invoices'], 1)
        self.assertEqual(res_real['created_job_costs'], 4)
        self.assertEqual(res_real['created_spares'], 1)

        # Rows MUST now exist in DB
        self.assertTrue(Invoice.objects.filter(invoice_number='INV-DRY-01').exists())
        inv = Invoice.objects.get(invoice_number='INV-DRY-01')
        self.assertEqual(inv.amount_excl_vat, Decimal('5000.00'))
        self.assertEqual(inv.credits_applied, Decimal('200.00'))
        self.assertEqual(inv.collection_value, Decimal('4800.00'))

        self.assertEqual(inv.job_costs.count(), 4)
        travel_cost = inv.job_costs.get(cost_type='TRAVEL')
        self.assertEqual(travel_cost.amount, Decimal('150.00'))
        self.assertEqual(travel_cost.travel_port, 'Khorfakkan')

        spare = SpareSale.objects.get(invoice=inv)
        self.assertEqual(spare.purchase_cost, Decimal('1000.00'))
        self.assertEqual(spare.selling_price, Decimal('1500.00'))
        self.assertEqual(spare.status, 'INVOICED')

    def test_duplicate_invoice_numbers_updated_not_duplicated(self):
        invoices_csv_1 = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Amount Without Tax,Credits Applied,Notes\n"
            "OPEN,2026-06-01,SR-DUP,Vessel Dup,INV-DUP-99,4000.00,0.00,Initial\n"
        )
        import_finance_sheets_data(
            invoices_file=io.StringIO(invoices_csv_1),
            jobs_file=None,
            spares_file=None,
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(Invoice.objects.filter(invoice_number='INV-DUP-99').count(), 1)
        self.assertEqual(Invoice.objects.get(invoice_number='INV-DUP-99').amount_excl_vat, Decimal('4000.00'))

        # Import again with updated amount and credits
        invoices_csv_2 = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Amount Without Tax,Credits Applied,Notes\n"
            "OPEN,2026-06-01,SR-DUP,Vessel Dup,INV-DUP-99,6500.00,500.00,Updated\n"
        )
        res2 = import_finance_sheets_data(
            invoices_file=io.StringIO(invoices_csv_2),
            jobs_file=None,
            spares_file=None,
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res2['updated_invoices'], 1)
        self.assertEqual(Invoice.objects.filter(invoice_number='INV-DUP-99').count(), 1)
        inv = Invoice.objects.get(invoice_number='INV-DUP-99')
        self.assertEqual(inv.amount_excl_vat, Decimal('6500.00'))
        self.assertEqual(inv.credits_applied, Decimal('500.00'))

    def test_management_command_import_finance_sheets(self):
        import tempfile
        with tempfile.NamedTemporaryFile('w', delete=False, suffix='.csv') as f_inv:
            f_inv.write("Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Amount Without Tax,Credits Applied\n"
                        "OPEN,2026-06-01,SR-CMD,Vessel Cmd,INV-CMD-1,3000.00,0.00\n")
            inv_path = f_inv.name

        try:
            # Dry run via call_command
            out = io.StringIO()
            call_command('import_finance_sheets', invoices=inv_path, dry_run=True, stdout=out)
            self.assertIn("DRY RUN", out.getvalue())
            self.assertFalse(Invoice.objects.filter(invoice_number='INV-CMD-1').exists())

            # Real run via call_command
            out_real = io.StringIO()
            call_command('import_finance_sheets', invoices=inv_path, stdout=out_real)
            self.assertIn("SUCCESS", out_real.getvalue())
            self.assertTrue(Invoice.objects.filter(invoice_number='INV-CMD-1').exists())
        finally:
            import os
            if os.path.exists(inv_path):
                os.remove(inv_path)

    def test_import_view_preview_and_execute(self):
        self.client.force_login(self.accountant_user)
        # GET import page
        resp_get = self.client.get(reverse('fin_import'))
        self.assertEqual(resp_get.status_code, 200)
        self.assertContains(resp_get, "Import Finance Data")

        # POST preview (dry run)
        csv_content = b"Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Amount Without Tax,Credits Applied\nOPEN,2026-06-01,SR-VIEW,Vessel View,INV-VIEW-1,4500.00,100.00\n"
        upload_file = SimpleUploadedFile("invoices.csv", csv_content, content_type="text/csv")
        resp_preview = self.client.post(reverse('fin_import'), {
            'action': 'preview',
            'invoices_file': upload_file,
        })
        self.assertEqual(resp_preview.status_code, 200)
        self.assertContains(resp_preview, "INV-VIEW-1")
        self.assertFalse(Invoice.objects.filter(invoice_number='INV-VIEW-1').exists())

        # POST execute
        upload_file.seek(0)
        resp_exec = self.client.post(reverse('fin_import'), {
            'action': 'execute',
            'invoices_file': upload_file,
        })
        self.assertEqual(resp_exec.status_code, 200)
        self.assertContains(resp_exec, "Successfully imported")
        self.assertTrue(Invoice.objects.filter(invoice_number='INV-VIEW-1').exists())

    def test_parse_amount_helper(self):
        self.assertEqual(parse_amount("2,900.00"), Decimal('2900.00'))
        self.assertEqual(parse_amount("(203.79)"), Decimal('-203.79'))
        self.assertEqual(parse_amount("-"), Decimal('0.00'))
        self.assertEqual(parse_amount(""), Decimal('0.00'))
        self.assertEqual(parse_amount("—"), Decimal('0.00'))
        self.assertEqual(parse_amount(None), Decimal('0.00'))
        self.assertEqual(parse_amount("100.0%"), Decimal('100.0'))
        self.assertEqual(parse_amount(2900), Decimal('2900'))
        self.assertEqual(parse_amount(2900.5), Decimal('2900.5'))
        self.assertEqual(parse_amount(Decimal('123.45')), Decimal('123.45'))

    def test_parse_sheet_date_helper(self):
        self.assertEqual(parse_sheet_date("10-Jan-2026"), datetime.date(2026, 1, 10))
        self.assertEqual(parse_sheet_date("2026-01-10"), datetime.date(2026, 1, 10))
        self.assertEqual(parse_sheet_date("10/01/2026"), datetime.date(2026, 1, 10))
        excel_dt = datetime.datetime(2026, 1, 10, 8, 30)
        self.assertEqual(parse_sheet_date(excel_dt), datetime.date(2026, 1, 10))
        excel_d = datetime.date(2026, 1, 10)
        self.assertEqual(parse_sheet_date(excel_d), datetime.date(2026, 1, 10))
        self.assertIsNone(parse_sheet_date("not-a-date"))
        self.assertIsNone(parse_sheet_date(""))
        self.assertIsNone(parse_sheet_date(None))

    def test_import_spares_with_note_rows_above_header(self):
        # Spares Accounting sheet has notes in rows 1-2 and headers in row 3
        spares_csv = (
            "Notes: Spares Accounting internal price list 2026,,\n"
            "Confidential - do not distribute without authorization,,\n"
            "Service Ref,Vessel / Customer,Source,Purchase Cost,Actual Selling Price,Invoice No,Status,Notes\n"
            'SR-NOTE-99,MT TALIA / Marine Services,SERVICE_JOB,"2,340.00","2,900.00",INV-SP-NOTE,INVOICED,Good markup\n'
        )
        res = import_finance_sheets_data(
            spares_file=io.StringIO(spares_csv),
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res['created_spares'], 1)
        spare = SpareSale.objects.get(service_ref='SR-NOTE-99')
        self.assertEqual(spare.purchase_cost, Decimal('2340.00'))
        self.assertEqual(spare.selling_price, Decimal('2900.00'))
        self.assertEqual(spare.profit, Decimal('560.00'))

    def test_import_invoice_paid_status_sets_received_and_never_lowers(self):
        # 1. "Paid" -> amount_received = collection_value (payment_received_date left empty)
        # "Over Due" -> amount_received = 0
        csv_content = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Amount Without Tax,Credits Applied\n"
            'Paid,10-Jan-2026,SR-PD-1,Vessel P1,INV-PD-1,"5,000.00",500.00\n'
            'Over Due,10-Jan-2026,SR-OD-1,Vessel OD,INV-OD-1,"3,000.00",0.00\n'
        )
        res = import_finance_sheets_data(
            invoices_file=io.StringIO(csv_content),
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res['created_invoices'], 2)
        inv_paid = Invoice.objects.get(invoice_number='INV-PD-1')
        self.assertEqual(inv_paid.collection_value, Decimal('4500.00'))
        self.assertEqual(inv_paid.amount_received, Decimal('4500.00'))
        self.assertIsNone(inv_paid.payment_received_date)

        inv_od = Invoice.objects.get(invoice_number='INV-OD-1')
        self.assertEqual(inv_od.amount_received, Decimal('0.00'))

        # 2. Never lower an amount_received that is already higher in the DB
        inv_paid.amount_received = Decimal('7000.00')
        inv_paid.save()

        # Re-import sheet where INV-PD-1 has lower collection_value (4500.00) or Over Due
        csv_reimport = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Amount Without Tax,Credits Applied\n"
            'Over Due,10-Jan-2026,SR-PD-1,Vessel P1,INV-PD-1,"5,000.00",500.00\n'
        )
        import_finance_sheets_data(
            invoices_file=io.StringIO(csv_reimport),
            dry_run=False,
            user=self.accountant_user,
        )
        inv_paid.refresh_from_db()
        self.assertEqual(inv_paid.amount_received, Decimal('7000.00'))

    def test_data_migration_duplicate_invoice_number_and_reverse(self):
        p_a = Project.objects.create(
            name='Project Dup A',
            invoice_amount=Decimal('5000.00'),
            invoice_number='INV-SHARED-01',
            service_reference='SR-DUP-A',
        )
        p_b = Project.objects.create(
            name='Project Dup B',
            invoice_amount=Decimal('8000.00'),
            invoice_number='INV-SHARED-01',
            service_reference='SR-DUP-B',
        )

        migrate_projects_to_invoices(django_apps, None)

        # First project keeps INV-SHARED-01
        self.assertTrue(Invoice.objects.filter(invoice_number='INV-SHARED-01').exists())
        inv_a = Invoice.objects.get(invoice_number='INV-SHARED-01')
        self.assertEqual(inv_a.project, p_a)

        # Second project is renamed to <number>-P<project.id>
        expected_dup_number = f"INV-SHARED-01-P{p_b.id}"
        self.assertTrue(Invoice.objects.filter(invoice_number=expected_dup_number).exists())
        inv_b = Invoice.objects.get(invoice_number=expected_dup_number)
        self.assertEqual(inv_b.project, p_b)

        # Test reverse migration
        reverse_migrate_projects_to_invoices(django_apps, None)
        self.assertFalse(Invoice.objects.filter(invoice_number='INV-SHARED-01').exists())
        self.assertFalse(Invoice.objects.filter(invoice_number=expected_dup_number).exists())

    def test_sparesale_list_query_count_with_alerts(self):
        inv = Invoice.objects.create(
            invoice_number='INV-SP-ALERT-TEST',
            invoice_date=self.today,
            amount_excl_vat=Decimal('10000.00'),
        )
        proj = Project.objects.create(name='Proj Alert Test')

        for i in range(10):
            SpareSale.objects.create(
                service_ref=f'SR-QC-{i}',
                vessel_customer=f'Vessel QC {i}',
                invoice=inv if i % 2 == 0 else None,
                project=proj if i % 3 == 0 else None,
                purchase_cost=Decimal(f'{100 + i * 50}.00'),
                selling_price=Decimal(f'{120 + i * 60}.00'),
            )

        with CaptureQueriesContext(connection) as ctx_10:
            spares_10 = list(SpareSale.objects.all().select_related('invoice', 'project'))
            all_alerts_10 = [s.alerts for s in spares_10]

        select_queries_10 = [q for q in ctx_10.captured_queries if q['sql'].strip().upper().startswith('SELECT')]
        self.assertEqual(len(select_queries_10), 1)

        for i in range(10, 20):
            SpareSale.objects.create(
                service_ref=f'SR-QC-{i}',
                vessel_customer=f'Vessel QC {i}',
                invoice=inv if i % 2 == 0 else None,
                project=proj if i % 3 == 0 else None,
                purchase_cost=Decimal(f'{100 + i * 50}.00'),
                selling_price=Decimal(f'{120 + i * 60}.00'),
            )

        with CaptureQueriesContext(connection) as ctx_20:
            spares_20 = list(SpareSale.objects.all().select_related('invoice', 'project'))
            all_alerts_20 = [s.alerts for s in spares_20]

        select_queries_20 = [q for q in ctx_20.captured_queries if q['sql'].strip().upper().startswith('SELECT')]
        self.assertEqual(len(select_queries_20), 1)


class PhaseBFinancialDataAndB5Tests(TestCase):
    def setUp(self):
        self.client = Client()
        self.accountant_user = User.objects.create_user('acc_b', 'acc_b@example.com', 'pass1234')
        self.accountant_emp = Employee.objects.create(
            user=self.accountant_user,
            is_accountant=True,
            salary=Decimal('6000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )
        self.client.force_login(self.accountant_user)
        self.today = timezone.localdate()

    def test_b5_northern_star_dataset(self):
        # NORTHERN STAR:
        # Revenue 9,525.00, other job cost 106.70, labour cost 5,811.63
        # Total cost = 5,918.33, profit = 3,606.67, margin = 37.9%
        eng = Employee.objects.create(
            user=User.objects.create_user('eng_ns', 'eng_ns@example.com', 'pass1234'),
            is_employee=True,
            salary=Decimal('5000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )
        proj = Project.objects.create(name='Job Northern Star', vessel_name='NORTHERN STAR', service_reference='SR-NS-01')
        inv = Invoice.objects.create(
            invoice_number='INV-NS-01',
            invoice_date=self.today,
            project=proj,
            service_ref='SR-NS-01',
            vessel='NORTHERN STAR',
            amount_excl_vat=Decimal('9525.00'),
        )
        JobCost.objects.create(
            invoice=inv,
            cost_type='OTHER',
            amount=Decimal('106.70'),
            description='Port charges',
            date=self.today,
        )
        log = EmployeeServiceLog.objects.create(
            employee=eng,
            project=proj,
            date=self.today,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(17, 0),
            service_type='JOB',
        )
        EmployeeServiceLog.objects.filter(id=log.id).update(normal_cost=Decimal('5811.63'), ot_cost=Decimal('0.00'))

        rows, totals = get_jobs_pnl_data()
        ns_row = next((r for r in rows if r['invoice'].id == inv.id), None)
        self.assertIsNotNone(ns_row)
        self.assertEqual(ns_row['net_invoice'], Decimal('9525.00'))
        self.assertEqual(ns_row['other'], Decimal('106.70'))
        self.assertEqual(ns_row['engineer'], Decimal('5811.63'))
        self.assertEqual(ns_row['total_cost'], Decimal('5918.33'))
        self.assertEqual(ns_row['profit'], Decimal('3606.67'))
        self.assertEqual(ns_row['margin_pct_display'], '37.9%')

    def test_b5_mt_pate_loss_making(self):
        # MT PATE:
        # Revenue 4,050.00, total cost 4,253.79, profit (203.79), margin (5.0%)
        proj = Project.objects.create(name='Job MT Pate', vessel_name='MT PATE', service_reference='SR-PATE-01')
        inv = Invoice.objects.create(
            invoice_number='INV-PATE-01',
            invoice_date=self.today,
            project=proj,
            service_ref='SR-PATE-01',
            vessel='MT PATE',
            amount_excl_vat=Decimal('4050.00'),
        )
        JobCost.objects.create(
            invoice=inv,
            cost_type='OTHER',
            amount=Decimal('4253.79'),
            description='Turbine overhaul kit',
            date=self.today,
        )

        rows, totals = get_jobs_pnl_data()
        pate_row = next((r for r in rows if r['invoice'].id == inv.id), None)
        self.assertIsNotNone(pate_row)
        self.assertEqual(pate_row['net_invoice'], Decimal('4050.00'))
        self.assertEqual(pate_row['total_cost'], Decimal('4253.79'))
        self.assertEqual(pate_row['profit'], Decimal('-203.79'))
        self.assertEqual(pate_row['profit_display'], '(203.79)')
        self.assertEqual(pate_row['margin_pct_display'], '(5.0%)')
        self.assertTrue(pate_row['is_loss'])

    def test_b5_credits_applied(self):
        # 32,000.00 with 1,800.00 credits -> collection value 30,200.00
        inv = Invoice.objects.create(
            invoice_number='INV-CRED-32K',
            invoice_date=self.today,
            amount_excl_vat=Decimal('32000.00'),
            credits_applied=Decimal('1800.00'),
            amount_received=Decimal('10000.00'),
        )
        self.assertEqual(inv.collection_value, Decimal('30200.00'))
        self.assertEqual(inv.outstanding, Decimal('20200.00'))

        # Post credit application via view
        resp = self.client.post(reverse('fin_invoice_apply_credit', args=[inv.id]), {'credit_amount': '200.00'})
        self.assertEqual(resp.status_code, 302)
        inv.refresh_from_db()
        self.assertEqual(inv.credits_applied, Decimal('2000.00'))
        self.assertEqual(inv.collection_value, Decimal('30000.00'))
        self.assertEqual(inv.outstanding, Decimal('20000.00'))

    def test_b5_spares_mt_talia(self):
        # Spares MT TALIA: cost 2,340, selling 2,900, profit 560, markup 23.9%
        spare = SpareSale.objects.create(
            service_ref='SR-TALIA-01',
            vessel_customer='MT TALIA',
            purchase_cost=Decimal('2340.00'),
            selling_price=Decimal('2900.00'),
        )
        self.assertEqual(spare.profit, Decimal('560.00'))
        self.assertEqual(round(spare.markup_pct, 1), Decimal('23.9'))

        spares_data, summary = get_spares_accounting_data()
        talia_row = next((s for s in spares_data if s.id == spare.id), None)
        self.assertIsNotNone(talia_row)
        self.assertEqual(talia_row.profit, Decimal('560.00'))
        self.assertEqual(round(talia_row.markup_pct, 1), Decimal('23.9'))

    def test_largest_remainder_engineer_cost_split(self):
        eng = Employee.objects.create(
            user=User.objects.create_user('eng_split', 'eng_split@example.com', 'pass1234'),
            is_employee=True,
            salary=Decimal('5000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )
        proj = Project.objects.create(name='Job Split Test', service_reference='SR-SPLIT')
        inv1 = Invoice.objects.create(
            invoice_number='INV-SPLIT-1',
            invoice_date=self.today,
            project=proj,
            amount_excl_vat=Decimal('7000.00'),
        )
        inv2 = Invoice.objects.create(
            invoice_number='INV-SPLIT-2',
            invoice_date=self.today,
            project=proj,
            amount_excl_vat=Decimal('3000.00'),
        )
        log = EmployeeServiceLog.objects.create(
            employee=eng,
            project=proj,
            date=self.today,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(17, 0),
            service_type='JOB',
        )
        EmployeeServiceLog.objects.filter(id=log.id).update(normal_cost=Decimal('100.01'), ot_cost=Decimal('0.00'))

        costs = compute_invoice_engineer_costs([inv1, inv2])
        # Total must match exactly 100.01 down to the cent
        self.assertEqual(costs[inv1.id] + costs[inv2.id], Decimal('100.01'))
        # 70% of 10001 is 7000.7 -> 7001 cents = 70.01; 30% of 10001 is 3000.3 -> 3000 cents = 30.00
        self.assertEqual(costs[inv1.id], Decimal('70.01'))
        self.assertEqual(costs[inv2.id], Decimal('30.00'))

        # When filtered to January only (inv1 only passed), it still gets 70.01 based on DB state
        costs_filtered = compute_invoice_engineer_costs([inv1])
        self.assertEqual(costs_filtered[inv1.id], Decimal('70.01'))
        self.assertNotIn(inv2.id, costs_filtered)

    def test_unpaid_status_does_not_set_amount_received(self):
        csv_data = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Amount Without Tax,Credits Applied,Notes\n"
            "Unpaid,2026-06-01,SR-UNP,Vessel Unpaid,INV-UNP-01,5000.00,0.00,Should remain unpaid\n"
            "Open,2026-06-01,SR-OPN,Vessel Open,INV-OPN-01,3000.00,0.00,Should remain open\n"
        )
        res = import_finance_sheets_data(
            invoices_file=io.StringIO(csv_data),
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res['created_invoices'], 2)
        inv_unp = Invoice.objects.get(invoice_number='INV-UNP-01')
        self.assertEqual(inv_unp.amount_received, Decimal('0.00'))
        self.assertIsNone(inv_unp.payment_received_date)

        inv_opn = Invoice.objects.get(invoice_number='INV-OPN-01')
        self.assertEqual(inv_opn.amount_received, Decimal('0.00'))
        self.assertIsNone(inv_opn.payment_received_date)

    def test_three_tab_auto_workbook_detection(self):
        wb = openpyxl.Workbook()
        # Tab 1: Spares Accounting
        ws_sp = wb.active
        ws_sp.title = "Spares Accounting"
        ws_sp.append(["Service Ref", "Vessel / Customer", "Source", "Purchase Cost", "Actual Selling Price", "Invoice No", "Status", "Notes"])
        ws_sp.append(["SR-AUTO-1", "Vessel Auto", "SERVICE_JOB", "200.00", "300.00", "INV-AUTO-1", "INVOICED", ""])

        # Tab 2: Costs
        ws_co = wb.create_sheet(title="Costs Sheet")
        ws_co.append(["Service Ref", "Vessel", "Invoice No", "Invoice", "Engineer", "Spare Cost", "Travel Port", "Travel Cost", "Flight", "Misc Cost", "Other", "Notes"])
        ws_co.append(["SR-AUTO-1", "Vessel Auto", "INV-AUTO-1", "1000.00", "150.00", "0.00", "Dubai", "75.00", "50.00", "0.00", "25.00", ""])

        # Tab 3: Invoices
        ws_in = wb.create_sheet(title="Invoices Summary")
        ws_in.append(["Status", "Invoice Date", "Project / Service Ref", "VESSEL", "Invoice No", "Amount Without Tax", "Credits Applied", "Notes"])
        ws_in.append(["OPEN", "2026-06-01", "SR-AUTO-1", "Vessel Auto", "INV-AUTO-1", "1000.00", "0.00", ""])

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        upload_file = SimpleUploadedFile("multi_tab.xlsx", buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        res = import_finance_sheets_data(
            excel_file=upload_file,
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res['created_invoices'], 1)
        self.assertEqual(res['created_job_costs'], 3)
        self.assertEqual(res['created_spares'], 1)
        self.assertTrue(Invoice.objects.filter(invoice_number='INV-AUTO-1').exists())
        self.assertTrue(JobCost.objects.filter(invoice__invoice_number='INV-AUTO-1').exists())
        self.assertTrue(SpareSale.objects.filter(service_ref='SR-AUTO-1').exists())

    def test_per_row_atomic_savepoint_skips_invalid_rows(self):
        csv_data = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Amount Without Tax,Credits Applied,Notes\n"
            "OPEN,2026-06-01,SR-VAL-1,Vessel 1,INV-VAL-01,1000.00,0.00,Valid row\n"
            "OPEN,INVALID_DATE_FORMAT,SR-INV-2,Vessel 2,INV-VAL-02,2000.00,0.00,Invalid date\n"
            "OPEN,2026-06-03,SR-VAL-3,Vessel 3,INV-VAL-03,3000.00,0.00,Valid row\n"
        )
        res = import_finance_sheets_data(
            invoices_file=io.StringIO(csv_data),
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res['created_invoices'], 2)
        self.assertTrue(Invoice.objects.filter(invoice_number='INV-VAL-01').exists())
        self.assertFalse(Invoice.objects.filter(invoice_number='INV-VAL-02').exists())
        self.assertTrue(Invoice.objects.filter(invoice_number='INV-VAL-03').exists())
        self.assertTrue(any("Row" in err for err in res['errors']))

    def test_per_row_atomic_savepoint_integrity_error_does_not_abort_subsequent_rows(self):
        csv_data = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Amount Without Tax,Credits Applied,Notes\n"
            "OPEN,2026-06-01,SR-INT-1,Vessel 1,INV-INT-01,1000.00,0.00,Valid row 1\n"
            "OPEN,2026-06-02,SR-INT-2,Vessel 2,INV-INT-02,2000.00,0.00,Integrity error row\n"
            "OPEN,2026-06-03,SR-INT-3,Vessel 3,INV-INT-03,3000.00,0.00,Valid row 3\n"
        )
        orig_get_or_create = Invoice.objects.get_or_create
        def mocked_get_or_create(*args, **kwargs):
            if kwargs.get('invoice_number') == 'INV-INT-02':
                from django.db import IntegrityError
                raise IntegrityError("Simulated duplicate unique key on INV-INT-02")
            return orig_get_or_create(*args, **kwargs)

        with patch.object(Invoice.objects, 'get_or_create', side_effect=mocked_get_or_create):
            res = import_finance_sheets_data(
                invoices_file=io.StringIO(csv_data),
                dry_run=False,
                user=self.accountant_user,
            )

        self.assertEqual(res['created_invoices'], 2)
        self.assertTrue(Invoice.objects.filter(invoice_number='INV-INT-01').exists())
        self.assertFalse(Invoice.objects.filter(invoice_number='INV-INT-02').exists())
        self.assertTrue(Invoice.objects.filter(invoice_number='INV-INT-03').exists())
        self.assertTrue(any("Simulated duplicate unique key" in err for err in res['errors']))

    def test_manual_jobcost_survives_reimport(self):
        inv = Invoice.objects.create(
            invoice_number='INV-MANUAL-SURVIVE',
            invoice_date=self.today,
            amount_excl_vat=Decimal('5000.00'),
        )
        manual_cost = JobCost.objects.create(
            invoice=inv,
            cost_type='OTHER',
            amount=Decimal('450.00'),
            description='Custom manual job fee',
            source='MANUAL',
            date=self.today,
        )

        costs_csv = (
            "Service Ref,Vessel,Invoice No,Invoice,Engineer,Spare Cost,Travel Port,Travel Cost,Flight,Misc Cost,Other,Notes\n"
            "SR-MAN,Vessel Man,INV-MANUAL-SURVIVE,5000.00,200.00,0.00,Sharjah,100.00,200.00,0.00,50.00,Sheet costs\n"
        )
        res = import_finance_sheets_data(
            jobs_file=io.StringIO(costs_csv),
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res['created_job_costs'], 3)
        manual_cost.refresh_from_db()
        self.assertEqual(manual_cost.amount, Decimal('450.00'))
        self.assertEqual(manual_cost.source, 'MANUAL')
        # Total costs on invoice: 1 manual + 3 sheet = 4
        self.assertEqual(inv.job_costs.count(), 4)

    def test_import_warnings_generated(self):
        # 1. 5% VAT discrepancy
        # 2. Duplicate invoice in sheet
        # 3. Missing invoice in costs sheet
        inv_csv = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Amount Without Tax,Credits Applied,VAT\n"
            "OPEN,2026-06-01,SR-WARN-1,Vessel W1,INV-WARN-01,1000.00,0.00,250.00\n"
            "OPEN,2026-06-01,SR-WARN-1,Vessel W1,INV-WARN-01,1000.00,0.00,50.00\n"
        )
        jobs_csv = (
            "Service Ref,Vessel,Invoice No,Invoice,Engineer,Spare Cost,Travel Port,Travel Cost,Flight,Misc Cost,Other,Notes\n"
            "SR-NONEXIST,Vessel X,INV-DOES-NOT-EXIST,2000.00,100.00,0.00,Dubai,50.00,0.00,0.00,0.00,\n"
        )
        res = import_finance_sheets_data(
            invoices_file=io.StringIO(inv_csv),
            jobs_file=io.StringIO(jobs_csv),
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertTrue(len(res['warnings']) >= 2)
        # Check VAT discrepancy warning
        self.assertTrue(any('VAT discrepancy' in w for w in res['warnings']))
        # Check duplicate invoice warning
        self.assertTrue(any('duplicate invoice' in w.lower() for w in res['warnings']))
        # Check missing invoice warning
        self.assertTrue(any('Invoice not found' in w or 'INV-DOES-NOT-EXIST' in w for w in res['warnings']))

    def test_fin_invoices_view_query_count(self):
        # Create 10 invoices
        for i in range(10):
            Invoice.objects.create(
                invoice_number=f'INV-QC-A-{i}',
                invoice_date=self.today,
                amount_excl_vat=Decimal('1000.00'),
            )
        url = reverse('fin_invoices')
        with CaptureQueriesContext(connection) as ctx_10:
            resp_10 = self.client.get(url)
        self.assertEqual(resp_10.status_code, 200)
        q_count_10 = len(ctx_10.captured_queries)

        # Create 10 more invoices (20 total)
        for i in range(10, 20):
            Invoice.objects.create(
                invoice_number=f'INV-QC-A-{i}',
                invoice_date=self.today,
                amount_excl_vat=Decimal('1000.00'),
            )
        with CaptureQueriesContext(connection) as ctx_20:
            resp_20 = self.client.get(url)
        self.assertEqual(resp_20.status_code, 200)
        q_count_20 = len(ctx_20.captured_queries)

        # Constant query count (no N+1)
        self.assertEqual(q_count_10, q_count_20)


class PhaseCCompanyPnLAndReconciliationTests(TestCase):
    def setUp(self):
        self.accountant_user = User.objects.create_user(
            username='fin_c_user',
            password='password123'
        )
        self.employee = Employee.objects.create(
            user=self.accountant_user,
            is_accountant=True,
            salary=Decimal('8000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )
        self.eng_user = User.objects.create_user(username='eng_c_user', password='password123')
        self.engineer = Employee.objects.create(
            user=self.eng_user,
            is_employee=True,
            salary=Decimal('5000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )
        self.cat_overhead, _ = ExpenseCategory.objects.get_or_create(name='Office Rent', defaults={'kind': 'OVERHEAD', 'sort_order': 1})
        self.cat_project, _ = ExpenseCategory.objects.get_or_create(name='Project Freight', defaults={'kind': 'PROJECT', 'sort_order': 2})
        self.client = Client()
        self.client.force_login(self.accountant_user)

    def test_company_pnl_from_invoices_and_reconciliation(self):
        """
        Tests Phase C Company P&L:
        - Revenue from Invoice.collection_value (amount_excl_vat - credits_applied).
        - Direct costs from JobCost (travel/flight/misc/other) + SpareSale (purchase_cost) + Project Expense.
        - Payroll cost from Payroll model.
        - Engineer cost is info-only (allocated vs unallocated payroll).
        - Detailed reconciliation math matches exactly.
        """
        proj = Project.objects.create(
            name='MV Titan Overhaul',
            service_reference='SR-TITAN-01',
            client_name='Titan Shipping',
            vessel_name='MV TITAN',
        )
        # Invoice 1: 10,000 AED excl. VAT, 500 AED credit -> 9,500 collection value
        inv1 = Invoice.objects.create(
            invoice_number='INV-TITAN-01',
            project=proj,
            service_ref='SR-TITAN-01',
            vessel='MV TITAN',
            customer='Titan Shipping',
            invoice_date=datetime.date(2026, 1, 15),
            amount_excl_vat=Decimal('10000.00'),
            credits_applied=Decimal('500.00'),
        )
        # Invoice 2: 5,000 AED excl. VAT, 0 credit -> 5,000 collection value
        inv2 = Invoice.objects.create(
            invoice_number='INV-TITAN-02',
            project=proj,
            service_ref='SR-TITAN-01',
            vessel='MV TITAN',
            customer='Titan Shipping',
            invoice_date=datetime.date(2026, 1, 20),
            amount_excl_vat=Decimal('5000.00'),
            credits_applied=Decimal('0.00'),
        )
        # JobCost on inv1: Travel 500.00, Flight 300.00
        JobCost.objects.create(
            invoice=inv1,
            cost_type='TRAVEL',
            amount=Decimal('500.00'),
            travel_port='Dubai',
            date=datetime.date(2026, 1, 15),
            source='IMPORT',
        )
        JobCost.objects.create(
            invoice=inv1,
            cost_type='FLIGHT',
            amount=Decimal('300.00'),
            date=datetime.date(2026, 1, 15),
            source='IMPORT',
        )
        # SpareSale on inv2: purchase_cost 1200.00, selling_price 1800.00
        SpareSale.objects.create(
            invoice=inv2,
            project=proj,
            service_ref='SR-TITAN-01',
            vessel_customer='MV TITAN / Titan Shipping',
            purchase_cost=Decimal('1200.00'),
            selling_price=Decimal('1800.00'),
            status='INVOICED',
        )
        # Direct Project Expense: 400.00
        Expense.objects.create(
            date=datetime.date(2026, 1, 18),
            category=self.cat_project,
            project=proj,
            amount=Decimal('400.00'),
            description='Crane rental',
        )
        # Overhead Expense: 1500.00
        Expense.objects.create(
            date=datetime.date(2026, 1, 25),
            category=self.cat_overhead,
            amount=Decimal('1500.00'),
            description='January Office Rent',
        )
        # Payroll: 8000.00
        Payroll.objects.create(
            employee=self.employee,
            month=datetime.date(2026, 1, 1),
            basic_salary=Decimal('8000.00'),
            status='APPROVED',
        )
        # Service Log: 2000.00 engineer cost
        log = EmployeeServiceLog.objects.create(
            employee=self.engineer,
            project=proj,
            date=datetime.date(2026, 1, 10),
            start_time=datetime.time(8, 0),
            end_time=datetime.time(17, 0),
            service_type='JOB',
        )
        EmployeeServiceLog.objects.filter(id=log.id).update(normal_cost=Decimal('2000.00'), ot_cost=Decimal('0.00'))

        res = pnl_for_range(datetime.date(2026, 1, 1), datetime.date(2026, 1, 31), granularity='month')
        buckets = res['buckets']
        self.assertEqual(len(buckets), 1)
        b = buckets[0]

        # Revenue = 9500 + 5000 = 14500.00
        self.assertEqual(b['revenue'], Decimal('14500.00'))

        # Project Direct = 500 (Travel) + 300 (Flight) + 1200 (Spares) + 400 (Project Expense) = 2400.00
        self.assertEqual(b['project_direct_cost'], Decimal('2400.00'))
        self.assertEqual(b['spares_cost'], Decimal('1200.00'))
        self.assertEqual(b['job_costs'], Decimal('800.00'))
        self.assertEqual(b['project_expenses'], Decimal('400.00'))

        # Payroll = 8000.00
        self.assertEqual(b['payroll_cost'], Decimal('8000.00'))

        # Overhead = 1500.00
        self.assertEqual(b['overhead_cost'], Decimal('1500.00'))

        # Total Cost = 2400 + 8000 + 1500 = 11900.00
        self.assertEqual(b['total_cost'], Decimal('11900.00'))

        # Profit = 14500 - 11900 = 2600.00
        self.assertEqual(b['profit'], Decimal('2600.00'))
        # Margin = 2600 / 14500 * 100 = 17.9%
        self.assertEqual(b['margin_pct'], Decimal('17.9'))

        # Engineer Cost is info-only: allocated 2000.00, unallocated 6000.00
        self.assertEqual(b['allocated_engineer_cost'], Decimal('2000.00'))
        self.assertEqual(b['unallocated_payroll'], Decimal('6000.00'))

        # Detailed Reconciliation check
        recon = b['details']['reconciliation']
        self.assertEqual(recon['job_revenue'], Decimal('14500.00'))
        self.assertEqual(recon['job_direct_cost'], Decimal('2000.00'))  # 1200 spares + 800 job costs
        self.assertEqual(recon['job_engineer_cost'], Decimal('2000.00'))
        # Job Gross Profit = 14500 - 2000 - 2000 = 10500.00
        self.assertEqual(recon['job_gross_profit'], Decimal('10500.00'))
        self.assertEqual(recon['unallocated_payroll'], Decimal('6000.00'))
        self.assertEqual(recon['project_expenses'], Decimal('400.00'))
        self.assertEqual(recon['overhead_cost'], Decimal('1500.00'))
        # 10500 - 6000 - 400 - 1500 = 2600.00 == company_net_profit
        self.assertEqual(
            recon['job_gross_profit'] - recon['unallocated_payroll'] - recon['project_expenses'] - recon['overhead_cost'],
            recon['company_net_profit']
        )
        self.assertEqual(recon['company_net_profit'], Decimal('2600.00'))

    def test_dashboard_switches_to_invoice_data(self):
        """
        Tests that get_dashboard_data uses Invoice collection_value and profit
        when Invoice records exist for the period.
        """
        proj = Project.objects.create(name='Dash Proj', client_name='Client Delta')
        Invoice.objects.create(
            invoice_number='INV-DASH-01',
            project=proj,
            customer='Client Delta',
            vessel='Vessel Delta',
            invoice_date=timezone.localdate(),
            amount_excl_vat=Decimal('20000.00'),
            credits_applied=Decimal('1000.00'),  # collection_value = 19,000.00
        )
        data = get_dashboard_data(period='this_month')
        self.assertEqual(data['kpis']['revenue']['value'], Decimal('19000.00'))
        self.assertTrue(len(data['top_5_profitable_projects']) >= 1)
        self.assertEqual(data['top_5_profitable_projects'][0]['name'], 'Dash Proj')

    def test_pnl_excel_response_export(self):
        """
        Tests that pnl_excel_response exports openpyxl workbook with all required columns.
        """
        proj = Project.objects.create(name='Export Proj')
        Invoice.objects.create(
            invoice_number='INV-EXP-01',
            project=proj,
            invoice_date=datetime.date(2026, 2, 10),
            amount_excl_vat=Decimal('12000.00'),
            credits_applied=Decimal('0.00'),
        )
        res = pnl_for_range(datetime.date(2026, 2, 1), datetime.date(2026, 2, 28), granularity='month')
        response = pnl_excel_response(res['buckets'], res['totals'], filename_suffix='2026-02')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

        # Load back workbook from response content
        wb = openpyxl.load_workbook(io.BytesIO(response.content))
        ws = wb.active
        self.assertEqual(ws.title, "P&L Statement")
        header_row = [cell.value for cell in ws[1]]
        self.assertIn("Revenue (AED)", header_row)
        self.assertIn("Spares Cost (AED)", header_row)
        self.assertIn("Job Direct Costs (AED)", header_row)
        self.assertIn("Gross Profit (AED)", header_row)
        self.assertIn("Gross Margin %", header_row)
        self.assertIn("Net Profit/Loss (AED)", header_row)

    def test_pnl_view_renders_successfully(self):
        """
        Tests that fin_pnl view renders with status 200 and includes reconciliation panel.
        """
        Invoice.objects.create(
            invoice_number='INV-VIEW-01',
            customer='View Marine',
            invoice_date=datetime.date(2026, 1, 15),
            amount_excl_vat=Decimal('10000.00'),
        )
        resp = self.client.get(reverse('fin_pnl') + '?year=2026')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Job P&amp;L to Company P&amp;L Reconciliation')

    def test_pnl_view_all_periods_return_200(self):
        """
        Tests that an accountant GET on fin_pnl returns 200 for every period option.
        """
        periods = [
            'this_month', 'last_month', 'this_quarter', 'this_year',
            'ytd', 'last_year', 'all_time', 'custom', 'week', 'month', 'year'
        ]
        for p in periods:
            url = f"{reverse('fin_pnl')}?period={p}"
            if p == 'custom':
                url += "&start_date=2026-01-01&end_date=2026-03-31"
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, f"fin_pnl failed with status {resp.status_code} for period '{p}'")

    def test_reconciliation_negative_unallocated_payroll_when_engineer_cost_exceeds_payroll(self):
        """
        Tests requirement 3:
        A month where allocated engineer cost > payroll -> unallocated_payroll is negative (unclamped).
        job profit - unallocated payroll - project expenses - overhead == net profit exactly,
        compared against an independently hand-calculated net profit (not values from the same dict).
        """
        proj = Project.objects.create(
            name='MV Titan Extra Eng',
            service_reference='SR-TITAN-NEG',
            client_name='Titan NEG Corp',
        )
        inv = Invoice.objects.create(
            invoice_number='INV-TITAN-NEG',
            project=proj,
            invoice_date=datetime.date(2026, 3, 15),
            amount_excl_vat=Decimal('22000.00'),
            credits_applied=Decimal('2000.00'),  # Collection value = 20,000.00
        )
        JobCost.objects.create(
            invoice=inv,
            cost_type='TRAVEL',
            amount=Decimal('1000.00'),
            date=datetime.date(2026, 3, 15),
            source='IMPORT',
        )
        SpareSale.objects.create(
            invoice=inv,
            project=proj,
            service_ref='SR-TITAN-NEG',
            purchase_cost=Decimal('2500.00'),
            selling_price=Decimal('3500.00'),
            status='INVOICED',
        )
        Expense.objects.create(
            date=datetime.date(2026, 3, 18),
            category=self.cat_project,
            project=proj,
            amount=Decimal('500.00'),
            description='Project testing tool',
        )
        Expense.objects.create(
            date=datetime.date(2026, 3, 20),
            category=self.cat_overhead,
            amount=Decimal('1500.00'),
            description='Overhead office charge',
        )
        # Payroll is 4,000.00
        Payroll.objects.create(
            employee=self.employee,
            month=datetime.date(2026, 3, 1),
            basic_salary=Decimal('4000.00'),
            status='APPROVED',
        )
        # Service Log cost is 6,000.00 (> Payroll of 4,000.00!)
        log = EmployeeServiceLog.objects.create(
            employee=self.engineer,
            project=proj,
            date=datetime.date(2026, 3, 10),
            start_time=datetime.time(8, 0),
            end_time=datetime.time(17, 0),
            service_type='JOB',
        )
        EmployeeServiceLog.objects.filter(id=log.id).update(normal_cost=Decimal('6000.00'), ot_cost=Decimal('0.00'))

        # Independent hand calculations:
        hand_calc_rev = Decimal('20000.00')
        hand_calc_job_direct = Decimal('1000.00') + Decimal('2500.00')  # 3500.00
        hand_calc_eng_cost = Decimal('6000.00')
        hand_calc_job_profit = hand_calc_rev - hand_calc_job_direct - hand_calc_eng_cost  # 10500.00
        hand_calc_payroll = Decimal('4000.00')
        hand_calc_unalloc_payroll = hand_calc_payroll - hand_calc_eng_cost  # -2000.00 (NEGATIVE)
        hand_calc_proj_exp = Decimal('500.00')
        hand_calc_overhead = Decimal('1500.00')
        # Net Profit = Revenue - (Payroll + Direct Job Costs + Project Expenses + Overhead)
        hand_calc_total_cost = hand_calc_payroll + hand_calc_job_direct + hand_calc_proj_exp + hand_calc_overhead  # 4000 + 3500 + 500 + 1500 = 9500
        hand_calc_net_profit = hand_calc_rev - hand_calc_total_cost  # 20000 - 9500 = 10500.00

        # Verify mathematical identity of reconciliation:
        # Job Profit - Unallocated Payroll - Project Expenses - Overhead == Net Profit
        # 10500 - (-2000) - 500 - 1500 = 10500 + 2000 - 2000 = 10500
        reconciled_profit = hand_calc_job_profit - hand_calc_unalloc_payroll - hand_calc_proj_exp - hand_calc_overhead
        self.assertEqual(reconciled_profit, hand_calc_net_profit)

        # Now query system pnl_for_range
        res = pnl_for_range(datetime.date(2026, 3, 1), datetime.date(2026, 3, 31), granularity='month')
        self.assertEqual(len(res['buckets']), 1)
        b = res['buckets'][0]
        recon = b['details']['reconciliation']

        # Assert negative unallocated payroll is preserved and NOT clamped to 0
        self.assertEqual(b['unallocated_payroll'], Decimal('-2000.00'))
        self.assertEqual(recon['unallocated_payroll'], Decimal('-2000.00'))
        self.assertEqual(b['revenue'], hand_calc_rev)
        self.assertEqual(b['profit'], hand_calc_net_profit)
        self.assertEqual(recon['job_gross_profit'], hand_calc_job_profit)

        # Assert reconciliation equality matches independent hand-calculation exactly
        system_recon_result = (
            recon['job_gross_profit']
            - recon['unallocated_payroll']
            - recon['project_expenses']
            - recon['overhead_cost']
        )
        self.assertEqual(system_recon_result, hand_calc_net_profit)
        self.assertEqual(system_recon_result, b['profit'])

    def test_dashboard_alert_overdue_invoices(self):
        """
        Tests data-quality alert for overdue invoices (count + amount).
        """
        today = timezone.localdate()
        Invoice.objects.create(
            invoice_number='INV-OVERDUE-01',
            invoice_date=today - datetime.timedelta(days=40),
            due_date=today - datetime.timedelta(days=10),
            amount_excl_vat=Decimal('5000.00'),
            credits_applied=Decimal('0.00'),
            amount_received=Decimal('1000.00'),  # Outstanding = 4000.00
        )
        data = get_dashboard_data(period='this_month')
        overdue_alerts = [a for a in data['alerts'] if a['id'] == 'overdue_invoices']
        self.assertTrue(len(overdue_alerts) >= 1)
        alert = overdue_alerts[0]
        self.assertIn("1 Overdue invoice", alert['title'])
        self.assertIn("4,000.00", alert['title'])

    def test_dashboard_alert_spares_with_alerts(self):
        """
        Tests data-quality alert for spare parts with alerts (e.g. missing purchase cost).
        """
        SpareSale.objects.create(
            service_ref='SR-SP-ALERT-01',
            vessel_customer='Alert Marine',
            purchase_cost=Decimal('0.00'),  # triggers "Missing purchase cost"
            selling_price=Decimal('500.00'),
        )
        data = get_dashboard_data(period='this_month')
        spares_alerts = [a for a in data['alerts'] if a['id'] == 'spares_with_alerts']
        self.assertTrue(len(spares_alerts) >= 1)
        self.assertIn("Spare parts record(s) with pricing or billing alerts", spares_alerts[0]['title'])

    def test_dashboard_alert_service_job_missing_spares(self):
        """
        Tests data-quality alert for SERVICE_JOB invoices whose project's WorkOrder has SpareConsumed rows
        but the invoice has no SpareSale.
        """
        p = Project.objects.create(name='Spares Job Project', client_name='Spares Client')
        wo = WorkOrder.objects.create(project=p, work_order_number='WO-SP-001')
        SpareConsumed.objects.create(work_order=wo, name='Gasket 40mm', unit='pcs', quantity=2)
        inv = Invoice.objects.create(
            invoice_number='INV-SVC-SP-01',
            invoice_type='SERVICE_JOB',
            project=p,
            invoice_date=timezone.localdate(),
            amount_excl_vat=Decimal('4500.00'),
        )
        data = get_dashboard_data(period='this_month')
        svc_alerts = [a for a in data['alerts'] if a['id'] == 'service_job_missing_spares']
        self.assertEqual(len(svc_alerts), 1)
        self.assertEqual(svc_alerts[0]['link'], reverse('fin_spares'))
        self.assertIn("consumed spares but no spares record", svc_alerts[0]['title'])

        # Adding a SpareSale record for this invoice clears the alert
        SpareSale.objects.create(
            invoice=inv,
            project=p,
            service_ref='WO-SP-001',
            vessel_customer='Spares Client',
            purchase_cost=Decimal('200.00'),
            selling_price=Decimal('500.00'),
        )
        data2 = get_dashboard_data(period='this_month')
        svc_alerts2 = [a for a in data2['alerts'] if a['id'] == 'service_job_missing_spares']
        self.assertEqual(len(svc_alerts2), 0)

    def test_dashboard_alert_completed_project_not_invoiced(self):
        """
        Tests data-quality alert for Project.status == 'COMPLETED' with no non-cancelled Invoice linked.
        """
        p = Project.objects.create(name='Uninvoiced Complete Project', status='COMPLETED')
        data = get_dashboard_data(period='this_month')
        comp_alerts = [a for a in data['alerts'] if a['id'] == 'completed_no_invoice']
        self.assertEqual(len(comp_alerts), 1)
        self.assertEqual(comp_alerts[0]['link'], reverse('fin_invoices'))

        # A cancelled invoice does not clear the alert
        Invoice.objects.create(
            invoice_number='INV-CAN-01',
            project=p,
            invoice_date=timezone.localdate(),
            amount_excl_vat=Decimal('1000.00'),
            is_cancelled=True,
        )
        data2 = get_dashboard_data(period='this_month')
        comp_alerts2 = [a for a in data2['alerts'] if a['id'] == 'completed_no_invoice']
        self.assertEqual(len(comp_alerts2), 1)

        # An active non-cancelled invoice clears the alert
        Invoice.objects.create(
            invoice_number='INV-ACT-01',
            project=p,
            invoice_date=timezone.localdate(),
            amount_excl_vat=Decimal('1000.00'),
            is_cancelled=False,
        )
        data3 = get_dashboard_data(period='this_month')
        comp_alerts3 = [a for a in data3['alerts'] if a['id'] == 'completed_no_invoice']
        self.assertEqual(len(comp_alerts3), 0)

    def test_dashboard_alert_invoice_without_engineer_logs(self):
        """
        Tests data-quality alert for non-cancelled SERVICE_JOB invoices whose project has no EmployeeServiceLog.
        """
        p = Project.objects.create(name='No Logs Project', client_name='Client ABC')
        inv = Invoice.objects.create(
            invoice_number='INV-NO-LOGS-01',
            invoice_type='SERVICE_JOB',
            project=p,
            invoice_date=timezone.localdate(),
            amount_excl_vat=Decimal('5000.00'),
        )
        data = get_dashboard_data(period='this_month')
        no_logs_alerts = [a for a in data['alerts'] if a['id'] == 'invoiced_no_logs']
        self.assertEqual(len(no_logs_alerts), 1)
        self.assertEqual(no_logs_alerts[0]['link'], reverse('fin_jobs_pnl'))

        # Adding an EmployeeServiceLog for this project clears the alert
        u = User.objects.create_user('eng_log_user', 'eng_log@example.com', 'pass1234')
        emp = Employee.objects.create(user=u, is_employee=True, salary=Decimal('3000.00'))
        EmployeeServiceLog.objects.create(
            employee=emp,
            project=p,
            date=timezone.localdate(),
            start_time=datetime.time(8, 0),
            end_time=datetime.time(17, 0),
            service_type='JOB',
        )
        data2 = get_dashboard_data(period='this_month')
        no_logs_alerts2 = [a for a in data2['alerts'] if a['id'] == 'invoiced_no_logs']
        self.assertEqual(len(no_logs_alerts2), 0)

    def test_pnl_excel_response_headers_match_c2_columns(self):
        """
        Tests that pnl_excel_response headers match the 11 C2 columns exactly.
        """
        res = pnl_for_range(datetime.date(2026, 1, 1), datetime.date(2026, 1, 31), granularity='month')
        response = pnl_excel_response(res['buckets'], res['totals'], filename_suffix='c2_test')
        wb = openpyxl.load_workbook(io.BytesIO(response.content))
        ws = wb.active
        headers = [cell.value for cell in ws[1]]
        expected_headers = [
            "Month", "Revenue (AED)", "Spares Cost (AED)", "Job Direct Costs (AED)",
            "Gross Profit (AED)", "Gross Margin %", "Payroll (AED)", "Overheads (AED)",
            "Net Profit/Loss (AED)", "Net Margin %", "Engineer Days"
        ]
        self.assertEqual(headers, expected_headers)

    def test_pnl_excel_response_empty_cells_when_margin_is_none(self):
        """
        Tests that pnl_excel_response writes empty cells (None/empty) instead of 0.0 or 0.0%
        when gross/net margin is None, both in buckets and totals.
        """
        buckets = [{
            'label': 'January 2026',
            'revenue': Decimal('0.00'),
            'spares_cost': Decimal('0.00'),
            'job_direct_cost': Decimal('0.00'),
            'gross_profit': Decimal('0.00'),
            'gross_margin_pct': None,
            'payroll_cost': Decimal('0.00'),
            'overhead_cost': Decimal('0.00'),
            'profit': Decimal('0.00'),
            'margin_pct': None,
            'engineer_days': 0,
        }]
        totals = {
            'revenue': Decimal('0.00'),
            'spares_cost': Decimal('0.00'),
            'job_direct_cost': Decimal('0.00'),
            'gross_profit': Decimal('0.00'),
            'gross_margin_pct': None,
            'payroll_cost': Decimal('0.00'),
            'overhead_cost': Decimal('0.00'),
            'profit': Decimal('0.00'),
            'margin_pct': None,
            'engineer_days': 0,
        }
        response = pnl_excel_response(buckets, totals, filename_suffix='margin_none_test')
        wb = openpyxl.load_workbook(io.BytesIO(response.content))
        ws = wb.active
        # Row 2 is the data row: col 6 is Gross Margin %, col 10 is Net Margin %
        self.assertIn(ws.cell(row=2, column=6).value, (None, ''))
        self.assertIn(ws.cell(row=2, column=10).value, (None, ''))
        # Row 3 is the totals row: col 6 is Gross Margin %, col 10 is Net Margin %
        self.assertIn(ws.cell(row=3, column=6).value, (None, ''))
        self.assertIn(ws.cell(row=3, column=10).value, (None, ''))

    def test_dashboard_alerts_fixed_query_count_1_vs_20_invoices(self):
        """
        Tests that get_dashboard_data executes a fixed number of queries regardless of
        whether there is 1 invoice or 20 invoices (no N+1 queries in alerts or dashboard processing).
        """
        from django.test.utils import CaptureQueriesContext
        today = timezone.localdate()
        p1 = Project.objects.create(name='P1', client_name='Client 1')
        Invoice.objects.create(
            invoice_number='INV-QC-001',
            invoice_date=today,
            due_date=today - datetime.timedelta(days=5),
            project=p1,
            invoice_type='SERVICE_JOB',
            amount_excl_vat=Decimal('1000.00'),
        )

        with CaptureQueriesContext(connection) as ctx1:
            get_dashboard_data(period='this_month')
        count_1 = len(ctx1)

        # Now create 19 more invoices across projects
        for i in range(2, 21):
            p = Project.objects.create(name=f'P{i}', client_name=f'Client {i}')
            Invoice.objects.create(
                invoice_number=f'INV-QC-{i:03d}',
                invoice_date=today,
                due_date=today - datetime.timedelta(days=i),
                project=p,
                invoice_type='SERVICE_JOB',
                amount_excl_vat=Decimal('1000.00') * i,
            )

        with CaptureQueriesContext(connection) as ctx20:
            get_dashboard_data(period='this_month')
        count_20 = len(ctx20)

        self.assertEqual(count_1, count_20, f"Query count should be fixed: got {count_1} with 1 invoice vs {count_20} with 20 invoices")


class InvoiceFormatAndLegacyTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.accountant_user = User.objects.create_user('acc_inv', 'acc_inv@example.com', 'pass1234')
        self.accountant_emp = Employee.objects.create(
            user=self.accountant_user,
            is_accountant=True,
            salary=Decimal('5000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )
        self.client.force_login(self.accountant_user)
        self.today = timezone.localdate()

    def test_invoice_format_validation(self):
        # 1. Valid numbers
        form = InvoiceForm(data={'invoice_number': 'AMMS-INV-26-0080', 'invoice_date': self.today, 'amount_excl_vat': '1000.00', 'invoice_type': 'SERVICE_JOB'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['invoice_number'], 'AMMS-INV-26-0080')

        # 2. Revision valid (-R1, -R2)
        form_rev = InvoiceForm(data={'invoice_number': 'AMMS-INV-26-0083-R1', 'invoice_date': self.today, 'amount_excl_vat': '1000.00', 'invoice_type': 'SERVICE_JOB'})
        self.assertTrue(form_rev.is_valid(), form_rev.errors)
        self.assertEqual(form_rev.cleaned_data['invoice_number'], 'AMMS-INV-26-0083-R1')

        # 3. Lowercase normalized and spaces trimmed
        form_lower = InvoiceForm(data={'invoice_number': '  amms-inv-26-0083-r2  ', 'invoice_date': self.today, 'amount_excl_vat': '1000.00', 'invoice_type': 'SERVICE_JOB'})
        self.assertTrue(form_lower.is_valid(), form_lower.errors)
        self.assertEqual(form_lower.cleaned_data['invoice_number'], 'AMMS-INV-26-0083-R2')

        # 4. Invalid rejected with specific error message
        invalid_numbers = [
            'INV-26-0080',
            'AMMS-INV-26-80',
            'AMMS-INV-2026-0080',
            'AMMS-INV-26-0080-R',
            'LEGACY-1',
            'AMMS-INV-XX-0001',
        ]
        expected_err = "Invoice No must look like AMMS-INV-26-0080 or AMMS-INV-26-0083-R1"
        for inv_no in invalid_numbers:
            form_inv = InvoiceForm(data={'invoice_number': inv_no, 'invoice_date': self.today, 'amount_excl_vat': '1000.00', 'invoice_type': 'SERVICE_JOB'})
            self.assertFalse(form_inv.is_valid(), f"Expected {inv_no} to be invalid")
            self.assertIn('invoice_number', form_inv.errors)
            self.assertEqual(form_inv.errors['invoice_number'], [expected_err])

    def test_invoice_edit_unchanged_number_and_legacy_exception(self):
        # 1. Edit an invoice numbered "AMMS-INV-26-114-R1" without changing the number -> saves
        inv_nonstandard = Invoice.objects.create(
            invoice_number='AMMS-INV-26-114-R1',
            invoice_date=self.today,
            amount_excl_vat=Decimal('1500.00'),
            invoice_type='SERVICE_JOB',
        )
        form_unchanged = InvoiceForm(
            instance=inv_nonstandard,
            data={
                'invoice_number': 'AMMS-INV-26-114-R1',
                'invoice_date': self.today,
                'amount_excl_vat': '1500.00',
                'invoice_type': 'SERVICE_JOB',
            }
        )
        self.assertTrue(form_unchanged.is_valid(), form_unchanged.errors)
        saved_inv = form_unchanged.save()
        self.assertEqual(saved_inv.invoice_number, 'AMMS-INV-26-114-R1')

        # 2. Change it to "BAD-1" -> error
        form_bad = InvoiceForm(
            instance=inv_nonstandard,
            data={
                'invoice_number': 'BAD-1',
                'invoice_date': self.today,
                'amount_excl_vat': '1500.00',
                'invoice_type': 'SERVICE_JOB',
            }
        )
        self.assertFalse(form_bad.is_valid())
        self.assertIn('invoice_number', form_bad.errors)
        self.assertEqual(form_bad.errors['invoice_number'], ["Invoice No must look like AMMS-INV-26-0080 or AMMS-INV-26-0083-R1"])

        # 3. Edit LEGACY-5 without changing number -> error
        inv_legacy = Invoice.objects.create(
            invoice_number='LEGACY-5',
            invoice_date=self.today,
            amount_excl_vat=Decimal('2000.00'),
            invoice_type='SERVICE_JOB',
        )
        form_legacy_unchanged = InvoiceForm(
            instance=inv_legacy,
            data={
                'invoice_number': 'LEGACY-5',
                'invoice_date': self.today,
                'amount_excl_vat': '2000.00',
                'invoice_type': 'SERVICE_JOB',
            }
        )
        self.assertFalse(form_legacy_unchanged.is_valid())
        self.assertIn('invoice_number', form_legacy_unchanged.errors)
        self.assertEqual(form_legacy_unchanged.errors['invoice_number'], ["Invoice No must look like AMMS-INV-26-0080 or AMMS-INV-26-0083-R1"])

        # Changing LEGACY-5 to a valid AMMS number -> saves
        form_legacy_fixed = InvoiceForm(
            instance=inv_legacy,
            data={
                'invoice_number': 'AMMS-INV-26-0105',
                'invoice_date': self.today,
                'amount_excl_vat': '2000.00',
                'invoice_type': 'SERVICE_JOB',
            }
        )
        self.assertTrue(form_legacy_fixed.is_valid(), form_legacy_fixed.errors)
        saved_legacy = form_legacy_fixed.save()
        self.assertEqual(saved_legacy.invoice_number, 'AMMS-INV-26-0105')

    def test_next_invoice_number_generation(self):
        # Empty year 2026 -> AMMS-INV-26-0001
        self.assertEqual(get_next_invoice_number(target_year=2026), 'AMMS-INV-26-0001')

        # Existing 0080 and 0083-R1, and a LEGACY number (ignored) and a different year (ignored)
        Invoice.objects.create(
            invoice_number='AMMS-INV-26-0080',
            invoice_date=datetime.date(2026, 1, 1),
            amount_excl_vat=Decimal('100.00')
        )
        Invoice.objects.create(
            invoice_number='AMMS-INV-26-0083-R1',
            invoice_date=datetime.date(2026, 1, 2),
            amount_excl_vat=Decimal('200.00')
        )
        Invoice.objects.create(
            invoice_number='LEGACY-999',
            invoice_date=datetime.date(2026, 1, 3),
            amount_excl_vat=Decimal('300.00')
        )
        Invoice.objects.create(
            invoice_number='AMMS-INV-25-0999',
            invoice_date=datetime.date(2025, 12, 31),
            amount_excl_vat=Decimal('400.00')
        )

        # Next for 2026 is 0084
        self.assertEqual(get_next_invoice_number(target_year=2026), 'AMMS-INV-26-0084')

        # New year restarts at 0001
        self.assertEqual(get_next_invoice_number(target_year=2027), 'AMMS-INV-27-0001')

        # Add-invoice page pre-fills next number automatically
        with patch('django.utils.timezone.localdate', return_value=datetime.date(2026, 5, 1)):
            resp = self.client.get(reverse('fin_invoice_add'))
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.context['form'].initial.get('invoice_number'), 'AMMS-INV-26-0084')

    def test_import_merge_legacy_invoice(self):
        p = Project.objects.create(name='Project Merge Test', service_reference='SR-MERGE-01')
        legacy_inv = Invoice.objects.create(
            invoice_number='LEGACY-P1',
            project=p,
            service_ref='SR-MERGE-01',
            amount_excl_vat=Decimal('5000.00'),
            invoice_date=datetime.date(2026, 1, 1),
            due_date=datetime.date(2026, 1, 31),
            amount_received=Decimal('0.00'),
            is_cancelled=False,
        )

        invoices_csv = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Amount Without Tax,Credits Applied,Notes\n"
            "Paid,2026-06-15,SR-MERGE-01,Ocean Wave,AMMS-INV-26-0099,5000.00,250.00,Updated via sheet\n"
            "Open,2026-06-16,SR-NONMATCH,Sea Breeze,OLD-INV-123,3000.00,0.00,Old format row\n"
        )
        inv_file = io.StringIO(invoices_csv)

        res = import_finance_sheets_data(
            invoices_file=inv_file,
            dry_run=False,
            user=self.accountant_user,
        )

        # Verify warnings:
        # 1. Merged LEGACY-P1 into AMMS-INV-26-0099
        # 2. OLD-INV-123 format warning
        self.assertIn("Merged LEGACY-P1 into AMMS-INV-26-0099", res['warnings'])
        self.assertIn("Invoice No 'OLD-INV-123' does not match AMMS-INV-YY-NNNN format", res['warnings'])

        # Never create a duplicate for the same project+amount
        self.assertEqual(Invoice.objects.filter(project=p).count(), 1)
        self.assertFalse(Invoice.objects.filter(invoice_number='LEGACY-P1').exists())

        merged_inv = Invoice.objects.get(invoice_number='AMMS-INV-26-0099')
        self.assertEqual(merged_inv.id, legacy_inv.id)
        self.assertEqual(merged_inv.project, p)
        self.assertEqual(merged_inv.invoice_date, datetime.date(2026, 6, 15))
        self.assertEqual(merged_inv.credits_applied, Decimal('250.00'))
        self.assertEqual(merged_inv.status, 'PAID')
        self.assertEqual(merged_inv.amount_received, Decimal('4750.00'))
        self.assertEqual(merged_inv.vessel, 'Ocean Wave')

    def test_legacy_alert_and_pill(self):
        # 1. No legacy invoices -> no alert
        data_no_legacy = get_dashboard_data(period='this_month')
        legacy_alert = next((a for a in data_no_legacy['alerts'] if a['id'] == 'legacy_invoices'), None)
        self.assertIsNone(legacy_alert)

        # 2. Add legacy invoice -> alert appears
        legacy_inv = Invoice.objects.create(
            invoice_number='LEGACY-99',
            invoice_date=self.today,
            amount_excl_vat=Decimal('2500.00'),
        )
        data_with_legacy = get_dashboard_data(period='this_month')
        legacy_alert = next((a for a in data_with_legacy['alerts'] if a['id'] == 'legacy_invoices'), None)
        self.assertIsNotNone(legacy_alert)
        self.assertIn("1 legacy invoice(s) need a real invoice number", legacy_alert['title'])
        self.assertEqual(legacy_alert['link'], f"{reverse('fin_invoices')}?search=LEGACY")

        # 3. Check legacy pill on invoices list page
        resp = self.client.get(reverse('fin_invoices'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'LEGACY-99')
        self.assertContains(resp, 'status hold ms-1')
        self.assertContains(resp, 'Legacy')


class LegacyDuplicateAndProjectSummaryFinancialsTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.admin_user = User.objects.create_superuser(
            username='admin_test_pnl',
            email='admin_pnl@example.com',
            password='testpassword'
        )
        self.accountant_user = User.objects.create_user(
            username='acct_test_pnl',
            email='acct_pnl@example.com',
            password='testpassword',
            is_staff=True
        )
        Employee.objects.create(user=self.accountant_user, is_accountant=True)
        self.client.login(username='acct_test_pnl', password='testpassword')

    def test_legacy_purchases_duplicate_warning_and_dashboard_alert(self):
        # 1. Invoice with JobCost(source='LEGACY', description='Legacy purchases')
        inv = Invoice.objects.create(
            invoice_number='AMMS-INV-26-0500',
            invoice_date=self.today,
            amount_excl_vat=Decimal('5000.00'),
            invoice_type='SERVICE_JOB',
        )
        legacy_cost = JobCost.objects.create(
            invoice=inv,
            cost_type='OTHER',
            source='LEGACY',
            description='Legacy purchases',
            amount=Decimal('1500.00'),
        )

        # 2. When a SpareSale is saved on an invoice that has this JobCost
        spare = SpareSale.objects.create(
            invoice=inv,
            purchase_cost=Decimal('500.00'),
            selling_price=Decimal('800.00'),
            source='SERVICE_JOB',
        )

        # 3. Check warning on spares form/list via spare.alerts property
        expected_msg = "This invoice also has a Legacy purchases cost of AED 1,500.00 — delete it if it is the same spares cost, otherwise it is counted twice."
        self.assertIn(expected_msg, spare.alerts)

        # 4. Check web form view for edit shows the warning
        resp = self.client.get(reverse('fin_spare_edit', args=[spare.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, expected_msg)

        # 5. Check dashboard data-quality alert
        data = get_dashboard_data(period='this_month')
        dup_alert = next((a for a in data['alerts'] if a['id'] == 'legacy_purchases_and_spares'), None)
        self.assertIsNotNone(dup_alert)
        self.assertIn("1 invoice(s) have both Legacy purchases and Spares records", dup_alert['title'])
        self.assertEqual(dup_alert['link'], reverse('fin_jobs_pnl'))

        # 6. Verify Do NOT auto-delete anything
        self.assertTrue(JobCost.objects.filter(id=legacy_cost.id).exists())

    def test_project_summary_same_numbers_as_jobs_pnl(self):
        # Requirements:
        # project with invoice 8,500, spares cost 2,750, employee cost 165.91
        # -> summary shows employee 165.91, spares 2,750.00, total 2,915.91, profit 5,584.09, 65.7%,
        # and Jobs P&L shows the same profit.
        proj = Project.objects.create(
            name='Sync Test Project',
            client_name='Maritime Client A',
            service_reference='SR-SYNC-01',
            status='IN_PROGRESS',
        )
        inv = Invoice.objects.create(
            project=proj,
            invoice_number='AMMS-INV-26-0501',
            invoice_date=self.today,
            amount_excl_vat=Decimal('8500.00'),
            invoice_type='SERVICE_JOB',
        )
        eng_user = User.objects.create_user(username='eng_sync', password='testpassword')
        eng_emp = Employee.objects.create(user=eng_user, salary=Decimal('4160.00'))
        slog = EmployeeServiceLog.objects.create(
            employee=eng_emp,
            project=proj,
            date=self.today,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(13, 0),
            service_type='JOB',
        )
        EmployeeServiceLog.objects.filter(id=slog.id).update(
            total_hours=Decimal('5.0'),
            normal_cost=Decimal('100.00'),
            ot_cost=Decimal('65.91'),
        )
        SpareSale.objects.create(
            project=proj,
            invoice=inv,
            purchase_cost=Decimal('2750.00'),
            selling_price=Decimal('3500.00'),
            source='SERVICE_JOB',
        )

        # 1. Test get_project_financials
        fin = get_project_financials(proj)
        self.assertEqual(fin['invoiced_amount'], Decimal('8500.00'))
        self.assertEqual(fin['employee_cost'], Decimal('165.91'))
        self.assertEqual(fin['spares_cost'], Decimal('2750.00'))
        self.assertEqual(fin['total_cost'], Decimal('2915.91'))
        self.assertEqual(fin['profit'], Decimal('5584.09'))
        self.assertEqual(fin['profit_pct'], Decimal('65.7'))

        # 2. Test get_jobs_pnl_data shows the exact same profit and total cost
        jobs_data, totals = get_jobs_pnl_data(filters={'search': 'AMMS-INV-26-0501'})
        self.assertEqual(len(jobs_data), 1)
        job_row = jobs_data[0]
        self.assertEqual(job_row['spare_cost'], Decimal('2750.00'))
        self.assertEqual(job_row['engineer'], Decimal('165.91'))
        self.assertEqual(job_row['total_cost'], Decimal('2915.91'))
        self.assertEqual(job_row['profit'], Decimal('5584.09'))
        self.assertEqual(round(job_row['margin_pct'], 1), Decimal('65.7'))

        # 3. Test Admin project summary page shows employee 165.91, spares 2,750.00, total 2,915.91, profit 5,584.09, 65.7%
        # and small "View in Jobs P&L" link
        self.client.login(username='admin_test_pnl', password='testpassword')
        resp = self.client.get(reverse('project-summary', args=[proj.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '165.91 AED')
        self.assertContains(resp, '2,750.00 AED')
        self.assertContains(resp, '2,915.91 AED')
        self.assertContains(resp, '5,584.09 AED')
        self.assertContains(resp, '65.7%')
        self.assertContains(resp, 'View in Jobs P&L')
        self.assertContains(resp, f"{reverse('fin_jobs_pnl')}?search=AMMS-INV-26-0501")

    def test_one_engineer_cost_rule_everywhere(self):
        p1 = Project.objects.create(name='P1-Rule', status='ONGOING')
        p2 = Project.objects.create(name='P2-Rule', status='ONGOING')

        u = User.objects.create_user(username='eng_rule', password='testpassword')
        emp = Employee.objects.create(user=u, salary=Decimal('4160.00'))  # hourly = 4160/208 = 20.00

        att_dt = timezone.now() - datetime.timedelta(days=1)
        Attendance.objects.create(
            employee=emp, project=p1, login_time=att_dt, log_out_time=att_dt + datetime.timedelta(hours=3),
            total_hours_of_work=3.0,
        )
        Attendance.objects.create(
            employee=emp, project=p2, login_time=att_dt, log_out_time=att_dt + datetime.timedelta(hours=2),
            total_hours_of_work=2.0,
        )

        # P1: 5 hours service log (normal 100, ot 50) + 3 hours attendance (3 * 20 = 60) -> 210.00, 8 hours
        s1 = EmployeeServiceLog.objects.create(
            employee=emp, project=p1, date=self.today,
            service_type='JOB', start_time=datetime.time(8, 0), end_time=datetime.time(13, 0),
        )
        EmployeeServiceLog.objects.filter(id=s1.id).update(total_hours=Decimal('5.0'), normal_cost=Decimal('100.00'), ot_cost=Decimal('50.00'))

        res = engineer_cost_for_projects([p1.id, p2.id])
        self.assertEqual(res[p1.id]['hours'], Decimal('8.00'))
        self.assertEqual(res[p1.id]['cost'], Decimal('210.00'))
        self.assertEqual(res[p2.id]['hours'], Decimal('2.00'))
        self.assertEqual(res[p2.id]['cost'], Decimal('40.00'))

        # Invoices: P1 has 2 invoices (collection 6000 and 4000 = 60% and 40%)
        inv1 = Invoice.objects.create(project=p1, invoice_number='AMMS-INV-26-0601', invoice_date=self.today, amount_excl_vat=Decimal('6000.00'))
        inv2 = Invoice.objects.create(project=p1, invoice_number='AMMS-INV-26-0602', invoice_date=self.today, amount_excl_vat=Decimal('4000.00'))
        # P2 has 1 invoice
        inv3 = Invoice.objects.create(project=p2, invoice_number='AMMS-INV-26-0603', invoice_date=self.today, amount_excl_vat=Decimal('5000.00'))

        cost_map = compute_invoice_engineer_costs([inv1, inv2, inv3])
        self.assertEqual(cost_map[inv1.id], Decimal('126.00'))
        self.assertEqual(cost_map[inv2.id], Decimal('84.00'))
        self.assertEqual(cost_map[inv3.id], Decimal('40.00'))
        self.assertEqual(cost_map[inv1.id] + cost_map[inv2.id], Decimal('210.00'))

        # pnl_for_range "engineer cost allocated" must equal Jobs P&L sum
        pnl = pnl_for_range(self.today, self.today, 'month')
        pnl_alloc_eng = sum(b['allocated_engineer_cost'] for b in pnl['buckets'])

        rows, totals = get_jobs_pnl_data(filters={'start_date': self.today, 'end_date': self.today})
        # Find rows for inv1, inv2, inv3
        target_inv_ids = {inv1.id, inv2.id, inv3.id}
        target_rows = [r for r in rows if r['id'] in target_inv_ids]
        jobs_eng_sum = sum(r['engineer'] for r in target_rows)
        self.assertEqual(jobs_eng_sum, Decimal('250.00'))
        self.assertEqual(jobs_eng_sum, cost_map[inv1.id] + cost_map[inv2.id] + cost_map[inv3.id])

    def test_project_summary_total_cost_equals_jobs_pnl_row(self):
        proj = Project.objects.create(name='P-Match', client_name='Client Match', status='IN_PROGRESS')
        inv = Invoice.objects.create(
            project=proj, invoice_number='AMMS-INV-26-0701', invoice_date=self.today, amount_excl_vat=Decimal('10000.00')
        )
        u = User.objects.create_user(username='eng_match', password='testpassword')
        emp = Employee.objects.create(user=u, salary=Decimal('2080.00'))  # 10/hr
        now_dt = timezone.now()
        Attendance.objects.create(employee=emp, project=proj, login_time=now_dt, log_out_time=now_dt + datetime.timedelta(hours=5), total_hours_of_work=5.0)  # 50.00
        SpareSale.objects.create(project=proj, invoice=inv, purchase_cost=Decimal('1200.00'), selling_price=Decimal('2000.00'))
        JobCost.objects.create(invoice=inv, cost_type='TRAVEL', amount=Decimal('300.00'), travel_port='Dubai')
        JobCost.objects.create(invoice=inv, cost_type='FLIGHT', amount=Decimal('450.00'))

        summary_fin = get_project_financials(proj)
        jobs_rows, _ = get_jobs_pnl_data(filters={'search': 'AMMS-INV-26-0701'})
        self.assertEqual(len(jobs_rows), 1)
        job_row = jobs_rows[0]

        self.assertEqual(summary_fin['total_cost'], job_row['total_cost'])
        self.assertEqual(summary_fin['profit'], job_row['profit'])
        self.assertEqual(summary_fin['employee_cost'], job_row['engineer'])
        self.assertEqual(summary_fin['spares_cost'], job_row['spare_cost'])
        self.assertEqual(summary_fin['other_job_costs'], job_row['travel_cost'] + job_row['flight'] + job_row['misc'] + job_row['other'])

        # Multi-invoice project with unlinked spares:
        # inv_early (earlier date), inv_late (later date), and an unlinked spare (project set, invoice NULL).
        # Unlinked spare must land on inv_late (the project's latest non-cancelled invoice).
        proj_m = Project.objects.create(name='P-Multi-Spares', client_name='Client Multi', status='IN_PROGRESS')
        inv_early = Invoice.objects.create(
            project=proj_m, invoice_number='AMMS-INV-26-0702', invoice_date=self.today - datetime.timedelta(days=10), amount_excl_vat=Decimal('4000.00')
        )
        inv_late = Invoice.objects.create(
            project=proj_m, invoice_number='AMMS-INV-26-0703', invoice_date=self.today, amount_excl_vat=Decimal('6000.00')
        )
        SpareSale.objects.create(project=proj_m, invoice=inv_early, purchase_cost=Decimal('500.00'), selling_price=Decimal('800.00'))
        SpareSale.objects.create(project=proj_m, invoice=inv_late, purchase_cost=Decimal('700.00'), selling_price=Decimal('1000.00'))
        SpareSale.objects.create(project=proj_m, invoice=None, purchase_cost=Decimal('300.00'), selling_price=Decimal('500.00'))

        m_fin = get_project_financials(proj_m)
        m_rows, _ = get_jobs_pnl_data(filters={'search': 'AMMS-INV-26-070'})
        m_rows_by_id = {r['id']: r for r in m_rows}

        # Early invoice has only its own linked spare
        self.assertEqual(m_rows_by_id[inv_early.id]['spare_cost'], Decimal('500.00'))
        # Late invoice has its own linked spare (700) + the unlinked spare (300) = 1000.00
        self.assertEqual(m_rows_by_id[inv_late.id]['spare_cost'], Decimal('1000.00'))
        # Total spares in Jobs P&L equals project summary spares_cost (1500.00)
        total_jobs_spares = m_rows_by_id[inv_early.id]['spare_cost'] + m_rows_by_id[inv_late.id]['spare_cost']
        self.assertEqual(total_jobs_spares, m_fin['spares_cost'])
        # Total cost across all invoices in Jobs P&L equals project summary total_cost
        total_jobs_cost = m_rows_by_id[inv_early.id]['total_cost'] + m_rows_by_id[inv_late.id]['total_cost']
        self.assertEqual(total_jobs_cost, m_fin['total_cost'])

    def test_spares_rules_and_pnl_unlinked_alert(self):
        # 1. Project with invoice and unlinked spare (project set, invoice NULL)
        p_with_inv = Project.objects.create(name='P-With-Inv', status='IN_PROGRESS')
        inv = Invoice.objects.create(
            project=p_with_inv, invoice_number='AMMS-INV-26-0801', invoice_date=self.today, amount_excl_vat=Decimal('5000.00')
        )
        SpareSale.objects.create(project=p_with_inv, invoice=None, purchase_cost=Decimal('400.00'), selling_price=Decimal('600.00'))

        # 2. Project with NO invoice and unlinked spare
        p_no_inv = Project.objects.create(name='P-No-Inv', status='IN_PROGRESS')
        SpareSale.objects.create(project=p_no_inv, invoice=None, purchase_cost=Decimal('900.00'), selling_price=Decimal('1200.00'))

        # Check pnl_for_range:
        # P-With-Inv's spare is dated by inv.invoice_date (today) -> included in bucket_spares
        # P-No-Inv's spare has no invoice -> excluded from P&L
        pnl = pnl_for_range(self.today, self.today, 'month')
        target_bucket = next((b for b in pnl['buckets'] if b['start_date'] <= self.today <= b['end_date']), None)
        self.assertIsNotNone(target_bucket)
        self.assertGreaterEqual(target_bucket['spares_cost'], Decimal('400.00'))

        # Check dashboard alert "Spares not linked to an invoice"
        dash = get_dashboard_data(period='this_month')
        alert = next((a for a in dash['alerts'] if a['id'] == 'spares_not_linked_to_invoice'), None)
        self.assertIsNotNone(alert)
        self.assertIn("Spares not linked to an invoice", alert['title'])
        self.assertEqual(alert['link'], reverse('fin_spares'))
        self.assertIn("1 Spares not linked to an invoice", alert['title'])

    def test_jobs_pnl_queries_3_projects_equals_10_projects(self):
        u = User.objects.create_user(username='eng_qtest', password='testpassword')
        emp = Employee.objects.create(user=u, salary=Decimal('4160.00'))

        # Create 3 projects
        for i in range(1, 4):
            proj = Project.objects.create(name=f'QProj-A{i}', status='ONGOING')
            inv = Invoice.objects.create(
                project=proj, invoice_number=f'AMMS-INV-26-30{i:02d}', invoice_date=self.today, amount_excl_vat=Decimal('1000.00')
            )
            slog = EmployeeServiceLog.objects.create(
                employee=emp, project=proj, date=self.today,
                service_type='JOB', start_time=datetime.time(9, 0), end_time=datetime.time(12, 0)
            )
            EmployeeServiceLog.objects.filter(id=slog.id).update(total_hours=Decimal('3.0'), normal_cost=Decimal('60.00'))
            now_dt = timezone.now()
            Attendance.objects.create(employee=emp, project=proj, login_time=now_dt, log_out_time=now_dt + datetime.timedelta(hours=2), total_hours_of_work=2.0)
            SpareSale.objects.create(project=proj, invoice=inv, purchase_cost=Decimal('150.00'), selling_price=Decimal('250.00'))
            JobCost.objects.create(invoice=inv, cost_type='TRAVEL', amount=Decimal('50.00'))
            JobCost.objects.create(invoice=inv, cost_type='FLIGHT', amount=Decimal('80.00'))

        with CaptureQueriesContext(connection) as ctx_3:
            rows_3, _ = get_jobs_pnl_data(filters={'search': 'AMMS-INV-26-30'})
        self.assertEqual(len(rows_3), 3)
        q_count_3 = len(ctx_3.captured_queries)

        # Create 7 more projects (10 projects under prefix AMMS-INV-26-31)
        for i in range(1, 11):
            proj = Project.objects.create(name=f'QProj-B{i}', status='ONGOING')
            inv = Invoice.objects.create(
                project=proj, invoice_number=f'AMMS-INV-26-31{i:02d}', invoice_date=self.today, amount_excl_vat=Decimal('1000.00')
            )
            slog = EmployeeServiceLog.objects.create(
                employee=emp, project=proj, date=self.today,
                service_type='JOB', start_time=datetime.time(9, 0), end_time=datetime.time(12, 0)
            )
            EmployeeServiceLog.objects.filter(id=slog.id).update(total_hours=Decimal('3.0'), normal_cost=Decimal('60.00'))
            now_dt = timezone.now()
            Attendance.objects.create(employee=emp, project=proj, login_time=now_dt, log_out_time=now_dt + datetime.timedelta(hours=2), total_hours_of_work=2.0)
            SpareSale.objects.create(project=proj, invoice=inv, purchase_cost=Decimal('150.00'), selling_price=Decimal('250.00'))
            JobCost.objects.create(invoice=inv, cost_type='TRAVEL', amount=Decimal('50.00'))
            JobCost.objects.create(invoice=inv, cost_type='FLIGHT', amount=Decimal('80.00'))

        with CaptureQueriesContext(connection) as ctx_10:
            rows_10, _ = get_jobs_pnl_data(filters={'search': 'AMMS-INV-26-31'})
        self.assertEqual(len(rows_10), 10)
        q_count_10 = len(ctx_10.captured_queries)

        self.assertEqual(q_count_3, q_count_10)

    def test_manager_and_administration_project_summary_templates(self):
        mgr_user = User.objects.create_user(username='mgr_test', password='testpassword')
        mgr_emp = Employee.objects.create(user=mgr_user, is_manager=True)
        proj = Project.objects.create(name='P-Tmpl', status='COMPLETED', manager=mgr_emp)
        inv = Invoice.objects.create(project=proj, invoice_number='AMMS-INV-26-0999', invoice_date=self.today, amount_excl_vat=Decimal('4000.00'))
        fin_pnl_url = reverse('fin_jobs_pnl')

        # 1. Manager view: financials hidden, no fin_jobs_pnl link, context has no invoiced_amount / profit keys
        self.client.login(username='mgr_test', password='testpassword')
        resp_mgr = self.client.get(reverse('project-summary-view', args=[proj.id]))
        self.assertEqual(resp_mgr.status_code, 200)
        self.assertNotContains(resp_mgr, 'Employee Cost')
        self.assertNotContains(resp_mgr, 'Spares Cost')
        self.assertNotContains(resp_mgr, 'Profit')
        self.assertNotContains(resp_mgr, fin_pnl_url)
        self.assertNotIn('invoiced_amount', resp_mgr.context)
        self.assertNotIn('profit', resp_mgr.context)
        mgr_project_data = resp_mgr.context.get('project_data', {})
        self.assertNotIn('invoiced_amount', mgr_project_data)
        self.assertNotIn('profit', mgr_project_data)

        # 2. Administration view: financials hidden, no fin_jobs_pnl link, context has no invoiced_amount / profit keys
        adm_user = User.objects.create_user(username='adm_test', password='testpassword')
        Employee.objects.create(user=adm_user, is_administration=True)
        self.client.login(username='adm_test', password='testpassword')
        resp_adm = self.client.get(reverse('admstrn_project_summary_view', args=[proj.id]))
        self.assertEqual(resp_adm.status_code, 200)
        self.assertNotContains(resp_adm, 'Employee Cost')
        self.assertNotContains(resp_adm, 'Spares Cost')
        self.assertNotContains(resp_adm, 'Profit')
        self.assertNotContains(resp_adm, fin_pnl_url)
        self.assertNotIn('invoiced_amount', resp_adm.context)
        self.assertNotIn('profit', resp_adm.context)
        adm_project_data = resp_adm.context.get('project_data', {})
        self.assertNotIn('invoiced_amount', adm_project_data)
        self.assertNotIn('profit', adm_project_data)

        # 3. Admin view: superuser still sees Employee Cost and fin_jobs_pnl link
        admin_user = User.objects.create_superuser(username='admin_test', email='admin@test.com', password='testpassword')
        self.client.login(username='admin_test', password='testpassword')
        resp_admin = self.client.get(reverse('project-summary', args=[proj.id]))
        self.assertEqual(resp_admin.status_code, 200)
        self.assertContains(resp_admin, 'Employee Cost')
        self.assertContains(resp_admin, fin_pnl_url)

        # 4. Manager GET fin_jobs_pnl -> 403 using 403.html
        self.client.login(username='mgr_test', password='testpassword')
        resp_pnl = self.client.get(fin_pnl_url)
        self.assertEqual(resp_pnl.status_code, 403)
        self.assertTemplateUsed(resp_pnl, '403.html')
        self.assertContains(resp_pnl, "You don't have permission to view this page", status_code=403)
        self.assertContains(resp_pnl, "history.back()", status_code=403)

    def test_manager_and_administration_dashboards_hide_revenue_expenses_profit(self):
        # Manager dashboard
        mgr_user = User.objects.create_user(username='mgr_dash_user', password='testpassword', is_staff=True)
        Employee.objects.create(user=mgr_user, is_manager=True)
        self.client.login(username='mgr_dash_user', password='testpassword')
        resp_mgr = self.client.get(reverse('manager-dashboard'))
        self.assertEqual(resp_mgr.status_code, 200)
        self.assertNotContains(resp_mgr, 'Revenue')
        self.assertNotContains(resp_mgr, 'Profit')
        self.assertNotContains(resp_mgr, 'AED')
        self.assertNotIn('total_revenue', resp_mgr.context)
        self.assertNotIn('total_expenses', resp_mgr.context)
        self.assertNotIn('total_profit', resp_mgr.context)

        # Administration dashboard
        adm_user = User.objects.create_user(username='adm_dash_user', password='testpassword')
        Employee.objects.create(user=adm_user, is_administration=True)
        self.client.login(username='adm_dash_user', password='testpassword')
        resp_adm = self.client.get(reverse('admstrn-dashboard'))
        self.assertEqual(resp_adm.status_code, 200)
        self.assertNotContains(resp_adm, 'Revenue')
        self.assertNotContains(resp_adm, 'Profit')
        self.assertNotContains(resp_adm, 'AED')
        self.assertNotIn('total_revenue', resp_adm.context)
        self.assertNotIn('total_expenses', resp_adm.context)
        self.assertNotIn('total_profit', resp_adm.context)

        # Admin dashboard
        admin_user = User.objects.create_superuser(username='admin_dash_user', email='admin_dash@test.com', password='testpassword')
        self.client.login(username='admin_dash_user', password='testpassword')
        resp_admin = self.client.get(reverse('admin-dashboard'))
        self.assertEqual(resp_admin.status_code, 200)
        self.assertContains(resp_admin, 'Revenue')
        self.assertContains(resp_admin, 'Profit')
        self.assertContains(resp_admin, 'AED')

    def test_engineer_cost_deduplicates_attendance_when_service_log_on_same_day(self):
        """
        Employee with an 8h service log and 8h attendance on the same day/project -> hours 8, not 16.
        """
        p = Project.objects.create(name="Dedup Test Project", service_reference="SR-DEDUP")
        u = User.objects.create_user(username='eng_dedup_user', password='testpassword')
        emp = Employee.objects.create(user=u, salary=Decimal('4160.00'))  # hourly = 20.00

        login_t = timezone.make_aware(datetime.datetime.combine(self.today, datetime.time(8, 0)))
        logout_t = login_t + datetime.timedelta(hours=8)
        att = Attendance.objects.create(
            employee=emp,
            project=p,
            login_time=login_t,
            log_out_time=logout_t,
            status='APPROVED',
        )
        att.total_hours_of_work = Decimal('8.00')
        att.save(update_fields=['total_hours_of_work'])

        EmployeeServiceLog.objects.create(
            employee=emp,
            project=p,
            date=self.today,
            service_type='JOB',
            start_time=datetime.time(8, 0),
            end_time=datetime.time(16, 0),
            total_hours=Decimal('8.00'),
            normal_cost=Decimal('8.00') * (emp.salary / Decimal('208.00')),
            ot_cost=Decimal('0.00'),
        )

        res = engineer_cost_for_projects([p.id])
        self.assertEqual(res[p.id]['hours'], Decimal('8.00'))
        self.assertEqual(res[p.id]['cost'], Decimal('160.00'))


class FinanceAdminAccessImprovementsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser('test_fin_admin', 'fin_admin@example.com', 'adminpass123')
        self.accountant_user = User.objects.create_user('test_fin_acc', 'fin_acc@example.com', 'accpass123')
        self.accountant_emp = Employee.objects.create(
            user=self.accountant_user,
            is_accountant=True,
            salary=Decimal('5000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )
        self.today = timezone.localdate()

    def test_sidebar_admin_vs_accountant(self):
        # Admin sidebar test
        self.client.force_login(self.admin)
        resp_admin = self.client.get(reverse('fin_dashboard'))
        self.assertEqual(resp_admin.status_code, 200)
        content_admin = resp_admin.content.decode('utf-8')
        self.assertIn('Back to Admin Dashboard', content_admin)
        self.assertIn(reverse('admin-dashboard'), content_admin)
        self.assertNotIn('Apply Leave', content_admin)
        self.assertNotIn('Punch In / Out', content_admin)
        # Logo links to admin-dashboard for admin
        self.assertIn(f'href="{reverse("admin-dashboard")}"', content_admin)
        # User dropdown has Admin Dashboard
        self.assertIn('Admin Dashboard', content_admin)

        # Accountant sidebar test
        self.client.force_login(self.accountant_user)
        resp_acc = self.client.get(reverse('fin_dashboard'))
        self.assertEqual(resp_acc.status_code, 200)
        content_acc = resp_acc.content.decode('utf-8')
        self.assertNotIn('Back to Admin Dashboard', content_acc)
        self.assertIn('Apply Leave', content_acc)
        self.assertIn('Punch In / Out', content_acc)
        # Logo links to fin_dashboard for accountant
        self.assertIn(f'href="{reverse("fin_dashboard")}"', content_acc)
        self.assertIn('Profile', content_acc)

    def test_superuser_get_apply_leave_redirects_to_admin_dashboard(self):
        self.client.force_login(self.admin)
        # All 11 personal views redirect superuser to admin-dashboard
        personal_urls = [
            reverse('fin_attendance_dashboard'),
            reverse('fin_log_in'),
            reverse('fin_attendance_status'),
            reverse('fin_apply_leave'),
            reverse('fin_leave_status'),
            reverse('fin_leave_records'),
            reverse('fin_profile_view'),
            reverse('fin_update_profile'),
        ]
        for url in personal_urls:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, f"Failed for {url}")
            self.assertRedirects(resp, reverse('admin-dashboard'))

    def test_superuser_fetch_notifications_without_employee_profile(self):
        # self.admin has no Employee profile
        self.assertFalse(hasattr(self.admin, 'employee') and self.admin.employee)
        self.client.force_login(self.admin)

        resp_fetch = self.client.get(reverse('fin_fetch_notifications'))
        self.assertEqual(resp_fetch.status_code, 200)
        self.assertEqual(resp_fetch.json(), {"notifications": []})

        resp_mark = self.client.post(reverse('fin_mark_notifications_as_read'))
        self.assertEqual(resp_mark.status_code, 200)
        self.assertEqual(resp_mark.json(), {"status": "ok"})

    def test_payroll_lock_revert_and_delete_permissions(self):
        eng_user = User.objects.create_user('eng_payroll', 'eng_payroll@example.com', 'pass123')
        eng = Employee.objects.create(user=eng_user, is_employee=True, salary=Decimal('4000.00'), date_of_join=datetime.date(2026, 1, 1))

        # 1. APPROVED payroll: accountant cannot edit, admin can
        p_approved = Payroll.objects.create(
            employee=eng,
            month=datetime.date(2026, 8, 1),
            basic_salary=Decimal('4000.00'),
            status='APPROVED',
            created_by=self.accountant_user,
        )

        self.client.force_login(self.accountant_user)
        resp_acc_edit = self.client.get(reverse('fin_payroll_edit', args=[p_approved.id]))
        self.assertEqual(resp_acc_edit.status_code, 302)  # redirected with error

        self.client.force_login(self.admin)
        resp_adm_edit = self.client.get(reverse('fin_payroll_edit', args=[p_approved.id]))
        self.assertEqual(resp_adm_edit.status_code, 200)

        # 2. PAID payroll: locked for everyone
        p_paid = Payroll.objects.create(
            employee=eng,
            month=datetime.date(2026, 7, 1),
            basic_salary=Decimal('4000.00'),
            status='PAID',
            created_by=self.accountant_user,
        )
        self.client.force_login(self.accountant_user)
        self.assertEqual(self.client.get(reverse('fin_payroll_edit', args=[p_paid.id])).status_code, 302)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('fin_payroll_edit', args=[p_paid.id])).status_code, 302)

        # Nobody can delete PAID
        resp_del_paid = self.client.post(reverse('fin_payroll_delete', args=[p_paid.id]))
        self.assertEqual(resp_del_paid.status_code, 302)
        self.assertTrue(Payroll.objects.filter(id=p_paid.id).exists())

        # 3. Accountant revert / delete returns 403 Forbidden
        self.client.force_login(self.accountant_user)
        resp_acc_rev = self.client.post(reverse('fin_payroll_revert', args=[p_approved.id]))
        self.assertEqual(resp_acc_rev.status_code, 403)
        resp_acc_del = self.client.post(reverse('fin_payroll_delete', args=[p_approved.id]))
        self.assertEqual(resp_acc_del.status_code, 403)

        # 4. Admin revert APPROVED -> DRAFT works
        self.client.force_login(self.admin)
        resp_adm_rev = self.client.post(reverse('fin_payroll_revert', args=[p_approved.id]))
        self.assertEqual(resp_adm_rev.status_code, 302)
        p_approved.refresh_from_db()
        self.assertEqual(p_approved.status, 'DRAFT')

        # 5. Admin delete DRAFT record works
        resp_adm_del = self.client.post(reverse('fin_payroll_delete', args=[p_approved.id]))
        self.assertEqual(resp_adm_del.status_code, 302)
        self.assertFalse(Payroll.objects.filter(id=p_approved.id).exists())

    def test_invoice_delete_permissions_and_relationships(self):
        inv = Invoice.objects.create(
            invoice_number='INV-TEST-DEL-01',
            invoice_date=self.today,
            amount_excl_vat=Decimal('5000.00'),
            amount_received=Decimal('1000.00'),
        )
        jc = JobCost.objects.create(invoice=inv, cost_type='TRAVEL', amount=Decimal('200.00'))
        spare = SpareSale.objects.create(invoice=inv, service_ref='SR-DEL-TEST', purchase_cost=Decimal('500.00'), selling_price=Decimal('800.00'))

        # Accountant gets 403
        self.client.force_login(self.accountant_user)
        resp_acc = self.client.post(reverse('fin_invoice_delete', args=[inv.id]))
        self.assertEqual(resp_acc.status_code, 403)

        # Admin delete refused when amount_received > 0
        self.client.force_login(self.admin)
        resp_adm_refused = self.client.post(reverse('fin_invoice_delete', args=[inv.id]))
        self.assertEqual(resp_adm_refused.status_code, 302)
        self.assertTrue(Invoice.objects.filter(id=inv.id).exists())

        # Admin delete succeeds when amount_received == 0
        inv.amount_received = Decimal('0.00')
        inv.save()
        resp_adm_ok = self.client.post(reverse('fin_invoice_delete', args=[inv.id]))
        self.assertEqual(resp_adm_ok.status_code, 302)
        self.assertFalse(Invoice.objects.filter(id=inv.id).exists())
        # Related JobCost deleted
        self.assertFalse(JobCost.objects.filter(id=jc.id).exists())
        # SpareSale.invoice becomes NULL
        spare.refresh_from_db()
        self.assertIsNone(spare.invoice)

    def test_category_delete_permissions_and_refusal(self):
        cat_with_exp = ExpenseCategory.objects.create(name='Used Category', kind='OVERHEAD')
        Expense.objects.create(category=cat_with_exp, amount=Decimal('250.00'), date=self.today, created_by=self.admin)

        cat_unused = ExpenseCategory.objects.create(name='Unused Category', kind='OVERHEAD')

        # Accountant gets 403
        self.client.force_login(self.accountant_user)
        resp_acc = self.client.post(reverse('fin_category_delete', args=[cat_with_exp.id]))
        self.assertEqual(resp_acc.status_code, 403)

        # Admin delete refused when category used by expense
        self.client.force_login(self.admin)
        resp_adm_refused = self.client.post(reverse('fin_category_delete', args=[cat_with_exp.id]))
        self.assertEqual(resp_adm_refused.status_code, 302)
        self.assertTrue(ExpenseCategory.objects.filter(id=cat_with_exp.id).exists())

        # Admin delete unused category succeeds
        resp_adm_ok = self.client.post(reverse('fin_category_delete', args=[cat_unused.id]))
        self.assertEqual(resp_adm_ok.status_code, 302)
        self.assertFalse(ExpenseCategory.objects.filter(id=cat_unused.id).exists())

    def test_spare_delete_permissions(self):
        spare = SpareSale.objects.create(service_ref='SR-SPARE-DEL', purchase_cost=Decimal('100.00'), selling_price=Decimal('150.00'))
        self.client.force_login(self.accountant_user)
        self.assertEqual(self.client.post(reverse('fin_spare_delete', args=[spare.id])).status_code, 403)

        self.client.force_login(self.admin)
        resp_adm = self.client.post(reverse('fin_spare_delete', args=[spare.id]))
        self.assertEqual(resp_adm.status_code, 302)
        self.assertFalse(SpareSale.objects.filter(id=spare.id).exists())


class FinanceProjectsMigrationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.accountant_user = User.objects.create_user('acc_proj_test', 'acc_proj@example.com', 'pass1234')
        self.accountant_emp = Employee.objects.create(
            user=self.accountant_user,
            is_accountant=True,
            salary=Decimal('5000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )
        self.client.force_login(self.accountant_user)

    def test_project_with_1_invoice_and_empty_legacy_fields(self):
        today = timezone.localdate()
        p = Project.objects.create(
            name='Single Inv Proj',
            client_name='Client Single',
            vessel_name='Vessel Single',
            invoice_amount=Decimal('0.00'),
            invoice_number='',
            invoice_date=None,
        )
        Invoice.objects.create(
            project=p,
            invoice_number='AMMS-INV-26-0090',
            invoice_date=today,
            due_date=today + datetime.timedelta(days=30),
            amount_excl_vat=Decimal('5000.00'),
            amount_received=Decimal('0.00'),
        )
        rows, totals = get_projects_profitability()
        p_row = next((r for r in rows if r['id'] == p.id), None)
        self.assertIsNotNone(p_row)
        self.assertEqual(p_row['invoice_no'], 'AMMS-INV-26-0090')
        self.assertEqual(p_row['status'], 'Open')
        self.assertEqual(p_row['revenue'], Decimal('5000.00'))
        self.assertTrue(p_row['invoiced'])
        self.assertNotEqual(p_row['status'], 'Not invoiced')

        resp = self.client.get(reverse('fin_projects'))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        self.assertIn('AMMS-INV-26-0090', content)
        self.assertTrue('5,000.00' in content or '5000.00' in content)
        self.assertIn('Open', content)

    def test_project_with_2_invoices_shows_plus_1_more_and_partial_status(self):
        today = timezone.localdate()
        p = Project.objects.create(
            name='Dual Inv Proj',
            client_name='Client Dual',
            vessel_name='Vessel Dual',
            invoice_amount=Decimal('0.00'),
        )
        Invoice.objects.create(
            project=p,
            invoice_number='AMMS-INV-26-0080',
            invoice_date=today,
            due_date=today + datetime.timedelta(days=30),
            amount_excl_vat=Decimal('3000.00'),
            amount_received=Decimal('3000.00'),  # PAID
        )
        Invoice.objects.create(
            project=p,
            invoice_number='AMMS-INV-26-0081',
            invoice_date=today,
            due_date=today + datetime.timedelta(days=30),
            amount_excl_vat=Decimal('2000.00'),
            amount_received=Decimal('0.00'),  # OPEN
        )
        rows, totals = get_projects_profitability()
        p_row = next((r for r in rows if r['id'] == p.id), None)
        self.assertIsNotNone(p_row)
        self.assertIn('+1 more', p_row['invoice_no'])
        self.assertEqual(p_row['revenue'], Decimal('5000.00'))
        self.assertEqual(p_row['status'], 'Partial')
        self.assertEqual(p_row['received'], Decimal('3000.00'))
        self.assertEqual(p_row['outstanding'], Decimal('2000.00'))

    def test_project_detail_revenue_profit_equals_get_project_financials_and_contains_bg_blue(self):
        today = timezone.localdate()
        p = Project.objects.create(
            name='Detail Test Proj',
            client_name='Detail Client',
            vessel_name='Detail Vessel',
            invoice_amount=Decimal('0.00'),
        )
        inv = Invoice.objects.create(
            project=p,
            invoice_number='AMMS-INV-26-0085',
            invoice_date=today,
            due_date=today + datetime.timedelta(days=30),
            amount_excl_vat=Decimal('7500.00'),
            amount_received=Decimal('2500.00'),
        )
        JobCost.objects.create(
            invoice=inv,
            cost_type='TRAVEL',
            amount=Decimal('500.00'),
            date=today,
        )
        fin = get_project_financials(p)
        resp = self.client.get(reverse('fin_project_detail', args=[p.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['cost_breakdown']['revenue'], fin['invoiced_amount'])
        self.assertEqual(resp.context['cost_breakdown']['profit'], fin['profit'])

        content = resp.content.decode('utf-8')
        self.assertIn('bg-blue', content)
        self.assertIn('Invoices', content)
        self.assertIn('AMMS-INV-26-0085', content)
        self.assertIn('7,500.00', content)
        self.assertEqual(content.count('class="kpi-card"'), 4)
        self.assertIn("Received: AED", content)
        self.assertIn("Outstanding: AED", content)
        self.assertIn('kpi-icon fin-round bg-orange', content)

        kpi_section = content.split('<!-- KPI Cards -->')[1].split('<!-- Invoices Section -->')[0]
        div_opens = len(re.findall(r'<div\b', kpi_section))
        div_closes = len(re.findall(r'</div\b', kpi_section))
        self.assertEqual(div_opens, div_closes)

    def test_fin_project_invoice_edit_redirects_for_0_1_2_invoices_and_ignores_evil_next(self):
        # 0 invoices -> redirects to fin_invoice_add?project=<id>
        p0 = Project.objects.create(name='Zero Invoices Proj')
        resp0 = self.client.get(reverse('fin_project_invoice_edit', args=[p0.id]))
        self.assertEqual(resp0.status_code, 302)
        self.assertIn(reverse('fin_invoice_add'), resp0.url)
        self.assertIn(f'project={p0.id}', resp0.url)

        # 0 invoices with evil next -> evil next is ignored
        resp0_evil = self.client.get(reverse('fin_project_invoice_edit', args=[p0.id]) + '?next=https://evil.com')
        self.assertEqual(resp0_evil.status_code, 302)
        self.assertNotIn('evil.com', resp0_evil.url)
        self.assertIn(f'project={p0.id}', resp0_evil.url)

        # 1 invoice -> redirects to fin_invoice_edit(inv.id)
        p1 = Project.objects.create(name='One Invoice Proj')
        inv1 = Invoice.objects.create(
            project=p1,
            invoice_number='AMMS-INV-26-0086',
            invoice_date=datetime.date(2026, 3, 5),
            amount_excl_vat=Decimal('4000.00'),
        )
        resp1 = self.client.get(reverse('fin_project_invoice_edit', args=[p1.id]))
        self.assertEqual(resp1.status_code, 302)
        self.assertIn(reverse('fin_invoice_edit', args=[inv1.id]), resp1.url)

        # 1 invoice with evil next -> evil next is ignored
        resp1_evil = self.client.get(reverse('fin_project_invoice_edit', args=[p1.id]) + '?next=https://evil.com')
        self.assertEqual(resp1_evil.status_code, 302)
        self.assertNotIn('evil.com', resp1_evil.url)
        self.assertIn(reverse('fin_invoice_edit', args=[inv1.id]), resp1_evil.url)

        # >1 invoices -> redirects to fin_invoices?search=<project name>
        from urllib.parse import unquote
        p2 = Project.objects.create(name='Multi Invoice Proj')
        Invoice.objects.create(
            project=p2,
            invoice_number='AMMS-INV-26-0087',
            invoice_date=datetime.date(2026, 3, 6),
            amount_excl_vat=Decimal('2000.00'),
        )
        Invoice.objects.create(
            project=p2,
            invoice_number='AMMS-INV-26-0088',
            invoice_date=datetime.date(2026, 3, 7),
            amount_excl_vat=Decimal('2000.00'),
        )
        resp2 = self.client.get(reverse('fin_project_invoice_edit', args=[p2.id]))
        self.assertEqual(resp2.status_code, 302)
        self.assertIn(reverse('fin_invoices'), resp2.url)
        self.assertIn('Multi Invoice Proj', unquote(resp2.url))

    def test_fin_invoice_add_prefills_project_customer_vessel(self):
        proj = Project.objects.create(
            name='Prefill Project',
            client_name='Prefill Client Inc',
            vessel_name='Prefill Vessel VII',
            service_reference='SR-PREFILL-99',
        )
        resp = self.client.get(reverse('fin_invoice_add') + f'?project={proj.id}')
        self.assertEqual(resp.status_code, 200)
        form = resp.context['form']
        self.assertEqual(form.initial['customer'], 'Prefill Client Inc')
        self.assertEqual(form.initial['vessel'], 'Prefill Vessel VII')
        self.assertEqual(form.initial['service_ref'], 'SR-PREFILL-99')
        self.assertEqual(form.initial['project'], proj)
        self.assertTrue(form.initial['invoice_number'].startswith('AMMS-INV-'))

    def test_sidebar_contains_fin_projects_link_and_active_highlight(self):
        resp_dash = self.client.get(reverse('fin_dashboard'))
        self.assertEqual(resp_dash.status_code, 200)
        content_dash = resp_dash.content.decode('utf-8')
        self.assertIn(reverse('fin_projects'), content_dash)
        self.assertIn('fa-project-diagram', content_dash)

        # Active highlighting on fin_projects
        resp_proj = self.client.get(reverse('fin_projects'))
        self.assertEqual(resp_proj.status_code, 200)
        content_proj = resp_proj.content.decode('utf-8')
        self.assertIn('class="active"', content_proj)

        # Active highlighting on fin_project_detail
        p = Project.objects.create(name='Sidebar Proj')
        resp_det = self.client.get(reverse('fin_project_detail', args=[p.id]))
        self.assertEqual(resp_det.status_code, 200)
        content_det = resp_det.content.decode('utf-8')
        self.assertIn('class="active"', content_det)

    def test_query_count_constant_for_3_vs_15_projects_with_invoices(self):
        Project.objects.all().delete()
        Invoice.objects.all().delete()

        # Create 3 projects with invoices
        for i in range(3):
            pr = Project.objects.create(name=f'P3_{i}', client_name=f'Client {i}')
            Invoice.objects.create(
                project=pr,
                invoice_number=f'AMMS-INV-26-{1000 + i}',
                invoice_date=datetime.date(2026, 4, 1),
                amount_excl_vat=Decimal('1000.00'),
            )

        with CaptureQueriesContext(connection) as ctx_3:
            self.client.get(reverse('fin_projects'))
        queries_3 = len(ctx_3.captured_queries)

        # Add 12 more projects (total 15)
        for i in range(3, 15):
            pr = Project.objects.create(name=f'P15_{i}', client_name=f'Client {i}')
            Invoice.objects.create(
                project=pr,
                invoice_number=f'AMMS-INV-26-{1000 + i}',
                invoice_date=datetime.date(2026, 4, 1),
                amount_excl_vat=Decimal('1000.00'),
            )

        with CaptureQueriesContext(connection) as ctx_15:
            self.client.get(reverse('fin_projects'))
        queries_15 = len(ctx_15.captured_queries)

        self.assertEqual(queries_3, queries_15)

    def test_dashboard_top_and_least_profitable_projects_grouping_and_behavior(self):
        Project.objects.all().delete()
        Invoice.objects.all().delete()

        today = timezone.localdate()
        p1 = Project.objects.create(name='Alpha Proj', client_name='Alpha Client')
        inv1 = Invoice.objects.create(
            project=p1,
            invoice_number='INV-ALPHA-01',
            invoice_date=today,
            amount_excl_vat=Decimal('10000.00'),
            credits_applied=Decimal('0.00'),
        )
        JobCost.objects.create(
            invoice=inv1,
            cost_type='TRAVEL',
            amount=Decimal('2000.00'),
            date=today,
        )
        inv2 = Invoice.objects.create(
            project=p1,
            invoice_number='INV-ALPHA-02',
            invoice_date=today,
            amount_excl_vat=Decimal('5000.00'),
            credits_applied=Decimal('0.00'),
        )
        JobCost.objects.create(
            invoice=inv2,
            cost_type='FLIGHT',
            amount=Decimal('1000.00'),
            date=today,
        )

        data = get_dashboard_data(period='this_month')
        self.assertEqual(len(data['top_5_profitable_projects']), 1)
        top1 = data['top_5_profitable_projects'][0]
        self.assertEqual(top1['id'], p1.id)
        self.assertEqual(top1['name'], 'Alpha Proj')
        self.assertEqual(top1['revenue'], Decimal('15000.00'))
        self.assertEqual(top1['total_cost'], Decimal('3000.00'))
        self.assertEqual(top1['profit'], Decimal('12000.00'))
        self.assertEqual(top1['invoice_count'], 2)
        self.assertEqual(top1['url'], reverse('fin_project_detail', args=[p1.id]))

        self.assertEqual(len(data['least_5_profitable_projects']), 0)

        resp = self.client.get(reverse('fin_dashboard') + '?period=this_month')
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')

        self.assertIn(f'href="{reverse("fin_project_detail", args=[p1.id])}"', content)
        self.assertIn('2 invoices', content)
        self.assertIn('3,000.00', content)
        self.assertIn('Not enough projects in this period', content)

    def test_dashboard_seven_projects_top_and_least_lists_do_not_overlap(self):
        Project.objects.all().delete()
        Invoice.objects.all().delete()

        today = timezone.localdate()
        for i in range(1, 8):
            p = Project.objects.create(name=f'Project {i}', client_name=f'Client {i}')
            Invoice.objects.create(
                project=p,
                invoice_number=f'INV-SEVEN-{i:02d}',
                invoice_date=today,
                amount_excl_vat=Decimal(f'{i * 1000}.00'),
            )

        data = get_dashboard_data(period='this_month')
        self.assertEqual(len(data['top_5_profitable_projects']), 5)
        self.assertEqual(len(data['least_5_profitable_projects']), 2)

        top_ids = {p['id'] for p in data['top_5_profitable_projects']}
        least_ids = {p['id'] for p in data['least_5_profitable_projects']}
        self.assertTrue(top_ids.isdisjoint(least_ids))

    def test_dashboard_projectless_invoice_links_to_fin_invoice_edit(self):
        Project.objects.all().delete()
        Invoice.objects.all().delete()

        today = timezone.localdate()
        inv_no_proj = Invoice.objects.create(
            project=None,
            invoice_number='INV-NO-PROJ-99',
            invoice_date=today,
            customer='Direct Client',
            amount_excl_vat=Decimal('4500.00'),
        )

        data = get_dashboard_data(period='this_month')
        self.assertEqual(len(data['top_5_profitable_projects']), 1)
        row = data['top_5_profitable_projects'][0]
        self.assertIsNone(row['id'])
        self.assertEqual(row['name'], 'INV-NO-PROJ-99')
        self.assertEqual(row['url'], reverse('fin_invoice_edit', args=[inv_no_proj.id]))

        resp = self.client.get(reverse('fin_dashboard') + '?period=this_month')
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        self.assertIn(f'href="{reverse("fin_invoice_edit", args=[inv_no_proj.id])}"', content)

    def test_fin_project_detail_service_log_total_cost_and_intcomma(self):
        today = timezone.localdate()
        p = Project.objects.create(name='Service Log Cost Proj', client_name='Client Cost')
        engineer_user = User.objects.create_user('eng_cost_test', 'eng@example.com', 'pass1234')
        engineer = Employee.objects.create(
            user=engineer_user,
            is_employee=True,
            salary=Decimal('4000.00'),
        )
        log = EmployeeServiceLog.objects.create(
            employee=engineer,
            project=p,
            date=today,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(16, 0),
        )
        EmployeeServiceLog.objects.filter(id=log.id).update(
            normal_cost=Decimal('100.00'),
            ot_cost=Decimal('50.00'),
        )
        log.refresh_from_db()
        self.assertEqual(log.line_total_cost, Decimal('150.00'))

        resp = self.client.get(reverse('fin_project_detail', args=[p.id]))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')

        self.assertIn('AED 100.00', content)
        self.assertIn('AED 50.00', content)
        self.assertIn('AED 150.00', content)

    def test_fin_project_detail_service_logs_footer_totals(self):
        today = timezone.localdate()
        p = Project.objects.create(name='Footer Totals Proj', client_name='Client Footer')
        eng_user = User.objects.create_user('eng_foot_test', 'foot@example.com', 'pass1234')
        eng = Employee.objects.create(user=eng_user, is_employee=True, salary=Decimal('4000.00'))

        log1 = EmployeeServiceLog.objects.create(
            employee=eng,
            project=p,
            date=today,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(16, 0),
        )
        log2 = EmployeeServiceLog.objects.create(
            employee=eng,
            project=p,
            date=today - datetime.timedelta(days=1),
            start_time=datetime.time(8, 0),
            end_time=datetime.time(12, 0),
        )
        EmployeeServiceLog.objects.filter(id=log1.id).update(
            total_hours=Decimal('8.00'),
            ot_hours=Decimal('2.00'),
            normal_cost=Decimal('100.00'),
            ot_cost=Decimal('50.00'),
        )
        EmployeeServiceLog.objects.filter(id=log2.id).update(
            total_hours=Decimal('4.00'),
            ot_hours=Decimal('0.00'),
            normal_cost=Decimal('60.00'),
            ot_cost=Decimal('0.00'),
        )

        resp = self.client.get(reverse('fin_project_detail', args=[p.id]))
        self.assertEqual(resp.status_code, 200)

        totals = resp.context['service_logs_totals']
        self.assertEqual(totals['total_hours'], Decimal('12.00'))
        self.assertEqual(totals['ot_hours'], Decimal('2.00'))
        self.assertEqual(totals['normal_cost'], Decimal('160.00'))
        self.assertEqual(totals['ot_cost'], Decimal('50.00'))
        self.assertEqual(totals['total_cost'], Decimal('210.00'))

        content = resp.content.decode('utf-8')
        self.assertIn('<tfoot>', content)
        self.assertIn('12.00', content)
        self.assertIn('2.00', content)
        self.assertIn('AED 160.00', content)
        self.assertIn('AED 50.00', content)
        self.assertIn('AED 210.00', content)

    def test_fin_project_detail_ot_mismatch_warning(self):
        today = timezone.localdate()
        p = Project.objects.create(name='Mismatch Warning Proj', client_name='Client Mismatch')
        eng_user = User.objects.create_user('eng_mismatch_test', 'mis@example.com', 'pass1234')
        eng = Employee.objects.create(user=eng_user, is_employee=True, salary=Decimal('4000.00'))

        log = EmployeeServiceLog.objects.create(
            employee=eng,
            project=p,
            date=today,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(16, 0),
        )
        EmployeeServiceLog.objects.filter(id=log.id).update(
            ot_cost=Decimal('50.00'),
            ot_hours=Decimal('0.00'),
        )
        log.refresh_from_db()
        self.assertTrue(log.ot_mismatch)

        resp = self.client.get(reverse('fin_project_detail', args=[p.id]))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')

        self.assertIn('title="OT cost/hours mismatch — recalculate"', content)
        self.assertIn('fa-exclamation-triangle', content)

    def test_recalculate_service_log_costs_command_dry_run_and_commit(self):
        today = timezone.localdate()
        p = Project.objects.create(name='Recalc Command Proj', client_name='Client Recalc')
        eng_user = User.objects.create_user('eng_recalc_test', 'recalc@example.com', 'pass1234')
        eng = Employee.objects.create(user=eng_user, is_employee=True, salary=Decimal('4160.00'))

        log = EmployeeServiceLog.objects.create(
            employee=eng,
            project=p,
            date=today,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(16, 0),
        )
        # Artificially tamper with ot_cost and ot_hours to create mismatch
        EmployeeServiceLog.objects.filter(id=log.id).update(
            ot_cost=Decimal('999.00'),
            ot_hours=Decimal('0.00'),
        )
        log.refresh_from_db()
        self.assertEqual(log.ot_cost, Decimal('999.00'))
        self.assertEqual(log.ot_hours, Decimal('0.00'))

        out = io.StringIO()
        # Dry-run
        call_command('recalculate_service_log_costs', project=p.id, dry_run=True, stdout=out)
        log.refresh_from_db()
        # In dry run, database changes must be rolled back completely
        self.assertEqual(log.ot_cost, Decimal('999.00'))
        self.assertEqual(log.ot_hours, Decimal('0.00'))

        # Actual run without dry run
        call_command('recalculate_service_log_costs', project=p.id, stdout=out)
        log.refresh_from_db()
        # Should be recomputed by current OT rules
        self.assertNotEqual(log.ot_cost, Decimal('999.00'))


class PayrollAbsenceDeductionTests(TestCase):
    """Unpaid-absence deduction, waiver, OT hours vs OT amount, regenerate_payroll command (Sep 2026: 26 working days)."""

    SEP = datetime.date(2026, 9, 1)

    def setUp(self):
        from Admin.models import Holiday
        self.Holiday = Holiday
        self.tz = timezone.get_current_timezone()
        self.client = Client()
        self.admin = User.objects.create_superuser('pay_admin', 'pa@example.com', 'pass1234')
        self.acc_user = User.objects.create_user('pay_acc', 'pacc@example.com', 'pass1234')
        self.acc_emp = Employee.objects.create(
            user=self.acc_user, is_accountant=True, salary=Decimal('5000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )
        self.project = Project.objects.create(name='Payroll Proj', client_name='C', vessel_name='V')
        self.user = User.objects.create_user('pay_emp', 'pe@example.com', 'pass1234', first_name='Pay', last_name='Emp')
        self.emp = Employee.objects.create(
            user=self.user, is_employee=True, salary=Decimal('2600.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )

    def _vessel_day(self, d, start=8, end=16):
        return EmployeeServiceLog.objects.create(
            employee=self.emp, project=self.project, date=d, vessel_name='V',
            start_time=datetime.time(start, 0), end_time=datetime.time(end, 0),
        )

    def _payroll(self, emp=None):
        return Payroll.objects.get(employee=emp or self.emp, month=self.SEP)

    def test_unpaid_days_and_deduction_sep_2026(self):
        # 2 worked days, 1 approved leave, 1 public holiday -> 26 - 4 = 22 unpaid days
        self._vessel_day(datetime.date(2026, 9, 1))
        self._vessel_day(datetime.date(2026, 9, 2))
        Leave.objects.create(
            user=self.user, leave_type='ANNUAL LEAVE', from_date=datetime.date(2026, 9, 3),
            to_date=datetime.date(2026, 9, 3), reason='x', approval_status='APPROVED',
        )
        self.Holiday.objects.create(date=datetime.date(2026, 9, 4), description='Holiday')

        generate_payroll(self.SEP, self.admin)
        p = self._payroll()
        self.assertEqual(p.basic_salary, Decimal('2600.00'))
        self.assertEqual(p.absent_days_unpaid, Decimal('22'))
        self.assertEqual(p.absence_deduction, Decimal('2200.00'))
        self.assertEqual(p.net_pay, Decimal('400.00'))

    def test_pending_leave_counts_as_unpaid(self):
        Leave.objects.create(
            user=self.user, leave_type='ANNUAL LEAVE', from_date=datetime.date(2026, 9, 1),
            to_date=datetime.date(2026, 9, 2), reason='x', approval_status='PENDING',
        )
        generate_payroll(self.SEP, self.admin)
        self.assertEqual(self._payroll().absent_days_unpaid, Decimal('26'))

    def test_mid_month_joiner_uses_full_daily_rate_and_cap(self):
        u = User.objects.create_user('pay_join', 'pj@example.com', 'pass1234')
        joiner = Employee.objects.create(
            user=u, is_employee=True, salary=Decimal('2600.00'), date_of_join=datetime.date(2026, 9, 15),
        )
        # 2 worked days after joining -> unpaid = 14 working days from 15 Sep - 2 = 12
        for day in (15, 16):
            EmployeeServiceLog.objects.create(
                employee=joiner, project=self.project, date=datetime.date(2026, 9, day), vessel_name='V',
                start_time=datetime.time(8, 0), end_time=datetime.time(16, 0),
            )
        generate_payroll(self.SEP, self.admin)
        p = self._payroll(joiner)
        self.assertEqual(p.basic_salary, Decimal('1386.67'))          # 2600 * 16/30
        self.assertEqual(p.absent_days_unpaid, Decimal('12'))         # days before 15 Sep not counted
        self.assertEqual(p.absence_deduction, Decimal('1200.00'))     # 12 x 100.00 (full salary / 26)

    def test_deduction_capped_at_basic(self):
        u = User.objects.create_user('pay_cap', 'pcap@example.com', 'pass1234')
        joiner = Employee.objects.create(
            user=u, is_employee=True, salary=Decimal('2600.00'), date_of_join=datetime.date(2026, 9, 15),
        )
        generate_payroll(self.SEP, self.admin)
        p = self._payroll(joiner)
        # 14 unpaid x 100 = 1400 > prorated basic 1386.67 -> capped
        self.assertEqual(p.absent_days_unpaid, Decimal('14'))
        self.assertEqual(p.absence_deduction, Decimal('1386.67'))
        self.assertEqual(p.net_pay, Decimal('0.00'))

    def test_waiver_requires_note_and_ignores_deduction(self):
        generate_payroll(self.SEP, self.admin)
        p = self._payroll()
        self.assertGreater(p.absence_deduction, Decimal('0.00'))
        self.client.force_login(self.acc_user)
        url = reverse('fin_payroll_edit', args=[p.id])

        resp = self.client.post(url, {'allowances': '0', 'deductions': '0', 'absence_waived': 'on', 'notes': ''})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('notes', resp.context['form'].errors)
        p.refresh_from_db()
        self.assertFalse(p.absence_waived)

        resp = self.client.post(url, {'allowances': '0', 'deductions': '0', 'absence_waived': 'on', 'notes': 'Missing punch-ins'})
        self.assertEqual(resp.status_code, 302)
        p.refresh_from_db()
        self.assertTrue(p.absence_waived)
        self.assertEqual(p.net_pay, p.gross)

    def test_approved_and_paid_rows_untouched_by_generate_and_command(self):
        generate_payroll(self.SEP, self.admin)
        p = self._payroll()
        Payroll.objects.filter(id=p.id).update(status='APPROVED', absence_deduction=Decimal('1.00'), absent_days_unpaid=Decimal('1'))
        acc_p = self._payroll(self.acc_emp)
        Payroll.objects.filter(id=acc_p.id).update(status='PAID', absence_deduction=Decimal('2.00'))

        generate_payroll(self.SEP, self.admin)
        out = io.StringIO()
        call_command('regenerate_payroll', month='2026-09', stdout=out)
        p.refresh_from_db(); acc_p.refresh_from_db()
        self.assertEqual(p.absence_deduction, Decimal('1.00'))
        self.assertEqual(acc_p.absence_deduction, Decimal('2.00'))
        self.assertIn('skipped (approved)', out.getvalue())
        self.assertIn('skipped (paid)', out.getvalue())

    def test_regenerate_command_dry_run_changes_nothing(self):
        generate_payroll(self.SEP, self.admin)
        p = self._payroll()
        Payroll.objects.filter(id=p.id).update(absence_deduction=Decimal('5.00'), absent_days_unpaid=Decimal('0'))
        out = io.StringIO()
        call_command('regenerate_payroll', month='2026-09', dry_run=True, stdout=out)
        p.refresh_from_db()
        self.assertEqual(p.absence_deduction, Decimal('5.00'))
        self.assertIn('Dry run', out.getvalue())
        self.assertIn('absence ded. 5.00 -> 2600.00', out.getvalue())

        call_command('regenerate_payroll', month='2026-09', stdout=io.StringIO())
        p.refresh_from_db()
        self.assertEqual(p.absence_deduction, Decimal('2600.00'))

    def test_office_ot_after_vessel_gives_ot_hours_matching_amount(self):
        # vessel 06-12 + office 12-18 -> 2h OT, all office OT (the case that showed OT Hours 0)
        d = datetime.date(2026, 9, 7)
        self._vessel_day(d, 6, 12)
        Attendance.objects.create(
            employee=self.emp,
            login_time=timezone.make_aware(datetime.datetime.combine(d, datetime.time(12, 0)), self.tz),
            log_out_time=timezone.make_aware(datetime.datetime.combine(d, datetime.time(18, 0)), self.tz),
            location='BDO', status='APPROVED', total_hours_of_work=Decimal('6.00'),
        )
        generate_payroll(self.SEP, self.admin)
        from finance.services import get_payroll_list_with_metrics
        payrolls, totals, _ = get_payroll_list_with_metrics(self.SEP)
        p = next(x for x in payrolls if x.employee_id == self.emp.id)
        self.assertEqual(p.ot_hours, Decimal('2.00'))
        self.assertEqual(p.ot_amount, Decimal('31.25'))  # 2 x (2600/208=12.5) x 1.25
        self.assertFalse(p.ot_mismatch)

    def test_page_and_excel_show_new_columns(self):
        generate_payroll(self.SEP, self.admin)
        self.client.force_login(self.acc_user)
        resp = self.client.get(reverse('fin_payroll') + '?month=2026-09')
        self.assertContains(resp, 'Unpaid Days')
        self.assertContains(resp, 'Absence Ded. (AED)')

        resp = self.client.get(reverse('fin_payroll_export_excel') + '?month=2026-09')
        self.assertEqual(resp.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        ws = wb.active
        headers = [c.value for c in ws[1]]
        self.assertIn('Unpaid Days', headers)
        self.assertIn('Absence Ded. (AED)', headers)
        ded_col = headers.index('Absence Ded. (AED)') + 1
        net_col = headers.index('Net Pay (AED)') + 1
        total_row = ws.max_row
        self.assertEqual(ws.cell(row=total_row, column=1).value, 'TOTAL')
        expected_ded = float(sum(p.effective_absence_deduction for p in Payroll.objects.filter(month=self.SEP)))
        expected_net = float(sum(p.net_pay for p in Payroll.objects.filter(month=self.SEP)))
        self.assertAlmostEqual(ws.cell(row=total_row, column=ded_col).value, expected_ded, places=2)
        self.assertAlmostEqual(ws.cell(row=total_row, column=net_col).value, expected_net, places=2)

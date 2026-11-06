import datetime
from decimal import Decimal
import io
import openpyxl

from django.test import TestCase, Client, override_settings
from django.contrib.auth.models import User
from django.utils import timezone
from django.urls import reverse

from Admin.models import Employee, Project
from finance.models import Invoice, JobCost, SpareSale, Expense, ExpenseCategory, ExchangeRate
from finance.currency import BASE_CURRENCY, get_rate, to_aed
from finance.services import (
    invoices_excel_response,
    receivables_excel_response,
    spares_excel_response,
    expenses_excel_response,
    jobs_pnl_excel_response,
    pnl_excel_response,
    projects_profitability_excel_response,
    employee_cost_excel_response,
    get_invoice_costing_data,
    get_receivables_data,
    get_spares_accounting_data,
    get_jobs_pnl_data,
    import_finance_sheets_data,
)


class MultiCurrencyPhase5FixesTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.accountant_user = User.objects.create_user('acc_io_user', 'acc_io@example.com', 'pass1234')
        self.accountant_emp = Employee.objects.create(
            user=self.accountant_user,
            is_accountant=True,
            salary=Decimal('5000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )
        self.client.force_login(self.accountant_user)
        self.today = timezone.localdate()

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_fix_a1_spares_only_mismatch_uses_amount_excl_vat_aed(self):
        """
        SPARES_ONLY invoice with a credit and spares equal to the pre-credit amount
        (amount_excl_vat_aed) must NOT produce an 'Invoice spares mismatch' alert.
        """
        ExchangeRate.objects.create(
            currency='USD',
            rate_to_aed=Decimal('3.672500'),
            effective_from=datetime.date(2026, 1, 1),
            is_active=True,
        )

        inv = Invoice.objects.create(
            invoice_number='AMMS-INV-26-9001',
            invoice_date=self.today,
            invoice_type='SPARES_ONLY',
            currency='USD',
            exchange_rate=Decimal('3.672500'),
            amount_excl_vat=Decimal('1000.00'),   # AED 3,672.50
            credits_applied=Decimal('200.00'),    # AED 734.50 -> collection_value_aed = 2,938.00
        )
        self.assertEqual(inv.amount_excl_vat_aed, Decimal('3672.50'))
        self.assertEqual(inv.collection_value_aed, Decimal('2938.00'))

        # Spare whose selling price matches the PRE-CREDIT invoice amount (AED 3,672.50)
        sp = SpareSale.objects.create(
            service_ref='SR-SP-A1',
            invoice=inv,
            source='SPARES_ONLY',
            purchase_currency='AED',
            purchase_cost=Decimal('2000.00'),
            selling_currency='USD',
            selling_rate=Decimal('3.672500'),
            selling_price=Decimal('1000.00'),  # AED 3,672.50
        )
        self.assertEqual(sp.selling_price_aed, Decimal('3672.50'))

        # 1. Model property check
        self.assertNotIn("Invoice spares mismatch", sp.alerts)

        # 2. Service check (get_spares_accounting_data)
        spares, summary = get_spares_accounting_data()
        sp_found = next(s for s in spares if s.id == sp.id)
        self.assertNotIn("Invoice spares mismatch", sp_found.alerts)

        # 3. If selling price does NOT match pre-credit amount, mismatch alert IS shown
        sp.selling_price = Decimal('800.00')  # AED 2,938.00 (matches collection value, but not pre-credit)
        sp.save()
        self.assertIn("Invoice spares mismatch", sp.alerts)

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_fix_a2_receivables_record_payment_stores_aed_on_project(self):
        """
        Recording payment from Receivables for a USD invoice copies invoice.amount_received_aed
        into invoice.project.amount_received, not the foreign currency amount.
        """
        ExchangeRate.objects.create(
            currency='USD',
            rate_to_aed=Decimal('3.672500'),
            effective_from=datetime.date(2026, 1, 1),
            is_active=True,
        )

        p = Project.objects.create(
            name='USD Vessel Job',
            client_name='Overseas Client',
            service_reference='SR-USD-PAY',
            amount_received=Decimal('0.00'),
        )

        inv = Invoice.objects.create(
            invoice_number='AMMS-INV-26-9002',
            invoice_date=self.today,
            project=p,
            currency='USD',
            exchange_rate=Decimal('3.672500'),
            amount_excl_vat=Decimal('2000.00'),
            credits_applied=Decimal('0.00'),
            amount_received=Decimal('0.00'),
        )

        # Record payment of USD 1,000 via POST to fin_receivables_record_payment
        url = reverse('fin_receivables_record_payment', args=[inv.id])
        resp = self.client.post(url, {
            'amount': '1000.00',
            'payment_date': self.today.strftime('%Y-%m-%d'),
        })
        self.assertEqual(resp.status_code, 302)

        inv.refresh_from_db()
        p.refresh_from_db()

        self.assertEqual(inv.amount_received, Decimal('1000.00'))          # USD 1,000.00
        self.assertEqual(inv.amount_received_aed, Decimal('3672.50'))      # AED 3,672.50
        # Project amount_received MUST be in AED:
        self.assertEqual(p.amount_received, Decimal('3672.50'))
        self.assertEqual(p.payment_status, 'PARTIAL')

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_fix_a3_spares_page_sort_uses_aed_fields(self):
        """
        Sorting by purchase_cost, selling_price, or profit on Spares page sorts by AED fields.
        """
        # Spare 1: Purchase USD 100 @ 3.6725 = AED 367.25; Selling AED 1,000 = AED 1,000; Profit AED 632.75
        s1 = SpareSale.objects.create(
            service_ref='SR-SORT-1',
            purchase_currency='USD',
            purchase_rate=Decimal('3.672500'),
            purchase_cost=Decimal('100.00'),
            selling_currency='AED',
            selling_rate=Decimal('1.000000'),
            selling_price=Decimal('1000.00'),
        )
        # Spare 2: Purchase AED 500 @ 1 = AED 500.00; Selling USD 200 @ 3.6725 = AED 734.50; Profit AED 234.50
        s2 = SpareSale.objects.create(
            service_ref='SR-SORT-2',
            purchase_currency='AED',
            purchase_rate=Decimal('1.000000'),
            purchase_cost=Decimal('500.00'),
            selling_currency='USD',
            selling_rate=Decimal('3.672500'),
            selling_price=Decimal('200.00'),
        )

        # In original currency: s1.purchase_cost (100) < s2.purchase_cost (500)
        # In AED: s1.purchase_cost_aed (367.25) < s2.purchase_cost_aed (500.00)
        spares_asc, _ = get_spares_accounting_data(sort_by='purchase_cost')
        ids_asc = [s.id for s in spares_asc if s.id in (s1.id, s2.id)]
        self.assertEqual(ids_asc, [s1.id, s2.id])

        # Test reverse profit: s1 profit AED 632.75 > s2 profit AED 234.50
        spares_prof_desc, _ = get_spares_accounting_data(sort_by='-profit')
        prof_ids = [s.id for s in spares_prof_desc if s.id in (s1.id, s2.id)]
        self.assertEqual(prof_ids, [s1.id, s2.id])


class MultiCurrencyExcelExportTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_invoices_excel_response_columns_and_total(self):
        """
        Invoices export has 19 columns with Currency, Rate, row values in both original and AED,
        and bottom TOTAL row in AED labelled 'TOTAL (AED)'.
        """
        p = Project.objects.create(name='Proj Export', client_name='Client A')
        inv = Invoice.objects.create(
            invoice_number='AMMS-INV-26-9101',
            invoice_date=self.today,
            project=p,
            currency='USD',
            exchange_rate=Decimal('3.672500'),
            amount_excl_vat=Decimal('1000.00'),
            credits_applied=Decimal('100.00'),
            amount_received=Decimal('400.00'),
        )

        rows, totals = get_invoice_costing_data()
        resp = invoices_excel_response(rows, totals)
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        ws = wb.active

        headers = [cell.value for cell in ws[1]]
        expected_headers = [
            "Sl", "Invoice #", "Invoice Date", "Due Date", "Project / Customer", "Vessel", "Type",
            "Currency", "Rate", "Amount Excl. VAT", "VAT Amount", "Credits Applied", "Received",
            "Amount Excl. VAT (AED)", "Collection Value (AED)", "Received (AED)", "Outstanding (AED)", "Status", "Days Overdue"
        ]
        self.assertEqual(headers, expected_headers)

        # Verify data row
        row2 = [cell.value for cell in ws[2]]
        self.assertEqual(row2[0], 1)                     # Sl
        self.assertEqual(row2[1], 'AMMS-INV-26-9101')    # Invoice #
        self.assertEqual(row2[7], 'USD')                 # Currency
        self.assertAlmostEqual(row2[8], 3.6725, places=4)# Rate
        self.assertEqual(row2[9], 1000.0)                # Amount Excl. VAT (USD)
        self.assertEqual(row2[11], 100.0)                # Credits Applied (USD)
        self.assertEqual(row2[12], 400.0)                # Received (USD)
        self.assertAlmostEqual(row2[13], 3672.50, places=2) # Amount Excl. VAT (AED)
        self.assertAlmostEqual(row2[14], 3305.25, places=2) # Collection Value (AED)
        self.assertAlmostEqual(row2[15], 1469.00, places=2) # Received (AED)
        self.assertAlmostEqual(row2[16], 1836.25, places=2) # Outstanding (AED)

        # Verify bottom total row
        total_row = [cell.value for cell in ws[ws.max_row]]
        self.assertEqual(total_row[0], "TOTAL (AED)")

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_receivables_excel_response_columns_and_total(self):
        """
        Receivables export has 16 columns with Currency, Rate, and AED columns.
        """
        p = Project.objects.create(name='Rec Proj', client_name='Client Rec')
        inv = Invoice.objects.create(
            invoice_number='AMMS-INV-26-9102',
            invoice_date=self.today,
            project=p,
            currency='USD',
            exchange_rate=Decimal('3.672500'),
            amount_excl_vat=Decimal('2000.00'),
            credits_applied=Decimal('0.00'),
            amount_received=Decimal('500.00'),
        )

        receivables, summary = get_receivables_data()
        resp = receivables_excel_response(receivables, summary)
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        ws = wb.active

        headers = [cell.value for cell in ws[1]]
        expected_headers = [
            "Sl", "Invoice #", "Project / Customer", "Invoice Date", "Due Date", "Days Overdue", "Aging Bucket",
            "Currency", "Rate", "Invoice Amount", "Received", "Outstanding",
            "Invoice Amount (AED)", "Received (AED)", "Outstanding (AED)", "Status"
        ]
        self.assertEqual(headers, expected_headers)

        total_row = [cell.value for cell in ws[ws.max_row]]
        self.assertEqual(total_row[0], "TOTAL (AED)")

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_spares_excel_response_columns_and_total(self):
        """
        Spares export has 18 columns with purchase and selling currency/rate/amounts.
        """
        sp = SpareSale.objects.create(
            service_ref='SR-SP-EXP',
            notes='Test Spare Description',
            vessel_customer='Vessel Exp',
            purchase_currency='EUR',
            purchase_rate=Decimal('4.000000'),
            purchase_cost=Decimal('500.00'),     # AED 2,000.00
            selling_currency='USD',
            selling_rate=Decimal('3.672500'),
            selling_price=Decimal('800.00'),     # AED 2,938.00
        )

        spares, summary = get_spares_accounting_data()
        resp = spares_excel_response(spares, summary)
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        ws = wb.active

        headers = [cell.value for cell in ws[1]]
        expected_headers = [
            "Sl", "Service Ref / PO", "Description", "Vessel / Customer", "Invoice #", "Source", "Status",
            "Purchase Curr", "Purchase Rate", "Purchase Cost", "Purchase Cost (AED)",
            "Selling Curr", "Selling Rate", "Selling Price", "Selling Price (AED)",
            "Profit (AED)", "Markup %", "Alerts"
        ]
        self.assertEqual(headers, expected_headers)

        row2 = [cell.value for cell in ws[2]]
        self.assertEqual(row2[1], 'SR-SP-EXP')
        self.assertEqual(row2[7], 'EUR')
        self.assertEqual(row2[11], 'USD')
        self.assertEqual(row2[10], 2000.0)      # Purchase Cost (AED)
        self.assertAlmostEqual(row2[14], 2938.0, places=2) # Selling Price (AED)

        total_row = [cell.value for cell in ws[ws.max_row]]
        self.assertEqual(total_row[0], "TOTAL (AED)")

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_expenses_excel_response_columns_and_total(self):
        """
        Expenses export has 15 columns with Currency, Rate, Amount (AED), VAT (AED), Total Incl. VAT (AED).
        """
        cat, _ = ExpenseCategory.objects.get_or_create(name='Office Supplies Test', defaults={'kind': 'OVERHEAD'})
        exp = Expense.objects.create(
            date=self.today,
            category=cat,
            description='Test office purchase',
            vendor='Vendor X',
            reference_no='REF-123',
            currency='USD',
            exchange_rate=Decimal('3.672500'),
            amount=Decimal('100.00'),       # AED 367.25
            vat_amount=Decimal('5.00'),      # AED 18.36
        )

        resp = expenses_excel_response([exp], {'amount_aed': exp.amount_aed, 'vat_amount_aed': exp.vat_amount_aed})
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        ws = wb.active

        headers = [cell.value for cell in ws[1]]
        expected_headers = [
            "Sl", "Date", "Category", "Description", "Vendor", "Payment Method", "Paid By", "Reference #",
            "Currency", "Rate", "Amount", "VAT Amount", "Amount (AED)", "VAT Amount (AED)", "Total Incl. VAT (AED)"
        ]
        self.assertEqual(headers, expected_headers)

        row2 = [cell.value for cell in ws[2]]
        self.assertEqual(row2[8], 'USD')
        self.assertAlmostEqual(row2[9], 3.6725, places=4)
        self.assertEqual(row2[10], 100.0)
        self.assertEqual(row2[11], 5.0)
        self.assertAlmostEqual(row2[12], 367.25, places=2)
        self.assertAlmostEqual(row2[13], 18.36, places=2)
        self.assertAlmostEqual(row2[14], 385.61, places=2)

        total_row = [cell.value for cell in ws[ws.max_row]]
        self.assertEqual(total_row[0], "TOTAL (AED)")

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_jobs_pnl_excel_response_columns_and_total(self):
        """
        Jobs P&L export has 20 columns including Inv Currency, Inv Rate, Net Invoice, Net Invoice (AED).
        """
        p = Project.objects.create(name='Jobs Proj', client_name='Client Jobs')
        inv = Invoice.objects.create(
            invoice_number='AMMS-INV-26-9103',
            invoice_date=self.today,
            project=p,
            currency='USD',
            exchange_rate=Decimal('3.672500'),
            amount_excl_vat=Decimal('3000.00'),
        )

        rows, totals = get_jobs_pnl_data()
        resp = jobs_pnl_excel_response(rows, totals)
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        ws = wb.active

        headers = [cell.value for cell in ws[1]]
        expected_headers = [
            "Sl", "Service Ref", "Vessel", "Invoice No", "Inv Currency", "Inv Rate", "Net Invoice", "Net Invoice (AED)",
            "Engineer (AED)", "Spare Cost (AED)", "Travel Port", "Travel Cost (AED)", "Flight (AED)", "Misc Cost (AED)",
            "Other (AED)", "Total Cost (AED)", "Profit / (Loss) (AED)", "Margin %", "Month", "Notes"
        ]
        self.assertEqual(headers, expected_headers)

        total_row = [cell.value for cell in ws[ws.max_row]]
        self.assertEqual(total_row[0], "TOTAL (AED)")

    def test_aed_only_reports_total_row_labels(self):
        """
        P&L statement, Project Profitability, and Employee Cost export total rows say 'TOTAL (AED)'.
        """
        # P&L
        resp_pnl = pnl_excel_response([], {})
        wb_pnl = openpyxl.load_workbook(io.BytesIO(resp_pnl.content))
        ws_pnl = wb_pnl.active
        self.assertEqual(ws_pnl.cell(row=ws_pnl.max_row, column=1).value, "TOTAL (AED)")

        # Project Profitability
        resp_proj = projects_profitability_excel_response([], {})
        wb_proj = openpyxl.load_workbook(io.BytesIO(resp_proj.content))
        ws_proj = wb_proj.active
        self.assertEqual(ws_proj.cell(row=ws_proj.max_row, column=1).value, "TOTAL (AED)")

        # Employee Cost
        resp_emp = employee_cost_excel_response([], {})
        wb_emp = openpyxl.load_workbook(io.BytesIO(resp_emp.content))
        ws_emp = wb_emp.active
        self.assertEqual(ws_emp.cell(row=ws_emp.max_row, column=1).value, "TOTAL (AED)")


class MultiCurrencyExcelImportTests(TestCase):
    def setUp(self):
        self.accountant_user = User.objects.create_user('acc_imp_user', 'acc_imp@example.com', 'pass1234')
        self.today = timezone.localdate()

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_import_old_sheet_without_currency_columns_defaults_to_aed(self):
        """
        Importing an old sheet without Currency / Rate columns defaults to AED, rate 1.0.
        """
        csv_data = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Amount Without Tax,Credits Applied\n"
            "OPEN,2026-06-01,SR-OLD-1,Vessel Old,AMMS-INV-26-9201,5000.00,0.00\n"
        )
        res = import_finance_sheets_data(
            invoices_file=io.StringIO(csv_data),
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res['created_invoices'], 1)
        inv = Invoice.objects.get(invoice_number='AMMS-INV-26-9201')
        self.assertEqual(inv.currency, 'AED')
        self.assertEqual(inv.exchange_rate, Decimal('1.000000'))
        self.assertEqual(inv.amount_excl_vat_aed, Decimal('5000.00'))

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_import_usd_with_rate_and_zero_vat_no_warning(self):
        """
        Importing a USD invoice with explicit Rate and 0 VAT (export of services) succeeds
        with no VAT warning and correctly calculates amount_excl_vat_aed.
        """
        csv_data = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Currency,Rate,Amount Without Tax,VAT,Credits Applied\n"
            "OPEN,2026-06-01,SR-USD-1,Vessel USD,AMMS-INV-26-9202,USD,3.6725,1000.00,0.00,0.00\n"
        )
        res = import_finance_sheets_data(
            invoices_file=io.StringIO(csv_data),
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res['created_invoices'], 1)
        self.assertFalse(any("VAT discrepancy" in w for w in res['warnings']))

        inv = Invoice.objects.get(invoice_number='AMMS-INV-26-9202')
        self.assertEqual(inv.currency, 'USD')
        self.assertEqual(inv.exchange_rate, Decimal('3.672500'))
        self.assertEqual(inv.amount_excl_vat, Decimal('1000.00'))
        self.assertEqual(inv.amount_excl_vat_aed, Decimal('3672.50'))

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_import_usd_without_rate_auto_lookups_table_with_warning(self):
        """
        Importing a USD invoice without Rate auto-lookups active ExchangeRate from table,
        records a warning, and saves successfully.
        """
        ExchangeRate.objects.create(
            currency='USD',
            rate_to_aed=Decimal('3.672500'),
            effective_from=datetime.date(2026, 1, 1),
            is_active=True,
        )

        csv_data = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Currency,Amount Without Tax,Credits Applied\n"
            "OPEN,2026-06-01,SR-USD-AUTO,Vessel Auto,AMMS-INV-26-9203,USD,2000.00,0.00\n"
        )
        res = import_finance_sheets_data(
            invoices_file=io.StringIO(csv_data),
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res['created_invoices'], 1)
        self.assertTrue(any("USD rate 3.6725 taken from Exchange Rates for row 1" in w for w in res['warnings']))

        inv = Invoice.objects.get(invoice_number='AMMS-INV-26-9203')
        self.assertEqual(inv.currency, 'USD')
        self.assertEqual(inv.exchange_rate, Decimal('3.672500'))
        self.assertEqual(inv.amount_excl_vat_aed, Decimal('7345.00'))

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_import_non_aed_without_rate_and_missing_table_rate_errors(self):
        """
        Importing a non-AED invoice with no rate in file and no rate in ExchangeRate table
        records a row error and skips the row.
        """
        csv_data = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Currency,Amount Without Tax,Credits Applied\n"
            "OPEN,2026-06-01,SR-EUR-ERR,Vessel Eur,AMMS-INV-26-9204,EUR,1500.00,0.00\n"
        )
        res = import_finance_sheets_data(
            invoices_file=io.StringIO(csv_data),
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res['skipped'], 1)
        self.assertTrue(any("No exchange rate found for EUR" in err for err in res['errors']))
        self.assertFalse(Invoice.objects.filter(invoice_number='AMMS-INV-26-9204').exists())

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_import_unknown_currency_code_errors(self):
        """
        Importing a row with unknown currency code (e.g. 'GBP') records a row error and skips row.
        """
        csv_data = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Currency,Rate,Amount Without Tax,Credits Applied\n"
            "OPEN,2026-06-01,SR-GBP-ERR,Vessel Gbp,AMMS-INV-26-9205,GBP,4.5,1000.00,0.00\n"
        )
        res = import_finance_sheets_data(
            invoices_file=io.StringIO(csv_data),
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res['skipped'], 1)
        self.assertTrue(any("Unknown currency code 'GBP'" in err for err in res['errors']))

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_import_rate_less_than_equal_zero_errors(self):
        """
        Importing a row with rate <= 0 records a row error and skips row.
        """
        csv_data = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Currency,Rate,Amount Without Tax,Credits Applied\n"
            "OPEN,2026-06-01,SR-ZERO-ERR,Vessel Zero,AMMS-INV-26-9206,USD,-1.5,1000.00,0.00\n"
        )
        res = import_finance_sheets_data(
            invoices_file=io.StringIO(csv_data),
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res['skipped'], 1)
        self.assertTrue(any("Rate must be greater than zero" in err for err in res['errors']))

    @override_settings(FINANCE_MULTI_CURRENCY=False)
    def test_import_when_switch_off_rejects_non_aed_rows(self):
        """
        When FINANCE_MULTI_CURRENCY = False, any non-AED row is rejected with
        'Multi-currency is not enabled'.
        """
        csv_data = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Currency,Rate,Amount Without Tax,Credits Applied\n"
            "OPEN,2026-06-01,SR-SWITCH-ERR,Vessel Switch,AMMS-INV-26-9207,USD,3.6725,1000.00,0.00\n"
        )
        res = import_finance_sheets_data(
            invoices_file=io.StringIO(csv_data),
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res['skipped'], 1)
        self.assertTrue(any("Multi-currency is not enabled" in err for err in res['errors']))
        self.assertFalse(Invoice.objects.filter(invoice_number='AMMS-INV-26-9207').exists())

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_dry_run_preview_contains_currency_rate_and_aed_amounts(self):
        """
        Dry run preview includes Currency, Rate, and AED amounts for invoices and spares.
        """
        inv_csv = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Currency,Rate,Amount Without Tax,Credits Applied\n"
            "OPEN,2026-06-01,SR-PREV-1,Vessel Prev,AMMS-INV-26-9208,USD,3.6725,1000.00,0.00\n"
        )
        sp_csv = (
            "Service Ref,Vessel / Customer,Purchase Currency,Purchase Rate,Purchase Cost,Selling Currency,Selling Rate,Actual Selling Price,Invoice No\n"
            "SR-PREV-SP,Vessel Prev SP,EUR,4.0,200.00,USD,3.6725,300.00,\n"
        )
        res = import_finance_sheets_data(
            invoices_file=io.StringIO(inv_csv),
            spares_file=io.StringIO(sp_csv),
            dry_run=True,
            user=self.accountant_user,
        )
        self.assertFalse(Invoice.objects.filter(invoice_number='AMMS-INV-26-9208').exists())
        self.assertEqual(len(res['preview']['invoices']), 1)
        p_inv = res['preview']['invoices'][0]
        self.assertEqual(p_inv['currency'], 'USD')
        self.assertEqual(p_inv['exchange_rate'], Decimal('3.6725'))
        self.assertEqual(p_inv['amount_aed'], Decimal('3672.50'))

        self.assertEqual(len(res['preview']['spares']), 1)
        p_sp = res['preview']['spares'][0]
        self.assertEqual(p_sp['purchase_currency'], 'EUR')
        self.assertEqual(p_sp['purchase_rate'], Decimal('4.0'))
        self.assertEqual(p_sp['purchase_cost_aed'], Decimal('800.00'))
        self.assertEqual(p_sp['selling_currency'], 'USD')
        self.assertEqual(p_sp['selling_rate'], Decimal('3.6725'))
        self.assertEqual(p_sp['selling_price_aed'], Decimal('1101.75'))

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_reimport_existing_usd_invoice_with_blank_currency_and_rate_preserves_currency_and_rate(self):
        """
        Re-importing an existing USD invoice from an old sheet with no Currency / Rate columns
        retains USD currency, existing rate, and preserves/updates AED calculations.
        Preview in dry run also reflects the preserved currency and rate.
        """
        inv = Invoice.objects.create(
            invoice_number='AMMS-INV-26-9301',
            invoice_date=self.today,
            currency='USD',
            exchange_rate=Decimal('3.672500'),
            amount_excl_vat=Decimal('1000.00'),
            credits_applied=Decimal('0.00'),
        )
        self.assertEqual(inv.amount_excl_vat_aed, Decimal('3672.50'))

        # Old-format CSV with no Currency / Rate columns
        csv_data = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Amount Without Tax,Credits Applied\n"
            f"OPEN,{self.today.isoformat()},SR-REIMP-1,Vessel Reimp,AMMS-INV-26-9301,1200.00,0.00\n"
        )

        # 1. Test preview (dry run)
        res_preview = import_finance_sheets_data(
            invoices_file=io.StringIO(csv_data),
            dry_run=True,
            user=self.accountant_user,
        )
        self.assertEqual(len(res_preview['preview']['invoices']), 1)
        p_inv = res_preview['preview']['invoices'][0]
        self.assertEqual(p_inv['action'], 'Updated')
        self.assertEqual(p_inv['currency'], 'USD')
        self.assertEqual(p_inv['exchange_rate'], Decimal('3.672500'))
        self.assertEqual(p_inv['amount_excl_vat'], Decimal('1200.00'))
        self.assertEqual(p_inv['amount_aed'], Decimal('4407.00'))

        # 2. Test actual save
        res = import_finance_sheets_data(
            invoices_file=io.StringIO(csv_data),
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res['updated_invoices'], 1)
        inv.refresh_from_db()
        self.assertEqual(inv.currency, 'USD')
        self.assertEqual(inv.exchange_rate, Decimal('3.672500'))
        self.assertEqual(inv.amount_excl_vat, Decimal('1200.00'))
        self.assertEqual(inv.amount_excl_vat_aed, Decimal('4407.00'))

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_reimport_existing_spare_with_blank_currency_and_rate_preserves_currencies_and_rates(self):
        """
        Re-importing an existing spare from an old sheet with blank purchase and selling
        currency / rate preserves its existing currencies, rates, and AED amounts.
        """
        inv = Invoice.objects.create(
            invoice_number='AMMS-INV-26-9302',
            invoice_date=self.today,
            currency='USD',
            exchange_rate=Decimal('3.672500'),
            amount_excl_vat=Decimal('2000.00'),
        )
        sp = SpareSale.objects.create(
            invoice=inv,
            service_ref='SR-SP-REIMP',
            vessel_customer='Vessel SP Reimp',
            purchase_currency='EUR',
            purchase_rate=Decimal('4.000000'),
            purchase_cost=Decimal('200.00'),     # AED 800.00
            selling_currency='USD',
            selling_rate=Decimal('3.672500'),
            selling_price=Decimal('500.00'),     # AED 1,836.25
        )

        sp_csv = (
            "Service Ref,Vessel / Customer,Purchase Cost,Actual Selling Price,Invoice No\n"
            "SR-SP-REIMP,Vessel SP Reimp,250.00,550.00,AMMS-INV-26-9302\n"
        )
        res = import_finance_sheets_data(
            spares_file=io.StringIO(sp_csv),
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res['updated_spares'], 1)
        sp.refresh_from_db()
        self.assertEqual(sp.purchase_currency, 'EUR')
        self.assertEqual(sp.purchase_rate, Decimal('4.000000'))
        self.assertEqual(sp.purchase_cost, Decimal('250.00'))
        self.assertEqual(sp.purchase_cost_aed, Decimal('1000.00')) # 250 * 4.0
        self.assertEqual(sp.selling_currency, 'USD')
        self.assertEqual(sp.selling_rate, Decimal('3.672500'))
        self.assertEqual(sp.selling_price, Decimal('550.00'))
        self.assertEqual(sp.selling_price_aed, Decimal('2019.88')) # 550 * 3.6725 = 2019.875 -> 2019.88

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_reimport_existing_invoice_currency_given_rate_blank_same_currency_keeps_existing_rate(self):
        """
        Currency given but Rate blank on an existing record with the SAME currency
        keeps its existing rate without looking up the ExchangeRate table.
        """
        ExchangeRate.objects.create(
            currency='USD',
            rate_to_aed=Decimal('3.673000'), # Different rate in table
            effective_from=datetime.date(2026, 1, 1),
            is_active=True,
        )
        inv = Invoice.objects.create(
            invoice_number='AMMS-INV-26-9303',
            invoice_date=self.today,
            currency='USD',
            exchange_rate=Decimal('3.672500'), # Existing rate on invoice
            amount_excl_vat=Decimal('1000.00'),
        )

        csv_data = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Currency,Rate,Amount Without Tax,Credits Applied\n"
            f"OPEN,{self.today.isoformat()},SR-SAME-CURR,Vessel Same,AMMS-INV-26-9303,USD,,1000.00,0.00\n"
        )
        res = import_finance_sheets_data(
            invoices_file=io.StringIO(csv_data),
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res['updated_invoices'], 1)
        # Must NOT have logged the ExchangeRate lookup warning because existing rate was retained
        self.assertFalse(any("taken from Exchange Rates" in w for w in res['warnings']))
        inv.refresh_from_db()
        self.assertEqual(inv.currency, 'USD')
        self.assertEqual(inv.exchange_rate, Decimal('3.672500'))

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_reimport_existing_invoice_currency_changed_applies_and_logs_warning(self):
        """
        If a sheet row's currency differs from the existing record's currency,
        apply it and log warning: 'Row N: AMMS-INV-YY-NNNN currency changed OLD -> NEW'.
        """
        inv = Invoice.objects.create(
            invoice_number='AMMS-INV-26-0001',
            invoice_date=self.today,
            currency='USD',
            exchange_rate=Decimal('3.672500'),
            amount_excl_vat=Decimal('1000.00'),
        )

        csv_data = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Currency,Rate,Amount Without Tax,Credits Applied\n"
            f"OPEN,{self.today.isoformat()},SR-CHG-1,Vessel Chg,AMMS-INV-26-0001,AED,,3672.50,0.00\n"
        )
        res = import_finance_sheets_data(
            invoices_file=io.StringIO(csv_data),
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res['updated_invoices'], 1)
        self.assertTrue(any("Row 1: AMMS-INV-26-0001 currency changed USD -> AED" in w for w in res['warnings']))
        inv.refresh_from_db()
        self.assertEqual(inv.currency, 'AED')
        self.assertEqual(inv.exchange_rate, Decimal('1.000000'))
        self.assertEqual(inv.amount_excl_vat, Decimal('3672.50'))
        self.assertEqual(inv.amount_excl_vat_aed, Decimal('3672.50'))

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_reimport_existing_invoice_currency_changed_to_new_foreign_currency_looks_up_table_and_warns(self):
        """
        When currency changes to a different foreign currency with blank rate,
        it performs table lookup, logs currency changed warning AND table rate warning.
        """
        ExchangeRate.objects.create(
            currency='EUR',
            rate_to_aed=Decimal('4.000000'),
            effective_from=datetime.date(2026, 1, 1),
            is_active=True,
        )
        inv = Invoice.objects.create(
            invoice_number='AMMS-INV-26-0002',
            invoice_date=self.today,
            currency='USD',
            exchange_rate=Decimal('3.672500'),
            amount_excl_vat=Decimal('1000.00'),
        )

        csv_data = (
            "Status,Invoice Date,Project / Service Ref,VESSEL,Invoice No,Currency,Rate,Amount Without Tax,Credits Applied\n"
            f"OPEN,{self.today.isoformat()},SR-CHG-2,Vessel Chg,AMMS-INV-26-0002,EUR,,800.00,0.00\n"
        )
        res = import_finance_sheets_data(
            invoices_file=io.StringIO(csv_data),
            dry_run=False,
            user=self.accountant_user,
        )
        self.assertEqual(res['updated_invoices'], 1)
        self.assertTrue(any("Row 1: AMMS-INV-26-0002 currency changed USD -> EUR" in w for w in res['warnings']))
        self.assertTrue(any("EUR rate 4 taken from Exchange Rates for row 1" in w for w in res['warnings']))
        inv.refresh_from_db()
        self.assertEqual(inv.currency, 'EUR')
        self.assertEqual(inv.exchange_rate, Decimal('4.000000'))
        self.assertEqual(inv.amount_excl_vat_aed, Decimal('3200.00'))

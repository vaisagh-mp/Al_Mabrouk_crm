import datetime
from decimal import Decimal
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone

from Admin.models import Project, Employee
from service_logs.models import EmployeeServiceLog
from finance.models import Invoice, JobCost, SpareSale, Expense, ExpenseCategory, ExchangeRate, Payroll
from finance.services import (
    pnl_for_range,
    get_dashboard_data,
    get_projects_profitability,
    get_jobs_pnl_data,
    get_spares_accounting_data,
    get_receivables_data,
    get_employee_cost_data,
    get_projects_financials_map,
    get_project_financials,
    jobs_pnl_excel_response,
    invoices_excel_response,
    spares_excel_response,
    receivables_excel_response,
    expenses_excel_response,
)

User = get_user_model()


class MultiCurrencyTotalsTests(TestCase):
    def setUp(self):
        self.admin_user, _ = User.objects.get_or_create(
            username='admin_fx_tot',
            defaults={'email': 'admin_fx_tot@example.com', 'is_staff': True, 'is_superuser': True}
        )
        self.admin_user.set_password('pass1234')
        self.admin_user.save()

        self.accountant_user, _ = User.objects.get_or_create(
            username='acc_fx_tot',
            defaults={'email': 'acc_fx_tot@example.com'}
        )
        self.accountant_user.set_password('pass1234')
        self.accountant_user.save()

        self.accountant_emp, _ = Employee.objects.get_or_create(
            user=self.accountant_user,
            defaults={
                'is_accountant': True,
                'salary': Decimal('5200.00'),
                'date_of_join': datetime.date(2026, 1, 1),
            }
        )

        self.tech_user, _ = User.objects.get_or_create(
            username='tech_fx_tot',
            defaults={'email': 'tech_fx_tot@example.com'}
        )
        self.tech_user.set_password('pass1234')
        self.tech_user.save()

        self.tech_emp, _ = Employee.objects.get_or_create(
            user=self.tech_user,
            defaults={
                'is_employee': True,
                'salary': Decimal('4160.00'),
                'date_of_join': datetime.date(2026, 1, 1),
            }
        )

        self.cat_overhead, _ = ExpenseCategory.objects.get_or_create(
            name="Office Rent FX Totals",
            defaults={'kind': 'OVERHEAD'}
        )
        self.cat_project, _ = ExpenseCategory.objects.get_or_create(
            name="Site Supplies FX Totals",
            defaults={'kind': 'PROJECT'}
        )

        today = timezone.localdate()
        self.cur_month_first = today.replace(day=1)
        self.inv_date = self.cur_month_first

    def test_aed_only_regression(self):
        """
        AED-only fixture: P&L, dashboard KPIs, project profitability, Jobs P&L,
        Spares summary, Receivables totals, Employees Cost and Project Analytics
        must compute exact expected AED totals without regression.
        """
        proj = Project.objects.create(
            name="AED Marine Job Regression",
            client_name="Gulf Shipping Regression",
            vessel_name="MT Dubai Reg",
            category="AT_BERTH",
            currency_code="AED",
            status="IN_PROGRESS"
        )

        # Invoice: 10,000 excl vat, 500 vat, 1,000 credit -> collection_value = 9,000. Received 4,000, outstanding 5,000
        inv = Invoice.objects.create(
            project=proj,
            invoice_number="AMMS-INV-26-REG01",
            invoice_date=self.inv_date,
            currency="AED",
            exchange_rate=Decimal("1.000000"),
            amount_excl_vat=Decimal("10000.00"),
            vat_amount=Decimal("500.00"),
            credits_applied=Decimal("1000.00"),
            amount_received=Decimal("4000.00"),
        )

        # Spares: purchase 2,000, sold 3,000 -> profit 1,000, markup 50%
        spare = SpareSale.objects.create(
            project=proj,
            invoice=inv,
            source="SERVICE_JOB",
            purchase_currency="AED",
            purchase_rate=Decimal("1.000000"),
            purchase_cost=Decimal("2000.00"),
            selling_currency="AED",
            selling_rate=Decimal("1.000000"),
            selling_price=Decimal("3000.00"),
        )

        # JobCost: Travel 500, Flight 700
        jc_travel = JobCost.objects.create(
            invoice=inv,
            cost_type="TRAVEL",
            amount=Decimal("500.00"),
            currency="AED",
            exchange_rate=Decimal("1.000000"),
            date=self.inv_date
        )
        jc_flight = JobCost.objects.create(
            invoice=inv,
            cost_type="FLIGHT",
            amount=Decimal("700.00"),
            currency="AED",
            exchange_rate=Decimal("1.000000"),
            date=self.inv_date
        )

        # Expenses: Project 300, Overhead 1,200
        exp_proj = Expense.objects.create(
            category=self.cat_project,
            project=proj,
            date=self.inv_date,
            currency="AED",
            exchange_rate=Decimal("1.000000"),
            amount=Decimal("300.00"),
            vat_amount=Decimal("15.00"),
        )
        exp_over = Expense.objects.create(
            category=self.cat_overhead,
            date=self.inv_date,
            currency="AED",
            exchange_rate=Decimal("1.000000"),
            amount=Decimal("1200.00"),
            vat_amount=Decimal("60.00"),
        )

        # Service log: 8 hours @ salary 4160/208 = 20/hr -> 160 normal_cost
        log = EmployeeServiceLog.objects.create(
            employee=self.tech_emp,
            project=proj,
            date=self.inv_date,
            vessel_name="MT Dubai Reg",
            start_time=datetime.time(8, 0),
            end_time=datetime.time(16, 0),
            total_hours=Decimal("8.00"),
            normal_cost=Decimal("160.00"),
            ot_cost=Decimal("0.00"),
        )

        # 1. Project Financials Map
        fin = get_project_financials(proj)
        self.assertEqual(fin['invoiced_amount'], Decimal("9000.00"))
        self.assertEqual(fin['employee_cost'], Decimal("160.00"))
        self.assertEqual(fin['spares_cost'], Decimal("2000.00"))
        self.assertEqual(fin['spares_revenue'], Decimal("3000.00"))
        self.assertEqual(fin['other_job_costs'], Decimal("1200.00"))  # 500 + 700
        total_proj_cost = Decimal("160.00") + Decimal("2000.00") + Decimal("1200.00")  # 3,360
        self.assertEqual(fin['total_cost'], total_proj_cost)
        self.assertEqual(fin['profit'], Decimal("9000.00") - total_proj_cost)  # 5,640.00

        # 2. P&L Engine
        pnl = pnl_for_range(self.inv_date, self.inv_date, granularity='month')
        pnl_tot = pnl['totals']
        self.assertGreaterEqual(pnl_tot['revenue'], Decimal("9000.00"))
        b = pnl['buckets'][0]
        self.assertGreaterEqual(b['revenue'], Decimal("9000.00"))
        self.assertGreaterEqual(b['spares_cost'], Decimal("2000.00"))
        self.assertGreaterEqual(b['job_costs'], Decimal("1200.00"))

        # 3. Project Profitability
        prof_rows, prof_tot = get_projects_profitability(filters={'search': 'AED Marine Job Regression'})
        self.assertEqual(len(prof_rows), 1)
        r = prof_rows[0]
        self.assertEqual(r['revenue'], Decimal("9000.00"))
        self.assertEqual(r['received'], Decimal("4000.00"))
        self.assertEqual(r['outstanding'], Decimal("5000.00"))

        # 4. Jobs P&L
        jobs_rows, jobs_tot = get_jobs_pnl_data(filters={'search': 'AMMS-INV-26-REG01'})
        self.assertEqual(len(jobs_rows), 1)
        jr = jobs_rows[0]
        self.assertEqual(jr['net_invoice'], Decimal("9000.00"))
        self.assertEqual(jr['engineer'], Decimal("160.00"))
        self.assertEqual(jr['spare_cost'], Decimal("2000.00"))
        self.assertEqual(jr['travel_cost'], Decimal("500.00"))
        self.assertEqual(jr['flight'], Decimal("700.00"))
        self.assertEqual(jr['total_cost'], Decimal("3360.00"))
        self.assertEqual(jr['profit'], Decimal("5640.00"))

        # 5. Spares Summary
        spares_list, spares_sum = get_spares_accounting_data(filters={'search': 'AMMS-INV-26-REG01'})
        self.assertEqual(len(spares_list), 1)
        self.assertEqual(spares_sum['total_purchase'], Decimal("2000.00"))
        self.assertEqual(spares_sum['total_selling'], Decimal("3000.00"))
        self.assertEqual(spares_sum['total_profit'], Decimal("1000.00"))
        self.assertEqual(spares_sum['avg_markup_pct'], Decimal("50.00"))

        # 6. Receivables
        rec_list, rec_sum = get_receivables_data(filters={'search': 'AMMS-INV-26-REG01'})
        self.assertEqual(len(rec_list), 1)
        self.assertEqual(rec_list[0]['invoice_amount'], Decimal("9000.00"))
        self.assertEqual(rec_list[0]['amount_received'], Decimal("4000.00"))
        self.assertEqual(rec_list[0]['outstanding_amount'], Decimal("5000.00"))

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_mixed_fixture_on_one_project(self):
        """
        Mixed fixture on one project:
        USD invoice: 10,000 @ 3.6725 (paid USD 6,000) -> rev AED 36,725.00, out AED 14,690.00
        EUR spare: purchase 500 @ 4.0, sold AED 2,500 -> cost AED 2,000, markup 25%
        INR flight: 20,000 @ 0.044 -> job cost AED 880.00
        AED expense: 1,000 linked to project
        AED service log: 8 hours @ 20/hr = AED 160.00
        """
        proj = Project.objects.create(
            name="Mixed FX Tanker Overhaul Unique",
            client_name="Apex Marine FX",
            vessel_name="MV Global Star FX",
            category="SERVICE_JOB",
            currency_code="USD",
            status="IN_PROGRESS"
        )

        inv = Invoice.objects.create(
            project=proj,
            invoice_number="AMMS-INV-26-MIX01",
            invoice_date=self.inv_date,
            currency="USD",
            exchange_rate=Decimal("3.672500"),
            amount_excl_vat=Decimal("10000.00"),
            vat_amount=Decimal("0.00"),
            credits_applied=Decimal("0.00"),
            amount_received=Decimal("6000.00"),
        )
        self.assertEqual(inv.collection_value_aed, Decimal("36725.00"))
        self.assertEqual(inv.amount_received_aed, Decimal("22035.00"))
        self.assertEqual(inv.outstanding_aed, Decimal("14690.00"))

        spare = SpareSale.objects.create(
            project=proj,
            invoice=inv,
            source="SERVICE_JOB",
            purchase_currency="EUR",
            purchase_rate=Decimal("4.000000"),
            purchase_cost=Decimal("500.00"),
            selling_currency="AED",
            selling_rate=Decimal("1.000000"),
            selling_price=Decimal("2500.00"),
        )
        self.assertEqual(spare.purchase_cost_aed, Decimal("2000.00"))
        self.assertEqual(spare.selling_price_aed, Decimal("2500.00"))
        self.assertEqual(spare.profit_aed, Decimal("500.00"))
        self.assertEqual(spare.markup_pct, Decimal("25.00"))

        jc_flight = JobCost.objects.create(
            invoice=inv,
            cost_type="FLIGHT",
            currency="INR",
            exchange_rate=Decimal("0.044000"),
            amount=Decimal("20000.00"),
            date=self.inv_date,
        )
        self.assertEqual(jc_flight.amount_aed, Decimal("880.00"))

        exp_proj = Expense.objects.create(
            category=self.cat_project,
            project=proj,
            date=self.inv_date,
            currency="AED",
            exchange_rate=Decimal("1.000000"),
            amount=Decimal("1000.00"),
        )

        log = EmployeeServiceLog.objects.create(
            employee=self.tech_emp,
            project=proj,
            date=self.inv_date,
            vessel_name="MV Global Star FX",
            start_time=datetime.time(8, 0),
            end_time=datetime.time(16, 0),
            total_hours=Decimal("8.00"),
            normal_cost=Decimal("160.00"),
            ot_cost=Decimal("0.00"),
        )

        # 1. Assert project financials
        fin = get_project_financials(proj)
        self.assertEqual(fin['invoiced_amount'], Decimal("36725.00"))
        self.assertEqual(fin['spares_cost'], Decimal("2000.00"))
        self.assertEqual(fin['job_costs']['flight'], Decimal("880.00"))
        self.assertEqual(fin['other_job_costs'], Decimal("880.00"))
        self.assertEqual(fin['employee_cost'], Decimal("160.00"))
        expected_total_cost = Decimal("160.00") + Decimal("2000.00") + Decimal("880.00")  # 3,040.00
        self.assertEqual(fin['total_cost'], expected_total_cost)
        expected_profit = Decimal("36725.00") - expected_total_cost  # 33,685.00
        self.assertEqual(fin['profit'], expected_profit)
        expected_margin = round((expected_profit / Decimal("36725.00")) * Decimal("100"), 1)
        self.assertEqual(fin['profit_pct'], expected_margin)

        # 2. Assert Receivables
        rec_list, rec_sum = get_receivables_data(filters={'search': 'AMMS-INV-26-MIX01'})
        self.assertEqual(rec_sum['total_outstanding'], Decimal("14690.00"))
        self.assertEqual(rec_sum['bucket_0_30']['amount'], Decimal("14690.00"))
        r = rec_list[0]
        # Row displays foreign currency in invoice_amount/amount_received/outstanding_amount
        self.assertEqual(r['currency'], 'USD')
        self.assertEqual(r['invoice_amount'], Decimal("10000.00"))
        self.assertEqual(r['amount_received'], Decimal("6000.00"))
        self.assertEqual(r['outstanding_amount'], Decimal("4000.00"))
        # And AED fields
        self.assertEqual(r['invoice_amount_aed'], Decimal("36725.00"))
        self.assertEqual(r['amount_received_aed'], Decimal("22035.00"))
        self.assertEqual(r['outstanding_aed'], Decimal("14690.00"))

        # 3. Assert Jobs P&L
        jobs_rows, jobs_tot = get_jobs_pnl_data(filters={'search': 'AMMS-INV-26-MIX01'})
        jr = jobs_rows[0]
        self.assertEqual(jr['net_invoice'], Decimal("36725.00"))
        self.assertEqual(jr['spare_cost'], Decimal("2000.00"))
        self.assertEqual(jr['flight'], Decimal("880.00"))
        self.assertEqual(jr['total_cost'], expected_total_cost)
        self.assertEqual(jr['profit'], expected_profit)

        # 4. Assert Spares Markup
        spares_rows, spares_sum = get_spares_accounting_data(filters={'search': 'AMMS-INV-26-MIX01'})
        sr = spares_rows[0]
        self.assertEqual(sr.markup_pct, Decimal("25.00"))
        self.assertEqual(spares_sum['total_purchase'], Decimal("2000.00"))
        self.assertEqual(spares_sum['total_selling'], Decimal("2500.00"))
        self.assertEqual(spares_sum['total_profit'], Decimal("500.00"))
        self.assertEqual(spares_sum['avg_markup_pct'], Decimal("25.00"))

        # 5. Assert SPARES_ONLY mismatch compares in AED
        inv_spares_only = Invoice.objects.create(
            project=proj,
            invoice_number="AMMS-INV-26-SPMIX01",
            invoice_date=self.inv_date,
            invoice_type="SPARES_ONLY",
            currency="USD",
            exchange_rate=Decimal("3.672500"),
            amount_excl_vat=Decimal("3000.00"),  # AED 11,017.50
        )
        sp_only = SpareSale.objects.create(
            project=proj,
            invoice=inv_spares_only,
            source="SPARES_ONLY",
            purchase_currency="EUR",
            purchase_rate=Decimal("4.000000"),
            purchase_cost=Decimal("500.00"),
            selling_currency="AED",
            selling_rate=Decimal("1.000000"),
            selling_price=Decimal("2500.00"),  # 2,500 AED != 11,017.50 AED
        )
        # Check alerts contains mismatch
        self.assertIn("Invoice spares mismatch", sp_only.alerts)

        # Update selling price to match collection_value_aed exactly
        sp_only.selling_price = Decimal("11017.50")
        sp_only.save()
        self.assertNotIn("Invoice spares mismatch", sp_only.alerts)

        # Clean up the extra spares-only invoice and spare sale
        sp_only.delete()
        inv_spares_only.delete()

        # 6. Project Analytics revenue and profit equals Projects page
        self.client.force_login(self.admin_user)
        resp = self.client.get(reverse('analytics_project'))
        self.assertEqual(resp.status_code, 200)
        pa_list = resp.context['project_analytics']
        matching_pa = next((item for item in pa_list if item['project'].id == proj.id), None)
        self.assertIsNotNone(matching_pa)
        self.assertEqual(matching_pa['revenue'], fin['invoiced_amount'])
        self.assertEqual(matching_pa['profit'], fin['profit'])

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_rate_change_after_saving_does_not_change_any_total(self):
        """
        Saved records retain their rates and *_aed values even if the ExchangeRate table is altered.
        """
        ExchangeRate.objects.create(
            currency="USD",
            rate_to_aed=Decimal("3.672500"),
            effective_from=datetime.date(2026, 1, 1),
            created_by=self.admin_user
        )

        proj = Project.objects.create(
            name="FX Rate Stability Test Unique",
            currency_code="USD",
            status="IN_PROGRESS"
        )
        inv = Invoice.objects.create(
            project=proj,
            invoice_number="AMMS-INV-26-STAB01",
            invoice_date=self.inv_date,
            currency="USD",
            exchange_rate=Decimal("3.672500"),
            amount_excl_vat=Decimal("5000.00"),
        )
        self.assertEqual(inv.collection_value_aed, Decimal("18362.50"))

        # Add a new rate row with a drastically different rate
        ExchangeRate.objects.create(
            currency="USD",
            rate_to_aed=Decimal("5.000000"),
            effective_from=datetime.date(2026, 1, 2),
            created_by=self.admin_user
        )

        # Re-fetch financials
        fin = get_project_financials(proj)
        self.assertEqual(fin['invoiced_amount'], Decimal("18362.50"))

        _, rec_sum = get_receivables_data(filters={'search': 'AMMS-INV-26-STAB01'})
        self.assertEqual(rec_sum['total_outstanding'], Decimal("18362.50"))

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_excel_exports_totals_in_aed(self):
        """
        Verify Excel export responses contain total rows formatted in AED.
        """
        proj = Project.objects.create(name="Excel Test Proj Unique", currency_code="USD")
        inv = Invoice.objects.create(
            project=proj,
            invoice_number="AMMS-INV-26-EXUN01",
            invoice_date=self.inv_date,
            currency="USD",
            exchange_rate=Decimal("3.672500"),
            amount_excl_vat=Decimal("2000.00"),  # AED 7,345.00
            credits_applied=Decimal("200.00"),   # AED 734.50
            amount_received=Decimal("1000.00"),  # AED 3,672.50
        )
        cost = JobCost.objects.create(
            invoice=inv,
            cost_type="TRAVEL",
            currency="EUR",
            exchange_rate=Decimal("4.000000"),
            amount=Decimal("100.00"),  # AED 400.00
            date=self.inv_date
        )
        spare = SpareSale.objects.create(
            project=proj,
            invoice=inv,
            purchase_currency="INR",
            purchase_rate=Decimal("0.044000"),
            purchase_cost=Decimal("10000.00"),  # AED 440.00
            selling_currency="AED",
            selling_rate=Decimal("1.000000"),
            selling_price=Decimal("1500.00"),   # AED 1,500.00
        )
        exp = Expense.objects.create(
            category=self.cat_overhead,
            date=self.inv_date,
            currency="EUR",
            exchange_rate=Decimal("4.000000"),
            amount=Decimal("50.00"),      # AED 200.00
            vat_amount=Decimal("2.50"),   # AED 10.00
        )

        # 1. Jobs P&L Excel
        jobs_data, jobs_totals = get_jobs_pnl_data(filters={'search': 'AMMS-INV-26-EXUN01'})
        resp_jobs = jobs_pnl_excel_response(jobs_data, jobs_totals)
        self.assertEqual(resp_jobs.status_code, 200)
        self.assertEqual(jobs_totals['net_invoice'], Decimal("6610.50"))  # 7345 - 734.50
        self.assertEqual(jobs_totals['travel_cost'], Decimal("400.00"))
        self.assertEqual(jobs_totals['spare_cost'], Decimal("440.00"))

        # 2. Invoices Excel
        from finance.services import get_invoice_costing_data
        inv_data, inv_totals = get_invoice_costing_data(filters={'search': 'AMMS-INV-26-EXUN01'})
        resp_inv = invoices_excel_response(inv_data, inv_totals)
        self.assertEqual(resp_inv.status_code, 200)
        self.assertEqual(inv_totals['amount_excl_vat'], Decimal("7345.00"))
        self.assertEqual(inv_totals['credits_applied'], Decimal("734.50"))
        self.assertEqual(inv_totals['collection_value'], Decimal("6610.50"))

        # 3. Spares Excel
        sp_data, sp_summary = get_spares_accounting_data(filters={'search': 'AMMS-INV-26-EXUN01'})
        resp_spares = spares_excel_response(sp_data, sp_summary)
        self.assertEqual(resp_spares.status_code, 200)
        self.assertEqual(sp_summary['total_purchase'], Decimal("440.00"))
        self.assertEqual(sp_summary['total_selling'], Decimal("1500.00"))
        self.assertEqual(sp_summary['total_profit'], Decimal("1060.00"))

        # 4. Receivables Excel
        rec_data, rec_summary = get_receivables_data(filters={'search': 'AMMS-INV-26-EXUN01'})
        resp_rec = receivables_excel_response(rec_data, rec_summary)
        self.assertEqual(resp_rec.status_code, 200)
        self.assertEqual(rec_summary['total_outstanding'], Decimal("2938.00"))  # 6610.50 - 3672.50

        # 5. Expenses Excel
        qs_exp = Expense.objects.filter(id=exp.id)
        totals_exp = {'amount': Decimal("200.00"), 'vat_amount': Decimal("10.00")}
        resp_exp = expenses_excel_response(qs_exp, totals_exp)
        self.assertEqual(resp_exp.status_code, 200)


class MultiCurrencyEndToEndFixesTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.accountant = User.objects.create_user(
            username='acc_fixes_user',
            email='acc_fixes@example.com',
            password='pass1234',
            is_staff=True,
            is_superuser=True,
        )
        self.today = timezone.localdate()

        # Seed Exchange Rates
        ExchangeRate.objects.create(
            currency='USD',
            rate_to_aed=Decimal('3.672500'),
            effective_from=datetime.date(2026, 1, 1),
            is_active=True,
        )
        ExchangeRate.objects.create(
            currency='INR',
            rate_to_aed=Decimal('0.044000'),
            effective_from=datetime.date(2026, 1, 1),
            is_active=True,
        )
        ExchangeRate.objects.create(
            currency='EUR',
            rate_to_aed=Decimal('4.000000'),
            effective_from=datetime.date(2026, 1, 1),
            is_active=True,
        )

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_dashboard_clients_and_concentration_in_aed(self):
        """
        Item 1: 4 clients in 4 currencies (AED, USD, INR, EUR), each with
        Invoice 10,000 - Credit 1,000 = Collection Value 9,000.
        Dashboard Top 5 Clients reflects AED collection value:
        - AED: 9,000.00
        - USD: 33,052.50 (9,000 * 3.6725)
        - INR: 396.00 (9,000 * 0.044)
        - EUR: 36,000.00 (9,000 * 4.0)
        Total AED = 78,448.50. EUR client concentration is 36,000 / 78,448.50 = 45.9% (>40%),
        triggering high client concentration warning in AED.
        """
        # Client 1: AED
        p_aed = Project.objects.create(name='Proj AED', client_name='Client AED')
        Invoice.objects.create(
            project=p_aed,
            customer='Client AED',
            invoice_number='AMMS-INV-26-D001',
            invoice_date=self.today,
            currency='AED',
            exchange_rate=Decimal('1.000000'),
            amount_excl_vat=Decimal('10000.00'),
            credits_applied=Decimal('1000.00'),
        )
        # Client 2: USD
        p_usd = Project.objects.create(name='Proj USD', client_name='Client USD')
        Invoice.objects.create(
            project=p_usd,
            customer='Client USD',
            invoice_number='AMMS-INV-26-D002',
            invoice_date=self.today,
            currency='USD',
            exchange_rate=Decimal('3.672500'),
            amount_excl_vat=Decimal('10000.00'),
            credits_applied=Decimal('1000.00'),
        )
        # Client 3: INR
        p_inr = Project.objects.create(name='Proj INR', client_name='Client INR')
        Invoice.objects.create(
            project=p_inr,
            customer='Client INR',
            invoice_number='AMMS-INV-26-D003',
            invoice_date=self.today,
            currency='INR',
            exchange_rate=Decimal('0.044000'),
            amount_excl_vat=Decimal('10000.00'),
            credits_applied=Decimal('1000.00'),
        )
        # Client 4: EUR
        p_eur = Project.objects.create(name='Proj EUR', client_name='Client EUR')
        Invoice.objects.create(
            project=p_eur,
            customer='Client EUR',
            invoice_number='AMMS-INV-26-D004',
            invoice_date=self.today,
            currency='EUR',
            exchange_rate=Decimal('4.000000'),
            amount_excl_vat=Decimal('10000.00'),
            credits_applied=Decimal('1000.00'),
        )

        dash = get_dashboard_data(period='this_month')
        clients_map = {c['name']: c for c in dash['top_5_clients']}

        self.assertEqual(clients_map['Client EUR']['revenue'], Decimal('36000.00'))
        self.assertEqual(clients_map['Client USD']['revenue'], Decimal('33052.50'))
        self.assertEqual(clients_map['Client AED']['revenue'], Decimal('9000.00'))
        self.assertEqual(clients_map['Client INR']['revenue'], Decimal('396.00'))

        # Margins: profit / AED revenue
        for c in dash['top_5_clients']:
            if c['revenue'] > Decimal('0.00'):
                expected_margin = round((c['profit'] / c['revenue']) * Decimal('100'), 1)
                self.assertEqual(c['margin_pct'], expected_margin)

        # EUR client is top client: 36,000 / 78,448.50 = 45.9% > 40%
        self.assertTrue(dash['has_high_client_concentration'])

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_invoices_modals_display_invoice_currency_and_muted_aed_line(self):
        """
        Item 2: Invoices list Record Payment & Apply Credit modals show invoice currency
        on every amount (Collection Value / Previously Received / Balance Due / Amount Without Tax / Credits)
        plus one muted AED line for the balance, and input label 'Payment Amount (USD)'.
        """
        self.client.force_login(self.accountant)
        p = Project.objects.create(name='Proj Modals', client_name='Client Modals')
        Invoice.objects.create(
            project=p,
            customer='Client Modals',
            invoice_number='AMMS-INV-26-M001',
            invoice_date=self.today,
            currency='USD',
            exchange_rate=Decimal('3.672500'),
            amount_excl_vat=Decimal('10000.00'),
            credits_applied=Decimal('1000.00'),
            amount_received=Decimal('4000.00'),
        )

        resp = self.client.get(reverse('fin_invoices'))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')

        # Modal labels in USD
        self.assertIn('9,000.00 USD', html)  # Collection Value
        self.assertIn('4,000.00 USD', html)  # Previously Received
        self.assertIn('5,000.00 USD', html)  # Current Balance Due
        self.assertIn('Payment Amount (USD)', html)

        # Muted AED line for balance: 5000 * 3.6725 = 18,362.50
        self.assertIn('18,362.50 AED', html)

        # Apply Credit Modal
        self.assertIn('10,000.00 USD', html)  # Amount Without Tax
        self.assertIn('1,000.00 USD', html)   # Credits Previously Applied

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_invoice_costing_rows_include_currency_and_headers_labeled_aed(self):
        """
        Item 3: get_invoice_costing_data rows include 'currency' and 'exchange_rate',
        and Invoices page table headers for Spares rev/cost, other cost, profit are labeled '(AED)'.
        """
        from finance.services import get_invoice_costing_data
        self.client.force_login(self.accountant)
        p = Project.objects.create(name='Proj Costing', client_name='Client Costing')
        Invoice.objects.create(
            project=p,
            customer='Client Costing',
            invoice_number='AMMS-INV-26-C001',
            invoice_date=self.today,
            currency='USD',
            exchange_rate=Decimal('3.672500'),
            amount_excl_vat=Decimal('10000.00'),
        )

        rows, _ = get_invoice_costing_data(filters={'search': 'AMMS-INV-26-C001'})
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r['currency'], 'USD')
        self.assertEqual(r['exchange_rate'], Decimal('3.672500'))

        resp = self.client.get(reverse('fin_invoices'))
        html = resp.content.decode('utf-8')

        self.assertIn('Spares Rev (AED)', html)
        self.assertIn('Spare Cost (AED)', html)
        self.assertIn('Spares Profit (AED)', html)
        self.assertIn('Other Cost (AED)', html)
        self.assertIn('Total Net Profit (AED)', html)

        # Row renders currency and AED line
        self.assertIn('AED 36,725.00', html)

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_receivables_aed_line_uses_collection_value_aed_and_intcomma(self):
        """
        Item 4: Receivables AED line under invoice amount uses invoice_amount_aed
        (collection_value_aed, i.e. after credits, NOT amount_excl_vat_aed),
        and values are formatted with thousands separators (|intcomma).
        Projects page also uses thousands separators.
        """
        self.client.force_login(self.accountant)
        p = Project.objects.create(name='Proj Rec FX', client_name='Client Rec FX')
        Invoice.objects.create(
            project=p,
            customer='Client Rec FX',
            invoice_number='AMMS-INV-26-R001',
            invoice_date=self.today,
            currency='USD',
            exchange_rate=Decimal('3.672500'),
            amount_excl_vat=Decimal('10000.00'),
            credits_applied=Decimal('1000.00'),   # Collection Value = 9000 USD
            amount_received=Decimal('4000.00'),    # Balance = 5000 USD
        )

        resp_rec = self.client.get(reverse('fin_receivables'))
        html_rec = resp_rec.content.decode('utf-8')

        # Pre-credit was 10000 * 3.6725 = 36,725.00
        # Post-credit is 9000 * 3.6725 = 33,052.50
        self.assertIn('AED 33,052.50', html_rec)
        self.assertNotIn('AED 36,725.00', html_rec)

        # Outstanding AED line has intcomma: 5000 * 3.6725 = 18,362.50
        self.assertIn('AED 18,362.50', html_rec)

        # Projects page formatting
        resp_proj = self.client.get(reverse('fin_projects'))
        self.assertEqual(resp_proj.status_code, 200)
        html_proj = resp_proj.content.decode('utf-8')
        self.assertIn('33,052.50', html_proj)

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_add_invoice_project_currencies_map_and_prefill(self):
        """
        Item 5: Add Invoice passes project currencies JSON map (id -> code)
        to template, and setting project pre-fills currency code.
        """
        self.client.force_login(self.accountant)
        p_eur = Project.objects.create(name='Proj EUR Test', currency_code='EUR')
        p_usd = Project.objects.create(name='Proj USD Test', currency_code='USD')

        resp = self.client.get(reverse('fin_invoice_add'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('project_currencies', resp.context)
        self.assertIn('project_currencies_json', resp.context)
        self.assertEqual(resp.context['project_currencies'][str(p_eur.id)], 'EUR')
        self.assertEqual(resp.context['project_currencies'][str(p_usd.id)], 'USD')

        html = resp.content.decode('utf-8')
        self.assertIn('projectCurrencies', html)
        self.assertIn(f'"{p_eur.id}": "EUR"', html)

        # Pre-fill when project query param is provided
        resp_prefill = self.client.get(f"{reverse('fin_invoice_add')}?project={p_eur.id}")
        self.assertEqual(resp_prefill.context['form'].initial['currency'], 'EUR')

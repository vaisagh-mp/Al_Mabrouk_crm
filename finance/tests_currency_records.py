import datetime
from decimal import Decimal
from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone

from Admin.models import Project, Employee
from finance.models import Invoice, JobCost, SpareSale, Expense, ExpenseCategory, ExchangeRate
from finance.forms import InvoiceForm, JobCostForm, SpareSaleForm, ExpenseForm
from finance.currency import to_aed, get_rate
from finance.services import pnl_for_range, get_receivables_data, get_dashboard_data

User = get_user_model()


class MultiCurrencyRecordsTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser('admin_user', 'admin@example.com', 'pass1234')
        self.accountant_user = User.objects.create_user('acc_user', 'acc@example.com', 'pass1234')
        self.accountant_emp = Employee.objects.create(
            user=self.accountant_user,
            is_accountant=True,
            salary=Decimal('5000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )
        self.regular_user = User.objects.create_user('reg_user', 'reg@example.com', 'pass1234')
        self.regular_emp = Employee.objects.create(
            user=self.regular_user,
            is_employee=True,
            salary=Decimal('4000.00'),
            date_of_join=datetime.date(2026, 1, 1),
        )

        self.project_aed = Project.objects.create(
            name="AED Project",
            currency_code="AED",
            status="IN_PROGRESS"
        )
        self.category = ExpenseCategory.objects.create(name="Office", kind="OVERHEAD")

    def test_migration_and_model_defaults_for_aed(self):
        """
        Existing AED invoice/job cost/spare/expense have rate 1 and *_aed == original.
        """
        inv = Invoice.objects.create(
            invoice_number="AMMS-INV-26-0001",
            invoice_date=datetime.date(2026, 11, 1),
            amount_excl_vat=Decimal("1500.00"),
            vat_amount=Decimal("75.00"),
            project=self.project_aed
        )
        self.assertEqual(inv.currency, "AED")
        self.assertEqual(inv.exchange_rate, Decimal("1.000000"))
        self.assertEqual(inv.amount_excl_vat_aed, Decimal("1500.00"))
        self.assertEqual(inv.vat_amount_aed, Decimal("75.00"))
        self.assertEqual(inv.collection_value_aed, Decimal("1500.00"))
        self.assertEqual(inv.outstanding_aed, Decimal("1500.00"))

        cost = JobCost.objects.create(
            invoice=inv,
            cost_type="MATERIALS",
            amount=Decimal("300.00"),
            date=datetime.date(2026, 11, 1)
        )
        self.assertEqual(cost.currency, "AED")
        self.assertEqual(cost.exchange_rate, Decimal("1.000000"))
        self.assertEqual(cost.amount_aed, Decimal("300.00"))

        spare = SpareSale.objects.create(
            service_ref="SR-AED-01",
            purchase_cost=Decimal("400.00"),
            selling_price=Decimal("650.00")
        )
        self.assertEqual(spare.purchase_currency, "AED")
        self.assertEqual(spare.purchase_rate, Decimal("1.000000"))
        self.assertEqual(spare.purchase_cost_aed, Decimal("400.00"))
        self.assertEqual(spare.selling_currency, "AED")
        self.assertEqual(spare.selling_rate, Decimal("1.000000"))
        self.assertEqual(spare.selling_price_aed, Decimal("650.00"))

        exp = Expense.objects.create(
            date=datetime.date(2026, 11, 1),
            category=self.category,
            amount=Decimal("250.00"),
            vat_amount=Decimal("12.50"),
            created_by=self.accountant_user
        )
        self.assertEqual(exp.currency, "AED")
        self.assertEqual(exp.exchange_rate, Decimal("1.000000"))
        self.assertEqual(exp.amount_aed, Decimal("250.00"))
        self.assertEqual(exp.vat_amount_aed, Decimal("12.50"))

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_invoice_usd_calculations_payments_and_credits(self):
        """
        Invoice USD 10,000 @ 3.6725, VAT 0 -> amount_excl_vat_aed = 36725.00.
        Record payment USD 6,000 -> amount_received_aed = 22035.00, outstanding_aed = 14690.00, status PARTIAL.
        Credits: credit USD 1,000 -> credits_applied_aed = 3672.50, outstanding_aed drops accordingly.
        """
        inv = Invoice.objects.create(
            invoice_number="AMMS-INV-26-0002",
            invoice_date=datetime.date(2026, 11, 1),
            currency="USD",
            exchange_rate=Decimal("3.672500"),
            amount_excl_vat=Decimal("10000.00"),
            vat_amount=Decimal("0.00"),
            project=self.project_aed
        )
        self.assertEqual(inv.amount_excl_vat_aed, Decimal("36725.00"))
        self.assertEqual(inv.collection_value_aed, Decimal("36725.00"))
        self.assertEqual(inv.outstanding_aed, Decimal("36725.00"))
        self.assertEqual(inv.status, "OVERDUE" if inv.due_date and inv.due_date < timezone.localdate() else "OPEN")

        # Record payment USD 6,000 via view
        self.client.force_login(self.accountant_user)
        pay_url = reverse('fin_invoice_record_payment', args=[inv.id])
        res = self.client.post(pay_url, {
            'amount_received': '6000.00',
            'payment_received_date': '2026-11-02'
        })
        self.assertEqual(res.status_code, 302)

        inv.refresh_from_db()
        self.assertEqual(inv.amount_received, Decimal("6000.00"))
        self.assertEqual(inv.amount_received_aed, Decimal("22035.00"))
        self.assertEqual(inv.outstanding, Decimal("4000.00"))
        self.assertEqual(inv.outstanding_aed, Decimal("14690.00"))
        self.assertEqual(inv.status, "PARTIAL")

        # Apply credit USD 1,000 via view
        credit_url = reverse('fin_invoice_apply_credit', args=[inv.id])
        res_credit = self.client.post(credit_url, {
            'credit_amount': '1000.00',
            'notes': 'Discount applied'
        })
        self.assertEqual(res_credit.status_code, 302)

        inv.refresh_from_db()
        self.assertEqual(inv.credits_applied, Decimal("1000.00"))
        self.assertEqual(inv.credits_applied_aed, Decimal("3672.50"))
        self.assertEqual(inv.outstanding, Decimal("3000.00"))
        # 36725 - 3672.50 - 22035 = 11017.50
        self.assertEqual(inv.outstanding_aed, Decimal("11017.50"))
        self.assertEqual(inv.status, "PARTIAL")

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_changing_invoice_rate_recomputes_aed_and_exchangerate_row_changes_do_not(self):
        """
        Changing invoice rate recomputes its AED fields; changing ExchangeRate rows later does NOT affect saved records.
        """
        ExchangeRate.objects.create(
            currency="USD",
            rate_to_aed=Decimal("3.672500"),
            effective_from=datetime.date(2026, 1, 1),
            is_active=True
        )
        inv = Invoice.objects.create(
            invoice_number="AMMS-INV-26-0003",
            invoice_date=datetime.date(2026, 11, 1),
            currency="USD",
            exchange_rate=Decimal("3.672500"),
            amount_excl_vat=Decimal("1000.00"),
            vat_amount=Decimal("50.00"),
            project=self.project_aed
        )
        self.assertEqual(inv.amount_excl_vat_aed, Decimal("3672.50"))
        self.assertEqual(inv.vat_amount_aed, Decimal("183.63"))

        # Changing rate on the invoice directly recomputes
        inv.exchange_rate = Decimal("3.700000")
        inv.save()
        self.assertEqual(inv.amount_excl_vat_aed, Decimal("3700.00"))
        self.assertEqual(inv.vat_amount_aed, Decimal("185.00"))

        # Updating ExchangeRate model table should have NO effect on saved invoice
        ExchangeRate.objects.filter(currency="USD").update(rate_to_aed=Decimal("4.000000"))
        inv.refresh_from_db()
        self.assertEqual(inv.exchange_rate, Decimal("3.700000"))
        self.assertEqual(inv.amount_excl_vat_aed, Decimal("3700.00"))

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_aed_record_with_rate_posted_is_forced_to_rate_one(self):
        """
        AED record with rate 5 posted -> saved with rate 1.
        """
        form = InvoiceForm(data={
            'invoice_number': 'AMMS-INV-26-0004',
            'invoice_date': '2026-11-01',
            'currency': 'AED',
            'exchange_rate': '5.000000',
            'amount_excl_vat': '2000.00',
            'vat_amount': '100.00',
            'credits_applied': '0.00',
            'invoice_type': 'SERVICE_JOB',
            'project': self.project_aed.id,
        })
        self.assertTrue(form.is_valid(), form.errors)
        inv = form.save()
        self.assertEqual(inv.exchange_rate, Decimal("1.000000"))
        self.assertEqual(inv.amount_excl_vat_aed, Decimal("2000.00"))

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_non_aed_without_rate_or_rate_zero_fails_form(self):
        """
        Non-AED without rate or rate 0 -> form error, nothing saved.
        """
        form_no_rate = InvoiceForm(data={
            'invoice_number': 'AMMS-INV-26-0005',
            'invoice_date': '2026-11-01',
            'currency': 'USD',
            'exchange_rate': '',
            'amount_excl_vat': '1000.00',
            'project': self.project_aed.id,
        })
        self.assertFalse(form_no_rate.is_valid())
        self.assertIn('exchange_rate', form_no_rate.errors)

        form_zero_rate = InvoiceForm(data={
            'invoice_number': 'AMMS-INV-26-0005',
            'invoice_date': '2026-11-01',
            'currency': 'USD',
            'exchange_rate': '0.00',
            'amount_excl_vat': '1000.00',
            'project': self.project_aed.id,
        })
        self.assertFalse(form_zero_rate.is_valid())
        self.assertIn('exchange_rate', form_zero_rate.errors)

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_spare_sale_multi_currency(self):
        """
        Spare purchase EUR 500 @ 4.0 + selling AED 2,500 -> purchase_cost_aed = 2000.00, selling_price_aed = 2500.00.
        """
        spare = SpareSale.objects.create(
            service_ref="SR-EUR-01",
            purchase_cost=Decimal("500.00"),
            purchase_currency="EUR",
            purchase_rate=Decimal("4.000000"),
            selling_price=Decimal("2500.00"),
            selling_currency="AED",
            selling_rate=Decimal("1.000000")
        )
        self.assertEqual(spare.purchase_cost_aed, Decimal("2000.00"))
        self.assertEqual(spare.selling_price_aed, Decimal("2500.00"))

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_expense_multi_currency(self):
        """
        Expense INR 10,000 @ 0.044 -> amount_aed = 440.00.
        """
        exp = Expense.objects.create(
            date=datetime.date(2026, 11, 1),
            category=self.category,
            amount=Decimal("10000.00"),
            currency="INR",
            exchange_rate=Decimal("0.044000"),
            vat_amount=Decimal("0.00"),
            created_by=self.accountant_user
        )
        self.assertEqual(exp.amount_aed, Decimal("440.00"))

    def test_rate_endpoint_functionality_and_permissions(self):
        """
        Rate endpoint /finance/api/exchange-rate/: returns right rate for date; null + message when missing; 403 for non-finance users.
        """
        ExchangeRate.objects.create(
            currency="USD",
            rate_to_aed=Decimal("3.672500"),
            effective_from=datetime.date(2026, 1, 1),
            is_active=True
        )

        url = reverse('fin_api_exchange_rate')

        # 403 for regular user
        self.client.force_login(self.regular_user)
        res = self.client.get(f"{url}?currency=USD&date=2026-11-01")
        self.assertEqual(res.status_code, 403)

        # 200 for accountant
        self.client.force_login(self.accountant_user)
        res = self.client.get(f"{url}?currency=USD&date=2026-11-01")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get('rate'), '3.672500')

        # Missing currency/rate
        res_missing = self.client.get(f"{url}?currency=EUR&date=2026-11-01")
        self.assertEqual(res_missing.status_code, 200)
        data_missing = res_missing.json()
        self.assertIsNone(data_missing.get('rate'))
        self.assertIn('No EUR rate', data_missing.get('message', ''))

    @override_settings(FINANCE_MULTI_CURRENCY=True)
    def test_new_invoice_for_usd_project_defaults_to_usd(self):
        """
        New invoice for USD project defaults to USD (when FINANCE_MULTI_CURRENCY=True).
        """
        proj_usd = Project.objects.create(
            name="USD Project",
            currency_code="USD",
            status="IN_PROGRESS"
        )
        form = InvoiceForm(initial={'project': proj_usd})
        self.assertEqual(form.fields['currency'].initial, 'USD')

    @override_settings(FINANCE_MULTI_CURRENCY=False)
    def test_feature_switch_disabled_only_aed_offered_and_usd_rejected(self):
        """
        FINANCE_MULTI_CURRENCY=False -> only AED offered and posted USD is rejected.
        """
        form = InvoiceForm()
        choices = [c[0] for c in form.fields['currency'].choices]
        self.assertEqual(choices, ['AED'])

        # Attempt to post USD when switch is False
        form_post = InvoiceForm(data={
            'invoice_number': 'AMMS-INV-26-0006',
            'invoice_date': '2026-11-01',
            'currency': 'USD',
            'exchange_rate': '3.6725',
            'amount_excl_vat': '1000.00',
            'project': self.project_aed.id,
        })
        self.assertFalse(form_post.is_valid())
        self.assertIn('currency', form_post.errors)

    def test_existing_totals_unchanged_on_aed_fixtures(self):
        """
        Existing totals unchanged: P&L, dashboard KPIs, Projects page and Receivables totals identical before/after migration on AED fixtures.
        """
        inv1 = Invoice.objects.create(
            invoice_number="AMMS-INV-26-0010",
            invoice_date=datetime.date(2026, 11, 1),
            amount_excl_vat=Decimal("5000.00"),
            vat_amount=Decimal("250.00"),
            amount_received=Decimal("2000.00"),
            project=self.project_aed
        )
        inv2 = Invoice.objects.create(
            invoice_number="AMMS-INV-26-0011",
            invoice_date=datetime.date(2026, 11, 2),
            amount_excl_vat=Decimal("3000.00"),
            vat_amount=Decimal("150.00"),
            amount_received=Decimal("3150.00"),
            project=self.project_aed
        )
        cost = JobCost.objects.create(
            invoice=inv1,
            cost_type="MATERIALS",
            amount=Decimal("800.00"),
            date=datetime.date(2026, 11, 1)
        )
        spare = SpareSale.objects.create(
            service_ref="SR-AED-TOTAL",
            purchase_cost=Decimal("1000.00"),
            selling_price=Decimal("1800.00")
        )
        exp = Expense.objects.create(
            date=datetime.date(2026, 11, 1),
            category=self.category,
            amount=Decimal("600.00"),
            created_by=self.accountant_user
        )

        pnl = pnl_for_range(start_date=datetime.date(2026, 11, 1), end_date=datetime.date(2026, 11, 30))
        self.assertEqual(pnl['totals']['revenue'], Decimal("8000.00")) # 5000 + 3000

        rec_rows, rec_summary = get_receivables_data()
        self.assertEqual(rec_summary['total_outstanding'], Decimal("3000.00"))
        self.assertEqual(sum(r['invoice_amount'] for r in rec_rows), Decimal("5000.00")) # Only open inv1 is in receivables

        dashboard = get_dashboard_data(period='this_month')
        self.assertEqual(dashboard['kpis']['receivables']['value'], Decimal("3000.00"))

        # Check pages render without error and show AED amounts
        self.client.force_login(self.accountant_user)
        res_proj = self.client.get(reverse('fin_projects'))
        self.assertEqual(res_proj.status_code, 200)

        res_rec = self.client.get(reverse('fin_receivables'))
        self.assertEqual(res_rec.status_code, 200)
        self.assertContains(res_rec, "3000.00")

"""
Create (or remove) multi-currency DEMO data in the LOCAL database so the finance pages can be checked.

    python manage.py seed_currency_demo            # create the demo data
    python manage.py seed_currency_demo --delete   # remove everything this command created

Creates 4 projects (AED, USD, INR, EUR). Each gets: a work order, an invoice of 10,000 in its
currency (credit 1,000, payment 4,000), a spare (bought 500, sold 800), a flight job cost of 1,000
and a project expense of 1,000. Everything is named "FX DEMO …" so it is easy to find and remove.
Invoice / cost dates are today, so the default "this month" views show them.

Do NOT run this on the live server.
"""
import datetime
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from Admin.models import Project
from finance.models import Invoice, JobCost, SpareSale, Expense, ExpenseCategory, ExchangeRate
from finance.currency import get_rate

TAG = 'FX DEMO'
DEMO_RATES = {'INR': Decimal('0.044000'), 'EUR': Decimal('4.000000')}  # used only if no rate exists yet
CURRENCIES = ['AED', 'USD', 'INR', 'EUR']


class Command(BaseCommand):
    help = "Create or delete multi-currency demo data (local testing only)."

    def add_arguments(self, parser):
        parser.add_argument('--delete', action='store_true', help='Remove all FX DEMO data.')

    def handle(self, *args, **opts):
        if not settings.DEBUG:
            raise CommandError("Refusing to run with DEBUG=False (this looks like a live server).")
        if opts['delete']:
            return self.delete()
        if not getattr(settings, 'FINANCE_MULTI_CURRENCY', False):
            self.stdout.write(self.style.WARNING(
                "FINANCE_MULTI_CURRENCY is False: data will be created, but the forms will only offer AED. "
                "Set it to True in Al_Mabrouk_crm/settings.py to see the currency dropdowns."))
        if Project.objects.filter(name__startswith=TAG).exists():
            raise CommandError("Demo data already exists. Run with --delete first.")
        self.create()

    @transaction.atomic
    def create(self):
        today = timezone.localdate()
        user = User.objects.filter(is_superuser=True).first()

        # Rates: use existing ones; add a DEMO rate only when a currency has none for today.
        for cur, rate in DEMO_RATES.items():
            if get_rate(cur, today) is None:
                ExchangeRate.objects.create(currency=cur, rate_to_aed=rate, effective_from=datetime.date(today.year, 1, 1),
                                            notes=f'{TAG} rate', is_active=True, created_by=user)
                self.stdout.write(f"  added {TAG} rate {cur} = {rate}")
        rates = {c: get_rate(c, today) for c in CURRENCIES}

        cat = ExpenseCategory.objects.filter(kind='PROJECT', is_active=True).order_by('sort_order').first()
        if not cat:
            raise CommandError("No active PROJECT expense category found.")

        try:
            from Admin.work_order_sync import sync_work_order_from_project
        except ImportError:
            sync_work_order_from_project = None

        last = (Invoice.objects.filter(invoice_number__startswith=f'AMMS-INV-{today:%y}-9')
                .order_by('-invoice_number').values_list('invoice_number', flat=True).first())
        seq = int(last.split('-')[3]) + 1 if last else 9900

        rows = []
        for cur in CURRENCIES:
            r = rates[cur]
            p = Project.objects.create(
                name=f'{TAG} {cur}', client_name=f'{TAG} Client {cur}', vessel_name=f'MV {TAG} {cur}',
                service_reference=f'FXD-{cur}', currency_code=cur, status='ONGOING', category='AT_BERTH',
                priority='MEDIUM', job_description=f'{TAG} project in {cur}')
            if sync_work_order_from_project:
                sync_work_order_from_project(p, {'location': 'DMC', 'job_scope': f'{TAG} scope'}, None, user)

            inv = Invoice.objects.create(
                invoice_number=f'AMMS-INV-{today:%y}-{seq:04d}', invoice_date=today, project=p,
                service_ref=p.service_reference, vessel=p.vessel_name, customer=p.client_name,
                invoice_type='SERVICE_JOB', currency=cur, exchange_rate=r,
                amount_excl_vat=Decimal('10000'), vat_amount=Decimal('500') if cur == 'AED' else Decimal('0'),
                credits_applied=Decimal('1000'), amount_received=Decimal('4000'), payment_received_date=today,
                notes=f'{TAG} invoice', created_by=user)
            seq += 1
            p.amount_received = inv.amount_received_aed
            p.save(update_fields=['amount_received'])

            JobCost.objects.create(invoice=inv, cost_type='FLIGHT', amount=Decimal('1000'), currency=cur, exchange_rate=r,
                                   description=f'{TAG} flight', date=today, source='MANUAL')
            SpareSale.objects.create(service_ref=p.service_reference, vessel_customer=p.vessel_name, project=p, invoice=inv,
                                     source='SERVICE_JOB', purchase_cost=Decimal('500'), purchase_currency=cur, purchase_rate=r,
                                     selling_price=Decimal('800'), selling_currency=cur, selling_rate=r, status='INVOICED',
                                     notes=f'{TAG} spare')
            Expense.objects.create(date=today, category=cat, amount=Decimal('1000'), currency=cur, exchange_rate=r,
                                   vat_amount=Decimal('0'), description=f'{TAG} expense', project=p)
            inv.refresh_from_db()
            rows.append((cur, r, inv))

        self.stdout.write(self.style.SUCCESS(f"\nCreated {TAG} data for {today:%d/%m/%Y}. Expected values (AED):"))
        self.stdout.write(f"{'Cur':<4} {'Rate':>9} {'Invoice':<17} {'Revenue':>11} {'Received':>11} {'Outstanding':>12} {'Spares':>9} {'Flight':>9} {'Profit':>11}")
        tot = Decimal('0')
        for cur, r, inv in rows:
            spare = SpareSale.objects.get(invoice=inv).purchase_cost_aed
            flight = JobCost.objects.get(invoice=inv).amount_aed
            profit = inv.collection_value_aed - spare - flight
            tot += inv.collection_value_aed
            self.stdout.write(f"{cur:<4} {r:>9.4f} {inv.invoice_number:<17} {inv.collection_value_aed:>11,.2f} "
                              f"{inv.amount_received_aed:>11,.2f} {inv.outstanding_aed:>12,.2f} {spare:>9,.2f} {flight:>9,.2f} {profit:>11,.2f}")
        self.stdout.write(f"Total revenue (AED): {tot:,.2f}")
        self.stdout.write("Pages: /finance/  /finance/invoices/  /finance/receivables/  /finance/projects/  /finance/pnl/  "
                          "/finance/jobs-pnl/  /finance/spares/  /finance/expenses/  /service-logs/analytics/projects/")
        self.stdout.write("Remove later with:  python manage.py seed_currency_demo --delete")

    @transaction.atomic
    def delete(self):
        projects = Project.objects.filter(name__startswith=TAG)
        n_projects = projects.count()
        invs = Invoice.objects.filter(project__in=projects)
        n = dict(
            spares=SpareSale.objects.filter(project__in=projects).delete()[0],
            job_costs=JobCost.objects.filter(invoice__in=invs).delete()[0],
            expenses=Expense.objects.filter(project__in=projects).delete()[0],
            invoices=invs.delete()[0],
            rates=ExchangeRate.objects.filter(notes=f'{TAG} rate').delete()[0],
        )
        projects.delete()  # also removes their work orders
        n['projects'] = n_projects
        self.stdout.write(self.style.SUCCESS(f"Removed {TAG} data: {n}"))

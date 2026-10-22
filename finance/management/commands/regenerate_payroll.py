from datetime import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from finance.models import Payroll
from finance.services import generate_payroll, get_payroll_list_with_metrics


def _snapshot(month_first_day):
    """Return {employee_id: dict} of the values we report on, for one month."""
    payrolls, _, _ = get_payroll_list_with_metrics(month_first_day)
    snap = {}
    for p in payrolls:
        snap[p.employee_id] = {
            'name': p.employee.user.get_full_name() or p.employee.user.username,
            'status': p.status,
            'unpaid_days': p.absent_days_unpaid or Decimal('0.00'),
            'absence_deduction': p.effective_absence_deduction,
            'ot_hours': p.ot_hours or Decimal('0.00'),
            'ot_amount': p.ot_amount or Decimal('0.00'),
            'net_pay': p.net_pay,
        }
    return snap


class Command(BaseCommand):
    help = (
        "Regenerate DRAFT payroll rows for a month (absence deduction, OT hours/amount). "
        "APPROVED and PAID rows are never changed. Use --dry-run to preview."
    )

    def add_arguments(self, parser):
        parser.add_argument('--month', required=True, help='Month as YYYY-MM, e.g. 2026-09')
        parser.add_argument('--dry-run', action='store_true', dest='dry_run',
                            help='Show the changes without saving them.')

    def handle(self, *args, **options):
        try:
            month = datetime.strptime(options['month'], '%Y-%m').date().replace(day=1)
        except ValueError:
            raise CommandError("--month must look like 2026-09")
        dry_run = options.get('dry_run', False)

        with transaction.atomic():
            before = _snapshot(month)
            created, updated, skipped = generate_payroll(month, None)
            after = _snapshot(month)

            self.stdout.write(f"Payroll {month.strftime('%B %Y')}{' (DRY RUN)' if dry_run else ''}")
            for emp_id, new in sorted(after.items(), key=lambda kv: kv[1]['name'].lower()):
                old = before.get(emp_id)
                if new['status'] in ('APPROVED', 'PAID'):
                    self.stdout.write(f"  {new['name']}: skipped ({new['status'].lower()})")
                    continue
                if old is None:
                    self.stdout.write(
                        f"  {new['name']}: NEW draft | unpaid days {new['unpaid_days']} | "
                        f"absence ded. {new['absence_deduction']} | OT {new['ot_hours']}h / {new['ot_amount']} | "
                        f"net {new['net_pay']}"
                    )
                    continue
                self.stdout.write(
                    f"  {new['name']}: unpaid days {old['unpaid_days']} -> {new['unpaid_days']} | "
                    f"absence ded. {old['absence_deduction']} -> {new['absence_deduction']} | "
                    f"OT hours {old['ot_hours']} -> {new['ot_hours']} | "
                    f"OT amount {old['ot_amount']} -> {new['ot_amount']} | "
                    f"net {old['net_pay']} -> {new['net_pay']}"
                )

            self.stdout.write(f"Created {created}, updated {updated}, skipped {skipped} (approved/paid).")
            if dry_run:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("Dry run: no changes saved."))
            else:
                self.stdout.write(self.style.SUCCESS("Saved."))

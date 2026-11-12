import datetime
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from Admin.leave_service import accrue_leave_for_month, grant_entitlements_for_year


class Command(BaseCommand):
    help = "Accrues monthly annual leave for eligible employees (D7) and grants yearly entitlements."

    def add_arguments(self, parser):
        parser.add_argument(
            '--month',
            type=str,
            help="Month to accrue for in YYYY-MM format (e.g. 2026-03). Defaults to current month."
        )
        parser.add_argument(
            '--entitlements-for',
            type=int,
            help="Year to grant annual entitlements for (e.g. 2026) for leave types with yearly_entitlement > 0 and no accrual rule."
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Simulate accrual without writing ledger entries."
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        entitlements_year = options.get('entitlements_for')

        if entitlements_year:
            self.stdout.write(f"Granting annual entitlements for year {entitlements_year} (dry_run={dry_run})...")
            res = grant_entitlements_for_year(entitlements_year, dry_run=dry_run)
            for log in res.get('logs', []):
                self.stdout.write(f"  {log}")
            self.stdout.write(
                self.style.SUCCESS(
                    f"Completed annual entitlements for {entitlements_year}: "
                    f"processed={res['processed']}, credited={res['credited']}, skipped={res['skipped']}."
                )
            )
            return

        month_str = options.get('month')
        dry_run = options.get('dry_run', False)

        if month_str:
            try:
                dt = datetime.datetime.strptime(month_str, '%Y-%m').date()
                year = dt.year
                month = dt.month
            except ValueError:
                raise CommandError("Invalid month format. Please use YYYY-MM (e.g. 2026-03).")
        else:
            today = timezone.localdate()
            year = today.year
            month = today.month

        self.stdout.write(f"Running leave accrual for {year:04d}-{month:02d} (dry_run={dry_run})...")
        res = accrue_leave_for_month(year, month, dry_run=dry_run)

        for log in res.get('logs', []):
            self.stdout.write(f"  {log}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Completed leave accrual for {year:04d}-{month:02d}: "
                f"processed={res['processed']}, credited={res['credited']}, skipped={res['skipped']}."
            )
        )


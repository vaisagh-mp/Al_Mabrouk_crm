from django.core.management.base import BaseCommand, CommandError
from Admin.leave_service import close_leave_year_for_all


class Command(BaseCommand):
    help = "Performs year-end leave close, carrying forward up to 5 days and flagging excess for HR review (D10)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--year',
            type=int,
            required=True,
            help="Year to close (e.g. 2026)."
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Simulate year close without writing ledger entries or reviews."
        )

    def handle(self, *args, **options):
        year = options['year']
        dry_run = options.get('dry_run', False)

        self.stdout.write(f"Running year-end close for year {year} (dry_run={dry_run})...")
        res = close_leave_year_for_all(year, dry_run=dry_run)

        for log in res.get('logs', []):
            self.stdout.write(f"  {log}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Completed year close for {year}: "
                f"processed={res['processed']}, reviews_created={res['reviews_created']}."
            )
        )


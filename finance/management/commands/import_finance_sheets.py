import os
from django.core.management.base import BaseCommand, CommandError
from finance.services import import_finance_sheets_data


class Command(BaseCommand):
    help = "Imports Google Sheets data for Invoices, Jobs P&L, and Spares Accounting."

    def add_arguments(self, parser):
        parser.add_argument('--invoices', type=str, help='Path to Invoice Costing .xlsx or .csv file')
        parser.add_argument('--jobs', type=str, help='Path to Jobs P&L .xlsx or .csv file')
        parser.add_argument('--spares', type=str, help='Path to Spares Accounting .xlsx or .csv file')
        parser.add_argument('--invoices-sheet', type=str, default=None, help='Worksheet name for Invoice Costing')
        parser.add_argument('--jobs-sheet', type=str, default=None, help='Worksheet name for Jobs P&L')
        parser.add_argument('--spares-sheet', type=str, default=None, help='Worksheet name for Spares Accounting')
        parser.add_argument('--dry-run', action='store_true', default=False, help='Simulate the import and roll back all changes')

    def handle(self, *args, **options):
        invoices_path = options.get('invoices')
        jobs_path = options.get('jobs')
        spares_path = options.get('spares')
        invoices_sheet = options.get('invoices_sheet')
        jobs_sheet = options.get('jobs_sheet')
        spares_sheet = options.get('spares_sheet')
        dry_run = options.get('dry_run', False)

        for path, name in [(invoices_path, '--invoices'), (jobs_path, '--jobs'), (spares_path, '--spares')]:
            if path and not os.path.exists(path):
                raise CommandError(f"File specified for {name} does not exist: {path}")

        if not any([invoices_path, jobs_path, spares_path]):
            self.stdout.write(self.style.WARNING("No files provided. Specify at least one of --invoices, --jobs, or --spares."))
            return

        self.stdout.write("Starting finance sheets import...")
        if dry_run:
            self.stdout.write(self.style.NOTICE("[DRY RUN] All database transactions will be rolled back."))

        res = import_finance_sheets_data(
            invoices_file=invoices_path,
            jobs_file=jobs_path,
            spares_file=spares_path,
            invoices_sheet=invoices_sheet,
            jobs_sheet=jobs_sheet,
            spares_sheet=spares_sheet,
            dry_run=dry_run,
        )

        self.stdout.write(self.style.SUCCESS(
            f"SUCCESS: Import completed.\n"
            f"Created Invoices: {res['created_invoices']}\n"
            f"Updated Invoices: {res['updated_invoices']}\n"
            f"Created Job Costs: {res['created_job_costs']}\n"
            f"Created Spares: {res['created_spares']}\n"
            f"Updated Spares: {res['updated_spares']}\n"
            f"Skipped: {res['skipped']}"
        ))

        if res.get('warnings'):
            self.stdout.write(self.style.WARNING(f"\nWarnings encountered ({len(res['warnings'])}):"))
            for warn in res['warnings']:
                self.stdout.write(self.style.WARNING(f"  - {warn}"))

        if res['errors']:
            self.stdout.write(self.style.ERROR(f"\nErrors encountered ({len(res['errors'])}):"))
            for err in res['errors']:
                self.stdout.write(self.style.ERROR(f"  - {err}"))

        if dry_run:
            self.stdout.write(self.style.NOTICE("\n[DRY RUN] Rolled back all changes."))


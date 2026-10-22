from decimal import Decimal
from datetime import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from service_logs.models import EmployeeServiceLog
from service_logs.ot import recalculate_day_ot


class Command(BaseCommand):
    help = "Recalculate daily overtime (OT) and costs for EmployeeServiceLog rows based on new 10h threshold rules."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            dest='dry_run',
            help='Run recalculation without committing changes to the database.',
        )
        parser.add_argument(
            '--from',
            type=str,
            dest='from_date',
            help='Start date (YYYY-MM-DD) inclusive.',
        )
        parser.add_argument(
            '--to',
            type=str,
            dest='to_date',
            help='End date (YYYY-MM-DD) inclusive.',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        from_date_str = options.get('from_date')
        to_date_str = options.get('to_date')

        from_date = None
        to_date = None
        if from_date_str:
            try:
                from_date = datetime.strptime(from_date_str, '%Y-%m-%d').date()
            except ValueError:
                self.stderr.write(self.style.ERROR(f"Invalid --from date format: {from_date_str}. Use YYYY-MM-DD."))
                return

        if to_date_str:
            try:
                to_date = datetime.strptime(to_date_str, '%Y-%m-%d').date()
            except ValueError:
                self.stderr.write(self.style.ERROR(f"Invalid --to date format: {to_date_str}. Use YYYY-MM-DD."))
                return

        qs = EmployeeServiceLog.objects.all()
        if from_date:
            qs = qs.filter(date__gte=from_date)
        if to_date:
            qs = qs.filter(date__lte=to_date)

        pairs = list(qs.values_list('employee_id', 'date').distinct().order_by('date', 'employee_id'))

        pairs_processed = 0
        logs_changed = 0
        ot_hours_before = Decimal('0.00')
        ot_hours_after = Decimal('0.00')
        ot_cost_before = Decimal('0.00')
        ot_cost_after = Decimal('0.00')

        with transaction.atomic():
            for emp_id, day in pairs:
                # Get current state of logs for this (employee, day)
                logs_before = {
                    l.id: (
                        l.ot_hours or Decimal('0.00'),
                        l.ot_cost or Decimal('0.00'),
                        l.ot_type,
                        l.normal_cost or Decimal('0.00'),
                    )
                    for l in EmployeeServiceLog.objects.filter(employee_id=emp_id, date=day)
                }

                recalculate_day_ot(emp_id, day)

                logs_after = {
                    l.id: (
                        l.ot_hours or Decimal('0.00'),
                        l.ot_cost or Decimal('0.00'),
                        l.ot_type,
                        l.normal_cost or Decimal('0.00'),
                    )
                    for l in EmployeeServiceLog.objects.filter(employee_id=emp_id, date=day)
                }

                for lid, b_vals in logs_before.items():
                    a_vals = logs_after.get(lid, (Decimal('0.00'), Decimal('0.00'), None, Decimal('0.00')))
                    ot_hours_before += b_vals[0]
                    ot_hours_after += a_vals[0]
                    ot_cost_before += b_vals[1]
                    ot_cost_after += a_vals[1]
                    if b_vals != a_vals:
                        logs_changed += 1

                pairs_processed += 1

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(f"Pairs processed: {pairs_processed}")
        self.stdout.write(f"Logs changed: {logs_changed}")
        self.stdout.write(f"OT hours: {ot_hours_before:.2f} → {ot_hours_after:.2f}")
        self.stdout.write(f"OT cost: {ot_cost_before:.2f} → {ot_cost_after:.2f}")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run completed: all changes rolled back."))
        else:
            self.stdout.write(self.style.SUCCESS("Recalculation committed successfully."))

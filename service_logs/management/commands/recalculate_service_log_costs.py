from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from Admin.models import Project
from service_logs.models import EmployeeServiceLog
from service_logs.ot import recalculate_day_ot


class Command(BaseCommand):
    help = "Recalculate service log OT hours and costs for a given project."

    def add_arguments(self, parser):
        parser.add_argument(
            '--project',
            type=int,
            required=True,
            help='ID of the project to recalculate service logs for.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            dest='dry_run',
            help='Simulate recalculation without committing changes to the database.',
        )

    def handle(self, *args, **options):
        project_id = options.get('project') or options.get('project_id')
        dry_run = options.get('dry_run', False)

        try:
            project = Project.objects.get(id=project_id)
        except Project.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Project with ID {project_id} does not exist."))
            return

        logs = list(
            EmployeeServiceLog.objects.filter(project=project)
            .select_related('employee', 'employee__user')
            .order_by('date', 'start_time', 'id')
        )

        if not logs:
            self.stdout.write(f"No service logs found for project {project.name} (ID: {project_id}).")
            return

        # Record old values
        old_values = {}
        for log in logs:
            old_values[log.id] = {
                'ot_hours': log.ot_hours or Decimal('0.00'),
                'ot_cost': log.ot_cost or Decimal('0.00'),
                'normal_cost': log.normal_cost or Decimal('0.00'),
                'line_total_cost': log.line_total_cost,
            }

        emp_day_pairs = {(log.employee_id, log.date) for log in logs if log.employee_id and log.date}

        with transaction.atomic():
            for log in logs:
                log.save()

            for emp_id, day in emp_day_pairs:
                recalculate_day_ot(emp_id, day)

            for log in logs:
                log.refresh_from_db()
                old = old_values[log.id]
                emp_name = log.employee.user.username if (log.employee and log.employee.user) else 'Unknown'
                self.stdout.write(
                    f"Log #{log.id} ({log.date} - {emp_name}): "
                    f"OT Hours: {old['ot_hours']} -> {log.ot_hours}, "
                    f"OT Cost: {old['ot_cost']} -> {log.ot_cost}, "
                    f"Normal Cost: {old['normal_cost']} -> {log.normal_cost}, "
                    f"Total Cost: {old['line_total_cost']} -> {log.line_total_cost}"
                )

            if dry_run:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("Dry run completed: all changes rolled back."))
            else:
                self.stdout.write(self.style.SUCCESS(f"Recalculated {len(logs)} service logs for project {project.name}."))

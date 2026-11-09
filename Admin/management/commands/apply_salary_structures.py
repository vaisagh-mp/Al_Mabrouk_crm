from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from Admin.models import Employee, EmployeeChangeLog


class Command(BaseCommand):
    help = "Applies effective salary structures to employee.salary when effective_from <= today."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simulate updates without modifying database records.'
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        today = timezone.localdate()
        self.stdout.write(f"Applying salary structures as of {today} (dry_run={dry_run})...")

        updated_count = 0
        employees = Employee.objects.select_related('user').all()

        for employee in employees:
            curr_struct = employee.current_salary_structure
            if not curr_struct:
                continue

            current_total = Decimal(str(curr_struct.total))
            current_salary = Decimal(str(employee.salary or '0.00'))

            if current_salary != current_total:
                self.stdout.write(
                    f"Employee {employee.name} ({employee.employee_code}): "
                    f"salary {current_salary} -> {current_total}"
                )
                if not dry_run:
                    Employee.objects.filter(pk=employee.pk).update(salary=current_total)
                    employee.salary = current_total
                    EmployeeChangeLog.objects.create(
                        employee=employee,
                        field='salary',
                        old_value=str(current_salary),
                        new_value=str(current_total),
                        changed_by=None,
                        source='Scheduled salary change'
                    )
                updated_count += 1

        action_word = "Would update" if dry_run else "Successfully updated"
        self.stdout.write(self.style.SUCCESS(f"{action_word} {updated_count} employee(s)."))


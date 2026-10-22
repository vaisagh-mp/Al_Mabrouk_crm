from django.core.management.base import BaseCommand
from Admin.models import WorkOrderTime, Employee


class Command(BaseCommand):
    help = "Backfill WorkOrderTime employee when work order has exactly one assigned user; otherwise leave NULL."

    def handle(self, *args, **options):
        null_time_entries = WorkOrderTime.objects.filter(employee__isnull=True).select_related('work_order')
        filled = 0
        unknown = 0

        for t in null_time_entries:
            wo = t.work_order
            if wo and wo.job_assigned_to.count() == 1:
                assigned_user = wo.job_assigned_to.first()
                emp = getattr(assigned_user, 'employee_profile', None) or Employee.objects.filter(user=assigned_user).first()
                if emp:
                    t.employee = emp
                    t.save(update_fields=['employee'])
                    # If this WorkOrderTime has a linked service_log, update employee there as well
                    if hasattr(t, 'service_log') and t.service_log and not t.service_log.employee:
                        t.service_log.employee = emp
                        t.service_log.save(update_fields=['employee'])
                    filled += 1
                else:
                    unknown += 1
            else:
                unknown += 1

        self.stdout.write(f"Backfill complete: {filled} filled, {unknown} stay unknown.")

"""
services.py — Part 4
Auto-creates or updates an EmployeeServiceLog whenever a WorkOrderTime is saved.
Called from the post_save signal on WorkOrderTime.
"""
import logging
from django.db import transaction

logger = logging.getLogger(__name__)


def create_or_update_service_log(time_log):
    """
    Create or update the EmployeeServiceLog linked to `time_log`.
    Safe to call on every WorkOrderTime save — uses update_or_create to avoid duplicates.
    Failures are caught, logged, and do NOT block the WorkOrderTime save.
    """
    # Lazy import to avoid circular dependency at module load time
    from service_logs.models import EmployeeServiceLog
    from Admin.models import Holiday

    try:
        with transaction.atomic():
            # Guard: employee must be set on the time_log
            if time_log.employee is None:
                logger.warning(
                    "WorkOrderTime id=%s has no employee set — skipping service log.",
                    time_log.pk,
                )
                return

            employee = time_log.employee
            work_order = time_log.work_order
            project = getattr(work_order, 'project', None)

            # Derive field values
            service_reference = project.service_reference if project else ""

            vessel_name = (
                work_order.vessel
                or (project.vessel_name if project else "")
                or ""
            )
            port = work_order.location or ""

            service_type = 'JOB'

            location_type = (
                'ANCHORAGE'
                if (project and project.category == 'ANCHORAGE')
                else 'ANCHORAGE'
            )

            is_holiday = bool(
                Holiday.objects.filter(date=time_log.date).exists()
                or (project and project.category == 'HOLIDAY_WORKING')
            )

            defaults = {
                'employee': employee,
                'project': project,
                'date': time_log.date,
                'start_time': time_log.start_time,
                'end_time': time_log.finish_time,  # None until finish is saved
                'service_reference': service_reference,
                'vessel_name': vessel_name,
                'port': port,
                'service_type': service_type,
                'location_type': location_type,
                'is_holiday': is_holiday,
                'is_auto_created': True,
            }

            log, created = EmployeeServiceLog.objects.update_or_create(
                time_log=time_log,
                defaults=defaults,
            )

            action = "created" if created else "updated"
            logger.info(
                "Service log %s for employee=%s, time_log=%s",
                action, employee, time_log.pk,
            )
            return log

    except Exception as exc:
        logger.error(
            "Failed to create/update service log for WorkOrderTime id=%s: %s",
            getattr(time_log, 'pk', '?'),
            exc,
            exc_info=True,
        )
        # Do NOT re-raise — must not block the WorkOrderTime save

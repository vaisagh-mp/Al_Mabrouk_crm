import os
from datetime import datetime, timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.utils import timezone


def validate_timesheet_attachment(file):
    if not file:
        return
    ext = os.path.splitext(file.name)[1].lower()
    valid_extensions = ['.pdf', '.jpg', '.jpeg', '.png']
    if ext not in valid_extensions:
        raise ValidationError("Attachment must be a PDF, JPG, or PNG file.")
    if hasattr(file, 'size') and file.size > 5 * 1024 * 1024:
        raise ValidationError("Attachment file size cannot exceed 5 MB.")


def check_timesheet_overlap(employee_id, date, start_time, finish_time, exclude_time_log_id=None, exclude_service_log_id=None, exclude_work_order_id=None):
    """
    Check if the employee already has another WorkOrderTime or manual EmployeeServiceLog
    on the given date that overlaps in time with [start_time, finish_time].
    Returns error string if overlapping, or None if valid.
    """
    if not employee_id or not date or not start_time or not finish_time:
        return None

    dt_start = datetime.combine(date, start_time)
    dt_finish = datetime.combine(date, finish_time)
    if dt_finish < dt_start:
        dt_finish += timedelta(days=1)

    from Admin.models import WorkOrderTime
    from service_logs.models import EmployeeServiceLog

    # 1. Check WorkOrderTime
    wot_qs = WorkOrderTime.objects.filter(employee_id=employee_id, date=date).select_related('work_order')
    if exclude_time_log_id:
        wot_qs = wot_qs.exclude(pk=exclude_time_log_id)
    if exclude_service_log_id:
        wot_qs = wot_qs.exclude(service_log__id=exclude_service_log_id)
    if exclude_work_order_id:
        wot_qs = wot_qs.exclude(work_order_id=exclude_work_order_id)

    for row in wot_qs:
        if not row.start_time or not row.finish_time:
            continue
        r_start = datetime.combine(date, row.start_time)
        r_finish = datetime.combine(date, row.finish_time)
        if r_finish < r_start:
            r_finish += timedelta(days=1)

        # Overlap condition: start < other_finish and other_start < finish
        if dt_start < r_finish and r_start < dt_finish:
            wo_no = row.work_order.work_order_number if (row.work_order and row.work_order.work_order_number) else f"WO #{row.work_order_id}"
            return f"Overlaps with {wo_no} {row.start_time.strftime('%H:%M')}–{row.finish_time.strftime('%H:%M')}"

    # 2. Check manual EmployeeServiceLog
    sl_qs = EmployeeServiceLog.objects.filter(
        employee_id=employee_id, date=date, time_log__isnull=True
    ).exclude(end_time__isnull=True)
    if exclude_service_log_id:
        sl_qs = sl_qs.exclude(pk=exclude_service_log_id)

    for log in sl_qs:
        l_start = datetime.combine(date, log.start_time)
        l_finish = datetime.combine(date, log.end_time)
        if l_finish < l_start:
            l_finish += timedelta(days=1)

        if dt_start < l_finish and l_start < dt_finish:
            ref = log.service_reference or log.vessel_name or f"Log #{log.id}"
            return f"Overlaps with {ref} {log.start_time.strftime('%H:%M')}–{log.end_time.strftime('%H:%M')}"

    return None


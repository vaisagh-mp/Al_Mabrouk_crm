import logging
from datetime import datetime, date, timedelta
from decimal import Decimal
from collections import defaultdict
from django.conf import settings
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone
from Admin.models import Employee, Notification, EmployeeChangeLog, OTApproval, check_payroll_lock_for_employee
from service_logs.models import EmployeeServiceLog

logger = logging.getLogger(__name__)


def notify_user_or_group(recipients, message, notif_type='TIMESHEET', link='', related=None, actor=None):
    from Admin.notifications import notify
    notify(recipients, notif_type, message, link=link, related=related, actor=actor)


def can_manage_timesheet(user, employee):
    """
    Returns True if user has permission to approve/reject timesheet of employee.
    Superuser and HR can manage any employee (except self).
    Manager can manage reporting employees (except self).
    """
    if employee and employee.user_id == user.id:
        return False
    if user.is_superuser:
        return True
    emp_profile = getattr(user, 'employee_profile', None)
    if not emp_profile:
        return False
    if emp_profile.is_hr:
        return True
    if emp_profile.is_manager and employee and employee.reporting_manager_id == emp_profile.id:
        return True
    return False


def submit_service_log_for_approval(log, user=None):
    """
    Submits a draft/rejected service log for approval.
    """
    if log.employee:
        check_payroll_lock_for_employee(log.employee, log.date)

    log.approval_status = 'SUBMITTED'
    log.submitted_at = timezone.now()
    log.approver = log.employee.reporting_manager if log.employee else None
    log.decision_remarks = ''
    log.save(update_fields=['approval_status', 'submitted_at', 'approver', 'decision_remarks'])

    # Notify approver or Admin/HR queue
    from Admin.notifications import get_user_role_link, notify_role
    emp_name = log.employee.user.get_full_name() or log.employee.user.username if (log.employee and log.employee.user) else "Staff"
    msg = f"Timesheet for {emp_name} on {log.date} has been submitted for approval."
    if log.approver and log.approver.user:
        notify_user_or_group(
            [log.approver.user],
            msg,
            notif_type='TIMESHEET',
            link=get_user_role_link(log.approver.user, 'timesheet_approvals', query='status=SUBMITTED'),
            related=log,
            actor=user,
        )
    else:
        notify_role(
            ['HR', 'ADMIN'],
            'TIMESHEET',
            msg,
            link=lambda u: get_user_role_link(u, 'timesheet_approvals', query='status=SUBMITTED'),
            related=log,
            actor=user,
        )

    return log


def models_Q_admin_hr():
    from django.db.models import Q
    return Q(is_superuser=True) | Q(employee_profile__is_hr=True)


def approve_service_log(log, user):
    """
    Approves a service log. Server-side checks: nobody approves own.
    """
    if not can_manage_timesheet(user, log.employee):
        if log.employee and log.employee.user_id == user.id:
            raise PermissionDenied("You cannot approve your own timesheet.")
        raise PermissionDenied("You do not have permission to approve this timesheet.")

    if log.employee:
        check_payroll_lock_for_employee(log.employee, log.date)

    log.approval_status = 'APPROVED'
    log.decided_by = user
    log.decided_at = timezone.now()
    log.decision_remarks = ''
    log.save(update_fields=['approval_status', 'decided_by', 'decided_at', 'decision_remarks'])

    if log.employee and log.employee.user:
        from Admin.notifications import get_user_role_link
        wo_id = log.time_log.work_order_id if log.time_log_id else None
        notify_user_or_group(
            [log.employee.user],
            f"Your timesheet for {log.date} was approved.",
            notif_type='TIMESHEET',
            link=get_user_role_link(log.employee.user, 'timesheet_my', obj_id=wo_id),
            related=log,
            actor=user,
        )

    sync_ot_approval_for_day(log.employee_id, log.date)
    return log


def reject_service_log(log, user, remarks):
    """
    Rejects a service log. Remarks required.
    """
    if not remarks or not remarks.strip():
        raise ValidationError("Remarks are required when rejecting a timesheet.")

    if not can_manage_timesheet(user, log.employee):
        if log.employee and log.employee.user_id == user.id:
            raise PermissionDenied("You cannot reject your own timesheet.")
        raise PermissionDenied("You do not have permission to reject this timesheet.")

    if log.employee:
        check_payroll_lock_for_employee(log.employee, log.date)

    log.approval_status = 'REJECTED'
    log.decision_remarks = remarks.strip()
    log.decided_by = user
    log.decided_at = timezone.now()
    log.save(update_fields=['approval_status', 'decision_remarks', 'decided_by', 'decided_at'])

    if log.employee and log.employee.user:
        from Admin.notifications import get_user_role_link
        wo_id = log.time_log.work_order_id if log.time_log_id else None
        notify_user_or_group(
            [log.employee.user],
            f"Your timesheet for {log.date} was rejected: {remarks.strip()}",
            notif_type='TIMESHEET',
            link=get_user_role_link(log.employee.user, 'timesheet_my', obj_id=wo_id),
            related=log,
            actor=user,
        )

    sync_ot_approval_for_day(log.employee_id, log.date)
    return log


def reopen_service_log(log, user, reason):
    """
    Reopens an APPROVED service log back to DRAFT.
    Admin or HR only. Reason required. Logged in EmployeeChangeLog.
    """
    emp_profile = getattr(user, 'employee_profile', None)
    is_hr = emp_profile.is_hr if emp_profile else False
    if not (user.is_superuser or is_hr):
        raise PermissionDenied("Only Admin or HR can reopen an approved timesheet.")

    if not getattr(settings, 'HR_TIMESHEET_APPROVAL', False):
        raise ValidationError("Timesheet approval is switched off — cannot reopen timesheet to Draft.")

    if not reason or not reason.strip():
        raise ValidationError("A reason is required to reopen an approved timesheet.")

    if log.employee:
        check_payroll_lock_for_employee(log.employee, log.date)

    if log.approval_status != 'APPROVED':
        raise ValidationError("Only APPROVED timesheets can be reopened.")

    log.approval_status = 'DRAFT'
    log.decision_remarks = f"Reopened by {user.get_full_name() or user.username}: {reason.strip()}"
    log.decided_by = None
    log.decided_at = None
    log.save(update_fields=['approval_status', 'decision_remarks', 'decided_by', 'decided_at'])

    EmployeeChangeLog.objects.create(
        employee=log.employee,
        field='timesheet_reopened',
        old_value='APPROVED',
        new_value=f'DRAFT: {reason.strip()}',
        changed_by=user,
        source=f'Timesheet Log #{log.id} ({log.date})',
    )

    sync_ot_approval_for_day(log.employee_id, log.date)
    return log


def sync_ot_approval_for_day(employee_id, day):
    """
    Computes overtime for employee on day from approved logs (+ office OT on those days
    when all vessel logs are approved). Syncs the OTApproval model.
    If day's OT hours change after approval, status reverts to PENDING automatically.
    """
    if not employee_id or not day:
        return None

    emp = Employee.objects.filter(pk=employee_id).select_related('user', 'branch').first()
    if not emp:
        return None

    logs = list(
        EmployeeServiceLog.objects.filter(employee_id=employee_id, date=day)
        .order_by('start_time', 'id')
    )
    if not logs:
        OTApproval.objects.filter(employee_id=employee_id, date=day).delete()
        return None

    counting_logs = [l for l in logs if l.is_counting()]
    if not counting_logs:
        OTApproval.objects.filter(employee_id=employee_id, date=day).delete()
        return None

    from service_logs.ot import split_day_ot, calculate_office_ot_cost, employee_office_attendances
    attendances = employee_office_attendances(employee_id, day)

    all_vessel_approved = (len(counting_logs) == len(logs))
    split_info = split_day_ot(attendances, counting_logs, day)

    vessel_ot_map = split_info['vessel_ot_by_log_id']
    vessel_ot_hours = sum(vessel_ot_map.values(), Decimal('0.00')).quantize(Decimal('0.01'))
    office_ot_hours = split_info['office_ot_hours'] if all_vessel_approved else Decimal('0.00')

    day_ot_hours = (vessel_ot_hours + office_ot_hours).quantize(Decimal('0.01'))

    vessel_ot_cost = sum((l.ot_cost or Decimal('0.00')) for l in counting_logs if l.id in vessel_ot_map)
    office_ot_cost = calculate_office_ot_cost(
        emp.salary,
        [{'date': day, 'office_ot_hours': office_ot_hours, 'employee': emp}],
        counting_logs,
        employee=emp
    ) if all_vessel_approved else Decimal('0.00')

    day_ot_cost = (vessel_ot_cost + office_ot_cost).quantize(Decimal('0.01'))

    if day_ot_hours > Decimal('0.00'):
        approval_from = getattr(settings, 'HR_TIMESHEET_APPROVAL_FROM', None)
        auto_approved = bool(approval_from and day < approval_from)

        ot_app = OTApproval.objects.filter(employee_id=employee_id, date=day).first()
        if ot_app:
            if ot_app.ot_hours != day_ot_hours or ot_app.ot_cost != day_ot_cost:
                # Changes after approval revert to PENDING
                ot_app.ot_hours = day_ot_hours
                ot_app.ot_cost = day_ot_cost
                ot_app.status = 'APPROVED' if auto_approved else 'PENDING'
                ot_app.decided_by = None
                ot_app.decided_at = None
                ot_app.save()
        else:
            init_status = 'APPROVED' if auto_approved else 'PENDING'
            OTApproval.objects.create(
                employee=emp,
                date=day,
                ot_hours=day_ot_hours,
                ot_cost=day_ot_cost,
                status=init_status,
            )
    else:
        OTApproval.objects.filter(employee_id=employee_id, date=day).delete()


def approve_ot_day(ot_app, user):
    """
    Approves an OTApproval record for an employee-day.
    """
    if not can_manage_timesheet(user, ot_app.employee):
        if ot_app.employee and ot_app.employee.user_id == user.id:
            raise PermissionDenied("You cannot approve your own overtime.")
        raise PermissionDenied("You do not have permission to approve this overtime.")

    if ot_app.employee:
        check_payroll_lock_for_employee(ot_app.employee, ot_app.date)

    ot_app.status = 'APPROVED'
    ot_app.decided_by = user
    ot_app.decided_at = timezone.now()
    ot_app.save(update_fields=['status', 'decided_by', 'decided_at'])

    if ot_app.employee and ot_app.employee.user:
        from Admin.notifications import notify, get_user_role_link
        notify(
            ot_app.employee.user,
            'OT',
            f"Your overtime ({ot_app.ot_hours} hrs) for {ot_app.date.strftime('%d/%m/%Y')} was approved.",
            link=get_user_role_link(ot_app.employee.user, 'timesheet_my'),
            related=ot_app,
            actor=user,
        )
    return ot_app


def reject_ot_day(ot_app, user, remarks):
    """
    Rejects an OTApproval record for an employee-day. Remarks required.
    """
    if not remarks or not remarks.strip():
        raise ValidationError("Remarks are required when rejecting overtime.")

    if not can_manage_timesheet(user, ot_app.employee):
        if ot_app.employee and ot_app.employee.user_id == user.id:
            raise PermissionDenied("You cannot reject your own overtime.")
        raise PermissionDenied("You do not have permission to reject this overtime.")

    if ot_app.employee:
        check_payroll_lock_for_employee(ot_app.employee, ot_app.date)

    ot_app.status = 'REJECTED'
    ot_app.remarks = remarks.strip()
    ot_app.decided_by = user
    ot_app.decided_at = timezone.now()
    ot_app.save(update_fields=['status', 'remarks', 'decided_by', 'decided_at'])

    if ot_app.employee and ot_app.employee.user:
        from Admin.notifications import notify, get_user_role_link
        notify(
            ot_app.employee.user,
            'OT',
            f"Your overtime for {ot_app.date.strftime('%d/%m/%Y')} was rejected: {remarks.strip()}",
            link=get_user_role_link(ot_app.employee.user, 'timesheet_my'),
            related=ot_app,
            actor=user,
        )
    return ot_app


def approve_all_ot_for_month(month_date, user, employee_id=None):
    """
    Bulk approve all PENDING OTApproval records in month_date for allowed employees.
    """
    first_day = month_date.replace(day=1)
    from calendar import monthrange
    last_day = month_date.replace(day=monthrange(month_date.year, month_date.month)[1])

    qs = OTApproval.objects.filter(date__range=(first_day, last_day), status='PENDING')
    if employee_id:
        qs = qs.filter(employee_id=employee_id)

    # Exclude user's own rows
    if hasattr(user, 'employee_profile'):
        qs = qs.exclude(employee_id=user.employee_profile.id)

    # If manager and not HR/Admin: filter to reports
    emp_profile = getattr(user, 'employee_profile', None)
    if not user.is_superuser and emp_profile and not emp_profile.is_hr:
        qs = qs.filter(employee__reporting_manager=emp_profile)

    now = timezone.now()
    count = 0
    for ot_app in qs:
        try:
            check_payroll_lock_for_employee(ot_app.employee, ot_app.date)
            ot_app.status = 'APPROVED'
            ot_app.decided_by = user
            ot_app.decided_at = now
            ot_app.save(update_fields=['status', 'decided_by', 'decided_at'])
            count += 1
        except ValidationError:
            continue

    return count


def sync_month_ot_approvals(start_date, end_date):
    """
    Ensure all employee-days with service logs in the date range have their OTApproval synced.
    """
    emp_days = (
        EmployeeServiceLog.objects.filter(date__gte=start_date, date__lte=end_date)
        .values_list('employee_id', 'date')
        .distinct()
    )
    for emp_id, day in emp_days:
        sync_ot_approval_for_day(emp_id, day)


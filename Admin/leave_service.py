import io
import datetime
from decimal import Decimal
from collections import defaultdict
import openpyxl

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from Admin.models import (
    Employee, Leave, LeaveType, LeavePolicy, LeaveLedger, LeaveYearReview,
    Notification, EmployeeChangeLog, check_payroll_lock_for_employee
)
from Admin.attendance_engine import rest_day_map, public_holiday_map


def is_leave_v2_active():
    """Returns True if the HR_LEAVE_V2 feature switch is on."""
    return getattr(settings, 'HR_LEAVE_V2', False)


def add_months(sourcedate, months):
    """Safely adds months to a date."""
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    import calendar
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def calculate_leave_working_days(employee, from_date, to_date, is_half_day=False, leave_type=None):
    """
    Computes scheduled working days for an employee across [from_date, to_date].
    Uses Phase 2 engine: excludes employee's rest days and branch public holidays (D8).
    Returns (working_days_decimal, excluded_days_list).
    """
    if isinstance(from_date, str):
        from_date = datetime.datetime.strptime(from_date, '%Y-%m-%d').date()
    if isinstance(to_date, str):
        to_date = datetime.datetime.strptime(to_date, '%Y-%m-%d').date()

    if from_date > to_date:
        return Decimal('0.00'), []

    if is_half_day:
        if from_date != to_date:
            raise ValidationError("Half-day leave must be on a single date.")
        # Check if this single date is a rest day or public holiday
        rday_m = rest_day_map([employee], from_date, from_date)
        hol_m = public_holiday_map([employee], from_date, from_date)
        is_rest = rday_m.get((employee.id, from_date), from_date.weekday() == 6)
        is_hol = (employee.id, from_date) in hol_m

        if is_rest or is_hol:
            reason = "Public Holiday" if is_hol else "Rest Day"
            return Decimal('0.00'), [f"{from_date.strftime('%d/%m/%Y')} ({reason})"]
        return Decimal('0.50'), []

    # Multi-day or standard full day
    rday_m = rest_day_map([employee], from_date, to_date)
    hol_m = public_holiday_map([employee], from_date, to_date)

    working_days = Decimal('0.00')
    excluded_days = []

    curr_d = from_date
    while curr_d <= to_date:
        is_rest = rday_m.get((employee.id, curr_d), curr_d.weekday() == 6)
        is_hol = (employee.id, curr_d) in hol_m

        if is_hol:
            hol_obj = hol_m[(employee.id, curr_d)]
            hol_name = getattr(hol_obj, 'name', 'Holiday') if hol_obj else 'Holiday'
            excluded_days.append(f"{curr_d.strftime('%d/%m/%Y')} (Holiday: {hol_name})")
        elif is_rest:
            excluded_days.append(f"{curr_d.strftime('%d/%m/%Y')} (Rest Day)")
        else:
            working_days += Decimal('1.00')
        curr_d += datetime.timedelta(days=1)

    return working_days, excluded_days


def get_employee_leave_balance(employee, leave_type, as_of_date=None):
    """
    Returns total balance from ledger entries (sum of entries).
    """
    if isinstance(leave_type, str):
        leave_type = LeaveType.objects.filter(Q(code=leave_type) | Q(name__iexact=leave_type)).first()
    if not leave_type:
        return Decimal('0.00')

    qs = LeaveLedger.objects.filter(employee=employee, leave_type=leave_type)
    if as_of_date:
        qs = qs.filter(date__lte=as_of_date)

    total = qs.aggregate(s=Sum('days'))['s']
    return total if total is not None else Decimal('0.00')


def get_employee_pending_leave_days(employee, leave_type):
    """
    Returns total days in PENDING or CLARIFICATION status for this leave type.
    """
    if isinstance(leave_type, str):
        leave_type = LeaveType.objects.filter(Q(code=leave_type) | Q(name__iexact=leave_type)).first()
    if not leave_type:
        return Decimal('0.00')

    leaves = Leave.objects.filter(
        Q(employee=employee) | Q(user=employee.user),
        approval_status__in=['PENDING', 'CLARIFICATION']
    )
    if leave_type:
        leaves = leaves.filter(
            Q(leave_type_ref=leave_type) | Q(leave_type__iexact=leave_type.name) | Q(leave_type__iexact=leave_type.code)
        )

    tot = Decimal('0.00')
    for l in leaves:
        if l.days is not None:
            tot += l.days
        elif l.no_of_days is not None:
            tot += Decimal(str(l.no_of_days))
    return tot


def get_employee_available_leave(employee, leave_type, as_of_date=None):
    """
    Available days = Ledger Balance - Pending Days.
    """
    bal = get_employee_leave_balance(employee, leave_type, as_of_date=as_of_date)
    pending = get_employee_pending_leave_days(employee, leave_type)
    return max(Decimal('0.00'), bal - pending)


def get_employee_leave_summary(employee, year=None):
    """
    Returns breakdown per leave type for balance header / dashboard:
    Earned this year, Carried forward, Taken, Pending, Available, Total balance.
    """
    if year is None:
        year = timezone.localdate().year

    start_of_year = datetime.date(year, 1, 1)
    end_of_year = datetime.date(year, 12, 31)

    types = LeaveType.objects.filter(is_active=True).order_by('sort_order', 'name')
    summary_by_type = []

    for lt in types:
        ledger_qs = LeaveLedger.objects.filter(employee=employee, leave_type=lt)
        
        # Opening
        opening_qs = ledger_qs.filter(entry_type='OPENING')
        opening_days = opening_qs.aggregate(s=Sum('days'))['s'] or Decimal('0.00')

        # Accrued this year
        accrued_qs = ledger_qs.filter(entry_type='ACCRUAL', date__year=year)
        accrued_days = accrued_qs.aggregate(s=Sum('days'))['s'] or Decimal('0.00')

        # Entitlement this year (e.g. Sick Leave)
        entitlement_qs = ledger_qs.filter(entry_type='ENTITLEMENT', date__year=year)
        entitlement_days = entitlement_qs.aggregate(s=Sum('days'))['s'] or Decimal('0.00')

        # Carried forward this year
        carried_qs = ledger_qs.filter(entry_type='CARRY_FORWARD', date__year=year)
        carried_days = carried_qs.aggregate(s=Sum('days'))['s'] or Decimal('0.00')

        # Taken this year
        taken_qs = ledger_qs.filter(entry_type='TAKEN', date__year=year)
        taken_days = abs(taken_qs.aggregate(s=Sum('days'))['s'] or Decimal('0.00'))

        # Adjustments / Reversals
        adj_qs = ledger_qs.filter(entry_type__in=['ADJUSTMENT', 'REVERSAL'], date__year=year)
        adj_days = adj_qs.aggregate(s=Sum('days'))['s'] or Decimal('0.00')

        # Excess / Expired / Closed
        excess_qs = ledger_qs.filter(entry_type='YEAR_END_EXCESS', date__year=year)
        excess_days = abs(excess_qs.aggregate(s=Sum('days'))['s'] or Decimal('0.00'))

        expired_qs = ledger_qs.filter(entry_type='EXPIRED', date__year=year)
        expired_days = abs(expired_qs.aggregate(s=Sum('days'))['s'] or Decimal('0.00'))

        closed_qs = ledger_qs.filter(entry_type='YEAR_END_CLOSE', date__year=year)
        closed_days = abs(closed_qs.aggregate(s=Sum('days'))['s'] or Decimal('0.00'))

        # Net ledger balance
        net_balance = ledger_qs.aggregate(s=Sum('days'))['s'] or Decimal('0.00')

        # Pending
        pending_days = get_employee_pending_leave_days(employee, lt)
        available_days = max(Decimal('0.00'), net_balance - pending_days) if lt.uses_balance else Decimal('0.00')

        summary_by_type.append({
            'leave_type': lt,
            'code': lt.code,
            'name': lt.name,
            'uses_balance': lt.uses_balance,
            'is_paid': lt.is_paid,
            'opening_days': opening_days,
            'accrued_days': accrued_days,
            'entitlement_days': entitlement_days,
            'carried_days': carried_days,
            'taken_days': taken_days,
            'adj_days': adj_days,
            'excess_days': excess_days,
            'expired_days': expired_days,
            'closed_days': closed_days,
            'net_balance': net_balance,
            'pending_days': pending_days,
            'available_days': available_days,
        })

    return summary_by_type


def can_decide_leave(user, leave):
    """
    Approval Rules (D12 / Role permissions):
    - Superuser / HR can decide any request (override).
    - Direct reporting manager can decide direct report's request.
    - Nobody decides their own request (strict check).
    - Other managers cannot decide.
    """
    if not user or not user.is_authenticated:
        return False

    # Strict check: cannot self-decide
    leave_user = leave.employee.user if leave.employee else leave.user
    if leave_user == user:
        return False

    if user.is_superuser:
        return True

    user_emp = getattr(user, 'employee_profile', None)
    if not user_emp:
        return False

    if user_emp.is_hr:
        return True

    # Direct reporting manager
    if leave.employee and leave.employee.reporting_manager_id == user_emp.id:
        return True

    return False


def submit_leave_request(
    employee,
    leave_type_ref,
    from_date,
    to_date,
    reason,
    is_half_day=False,
    half_day_part=None,
    attachment=None,
    hr_override=False,
    hr_override_reason='',
    submitted_by=None
):
    """
    Creates and validates a new Leave request under V2 rules.
    """
    if isinstance(from_date, str):
        from_date = datetime.datetime.strptime(from_date, '%Y-%m-%d').date()
    if isinstance(to_date, str):
        to_date = datetime.datetime.strptime(to_date, '%Y-%m-%d').date()

    if to_date < from_date:
        raise ValidationError({'to_date': "To Date cannot be before From Date."})

    if employee.date_of_join and from_date < employee.date_of_join:
        raise ValidationError({'from_date': "Leave cannot start before employee's joining date."})

    # Payroll lock check
    check_payroll_lock_for_employee(employee, from_date, is_open_ended=False)
    if to_date.month != from_date.month or to_date.year != from_date.year:
        check_payroll_lock_for_employee(employee, to_date, is_open_ended=False)

    # Overlap check with own PENDING, CLARIFICATION, or APPROVED leave
    overlap_qs = Leave.objects.filter(
        Q(employee=employee) | Q(user=employee.user),
        approval_status__in=['PENDING', 'CLARIFICATION', 'APPROVED'],
        from_date__lte=to_date,
        to_date__gte=from_date
    )
    if overlap_qs.exists():
        first_ov = overlap_qs.first()
        raise ValidationError(f"Leave overlaps with existing {first_ov.get_approval_status_display()} leave from {first_ov.from_date.strftime('%d/%m/%Y')} to {first_ov.to_date.strftime('%d/%m/%Y')}.")

    # Half day checks
    if is_half_day:
        if not leave_type_ref.allows_half_day:
            raise ValidationError({'is_half_day': f"Half-day leave is not allowed for {leave_type_ref.name}."})
        if from_date != to_date:
            raise ValidationError({'to_date': "Half-day leave must be for a single date."})
        if not half_day_part or half_day_part not in ('AM', 'PM'):
            raise ValidationError({'half_day_part': "Please specify AM or PM for half-day leave."})

    # Calculate scheduled working days using engine (D8)
    working_days, excluded_days = calculate_leave_working_days(
        employee, from_date, to_date, is_half_day=is_half_day, leave_type=leave_type_ref
    )
    if working_days <= Decimal('0.00'):
        raise ValidationError("The selected dates contain only rest days or public holidays. No working days to deduct.")

    # Probation rule (D7)
    if leave_type_ref.code == 'ANNUAL':
        if employee.date_of_join:
            probation_end = add_months(employee.date_of_join, 6)
            if from_date < probation_end:
                raise ValidationError("Annual leave is available after 6 months of service — apply as Unpaid Leave (needs management approval).")

    # Attachment check (e.g. Sick leave >= certificate_required_after_days)
    if leave_type_ref.certificate_required_after_days > 0 and working_days >= leave_type_ref.certificate_required_after_days:
        if not attachment:
            raise ValidationError({'medical_certificate': f"A certificate attachment is required for {leave_type_ref.name} of {working_days} day(s) or more."})

    if attachment and hasattr(attachment, 'size') and attachment.size:
        if attachment.size > 5 * 1024 * 1024:
            raise ValidationError({'medical_certificate': "Attachment cannot exceed 5 MB."})
        ext = attachment.name.split('.')[-1].lower() if '.' in attachment.name else ''
        if ext not in ['pdf', 'jpg', 'jpeg', 'png']:
            raise ValidationError({'medical_certificate': "Only PDF, JPG, and PNG files are allowed as attachments."})

    # Balance check for balance types
    if leave_type_ref.uses_balance:
        avail = get_employee_available_leave(employee, leave_type_ref)
        if working_days > avail:
            can_override = False
            if submitted_by and (submitted_by.is_superuser or (hasattr(submitted_by, 'employee_profile') and submitted_by.employee_profile.is_hr)):
                can_override = True

            if can_override and hr_override:
                if not hr_override_reason or not hr_override_reason.strip():
                    raise ValidationError({'hr_override_reason': "A reason is required when HR overrides leave balance."})
                EmployeeChangeLog.objects.create(
                    employee=employee,
                    field='leave_balance_override',
                    old_value=str(avail),
                    new_value=f"{working_days} requested ({hr_override_reason.strip()})",
                    changed_by=submitted_by,
                    source='Leave balance override'
                )
            else:
                raise ValidationError(f"Insufficient leave balance. You have {avail} days available, but requested {working_days} days.")

    # Auto-assign approver to reporting manager
    approver = employee.reporting_manager

    leave = Leave(
        user=employee.user,
        employee=employee,
        leave_type=leave_type_ref.name.upper(),
        leave_type_ref=leave_type_ref,
        from_date=from_date,
        to_date=to_date,
        is_half_day=is_half_day,
        half_day_part=half_day_part if is_half_day else None,
        days=working_days,
        reason=reason,
        medical_certificate=attachment,
        approval_status='PENDING',
        approver=approver,
    )
    leave.save()

    # Notifications
    from Admin.notifications import notify, notify_role, get_user_role_link
    msg = f"New leave request submitted by {employee.name} for {working_days} day(s) ({leave_type_ref.name}) from {from_date.strftime('%d/%m/%Y')} to {to_date.strftime('%d/%m/%Y')}."
    if approver and approver.user:
        notify(
            approver.user,
            'LEAVE',
            msg,
            link=get_user_role_link(approver.user, 'leave_manage'),
            related=leave,
            actor=employee.user,
        )
    else:
        notify_role(
            ['HR', 'ADMIN'],
            'LEAVE',
            msg,
            link=lambda u: get_user_role_link(u, 'leave_manage'),
            related=leave,
            actor=employee.user,
        )

    return leave


def approve_leave_request(leave, decided_by, remarks=''):
    """
    Approves a leave request. Creates TAKEN entry in LeaveLedger for balance types.
    """
    if not can_decide_leave(decided_by, leave):
        raise PermissionDenied("You do not have permission to approve this leave request.")

    if leave.approval_status not in ('PENDING', 'CLARIFICATION'):
        raise ValidationError("This leave request is not pending decision.")

    check_payroll_lock_for_employee(leave.employee, leave.from_date, is_open_ended=False)
    if leave.to_date.month != leave.from_date.month:
        check_payroll_lock_for_employee(leave.employee, leave.to_date, is_open_ended=False)

    with transaction.atomic():
        leave = Leave.objects.select_for_update().get(pk=leave.pk)
        leave.approval_status = 'APPROVED'
        leave.decided_by = decided_by
        leave.decided_at = timezone.now()
        leave.decision_remarks = remarks
        leave.save()

        # If balance type, write TAKEN entry
        lt = leave.leave_type_ref
        if lt and lt.uses_balance:
            LeaveLedger.objects.create(
                employee=leave.employee,
                leave_type=lt,
                date=leave.from_date,
                entry_type='TAKEN',
                days=-abs(leave.days or Decimal('1.00')),
                leave=leave,
                note=f"Leave #{leave.id} ({leave.from_date.strftime('%d/%m/%Y')} to {leave.to_date.strftime('%d/%m/%Y')})",
                created_by=decided_by
            )

        # Notify employee
        from Admin.notifications import notify, get_user_role_link
        notify(
            leave.user,
            'LEAVE',
            f"Your {leave.leave_type} request for {leave.from_date.strftime('%d/%m/%Y')} has been APPROVED by {decided_by.username}.",
            link=get_user_role_link(leave.user, 'leave_status'),
            related=leave,
            actor=decided_by,
        )


def reject_leave_request(leave, decided_by, remarks):
    """
    Rejects a leave request. Requires non-empty remarks.
    """
    if not can_decide_leave(decided_by, leave):
        raise PermissionDenied("You do not have permission to reject this leave request.")

    if not remarks or not remarks.strip():
        raise ValidationError("Remarks are required when rejecting a leave request.")

    if leave.approval_status not in ('PENDING', 'CLARIFICATION'):
        raise ValidationError("This leave request is not pending decision.")

    with transaction.atomic():
        leave = Leave.objects.select_for_update().get(pk=leave.pk)
        leave.approval_status = 'REJECTED'
        leave.decided_by = decided_by
        leave.decided_at = timezone.now()
        leave.decision_remarks = remarks.strip()
        leave.save()

        # Notify employee
        from Admin.notifications import notify, get_user_role_link
        notify(
            leave.user,
            'LEAVE',
            f"Your {leave.leave_type} request for {leave.from_date.strftime('%d/%m/%Y')} was REJECTED: {remarks.strip()}",
            link=get_user_role_link(leave.user, 'leave_status'),
            related=leave,
            actor=decided_by,
        )


def request_leave_clarification(leave, user, message):
    """
    Sets status to CLARIFICATION with clarification_request.
    """
    if not can_decide_leave(user, leave):
        raise PermissionDenied("You do not have permission to request clarification for this leave request.")

    if not message or not message.strip():
        raise ValidationError("Clarification question/message cannot be empty.")

    with transaction.atomic():
        leave = Leave.objects.select_for_update().get(pk=leave.pk)
        leave.approval_status = 'CLARIFICATION'
        leave.clarification_request = message.strip()
        leave.save()

        # Notify employee
        from Admin.notifications import notify, get_user_role_link
        notify(
            leave.user,
            'LEAVE',
            f"Clarification requested for your {leave.leave_type} request ({leave.from_date.strftime('%d/%m/%Y')}): {message.strip()}",
            link=get_user_role_link(leave.user, 'leave_status'),
            related=leave,
            actor=user,
        )


def respond_leave_clarification(leave, user, response):
    """
    Employee provides clarification_response; moves status back to PENDING.
    """
    leave_user = leave.employee.user if leave.employee else leave.user
    if leave_user != user and not user.is_superuser:
        raise PermissionDenied("Only the requesting employee can provide clarification.")

    if not response or not response.strip():
        raise ValidationError("Clarification response cannot be empty.")

    with transaction.atomic():
        leave = Leave.objects.select_for_update().get(pk=leave.pk)
        leave.approval_status = 'PENDING'
        leave.clarification_response = response.strip()
        leave.save()

        # Notify approver
        from Admin.notifications import notify, notify_role, get_user_role_link
        msg = f"Clarification provided by {leave.employee.name if leave.employee else user.username} for leave #{leave.id}: {response.strip()}"
        if leave.approver and leave.approver.user:
            notify(
                leave.approver.user,
                'LEAVE',
                msg,
                link=get_user_role_link(leave.approver.user, 'leave_manage'),
                related=leave,
                actor=user,
            )
        else:
            notify_role(
                ['HR', 'ADMIN'],
                'LEAVE',
                msg,
                link=lambda u: get_user_role_link(u, 'leave_manage'),
                related=leave,
                actor=user,
            )


def cancel_leave_request(leave, user, remarks=''):
    """
    Cancels a leave request.
    - PENDING / CLARIFICATION: requesting employee or manager/HR can cancel.
    - APPROVED: only HR or Admin can cancel; creates REVERSAL entry for balance types.
    """
    leave_user = leave.employee.user if leave.employee else leave.user
    is_owner = (leave_user == user)
    is_hr_admin = (user.is_superuser or (hasattr(user, 'employee_profile') and user.employee_profile.is_hr))

    check_payroll_lock_for_employee(leave.employee, leave.from_date, is_open_ended=False)
    if leave.to_date.month != leave.from_date.month:
        check_payroll_lock_for_employee(leave.employee, leave.to_date, is_open_ended=False)

    with transaction.atomic():
        leave = Leave.objects.select_for_update().get(pk=leave.pk)

        if leave.approval_status == 'APPROVED':
            if not is_hr_admin:
                raise PermissionDenied("Only HR or Admin can cancel an approved leave request.")

            # Create REVERSAL entry if balance type
            lt = leave.leave_type_ref
            if lt and lt.uses_balance:
                LeaveLedger.objects.create(
                    employee=leave.employee,
                    leave_type=lt,
                    date=timezone.localdate(),
                    entry_type='REVERSAL',
                    days=abs(leave.days or Decimal('1.00')),
                    leave=leave,
                    note=f"Reversal of cancelled leave #{leave.id}: {remarks or 'Cancelled by HR/Admin'}",
                    created_by=user
                )

        elif leave.approval_status in ('PENDING', 'CLARIFICATION'):
            if not (is_owner or can_decide_leave(user, leave)):
                raise PermissionDenied("You do not have permission to cancel this request.")
        else:
            raise ValidationError(f"Cannot cancel leave with status {leave.approval_status}.")

        leave.approval_status = 'CANCELLED'
        if remarks:
            leave.decision_remarks = f"{leave.decision_remarks} [Cancelled: {remarks}]".strip()
        leave.save()

        # Notify
        notify_user = leave.approver.user if (is_owner and leave.approver) else leave.user
        if notify_user and notify_user != user:
            from Admin.notifications import notify, get_user_role_link
            area = 'leave_manage' if (is_owner and leave.approver) else 'leave_status'
            notify(
                notify_user,
                'LEAVE',
                f"Leave request #{leave.id} for {leave.from_date.strftime('%d/%m/%Y')} was CANCELLED by {user.username}.",
                link=get_user_role_link(notify_user, area),
                related=leave,
                actor=user,
            )


def accrue_leave_for_month(year, month, dry_run=False):
    """
    Monthly accrual command logic (D7).
    Service months counted from date_of_join:
    - months 1–6 (probation): completed_months <= 6 -> 0
    - months 7–12: 7 <= completed_months <= 12 -> 2.0 days per month
    - month 13 onwards: completed_months >= 13 -> 2.5 days per month (30 per year)
    Idempotent per employee+type+period.
    Skips inactive staff for months after deactivation.
    Starts from later of date_of_join and employee's OPENING entry date.
    Also ensures annual entitlements (e.g. Sick) are granted for any eligible staff.
    """
    annual_type = LeaveType.objects.filter(code='ANNUAL').first()
    if not annual_type:
        return {'processed': 0, 'credited': 0, 'skipped': 0, 'logs': []}

    import calendar
    _, num_days = calendar.monthrange(year, month)
    period_start = datetime.date(year, month, 1)
    period_end = datetime.date(year, month, num_days)

    employees = Employee.objects.filter(
        user__is_active=True
    ).filter(
        Q(is_employee=True) | Q(is_manager=True) | Q(is_administration=True) | Q(is_hr=True) | Q(is_accountant=True)
    ).select_related('user')

    processed = 0
    credited = 0
    skipped = 0
    logs = []

    for emp in employees:
        # Check deactivation
        if emp.deactivated_at and period_start > emp.deactivated_at.date():
            skipped += 1
            continue

        # Check joining date
        if not emp.date_of_join or period_end < emp.date_of_join:
            skipped += 1
            continue

        # Check opening balance entry date
        opening_entry = LeaveLedger.objects.filter(employee=emp, leave_type=annual_type, entry_type='OPENING').order_by('date').first()
        if opening_entry and period_start < opening_entry.date:
            skipped += 1
            continue

        # Check idempotency
        already_accrued = LeaveLedger.objects.filter(
            employee=emp,
            leave_type=annual_type,
            entry_type='ACCRUAL',
            period=period_start
        ).exists()
        if already_accrued:
            skipped += 1
            continue

        # Calculate completed service months at period_end
        # Months from date_of_join to period_end
        completed_months = (period_end.year - emp.date_of_join.year) * 12 + (period_end.month - emp.date_of_join.month)
        if period_end.day < emp.date_of_join.day:
            completed_months -= 1

        if completed_months <= 6:
            accrual_days = Decimal('0.00')
        elif 7 <= completed_months <= 12:
            accrual_days = Decimal('2.00')
        else:
            accrual_days = Decimal('2.50')

        processed += 1

        if accrual_days > Decimal('0.00'):
            credited += 1
            log_msg = f"Employee {emp.name} ({emp.employee_code}): +{accrual_days} days for {period_start.strftime('%B %Y')} (completed months: {completed_months})"
            logs.append(log_msg)
            if not dry_run:
                LeaveLedger.objects.create(
                    employee=emp,
                    leave_type=annual_type,
                    date=period_end,
                    entry_type='ACCRUAL',
                    days=accrual_days,
                    period=period_start,
                    note=f"Monthly accrual for {period_start.strftime('%B %Y')} ({completed_months} completed service months)"
                )
        else:
            # 0 days during probation completed_months <= 6
            logs.append(f"Employee {emp.name} ({emp.employee_code}): 0 days (probation, completed months: {completed_months})")
            if not dry_run:
                LeaveLedger.objects.create(
                    employee=emp,
                    leave_type=annual_type,
                    date=period_end,
                    entry_type='ACCRUAL',
                    days=Decimal('0.00'),
                    period=period_start,
                    note=f"Monthly accrual (probation, 0 days)"
                )

    # Also grant entitlements for non-accruing types (e.g. Sick) for staff joined up to period_end
    ent_res = grant_entitlements_for_year(year, dry_run=dry_run, up_to_date=period_end)
    logs.extend(ent_res.get('logs', []))

    return {'processed': processed, 'credited': credited, 'skipped': skipped, 'logs': logs}


def grant_entitlements_for_year(year, dry_run=False, up_to_date=None):
    """
    Grants annual entitlements for balance types with yearly_entitlement > 0 and no accrual rule (e.g. SICK).
    - Staff joining on or before 01/01 of that year get full yearly_entitlement on 01/01 (period=01/01).
    - Staff joining mid-year get it from date_of_join, pro-rated by remaining months (round to 0.5).
    - Idempotent: period is set to datetime.date(year, 1, 1).
    """
    from decimal import ROUND_HALF_UP

    entitlement_types = LeaveType.objects.filter(
        is_active=True,
        uses_balance=True,
        policy__yearly_entitlement__gt=Decimal('0.00'),
    ).filter(
        Q(policy__accrual_rule='') | Q(policy__accrual_rule__isnull=True)
    ).select_related('policy')

    if not entitlement_types.exists():
        return {'processed': 0, 'credited': 0, 'skipped': 0, 'logs': []}

    year_start = datetime.date(year, 1, 1)
    year_end = datetime.date(year, 12, 31)
    effective_limit = up_to_date or year_end

    employees = Employee.objects.filter(
        user__is_active=True
    ).filter(
        Q(is_employee=True) | Q(is_manager=True) | Q(is_administration=True) | Q(is_hr=True) | Q(is_accountant=True)
    ).select_related('user')

    processed = 0
    credited = 0
    skipped = 0
    logs = []

    for emp in employees:
        if emp.deactivated_at and emp.deactivated_at.date() < year_start:
            skipped += 1
            continue
        if not emp.date_of_join or emp.date_of_join > effective_limit:
            skipped += 1
            continue
        if emp.date_of_join.year > year:
            skipped += 1
            continue

        for lt in entitlement_types:
            policy = getattr(lt, 'policy', None)
            if not policy or policy.yearly_entitlement <= Decimal('0.00'):
                continue

            # Idempotency check:
            already_granted = LeaveLedger.objects.filter(
                employee=emp,
                leave_type=lt,
                entry_type='ENTITLEMENT',
                period=year_start
            ).exists()
            if already_granted:
                skipped += 1
                continue

            processed += 1

            if emp.date_of_join <= year_start:
                # Full annual entitlement
                grant_date = year_start
                grant_days = policy.yearly_entitlement
                note = f"Annual entitlement for {year}"
            else:
                # Mid-year joiner: pro-rated by remaining months (from date_of_join)
                grant_date = emp.date_of_join
                remaining_months = 12 - emp.date_of_join.month + 1
                pro_rated = (policy.yearly_entitlement * Decimal(str(remaining_months))) / Decimal('12')
                # Round to 0.5
                grant_days = (pro_rated * Decimal('2')).quantize(Decimal('1'), rounding=ROUND_HALF_UP) / Decimal('2')
                note = f"Pro-rated annual entitlement for {year} ({remaining_months} months from {emp.date_of_join.strftime('%d/%m/%Y')})"

            if grant_days > Decimal('0.00'):
                credited += 1
                log_msg = f"{emp.name} ({emp.employee_code}): +{grant_days} days {lt.name} entitlement for {year} (entry date {grant_date.strftime('%d/%m/%Y')})"
                logs.append(log_msg)

                if not dry_run:
                    LeaveLedger.objects.create(
                        employee=emp,
                        leave_type=lt,
                        date=grant_date,
                        entry_type='ENTITLEMENT',
                        days=grant_days,
                        period=year_start,
                        note=note
                    )

    return {'processed': processed, 'credited': credited, 'skipped': skipped, 'logs': logs}


def close_leave_year_for_all(year, dry_run=False):
    """
    Year-End close command (D10).
    For each employee and balance type:
    - Types with carry_forward_max == 0 (e.g. SICK):
      write EXPIRED -remaining at 31/12 (no review needed)
    - Types with carry_forward_max > 0 (e.g. ANNUAL):
      unused = balance at 31/12
      carried = min(unused, carry_max)
      excess = max(0, unused - carried)
      At 31/12: write YEAR_END_EXCESS -excess (if excess > 0)
                write YEAR_END_CLOSE -carried (if carried > 0)
      On 01/01: write CARRY_FORWARD +carried (if carried > 0)
      Net effect on balance: -excess
      Create LeaveYearReview(status='PENDING') for HR review
    Idempotent: re-run is a no-op.
    """
    as_of_date = datetime.date(year, 12, 31)
    next_year_start = datetime.date(year + 1, 1, 1)

    balance_types = LeaveType.objects.filter(is_active=True, uses_balance=True).select_related('policy')
    if not balance_types.exists():
        return {'processed': 0, 'reviews_created': 0, 'logs': []}

    employees = Employee.objects.filter(
        user__is_active=True
    ).filter(
        Q(is_employee=True) | Q(is_manager=True) | Q(is_administration=True) | Q(is_hr=True) | Q(is_accountant=True)
    ).select_related('user')

    processed = 0
    reviews_created = 0
    logs = []

    for emp in employees:
        if emp.deactivated_at and emp.deactivated_at.date() < datetime.date(year, 1, 1):
            continue
        if not emp.date_of_join or emp.date_of_join > as_of_date:
            continue

        for lt in balance_types:
            policy = getattr(lt, 'policy', None)
            carry_max = policy.carry_forward_max if policy else Decimal('0.00')

            if carry_max == Decimal('0.00'):
                # Non-carry-forward type (e.g. SICK): expire remaining at 31/12
                already_expired = LeaveLedger.objects.filter(
                    employee=emp,
                    leave_type=lt,
                    entry_type='EXPIRED',
                    date=as_of_date
                ).exists()
                if already_expired:
                    continue

                remaining = get_employee_leave_balance(emp, lt, as_of_date=as_of_date)
                if remaining > Decimal('0.00'):
                    processed += 1
                    log_msg = f"{emp.name} ({emp.employee_code}): Expired {remaining} days of {lt.name} for year {year}"
                    logs.append(log_msg)
                    if not dry_run:
                        LeaveLedger.objects.create(
                            employee=emp,
                            leave_type=lt,
                            date=as_of_date,
                            entry_type='EXPIRED',
                            days=-remaining,
                            note=f"Year-end {year} expired unused {lt.name}"
                        )
            else:
                # Carry-forward type (e.g. ANNUAL):
                has_review = LeaveYearReview.objects.filter(employee=emp, year=year).exists()
                has_close = LeaveLedger.objects.filter(
                    employee=emp,
                    leave_type=lt,
                    entry_type='YEAR_END_CLOSE',
                    date=as_of_date
                ).exists()
                has_carry = LeaveLedger.objects.filter(
                    employee=emp,
                    leave_type=lt,
                    entry_type='CARRY_FORWARD',
                    date=next_year_start
                ).exists()
                if has_review or has_close or has_carry:
                    continue

                unused = get_employee_leave_balance(emp, lt, as_of_date=as_of_date)
                if unused <= Decimal('0.00'):
                    continue

                carried = min(unused, carry_max)
                excess = max(Decimal('0.00'), unused - carried)

                processed += 1
                log_msg = f"{emp.name} ({emp.employee_code}): Unused {unused} days -> Carried {carried} days, Excess {excess} days"
                logs.append(log_msg)

                if not dry_run:
                    with transaction.atomic():
                        # 1. Deduct excess at 31/12
                        if excess > Decimal('0.00'):
                            LeaveLedger.objects.create(
                                employee=emp,
                                leave_type=lt,
                                date=as_of_date,
                                entry_type='YEAR_END_EXCESS',
                                days=-excess,
                                note=f"Year-end {year} excess over {carry_max} days carry limit"
                            )

                        # 2. Close carried at 31/12
                        if carried > Decimal('0.00'):
                            LeaveLedger.objects.create(
                                employee=emp,
                                leave_type=lt,
                                date=as_of_date,
                                entry_type='YEAR_END_CLOSE',
                                days=-carried,
                                note=f"Year-end {year} close of carried {lt.name}"
                            )

                        # 3. Credit carry forward on 01/01
                        if carried > Decimal('0.00'):
                            LeaveLedger.objects.create(
                                employee=emp,
                                leave_type=lt,
                                date=next_year_start,
                                entry_type='CARRY_FORWARD',
                                days=carried,
                                note=f"Carried forward from year {year}"
                            )

                        # 4. Create review
                        LeaveYearReview.objects.create(
                            employee=emp,
                            year=year,
                            unused_days=unused,
                            carried_days=carried,
                            excess_days=excess,
                            status='PENDING'
                        )
                        reviews_created += 1

    return {'processed': processed, 'reviews_created': reviews_created, 'logs': logs}


def generate_opening_balances_template():
    """
    Generates a prefilled Excel workbook for HR to fill in opening balances.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Opening Balances"

    headers = [
        "Employee Code",
        "Employee Name",
        "Leave Type",
        "Effective Date (YYYY-MM-DD)",
        "Days",
        "Note"
    ]
    ws.append(headers)

    for col_num in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_num)
        cell.font = openpyxl.styles.Font(bold=True, color="FFFFFF")
        cell.fill = openpyxl.styles.PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
        cell.alignment = openpyxl.styles.Alignment(horizontal="center", vertical="center")

    employees = Employee.objects.filter(
        user__is_active=True
    ).filter(
        Q(is_employee=True) | Q(is_manager=True) | Q(is_administration=True) | Q(is_hr=True) | Q(is_accountant=True)
    ).order_by('employee_code', 'id')

    curr_year_start = datetime.date(timezone.localdate().year, 1, 1).strftime('%Y-%m-%d')
    for emp in employees:
        ws.append([
            emp.employee_code or f"AMMS-{emp.id:04d}",
            emp.name or emp.user.username,
            "ANNUAL",
            curr_year_start,
            "",
            "Opening balance"
        ])

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 15)

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream


def process_opening_balances_excel(file_obj, user=None, dry_run=True):
    """
    Parses and validates opening balances Excel file.
    If dry_run=True: validates without saving, returns preview rows.
    If dry_run=False: atomic save if valid, returns result.
    """
    wb = openpyxl.load_workbook(file_obj, data_only=True)
    ws = wb.active

    rows = []
    has_errors = False
    valid_count = 0
    error_count = 0

    annual_type = LeaveType.objects.filter(code='ANNUAL').first()
    seen_in_file = {}

    for idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not row or not any(row):
            continue

        emp_code = str(row[0]).strip() if len(row) > 0 and row[0] is not None else ''
        emp_name = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ''
        leave_type_str = str(row[2]).strip() if len(row) > 2 and row[2] is not None else 'ANNUAL'
        date_val = row[3] if len(row) > 3 else None
        days_val = row[4] if len(row) > 4 else None
        note_val = str(row[5]).strip() if len(row) > 5 and row[5] is not None else ''

        row_errors = []

        # 1. Employee lookup
        emp = None
        if not emp_code:
            row_errors.append("Employee code is required.")
        else:
            # Match on Employee Code only. (An old fallback also matched the internal database id,
            # which picked the wrong person whenever ids and codes differ.)
            matches = list(Employee.objects.filter(employee_code__iexact=emp_code).select_related('user')[:2])
            if not matches:
                row_errors.append(f"Employee with code '{emp_code}' not found.")
            elif len(matches) > 1:
                row_errors.append(f"More than one employee has code '{emp_code}'. Fix the employee codes first.")
            else:
                emp = matches[0]
                # Safety check: the name in the file must be the same person as in this system
                def _norm(v):
                    return ' '.join(str(v or '').split()).casefold()
                if emp_name and _norm(emp_name) not in {_norm(emp.name), _norm(emp.user.username) if emp.user else ''}:
                    row_errors.append(
                        f"Code {emp_code} belongs to '{emp.name}' in this system, but the file says '{emp_name}'. "
                        f"Check the code, or download a fresh template from this site."
                    )

        # 2. Leave type lookup
        lt = None
        if not leave_type_str:
            lt = annual_type
        else:
            lt = LeaveType.objects.filter(
                Q(code__iexact=leave_type_str) | Q(name__iexact=leave_type_str)
            ).first()
            if not lt:
                row_errors.append(f"Leave type '{leave_type_str}' not found.")
            elif not lt.uses_balance:
                row_errors.append(f"Leave type '{lt.name}' does not use balance.")

        # 3. Date validation
        parsed_date = None
        if not date_val:
            row_errors.append("Effective date is required.")
        elif isinstance(date_val, (datetime.datetime, datetime.date)):
            parsed_date = date_val.date() if isinstance(date_val, datetime.datetime) else date_val
        elif isinstance(date_val, str):
            for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
                try:
                    parsed_date = datetime.datetime.strptime(date_val.strip(), fmt).date()
                    break
                except ValueError:
                    pass
            if not parsed_date:
                row_errors.append(f"Invalid date format '{date_val}'. Use YYYY-MM-DD.")
        else:
            row_errors.append(f"Invalid date value '{date_val}'.")

        # 4. Days validation
        parsed_days = None
        if days_val is None or str(days_val).strip() == '':
            row_errors.append("Days is required.")
        else:
            try:
                parsed_days = Decimal(str(days_val).strip())
                if parsed_days < Decimal('0.00'):
                    row_errors.append("Days cannot be negative.")
            except (ValueError, TypeError, ArithmeticError):
                row_errors.append(f"Invalid days value '{days_val}'.")

        # 5. Only one opening balance per employee and leave type (a re-upload would double it)
        if emp and lt:
            existing = LeaveLedger.objects.filter(employee=emp, leave_type=lt, entry_type='OPENING').first()
            if existing:
                row_errors.append(
                    f"{emp.name} already has an opening balance for {lt.name} "
                    f"({existing.days} days on {existing.date:%d/%m/%Y}). Use a leave adjustment to change it."
                )
            key = (emp.pk, lt.pk)
            if key in seen_in_file:
                row_errors.append(f"Duplicate row: {emp.name} / {lt.name} is already on row {seen_in_file[key]} of this file.")
            else:
                seen_in_file[key] = idx

        # 6. Payroll lock check
        if emp and parsed_date:
            try:
                check_payroll_lock_for_employee(emp, parsed_date, is_open_ended=False)
            except ValidationError as e:
                row_errors.append(str(e))

        is_valid = len(row_errors) == 0
        if is_valid:
            valid_count += 1
        else:
            has_errors = True
            error_count += 1

        rows.append({
            'row_num': idx,
            'employee_code': emp_code,
            'employee_name': emp.name if emp else emp_name,
            'employee': emp,
            'leave_type_str': leave_type_str,
            'leave_type': lt,
            'effective_date': parsed_date,
            'date_display': parsed_date.strftime('%d/%m/%Y') if parsed_date else str(date_val or ''),
            'days': parsed_days,
            'note': note_val or "Opening balance import",
            'is_valid': is_valid,
            'errors': row_errors,
        })

    if not dry_run:
        if has_errors:
            raise ValidationError(f"Cannot import: {error_count} row(s) contain errors.")

        with transaction.atomic():
            for r in rows:
                if not r['is_valid']:
                    continue
                LeaveLedger.objects.create(
                    employee=r['employee'],
                    leave_type=r['leave_type'],
                    date=r['effective_date'],
                    entry_type='OPENING',
                    days=r['days'],
                    note=r['note'],
                    created_by=user
                )

    return {
        'rows': rows,
        'has_errors': has_errors,
        'valid_count': valid_count,
        'error_count': error_count,
        'total_count': len(rows),
    }


def export_leave_list_excel(leaves):
    """
    Exports a queryset or list of Leave objects to an Excel spreadsheet.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Leave Requests"

    headers = [
        "Req #", "Employee Code", "Employee Name", "Department", "Branch",
        "Leave Type", "From Date", "To Date", "Working Days", "Half Day",
        "Status", "Approver", "Reason", "Decided At", "Remarks"
    ]
    ws.append(headers)

    for col_num in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_num)
        cell.font = openpyxl.styles.Font(bold=True, color="FFFFFF")
        cell.fill = openpyxl.styles.PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
        cell.alignment = openpyxl.styles.Alignment(horizontal="center", vertical="center")

    for l in leaves:
        emp = l.employee
        dept = emp.department.name if emp and emp.department else '-'
        branch = emp.branch.name if emp and emp.branch else '-'
        code = emp.employee_code if emp and emp.employee_code else (f"AMMS-{emp.id:04d}" if emp else '-')
        name = emp.name if emp else (l.user.get_full_name() or l.user.username)
        lt_name = l.leave_type_ref.name if l.leave_type_ref else l.leave_type
        hd = f"Yes ({l.half_day_part or 'Half'})" if l.is_half_day else "No"
        approver = l.approver.name if l.approver else 'HR Queue'
        decided_at = l.decided_at.strftime('%d/%m/%Y %H:%M') if l.decided_at else '-'

        ws.append([
            f"#{l.id}",
            code,
            name,
            dept,
            branch,
            lt_name,
            l.from_date.strftime('%d/%m/%Y') if l.from_date else '-',
            l.to_date.strftime('%d/%m/%Y') if l.to_date else '-',
            float(l.days or l.no_of_days or 0),
            hd,
            l.get_approval_status_display(),
            approver,
            l.reason or '',
            decided_at,
            l.decision_remarks or ''
        ])

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream


def export_leave_statement_excel(employee, year, summary_list, entries_with_bal):
    """
    Exports employee's annual leave statement to Excel with summary cards & running ledger.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Leave Statement {year}"

    # Title
    ws.append([f"LEAVE STATEMENT - {year}"])
    title_cell = ws.cell(row=1, column=1)
    title_cell.font = openpyxl.styles.Font(bold=True, size=14, color="1F497D")

    emp_code = employee.employee_code or f"AMMS-{employee.id:04d}"
    ws.append([f"Employee: {employee.name} ({emp_code}) | Dept: {employee.department.name if employee.department else '-'} | Branch: {employee.branch.name if employee.branch else '-'}"])
    ws.append([])

    # Section 1: Summary Table
    ws.append(["Leave Type", "Opening", "Accrued", "Carried Over", "Taken", "Adjustments", "Excess", "Net Balance", "Pending", "Available"])
    sum_header_row = ws.max_row
    for col_num in range(1, 11):
        cell = ws.cell(row=sum_header_row, column=col_num)
        cell.font = openpyxl.styles.Font(bold=True, color="FFFFFF")
        cell.fill = openpyxl.styles.PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
        cell.alignment = openpyxl.styles.Alignment(horizontal="center")

    for s in summary_list:
        ws.append([
            s['name'],
            float(s['opening_days']),
            float(s['accrued_days']),
            float(s['carried_days']),
            float(s['taken_days']),
            float(s['adj_days']),
            float(s['excess_days']),
            float(s['net_balance']),
            float(s['pending_days']),
            float(s['available_days']) if s['uses_balance'] else '-'
        ])

    ws.append([])
    ws.append([])

    # Section 2: Ledger Entries
    ws.append([f"LEDGER ENTRIES ({year})"])
    sec2_title = ws.cell(row=ws.max_row, column=1)
    sec2_title.font = openpyxl.styles.Font(bold=True, size=12, color="1F497D")

    ledger_headers = ["Date", "Leave Type", "Entry Type", "Days (+/-)", "Running Balance", "Leave Ref", "Note", "Recorded By"]
    ws.append(ledger_headers)
    led_header_row = ws.max_row
    for col_num in range(1, len(ledger_headers) + 1):
        cell = ws.cell(row=led_header_row, column=col_num)
        cell.font = openpyxl.styles.Font(bold=True, color="FFFFFF")
        cell.fill = openpyxl.styles.PatternFill(start_color="595959", end_color="595959", fill_type="solid")
        cell.alignment = openpyxl.styles.Alignment(horizontal="center")

    for item in entries_with_bal:
        e = item['entry']
        run_bal = item['running_balance']
        leave_ref = f"Leave #{e.leave.id}" if e.leave else '-'
        user_name = e.created_by.username if e.created_by else 'System'

        ws.append([
            e.date.strftime('%d/%m/%Y'),
            e.leave_type.name,
            e.get_entry_type_display(),
            float(e.days),
            float(run_bal),
            leave_ref,
            e.note or '',
            user_name
        ])

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream


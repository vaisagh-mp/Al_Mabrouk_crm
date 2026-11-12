import json
from decimal import Decimal
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Sum

from Admin.upload_helpers import (
    PROJECT_ATTACHMENT_EXTENSIONS,
    PROJECT_ATTACHMENT_ACCEPT,
    MAX_PROJECT_ATTACHMENT_SIZE,
    validate_project_attachment,
    get_file_type_info,
)


def _js(data):
    return json.dumps(data, cls=DjangoJSONEncoder).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def get_work_order_time_summary(work_order):
    """
    Computes time summary for a work order.
    Returns (time_entries, member_totals, total_hours).
    """
    time_entries = work_order.times.select_related('employee__user').order_by(
        'date', 'employee__user__first_name', 'start_time'
    )

    member_totals_qs = (
        work_order.times.values(
            'employee__user__first_name',
            'employee__user__last_name',
            'employee__user__username',
            'employee_id'
        )
        .annotate(hours=Sum('estimated_hours'))
        .order_by('employee__user__first_name', 'employee__user__username')
    )
    member_totals = []
    for row in member_totals_qs:
        if row['employee_id']:
            fn = (row['employee__user__first_name'] or '').strip()
            ln = (row['employee__user__last_name'] or '').strip()
            full_name = f"{fn} {ln}".strip()
            name = full_name or row['employee__user__username'] or '—'
        else:
            name = '—'
        member_totals.append({
            'name': name,
            'hours': row['hours'] or 0,
        })

    total_hours = float(sum((m['hours'] for m in member_totals), Decimal('0.00')))
    return time_entries, member_totals, total_hours


# ---------------------------------------------------------------------------
# Safe parsing for work order update forms (employee / manager / admin views).
# Bad rows are skipped with a readable warning instead of crashing the save.
# ---------------------------------------------------------------------------
from decimal import InvalidOperation
from django.utils.dateparse import parse_date as _parse_date, parse_time as _parse_time


def safe_date(value):
    """Return (date_or_None, ok). Empty -> (None, True); unparseable/impossible -> (None, False)."""
    value = (value or '').strip()
    if not value:
        return None, True
    try:
        d = _parse_date(value)
    except ValueError:
        return None, False
    return d, d is not None


def safe_time(value):
    """Return (time_or_None, ok). Empty -> (None, True); invalid -> (None, False)."""
    value = (value or '').strip()
    if not value:
        return None, True
    try:
        t = _parse_time(value)
    except ValueError:
        return None, False
    return t, t is not None


def apply_detail_dates(detail, post, warnings):
    """Set start/completion date on WorkOrderDetail; keep the old value if the new one is invalid."""
    for field, label in (('start_date', 'Start date'), ('completion_date', 'Completion date')):
        d, ok = safe_date(post.get(field))
        if ok:
            setattr(detail, field, d)
        else:
            warnings.append(f"{label} '{post.get(field)}' is not a valid date and was not changed.")
    est = (post.get('estimated_hours') or '').strip()
    if est:
        try:
            detail.estimated_hours = Decimal(est)
        except (InvalidOperation, ValueError):
            warnings.append(f"Estimated hours '{est}' is not a number and was not changed.")
    else:
        detail.estimated_hours = None


def clean_time_rows(dates, starts, finishes, employees=None, row_ids=None, break_minutes=None, travel_minutes=None, descriptions=None, attachments=None):
    """
    Turn the posted time_date[] / start_time[] / finish_time[] (/ time_employee[]) lists
    into valid rows. Returns (rows, warnings); rows are dicts with id, date, start, finish, emp_val,
    break_minutes, travel_minutes, description, attachment.
    Completely empty rows are ignored silently.
    """
    employees = employees or []
    row_ids = row_ids or []
    break_minutes = break_minutes or []
    travel_minutes = travel_minutes or []
    descriptions = descriptions or []
    attachments = attachments or []

    n = max(
        len(dates), len(starts), len(finishes), len(employees),
        len(row_ids), len(break_minutes), len(travel_minutes), len(descriptions)
    ) if (dates or starts or finishes) else 0

    rows, warnings = [], []
    for i in range(n):
        raw_date = dates[i] if i < len(dates) else ''
        raw_start = starts[i] if i < len(starts) else ''
        raw_finish = finishes[i] if i < len(finishes) else ''
        emp_val = (employees[i] if i < len(employees) else '') or ''
        raw_id = row_ids[i] if i < len(row_ids) else None
        raw_brk = break_minutes[i] if i < len(break_minutes) else 0
        raw_trv = travel_minutes[i] if i < len(travel_minutes) else 0
        raw_desc = descriptions[i] if i < len(descriptions) else ''
        att = attachments[i] if i < len(attachments) else None

        if not (raw_start or '').strip() and not (raw_finish or '').strip():
            continue  # nothing entered on this row
        d, d_ok = safe_date(raw_date)
        s, s_ok = safe_time(raw_start)
        f, f_ok = safe_time(raw_finish)
        if not d_ok or not s_ok or not f_ok:
            warnings.append(f"Time row {i + 1} was skipped: the date or time is not valid.")
            continue
        if d is None or s is None:
            warnings.append(f"Time row {i + 1} was skipped: date and start time are required.")
            continue

        try:
            r_id = int(raw_id) if raw_id else None
        except (ValueError, TypeError):
            r_id = None

        try:
            brk = max(0, int(raw_brk)) if raw_brk else 0
        except (ValueError, TypeError):
            brk = 0

        try:
            trv = max(0, int(raw_trv)) if raw_trv else 0
        except (ValueError, TypeError):
            trv = 0

        rows.append({
            'row_idx': i + 1,
            'id': r_id,
            'date': d,
            'start': s,
            'finish': f,
            'emp_val': emp_val.strip(),
            'break_minutes': brk,
            'travel_minutes': trv,
            'description': raw_desc.strip(),
            'attachment': att,
        })
    return rows, warnings


def sync_work_order_times(work_order, time_rows, scope_employee=None, allowed_for_save=None, default_employee=None, action='save_draft', user=None, warnings=None):
    """
    Update-in-place sync keyed by row id:
    - When HR_TIMESHEET_APPROVAL is False: keeps legacy replace behavior so existing tests pass unchanged.
    - When HR_TIMESHEET_APPROVAL is True:
      - Update changed DRAFT/REJECTED rows
      - Create new rows
      - Delete only DRAFT rows the user removed
      - SUBMITTED and APPROVED rows are never deleted or changed by a work order save (rendered read-only)
      - Service log keeps its id when its row is updated
    """
    from datetime import datetime, timedelta
    from django.conf import settings
    from Admin.models import WorkOrderTime, check_payroll_lock_for_employee
    from Admin.timesheet_helpers import check_timesheet_overlap
    from Admin.timesheet_service import submit_service_log_for_approval
    from service_logs.models import EmployeeServiceLog

    if warnings is None:
        warnings = []

    if not getattr(settings, 'HR_TIMESHEET_APPROVAL', False):
        # Legacy behavior: clear old auto-created service logs and WorkOrderTimes, recreate from time_rows
        if scope_employee:
            EmployeeServiceLog.objects.filter(time_log__work_order=work_order, employee=scope_employee, is_auto_created=True).delete()
            WorkOrderTime.objects.filter(work_order=work_order, employee=scope_employee).delete()
        else:
            EmployeeServiceLog.objects.filter(time_log__work_order=work_order, is_auto_created=True).delete()
            WorkOrderTime.objects.filter(work_order=work_order).delete()

        saved_time_logs = []
        for r in time_rows:
            if scope_employee:
                row_emp = scope_employee
            else:
                emp_val = r.get('emp_val')
                row_emp = None
                if emp_val and allowed_for_save:
                    try:
                        row_emp = allowed_for_save.get(int(emp_val))
                    except (ValueError, TypeError):
                        row_emp = None
                if not row_emp:
                    row_emp = default_employee
                if not row_emp:
                    warnings.append(f"Time row {r.get('row_idx')} was skipped: choose who did the work.")
                    continue

            tl = WorkOrderTime.objects.create(
                work_order=work_order,
                date=r['date'],
                start_time=r['start'],
                finish_time=r['finish'],
                employee=row_emp,
                break_minutes=r.get('break_minutes', 0),
                travel_minutes=r.get('travel_minutes', 0),
                description=r.get('description', ''),
                attachment=r.get('attachment', None),
            )
            saved_time_logs.append(tl)
        return saved_time_logs

    # Phase 5: Update-in-place
    existing_qs = WorkOrderTime.objects.filter(work_order=work_order).select_related('service_log', 'employee')
    if scope_employee:
        existing_qs = existing_qs.filter(employee=scope_employee)
    existing_rows = list(existing_qs)
    existing_by_id = {t.id: t for t in existing_rows}

    # Delete draft rows of this work order that were removed by the user in this save
    submitted_ids = {r.get('id') for r in time_rows if r.get('id')}
    for old_row in list(existing_rows):
        if old_row.id not in submitted_ids:
            slog = getattr(old_row, 'service_log', None)
            slog_status = getattr(slog, 'approval_status', 'DRAFT')
            if slog_status == 'DRAFT':
                if slog and slog.is_auto_created:
                    slog.delete()
                old_row.delete()
                existing_rows.remove(old_row)
                existing_by_id.pop(old_row.id, None)

    kept_row_ids = set()
    saved_time_logs = []
    seen_intervals = []

    # Add every kept read-only row to seen_intervals first
    for existing_row in existing_rows:
        slog = getattr(existing_row, 'service_log', None)
        slog_status = getattr(slog, 'approval_status', 'DRAFT')
        if slog_status in ('SUBMITTED', 'APPROVED'):
            kept_row_ids.add(existing_row.id)
            saved_time_logs.append(existing_row)
            if existing_row.start_time and existing_row.finish_time and existing_row.employee_id:
                dt_s = datetime.combine(existing_row.date, existing_row.start_time)
                dt_f = datetime.combine(existing_row.date, existing_row.finish_time)
                if dt_f < dt_s:
                    dt_f += timedelta(days=1)
                seen_intervals.append((existing_row.employee_id, existing_row.date, dt_s, dt_f))

    for r in time_rows:
        if scope_employee:
            row_emp = scope_employee
        else:
            emp_val = r.get('emp_val')
            row_emp = None
            if emp_val and allowed_for_save:
                try:
                    row_emp = allowed_for_save.get(int(emp_val))
                except (ValueError, TypeError):
                    row_emp = None
            if not row_emp:
                row_emp = default_employee
            if not row_emp:
                warnings.append(f"Time row {r.get('row_idx')} was skipped: choose who did the work.")
                continue

        r_id = r.get('id')
        existing_row = existing_by_id.get(r_id) if r_id else None

        # Check if existing row is read-only (SUBMITTED / APPROVED)
        if existing_row:
            slog = getattr(existing_row, 'service_log', None)
            slog_status = getattr(slog, 'approval_status', 'DRAFT')
            if slog_status in ('SUBMITTED', 'APPROVED'):
                # Read-only! Already kept and added to seen_intervals
                continue

        # Check payroll lock for row_emp on r['date']
        try:
            check_payroll_lock_for_employee(row_emp, r['date'])
        except Exception as e:
            msg = getattr(e, 'message', str(e))
            warnings.append(f"Time row {r.get('row_idx')} was skipped: {msg}")
            continue

        # Check break < duration
        if r['finish']:
            dt_start = datetime.combine(r['date'], r['start'])
            dt_finish = datetime.combine(r['date'], r['finish'])
            if dt_finish < dt_start:
                dt_finish += timedelta(days=1)
            dur_mins = int((dt_finish - dt_start).total_seconds() // 60)
            if r.get('break_minutes', 0) >= dur_mins:
                warnings.append(f"Time row {r.get('row_idx')} was skipped: break duration must be less than working duration.")
                continue

            # Check intra-batch overlap against seen_intervals (which includes read-only rows from this WO)
            intra_overlap = False
            for (prev_emp_id, prev_date, prev_start, prev_finish) in seen_intervals:
                if prev_emp_id == row_emp.id and prev_date == r['date']:
                    if dt_start < prev_finish and prev_start < dt_finish:
                        intra_overlap = True
                        break
            if intra_overlap:
                warnings.append(f"Time row {r.get('row_idx')} was skipped: overlaps with another row on {r['date']}.")
                continue

        # Check overlap across other work orders, manual service logs, and DB rows
        overlap_err = check_timesheet_overlap(
            row_emp.id, r['date'], r['start'], r['finish'],
            exclude_time_log_id=r_id
        )
        if overlap_err:
            warnings.append(f"Time row {r.get('row_idx')} was skipped: {overlap_err}")
            continue

        if r['finish']:
            seen_intervals.append((row_emp.id, r['date'], dt_start, dt_finish))

        if existing_row:
            # Update DRAFT/REJECTED row in place
            existing_row.date = r['date']
            existing_row.start_time = r['start']
            existing_row.finish_time = r['finish']
            existing_row.employee = row_emp
            existing_row.break_minutes = r.get('break_minutes', 0)
            existing_row.travel_minutes = r.get('travel_minutes', 0)
            existing_row.description = r.get('description', '')
            if r.get('attachment'):
                existing_row.attachment = r['attachment']
            existing_row.save()
            kept_row_ids.add(r_id)
            saved_time_logs.append(existing_row)

            if action in ('submit_approval', 'submit'):
                slog = getattr(existing_row, 'service_log', None)
                if slog:
                    submit_service_log_for_approval(slog, user)
        else:
            # Create new row
            new_row = WorkOrderTime.objects.create(
                work_order=work_order,
                date=r['date'],
                start_time=r['start'],
                finish_time=r['finish'],
                employee=row_emp,
                break_minutes=r.get('break_minutes', 0),
                travel_minutes=r.get('travel_minutes', 0),
                description=r.get('description', ''),
                attachment=r.get('attachment', None),
            )
            saved_time_logs.append(new_row)
            if action in ('submit_approval', 'submit'):
                slog = getattr(new_row, 'service_log', None)
                if slog:
                    submit_service_log_for_approval(slog, user)

    # Delete only DRAFT rows the user removed
    for old_row in existing_rows:
        if old_row.id not in kept_row_ids:
            slog = getattr(old_row, 'service_log', None)
            slog_status = getattr(slog, 'approval_status', 'DRAFT')
            if slog_status == 'DRAFT':
                if slog and slog.is_auto_created:
                    slog.delete()
                old_row.delete()

    return saved_time_logs



def clean_quantity(value, label, warnings):
    """Whole-number quantity. Empty -> 0. '2.0' -> 2. '1.5' / 'abc' -> None + warning (row skipped)."""
    value = (value or '').strip()
    if not value:
        return 0
    try:
        q = Decimal(value)
    except (InvalidOperation, ValueError):
        warnings.append(f"{label} was skipped: quantity '{value}' is not a number.")
        return None
    if q != q.to_integral_value() or q < 0:
        warnings.append(f"{label} was skipped: quantity must be a whole number (got '{value}').")
        return None
    return int(q)

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


def clean_time_rows(dates, starts, finishes, employees=None):
    """
    Turn the posted time_date[] / start_time[] / finish_time[] (/ time_employee[]) lists
    into valid rows. Returns (rows, warnings); rows are dicts with date, start, finish, emp_val.
    Completely empty rows are ignored silently.
    """
    employees = employees or []
    n = max(len(dates), len(starts), len(finishes), len(employees)) if (dates or starts or finishes) else 0
    rows, warnings = [], []
    for i in range(n):
        raw_date = dates[i] if i < len(dates) else ''
        raw_start = starts[i] if i < len(starts) else ''
        raw_finish = finishes[i] if i < len(finishes) else ''
        emp_val = (employees[i] if i < len(employees) else '') or ''
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
        rows.append({'row_idx': i + 1, 'date': d, 'start': s, 'finish': f, 'emp_val': emp_val.strip()})
    return rows, warnings


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

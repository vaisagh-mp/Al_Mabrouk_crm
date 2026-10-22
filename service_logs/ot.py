from collections import defaultdict
from decimal import Decimal
from datetime import datetime, time, timedelta
from django.utils import timezone


OT_THRESHOLD_HOURS = Decimal('10')


def calculate_office_ot_cost(salary, register_rows, logs=None):
    """
    Computes office overtime cost from attendance register rows.
    Formula: row['office_ot_hours'] * rate * 1.5 (or * 2.0 if any log on that day is holiday).
    rate = salary / 208.
    """
    if not salary:
        return Decimal('0.00')
    rate = Decimal(str(salary)) / Decimal('208')
    if rate <= 0:
        return Decimal('0.00')

    logs_by_date = defaultdict(list)
    if logs:
        for l in logs:
            logs_by_date[l.date].append(l)

    office_ot_cost = Decimal('0.00')
    for r in register_rows:
        off_ot = r.get('office_ot_hours') or Decimal('0.00')
        if off_ot > 0:
            day_logs = logs_by_date.get(r['date'], [])
            has_holiday = any(getattr(l, 'is_holiday', False) for l in day_logs)
            mult = Decimal('2.0') if has_holiday else Decimal('1.5')
            office_ot_cost += off_ot * rate * mult
    return round(office_ot_cost, 2)



def build_day_timeline(attendances, logs, day):
    """
    Build non-overlapping segments for the given day in time order.
    Segments are tuples of: (start_dt, end_dt, source, log_or_None)
    where source is 'VESSEL' or 'OFFICE'.
    Where office overlaps vessel, the segment is vessel (attributed to that log).
    """
    tz = timezone.get_current_timezone()

    office_intervals = []
    for a in attendances:
        if not a.login_time or not a.log_out_time:
            continue
        login_dt = timezone.localtime(a.login_time, tz)
        logout_dt = timezone.localtime(a.log_out_time, tz)
        if logout_dt > login_dt:
            office_intervals.append((login_dt, logout_dt))

    vessel_intervals = []
    sorted_logs = sorted(logs, key=lambda l: (l.start_time, getattr(l, 'id', None) or 0))
    for l in sorted_logs:
        if l.start_time is None or l.end_time is None:
            continue
        s_dt = timezone.make_aware(datetime.combine(day, l.start_time), tz)
        end_date = day + timedelta(days=1) if l.end_time < l.start_time else day
        e_dt = timezone.make_aware(datetime.combine(end_date, l.end_time), tz)
        if e_dt > s_dt:
            vessel_intervals.append((s_dt, e_dt, l))

    points = set()
    for s, e in office_intervals:
        points.add(s)
        points.add(e)
    for s, e, l in vessel_intervals:
        points.add(s)
        points.add(e)

    if not points:
        return []

    sorted_points = sorted(points)
    raw_segments = []

    for p_curr, p_next in zip(sorted_points, sorted_points[1:]):
        # Vessel takes precedence over office when overlapping
        vessel_match = None
        for s, e, l in vessel_intervals:
            if s <= p_curr and p_next <= e:
                vessel_match = l
                break

        if vessel_match is not None:
            raw_segments.append((p_curr, p_next, 'VESSEL', vessel_match))
        else:
            office_match = False
            for s, e in office_intervals:
                if s <= p_curr and p_next <= e:
                    office_match = True
                    break
            if office_match:
                raw_segments.append((p_curr, p_next, 'OFFICE', None))

    merged = []
    for s, e, source, log_obj in raw_segments:
        if merged and merged[-1][2] == source and merged[-1][3] == log_obj and merged[-1][1] == s:
            merged[-1] = (merged[-1][0], e, source, log_obj)
        else:
            merged.append((s, e, source, log_obj))

    return merged


def split_day_ot(attendances, logs, day):
    """
    Computes total hours, overtime hours and splits OT between vessel logs and office.
    Returns:
      {
        'total_hours': Decimal,
        'ot_hours': Decimal,
        'vessel_ot_by_log_id': {log_id: Decimal},
        'office_ot_hours': Decimal,
      }
    If there are no closed logs -> ot is 0 (office-only days never have OT).
    """
    closed_logs = [l for l in logs if getattr(l, 'end_time', None) is not None]
    timeline = build_day_timeline(attendances, logs, day)
    tot_secs = sum((seg[1] - seg[0]).total_seconds() for seg in timeline)
    total_hours = Decimal(str(round(tot_secs / 3600.0, 2))).quantize(Decimal('0.01'))

    vessel_ot_by_log_id = {l.id: Decimal('0.00') for l in logs}

    if not closed_logs:
        return {
            'total_hours': total_hours,
            'ot_hours': Decimal('0.00'),
            'vessel_ot_by_log_id': vessel_ot_by_log_id,
            'office_ot_hours': Decimal('0.00'),
        }

    threshold_secs = OT_THRESHOLD_HOURS * Decimal('3600')  # 36000
    vessel_ot_seconds_by_log = {l.id: Decimal('0') for l in logs}
    office_ot_seconds = Decimal('0')
    cum_secs = Decimal('0')

    for start_dt, end_dt, source, log_obj in timeline:
        seg_secs = Decimal(str((end_dt - start_dt).total_seconds()))
        seg_start = cum_secs
        seg_end = cum_secs + seg_secs
        cum_secs = seg_end

        if seg_end <= threshold_secs:
            continue
        elif seg_start >= threshold_secs:
            ot_sec = seg_secs
        else:
            ot_sec = seg_end - threshold_secs

        if source == 'VESSEL' and log_obj:
            log_key = getattr(log_obj, 'id', None)
            vessel_ot_seconds_by_log[log_key] = (
                vessel_ot_seconds_by_log.get(log_key, Decimal('0')) + ot_sec
            )
        elif source == 'OFFICE':
            office_ot_seconds += ot_sec

    for lid, sec in vessel_ot_seconds_by_log.items():
        vessel_ot_by_log_id[lid] = round(sec / Decimal('3600'), 2).quantize(Decimal('0.01'))

    office_ot_hours = round(office_ot_seconds / Decimal('3600'), 2).quantize(Decimal('0.01'))
    vessel_ot_total = sum(vessel_ot_by_log_id.values(), Decimal('0.00')).quantize(Decimal('0.01'))
    ot_hours = (vessel_ot_total + office_ot_hours).quantize(Decimal('0.01'))

    return {
        'total_hours': total_hours,
        'ot_hours': ot_hours,
        'vessel_ot_by_log_id': vessel_ot_by_log_id,
        'office_ot_hours': office_ot_hours,
    }


def employee_office_attendances(employee_id, day):
    """
    Attendance rows whose login_time falls inside the LOCAL day
    (aware start/end of day in Asia/Dubai; do not use login_time__date).
    """
    from Admin.models import Attendance
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(day, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(day, time.max), tz)
    return list(
        Attendance.objects.filter(
            employee_id=employee_id,
            login_time__gte=start_dt,
            login_time__lte=end_dt
        ).exclude(status='REJECTED').order_by('login_time', 'id')
    )


def recalculate_day_ot(employee_id, day):
    """
    Recalculates overtime and costs for all logs of an employee on a given day.
    Saves via bulk_update (fixed query count, no save() recursion).
    """
    from service_logs.models import EmployeeServiceLog

    logs = list(
        EmployeeServiceLog.objects.filter(
            employee_id=employee_id,
            date=day
        ).select_related('employee').order_by('start_time', 'id')
    )
    if not logs:
        return

    attendances = employee_office_attendances(employee_id, day)
    split_info = split_day_ot(attendances, logs, day)
    vessel_ot_map = split_info['vessel_ot_by_log_id']

    for log in logs:
        if log.end_time is None:
            log.total_hours = Decimal('0.00')
            log.ot_hours = Decimal('0.00')
            log.ot_type = None
            log.normal_cost = Decimal('0.00')
            log.ot_cost = Decimal('0.00')
            continue

        if log.total_hours is None or log.total_hours == Decimal('0.00'):
            start_dt = datetime.combine(log.date, log.start_time)
            end_dt = datetime.combine(log.date, log.end_time)
            if end_dt < start_dt:
                end_dt += timedelta(days=1)
            log.total_hours = round(Decimal(str((end_dt - start_dt).total_seconds() / 3600)), 2)

        v_ot = vessel_ot_map.get(log.id, Decimal('0.00'))
        log.ot_hours = v_ot
        normal_hours = max(log.total_hours - log.ot_hours, Decimal('0.00'))

        log.ot_type = ('HOLIDAY' if log.is_holiday else 'WEEKDAY') if log.ot_hours > 0 else None

        salary = log.employee.salary if log.employee else None
        if salary:
            rate = salary / Decimal('208')  # 26 * 8 = 208
            log.normal_cost = round(normal_hours * rate, 2)
            mult = Decimal('2') if log.is_holiday else Decimal('1.5')
            log.ot_cost = round(log.ot_hours * rate * mult, 2)
        else:
            log.normal_cost = Decimal('0.00')
            log.ot_cost = Decimal('0.00')

    EmployeeServiceLog.objects.bulk_update(
        logs,
        ['ot_hours', 'ot_type', 'normal_cost', 'ot_cost']
    )

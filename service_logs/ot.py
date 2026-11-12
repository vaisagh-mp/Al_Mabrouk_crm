from collections import defaultdict
from decimal import Decimal
from datetime import datetime, time, timedelta
from django.utils import timezone
from django.db.models import Q


OT_THRESHOLD_HOURS = Decimal('10')

OT_MULT_NORMAL = Decimal('1.25')
OT_MULT_SUNDAY = Decimal('1.5')
OT_MULT_PUBLIC_HOLIDAY = Decimal('2.0')


def ot_multiplier(day, is_public_holiday, is_rest_day=None) -> Decimal:
    if is_public_holiday:
        return OT_MULT_PUBLIC_HOLIDAY
    if is_rest_day is None:
        is_rest = (day.weekday() == 6)
    else:
        is_rest = bool(is_rest_day)
    if is_rest:
        return OT_MULT_SUNDAY
    return OT_MULT_NORMAL


def ot_type_for(day, is_public_holiday, is_rest_day=None) -> str:
    """
    Returns 'HOLIDAY' | 'SUNDAY' | 'WEEKDAY'.
    Note: 'SUNDAY' is stored in the DB for rest day OT, but displayed as 'Rest Day' on screen.
    """
    if is_public_holiday:
        return 'HOLIDAY'
    if is_rest_day is None:
        is_rest = (day.weekday() == 6)
    else:
        is_rest = bool(is_rest_day)
    if is_rest:
        return 'SUNDAY'
    return 'WEEKDAY'


def calculate_office_ot_cost(salary, register_rows, logs=None, employee=None):
    """
    Computes office overtime cost from attendance register rows.
    Formula: row['office_ot_hours'] * rate * multiplier.
    Multiplier:
      - Public holiday (date in Holiday table or any log that day has is_holiday=True): 2.0
      - Rest day (Sunday or employee rest day when V2 is on): 1.5
      - Normal day (Monday-Saturday, not public holiday, not rest day): 1.25
    rate = base_salary / 208.
    """
    from django.conf import settings
    from Admin.attendance_engine import is_rest_day, pay_base_salary
    v2_on = getattr(settings, 'HR_ATTENDANCE_ENGINE_V2', False)

    from Admin.models import Holiday

    dates = {r['date'] for r in register_rows if 'date' in r and (r.get('office_ot_hours') or Decimal('0.00')) > 0}
    holiday_dates = set()
    if dates:
        if v2_on and employee:
            holiday_dates = set(
                Holiday.objects.filter(
                    date__in=dates,
                    is_active=True
                ).filter(
                    Q(branch__isnull=True) | Q(branch_id=employee.branch_id)
                ).values_list('date', flat=True)
            )
        else:
            holiday_dates = set(Holiday.objects.filter(date__in=dates).values_list('date', flat=True))

    logs_by_date = defaultdict(list)
    if logs:
        for l in logs:
            logs_by_date[l.date].append(l)

    office_ot_cost = Decimal('0.00')
    for r in register_rows:
        off_ot = r.get('office_ot_hours') or Decimal('0.00')
        if off_ot > 0:
            day = r['date']
            emp = employee or r.get('employee')
            if v2_on and emp:
                base_sal = pay_base_salary(emp, day)
                rate = (base_sal / Decimal('208')) if base_sal else Decimal('0.00')
                rest_flag = is_rest_day(emp, day)
            else:
                sal = Decimal(str(salary or '0.00'))
                rate = (sal / Decimal('208')) if sal else Decimal('0.00')
                rest_flag = None

            if rate <= 0:
                continue

            day_logs = logs_by_date.get(day, [])

            # Phase 5: office OT on Office+Vessel days only from days whose vessel logs are all approved
            if getattr(settings, 'HR_TIMESHEET_APPROVAL', False):
                if day_logs and not all(getattr(l, 'is_counting', lambda: True)() for l in day_logs):
                    continue

            is_public_holiday = (day in holiday_dates) or any(getattr(l, 'is_holiday', False) for l in day_logs)
            mult = ot_multiplier(day, is_public_holiday, is_rest_day=rest_flag)
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

    events = []
    for s_dt, e_dt in office_intervals:
        events.append((s_dt, 'START', 'OFFICE', None))
        events.append((e_dt, 'END', 'OFFICE', None))
    for s_dt, e_dt, l in vessel_intervals:
        events.append((s_dt, 'START', 'VESSEL', l))
        events.append((e_dt, 'END', 'VESSEL', l))

    events.sort(key=lambda x: (x[0], 0 if x[1] == 'END' else 1))

    segments = []
    current_time = None
    active_office = 0
    active_vessels = []

    for ev_time, ev_type, source, log_obj in events:
        if current_time is not None and ev_time > current_time:
            if active_vessels:
                segments.append((current_time, ev_time, 'VESSEL', active_vessels[-1]))
            elif active_office > 0:
                segments.append((current_time, ev_time, 'OFFICE', None))

        if ev_type == 'START':
            if source == 'OFFICE':
                active_office += 1
            else:
                active_vessels.append(log_obj)
        else:
            if source == 'OFFICE':
                active_office = max(0, active_office - 1)
            else:
                if log_obj in active_vessels:
                    active_vessels.remove(log_obj)

        current_time = ev_time

    return segments


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
    closed_logs = [l for l in logs if l.end_time is not None]
    if not closed_logs:
        tz = timezone.get_current_timezone()
        office_intervals = []
        for a in attendances:
            if a.login_time and a.log_out_time:
                login_dt = timezone.localtime(a.login_time, tz)
                logout_dt = timezone.localtime(a.log_out_time, tz)
                if logout_dt > login_dt:
                    office_intervals.append((login_dt, logout_dt))
        if office_intervals:
            sorted_ints = sorted(office_intervals, key=lambda x: x[0])
            merged = []
            for s, e in sorted_ints:
                if not merged:
                    merged.append([s, e])
                else:
                    if s <= merged[-1][1]:
                        merged[-1][1] = max(merged[-1][1], e)
                    else:
                        merged.append([s, e])
            tot_secs = sum((m[1] - m[0]).total_seconds() for m in merged)
            tot_hours = Decimal(str(round(tot_secs / 3600.0, 2))).quantize(Decimal('0.01'))
        else:
            tot_hours = Decimal('0.00')

        return {
            'total_hours': tot_hours,
            'ot_hours': Decimal('0.00'),
            'vessel_ot_by_log_id': {},
            'office_ot_hours': Decimal('0.00'),
        }

    segments = build_day_timeline(attendances, closed_logs, day)

    total_seconds = Decimal('0')
    threshold_seconds = OT_THRESHOLD_HOURS * Decimal('3600')  # 36000 sec

    vessel_ot_seconds_by_log = defaultdict(Decimal)
    office_ot_seconds = Decimal('0')
    vessel_ot_by_log_id = {}

    from django.conf import settings
    travel_counts = getattr(settings, 'HR_TRAVEL_COUNTS_AS_WORK', True)
    tz = timezone.get_current_timezone()

    for s_dt, e_dt, source, log_obj in segments:
        seg_raw_duration = Decimal(str((e_dt - s_dt).total_seconds()))
        if source == 'VESSEL' and log_obj and log_obj.start_time and log_obj.end_time:
            l_start_dt = timezone.make_aware(datetime.combine(day, log_obj.start_time), tz)
            l_end_date = day + timedelta(days=1) if log_obj.end_time < log_obj.start_time else day
            l_end_dt = timezone.make_aware(datetime.combine(l_end_date, log_obj.end_time), tz)
            l_raw_sec = Decimal(str(max(1, int((l_end_dt - l_start_dt).total_seconds()))))
            brk_sec = Decimal(str((getattr(log_obj, 'break_minutes', 0) or 0) * 60))
            trv_sec = Decimal(str((getattr(log_obj, 'travel_minutes', 0) or 0) * 60)) if travel_counts else Decimal('0')
            net_sec = max(Decimal('0'), l_raw_sec - brk_sec + trv_sec)
            ratio = net_sec / l_raw_sec
            seg_duration = seg_raw_duration * ratio
        else:
            seg_duration = seg_raw_duration

        new_total = total_seconds + seg_duration

        if new_total <= threshold_seconds:
            ot_sec = Decimal('0')
        elif total_seconds < threshold_seconds:
            ot_sec = new_total - threshold_seconds
        else:
            ot_sec = seg_duration

        total_seconds = new_total

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
    total_hours = round(total_seconds / Decimal('3600'), 2).quantize(Decimal('0.01'))

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
    from Admin.models import Holiday, Employee
    from Admin.attendance_engine import is_rest_day, pay_base_salary
    from django.conf import settings
    v2_on = getattr(settings, 'HR_ATTENDANCE_ENGINE_V2', False)

    logs = list(
        EmployeeServiceLog.objects.filter(
            employee_id=employee_id,
            date=day
        ).select_related('employee', 'employee__branch').order_by('start_time', 'id')
    )
    if not logs:
        return

    emp = logs[0].employee if logs else None
    if not emp:
        emp = Employee.objects.filter(id=employee_id).select_related('branch').first()

    if v2_on and emp:
        is_holiday_date = Holiday.objects.filter(
            date=day,
            is_active=True
        ).filter(
            Q(branch__isnull=True) | Q(branch_id=emp.branch_id)
        ).exists()
        rest_flag = is_rest_day(emp, day)
    else:
        is_holiday_date = Holiday.objects.filter(date=day).exists()
        rest_flag = None

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
            dur_mins = Decimal(str(int((end_dt - start_dt).total_seconds() // 60)))
            brk = Decimal(str(getattr(log, 'break_minutes', 0) or 0))
            trv = Decimal(str(getattr(log, 'travel_minutes', 0) or 0))
            from django.conf import settings
            travel_counts = getattr(settings, 'HR_TRAVEL_COUNTS_AS_WORK', True)
            work_mins = dur_mins - brk + (trv if travel_counts else Decimal('0'))
            log.total_hours = round(max(Decimal('0'), work_mins / Decimal('60')), 2)

        v_ot = vessel_ot_map.get(log.id, Decimal('0.00'))
        log.ot_hours = v_ot
        normal_hours = max(log.total_hours - log.ot_hours, Decimal('0.00'))

        is_public_holiday = bool(log.is_holiday or is_holiday_date)
        log.ot_type = ot_type_for(log.date, is_public_holiday, is_rest_day=rest_flag) if log.ot_hours > 0 else None

        total_salary = log.employee.salary if log.employee else None
        if total_salary:
            normal_rate = total_salary / Decimal('208')
            log.normal_cost = round(normal_hours * normal_rate, 2)
        else:
            log.normal_cost = Decimal('0.00')

        if v2_on and log.employee:
            ot_salary = pay_base_salary(log.employee, log.date)
        else:
            ot_salary = total_salary

        if ot_salary and log.ot_hours > 0:
            ot_rate = ot_salary / Decimal('208')
            mult = ot_multiplier(log.date, is_public_holiday, is_rest_day=rest_flag)
            log.ot_cost = round(log.ot_hours * ot_rate * mult, 2)
        else:
            log.ot_cost = Decimal('0.00')

    EmployeeServiceLog.objects.bulk_update(
        logs,
        ['ot_hours', 'ot_type', 'normal_cost', 'ot_cost']
    )

    if getattr(settings, 'HR_TIMESHEET_APPROVAL', False):
        try:
            from Admin.timesheet_service import sync_ot_approval_for_day
            sync_ot_approval_for_day(employee_id, day)
        except Exception:
            pass


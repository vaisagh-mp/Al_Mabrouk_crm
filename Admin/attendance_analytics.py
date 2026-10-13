from collections import defaultdict, Counter
from datetime import datetime, date, timedelta
from decimal import Decimal

from django.utils import timezone

STANDARD_DAY_HOURS = Decimal('8')
OT_ALERT_HOURS_PER_30_DAYS = Decimal('40')
BRADFORD_MONITOR = 50
BRADFORD_ACTION = 200


def _compute_period_kpis(rows):
    """
    Computes base KPI totals from attendance register rows.
    Uses the exact same attendance rate denominator as build_employee_analytics_context.
    """
    emp_groups = defaultdict(list)
    for r in rows:
        emp_groups[r['employee'].id].append(r)

    total_worked_days = 0
    total_expected_days = 0
    total_absent_days = 0
    total_hours = Decimal('0.00')
    gross_ot_hours = Decimal('0.00')
    total_leave_days = 0
    off_days_worked = 0

    for emp_id, emp_rows in emp_groups.items():
        emp_working_days = sum(
            1 for r in emp_rows
            if (r.get('status_code') not in ('SUNDAY', 'HOLIDAY', 'NONE')) or r.get('is_off_day') or ((r.get('total_hours') or Decimal(0)) > 0)
        )
        emp_leave = sum(1 for r in emp_rows if r['status_code'] in ('ANNUAL_LEAVE', 'SICK_LEAVE', 'PERSONAL_LEAVE'))
        emp_training = sum(1 for r in emp_rows if r['status_code'] == 'TRAINING')
        emp_denom = max(emp_working_days - emp_leave - emp_training, 0)
        total_expected_days += emp_denom

        for r in emp_rows:
            st = r['status_code']
            if st in ('OFFICE', 'VESSEL', 'OFFICE_VESSEL'):
                total_worked_days += 1
                if r.get('is_off_day'):
                    off_days_worked += 1
            elif st == 'ABSENT':
                total_absent_days += 1
            elif st in ('ANNUAL_LEAVE', 'SICK_LEAVE', 'PERSONAL_LEAVE'):
                total_leave_days += 1

            total_hours += Decimal(str(r.get('total_hours') or '0.00'))
            gross_ot_hours += Decimal(str(r.get('ot_hours') or '0.00'))

    attendance_rate = (
        round((Decimal(total_worked_days) / Decimal(total_expected_days)) * 100, 2)
        if total_expected_days > 0 else Decimal('0.00')
    )
    absenteeism_rate = (
        round((Decimal(total_absent_days) / Decimal(total_expected_days)) * 100, 2)
        if total_expected_days > 0 else Decimal('0.00')
    )
    avg_hours_per_worked_day = (
        round(total_hours / Decimal(total_worked_days), 2)
        if total_worked_days > 0 else Decimal('0.00')
    )
    gross_ot_days = round(gross_ot_hours / STANDARD_DAY_HOURS, 2)

    return {
        'attendance_rate': attendance_rate,
        'absenteeism_rate': absenteeism_rate,
        'avg_hours_per_worked_day': avg_hours_per_worked_day,
        'gross_ot_hours': gross_ot_hours,
        'gross_ot_days': gross_ot_days,
        'total_leave_days': total_leave_days,
        'off_days_worked': off_days_worked,
    }


def _calc_delta(current, prev, is_pct=False):
    """
    Computes delta against previous period:
    - Absolute change for % metrics
    - Percentage change for counts / hours
    - None if prev is 0 or None
    """
    if prev is None:
        return None
    prev_d = Decimal(str(prev))
    if prev_d == 0:
        return None
    curr_d = Decimal(str(current))
    if is_pct:
        return round(curr_d - prev_d, 1)
    else:
        return round(((curr_d - prev_d) / prev_d) * 100, 1)


def build_attendance_analytics(rows, prev_rows, employees_filter_ids, start_date, end_date):
    """
    Builds attendance analytics without performing any database queries.
    Operates strictly on the provided register rows.
    """
    if isinstance(start_date, str):
        start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
    if isinstance(end_date, str):
        end_date = datetime.strptime(end_date, '%Y-%m-%d').date()

    if employees_filter_ids is not None:
        filter_set = set(employees_filter_ids)
        curr_rows = [r for r in rows if r['employee'].id in filter_set]
        p_rows = [r for r in prev_rows if r['employee'].id in filter_set] if prev_rows else []
    else:
        curr_rows = rows
        p_rows = prev_rows or []

    days_in_range = (end_date - start_date).days + 1

    # ---------------------------------------------------------
    # a) KPIs & Deltas
    # ---------------------------------------------------------
    curr_kpi = _compute_period_kpis(curr_rows)
    prev_kpi = _compute_period_kpis(p_rows) if p_rows else None

    def get_kpi_entry(key, is_pct, lower_is_better=False, is_neutral=False):
        val = curr_kpi[key]
        p_val = prev_kpi[key] if prev_kpi else None
        delta = _calc_delta(val, p_val, is_pct=is_pct)

        is_good = None
        if delta is not None and not is_neutral:
            if delta == 0:
                is_good = None
            elif lower_is_better:
                is_good = delta < 0
            else:
                is_good = delta > 0

        return {
            'value': val,
            'delta': delta,
            'prev_value': p_val,
            'is_good': is_good,
        }

    kpis = {
        'attendance_rate': get_kpi_entry('attendance_rate', is_pct=True, lower_is_better=False),
        'absenteeism_rate': get_kpi_entry('absenteeism_rate', is_pct=True, lower_is_better=True),
        'avg_hours_per_worked_day': get_kpi_entry('avg_hours_per_worked_day', is_pct=False, lower_is_better=False),
        'gross_ot_hours': get_kpi_entry('gross_ot_hours', is_pct=False, lower_is_better=True),
        'gross_ot_days': get_kpi_entry('gross_ot_days', is_pct=False, lower_is_better=True),
        'total_leave_days': get_kpi_entry('total_leave_days', is_pct=False, is_neutral=True),
        'off_days_worked': get_kpi_entry('off_days_worked', is_pct=False, lower_is_better=True),
    }

    # ---------------------------------------------------------
    # b) Daily workforce mix
    # ---------------------------------------------------------
    rows_by_date = defaultdict(list)
    for r in curr_rows:
        rows_by_date[r['date']].append(r)

    daily_labels = []
    mix_office = []
    mix_vessel = []
    mix_office_vessel = []
    mix_leave = []
    mix_absent = []
    mix_rest = []
    mix_training = []

    cur_d = start_date
    while cur_d <= end_date:
        daily_labels.append(f"{cur_d.day} {cur_d.strftime('%b')}")
        d_rows = rows_by_date.get(cur_d, [])
        mix_office.append(sum(1 for r in d_rows if r['status_code'] == 'OFFICE'))
        mix_vessel.append(sum(1 for r in d_rows if r['status_code'] == 'VESSEL'))
        mix_office_vessel.append(sum(1 for r in d_rows if r['status_code'] == 'OFFICE_VESSEL'))
        mix_leave.append(sum(1 for r in d_rows if r['status_code'] in ('ANNUAL_LEAVE', 'SICK_LEAVE', 'PERSONAL_LEAVE')))
        mix_absent.append(sum(1 for r in d_rows if r['status_code'] == 'ABSENT'))
        mix_rest.append(sum(1 for r in d_rows if r['status_code'] == 'REST'))
        mix_training.append(sum(1 for r in d_rows if r['status_code'] == 'TRAINING'))
        cur_d += timedelta(days=1)

    daily_mix = {
        'labels': daily_labels,
        'office': mix_office,
        'vessel': mix_vessel,
        'office_vessel': mix_office_vessel,
        'leave': mix_leave,
        'absent': mix_absent,
        'rest': mix_rest,
        'training': mix_training,
    }

    # ---------------------------------------------------------
    # c) Calendar heatmap (range <= 62 days)
    # ---------------------------------------------------------
    if days_in_range > 62:
        heatmap = None
    else:
        today = timezone.localdate()
        cal_dates = []
        cur_d = start_date
        while cur_d <= end_date:
            d_rows = rows_by_date.get(cur_d, [])
            is_holiday = any(r['status_code'] == 'HOLIDAY' or 'Holiday' in r.get('off_day_label', '') for r in d_rows)
            is_sunday = (cur_d.weekday() == 6)
            cal_dates.append({
                'date': cur_d,
                'day_num': cur_d.day,
                'weekday_letter': cur_d.strftime('%a')[0],
                'is_off': is_sunday or is_holiday,
            })
            cur_d += timedelta(days=1)

        emp_map = {}
        emp_date_map = defaultdict(dict)
        for r in curr_rows:
            emp = r['employee']
            emp_map[emp.id] = emp
            emp_date_map[emp.id][r['date']] = r

        cal_rows = []
        for emp_id in sorted(emp_map.keys(), key=lambda eid: (emp_map[eid].user.get_full_name() or emp_map[eid].user.username).lower()):
            emp = emp_map[emp_id]
            name = emp.user.get_full_name() or emp.user.username
            username = emp.user.username
            emp_dates = emp_date_map[emp_id]

            cells = []
            for d_info in cal_dates:
                d = d_info['date']
                r = emp_dates.get(d)
                if r:
                    code = r.get('status_code', 'OFFICE')
                    label = r.get('status_label') or code.replace('_', ' ').title()
                    t_hours = r.get('total_hours') or Decimal('0.00')
                    ot_h = r.get('ot_hours') or Decimal('0.00')
                else:
                    code = 'NONE'
                    label = 'Upcoming' if d > today else 'No data'
                    t_hours = Decimal('0.00')
                    ot_h = Decimal('0.00')

                d_fmt = f"{d.day:02d} {d.strftime('%b')}"
                if code == 'NONE':
                    tooltip = f"{name} · {d_fmt} · {label}"
                elif t_hours > 0:
                    h_int = int(t_hours)
                    m_int = int(round((t_hours - h_int) * 60))
                    hours_str = f"{h_int}:{m_int:02d} hrs"
                    if ot_h > 0:
                        ot_int = int(ot_h)
                        ot_m = int(round((ot_h - ot_int) * 60))
                        hours_str += f" (OT {ot_int}:{ot_m:02d})"
                    tooltip = f"{name} · {d_fmt} · {label} · {hours_str}"
                else:
                    tooltip = f"{name} · {d_fmt} · {label}"

                cells.append({
                    'date': d,
                    'status_code': code,
                    'status_label': label,
                    'total_hours': t_hours,
                    'ot_hours': ot_h,
                    'tooltip': tooltip,
                    'is_clickable': (code != 'NONE'),
                })

            cal_rows.append({
                'employee': emp,
                'name': name,
                'username': username,
                'cells': cells,
            })

        heatmap = {
            'dates': cal_dates,
            'rows': cal_rows,
        }

    # ---------------------------------------------------------
    # d) OT leaderboard (Top 10 employees by gross OT > 0)
    # ---------------------------------------------------------
    emp_ot_data = defaultdict(lambda: {'ot_hours': Decimal('0.00'), 'total_hours': Decimal('0.00'), 'off_day_ot': Decimal('0.00')})
    emp_objects = {}
    for r in curr_rows:
        emp = r['employee']
        emp_objects[emp.id] = emp
        ot = Decimal(str(r.get('ot_hours') or '0.00'))
        th = Decimal(str(r.get('total_hours') or '0.00'))
        emp_ot_data[emp.id]['ot_hours'] += ot
        emp_ot_data[emp.id]['total_hours'] += th
        if r.get('is_off_day') and ot > 0:
            emp_ot_data[emp.id]['off_day_ot'] += ot

    ot_threshold = OT_ALERT_HOURS_PER_30_DAYS * (Decimal(str(days_in_range)) / Decimal('30'))

    leaderboard_all = []
    for emp_id, ot_info in emp_ot_data.items():
        ot_h = ot_info['ot_hours']
        if ot_h > 0:
            emp = emp_objects[emp_id]
            name = emp.user.get_full_name() or emp.user.username
            designation = emp.rank if (emp.rank and emp.rank != '-') else emp.get_role()
            ot_days = round(ot_h / STANDARD_DAY_HOURS, 2)
            off_day_share = round((ot_info['off_day_ot'] / ot_h) * 100, 1)
            ot_alert = (ot_h > ot_threshold)

            leaderboard_all.append({
                'employee': emp,
                'name': name,
                'username': emp.user.username,
                'designation': designation,
                'ot_hours': ot_h,
                'ot_days': ot_days,
                'total_hours': ot_info['total_hours'],
                'off_day_ot_share': off_day_share,
                'ot_alert': ot_alert,
            })

    leaderboard_all.sort(key=lambda x: x['ot_hours'], reverse=True)
    leaderboard = leaderboard_all[:10]

    max_ot = leaderboard[0]['ot_hours'] if leaderboard else Decimal('1.00')
    for idx, item in enumerate(leaderboard, start=1):
        item['rank'] = idx
        item['bar_width_pct'] = round((item['ot_hours'] / max_ot) * 100, 1)

    # ---------------------------------------------------------
    # e) Absence watchlist (employees with absent days > 0)
    # ---------------------------------------------------------
    emp_rows_grouped = defaultdict(list)
    for r in curr_rows:
        emp_rows_grouped[r['employee'].id].append(r)

    watchlist = []
    for emp_id, emp_rows in emp_rows_grouped.items():
        emp = emp_objects.get(emp_id) or emp_rows[0]['employee']
        emp_rows_sorted = sorted(emp_rows, key=lambda x: x['date'])

        absent_days = sum(1 for r in emp_rows_sorted if r['status_code'] == 'ABSENT')
        if absent_days == 0:
            continue

        spells = 0
        current_spell_length = 0
        in_spell = False
        longest_spell = 0
        absent_weekdays = []

        for r in emp_rows_sorted:
            code = r['status_code']
            if code == 'ABSENT':
                absent_weekdays.append(r['date'].strftime('%A'))
                if not in_spell:
                    in_spell = True
                    spells += 1
                    current_spell_length = 1
                else:
                    current_spell_length += 1
                if current_spell_length > longest_spell:
                    longest_spell = current_spell_length
            elif code in ('SUNDAY', 'HOLIDAY'):
                # Sunday or Holiday doesn't break the run
                pass
            else:
                in_spell = False
                current_spell_length = 0

        bradford_factor = (spells ** 2) * absent_days

        if bradford_factor < BRADFORD_MONITOR:
            level = 'normal'
        elif bradford_factor < BRADFORD_ACTION:
            level = 'monitor'
        else:
            level = 'action'

        weekday_counter = Counter(absent_weekdays)
        most_common_weekday = weekday_counter.most_common(1)[0][0] if absent_weekdays else '-'

        name = emp.user.get_full_name() or emp.user.username
        designation = emp.rank if (emp.rank and emp.rank != '-') else emp.get_role()

        watchlist.append({
            'employee': emp,
            'name': name,
            'username': emp.user.username,
            'designation': designation,
            'absent_days': absent_days,
            'spells': spells,
            'longest_spell': longest_spell,
            'bradford_factor': bradford_factor,
            'most_common_weekday': most_common_weekday,
            'level': level,
        })

    watchlist.sort(key=lambda x: (x['bradford_factor'], x['absent_days']), reverse=True)

    return {
        'kpis': kpis,
        'daily_mix': daily_mix,
        'heatmap': heatmap,
        'leaderboard': leaderboard,
        'watchlist': watchlist,
    }

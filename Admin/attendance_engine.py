from datetime import date, datetime, timedelta
from decimal import Decimal
from collections import defaultdict
from django.conf import settings
from django.db.models import Q

from Admin.models import Holiday, RestDayRule, RosterDay, EmployeeSchedule, SalaryStructure


def is_rest_day(employee, day: date) -> bool:
    """
    Returns True if `day` is a rest day for `employee`.
    When HR_ATTENDANCE_ENGINE_V2 is False, Sunday (weekday() == 6) is the only rest day.
    When V2 is True, checks RestDayRule (uses_roster -> RosterDay, else rest_weekday, fallback Sunday).
    """
    if not getattr(settings, 'HR_ATTENDANCE_ENGINE_V2', False):
        return day.weekday() == 6

    if not employee:
        return day.weekday() == 6

    rule = employee.rest_day_rules.filter(
        effective_from__lte=day
    ).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=day)
    ).order_by('-effective_from', '-created_at').first()

    if rule:
        if rule.uses_roster:
            roster = employee.roster_days.filter(date=day).first()
            if roster:
                return roster.day_type == 'REST'
            return (day.weekday() == rule.rest_weekday) if rule.rest_weekday is not None else (day.weekday() == 6)
        if rule.rest_weekday is not None:
            return day.weekday() == rule.rest_weekday
        return day.weekday() == 6

    return day.weekday() == 6


def rest_day_map(employees, start: date, end: date) -> dict:
    """
    Bulk rest day calculator for multiple employees over [start, end].
    Returns dict: (employee_id, date) -> bool.
    No N+1 queries.
    """
    num_days = (end - start).days + 1
    date_list = [start + timedelta(days=i) for i in range(num_days)]
    res = {}

    if not getattr(settings, 'HR_ATTENDANCE_ENGINE_V2', False):
        for d in date_list:
            is_sun = (d.weekday() == 6)
            for e in employees:
                res[(e.id, d)] = is_sun
        return res

    emp_ids = [e.id for e in employees]
    rules_qs = RestDayRule.objects.filter(
        employee_id__in=emp_ids,
        effective_from__lte=end
    ).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=start)
    ).order_by('effective_from', 'created_at')

    rules_by_emp = defaultdict(list)
    for r in rules_qs:
        rules_by_emp[r.employee_id].append(r)

    roster_qs = RosterDay.objects.filter(
        employee_id__in=emp_ids,
        date__range=(start, end)
    )
    roster_by_emp_date = {(r.employee_id, r.date): r for r in roster_qs}

    for e in employees:
        emp_rules = rules_by_emp.get(e.id, [])
        for d in date_list:
            # Find active rule for date d
            active_rule = None
            for r in emp_rules:
                if r.effective_from <= d and (r.effective_to is None or r.effective_to >= d):
                    active_rule = r

            if active_rule:
                if active_rule.uses_roster:
                    roster = roster_by_emp_date.get((e.id, d))
                    if roster:
                        res[(e.id, d)] = (roster.day_type == 'REST')
                    else:
                        res[(e.id, d)] = (d.weekday() == active_rule.rest_weekday) if active_rule.rest_weekday is not None else (d.weekday() == 6)
                elif active_rule.rest_weekday is not None:
                    res[(e.id, d)] = (d.weekday() == active_rule.rest_weekday)
                else:
                    res[(e.id, d)] = (d.weekday() == 6)
            else:
                res[(e.id, d)] = (d.weekday() == 6)

    return res


def public_holiday_map(employees, start: date, end: date) -> dict:
    """
    Bulk public holiday lookup for multiple employees over [start, end].
    Returns dict: (employee_id, date) -> Holiday or None.
    When V2 is False, returns global holidays for all employees.
    When V2 is True, active holidays matching branch (null = all branches or matching branch).
    """
    res = {}
    if not getattr(settings, 'HR_ATTENDANCE_ENGINE_V2', False):
        holiday_qs = Holiday.objects.filter(date__gte=start, date__lte=end)
        holidays_by_date = {h.date: h for h in holiday_qs}
        for e in employees:
            for d, h in holidays_by_date.items():
                res[(e.id, d)] = h
        return res

    holiday_qs = Holiday.objects.filter(
        date__gte=start,
        date__lte=end,
        is_active=True
    ).select_related('branch')

    # Group holidays by date
    holidays_by_date = defaultdict(list)
    for h in holiday_qs:
        holidays_by_date[h.date].append(h)

    for e in employees:
        for d, h_list in holidays_by_date.items():
            # Check branch-specific first, then all-branches (branch is None)
            matched_holiday = None
            for h in h_list:
                if h.branch_id == e.branch_id and e.branch_id is not None:
                    matched_holiday = h
                    break
            if not matched_holiday:
                for h in h_list:
                    if h.branch_id is None:
                        matched_holiday = h
                        break
            if matched_holiday:
                res[(e.id, d)] = matched_holiday

    return res


def pay_base_salary(employee, day: date) -> Decimal:
    """
    Returns the base monthly salary to use for:
    - OT hourly rate (rate = pay_base_salary / 208)
    - Absence deduction daily rate (rate = pay_base_salary / 26)
    When V2 is on and day >= settings.HR_BASIC_SALARY_FROM:
      uses SalaryStructure effective on `day`.basic; falls back to employee.salary.
    Otherwise returns employee.salary (V1 behaviour).
    """
    if not employee:
        return Decimal('0.00')

    emp_salary = employee.salary or Decimal('0.00')

    if getattr(settings, 'HR_ATTENDANCE_ENGINE_V2', False):
        basic_from = getattr(settings, 'HR_BASIC_SALARY_FROM', None)
        if basic_from is not None:
            if isinstance(basic_from, str):
                basic_from = datetime.strptime(basic_from, '%Y-%m-%d').date()
            if day >= basic_from:
                struct = employee.salary_structures.filter(
                    effective_from__lte=day
                ).filter(
                    Q(effective_to__isnull=True) | Q(effective_to__gte=day)
                ).order_by('-effective_from', '-created_at').first()

                if struct and struct.basic and struct.basic > 0:
                    return struct.basic
                return emp_salary

    return emp_salary


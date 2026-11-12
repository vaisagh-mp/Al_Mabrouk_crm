import calendar
import csv
import io
import math
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from django.db import transaction
from django.db.models import Q, Sum, Count, Min, Max, Exists, OuterRef
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from urllib.parse import urlencode

from Admin.models import Employee, Project, Attendance, WorkOrder, SpareConsumed
from Admin.attendance_register import build_attendance_register
from service_logs.models import EmployeeServiceLog
from service_logs.ot import calculate_office_ot_cost
import logging
from django.conf import settings
from .models import Payroll, Expense, ExpenseCategory, Invoice, JobCost, SpareSale
from .currency import BASE_CURRENCY, CURRENCY_CHOICES, get_rate, to_aed

logger = logging.getLogger(__name__)

PAYROLL_DAYS_DIVISOR = Decimal('26')


def get_finance_start_month(request=None):
    """
    Returns the 1st of the month of the EARLIEST of:
      - Attendance.login_time (local date)
      - EmployeeServiceLog.date
      - Payroll.month
      - Project.invoice_date
      - Expense.date
    Caches the result on the request object if provided.
    If there is no data, returns the current month (1st of month).
    """
    if request is not None and hasattr(request, '_finance_start_month'):
        return request._finance_start_month

    today = timezone.localdate()
    dates = []

    att_min = Attendance.objects.aggregate(m=Min('login_time'))['m']
    if att_min:
        dates.append(timezone.localtime(att_min).date())

    log_min = EmployeeServiceLog.objects.aggregate(m=Min('date'))['m']
    if log_min:
        dates.append(log_min)

    pay_min = Payroll.objects.aggregate(m=Min('month'))['m']
    if pay_min:
        dates.append(pay_min)

    proj_min = Project.objects.filter(invoice_date__isnull=False).aggregate(m=Min('invoice_date'))['m']
    if proj_min:
        dates.append(proj_min)

    exp_min = Expense.objects.aggregate(m=Min('date'))['m']
    if exp_min:
        dates.append(exp_min)

    if dates:
        start_month = min(dates).replace(day=1)
    else:
        start_month = today.replace(day=1)

    if request is not None:
        request._finance_start_month = start_month

    return start_month




def get_month_range(month_date):
    """
    Given any date, returns (first_day, last_day, days_in_month).
    """
    first_day = month_date.replace(day=1)
    days_in_month = calendar.monthrange(first_day.year, first_day.month)[1]
    last_day = first_day.replace(day=days_in_month)
    return first_day, last_day, days_in_month


def calculate_employee_prorated_salary(employee, first_day, last_day, days_in_month):
    """
    Calculates basic salary prorated by calendar days if date_of_join is within the month.
    If joined after the month: returns 0.
    If joined before or on the 1st: full salary.
    """
    salary = employee.salary or Decimal('0.00')
    if salary <= 0:
        return Decimal('0.00')

    doj = employee.date_of_join
    if not doj:
        return salary

    if doj > last_day:
        return Decimal('0.00')
    elif doj >= first_day:
        active_days = (last_day - doj).days + 1
        return round(salary * Decimal(active_days) / Decimal(days_in_month), 2)
    else:
        return salary


def calculate_employee_salary_split(employee, first_day, last_day, days_in_month):
    """
    For HR_PAYROLL_V2:
    Calculates active days and prorates each component of the effective SalaryStructure(s)
    (Basic, Housing, Transport, Other Allowances) by calendar days.
    - If joined after month or left before month: returns None (no payroll row).
    - If mid-month change: prorated by calendar days of each structure.
    - If joiner (date_of_join in month) or leaver (last_working_day in month):
      prorated by active calendar days.
    - If no structure exists: falls back to employee.salary as BASIC.
    """
    doj = employee.date_of_join
    lwd = getattr(employee, 'last_working_day', None)

    if lwd and lwd < first_day:
        return None

    if doj and doj > last_day:
        return None

    active_start = max(first_day, doj) if doj else first_day
    active_end = min(last_day, lwd) if lwd else last_day

    if active_start > active_end:
        return None

    active_days_count = (active_end - active_start).days + 1

    structures = list(
        employee.salary_structures.filter(
            effective_from__lte=active_end
        ).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gte=active_start)
        ).order_by('-effective_from', '-created_at')
    )

    if not structures:
        salary = employee.salary or Decimal('0.00')
        basic = round(salary * Decimal(active_days_count) / Decimal(days_in_month), 2)
        return {
            'basic': basic,
            'housing': Decimal('0.00'),
            'transport': Decimal('0.00'),
            'other': Decimal('0.00'),
            'total': basic,
            'active_days': active_days_count,
        }

    basic_sum = Decimal('0.00')
    housing_sum = Decimal('0.00')
    transport_sum = Decimal('0.00')
    other_sum = Decimal('0.00')

    cur_d = active_start
    while cur_d <= active_end:
        matched = None
        for s in structures:
            if s.effective_from <= cur_d and (s.effective_to is None or s.effective_to >= cur_d):
                matched = s
                break
        if matched:
            basic_sum += (matched.basic or Decimal('0.00')) / Decimal(days_in_month)
            housing_sum += (matched.housing_allowance or Decimal('0.00')) / Decimal(days_in_month)
            transport_sum += (matched.transport_allowance or Decimal('0.00')) / Decimal(days_in_month)
            other_sum += (matched.other_allowances or Decimal('0.00')) / Decimal(days_in_month)
        else:
            salary = employee.salary or Decimal('0.00')
            basic_sum += salary / Decimal(days_in_month)
        cur_d += timedelta(days=1)

    basic = round(basic_sum, 2)
    housing = round(housing_sum, 2)
    transport = round(transport_sum, 2)
    other = round(other_sum, 2)
    total = basic + housing + transport + other

    return {
        'basic': basic,
        'housing': housing,
        'transport': transport,
        'other': other,
        'total': total,
        'active_days': active_days_count,
    }


def get_month_attendance_metrics(first_day, last_day):
    """
    Builds attendance register once for the whole month and loads service logs.
    Returns:
      metrics_by_emp_id = {
          emp_id: {
              'worked_days': int,
              'absent_days': int,
              'leave_days': int,
              'vessel_hours': Decimal,
              'ot_hours': Decimal,
              'vessel_ot_cost': Decimal,
              'office_ot_cost': Decimal,
              'total_ot_cost': Decimal,
          }
      }
    """
    register_rows = build_attendance_register(first_day, last_day)

    rows_by_emp = defaultdict(list)
    for r in register_rows:
        rows_by_emp[r['employee'].id].append(r)

    logs = list(
        EmployeeServiceLog.objects.counting().filter(
            date__range=(first_day, last_day)
        ).select_related('employee')
    )
    logs_by_emp = defaultdict(list)
    for l in logs:
        logs_by_emp[l.employee_id].append(l)

    # All employees in register or logs
    emp_ids = set(rows_by_emp.keys()) | set(logs_by_emp.keys())

    metrics_by_emp = {}
    for emp_id in emp_ids:
        emp_rows = rows_by_emp.get(emp_id, [])
        emp_logs = logs_by_emp.get(emp_id, [])

        emp_obj = emp_rows[0]['employee'] if emp_rows else (emp_logs[0].employee if emp_logs else None)
        salary = emp_obj.salary if emp_obj else Decimal('0.00')

        worked_days = sum(1 for r in emp_rows if r.get('status_code') in ('OFFICE', 'VESSEL', 'OFFICE_VESSEL'))
        absent_days = sum(1 for r in emp_rows if r.get('status_code') == 'ABSENT')
        leave_days = sum(1 for r in emp_rows if r.get('status_code') in ('ANNUAL_LEAVE', 'SICK_LEAVE', 'PERSONAL_LEAVE'))

        # Unpaid days: absent days that are not before date_of_join
        unpaid_days = sum(
            1 for r in emp_rows
            if r.get('status_code') == 'ABSENT' and not (emp_obj and emp_obj.date_of_join and r['date'] < emp_obj.date_of_join)
        )

        leave_v2_on = getattr(settings, 'HR_LEAVE_V2', False)
        if leave_v2_on:
            unpaid_leave = Decimal('0.00')
            for r in emp_rows:
                is_ul = (r.get('status_code') == 'UNPAID_LEAVE')
                is_hd_ul = (r.get('is_half_day') and getattr(r.get('leave_obj'), 'leave_type_ref', None) and r['leave_obj'].leave_type_ref.code == 'UNPAID')
                if is_ul:
                    if r.get('is_half_day'):
                        unpaid_leave += Decimal('0.50')
                    else:
                        unpaid_leave += Decimal('1.00')
                elif is_hd_ul:
                    unpaid_leave += Decimal('0.50')
            unpaid_days = Decimal(str(unpaid_days)) + unpaid_leave

        vessel_hours = sum((l.total_hours or Decimal('0.00')) for l in emp_logs)
        vessel_ot_hours = sum((l.ot_hours or Decimal('0.00')) for l in emp_logs)
        vessel_ot_cost = sum((l.ot_cost or Decimal('0.00')) for l in emp_logs)

        missing_punch_days = sum(1 for r in emp_rows if r.get('status_code') == 'MISSING_PUNCH')
        missing_punch_dates = [r['date'] for r in emp_rows if r.get('status_code') == 'MISSING_PUNCH']
        office_ot_hours = sum((r.get('office_ot_hours') or Decimal('0.00')) for r in emp_rows)
        office_ot_cost = calculate_office_ot_cost(salary, emp_rows, emp_logs, employee=emp_obj)

        total_ot_hours = round(vessel_ot_hours + office_ot_hours, 2)
        total_ot_cost = round(vessel_ot_cost + office_ot_cost, 2)

        # Phase 5: When HR_TIMESHEET_APPROVAL is on, payroll ot_amount includes only APPROVED OTApproval days
        if getattr(settings, 'HR_TIMESHEET_APPROVAL', False):
            from Admin.models import OTApproval
            approval_from = getattr(settings, 'HR_TIMESHEET_APPROVAL_FROM', None)
            if approval_from:
                from datetime import date, datetime
                from django.utils.dateparse import parse_date
                app_from_date = approval_from if isinstance(approval_from, (date, datetime)) else parse_date(str(approval_from))
                if isinstance(app_from_date, datetime):
                    app_from_date = app_from_date.date()

                # For days >= app_from_date, only APPROVED OTApproval rows count
                approved_ot_apps = list(OTApproval.objects.filter(
                    employee_id=emp_id,
                    date__range=(max(first_day, app_from_date), last_day),
                    status='APPROVED'
                ))
                approved_post_cost = sum((a.ot_cost or Decimal('0.00')) for a in approved_ot_apps)
                approved_post_hours = sum((a.ot_hours or Decimal('0.00')) for a in approved_ot_apps)

                # For days < app_from_date, include vessel OT and office OT (same rule as before the switch)
                prior_vessel_ot = sum(
                    (l.ot_cost or Decimal('0.00')) for l in emp_logs
                    if l.date < app_from_date
                )
                prior_vessel_ot_h = sum(
                    (l.ot_hours or Decimal('0.00')) for l in emp_logs
                    if l.date < app_from_date
                )
                prior_rows = [r for r in emp_rows if r.get('date') and r['date'] < app_from_date]
                prior_logs = [l for l in emp_logs if l.date < app_from_date]
                prior_office_ot_h = sum((r.get('office_ot_hours') or Decimal('0.00')) for r in prior_rows)
                prior_office_ot_c = calculate_office_ot_cost(
                    salary,
                    prior_rows,
                    prior_logs,
                    employee=emp_obj
                )

                total_ot_cost = round(approved_post_cost + prior_vessel_ot + prior_office_ot_c, 2)
                total_ot_hours = round(approved_post_hours + prior_vessel_ot_h + prior_office_ot_h, 2)
            else:
                approved_ot_apps = OTApproval.objects.filter(
                    employee_id=emp_id,
                    date__range=(first_day, last_day),
                    status='APPROVED'
                )
                total_ot_cost = round(sum((a.ot_cost or Decimal('0.00')) for a in approved_ot_apps), 2)
                total_ot_hours = round(sum((a.ot_hours or Decimal('0.00')) for a in approved_ot_apps), 2)


        metrics_by_emp[emp_id] = {
            'worked_days': worked_days,
            'absent_days': absent_days,
            'unpaid_days': unpaid_days,
            'leave_days': leave_days,
            'vessel_hours': round(vessel_hours, 2),
            'ot_hours': total_ot_hours,
            'vessel_ot_hours': round(vessel_ot_hours, 2),
            'office_ot_hours': round(office_ot_hours, 2),
            'vessel_ot_cost': round(vessel_ot_cost, 2),
            'office_ot_cost': round(office_ot_cost, 2),
            'total_ot_cost': total_ot_cost,
            'missing_punch_days': missing_punch_days,
            'missing_punch_dates': missing_punch_dates,
        }

    return metrics_by_emp


def get_active_payroll_employees():
    return list(
        Employee.objects.filter(
            user__is_active=True,
            salary__isnull=False,
            salary__gt=0
        ).filter(
            Q(is_employee=True) | Q(is_manager=True) | Q(is_administration=True) | Q(is_hr=True) | Q(is_accountant=True)
        ).select_related('user').order_by('user__first_name', 'user__username')
    )


def generate_payroll(month_date, user=None):
    """
    Creates/updates DRAFT payroll rows for all active employees (user.is_active, has salary).
    - basic_salary: prorated if date_of_join in month (skips employee if joined after month).
    - ot_amount: vessel OT cost + office OT cost from attendance register.
    - absent_days_unpaid and absence_deduction: daily_rate * unpaid_days capped at basic_salary.
    Never overwrites APPROVED or PAID rows.
    Loads existing Payroll rows for the month once into a dictionary.
    Returns (created_count, updated_count, skipped_count).
    """
    first_day, last_day, days_in_month = get_month_range(month_date)

    active_employees = get_active_payroll_employees()
    metrics_by_emp = get_month_attendance_metrics(first_day, last_day)

    existing_payrolls = {
        p.employee_id: p
        for p in Payroll.objects.filter(month=first_day)
    }

    created_count = 0
    updated_count = 0
    skipped_count = 0

    to_create = []
    to_update = []

    v2_payroll = getattr(settings, 'HR_PAYROLL_V2', False)
    if not v2_payroll:
        for emp in active_employees:
            if emp.date_of_join and emp.date_of_join > last_day:
                continue

            basic = calculate_employee_prorated_salary(emp, first_day, last_day, days_in_month)
            emp_metrics = metrics_by_emp.get(emp.id, {})
            ot_amount = emp_metrics.get('total_ot_cost', Decimal('0.00'))
            ot_hours = emp_metrics.get('ot_hours', Decimal('0.00'))
            unpaid_days = emp_metrics.get('unpaid_days', 0)

            # Daily rate uses the FULL monthly salary (or basic when V2 on), so a
            # mid-month joiner is not under-deducted; the result is capped at the prorated basic.
            from Admin.attendance_engine import pay_base_salary
            emp_salary = pay_base_salary(emp, first_day)
            daily_rate = (emp_salary / PAYROLL_DAYS_DIVISOR) if emp_salary else Decimal('0.00')
            raw_deduction = round(daily_rate * Decimal(str(unpaid_days)), 2)
            absence_deduction = min(raw_deduction, basic)

            if (ot_amount > Decimal('0.00') and ot_hours == Decimal('0.00')) or (ot_hours > Decimal('0.00') and ot_amount == Decimal('0.00')):
                logger.warning(
                    "OT hours/amount mismatch for %s (%s) in %s: OT Amount=%s, OT Hours=%s",
                    emp.user.get_full_name() or emp.user.username,
                    emp.id,
                    first_day.strftime('%Y-%m'),
                    ot_amount,
                    ot_hours
                )

            existing = existing_payrolls.get(emp.id)
            if existing:
                if existing.status in ('APPROVED', 'PAID'):
                    skipped_count += 1
                else:
                    existing.basic_salary = basic
                    existing.ot_amount = ot_amount
                    existing.absent_days_unpaid = Decimal(str(unpaid_days))
                    existing.absence_deduction = absence_deduction
                    existing.updated_at = timezone.now()
                    to_update.append(existing)
                    updated_count += 1
            else:
                to_create.append(
                    Payroll(
                        employee=emp,
                        month=first_day,
                        basic_salary=basic,
                        allowances=Decimal('0.00'),
                        ot_amount=ot_amount,
                        deductions=Decimal('0.00'),
                        absent_days_unpaid=Decimal(str(unpaid_days)),
                        absence_deduction=absence_deduction,
                        absence_waived=False,
                        notes='',
                        status='DRAFT',
                        created_by=user,
                    )
                )
                created_count += 1

        if to_create:
            Payroll.objects.bulk_create(to_create)
        if to_update:
            Payroll.objects.bulk_update(to_update, ['basic_salary', 'ot_amount', 'absent_days_unpaid', 'absence_deduction', 'updated_at'])
    else:
        from finance.models import AdvanceInstalment, PayrollLine, SalaryAdvance
        for emp in active_employees:
            split = calculate_employee_salary_split(emp, first_day, last_day, days_in_month)
            if split is None:
                continue

            basic = split['basic']
            housing = split['housing']
            transport = split['transport']
            other_allowance = split['other']
            allowances_base = housing + transport + other_allowance

            emp_metrics = metrics_by_emp.get(emp.id, {})
            ot_amount = emp_metrics.get('total_ot_cost', Decimal('0.00'))
            ot_hours = emp_metrics.get('ot_hours', Decimal('0.00'))
            unpaid_days = emp_metrics.get('unpaid_days', 0)

            from Admin.attendance_engine import pay_base_salary
            emp_salary = pay_base_salary(emp, first_day)
            daily_rate = (emp_salary / PAYROLL_DAYS_DIVISOR) if emp_salary else Decimal('0.00')
            raw_deduction = round(daily_rate * Decimal(str(unpaid_days)), 2)
            earnings_before_ot = basic + allowances_base
            absence_deduction = min(raw_deduction, earnings_before_ot)

            absent_days_count = emp_metrics.get('absent_days', 0)
            leave_days_unpaid = max(Decimal('0.00'), Decimal(str(unpaid_days)) - Decimal(str(absent_days_count)))
            if leave_days_unpaid > Decimal('0.00'):
                unpaid_leave_amount = min(absence_deduction, round(daily_rate * leave_days_unpaid, 2))
                absence_amount = max(Decimal('0.00'), absence_deduction - unpaid_leave_amount)
            else:
                absence_amount = absence_deduction
                unpaid_leave_amount = Decimal('0.00')

            existing = existing_payrolls.get(emp.id)
            if existing:
                if existing.status in ('APPROVED', 'PAID'):
                    skipped_count += 1
                    continue
                payroll = existing
                updated_count += 1
            else:
                payroll = Payroll(
                    employee=emp,
                    month=first_day,
                    basic_salary=basic,
                    allowances=allowances_base,
                    ot_amount=ot_amount,
                    deductions=Decimal('0.00'),
                    absent_days_unpaid=Decimal(str(unpaid_days)),
                    absence_deduction=absence_deduction,
                    absence_waived=False,
                    notes='',
                    status='DRAFT',
                    created_by=user,
                )
                payroll.save()
                existing_payrolls[emp.id] = payroll
                created_count += 1

            # Preserve manual lines
            manual_lines = list(payroll.lines.filter(source='manual'))
            manual_earnings = sum((l.amount or Decimal('0.00')) for l in manual_lines if l.kind == 'EARNING')
            manual_deductions = sum((l.amount or Decimal('0.00')) for l in manual_lines if l.kind == 'DEDUCTION')

            # Delete existing auto lines
            payroll.lines.filter(source='auto').delete()

            # Handle Advance instalments for this month
            instalments = list(
                AdvanceInstalment.objects.filter(
                    advance__employee=emp,
                    advance__status='ACTIVE',
                    month=first_day,
                    status='SCHEDULED'
                ).order_by('id')
            )

            gross_calc = basic + allowances_base + manual_earnings + ot_amount
            eff_absence = Decimal('0.00') if payroll.absence_waived else absence_deduction
            deductions_before_advance = eff_absence + manual_deductions
            available_net = max(Decimal('0.00'), gross_calc - deductions_before_advance)

            total_advance_ded = Decimal('0.00')
            for inst in instalments:
                if inst.amount <= available_net:
                    deduct = inst.amount
                    available_net -= deduct
                else:
                    deduct = available_net
                    rem = inst.amount - deduct
                    available_net = Decimal('0.00')
                    if rem > Decimal('0.00'):
                        last_inst = inst.advance.instalments_schedule.order_by('-month').first()
                        if last_inst:
                            if last_inst.month.month == 12:
                                next_m = date(last_inst.month.year + 1, 1, 1)
                            else:
                                next_m = date(last_inst.month.year, last_inst.month.month + 1, 1)
                        else:
                            next_m = first_day
                        AdvanceInstalment.objects.create(
                            advance=inst.advance,
                            month=next_m,
                            amount=rem,
                            status='SCHEDULED',
                            sort_order=(last_inst.sort_order + 1 if last_inst else 1)
                        )
                        inst.amount = deduct
                        inst.save(update_fields=['amount'])
                        inst.advance.remarks = (inst.advance.remarks or '') + f"\n[{timezone.localdate()}] Partial deduction of AED {deduct} in {first_day.strftime('%b %Y')}; remaining AED {rem} deferred to {next_m.strftime('%b %Y')}."
                        inst.advance.save(update_fields=['remarks'])

                if deduct > Decimal('0.00'):
                    PayrollLine.objects.create(
                        payroll=payroll,
                        kind='DEDUCTION',
                        code='ADVANCE',
                        description=f"Salary Advance ({inst.advance_id})",
                        amount=deduct,
                        source='auto',
                        sort_order=50,
                    )
                    inst.payroll = payroll
                    inst.save(update_fields=['payroll'])
                    total_advance_ded += deduct

            # Auto Earning lines
            PayrollLine.objects.create(
                payroll=payroll,
                kind='EARNING',
                code='BASIC',
                description='Basic Salary',
                amount=basic,
                source='auto',
                sort_order=10
            )
            if housing > Decimal('0.00'):
                PayrollLine.objects.create(
                    payroll=payroll,
                    kind='EARNING',
                    code='HOUSING',
                    description='Housing Allowance',
                    amount=housing,
                    source='auto',
                    sort_order=20
                )
            if transport > Decimal('0.00'):
                PayrollLine.objects.create(
                    payroll=payroll,
                    kind='EARNING',
                    code='TRANSPORT',
                    description='Transport Allowance',
                    amount=transport,
                    source='auto',
                    sort_order=30
                )
            if other_allowance > Decimal('0.00'):
                PayrollLine.objects.create(
                    payroll=payroll,
                    kind='EARNING',
                    code='OTHER_ALLOWANCE',
                    description='Other Allowance',
                    amount=other_allowance,
                    source='auto',
                    sort_order=40
                )
            if ot_amount > Decimal('0.00'):
                PayrollLine.objects.create(
                    payroll=payroll,
                    kind='EARNING',
                    code='OT',
                    description=f"Overtime ({ot_hours} hrs)",
                    amount=ot_amount,
                    source='auto',
                    sort_order=50
                )

            # Auto Deduction lines
            if absence_amount > Decimal('0.00'):
                PayrollLine.objects.create(
                    payroll=payroll,
                    kind='DEDUCTION',
                    code='ABSENCE',
                    description='Absence Deduction',
                    amount=absence_amount,
                    source='auto',
                    sort_order=10
                )
            if unpaid_leave_amount > Decimal('0.00'):
                PayrollLine.objects.create(
                    payroll=payroll,
                    kind='DEDUCTION',
                    code='UNPAID_LEAVE',
                    description='Unpaid Leave Deduction',
                    amount=unpaid_leave_amount,
                    source='auto',
                    sort_order=20
                )

            # Sync totals
            payroll.basic_salary = basic
            payroll.allowances = allowances_base + manual_earnings
            payroll.ot_amount = ot_amount
            payroll.absent_days_unpaid = Decimal(str(unpaid_days))
            payroll.absence_deduction = absence_deduction
            payroll.deductions = total_advance_ded + manual_deductions
            payroll.updated_at = timezone.now()
            payroll.save()

    return created_count, updated_count, skipped_count


def get_payroll_list_with_metrics(month_date):
    """
    Returns (payrolls, totals, metrics_by_emp) for a given month.
    Attaches display metrics (worked_days, absent_days, unpaid_days, leave_days, vessel_hours, ot_hours, ot_mismatch)
    to each payroll instance.
    """
    first_day, last_day, _ = get_month_range(month_date)
    metrics_by_emp = get_month_attendance_metrics(first_day, last_day)

    payrolls = list(
        Payroll.objects.filter(month=first_day)
        .select_related('employee', 'employee__user')
        .order_by('employee__user__first_name', 'employee__user__username')
    )

    total_basic = Decimal('0.00')
    total_allowances = Decimal('0.00')
    total_ot = Decimal('0.00')
    total_deductions = Decimal('0.00')
    total_unpaid_days = Decimal('0.00')
    total_absence_ded = Decimal('0.00')
    total_gross = Decimal('0.00')
    total_net = Decimal('0.00')

    for p in payrolls:
        m = metrics_by_emp.get(p.employee_id, {
            'worked_days': 0,
            'absent_days': 0,
            'unpaid_days': 0,
            'leave_days': 0,
            'vessel_hours': Decimal('0.00'),
            'ot_hours': Decimal('0.00'),
        })
        p.worked_days = m['worked_days']
        p.absent_days = m['absent_days']
        p.unpaid_days = p.absent_days_unpaid if getattr(p, 'absent_days_unpaid', None) is not None else Decimal(str(m['unpaid_days']))
        p.leave_days = m['leave_days']
        p.vessel_hours = m['vessel_hours']
        p.ot_hours = m['ot_hours']
        p.missing_punch_days = m.get('missing_punch_days', 0)
        p.missing_punch_dates = m.get('missing_punch_dates', [])
        p.ot_mismatch = (p.ot_amount > Decimal('0.00') and p.ot_hours == Decimal('0.00')) or (p.ot_hours > Decimal('0.00') and p.ot_amount == Decimal('0.00'))

        total_basic += p.basic_salary or Decimal('0.00')
        total_allowances += p.allowances or Decimal('0.00')
        total_ot += p.ot_amount or Decimal('0.00')
        total_deductions += p.deductions or Decimal('0.00')
        total_unpaid_days += p.absent_days_unpaid or Decimal('0.00')
        total_absence_ded += p.effective_absence_deduction
        total_gross += p.gross
        total_net += p.net_pay

    totals = {
        'basic': total_basic,
        'allowances': total_allowances,
        'ot': total_ot,
        'deductions': total_deductions,
        'unpaid_days': total_unpaid_days,
        'absence_deduction': total_absence_ded,
        'missing_punch_days': sum(getattr(p, 'missing_punch_days', 0) for p in payrolls),
        'gross': total_gross,
        'net': total_net,
    }

    return payrolls, totals, metrics_by_emp


def payroll_excel_response(payrolls, totals, month_date):
    month_str = month_date.strftime('%B_%Y')
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Payroll {month_date.strftime('%b %Y')}"
    ws.views.sheetView[0].showGridLines = True

    COLOR_PRIMARY = "1F3A52"
    COLOR_TOTAL = "F1F5F9"
    COLOR_ZEBRA = "F8FAFC"

    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_data = Font(name="Calibri", size=10, color="1A202C")
    font_total = Font(name="Calibri", size=10, bold=True, color="1A202C")

    fill_primary = PatternFill(start_color=COLOR_PRIMARY, end_color=COLOR_PRIMARY, fill_type="solid")
    fill_zebra = PatternFill(start_color=COLOR_ZEBRA, end_color=COLOR_ZEBRA, fill_type="solid")
    fill_total = PatternFill(start_color=COLOR_TOTAL, end_color=COLOR_TOTAL, fill_type="solid")

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    thin_border_side = Side(border_style="thin", color="CBD5E1")
    border_cell = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    border_total = Border(
        left=thin_border_side, right=thin_border_side,
        top=Side(border_style="medium", color="1F3A52"),
        bottom=Side(border_style="double", color="1F3A52")
    )

    v2_payroll = getattr(settings, 'HR_PAYROLL_V2', False)
    if v2_payroll:
        headers = [
            "Employee", "Code", "Designation",
            "Basic (AED)", "Housing (AED)", "Transport (AED)", "Other Allowance (AED)", "OT Amount (AED)", "Other Earnings (AED)", "Gross (AED)",
            "Absence Ded. (AED)", "Unpaid Leave (AED)", "Advance Ded. (AED)", "Other Ded. (AED)", "Total Deductions (AED)", "Net Pay (AED)",
            "Status", "Notes"
        ]
        ws.row_dimensions[1].height = 26
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = font_header
            cell.fill = fill_primary
            cell.alignment = align_center
            cell.border = border_cell

        current_row = 2
        sum_basic = Decimal('0.00')
        sum_housing = Decimal('0.00')
        sum_transport = Decimal('0.00')
        sum_other_allow = Decimal('0.00')
        sum_ot = Decimal('0.00')
        sum_other_earn = Decimal('0.00')
        sum_gross = Decimal('0.00')
        sum_abs_ded = Decimal('0.00')
        sum_ul_ded = Decimal('0.00')
        sum_adv_ded = Decimal('0.00')
        sum_other_ded = Decimal('0.00')
        sum_tot_ded = Decimal('0.00')
        sum_net = Decimal('0.00')

        for idx, p in enumerate(payrolls):
            row_fill = fill_zebra if idx % 2 == 1 else None
            ws.row_dimensions[current_row].height = 20

            emp_name = p.employee.user.get_full_name() or p.employee.user.username
            emp_code = p.employee.employee_code or f"AMMS-{p.employee.id:04d}"

            lines = list(p.lines.all())
            lines_by_code = defaultdict(Decimal)
            for l in lines:
                lines_by_code[l.code] += (l.amount or Decimal('0.00'))

            if not lines:
                # Fallback to model fields
                val_basic = p.basic_salary or Decimal('0.00')
                val_housing = Decimal('0.00')
                val_transport = Decimal('0.00')
                val_other_allow = p.allowances or Decimal('0.00')
                val_ot = p.ot_amount or Decimal('0.00')
                val_other_earn = Decimal('0.00')
                val_abs_ded = p.effective_absence_deduction
                val_ul_ded = Decimal('0.00')
                val_adv_ded = Decimal('0.00')
                val_other_ded = p.deductions or Decimal('0.00')
            else:
                val_basic = lines_by_code.get('BASIC', Decimal('0.00'))
                val_housing = lines_by_code.get('HOUSING', Decimal('0.00'))
                val_transport = lines_by_code.get('TRANSPORT', Decimal('0.00'))
                val_other_allow = lines_by_code.get('OTHER_ALLOWANCE', Decimal('0.00'))
                val_ot = lines_by_code.get('OT', Decimal('0.00'))
                val_other_earn = sum(l.amount for l in lines if l.kind == 'EARNING' and l.code == 'OTHER')
                val_abs_ded = lines_by_code.get('ABSENCE', Decimal('0.00'))
                if p.absence_waived:
                    val_abs_ded = Decimal('0.00')
                val_ul_ded = lines_by_code.get('UNPAID_LEAVE', Decimal('0.00'))
                if p.absence_waived:
                    val_ul_ded = Decimal('0.00')
                val_adv_ded = lines_by_code.get('ADVANCE', Decimal('0.00'))
                val_other_ded = sum(l.amount for l in lines if l.kind == 'DEDUCTION' and l.code == 'OTHER')

            val_gross = p.gross
            val_tot_ded = (val_abs_ded + val_ul_ded + val_adv_ded + val_other_ded)
            val_net = p.net_pay

            sum_basic += val_basic
            sum_housing += val_housing
            sum_transport += val_transport
            sum_other_allow += val_other_allow
            sum_ot += val_ot
            sum_other_earn += val_other_earn
            sum_gross += val_gross
            sum_abs_ded += val_abs_ded
            sum_ul_ded += val_ul_ded
            sum_adv_ded += val_adv_ded
            sum_other_ded += val_other_ded
            sum_tot_ded += val_tot_ded
            sum_net += val_net

            row_data = [
                (emp_name, align_left, font_data, "text"),
                (emp_code, align_center, font_data, "text"),
                (p.employee.rank or "—", align_left, font_data, "text"),
                (float(val_basic), align_right, font_data, "0.00"),
                (float(val_housing), align_right, font_data, "0.00"),
                (float(val_transport), align_right, font_data, "0.00"),
                (float(val_other_allow), align_right, font_data, "0.00"),
                (float(val_ot), align_right, font_data, "0.00"),
                (float(val_other_earn), align_right, font_data, "0.00"),
                (float(val_gross), align_right, font_data, "0.00"),
                (float(val_abs_ded), align_right, font_data, "0.00"),
                (float(val_ul_ded), align_right, font_data, "0.00"),
                (float(val_adv_ded), align_right, font_data, "0.00"),
                (float(val_other_ded), align_right, font_data, "0.00"),
                (float(val_tot_ded), align_right, font_data, "0.00"),
                (float(val_net), align_right, font_data, "0.00"),
                (p.get_status_display(), align_center, font_data, "text"),
                (p.notes or "", align_left, font_data, "text"),
            ]
            for col_idx, (val, alignment, font, kind) in enumerate(row_data, start=1):
                cell = ws.cell(row=current_row, column=col_idx, value=val)
                cell.alignment = alignment
                cell.font = font
                cell.border = border_cell
                if row_fill:
                    cell.fill = row_fill
                if kind == "0.00":
                    cell.number_format = "#,##0.00"
            current_row += 1

        # Totals Row
        ws.row_dimensions[current_row].height = 22
        total_data = [
            ("TOTAL", align_left, font_total, "text"),
            ("", align_center, font_total, "text"),
            ("", align_left, font_total, "text"),
            (float(sum_basic), align_right, font_total, "0.00"),
            (float(sum_housing), align_right, font_total, "0.00"),
            (float(sum_transport), align_right, font_total, "0.00"),
            (float(sum_other_allow), align_right, font_total, "0.00"),
            (float(sum_ot), align_right, font_total, "0.00"),
            (float(sum_other_earn), align_right, font_total, "0.00"),
            (float(sum_gross), align_right, font_total, "0.00"),
            (float(sum_abs_ded), align_right, font_total, "0.00"),
            (float(sum_ul_ded), align_right, font_total, "0.00"),
            (float(sum_adv_ded), align_right, font_total, "0.00"),
            (float(sum_other_ded), align_right, font_total, "0.00"),
            (float(sum_tot_ded), align_right, font_total, "0.00"),
            (float(sum_net), align_right, font_total, "0.00"),
            ("", align_center, font_total, "text"),
            ("", align_left, font_total, "text"),
        ]
        for col_idx, (val, alignment, font, kind) in enumerate(total_data, start=1):
            cell = ws.cell(row=current_row, column=col_idx, value=val)
            cell.alignment = alignment
            cell.font = font
            cell.fill = fill_total
            cell.border = border_total
            if kind == "0.00":
                cell.number_format = "#,##0.00"

        ws.freeze_panes = "D2"
        for col in ws.columns:
            col_letter = get_column_letter(col[0].column)
            max_len = 0
            for cell in col:
                val_str = str(cell.value or '')
                if len(val_str) > max_len:
                    max_len = len(val_str)
            ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        response = HttpResponse(
            output.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="payroll_{month_str}.xlsx"'
        return response

    headers = [
        "Employee", "Designation", "Basic (AED)", "Allowances (AED)", "OT Amount (AED)",
        "Deductions (AED)", "Unpaid Days", "Absence Ded. (AED)", "Gross (AED)", "Net Pay (AED)", "Worked Days", "Absent Days",
        "Leave Days", "Vessel Hours", "OT Hours", "Status", "Notes"
    ]

    ws.row_dimensions[1].height = 26
    for col_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_primary
        cell.alignment = align_center
        cell.border = border_cell

    current_row = 2
    for idx, p in enumerate(payrolls):
        row_fill = fill_zebra if idx % 2 == 1 else None
        ws.row_dimensions[current_row].height = 20

        emp_name = p.employee.user.get_full_name() or p.employee.user.username
        values = [
            (emp_name, align_left, font_data, "text"),
            (p.employee.rank or "—", align_left, font_data, "text"),
            (float(p.basic_salary or 0), align_right, font_data, "0.00"),
            (float(p.allowances or 0), align_right, font_data, "0.00"),
            (float(p.ot_amount or 0), align_right, font_data, "0.00"),
            (float(p.deductions or 0), align_right, font_data, "0.00"),
            (float(p.absent_days_unpaid or 0), align_center, font_data, "0.00"),
            (float(p.effective_absence_deduction), align_right, font_data, "0.00"),
            (float(p.gross or 0), align_right, font_data, "0.00"),
            (float(p.net_pay or 0), align_right, font_data, "0.00"),
            (getattr(p, 'worked_days', 0), align_center, font_data, "int"),
            (getattr(p, 'absent_days', 0), align_center, font_data, "int"),
            (getattr(p, 'leave_days', 0), align_center, font_data, "int"),
            (float(getattr(p, 'vessel_hours', 0)), align_right, font_data, "0.00"),
            (float(getattr(p, 'ot_hours', 0)), align_right, font_data, "0.00"),
            (p.get_status_display(), align_center, font_data, "text"),
            (p.notes or "", align_left, font_data, "text"),
        ]

        for col_idx, (val, alignment, font, kind) in enumerate(values, start=1):
            cell = ws.cell(row=current_row, column=col_idx, value=val)
            cell.alignment = alignment
            cell.font = font
            cell.border = border_cell
            if row_fill:
                cell.fill = row_fill
            if kind == "0.00":
                cell.number_format = "#,##0.00"

        current_row += 1

    # Totals Row
    ws.row_dimensions[current_row].height = 22
    total_values = [
        ("TOTAL", align_left, font_total, "text"),
        ("", align_left, font_total, "text"),
        (float(totals.get('basic', 0)), align_right, font_total, "0.00"),
        (float(totals.get('allowances', 0)), align_right, font_total, "0.00"),
        (float(totals.get('ot', 0)), align_right, font_total, "0.00"),
        (float(totals.get('deductions', 0)), align_right, font_total, "0.00"),
        (float(totals.get('unpaid_days', 0)), align_center, font_total, "0.00"),
        (float(totals.get('absence_deduction', 0)), align_right, font_total, "0.00"),
        (float(totals.get('gross', 0)), align_right, font_total, "0.00"),
        (float(totals.get('net', 0)), align_right, font_total, "0.00"),
        ("", align_center, font_total, "text"),
        ("", align_center, font_total, "text"),
        ("", align_center, font_total, "text"),
        ("", align_right, font_total, "text"),
        ("", align_right, font_total, "text"),
        ("", align_center, font_total, "text"),
        ("", align_left, font_total, "text"),
    ]
    for col_idx, (val, alignment, font, kind) in enumerate(total_values, start=1):
        cell = ws.cell(row=current_row, column=col_idx, value=val)
        cell.alignment = alignment
        cell.font = font
        cell.fill = fill_total
        cell.border = border_total
        if kind == "0.00":
            cell.number_format = "#,##0.00"

    ws.freeze_panes = "C2"

    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            val_str = str(cell.value or '')
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="payroll_{month_str}.xlsx"'
    return response


def expenses_excel_response(expenses, totals, filename_suffix="all"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Expenses"
    ws.views.sheetView[0].showGridLines = True

    COLOR_PRIMARY = "1F3A52"
    COLOR_TOTAL = "F1F5F9"
    COLOR_ZEBRA = "F8FAFC"

    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_data = Font(name="Calibri", size=10, color="1A202C")
    font_total = Font(name="Calibri", size=10, bold=True, color="1A202C")

    fill_primary = PatternFill(start_color=COLOR_PRIMARY, end_color=COLOR_PRIMARY, fill_type="solid")
    fill_zebra = PatternFill(start_color=COLOR_ZEBRA, end_color=COLOR_ZEBRA, fill_type="solid")
    fill_total = PatternFill(start_color=COLOR_TOTAL, end_color=COLOR_TOTAL, fill_type="solid")

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    thin_border_side = Side(border_style="thin", color="CBD5E1")
    border_cell = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    border_total = Border(
        left=thin_border_side, right=thin_border_side,
        top=Side(border_style="medium", color="1F3A52"),
        bottom=Side(border_style="double", color="1F3A52")
    )

    headers = [
        "Sl", "Date", "Category", "Description", "Vendor", "Payment Method", "Paid By", "Reference #",
        "Currency", "Rate", "Amount", "VAT Amount", "Amount (AED)", "VAT Amount (AED)", "Total Incl. VAT (AED)"
    ]

    ws.row_dimensions[1].height = 26
    for col_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_primary
        cell.alignment = align_center
        cell.border = border_cell

    current_row = 2
    for idx, exp in enumerate(expenses):
        row_fill = fill_zebra if idx % 2 == 1 else None
        ws.row_dimensions[current_row].height = 20

        exp_amount_aed = float(exp.amount_aed or 0)
        exp_vat_aed = float(exp.vat_amount_aed or 0)
        total_incl_vat_aed = exp_amount_aed + exp_vat_aed
        paid_by_str = (exp.created_by.get_full_name() or exp.created_by.username) if exp.created_by else "—"

        values = [
            (idx + 1, align_center, font_data, "int"),
            (exp.date.strftime('%Y-%m-%d') if exp.date else "—", align_center, font_data, "text"),
            (exp.category.name if exp.category else "—", align_left, font_data, "text"),
            (exp.description or "", align_left, font_data, "text"),
            (exp.vendor or "—", align_left, font_data, "text"),
            ("—", align_left, font_data, "text"),
            (paid_by_str, align_left, font_data, "text"),
            (exp.reference_no or "—", align_left, font_data, "text"),
            (exp.currency, align_center, font_data, "text"),
            (float(exp.exchange_rate or 1), align_right, font_data, "rate"),
            (float(exp.amount or 0), align_right, font_data, "0.00"),
            (float(exp.vat_amount or 0), align_right, font_data, "0.00"),
            (exp_amount_aed, align_right, font_data, "0.00"),
            (exp_vat_aed, align_right, font_data, "0.00"),
            (total_incl_vat_aed, align_right, font_data, "0.00"),
        ]

        for col_idx, (val, alignment, font, kind) in enumerate(values, start=1):
            cell = ws.cell(row=current_row, column=col_idx, value=val)
            cell.alignment = alignment
            cell.font = font
            cell.border = border_cell
            if row_fill:
                cell.fill = row_fill
            if kind == "0.00":
                cell.number_format = "#,##0.00"
            elif kind == "rate":
                cell.number_format = "#,##0.0000"
            elif kind == "int":
                cell.number_format = "#,##0"

        current_row += 1

    # Totals Row
    ws.row_dimensions[current_row].height = 22
    tot_amt = sum((float(exp.amount_aed or 0) for exp in expenses), 0.0)
    tot_vat = sum((float(exp.vat_amount_aed or 0) for exp in expenses), 0.0)
    tot_total = tot_amt + tot_vat

    total_values = [
        ("TOTAL (AED)", align_left, font_total, "text"),
        ("", align_left, font_total, "text"),
        ("", align_left, font_total, "text"),
        ("", align_left, font_total, "text"),
        ("", align_left, font_total, "text"),
        ("", align_left, font_total, "text"),
        ("", align_left, font_total, "text"),
        ("", align_left, font_total, "text"),
        ("", align_center, font_total, "text"),
        ("", align_right, font_total, "text"),
        ("", align_right, font_total, "text"),
        ("", align_right, font_total, "text"),
        (tot_amt, align_right, font_total, "0.00"),
        (tot_vat, align_right, font_total, "0.00"),
        (tot_total, align_right, font_total, "0.00"),
    ]
    for col_idx, (val, alignment, font, kind) in enumerate(total_values, start=1):
        cell = ws.cell(row=current_row, column=col_idx, value=val)
        cell.alignment = alignment
        cell.font = font
        cell.fill = fill_total
        cell.border = border_total
        if kind == "0.00":
            cell.number_format = "#,##0.00"

    ws.freeze_panes = "C2"

    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            val_str = str(cell.value or '')
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="expenses_{filename_suffix}.xlsx"'
    return response


def calculate_month_estimated_payroll(first_day, last_day, days_in_month, active_employees, month_rows_by_emp, month_logs_by_emp, return_breakdown=False):
    """
    Calculates estimated total payroll cost for a month using active employees,
    prorated salary, unpaid absence deductions, and attendance/vessel OT metrics.
    Skips employees whose date_of_join is after the month.
    """
    total_est = Decimal('0.00')
    total_ot_all = Decimal('0.00')
    for emp in active_employees:
        if emp.date_of_join and emp.date_of_join > last_day:
            continue
        basic = calculate_employee_prorated_salary(emp, first_day, last_day, days_in_month)
        emp_rows = month_rows_by_emp.get(emp.id, [])
        emp_logs = month_logs_by_emp.get(emp.id, [])
        vessel_ot_cost = sum((l.ot_cost or Decimal('0.00')) for l in emp_logs)
        office_ot_cost = calculate_office_ot_cost(emp.salary, emp_rows, emp_logs, employee=emp)
        total_ot = vessel_ot_cost + office_ot_cost

        emp_cost = basic + total_ot
        total_est += emp_cost
        total_ot_all += total_ot
    if return_breakdown:
        return round(total_est, 2), round(total_ot_all, 2)
    return round(total_est, 2)


def pnl_for_range(start_date, end_date, granularity='month', request=None):
    """
    P&L engine for date range and granularity ('week', 'month', 'year').
    Future months (after current local month) are excluded.
    Returns:
      {
         'buckets': [bucket_dict, ...],
         'totals': {
             'revenue': Decimal,
             'payroll_cost': Decimal,
             'project_direct_cost': Decimal,
             'overhead_cost': Decimal,
             'total_cost': Decimal,
             'profit': Decimal,
             'margin_pct': Decimal or None,
             'engineer_days': int,
             'revenue_per_engineer_day': Decimal or None,
         }
      }
    """
    today = timezone.localdate()
    current_month_first = today.replace(day=1)
    fin_start_month = get_finance_start_month(request)

    if start_date > end_date:
        return {'buckets': [], 'totals': {}}

    if granularity == 'month':
        start_date = start_date.replace(day=1)
        if end_date.year == today.year and end_date.month == today.month:
            effective_end = today
        else:
            days_in_end_m = calendar.monthrange(end_date.year, end_date.month)[1]
            end_date = end_date.replace(day=days_in_end_m)
            effective_end = min(end_date, today)
    else:
        effective_end = min(end_date, today)

    if start_date > effective_end:
        return {'buckets': [], 'totals': {}}

    buckets_meta = []
    if granularity == 'month':
        cur = start_date.replace(day=1)
        while cur <= effective_end and cur <= current_month_first:
            days_in_m = calendar.monthrange(cur.year, cur.month)[1]
            b_start = cur
            b_end = min(cur.replace(day=days_in_m), effective_end)
            key = cur.strftime('%Y-%m')
            label = cur.strftime('%B %Y')
            buckets_meta.append({
                'key': key,
                'label': label,
                'start_date': b_start,
                'end_date': b_end,
                'month_date': cur,
                'days_in_month': days_in_m,
            })
            if cur.month == 12:
                cur = date(cur.year + 1, 1, 1)
            else:
                cur = date(cur.year, cur.month + 1, 1)

    elif granularity == 'week':
        cur = start_date - timedelta(days=start_date.weekday())
        while cur <= effective_end:
            w_end = cur + timedelta(days=6)
            iso_year, iso_week, _ = cur.isocalendar()
            key = f"{iso_year}-W{iso_week:02d}"
            label = f"W{iso_week:02d} ({cur.strftime('%d %b')} - {w_end.strftime('%d %b')})"
            buckets_meta.append({
                'key': key,
                'label': label,
                'start_date': cur,
                'end_date': w_end,
            })
            cur += timedelta(days=7)

    elif granularity == 'year':
        for y in range(start_date.year, min(effective_end.year, today.year) + 1):
            b_start = date(y, 1, 1)
            b_end = min(date(y, 12, 31), effective_end)
            buckets_meta.append({
                'key': str(y),
                'label': str(y),
                'start_date': b_start,
                'end_date': b_end,
                'year': y,
            })

    elif granularity == 'range':
        b_end = min(end_date, today)
        key = f"{start_date.isoformat()}_{b_end.isoformat()}"
        label = f"{start_date.strftime('%d %b %Y')} - {b_end.strftime('%d %b %Y')}"
        buckets_meta.append({
            'key': key,
            'label': label,
            'start_date': start_date,
            'end_date': b_end,
        })

    if not buckets_meta:
        return {'buckets': [], 'totals': {}}

    overall_start = buckets_meta[0]['start_date']
    overall_end = buckets_meta[-1]['end_date']

    # Months involved
    cur_m = overall_start.replace(day=1)
    end_m = overall_end.replace(day=1)
    all_months = []
    while cur_m <= end_m and cur_m <= current_month_first:
        all_months.append(cur_m)
        if cur_m.month == 12:
            cur_m = date(cur_m.year + 1, 1, 1)
        else:
            cur_m = date(cur_m.year, cur_m.month + 1, 1)

    existing_payrolls = list(
        Payroll.objects.filter(
            month__in=all_months
        ).values('employee_id', 'month', 'basic_salary', 'allowances', 'ot_amount')
    )
    existing_by_month = defaultdict(list)
    for p in existing_payrolls:
        existing_by_month[p['month']].append(p)

    month_payroll = {}
    month_ot_cost = {}
    month_is_est = {}
    month_no_data = {}

    for m in all_months:
        if m < fin_start_month:
            month_payroll[m] = Decimal('0.00')
            month_ot_cost[m] = Decimal('0.00')
            month_is_est[m] = False
            month_no_data[m] = True
        else:
            p_list = existing_by_month.get(m)
            if p_list:
                gross_sum = sum((p['basic_salary'] + p['allowances'] + p['ot_amount']) for p in p_list)
                ot_sum = sum((p['ot_amount'] or Decimal('0.00')) for p in p_list)
                month_payroll[m] = round(gross_sum, 2)
                month_ot_cost[m] = round(ot_sum, 2)
                month_is_est[m] = False
                month_no_data[m] = False
            else:
                month_no_data[m] = False

    missing_months = [
        m for m in all_months
        if m >= fin_start_month and not existing_by_month.get(m)
    ]

    if missing_months:
        min_est = min(missing_months).replace(day=1)
        max_days = calendar.monthrange(max(missing_months).year, max(missing_months).month)[1]
        max_est = max(missing_months).replace(day=max_days)

        active_employees = get_active_payroll_employees()
        est_register_rows = build_attendance_register(min_est, max_est)
        est_logs = list(
            EmployeeServiceLog.objects.counting().filter(
                date__range=(min_est, max_est)
            ).select_related('employee')
        )

        month_reg_by_emp = defaultdict(lambda: defaultdict(list))
        for r in est_register_rows:
            d = r['date']
            month_reg_by_emp[(d.year, d.month)][r['employee'].id].append(r)

        month_logs_by_emp = defaultdict(lambda: defaultdict(list))
        for l in est_logs:
            month_logs_by_emp[(l.date.year, l.date.month)][l.employee_id].append(l)

        for m in missing_months:
            m_first, m_last, m_days = get_month_range(m)
            m_reg = month_reg_by_emp.get((m.year, m.month), {})
            m_logs = month_logs_by_emp.get((m.year, m.month), {})
            est_val, est_ot = calculate_month_estimated_payroll(
                m_first, m_last, m_days, active_employees, m_reg, m_logs, return_breakdown=True
            )
            month_payroll[m] = est_val
            month_ot_cost[m] = est_ot
            month_is_est[m] = True

    daily_rates = {}
    daily_ot_rates = {}
    for m, p_cost in month_payroll.items():
        if month_no_data.get(m, False):
            daily_rates[m] = Decimal('0.00')
            daily_ot_rates[m] = Decimal('0.00')
        else:
            days_in_m = calendar.monthrange(m.year, m.month)[1]
            daily_rates[m] = p_cost / Decimal(days_in_m)
            daily_ot_rates[m] = month_ot_cost.get(m, Decimal('0.00')) / Decimal(days_in_m)

    invoices_qs = list(
        Invoice.objects.filter(
            invoice_date__gte=start_date,
            invoice_date__lte=effective_end,
            is_cancelled=False
        ).select_related('project').prefetch_related('job_costs', 'spare_sales')
    )
    inv_project_ids = {inv.project_id for inv in invoices_qs if inv.project_id}

    # Legacy projects without invoices (fallback for backward compatibility)
    legacy_projects_qs = list(
        Project.objects.filter(
            invoice_date__gte=start_date,
            invoice_date__lte=effective_end,
            invoice_date__isnull=False
        ).exclude(status='CANCELLED').exclude(id__in=inv_project_ids).values('id', 'invoice_date', 'invoice_amount', 'purchase_and_expenses')
    )

    expenses_qs = list(Expense.objects.filter(
        date__gte=start_date,
        date__lte=effective_end
    ).values('date', 'amount_aed', 'category__kind', 'category__name'))

    logs_qs = list(EmployeeServiceLog.objects.counting().filter(
        date__gte=start_date,
        date__lte=effective_end
    ).values('date', 'employee_id'))

    def find_bucket(d):
        for b in buckets_meta:
            if b['start_date'] <= d <= b['end_date']:
                return b['key']
        return None

    bucket_invoices = defaultdict(list)
    bucket_revenue = defaultdict(lambda: Decimal('0.00'))
    bucket_spares = defaultdict(lambda: Decimal('0.00'))
    bucket_job_costs = defaultdict(lambda: Decimal('0.00'))
    bucket_purchases = defaultdict(lambda: Decimal('0.00'))
    bucket_project_exp = defaultdict(lambda: Decimal('0.00'))
    bucket_overhead_exp = defaultdict(lambda: Decimal('0.00'))
    bucket_overhead_by_cat = defaultdict(lambda: defaultdict(lambda: Decimal('0.00')))
    bucket_engineer_days = defaultdict(set)

    for inv in invoices_qs:
        bk = find_bucket(inv.invoice_date)
        if bk:
            bucket_invoices[bk].append(inv)
            rev = inv.collection_value_aed
            bucket_revenue[bk] += rev

            sp_cost = sum((sp.purchase_cost_aed or Decimal('0.00')) for sp in inv.spare_sales.all())
            bucket_spares[bk] += sp_cost

            for jc in inv.job_costs.all():
                bucket_job_costs[bk] += (jc.amount_aed or Decimal('0.00'))

    # Spares with project set but invoice NULL, dated by the project's latest non-cancelled invoice_date
    if request is not None and hasattr(request, '_unlinked_spares_cache'):
        unlinked_spares, latest_inv_dates = request._unlinked_spares_cache
    else:
        unlinked_spares = list(
            SpareSale.objects.filter(project__isnull=False, invoice__isnull=True)
            .values('id', 'project_id', 'purchase_cost_aed')
        )
        if unlinked_spares:
            unlinked_pids = {s['project_id'] for s in unlinked_spares}
            latest_inv_dates = dict(
                Invoice.objects.filter(
                    project_id__in=unlinked_pids,
                    is_cancelled=False,
                    invoice_date__isnull=False
                ).values('project_id')
                .annotate(latest_date=Max('invoice_date'))
                .values_list('project_id', 'latest_date')
            )
        else:
            latest_inv_dates = {}
        if request is not None:
            request._unlinked_spares_cache = (unlinked_spares, latest_inv_dates)

    if unlinked_spares:
        for s in unlinked_spares:
            pid = s['project_id']
            latest_date = latest_inv_dates.get(pid)
            if latest_date:
                bk = find_bucket(latest_date)
                if bk:
                    bucket_spares[bk] += (s['purchase_cost_aed'] or Decimal('0.00'))
            # If the project has no invoice, exclude it from P&L (not added to any bucket)

    for p in legacy_projects_qs:
        bk = find_bucket(p['invoice_date'])
        if bk:
            bucket_revenue[bk] += p['invoice_amount'] or Decimal('0.00')
            bucket_purchases[bk] += p['purchase_and_expenses'] or Decimal('0.00')

    for e in expenses_qs:
        bk = find_bucket(e['date'])
        if bk:
            amt = e['amount_aed'] or Decimal('0.00')
            kind = e['category__kind']
            if kind == 'PROJECT':
                bucket_project_exp[bk] += amt
            elif kind == 'OVERHEAD':
                bucket_overhead_exp[bk] += amt
                cat_name = e['category__name'] or 'General Overhead'
                bucket_overhead_by_cat[bk][cat_name] += amt

    for l in logs_qs:
        bk = find_bucket(l['date'])
        if bk:
            bucket_engineer_days[bk].add((l['employee_id'], l['date']))

    if invoices_qs:
        all_inv_eng_map = compute_invoice_engineer_costs(invoices_qs)
    else:
        all_inv_eng_map = {}

    buckets = []
    tot_rev = Decimal('0.00')
    tot_pay = Decimal('0.00')
    tot_ot_pay = Decimal('0.00')
    tot_alloc_eng = Decimal('0.00')
    tot_spares = Decimal('0.00')
    tot_job_costs = Decimal('0.00')
    tot_purchases = Decimal('0.00')
    tot_proj_exp = Decimal('0.00')
    tot_proj_dir = Decimal('0.00')
    tot_over = Decimal('0.00')
    tot_distinct_eng_days = set()

    for b in buckets_meta:
        bk = b['key']
        b_start = b['start_date']
        b_end = b['end_date']

        if granularity == 'month':
            m = b['month_date']
            is_no_data = month_no_data.get(m, False)
            is_mtd = (m == current_month_first)
            if is_no_data:
                p_cost = Decimal('0.00')
                p_ot_cost = Decimal('0.00')
                is_est = False
            elif is_mtd:
                days_in_m = b['days_in_month']
                full_p = month_payroll.get(m, Decimal('0.00'))
                full_ot = month_ot_cost.get(m, Decimal('0.00'))
                p_cost = round(full_p * Decimal(today.day) / Decimal(days_in_m), 2)
                p_ot_cost = round(full_ot * Decimal(today.day) / Decimal(days_in_m), 2)
                is_est = month_is_est.get(m, False)
            else:
                p_cost = month_payroll.get(m, Decimal('0.00'))
                p_ot_cost = month_ot_cost.get(m, Decimal('0.00'))
                is_est = month_is_est.get(m, False)

        elif granularity == 'year':
            y = b['year']
            is_ytd = (y == today.year)
            is_mtd = False
            y_months = [m for m in all_months if m.year == y]
            is_no_data = all(month_no_data.get(m, False) for m in y_months) if y_months else True
            p_cost = Decimal('0.00')
            p_ot_cost = Decimal('0.00')
            is_est = False
            for m in y_months:
                if month_no_data.get(m, False):
                    continue
                full_p = month_payroll.get(m, Decimal('0.00'))
                full_ot = month_ot_cost.get(m, Decimal('0.00'))
                if m == current_month_first:
                    days_in_m = calendar.monthrange(m.year, m.month)[1]
                    p_cost += round(full_p * Decimal(today.day) / Decimal(days_in_m), 2)
                    p_ot_cost += round(full_ot * Decimal(today.day) / Decimal(days_in_m), 2)
                else:
                    p_cost += full_p
                    p_ot_cost += full_ot
                if month_is_est.get(m, False):
                    is_est = True

        elif granularity == 'week':
            week_days = (b_end - b_start).days + 1
            w_cost = Decimal('0.00')
            w_ot = Decimal('0.00')
            w_est = False
            is_mtd = False
            is_ytd = False
            week_months = set()
            for d_idx in range(week_days):
                day_dt = b_start + timedelta(days=d_idx)
                if not (start_date <= day_dt <= effective_end):
                    continue
                m_dt = day_dt.replace(day=1)
                week_months.add(m_dt)
                if month_no_data.get(m_dt, False):
                    continue
                w_cost += daily_rates.get(m_dt, Decimal('0.00'))
                w_ot += daily_ot_rates.get(m_dt, Decimal('0.00'))
                if month_is_est.get(m_dt, False):
                    w_est = True
            p_cost = round(w_cost, 2)
            p_ot_cost = round(w_ot, 2)
            is_est = w_est
            is_no_data = all(month_no_data.get(m_dt, False) for m_dt in week_months) if week_months else False

        elif granularity == 'range':
            range_days = (b_end - b_start).days + 1
            r_cost = Decimal('0.00')
            r_ot = Decimal('0.00')
            r_est = False
            is_mtd = False
            is_ytd = False
            range_months = set()
            for d_idx in range(range_days):
                day_dt = b_start + timedelta(days=d_idx)
                if not (start_date <= day_dt <= effective_end):
                    continue
                m_dt = day_dt.replace(day=1)
                range_months.add(m_dt)
                if month_no_data.get(m_dt, False):
                    continue
                r_cost += daily_rates.get(m_dt, Decimal('0.00'))
                r_ot += daily_ot_rates.get(m_dt, Decimal('0.00'))
                if month_is_est.get(m_dt, False):
                    r_est = True
            p_cost = round(r_cost, 2)
            p_ot_cost = round(r_ot, 2)
            is_est = r_est
            is_no_data = all(month_no_data.get(m_dt, False) for m_dt in range_months) if range_months else False
        else:
            is_ytd = False
            p_cost = Decimal('0.00')
            p_ot_cost = Decimal('0.00')

        rev = bucket_revenue[bk]
        purchases = bucket_purchases[bk]
        spares_c = bucket_spares[bk]
        job_c = bucket_job_costs[bk]
        p_exp = bucket_project_exp[bk]
        proj_dir = spares_c + job_c + purchases + p_exp
        over = bucket_overhead_exp[bk]
        t_cost = p_cost + proj_dir + over
        profit = rev - t_cost
        margin_pct = round((profit / rev) * Decimal('100'), 1) if rev > Decimal('0.00') else None

        e_days_count = len(bucket_engineer_days[bk])
        tot_distinct_eng_days.update(bucket_engineer_days[bk])
        rev_per_e_day = round(rev / Decimal(e_days_count), 2) if e_days_count > 0 else None

        overhead_cats = [
            {'name': cat, 'amount': amt}
            for cat, amt in sorted(bucket_overhead_by_cat[bk].items(), key=lambda x: -x[1])
        ]

        b_invs = bucket_invoices.get(bk, [])
        if b_invs:
            b_alloc_eng_cost = sum((all_inv_eng_map.get(inv.id, Decimal('0.00')) for inv in b_invs), Decimal('0.00'))
        else:
            b_alloc_eng_cost = Decimal('0.00')
        b_unalloc_payroll = p_cost - b_alloc_eng_cost

        job_direct_c = job_c + purchases + p_exp
        gross_profit = rev - spares_c - job_direct_c
        gross_margin_pct = round((gross_profit / rev) * Decimal('100'), 1) if rev > Decimal('0.00') else None

        bucket_item = {
            'key': bk,
            'label': b['label'],
            'start_date': b_start,
            'end_date': b_end,
            'revenue': rev,
            'payroll_cost': p_cost,
            'payroll_ot_cost': p_ot_cost,
            'allocated_engineer_cost': b_alloc_eng_cost,
            'unallocated_payroll': b_unalloc_payroll,
            'spares_cost': spares_c,
            'job_costs': job_c,
            'project_purchases': purchases,
            'project_expenses': p_exp,
            'job_direct_cost': job_direct_c,
            'project_direct_cost': proj_dir,
            'gross_profit': gross_profit,
            'gross_margin_pct': gross_margin_pct,
            'overhead_cost': over,
            'total_cost': t_cost,
            'profit': profit,
            'margin_pct': margin_pct,
            'engineer_days': e_days_count,
            'revenue_per_engineer_day': rev_per_e_day,
            'estimated': is_est,
            'no_data': is_no_data,
            'mtd': is_mtd,
            'ytd': is_ytd if granularity == 'year' else False,
            'details': {
                'payroll_cost': p_cost,
                'payroll_ot_cost': p_ot_cost,
                'allocated_engineer_cost': b_alloc_eng_cost,
                'unallocated_payroll': b_unalloc_payroll,
                'spares_cost': spares_c,
                'job_costs': job_c,
                'project_purchases': purchases,
                'project_expenses': p_exp,
                'job_direct_cost': job_direct_c,
                'project_direct_cost': proj_dir,
                'gross_profit': gross_profit,
                'gross_margin_pct': gross_margin_pct,
                'overhead_cost': over,
                'overhead_by_category': overhead_cats,
                'reconciliation': {
                    'job_revenue': rev,
                    'job_direct_cost': spares_c + job_c + purchases,
                    'job_engineer_cost': b_alloc_eng_cost,
                    'job_gross_profit': rev - (spares_c + job_c + purchases + b_alloc_eng_cost),
                    'unallocated_payroll': b_unalloc_payroll,
                    'project_expenses': p_exp,
                    'overhead_cost': over,
                    'company_net_profit': profit,
                }
            }
        }
        buckets.append(bucket_item)

        tot_rev += rev
        tot_pay += p_cost
        tot_ot_pay += p_ot_cost
        tot_alloc_eng += b_alloc_eng_cost
        tot_spares += spares_c
        tot_job_costs += job_c
        tot_purchases += purchases
        tot_proj_exp += p_exp
        tot_proj_dir += proj_dir
        tot_over += over

    tot_cost = tot_pay + tot_proj_dir + tot_over
    tot_profit = tot_rev - tot_cost
    tot_margin = round((tot_profit / tot_rev) * Decimal('100'), 1) if tot_rev > Decimal('0.00') else None
    tot_gross_profit = tot_rev - tot_proj_dir
    tot_gross_margin = round((tot_gross_profit / tot_rev) * Decimal('100'), 1) if tot_rev > Decimal('0.00') else None
    tot_eng_days_count = len(tot_distinct_eng_days)
    tot_rev_per_eng_day = round(tot_rev / Decimal(tot_eng_days_count), 2) if tot_eng_days_count > 0 else None

    totals = {
        'revenue': tot_rev,
        'payroll_cost': tot_pay,
        'payroll_ot_cost': tot_ot_pay,
        'allocated_engineer_cost': tot_alloc_eng,
        'unallocated_payroll': tot_pay - tot_alloc_eng,
        'spares_cost': tot_spares,
        'job_costs': tot_job_costs,
        'project_purchases': tot_purchases,
        'project_expenses': tot_proj_exp,
        'job_direct_cost': tot_job_costs + tot_purchases + tot_proj_exp,
        'project_direct_cost': tot_proj_dir,
        'gross_profit': tot_gross_profit,
        'gross_margin_pct': tot_gross_margin,
        'overhead_cost': tot_over,
        'total_cost': tot_cost,
        'profit': tot_profit,
        'margin_pct': tot_margin,
        'engineer_days': tot_eng_days_count,
        'revenue_per_engineer_day': tot_rev_per_eng_day,
        'reconciliation': {
            'job_revenue': tot_rev,
            'job_direct_cost': tot_spares + tot_job_costs + tot_purchases,
            'job_engineer_cost': tot_alloc_eng,
            'job_gross_profit': tot_rev - (tot_spares + tot_job_costs + tot_purchases + tot_alloc_eng),
            'unallocated_payroll': tot_pay - tot_alloc_eng,
            'project_expenses': tot_proj_exp,
            'overhead_cost': tot_over,
            'company_net_profit': tot_profit,
        }
    }

    return {'buckets': buckets, 'totals': totals}


def pnl_excel_response(buckets, totals, filename_suffix='all'):
    """
    Exports P&L statement to formatted Excel file using openpyxl.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "P&L Statement"

    fill_header = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_data = Font(name="Calibri", size=11)
    font_total = Font(name="Calibri", size=11, bold=True)
    fill_total = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")

    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9'),
    )
    border_total = Border(
        left=Side(style='thin', color='000000'),
        right=Side(style='thin', color='000000'),
        top=Side(style='thin', color='000000'),
        bottom=Side(style='double', color='000000'),
    )

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    headers = [
        "Month", "Revenue (AED)", "Spares Cost (AED)", "Job Direct Costs (AED)",
        "Gross Profit (AED)", "Gross Margin %", "Payroll (AED)", "Overheads (AED)",
        "Net Profit/Loss (AED)", "Net Margin %", "Engineer Days"
    ]

    ws.row_dimensions[1].height = 26
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill = fill_header
        cell.font = font_header
        cell.alignment = align_center

    current_row = 2
    for b in buckets:
        ws.row_dimensions[current_row].height = 20
        if b.get('ytd'):
            label_text = f"{b['label']} (YTD)"
        elif b.get('mtd'):
            label_text = f"{b['label']} (MTD)"
        else:
            label_text = b['label']

        gross_p = float(b.get('gross_profit', b['revenue'] - b['spares_cost'] - b.get('job_direct_cost', 0)))
        gross_m = (float(b['gross_margin_pct']) / 100.0) if b.get('gross_margin_pct') is not None else None
        net_m = (float(b['margin_pct']) / 100.0) if b.get('margin_pct') is not None else None

        row_values = [
            (label_text, align_left, "text"),
            (float(b['revenue']), align_right, "0.00"),
            (float(b['spares_cost']), align_right, "0.00"),
            (float(b.get('job_direct_cost', 0)), align_right, "0.00"),
            (gross_p, align_right, "0.00"),
            (gross_m, align_right, "pct"),
            (float(b['payroll_cost']), align_right, "0.00"),
            (float(b['overhead_cost']), align_right, "0.00"),
            (float(b['profit']), align_right, "0.00"),
            (net_m, align_right, "pct"),
            (b['engineer_days'], align_center, "int"),
        ]

        for col_idx, (val, alignment, kind) in enumerate(row_values, start=1):
            cell = ws.cell(row=current_row, column=col_idx, value=val if val is not None else None)
            cell.alignment = alignment
            cell.font = font_data
            cell.border = thin_border
            if val is not None:
                if kind == "0.00" and isinstance(val, (int, float)):
                    cell.number_format = "#,##0.00"
                elif kind == "pct" and isinstance(val, (int, float)):
                    cell.number_format = '0.0%'
                elif kind == "int":
                    cell.number_format = '#,##0'

        current_row += 1

    # Totals Row
    ws.row_dimensions[current_row].height = 24
    tot_gross_p = float(totals.get('gross_profit', totals.get('revenue', 0) - totals.get('spares_cost', 0) - totals.get('job_direct_cost', 0)))
    tot_gross_m = (float(totals['gross_margin_pct']) / 100.0) if totals.get('gross_margin_pct') is not None else None
    tot_net_m = (float(totals['margin_pct']) / 100.0) if totals.get('margin_pct') is not None else None

    total_values = [
        ("TOTAL (AED)", align_left, "text"),
        (float(totals.get('revenue', 0)), align_right, "0.00"),
        (float(totals.get('spares_cost', 0)), align_right, "0.00"),
        (float(totals.get('job_direct_cost', 0)), align_right, "0.00"),
        (tot_gross_p, align_right, "0.00"),
        (tot_gross_m, align_right, "pct"),
        (float(totals.get('payroll_cost', 0)), align_right, "0.00"),
        (float(totals.get('overhead_cost', 0)), align_right, "0.00"),
        (float(totals.get('profit', 0)), align_right, "0.00"),
        (tot_net_m, align_right, "pct"),
        (totals.get('engineer_days', 0), align_center, "int"),
    ]

    for col_idx, (val, alignment, kind) in enumerate(total_values, start=1):
        cell = ws.cell(row=current_row, column=col_idx, value=val if val is not None else None)
        cell.alignment = alignment
        cell.font = font_total
        cell.fill = fill_total
        cell.border = border_total
        if val is not None:
            if kind == "0.00" and isinstance(val, (int, float)):
                cell.number_format = "#,##0.00"
            elif kind == "pct" and isinstance(val, (int, float)):
                cell.number_format = '0.0%'
            elif kind == "int":
                cell.number_format = '#,##0'

    ws.freeze_panes = "B2"

    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            val_str = str(cell.value or '')
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 13)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="pnl_statement_{filename_suffix}.xlsx"'
    return response


def get_projects_profitability(filters=None, sort_by=None):
    """
    Returns list of project profitability dicts and summary totals.
    Columns: Project, Client, Vessel, Category, Invoice No, Invoice Date,
             Revenue, Labour Cost, Materials/Purchases, Other Expenses, Total Cost,
             Profit, Margin %, Engineer Days, Revenue / Engineer Day, Payment Status.
    Rebuilt on the Invoice model and get_projects_financials_map.
    """
    filters = filters or {}
    qs = Project.objects.all().select_related('manager', 'manager__user')

    if filters.get('start_date') or filters.get('end_date'):
        inv_q = Q(invoices__is_cancelled=False)
        if filters.get('start_date'):
            inv_q &= Q(invoices__invoice_date__gte=filters['start_date'])
        if filters.get('end_date'):
            inv_q &= Q(invoices__invoice_date__lte=filters['end_date'])

        legacy_q = Q(invoice_amount__gt=0)
        if filters.get('start_date'):
            legacy_q &= Q(invoice_date__gte=filters['start_date'])
        if filters.get('end_date'):
            legacy_q &= Q(invoice_date__lte=filters['end_date'])

        qs = qs.filter(inv_q | legacy_q).distinct()

    if filters.get('client'):
        qs = qs.filter(client_name__icontains=filters['client'])
    if filters.get('category'):
        qs = qs.filter(category=filters['category'])
    if filters.get('status') and filters['status'] != 'all':
        qs = qs.filter(status=filters['status'])
    if filters.get('search'):
        s = filters['search']
        qs = qs.filter(
            Q(name__icontains=s) | Q(client_name__icontains=s) | Q(vessel_name__icontains=s) | Q(code__icontains=s)
        )

    projects = list(qs)
    if not projects:
        return [], {
            'revenue': Decimal('0.00'),
            'labour_cost': Decimal('0.00'),
            'materials': Decimal('0.00'),
            'other_expenses': Decimal('0.00'),
            'total_cost': Decimal('0.00'),
            'profit': Decimal('0.00'),
            'margin_pct': None,
            'engineer_days': 0,
        }

    project_ids = [p.id for p in projects]

    # Bulk-load non-cancelled invoices per project in ONE query
    db_invoices = list(
        Invoice.objects.filter(project_id__in=project_ids, is_cancelled=False).order_by('invoice_date', 'id')
    )
    invs_by_proj = defaultdict(list)
    for inv in db_invoices:
        invs_by_proj[inv.project_id].append(inv)

    # Use get_projects_financials_map(projects) for all financial metrics
    financials_map = get_projects_financials_map(projects)

    # Engineer days tracking
    logs_data = list(
        EmployeeServiceLog.objects.counting().filter(
            project_id__in=project_ids
        ).values('project_id', 'employee_id', 'date')
    )
    eng_days_by_proj = defaultdict(set)
    for l in logs_data:
        eng_days_by_proj[l['project_id']].add((l['employee_id'], l['date']))

    results = []
    tot_rev = Decimal('0.00')
    tot_lab = Decimal('0.00')
    tot_mat = Decimal('0.00')
    tot_oth = Decimal('0.00')
    all_eng_days = set()

    for p in projects:
        p_invs = invs_by_proj.get(p.id, [])
        fin = financials_map.get(p.id, {})

        is_invoiced = len(p_invs) > 0
        is_legacy = bool(not p_invs and (p.invoice_amount or Decimal('0.00')) > Decimal('0.00'))

        # Date range filter at row level
        if filters.get('start_date') or filters.get('end_date'):
            if is_invoiced:
                has_inv_in_range = any(
                    inv.invoice_date and
                    (not filters.get('start_date') or inv.invoice_date >= filters['start_date']) and
                    (not filters.get('end_date') or inv.invoice_date <= filters['end_date'])
                    for inv in p_invs
                )
                if not has_inv_in_range:
                    continue
            elif is_legacy:
                has_legacy_in_range = bool(
                    p.invoice_date and
                    (not filters.get('start_date') or p.invoice_date >= filters['start_date']) and
                    (not filters.get('end_date') or p.invoice_date <= filters['end_date'])
                )
                if not has_legacy_in_range:
                    continue
            else:
                continue

        # Invoiced status filter: 'invoiced', 'not_invoiced'
        inv_filter = filters.get('invoiced_status')
        if inv_filter == 'invoiced' and not is_invoiced:
            continue
        elif inv_filter == 'not_invoiced' and is_invoiced:
            continue

        # Invoice number & date
        if is_invoiced:
            first_no = p_invs[0].invoice_number
            invoice_no = f"{first_no} +{len(p_invs) - 1} more" if len(p_invs) > 1 else first_no
            valid_dates = [inv.invoice_date for inv in p_invs if inv.invoice_date]
            invoice_date = max(valid_dates) if valid_dates else None
        elif is_legacy:
            invoice_no = p.invoice_number or '—'
            invoice_date = p.invoice_date
        else:
            invoice_no = '—'
            invoice_date = None

        if filters.get('missing_invoice_date') and not ((is_invoiced or is_legacy) and not invoice_date):
            continue

        # Status: all PAID -> Paid; any OVERDUE -> Overdue; any PARTIAL or mix of paid/open -> Partial; else Open. No invoice -> "Not invoiced".
        if is_invoiced:
            inv_statuses = [inv.status for inv in p_invs]
            if all(s == 'PAID' for s in inv_statuses):
                computed_status = 'Paid'
            elif any(s == 'OVERDUE' for s in inv_statuses):
                computed_status = 'Overdue'
            elif any(s == 'PARTIAL' for s in inv_statuses) or ('PAID' in inv_statuses and any(s != 'PAID' for s in inv_statuses)):
                computed_status = 'Partial'
            else:
                computed_status = 'Open'
        elif is_legacy:
            legacy_map = {'PAID': 'Paid', 'PARTIAL': 'Partial', 'OVERDUE': 'Overdue', 'UNPAID': 'Open'}
            computed_status = legacy_map.get(getattr(p, 'payment_status', 'UNPAID'), 'Open')
        else:
            computed_status = 'Not invoiced'

        # Payment status filter
        pay_filter = filters.get('payment_status')
        if pay_filter and pay_filter != 'all':
            if computed_status != pay_filter:
                continue

        # Received & Outstanding
        if is_invoiced:
            received = sum((inv.amount_received_aed or Decimal('0.00') for inv in p_invs), Decimal('0.00'))
            outstanding = sum((inv.outstanding_aed for inv in p_invs), Decimal('0.00'))
        elif is_legacy:
            received = p.amount_received or Decimal('0.00')
            outstanding = max((p.invoice_amount or Decimal('0.00')) - received, Decimal('0.00'))
        else:
            received = Decimal('0.00')
            outstanding = Decimal('0.00')

        # Financials from get_projects_financials_map
        rev = fin.get('invoiced_amount', Decimal('0.00'))
        lab = fin.get('employee_cost', Decimal('0.00'))
        mat = fin.get('spares_cost', Decimal('0.00'))
        oth = fin.get('other_job_costs', Decimal('0.00'))
        t_cost = fin.get('total_cost', Decimal('0.00'))
        profit = fin.get('profit', Decimal('0.00'))

        if (is_invoiced or is_legacy) and rev > Decimal('0.00'):
            margin_pct = round((profit / rev) * Decimal('100'), 1)
        else:
            margin_pct = None

        is_loss = bool((is_invoiced or is_legacy) and profit < Decimal('0.00'))
        if filters.get('loss_only') and not is_loss:
            continue

        e_days = len(eng_days_by_proj[p.id])
        if filters.get('no_service_logs') and e_days > 0:
            continue

        all_eng_days.update(eng_days_by_proj[p.id])
        rev_per_day = round(rev / Decimal(e_days), 2) if (e_days > 0 and rev > Decimal('0.00')) else None

        row = {
            'project': p,
            'id': p.id,
            'name': p.name,
            'code': p.code,
            'client_name': p.client_name,
            'vessel_name': p.vessel_name,
            'category': p.category,
            'invoice_no': invoice_no,
            'invoice_date': invoice_date,
            'revenue': rev,
            'labour_cost': lab,
            'materials': mat,
            'other_expenses': oth,
            'total_cost': t_cost,
            'profit': profit,
            'margin_pct': margin_pct,
            'engineer_days': e_days,
            'revenue_per_engineer_day': rev_per_day,
            'payment_status': computed_status,
            'status': computed_status,
            'invoiced': is_invoiced,
            'invoice_count': len(p_invs),
            'is_legacy': is_legacy,
            'is_loss': is_loss,
            'received': received,
            'outstanding': outstanding,
        }

        results.append(row)
        tot_rev += rev
        tot_lab += lab
        tot_mat += mat
        tot_oth += oth

    tot_cost = tot_lab + tot_mat + tot_oth
    tot_profit = tot_rev - tot_cost

    invoiced_rev = sum(r['revenue'] for r in results if r['invoiced'] or r['is_legacy'])
    invoiced_profit = sum(r['profit'] for r in results if r['invoiced'] or r['is_legacy'])
    tot_margin = round((invoiced_profit / invoiced_rev) * Decimal('100'), 1) if invoiced_rev > Decimal('0.00') else None

    # Sorting
    if sort_by:
        reverse = sort_by.startswith('-')
        field = sort_by.lstrip('-')
        if field in ('revenue', 'labour_cost', 'materials', 'other_expenses', 'total_cost', 'profit', 'engineer_days', 'received', 'outstanding'):
            results.sort(key=lambda x: x.get(field, Decimal('0.00')), reverse=reverse)
        elif field == 'margin_pct':
            results.sort(key=lambda x: (x.get('margin_pct') is not None, x.get('margin_pct') or Decimal('-999999')), reverse=reverse)
        elif field in ('invoice_date', 'date'):
            results.sort(key=lambda x: (x.get('invoice_date') is not None, x.get('invoice_date') or date.min), reverse=reverse)
        elif field == 'name':
            results.sort(key=lambda x: (x.get('name') or '').lower(), reverse=reverse)
        elif field == 'client_name':
            results.sort(key=lambda x: (x.get('client_name') or '').lower(), reverse=reverse)
        elif field in ('status', 'payment_status'):
            results.sort(key=lambda x: (x.get('status') or '').lower(), reverse=reverse)
    else:
        results.sort(key=lambda x: (x.get('invoice_date') is not None, x.get('invoice_date') or date.min), reverse=True)

    totals = {
        'revenue': tot_rev,
        'labour_cost': tot_lab,
        'materials': tot_mat,
        'other_expenses': tot_oth,
        'total_cost': tot_cost,
        'profit': tot_profit,
        'margin_pct': tot_margin,
        'engineer_days': len(all_eng_days),
    }

    return results, totals

    totals = {
        'revenue': tot_rev,
        'labour_cost': tot_lab,
        'materials': tot_mat,
        'other_expenses': tot_oth,
        'total_cost': tot_cost,
        'profit': tot_profit,
        'margin_pct': tot_margin,
        'engineer_days': len(all_eng_days),
    }

    return results, totals


def projects_profitability_excel_response(project_rows, totals, filename_suffix='all'):
    """
    Exports project profitability report to formatted Excel file using openpyxl.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Project Profitability"

    fill_header = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_data = Font(name="Calibri", size=11)
    font_total = Font(name="Calibri", size=11, bold=True)
    fill_total = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    fill_loss = PatternFill(start_color="FFD9D9", end_color="FFD9D9", fill_type="solid")

    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9'),
    )
    border_total = Border(
        left=Side(style='thin', color='000000'),
        right=Side(style='thin', color='000000'),
        top=Side(style='thin', color='000000'),
        bottom=Side(style='double', color='000000'),
    )

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    headers = [
        "Project", "Client", "Vessel", "Category", "Invoice No", "Invoice Date",
        "Revenue (AED)", "Labour Cost (AED)", "Materials (AED)", "Other Expenses (AED)",
        "Total Cost (AED)", "Profit / Loss (AED)", "Margin %", "Engineer Days",
        "Rev / Eng Day (AED)", "Payment Status"
    ]

    ws.row_dimensions[1].height = 26
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill = fill_header
        cell.font = font_header
        cell.alignment = align_center

    current_row = 2
    for r in project_rows:
        ws.row_dimensions[current_row].height = 20
        is_loss = r.get('is_loss', False)
        row_fill = fill_loss if is_loss else None

        row_values = [
            (f"{r['name']} ({r['code']})" if r.get('code') else r['name'], align_left, "text"),
            (r.get('client_name') or "—", align_left, "text"),
            (r.get('vessel_name') or "—", align_left, "text"),
            (r.get('category') or "—", align_center, "text"),
            (r.get('invoice_no') or "—", align_center, "text"),
            (r['invoice_date'].strftime('%Y-%m-%d') if r.get('invoice_date') else "—", align_center, "text"),
            (float(r['revenue']), align_right, "0.00"),
            (float(r['labour_cost']), align_right, "0.00"),
            (float(r['materials']), align_right, "0.00"),
            (float(r['other_expenses']), align_right, "0.00"),
            (float(r['total_cost']), align_right, "0.00"),
            (float(r['profit']), align_right, "0.00"),
            (float(r['margin_pct']) if r['margin_pct'] is not None else "—", align_right, "pct"),
            (r['engineer_days'], align_center, "int"),
            (float(r['revenue_per_engineer_day']) if r['revenue_per_engineer_day'] is not None else "—", align_right, "0.00"),
            (r.get('payment_status') or "UNPAID", align_center, "text"),
        ]

        for col_idx, (val, alignment, kind) in enumerate(row_values, start=1):
            cell = ws.cell(row=current_row, column=col_idx, value=val)
            cell.alignment = alignment
            cell.font = font_data
            cell.border = thin_border
            if row_fill:
                cell.fill = row_fill
            if kind == "0.00" and isinstance(val, (int, float)):
                cell.number_format = "#,##0.00"
            elif kind == "pct" and isinstance(val, (int, float)):
                cell.number_format = '0.0"%"'
            elif kind == "int":
                cell.number_format = '#,##0'

        current_row += 1

    # Totals Row
    ws.row_dimensions[current_row].height = 24
    total_values = [
        ("TOTAL (AED)", align_left, "text"),
        ("", align_left, "text"),
        ("", align_left, "text"),
        ("", align_center, "text"),
        ("", align_center, "text"),
        ("", align_center, "text"),
        (float(totals.get('revenue', 0)), align_right, "0.00"),
        (float(totals.get('labour_cost', 0)), align_right, "0.00"),
        (float(totals.get('materials', 0)), align_right, "0.00"),
        (float(totals.get('other_expenses', 0)), align_right, "0.00"),
        (float(totals.get('total_cost', 0)), align_right, "0.00"),
        (float(totals.get('profit', 0)), align_right, "0.00"),
        (float(totals.get('margin_pct')) if totals.get('margin_pct') is not None else "—", align_right, "pct"),
        (totals.get('engineer_days', 0), align_center, "int"),
        ("", align_right, "text"),
        ("", align_center, "text"),
    ]

    for col_idx, (val, alignment, kind) in enumerate(total_values, start=1):
        cell = ws.cell(row=current_row, column=col_idx, value=val)
        cell.alignment = alignment
        cell.font = font_total
        cell.fill = fill_total
        cell.border = border_total
        if kind == "0.00" and isinstance(val, (int, float)):
            cell.number_format = "#,##0.00"
        elif kind == "pct" and isinstance(val, (int, float)):
            cell.number_format = '0.0"%"'
        elif kind == "int":
            cell.number_format = '#,##0'

    ws.freeze_panes = "B2"

    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            val_str = str(cell.value or '')
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 13)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="project_profitability_{filename_suffix}.xlsx"'
    return response


# =====================================================================
# PHASE 3: ATTRIBUTED REVENUE & EMPLOYEE COST
# =====================================================================

def get_attributed_revenue_by_employee(start_date, end_date):
    """
    Attributed revenue uses INVOICED projects only (invoice_date in range),
    split by logged hours.
    Remainder given to the employee with the largest share so sum equals
    project revenue exactly.
    Returns:
        dict: {employee_id: Decimal('share_sum')}
        dict: {project_id: {employee_id: Decimal('share')}}
    """
    # Projects with non-cancelled invoices in date range
    inv_projects = list(Project.objects.filter(
        invoices__is_cancelled=False,
        invoices__invoice_date__gte=start_date,
        invoices__invoice_date__lte=end_date,
    ).distinct())
    # Legacy projects with invoice_amount in range without invoices
    legacy_projects = list(Project.objects.filter(
        invoice_date__gte=start_date,
        invoice_date__lte=end_date,
        invoice_amount__gt=0
    ).exclude(id__in=[p.id for p in inv_projects]))
    invoiced_projects = inv_projects + legacy_projects

    if not invoiced_projects:
        return {}, {}

    project_ids = [p.id for p in invoiced_projects]
    logs = list(
        EmployeeServiceLog.objects.counting().filter(
            project_id__in=project_ids
        ).values('project_id', 'employee_id', 'total_hours')
    )

    proj_emp_hours = defaultdict(lambda: defaultdict(Decimal))
    for l in logs:
        proj_emp_hours[l['project_id']][l['employee_id']] += (l['total_hours'] or Decimal('0.00'))

    emp_attributed = defaultdict(lambda: Decimal('0.00'))
    project_shares = {}

    for p in invoiced_projects:
        p_invs_in_range = list(p.invoices.filter(
            is_cancelled=False,
            invoice_date__gte=start_date,
            invoice_date__lte=end_date
        ))
        if p_invs_in_range:
            rev = sum((inv.collection_value_aed for inv in p_invs_in_range), Decimal('0.00'))
        else:
            rev = p.invoice_amount or Decimal('0.00')
        emp_hours = proj_emp_hours.get(p.id, {})
        active_hours = {eid: h for eid, h in emp_hours.items() if h > Decimal('0.00')}
        total_hours = sum(active_hours.values())

        if total_hours <= Decimal('0.00'):
            project_shares[p.id] = {}
            continue

        shares = {}
        for eid, h in active_hours.items():
            share = round(rev * (h / total_hours), 2)
            shares[eid] = share

        diff = rev - sum(shares.values())
        if diff != Decimal('0.00'):
            largest_emp = max(active_hours.keys(), key=lambda eid: (shares[eid], active_hours[eid], eid))
            shares[largest_emp] += diff

        project_shares[p.id] = shares
        for eid, s in shares.items():
            emp_attributed[eid] += s

    return emp_attributed, project_shares


def get_employee_cost_data(start_date, end_date, request=None, sort_by=None):
    """
    Computes employee cost, billable utilisation, attributed revenue, and contribution.
    Returns:
        tuple (list of employee rows, summary totals dict)
    """
    today = timezone.localdate()
    current_month_first = today.replace(day=1)
    fin_start_month = get_finance_start_month(request)

    if start_date > end_date:
        return [], {}

    effective_end = min(end_date, today)

    # Determine months covered
    cur_m = start_date.replace(day=1)
    end_m = effective_end.replace(day=1)
    months = []
    while cur_m <= end_m and cur_m <= current_month_first:
        months.append(cur_m)
        if cur_m.month == 12:
            cur_m = date(cur_m.year + 1, 1, 1)
        else:
            cur_m = date(cur_m.year, cur_m.month + 1, 1)

    # Working days in period (non-Sundays)
    range_days = (effective_end - start_date).days + 1
    working_days = sum(1 for d in range(range_days) if (start_date + timedelta(days=d)).weekday() != 6)
    capacity_hours = working_days * 8

    # Query active employees + employees with service logs or payroll in period
    active_employees = get_active_payroll_employees()
    active_emp_ids = {e.id for e in active_employees}
    extra_emp_ids = set(
        Payroll.objects.filter(month__in=months).values_list('employee_id', flat=True)
    ) | set(
        EmployeeServiceLog.objects.counting().filter(date__range=(start_date, effective_end)).values_list('employee_id', flat=True)
    )
    extra_ids_to_fetch = extra_emp_ids - active_emp_ids
    extra_employees = list(Employee.objects.filter(id__in=extra_ids_to_fetch).select_related('user'))
    employees = sorted(active_employees + extra_employees, key=lambda e: (e.user.first_name, e.user.last_name, e.user.username))

    emp_ids = [e.id for e in employees]

    # Existing payroll rows
    existing_payrolls = list(
        Payroll.objects.filter(
            month__in=months,
            employee_id__in=emp_ids
        ).values('employee_id', 'month', 'basic_salary', 'allowances', 'ot_amount', 'status')
    )
    payroll_map = {(p['employee_id'], p['month']): p for p in existing_payrolls}
    months_with_payroll = set(p['month'] for p in existing_payrolls)

    # Missing months for estimation: only months with NO payroll rows at all (same as P&L)
    missing_months = [m for m in months if m >= fin_start_month and m not in months_with_payroll]
    month_reg_by_emp = defaultdict(lambda: defaultdict(list))
    month_logs_by_emp = defaultdict(lambda: defaultdict(list))

    if missing_months:
        min_est = min(missing_months).replace(day=1)
        max_days = calendar.monthrange(max(missing_months).year, max(missing_months).month)[1]
        max_est = max(missing_months).replace(day=max_days)

        est_register_rows = build_attendance_register(min_est, max_est)
        est_logs = list(
            EmployeeServiceLog.objects.counting().filter(
                date__range=(min_est, max_est),
                employee_id__in=emp_ids
            ).select_related('employee')
        )

        for r in est_register_rows:
            d = r['date']
            month_reg_by_emp[(d.year, d.month)][r['employee'].id].append(r)

        for l in est_logs:
            month_logs_by_emp[(l.date.year, l.date.month)][l.employee_id].append(l)

    # Vessel hours in period
    vessel_logs = list(
        EmployeeServiceLog.objects.counting().filter(
            employee_id__in=emp_ids,
            date__range=(start_date, effective_end)
        ).values('employee_id').annotate(total_hours=Sum('total_hours'), total_ot_cost=Sum('ot_cost'))
    )
    vessel_hours_map = {item['employee_id']: (item['total_hours'] or Decimal('0.00')) for item in vessel_logs}
    vessel_ot_cost_map = {item['employee_id']: (item['total_ot_cost'] or Decimal('0.00')) for item in vessel_logs}

    # Office hours in period from Attendance (excluding REJECTED)
    att_logs = list(
        Attendance.objects.filter(
            employee_id__in=emp_ids,
            login_time__date__gte=start_date,
            login_time__date__lte=effective_end
        ).exclude(status='REJECTED').values('employee_id').annotate(total_hours=Sum('total_hours_of_work'))
    )
    total_att_hours_map = {item['employee_id']: (item['total_hours'] or Decimal('0.00')) for item in att_logs}

    # Attributed revenue
    emp_attributed, _ = get_attributed_revenue_by_employee(start_date, end_date)

    rows = []
    tot_salary = Decimal('0.00')
    tot_payroll = Decimal('0.00')
    tot_ot = Decimal('0.00')
    tot_vessel_h = Decimal('0.00')
    tot_office_h = Decimal('0.00')
    tot_attributed = Decimal('0.00')

    for emp in employees:
        monthly_sal = emp.salary or Decimal('0.00')
        tot_salary += monthly_sal

        # Calculate payroll cost & OT across months
        emp_p_cost = Decimal('0.00')
        emp_ot_cost = Decimal('0.00')
        is_estimated = False

        for m in months:
            if m < fin_start_month:
                continue

            if m in months_with_payroll:
                # If month has ANY Payroll rows, use only those rows (no per-employee estimate)
                p_entry = payroll_map.get((emp.id, m))
                if p_entry:
                    gross = p_entry['basic_salary'] + p_entry['allowances'] + p_entry['ot_amount']
                    emp_p_cost += gross
                    emp_ot_cost += p_entry['ot_amount']
            else:
                # Month has NO payroll rows: estimate using active payroll employees
                if emp.id not in active_emp_ids:
                    continue
                m_first, m_last, m_days = get_month_range(m)
                if emp.date_of_join and emp.date_of_join > m_last:
                    continue

                basic = calculate_employee_prorated_salary(emp, m_first, m_last, m_days)
                emp_rows = month_reg_by_emp.get((m.year, m.month), {}).get(emp.id, [])
                emp_m_logs = month_logs_by_emp.get((m.year, m.month), {}).get(emp.id, [])
                vessel_ot = sum((l.ot_cost or Decimal('0.00')) for l in emp_m_logs)
                office_ot = calculate_office_ot_cost(emp.salary, emp_rows, emp_m_logs, employee=emp)
                month_ot = vessel_ot + office_ot
                month_gross = basic + month_ot

                if m == current_month_first and effective_end < m_last:
                    factor = Decimal(today.day) / Decimal(m_days)
                    emp_p_cost += round(month_gross * factor, 2)
                    emp_ot_cost += round(month_ot * factor, 2)
                else:
                    emp_p_cost += month_gross
                    emp_ot_cost += month_ot

                is_estimated = True

        vessel_h = vessel_hours_map.get(emp.id, Decimal('0.00'))
        att_h = total_att_hours_map.get(emp.id, Decimal('0.00'))

        # Office hours: if office staff, all attendance is office; if engineer, att - vessel (min 0)
        is_office_role = (
            getattr(emp, 'work_type', '') == 'office'
            or getattr(emp, 'is_administration', False)
            or getattr(emp, 'is_manager', False)
            or getattr(emp, 'is_accountant', False)
            or getattr(emp, 'is_hr', False)
            or (hasattr(emp, 'get_role') and emp.get_role() in ('Manager', 'Administration', 'HR', 'Accountant'))
        )
        if is_office_role:
            office_h = att_h
        else:
            office_h = max(att_h - vessel_h, Decimal('0.00'))

        utilisation = (
            round((vessel_h / Decimal(capacity_hours)) * Decimal('100'), 1)
            if capacity_hours > 0 else Decimal('0.0')
        )

        # Attributed revenue: only for staff with vessel hours/project service logs; office-only staff show "—"
        if vessel_h > Decimal('0.00'):
            attributed_rev = emp_attributed.get(emp.id, Decimal('0.00'))
            contribution = attributed_rev - emp_p_cost
            tot_attributed += attributed_rev
        else:
            attributed_rev = None
            contribution = None

        tot_payroll += emp_p_cost
        tot_ot += emp_ot_cost
        tot_vessel_h += vessel_h
        tot_office_h += office_h

        name_display = emp.user.get_full_name() if emp.user else ""
        if not name_display:
            name_display = emp.user.username if emp.user else f"Employee #{emp.id}"

        rows.append({
            'employee': emp,
            'id': emp.id,
            'name': name_display,
            'designation': emp.rank or emp.get_role() or "—",
            'monthly_salary': monthly_sal,
            'payroll_cost': emp_p_cost,
            'is_estimated': is_estimated,
            'ot_cost': emp_ot_cost,
            'vessel_hours': vessel_h,
            'office_hours': office_h,
            'billable_utilisation': utilisation,
            'attributed_revenue': attributed_rev,
            'contribution': contribution,
        })

    # Summary Totals
    total_capacity = Decimal(len(employees) * capacity_hours) if (employees and capacity_hours > 0) else Decimal('0')
    avg_utilisation = round((tot_vessel_h / total_capacity) * Decimal('100'), 1) if total_capacity > Decimal('0') else Decimal('0.0')
    tot_contribution = tot_attributed - tot_payroll

    totals = {
        'total_salary': tot_salary,
        'total_payroll_cost': tot_payroll,
        'total_ot_cost': tot_ot,
        'total_vessel_hours': tot_vessel_h,
        'total_office_hours': tot_office_h,
        'avg_utilisation': avg_utilisation,
        'total_attributed_revenue': tot_attributed,
        'total_contribution': tot_contribution,
    }

    # Sorting
    if sort_by:
        reverse = sort_by.startswith('-')
        field = sort_by.lstrip('-')
        if field in ('monthly_salary', 'payroll_cost', 'ot_cost', 'vessel_hours', 'office_hours', 'billable_utilisation'):
            rows.sort(key=lambda x: x.get(field, Decimal('0.00')), reverse=reverse)
        elif field in ('attributed_revenue', 'contribution'):
            rows.sort(key=lambda x: (x.get(field) is not None, x.get(field) or Decimal('-999999')), reverse=reverse)
        elif field == 'name':
            rows.sort(key=lambda x: (x.get('name') or '').lower(), reverse=reverse)
        elif field == 'designation':
            rows.sort(key=lambda x: (x.get('designation') or '').lower(), reverse=reverse)

    return rows, totals


def employee_cost_excel_response(rows, totals, filename_suffix="all"):
    """
    Exports styled Excel workbook for Employees Cost analysis.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Employees Cost"
    ws.views.sheetView[0].showGridLines = True

    COLOR_PRIMARY = "1F4E78"
    COLOR_TOTAL = "D9E1F2"
    COLOR_ZEBRA = "F9FAFB"

    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_data = Font(name="Calibri", size=10, color="1A202C")
    font_total = Font(name="Calibri", size=10, bold=True, color="1A202C")

    fill_primary = PatternFill(start_color=COLOR_PRIMARY, end_color=COLOR_PRIMARY, fill_type="solid")
    fill_zebra = PatternFill(start_color=COLOR_ZEBRA, end_color=COLOR_ZEBRA, fill_type="solid")
    fill_total = PatternFill(start_color=COLOR_TOTAL, end_color=COLOR_TOTAL, fill_type="solid")

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    thin_border_side = Side(border_style="thin", color="CBD5E1")
    border_cell = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    border_total = Border(
        left=thin_border_side, right=thin_border_side,
        top=Side(border_style="medium", color="1F4E78"),
        bottom=Side(border_style="double", color="1F4E78")
    )

    headers = [
        "Employee Name", "Designation", "Monthly Salary (AED)", "Payroll Cost (AED)",
        "OT Cost (AED)", "Vessel Hours", "Office Hours", "Billable Utilisation %",
        "Attributed Revenue (AED)", "Contribution (AED)"
    ]

    ws.row_dimensions[1].height = 26
    for col_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_primary
        cell.alignment = align_center
        cell.border = border_cell

    current_row = 2
    for idx, r in enumerate(rows):
        row_fill = fill_zebra if idx % 2 == 1 else None
        ws.row_dimensions[current_row].height = 20

        cost_label = float(r['payroll_cost'])
        att_rev_val = float(r['attributed_revenue']) if r['attributed_revenue'] is not None else "—"
        contrib_val = float(r['contribution']) if r['contribution'] is not None else "—"

        values = [
            (r['name'], align_left, font_data, "text"),
            (r['designation'], align_left, font_data, "text"),
            (float(r['monthly_salary']), align_right, font_data, "0.00"),
            (cost_label, align_right, font_data, "0.00"),
            (float(r['ot_cost']), align_right, font_data, "0.00"),
            (float(r['vessel_hours']), align_right, font_data, "0.00"),
            (float(r['office_hours']), align_right, font_data, "0.00"),
            (float(r['billable_utilisation']), align_right, font_data, "pct"),
            (att_rev_val, align_right, font_data, "0.00" if isinstance(att_rev_val, (int, float)) else "text"),
            (contrib_val, align_right, font_data, "0.00" if isinstance(contrib_val, (int, float)) else "text"),
        ]

        for col_idx, (val, alignment, font, kind) in enumerate(values, start=1):
            cell = ws.cell(row=current_row, column=col_idx, value=val)
            cell.alignment = alignment
            cell.font = font
            cell.border = border_cell
            if row_fill:
                cell.fill = row_fill
            if kind == "0.00" and isinstance(val, (int, float)):
                cell.number_format = "#,##0.00"
            elif kind == "pct" and isinstance(val, (int, float)):
                cell.number_format = '0.0"%"'

        current_row += 1

    # Totals Row
    ws.row_dimensions[current_row].height = 22
    total_values = [
        ("TOTAL (AED)", align_left, "text"),
        ("", align_left, "text"),
        (float(totals.get('total_salary', 0)), align_right, "0.00"),
        (float(totals.get('total_payroll_cost', 0)), align_right, "0.00"),
        (float(totals.get('total_ot_cost', 0)), align_right, "0.00"),
        (float(totals.get('total_vessel_hours', 0)), align_right, "0.00"),
        (float(totals.get('total_office_hours', 0)), align_right, "0.00"),
        (float(totals.get('avg_utilisation', 0)), align_right, "pct"),
        (float(totals.get('total_attributed_revenue', 0)), align_right, "0.00"),
        (float(totals.get('total_contribution', 0)), align_right, "0.00"),
    ]

    for col_idx, (val, alignment, kind) in enumerate(total_values, start=1):
        cell = ws.cell(row=current_row, column=col_idx, value=val)
        cell.alignment = alignment
        cell.font = font_total
        cell.fill = fill_total
        cell.border = border_total
        if kind == "0.00" and isinstance(val, (int, float)):
            cell.number_format = "#,##0.00"
        elif kind == "pct" and isinstance(val, (int, float)):
            cell.number_format = '0.0"%"'

    ws.freeze_panes = "C2"

    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            val_str = str(cell.value or '')
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 13)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="employee_cost_{filename_suffix}.xlsx"'
    return response


# =====================================================================
# PHASE 3: RECEIVABLES & AGING
# =====================================================================
# PHASE 3: RECEIVABLES & AGING (BACKED BY INVOICE)
# =====================================================================

def get_receivables_data(filters=None, sort_by=None):
    """
    Returns invoices with outstanding balance > 0 and aging analysis based on due_date.
    Buckets: 0-30, 31-60, 61-90, 90+ days.
    """
    filters = filters or {}
    today = timezone.localdate()

    qs = Invoice.objects.filter(is_cancelled=False).select_related('project')

    if filters.get('client'):
        c_filter = filters['client']
        qs = qs.filter(Q(customer__icontains=c_filter) | Q(project__client_name__icontains=c_filter))
    if filters.get('search'):
        s = filters['search'].strip()
        qs = qs.filter(
            Q(invoice_number__icontains=s) |
            Q(service_ref__icontains=s) |
            Q(vessel__icontains=s) |
            Q(customer__icontains=s) |
            Q(project__name__icontains=s) |
            Q(project__client_name__icontains=s)
        )

    invoices = list(qs)
    receivables = []

    bucket_0_30 = {'amount': Decimal('0.00'), 'count': 0}
    bucket_31_60 = {'amount': Decimal('0.00'), 'count': 0}
    bucket_61_90 = {'amount': Decimal('0.00'), 'count': 0}
    bucket_90_plus = {'amount': Decimal('0.00'), 'count': 0}

    total_outstanding = Decimal('0.00')
    oldest_inv = None
    oldest_days = -1

    for inv in invoices:
        out_aed = inv.outstanding_aed
        out_curr = inv.outstanding
        if out_aed <= Decimal('0.00'):
            continue
        out = out_aed

        if inv.due_date:
            if today <= inv.due_date:
                days_overdue = 0
                bucket = '0-30'
            else:
                days_overdue = (today - inv.due_date).days
                if days_overdue <= 30:
                    bucket = '31-60'
                elif days_overdue <= 60:
                    bucket = '61-90'
                else:
                    bucket = '90+'
        elif inv.invoice_date:
            days_since = (today - inv.invoice_date).days
            days_overdue = days_since
            if days_since <= 30:
                bucket = '0-30'
            elif days_since <= 60:
                bucket = '31-60'
            elif days_since <= 90:
                bucket = '61-90'
            else:
                bucket = '90+'
        else:
            days_overdue = 999
            bucket = '90+'

        if bucket == '0-30':
            bucket_0_30['amount'] += out
            bucket_0_30['count'] += 1
        elif bucket == '31-60':
            bucket_31_60['amount'] += out
            bucket_31_60['count'] += 1
        elif bucket == '61-90':
            bucket_61_90['amount'] += out
            bucket_61_90['count'] += 1
        else:
            bucket_90_plus['amount'] += out
            bucket_90_plus['count'] += 1

        total_outstanding += out

        if days_overdue > oldest_days:
            oldest_days = days_overdue
            oldest_inv = inv

        row = {
            'invoice': inv,
            'id': inv.id,
            'project': inv.project,
            'name': inv.service_ref or (inv.project.name if inv.project else inv.invoice_number),
            'code': inv.project.code if inv.project else '',
            'client_name': inv.customer or (inv.project.client_name if inv.project else '—'),
            'vessel_name': inv.vessel or (inv.project.vessel_name if inv.project else '—'),
            'invoice_number': inv.invoice_number,
            'invoice_date': inv.invoice_date,
            'due_date': inv.due_date,
            'invoice_amount': inv.collection_value,
            'amount_received': inv.amount_received or Decimal('0.00'),
            'outstanding_amount': out_curr,
            'days_outstanding': days_overdue,
            'aging_bucket': bucket,
            'payment_status': inv.status,
            'currency': inv.currency,
            'invoice_amount_aed': inv.collection_value_aed,
            'amount_excl_vat_aed': inv.amount_excl_vat_aed,
            'amount_received_aed': inv.amount_received_aed,
            'outstanding_aed': out_aed,
        }

        # Filter by bucket if specified
        if filters.get('bucket') and filters['bucket'] != 'all':
            if bucket != filters['bucket']:
                continue

        receivables.append(row)

    # Sort receivables
    if sort_by:
        reverse = sort_by.startswith('-')
        field = sort_by.lstrip('-')
        if field in ('invoice_amount', 'amount_received', 'outstanding_amount'):
            aed_key = 'outstanding_aed' if field == 'outstanding_amount' else ('invoice_amount_aed' if field == 'invoice_amount' else 'amount_received_aed')
            receivables.sort(key=lambda x: x.get(aed_key, x.get(field, Decimal('0.00'))), reverse=reverse)
        elif field == 'days_outstanding':
            receivables.sort(key=lambda x: (x.get('days_outstanding') is not None, x.get('days_outstanding') or 0), reverse=reverse)
        elif field == 'invoice_date':
            receivables.sort(key=lambda x: (x.get('invoice_date') is not None, x.get('invoice_date') or date.min), reverse=reverse)
        elif field in ('name', 'client_name', 'vessel_name', 'invoice_number'):
            receivables.sort(key=lambda x: (x.get(field) or '').lower(), reverse=reverse)
        else:
            receivables.sort(key=lambda x: (x.get('days_outstanding') is not None, x.get('days_outstanding') or 0), reverse=True)
    else:
        receivables.sort(key=lambda x: (x['days_outstanding'] is not None, x['days_outstanding'] or 0), reverse=True)

    summary = {
        'total_outstanding': total_outstanding,
        'total_count': sum(b['count'] for b in [bucket_0_30, bucket_31_60, bucket_61_90, bucket_90_plus]),
        'bucket_0_30': bucket_0_30,
        'bucket_31_60': bucket_31_60,
        'bucket_61_90': bucket_61_90,
        'bucket_90_plus': bucket_90_plus,
        'oldest_project': oldest_inv,
        'oldest_days': oldest_days if oldest_days >= 0 else None,
    }

    return receivables, summary


def receivables_excel_response(receivables, summary, filename_suffix="all"):
    """
    Exports styled Excel workbook for Receivables & Aging analysis.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Receivables Aging"
    ws.views.sheetView[0].showGridLines = True

    COLOR_PRIMARY = "1F4E78"
    COLOR_TOTAL = "D9E1F2"
    COLOR_ZEBRA = "F9FAFB"

    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_data = Font(name="Calibri", size=10, color="1A202C")
    font_total = Font(name="Calibri", size=10, bold=True, color="1A202C")

    fill_primary = PatternFill(start_color=COLOR_PRIMARY, end_color=COLOR_PRIMARY, fill_type="solid")
    fill_zebra = PatternFill(start_color=COLOR_ZEBRA, end_color=COLOR_ZEBRA, fill_type="solid")
    fill_total = PatternFill(start_color=COLOR_TOTAL, end_color=COLOR_TOTAL, fill_type="solid")

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    thin_border_side = Side(border_style="thin", color="CBD5E1")
    border_cell = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    border_total = Border(
        left=thin_border_side, right=thin_border_side,
        top=Side(border_style="medium", color="1F4E78"),
        bottom=Side(border_style="double", color="1F4E78")
    )

    headers = [
        "Sl", "Invoice #", "Project / Customer", "Invoice Date", "Due Date", "Days Overdue", "Aging Bucket",
        "Currency", "Rate", "Invoice Amount", "Received", "Outstanding",
        "Invoice Amount (AED)", "Received (AED)", "Outstanding (AED)", "Status"
    ]

    ws.row_dimensions[1].height = 26
    for col_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_primary
        cell.alignment = align_center
        cell.border = border_cell

    current_row = 2
    tot_inv = Decimal('0.00')
    tot_rec = Decimal('0.00')
    tot_out = Decimal('0.00')

    for idx, r in enumerate(receivables):
        row_fill = fill_zebra if idx % 2 == 1 else None
        ws.row_dimensions[current_row].height = 20

        inv = r.get('invoice')
        inv_amt_aed = r.get('invoice_amount_aed', r['invoice_amount'])
        rec_amt_aed = r.get('amount_received_aed', r['amount_received'])
        out_amt_aed = r.get('outstanding_aed', r['outstanding_amount'])

        tot_inv += inv_amt_aed
        tot_rec += rec_amt_aed
        tot_out += out_amt_aed

        inv_date_str = r['invoice_date'].strftime('%Y-%m-%d') if r.get('invoice_date') else "—"
        due_date_str = r['due_date'].strftime('%Y-%m-%d') if r.get('due_date') else (inv.due_date.strftime('%Y-%m-%d') if inv and inv.due_date else "—")
        days_val = r['days_outstanding'] if r.get('days_outstanding') is not None else 0
        proj_cust_str = f"{r.get('name') or ''} / {r.get('client_name') or ''}".strip(' /') or "—"
        currency = r.get('currency') or (inv.currency if inv else "AED")
        rate_val = float(inv.exchange_rate if inv else 1)

        values = [
            (idx + 1, align_center, font_data, "int"),
            (str(r.get('invoice_number') or "—"), align_left, font_data, "text"),
            (proj_cust_str, align_left, font_data, "text"),
            (inv_date_str, align_center, font_data, "text"),
            (due_date_str, align_center, font_data, "text"),
            (days_val, align_center, font_data, "int"),
            (f"{r.get('aging_bucket')} Days" if r.get('aging_bucket') else "—", align_center, font_data, "text"),
            (currency, align_center, font_data, "text"),
            (rate_val, align_right, font_data, "rate"),
            (float(r.get('invoice_amount') or 0), align_right, font_data, "0.00"),
            (float(r.get('amount_received') or 0), align_right, font_data, "0.00"),
            (float(r.get('outstanding_amount') or 0), align_right, font_data, "0.00"),
            (float(inv_amt_aed), align_right, font_data, "0.00"),
            (float(rec_amt_aed), align_right, font_data, "0.00"),
            (float(out_amt_aed), align_right, font_data, "0.00"),
            (str(r.get('payment_status') or "OPEN"), align_center, font_data, "text"),
        ]

        for col_idx, (val, alignment, font, kind) in enumerate(values, start=1):
            cell = ws.cell(row=current_row, column=col_idx, value=val)
            cell.alignment = alignment
            cell.font = font
            cell.border = border_cell
            if row_fill:
                cell.fill = row_fill
            if kind == "0.00" and isinstance(val, (int, float)):
                cell.number_format = "#,##0.00"
            elif kind == "rate" and isinstance(val, (int, float)):
                cell.number_format = "#,##0.0000"
            elif kind == "int" and isinstance(val, int):
                cell.number_format = '#,##0'

        current_row += 1

    # Totals Row
    ws.row_dimensions[current_row].height = 22
    total_values = [
        ("TOTAL (AED)", align_left, font_total, "text"),
        ("", align_left, font_total, "text"),
        ("", align_left, font_total, "text"),
        ("", align_center, font_total, "text"),
        ("", align_center, font_total, "text"),
        ("", align_center, font_total, "text"),
        ("", align_center, font_total, "text"),
        ("", align_center, font_total, "text"),
        ("", align_right, font_total, "text"),
        ("", align_right, font_total, "text"),
        ("", align_right, font_total, "text"),
        ("", align_right, font_total, "text"),
        (float(tot_inv), align_right, font_total, "0.00"),
        (float(tot_rec), align_right, font_total, "0.00"),
        (float(tot_out), align_right, font_total, "0.00"),
        ("", align_center, font_total, "text"),
    ]

    for col_idx, (val, alignment, font, kind) in enumerate(total_values, start=1):
        cell = ws.cell(row=current_row, column=col_idx, value=val)
        cell.alignment = alignment
        cell.font = font
        cell.fill = fill_total
        cell.border = border_total
        if kind == "0.00" and isinstance(val, (int, float)):
            cell.number_format = "#,##0.00"

    ws.freeze_panes = "C2"

    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            val_str = str(cell.value or '')
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 13)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="receivables_aging_{filename_suffix}.xlsx"'
    return response



# =====================================================================
# PHASE 3: DASHBOARD ANALYTICS & INSIGHT PANELS
# =====================================================================

def calc_kpi_delta(current_val, prev_val, is_pct=False, lower_is_better=False):
    """
    Computes delta against previous equal period:
    - Absolute change for % metrics
    - Percentage change for currency / counts
    - None if current_val or prev is 0 or None
    """
    if current_val is None or prev_val is None:
        return None, None
    prev_d = Decimal(str(prev_val))
    if prev_d == Decimal('0.00'):
        return None, None
    curr_d = Decimal(str(current_val))

    if is_pct:
        delta = round(curr_d - prev_d, 1)
    else:
        delta = round(((curr_d - prev_d) / abs(prev_d)) * Decimal('100'), 1)

    if delta == Decimal('0.0'):
        is_good = None
    elif lower_is_better:
        is_good = (delta < 0)
    else:
        is_good = (delta > 0)

    return delta, is_good


def get_dashboard_data(period='this_month', custom_start=None, custom_end=None, request=None):
    """
    Dashboard engine reusing pnl_for_range and get_projects_profitability.
    Computes:
      - 8 KPI cards with previous-period deltas (.att-an-delta good/bad/muted)
      - 12-month mini chart
      - Cost breakdown doughnut chart
      - Top 5 clients & client concentration percentage (>40% warning)
      - Top 5 most profitable & 5 least profitable/loss-making projects
      - Project category margins bar chart
      - Break-even panel (last 3 full months, ignoring no_data and MTD)
      - Expense-trend panel (last 3 full months, ignoring no_data and MTD, flagging >= +25%)
      - 5 Data-quality alerts with matching URLs
    """
    today = timezone.localdate()
    current_month_first = today.replace(day=1)
    fin_start_month = get_finance_start_month(request)

    # 1. Determine date range and previous equal period
    if period == 'last_month':
        last_m_end = current_month_first - timedelta(days=1)
        start_date = last_m_end.replace(day=1)
        end_date = last_m_end
        prev_end = start_date - timedelta(days=1)
        prev_start = prev_end.replace(day=1)
    elif period == 'this_quarter':
        quarter_month = ((today.month - 1) // 3) * 3 + 1
        start_date = date(today.year, quarter_month, 1)
        end_date = today
        num_days = (end_date - start_date).days + 1
        prev_end = start_date - timedelta(days=1)
        prev_start = prev_end - timedelta(days=num_days - 1)
    elif period == 'ytd':
        start_date = date(today.year, 1, 1)
        end_date = today
        num_days = (end_date - start_date).days + 1
        prev_end = start_date - timedelta(days=1)
        prev_start = prev_end - timedelta(days=num_days - 1)
    elif period == 'custom' and custom_start and custom_end:
        start_date = custom_start
        end_date = custom_end
        num_days = (end_date - start_date).days + 1
        prev_end = start_date - timedelta(days=1)
        prev_start = prev_end - timedelta(days=num_days - 1)
    else:
        period = 'this_month'
        start_date = current_month_first
        end_date = today
        num_days = (end_date - start_date).days + 1
        prev_end = start_date - timedelta(days=1)
        prev_start = prev_end - timedelta(days=num_days - 1)

    # 2. Reuse pnl_for_range for current and previous period with granularity='range'
    curr_pnl = pnl_for_range(start_date, end_date, granularity='range', request=request)
    curr_tot = curr_pnl['totals']

    prev_pnl = pnl_for_range(prev_start, prev_end, granularity='range', request=request)
    prev_tot = prev_pnl['totals']

    # 3. Calculate 8 KPI Cards
    # KPI 1: Revenue
    rev_val = curr_tot.get('revenue', Decimal('0.00'))
    rev_prev = prev_tot.get('revenue', Decimal('0.00'))
    rev_delta, rev_good = calc_kpi_delta(rev_val, rev_prev, is_pct=False, lower_is_better=False)

    # KPI 2: Total Cost
    cost_val = curr_tot.get('total_cost', Decimal('0.00'))
    cost_prev = prev_tot.get('total_cost', Decimal('0.00'))
    cost_delta, cost_good = calc_kpi_delta(cost_val, cost_prev, is_pct=False, lower_is_better=True)

    # KPI 3: Net Profit
    profit_val = curr_tot.get('profit', Decimal('0.00'))
    profit_prev = prev_tot.get('profit', Decimal('0.00'))
    profit_delta, profit_good = calc_kpi_delta(profit_val, profit_prev, is_pct=False, lower_is_better=False)

    # KPI 4: Margin %
    margin_val = curr_tot.get('margin_pct')
    margin_prev = prev_tot.get('margin_pct')
    margin_delta, margin_good = calc_kpi_delta(margin_val, margin_prev, is_pct=True, lower_is_better=False)

    # KPI 5: Outstanding Receivables
    all_receivables, rec_summary = get_receivables_data()
    rec_val = rec_summary['total_outstanding']
    # Receivables delta vs previous period can be neutral
    rec_delta, rec_good = None, None

    # KPI 8: Engineer Days (extracted early for KPI 6 calculation)
    eng_days_val = curr_tot.get('engineer_days', 0)
    eng_days_prev = prev_tot.get('engineer_days', 0)
    eng_days_delta, eng_days_good = calc_kpi_delta(eng_days_val, eng_days_prev, is_pct=False, lower_is_better=False)

    # KPI 6: Rev per Engineer Day
    if rev_val > Decimal('0.00') and eng_days_val > 0:
        rev_day_val = curr_tot.get('revenue_per_engineer_day')
    else:
        rev_day_val = None
    if rev_prev > Decimal('0.00') and eng_days_prev > 0:
        rev_day_prev = prev_tot.get('revenue_per_engineer_day')
    else:
        rev_day_prev = None
    rev_day_delta, rev_day_good = calc_kpi_delta(rev_day_val, rev_day_prev, is_pct=False, lower_is_better=False)

    # KPI 7: Payroll % of Revenue
    curr_pay = curr_tot.get('payroll_cost', Decimal('0.00'))
    payroll_pct = round((curr_pay / rev_val) * Decimal('100'), 1) if rev_val > Decimal('0.00') else None
    prev_pay = prev_tot.get('payroll_cost', Decimal('0.00'))
    prev_pay_pct = round((prev_pay / rev_prev) * Decimal('100'), 1) if rev_prev > Decimal('0.00') else None
    payroll_pct_delta, payroll_pct_good = calc_kpi_delta(payroll_pct, prev_pay_pct, is_pct=True, lower_is_better=True)

    kpis = {
        'revenue': {'value': rev_val, 'delta': rev_delta, 'is_good': rev_good, 'prev': rev_prev},
        'total_cost': {'value': cost_val, 'delta': cost_delta, 'is_good': cost_good, 'prev': cost_prev},
        'profit': {'value': profit_val, 'delta': profit_delta, 'is_good': profit_good, 'prev': profit_prev},
        'margin_pct': {'value': margin_val, 'delta': margin_delta, 'is_good': margin_good, 'prev': margin_prev},
        'receivables': {'value': rec_val, 'count': rec_summary['total_count'], 'delta': rec_delta, 'is_good': rec_good},
        'rev_per_engineer_day': {'value': rev_day_val, 'delta': rev_day_delta, 'is_good': rev_day_good, 'prev': rev_day_prev},
        'payroll_pct_revenue': {'value': payroll_pct, 'delta': payroll_pct_delta, 'is_good': payroll_pct_good, 'prev': prev_pay_pct},
        'engineer_days': {'value': eng_days_val, 'delta': eng_days_delta, 'is_good': eng_days_good, 'prev': eng_days_prev},
    }

    # 4. Mini Chart: Last 12 Months Revenue vs Total Cost
    m12_start = (current_month_first - timedelta(days=365)).replace(day=1)
    m12_pnl = pnl_for_range(m12_start, today, granularity='month', request=request)
    mini_chart = {
        'labels': [b['label'] for b in m12_pnl['buckets']],
        'revenue': [float(b['revenue']) for b in m12_pnl['buckets']],
        'total_cost': [float(b['total_cost']) for b in m12_pnl['buckets']],
    }

    # 5. Cost Breakdown Doughnut Chart
    # Reuses curr_pnl range bucket: payroll_cost - payroll_ot_cost, payroll_ot_cost, project_direct, overheads
    curr_bucket = curr_pnl['buckets'][0] if curr_pnl['buckets'] else None
    pay_cost = curr_bucket['payroll_cost'] if curr_bucket else Decimal('0.00')
    period_ot = curr_bucket['payroll_ot_cost'] if curr_bucket else Decimal('0.00')
    payroll_basic = pay_cost - period_ot
    proj_direct = curr_bucket['project_direct_cost'] if curr_bucket else Decimal('0.00')
    overhead_total = curr_bucket['overhead_cost'] if curr_bucket else Decimal('0.00')

    # Overheads breakdown by category
    overhead_expenses = list(
        Expense.objects.filter(
            date__range=(start_date, end_date),
            category__kind='OVERHEAD'
        ).values('category__name').annotate(cat_total=Sum('amount_aed')).order_by('-cat_total')
    )
    top_5_cats = overhead_expenses[:5]
    top_5_cats_sum = sum((c['cat_total'] or Decimal('0.00')) for c in top_5_cats)
    other_overhead_sum = max(overhead_total - top_5_cats_sum, Decimal('0.00'))

    cost_doughnut_labels = ["Payroll (Basic & Allowances)", "Overtime", "Project Direct Costs"]
    cost_doughnut_values = [float(payroll_basic), float(period_ot), float(proj_direct)]

    for c in top_5_cats:
        c_name = c['category__name'] or 'Overhead'
        cost_doughnut_labels.append(c_name)
        cost_doughnut_values.append(float(c['cat_total'] or 0))

    if other_overhead_sum > Decimal('0.00'):
        cost_doughnut_labels.append("Other Overheads")
        cost_doughnut_values.append(float(other_overhead_sum))

    cost_doughnut = {
        'labels': cost_doughnut_labels,
        'values': cost_doughnut_values,
    }

    # 6. Projects & Clients Analysis (using Invoice Costing when available, fallback to Projects)
    inv_rows, _ = get_invoice_costing_data(filters={'start_date': start_date, 'end_date': end_date})
    if inv_rows:
        client_stats = defaultdict(lambda: {'revenue': Decimal('0.00'), 'profit': Decimal('0.00'), 'projects_count': 0})
        cat_stats = defaultdict(lambda: {'revenue': Decimal('0.00'), 'profit': Decimal('0.00')})

        for r in inv_rows:
            inv = r['invoice']
            c_name = r['customer'] or 'Unknown Client'
            rev = r['collection_value_aed']
            prof = r['total_net_profit']
            client_stats[c_name]['revenue'] += rev
            client_stats[c_name]['profit'] += prof
            client_stats[c_name]['projects_count'] += 1

            cat_name = inv.get_invoice_type_display() if hasattr(inv, 'get_invoice_type_display') else 'Service Job'
            cat_stats[cat_name]['revenue'] += rev
            cat_stats[cat_name]['profit'] += prof

        top_clients_list = []
        for c_name, st in sorted(client_stats.items(), key=lambda x: -x[1]['revenue']):
            c_rev = st['revenue']
            c_prof = st['profit']
            c_margin = round((c_prof / c_rev) * Decimal('100'), 1) if c_rev > Decimal('0.00') else Decimal('0.0')
            top_clients_list.append({
                'name': c_name,
                'revenue': c_rev,
                'profit': c_prof,
                'margin_pct': c_margin,
                'projects_count': st['projects_count'],
            })

        top_5_clients = top_clients_list[:5]
        top_client_concentration = Decimal('0.0')
        if top_clients_list and rev_val > Decimal('0.00'):
            top_client_concentration = round((top_clients_list[0]['revenue'] / rev_val) * Decimal('100'), 1)

        has_high_client_concentration = bool(top_client_concentration > Decimal('40.0'))

        # Group period's inv_rows by project (or per invoice if no project)
        grouped_projects = {}

        for r in inv_rows:
            inv = r['invoice']
            if inv.project_id:
                key = ('project', inv.project_id)
                if key not in grouped_projects:
                    proj = inv.project
                    p_name = proj.name if proj else (r['project'].name if r.get('project') else 'Project')
                    p_client = (proj.client_name if proj else '') or r.get('customer') or ''
                    p_vessel = (proj.vessel_name if proj else '') or r.get('vessel') or ''
                    grouped_projects[key] = {
                        'id': inv.project_id,
                        'name': p_name,
                        'client_name': p_client,
                        'vessel_name': p_vessel,
                        'revenue': Decimal('0.00'),
                        'total_cost': Decimal('0.00'),
                        'profit': Decimal('0.00'),
                        'invoice_count': 0,
                        'url': reverse('fin_project_detail', args=[inv.project_id]),
                    }
                grp = grouped_projects[key]
                grp['revenue'] += r['collection_value_aed']
                grp['total_cost'] += r['total_cost']
                grp['profit'] += r['total_net_profit']
                grp['invoice_count'] += 1
            else:
                key = ('invoice', inv.id)
                grouped_projects[key] = {
                    'id': None,
                    'name': inv.invoice_number,
                    'client_name': r.get('customer') or '',
                    'vessel_name': r.get('vessel') or '',
                    'revenue': r['collection_value_aed'],
                    'total_cost': r['total_cost'],
                    'profit': r['total_net_profit'],
                    'invoice_count': 1,
                    'url': reverse('fin_invoice_edit', args=[inv.id]),
                }

        project_list = []
        for grp in grouped_projects.values():
            rev = grp['revenue']
            prof = grp['profit']
            grp['margin_pct'] = round((prof / rev) * Decimal('100'), 1) if rev != Decimal('0.00') else None
            grp['is_loss'] = bool(prof < Decimal('0.00'))
            project_list.append(grp)

        projects_by_profit_desc = sorted(project_list, key=lambda x: x['profit'], reverse=True)
        top_5_profitable_projects = projects_by_profit_desc[:5]
        remaining_projects = projects_by_profit_desc[5:]
        least_5_profitable_projects = sorted(remaining_projects, key=lambda x: x['profit'])[:5]

        category_margins = []
        for cat_name, st in sorted(cat_stats.items(), key=lambda x: -x[1]['revenue']):
            c_rev = st['revenue']
            c_prof = st['profit']
            c_m = round((c_prof / c_rev) * Decimal('100'), 1) if c_rev > Decimal('0.00') else Decimal('0.0')
            category_margins.append({
                'category': cat_name,
                'revenue': float(c_rev),
                'profit': float(c_prof),
                'margin_pct': float(c_m),
            })
    else:
        all_projects, _ = get_projects_profitability(filters={'start_date': start_date, 'end_date': end_date})
        invoiced_projects = [p for p in all_projects if p['invoiced']]

        # Client concentration & Top 5 clients
        client_stats = defaultdict(lambda: {'revenue': Decimal('0.00'), 'profit': Decimal('0.00'), 'projects_count': 0})
        for p in invoiced_projects:
            c_name = p['client_name'] or 'Unknown Client'
            client_stats[c_name]['revenue'] += p['revenue']
            client_stats[c_name]['profit'] += p['profit']
            client_stats[c_name]['projects_count'] += 1

        top_clients_list = []
        for c_name, st in sorted(client_stats.items(), key=lambda x: -x[1]['revenue']):
            c_rev = st['revenue']
            c_prof = st['profit']
            c_margin = round((c_prof / c_rev) * Decimal('100'), 1) if c_rev > Decimal('0.00') else Decimal('0.0')
            top_clients_list.append({
                'name': c_name,
                'revenue': c_rev,
                'profit': c_prof,
                'margin_pct': c_margin,
                'projects_count': st['projects_count'],
            })

        top_5_clients = top_clients_list[:5]
        top_client_concentration = Decimal('0.0')
        if top_clients_list and rev_val > Decimal('0.00'):
            top_client_concentration = round((top_clients_list[0]['revenue'] / rev_val) * Decimal('100'), 1)

        has_high_client_concentration = bool(top_client_concentration > Decimal('40.0'))

        # Top 5 most profitable & 5 least profitable projects
        fallback_list = []
        for p in all_projects:
            rev = p['revenue']
            prof = p['profit']
            margin_pct = round((prof / rev) * Decimal('100'), 1) if rev != Decimal('0.00') else None
            fallback_list.append({
                'id': p['id'],
                'name': p['name'],
                'client_name': p.get('client_name') or '',
                'vessel_name': p.get('vessel_name') or '',
                'revenue': rev,
                'total_cost': p['total_cost'],
                'profit': prof,
                'margin_pct': margin_pct,
                'invoice_count': p.get('invoice_count', 1 if p.get('invoiced') else 0),
                'url': reverse('fin_project_detail', args=[p['id']]),
                'is_loss': bool(prof < Decimal('0.00')),
            })

        projects_by_profit_desc = sorted(fallback_list, key=lambda x: x['profit'], reverse=True)
        top_5_profitable_projects = projects_by_profit_desc[:5]
        remaining_projects = projects_by_profit_desc[5:]
        least_5_profitable_projects = sorted(remaining_projects, key=lambda x: x['profit'])[:5]

        # Category margins bar chart
        cat_stats = defaultdict(lambda: {'revenue': Decimal('0.00'), 'profit': Decimal('0.00')})
        for p in invoiced_projects:
            cat_name = p['category'] or 'General'
            cat_stats[cat_name]['revenue'] += p['revenue']
            cat_stats[cat_name]['profit'] += p['profit']

        category_margins = []
        for cat_name, st in sorted(cat_stats.items(), key=lambda x: -x[1]['revenue']):
            c_rev = st['revenue']
            c_prof = st['profit']
            c_m = round((c_prof / c_rev) * Decimal('100'), 1) if c_rev > Decimal('0.00') else Decimal('0.0')
            category_margins.append({
                'category': cat_name,
                'revenue': float(c_rev),
                'profit': float(c_prof),
                'margin_pct': float(c_m),
            })

    # 7. Break-even & Expense-trend panels (ignore no_data and MTD months)
    candidate_months = []
    check_m = (current_month_first - timedelta(days=1)).replace(day=1)
    while len(candidate_months) < 3 and check_m >= fin_start_month and (current_month_first.year - check_m.year) <= 2:
        candidate_months.append(check_m)
        check_m = (check_m - timedelta(days=1)).replace(day=1)

    candidate_months = sorted(candidate_months)

    break_even_panel = None
    expense_trend_panel = []

    if candidate_months:
        hist_pnl = pnl_for_range(candidate_months[0], candidate_months[-1].replace(day=28), granularity='month', request=request)
        valid_buckets = [b for b in hist_pnl['buckets'] if not b['no_data'] and not b['mtd']]

        if valid_buckets:
            n_months = len(valid_buckets)
            sum_fixed_costs = sum(b['overhead_cost'] + b['payroll_cost'] for b in valid_buckets)
            avg_monthly_fixed = round(sum_fixed_costs / Decimal(n_months), 2)

            hist_tot_rev = sum(b['revenue'] for b in valid_buckets)
            hist_tot_direct = sum(b['project_direct_cost'] for b in valid_buckets)

            if hist_tot_rev > Decimal('0.00'):
                contribution_margin = (hist_tot_rev - hist_tot_direct) / hist_tot_rev
                cm_pct = round(contribution_margin * Decimal('100'), 1)
            else:
                contribution_margin = Decimal('0.00')
                cm_pct = Decimal('0.0')

            hist_eng_days = sum(b['engineer_days'] for b in valid_buckets)
            avg_rev_eng_day = round(hist_tot_rev / Decimal(hist_eng_days), 2) if hist_eng_days > 0 else Decimal('0.00')

            if contribution_margin > Decimal('0.00'):
                break_even_rev = round(avg_monthly_fixed / contribution_margin, 2)
                break_even_eng_days = math.ceil(break_even_rev / avg_rev_eng_day) if avg_rev_eng_day > Decimal('0.00') else 0
            else:
                break_even_rev = None
                break_even_eng_days = None

            break_even_panel = {
                'months_count': n_months,
                'avg_monthly_fixed_cost': avg_monthly_fixed,
                'contribution_margin_pct': cm_pct,
                'contribution_margin': contribution_margin,
                'break_even_revenue': break_even_rev,
                'avg_rev_per_eng_day': avg_rev_eng_day,
                'break_even_engineer_days': break_even_eng_days,
            }

            # Expense Trend Panel (MTD vs prorated 3-mo avg)
            hist_cat_expenses = defaultdict(Decimal)
            for b in valid_buckets:
                for c in b['details']['overhead_by_category']:
                    hist_cat_expenses[c['name']] += c['amount']

            # Current month (MTD) expenses by category
            curr_mtd_expenses = list(
                Expense.objects.filter(
                    date__gte=current_month_first,
                    date__lte=today,
                    category__kind='OVERHEAD'
                ).values('category__name').annotate(amt=Sum('amount_aed'))
            )
            curr_mtd_by_cat = {c['category__name']: (c['amt'] or Decimal('0.00')) for c in curr_mtd_expenses}

            days_elapsed = today.day
            days_in_current_month = calendar.monthrange(today.year, today.month)[1]
            proration_factor = Decimal(days_elapsed) / Decimal(days_in_current_month)

            all_cat_names = set(hist_cat_expenses.keys()) | set(curr_mtd_by_cat.keys())
            for cat_name in sorted(all_cat_names):
                hist_avg = round(hist_cat_expenses[cat_name] / Decimal(n_months), 2)
                prorated_3mo_avg = round(hist_avg * proration_factor, 2)
                curr_val = curr_mtd_by_cat.get(cat_name, Decimal('0.00'))
                if prorated_3mo_avg > Decimal('0.00'):
                    delta_pct = round(((curr_val - prorated_3mo_avg) / prorated_3mo_avg) * Decimal('100'), 1)
                else:
                    delta_pct = Decimal('100.0') if curr_val > Decimal('0.00') else Decimal('0.0')

                is_flagged = bool(delta_pct >= Decimal('25.0'))
                expense_trend_panel.append({
                    'category': cat_name,
                    'hist_avg': hist_avg,
                    'prorated_avg': prorated_3mo_avg,
                    'current_mtd': curr_val,
                    'delta_pct': delta_pct,
                    'is_flagged': is_flagged,
                })

    # 8. Data-Quality Alerts with direct filtered links via reverse and urlencode
    alerts = []

    # Alert 1: Overdue invoices count + total amount (query only candidates)
    candidate_overdue = [
        inv for inv in Invoice.objects.filter(is_cancelled=False, due_date__lt=today)
        if inv.outstanding_aed > Decimal('0.00')
    ]
    c_overdue = len(candidate_overdue)
    amt_overdue = sum((inv.outstanding_aed for inv in candidate_overdue), Decimal('0.00'))
    if c_overdue > 0:
        alerts.append({
            'id': 'overdue_invoices',
            'type': 'danger',
            'icon': 'fas fa-file-invoice-dollar',
            'title': f"{c_overdue} Overdue invoice(s) totalling AED {amt_overdue:,.2f}",
            'description': f"Invoices past due date with total pending balance of AED {amt_overdue:,.2f}.",
            'link': reverse('fin_receivables'),
            'link_text': "View Receivables",
        })

    # Alert 2: Spares rows with alerts
    spares_alert_rows = [s for s in SpareSale.objects.all().select_related('invoice') if s.alerts]
    c_spares_alerts = len(spares_alert_rows)
    if c_spares_alerts > 0:
        alerts.append({
            'id': 'spares_with_alerts',
            'type': 'warning',
            'icon': 'fas fa-exclamation-triangle',
            'title': f"{c_spares_alerts} Spare parts record(s) with pricing or billing alerts",
            'description': "Spares with missing purchase cost, pricing discrepancies, or unbilled status.",
            'link': f"{reverse('fin_spares')}?alerts_only=1",
            'link_text': "Review Spares",
        })

    # Alert 3: SERVICE_JOB invoices whose project's WorkOrder has SpareConsumed rows but invoice has no SpareSale
    c_svc_spares_missing = Invoice.objects.filter(
        is_cancelled=False,
        invoice_type='SERVICE_JOB',
        project__isnull=False
    ).filter(
        Exists(SpareConsumed.objects.filter(work_order__project=OuterRef('project'))),
        ~Exists(SpareSale.objects.filter(invoice=OuterRef('pk')))
    ).count()
    if c_svc_spares_missing > 0:
        alerts.append({
            'id': 'service_job_missing_spares',
            'type': 'warning',
            'icon': 'fas fa-cogs',
            'title': f"{c_svc_spares_missing} Service Job invoice(s) with consumed spares but no spares record",
            'description': "Service job invoices with consumed spares recorded on the work order but no itemized SpareSale records.",
            'link': reverse('fin_spares'),
            'link_text': "Review Spares",
        })

    # Alert 4: Completed project not invoiced (no non-cancelled invoice linked)
    c_completed_uninv = Project.objects.filter(
        status='COMPLETED'
    ).filter(
        ~Exists(Invoice.objects.filter(project=OuterRef('pk'), is_cancelled=False))
    ).count()
    if c_completed_uninv > 0:
        alerts.append({
            'id': 'completed_no_invoice',
            'type': 'warning',
            'icon': 'fas fa-exclamation-triangle',
            'title': f"{c_completed_uninv} Completed project(s) not invoiced",
            'description': "Projects marked Completed with no active invoice linked.",
            'link': reverse('fin_invoices'),
            'link_text': "View Invoices",
        })

    # Alert 5: Invoice without engineer logs (non-cancelled SERVICE_JOB invoices whose project has no EmployeeServiceLog)
    c_no_logs = Invoice.objects.filter(
        is_cancelled=False,
        invoice_type='SERVICE_JOB',
        project__isnull=False
    ).filter(
        ~Exists(EmployeeServiceLog.objects.filter(project=OuterRef('project')))
    ).count()
    if c_no_logs > 0:
        alerts.append({
            'id': 'invoiced_no_logs',
            'type': 'warning',
            'icon': 'fas fa-clipboard-list',
            'title': f"{c_no_logs} Invoice(s) without engineer service logs",
            'description': "Non-cancelled service job invoices whose project has no engineer service logs recorded.",
            'link': reverse('fin_jobs_pnl'),
            'link_text': "Review Jobs P&L",
        })

    # Alert: Legacy invoices needing a real invoice number
    c_legacy = Invoice.objects.filter(invoice_number__startswith='LEGACY-', is_cancelled=False).count()
    if c_legacy > 0:
        alerts.append({
            'id': 'legacy_invoices',
            'type': 'warning',
            'icon': 'fas fa-history',
            'title': f"{c_legacy} legacy invoice(s) need a real invoice number",
            'description': "Invoices created with temporary LEGACY-* numbers require proper AMMS invoice numbers.",
            'link': f"{reverse('fin_invoices')}?search=LEGACY",
            'link_text': "Review Legacy Invoices",
        })

    # Alert: Invoices with both Legacy purchases and Spares records
    c_legacy_spares_overlap = Invoice.objects.filter(
        is_cancelled=False
    ).filter(
        Exists(JobCost.objects.filter(invoice=OuterRef('pk'), source='LEGACY', description='Legacy purchases')),
        Exists(SpareSale.objects.filter(invoice=OuterRef('pk')))
    ).count()
    if c_legacy_spares_overlap > 0:
        alerts.append({
            'id': 'legacy_purchases_and_spares',
            'type': 'warning',
            'icon': 'fas fa-exclamation-triangle',
            'title': f"{c_legacy_spares_overlap} invoice(s) have both Legacy purchases and Spares records",
            'description': "Invoices have both a legacy purchases cost and itemized spare parts recorded, which may result in double-counting.",
            'link': reverse('fin_jobs_pnl'),
            'link_text': "Review Jobs P&L",
        })

    # Alert: Spares not linked to an invoice
    c_unlinked_spares = SpareSale.objects.filter(
        invoice__isnull=True
    ).filter(
        Q(project__isnull=True) |
        ~Exists(Invoice.objects.filter(project=OuterRef('project'), is_cancelled=False))
    ).count()
    if c_unlinked_spares > 0:
        alerts.append({
            'id': 'spares_not_linked_to_invoice',
            'type': 'warning',
            'icon': 'fas fa-tools',
            'title': f"{c_unlinked_spares} Spares not linked to an invoice",
            'description': "Spare part records without an invoice or whose project has no non-cancelled invoice, excluded from P&L.",
            'link': reverse('fin_spares'),
            'link_text': "Review Spares Accounting",
        })

    # Alert 4: Active employees without salary
    c4 = Employee.objects.filter(user__is_active=True).filter(Q(salary__isnull=True) | Q(salary=0)).count()
    if c4 > 0:
        alerts.append({
            'id': 'active_no_salary',
            'type': 'info',
            'icon': 'fas fa-user-tag',
            'title': f"{c4} Active employee(s) without base salary configured",
            'description': "Base monthly salary is missing, leading to 0.00 basic payroll estimates.",
            'link': reverse('fin_employee_cost'),
            'link_text': "View Employees",
        })

    # Alert 5: Months without approved payroll (a month is OK if it has any Payroll with status in ('APPROVED', 'PAID'))
    last_completed_m = (current_month_first - timedelta(days=1)).replace(day=1)
    all_completed_months = []
    chk_m = fin_start_month
    while chk_m <= last_completed_m:
        all_completed_months.append(chk_m)
        if chk_m.month == 12:
            chk_m = date(chk_m.year + 1, 1, 1)
        else:
            chk_m = date(chk_m.year, chk_m.month + 1, 1)

    ok_months = set(Payroll.objects.filter(
        month__gte=fin_start_month,
        month__lte=last_completed_m,
        status__in=['APPROVED', 'PAID']
    ).values_list('month', flat=True).distinct())

    unapproved_months = [m for m in all_completed_months if m not in ok_months]
    if unapproved_months:
        earliest_missing = unapproved_months[0].strftime('%Y-%m')
        query = urlencode({'month': earliest_missing})
        alerts.append({
            'id': 'unapproved_payroll',
            'type': 'warning',
            'icon': 'fas fa-money-check-alt',
            'title': f"{len(unapproved_months)} Month(s) without approved payroll",
            'description': "Past months prior to this month lack approved or paid payroll rows.",
            'link': f"{reverse('fin_payroll')}?{query}",
            'link_text': f"Go to Payroll ({earliest_missing})",
        })

    return {
        'period': period,
        'start_date': start_date,
        'end_date': end_date,
        'prev_start': prev_start,
        'prev_end': prev_end,
        'kpis': kpis,
        'mini_chart': mini_chart,
        'cost_doughnut': cost_doughnut,
        'top_5_clients': top_5_clients,
        'top_client_concentration': top_client_concentration,
        'has_high_client_concentration': has_high_client_concentration,
        'top_5_profitable_projects': top_5_profitable_projects,
        'least_5_profitable_projects': least_5_profitable_projects,
        'category_margins': category_margins,
        'break_even_panel': break_even_panel,
        'expense_trend_panel': expense_trend_panel,
        'alerts': alerts,
    }


# =====================================================================
# FINANCE DATA IMPORT (INVOICES, JOBS P&L, SPARES)
# =====================================================================

def _normalize_key(key):
    if not key:
        return ''
    return ''.join(c.lower() for c in str(key) if c.isalnum())


def _get_row_val(row, *candidates):
    norm_row = {_normalize_key(k): v for k, v in row.items()}
    for cand in candidates:
        norm_c = _normalize_key(cand)
        if norm_c in norm_row and norm_row[norm_c] is not None:
            return norm_row[norm_c]
    return None


def _parse_import_date(val):
    if not val:
        return None
    if isinstance(val, (datetime, date)):
        return val.date() if isinstance(val, datetime) else val
    val_str = str(val).strip()
    if not val_str:
        return None
    if val_str.replace('.', '', 1).isdigit():
        try:
            num = float(val_str)
            if 30000 < num < 70000:
                return (datetime(1899, 12, 30) + timedelta(days=num)).date()
        except Exception:
            pass
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y', '%d.%m.%Y', '%Y/%m/%d', '%d %b %Y', '%d %B %Y'):
        try:
            return datetime.strptime(val_str, fmt).date()
        except ValueError:
            continue
    return None


def normalize_header(h):
    if h is None:
        return ''
    return re.sub(r'\s+', ' ', str(h).strip()).lower()


def parse_amount(val):
    """
    Parses money/amount values:
    - "2,900.00" -> 2900.00
    - "(203.79)" -> -203.79
    - "-", "", "—", None -> 0.00
    - "100.0%" -> 100.0
    - Numbers already numeric (int, float, Decimal) stay as-is.
    """
    if val is None:
        return Decimal('0.00')
    if isinstance(val, Decimal):
        return val
    if isinstance(val, (int, float)):
        return Decimal(str(val))

    s = str(val).strip()
    if not s or s in ('-', '—', '–', 'N/A', 'n/a', 'None', 'null'):
        return Decimal('0.00')

    if s.endswith('%'):
        s = s[:-1].strip()

    is_negative = False
    if s.startswith('(') and s.endswith(')'):
        is_negative = True
        s = s[1:-1].strip()
    elif s.startswith('-'):
        is_negative = True
        s = s[1:].strip()

    for token in (',', 'AED', 'aed', '$', '€', '£'):
        s = s.replace(token, '')
    s = s.strip()

    if not s:
        return Decimal('0.00')

    try:
        dec = Decimal(s)
        return -dec if is_negative else dec
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0.00')


def parse_sheet_date(val):
    """
    Parses dates:
    - Excel date cells (date, datetime)
    - "10-Jan-2026", "2026-01-10", "10/01/2026" (day first).
    - Invalid -> returns None.
    """
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()

    s = str(val).strip()
    if not s or s in ('-', '—', 'N/A', 'n/a', 'None'):
        return None

    formats = [
        '%d-%b-%Y', '%d-%b-%y',
        '%d-%B-%Y', '%d-%B-%y',
        '%Y-%m-%d',
        '%d/%m/%Y', '%d/%m/%y',
        '%d.%m.%Y', '%d.%m.%y',
        '%d %b %Y', '%d %b %y',
        '%d %B %Y', '%d %B %y',
        '%Y/%m/%d',
    ]

    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    return None


def _row_contains_required_headers(row_cells, sheet_type):
    if not sheet_type:
        return any(bool(c and str(c).strip()) for c in row_cells)

    norm_cells = [normalize_header(c).rstrip('.').strip() for c in row_cells if c is not None]

    def has_match(target):
        t = normalize_header(target).rstrip('.').strip()
        return any(t == c or t in c for c in norm_cells)

    if sheet_type == 'invoices':
        has_inv = has_match('invoice no')
        has_amt = has_match('amount without tax')
        return has_inv and has_amt
    elif sheet_type == 'jobs':
        has_inv = has_match('invoice no')
        has_job_col = has_match('engineer') or has_match('travel cost') or has_match('misc cost')
        return has_inv and has_job_col
    elif sheet_type == 'spares':
        has_ref = has_match('service ref')
        has_cost = has_match('purchase cost')
        has_selling = has_match('actual selling price') or has_match('selling price')
        return has_ref and has_cost and has_selling

    return True


def _get_row_val(row, *aliases):
    norm_map = {normalize_header(k): v for k, v in row.items()}
    for a in aliases:
        norm_a = normalize_header(a)
        if norm_a in norm_map and norm_map[norm_a] is not None:
            return norm_map[norm_a]
    return None


def parse_finance_file_rows(file_or_path, sheet_type=None, sheet_name=None):
    """
    Parses a CSV or Excel file and yields dicts of {header: value}.
    Scans the first 10 rows to detect the header row containing required headers for sheet_type.
    For workbooks, picks sheet_name if given, or the first worksheet whose header row matches.
    """
    is_path = isinstance(file_or_path, str)
    filename = file_or_path if is_path else getattr(file_or_path, 'name', '')
    is_csv = filename.lower().endswith('.csv')

    if not is_csv and not filename.lower().endswith(('.xlsx', '.xlsm', '.xltx', '.xltm')):
        if hasattr(file_or_path, 'read'):
            pos = file_or_path.tell() if hasattr(file_or_path, 'tell') else 0
            header_sample = file_or_path.read(4)
            if hasattr(file_or_path, 'seek'):
                file_or_path.seek(pos)
            if isinstance(header_sample, bytes):
                is_csv = not header_sample.startswith(b'PK')
            else:
                is_csv = not str(header_sample).startswith('PK')

    if is_csv:
        if is_path:
            with open(file_or_path, mode='r', encoding='utf-8-sig', errors='replace') as f:
                content = f.read()
        else:
            if hasattr(file_or_path, 'seek'):
                file_or_path.seek(0)
            raw = file_or_path.read()
            content = raw.decode('utf-8-sig', errors='replace') if isinstance(raw, bytes) else raw

        lines = content.splitlines()
        reader = csv.reader(lines)
        all_rows = list(reader)

        header_row_idx = 0
        headers = []
        for idx, row in enumerate(all_rows[:10]):
            if _row_contains_required_headers(row, sheet_type):
                header_row_idx = idx
                headers = [str(v).strip() for v in row]
                break

        if not headers and all_rows:
            headers = [str(v).strip() for v in all_rows[0]]
            header_row_idx = 0

        for row in all_rows[header_row_idx + 1:]:
            if not any(v and str(v).strip() for v in row):
                continue
            row_dict = {}
            for h, v in zip(headers, row):
                if h:
                    row_dict[h] = v
            yield row_dict
    else:
        if not is_path and hasattr(file_or_path, 'seek'):
            file_or_path.seek(0)
        wb = openpyxl.load_workbook(file_or_path, data_only=True)

        target_ws = None
        if sheet_name:
            if sheet_name in wb.sheetnames:
                target_ws = wb[sheet_name]
            else:
                for s in wb.sheetnames:
                    if s.strip().lower() == sheet_name.strip().lower():
                        target_ws = wb[s]
                        break

        if target_ws is None:
            # Pick first worksheet whose header row matches; never blindly use wb.active
            for ws in wb.worksheets:
                for r_idx in range(1, min(11, ws.max_row + 1)):
                    row_vals = [ws.cell(row=r_idx, column=c_idx).value for c_idx in range(1, ws.max_column + 1)]
                    if _row_contains_required_headers(row_vals, sheet_type):
                        target_ws = ws
                        break
                if target_ws is not None:
                    break

        if target_ws is None:
            target_ws = wb.worksheets[0]

        # Scan first 10 rows of target_ws to find the header row
        header_row_idx = 1
        headers = []
        for r_idx in range(1, min(11, target_ws.max_row + 1)):
            row_vals = [target_ws.cell(row=r_idx, column=c_idx).value for c_idx in range(1, target_ws.max_column + 1)]
            if _row_contains_required_headers(row_vals, sheet_type):
                header_row_idx = r_idx
                headers = [str(v).strip() if v is not None else '' for v in row_vals]
                break

        if not headers:
            headers = [str(cell.value).strip() if cell.value is not None else '' for cell in target_ws[1]]
            header_row_idx = 1

        for row in target_ws.iter_rows(min_row=header_row_idx + 1, values_only=True):
            if not any(v is not None and str(v).strip() for v in row):
                continue
            row_dict = {}
            for h, v in zip(headers, row):
                if h:
                    row_dict[h] = v
            yield row_dict


def resolve_import_currency_and_rate(
    row,
    curr_aliases,
    rate_aliases,
    row_date,
    row_idx,
    label="",
    existing_currency=None,
    existing_rate=None,
    record_label="",
):
    """
    Resolves currency and rate from row dictionary.
    Returns: (currency, rate, error_message, list_of_warnings)
    """
    warnings = []
    raw_curr = _get_row_val(row, *curr_aliases)
    has_curr = raw_curr is not None and bool(str(raw_curr).strip())

    if not has_curr:
        if existing_currency:
            curr = str(existing_currency).strip().upper()
            rate = Decimal(str(existing_rate)) if existing_rate is not None else Decimal('1.000000')
            return curr, rate, None, warnings
        else:
            curr = BASE_CURRENCY
    else:
        curr = str(raw_curr).strip().upper()

    multi_currency_enabled = getattr(settings, 'FINANCE_MULTI_CURRENCY', False)
    if curr != BASE_CURRENCY and not multi_currency_enabled:
        return None, None, f"Row {row_idx}: Multi-currency is not enabled.", []

    valid_codes = {code for code, _ in CURRENCY_CHOICES}
    if curr not in valid_codes:
        prefix = f"{label} " if label else ""
        return None, None, f"Row {row_idx}: Unknown {prefix}currency code '{curr}'.", []

    # Check for currency change on existing record
    if existing_currency and curr != str(existing_currency).strip().upper():
        target_name = f"{record_label} {label}".strip() if (record_label or label) else ""
        prefix = f" {target_name}" if target_name else ""
        warnings.append(f"Row {row_idx}:{prefix} currency changed {existing_currency} -> {curr}")

    if curr == BASE_CURRENCY:
        return BASE_CURRENCY, Decimal('1.000000'), None, warnings

    raw_rate = _get_row_val(row, *rate_aliases)
    if raw_rate is not None and str(raw_rate).strip():
        try:
            rate = Decimal(str(raw_rate).strip())
            if rate <= Decimal('0'):
                return None, None, f"Row {row_idx}: Rate must be greater than zero.", []
            return curr, rate, None, warnings
        except (InvalidOperation, ValueError, TypeError):
            return None, None, f"Row {row_idx}: Invalid exchange rate '{raw_rate}'.", []
    else:
        # Rate missing / empty:
        # If existing record with SAME currency, keep existing rate
        if existing_currency and curr == str(existing_currency).strip().upper() and existing_rate:
            rate = Decimal(str(existing_rate))
            if rate > Decimal('0'):
                return curr, rate, None, warnings

        # Auto-lookup get_rate(currency, row_date)
        found_rate = get_rate(curr, row_date)
        if found_rate is not None:
            rate_str = f"{found_rate}".rstrip('0').rstrip('.') if '.' in f"{found_rate}" else f"{found_rate}"
            warnings.append(f"{curr} rate {rate_str} taken from Exchange Rates for row {row_idx}")
            return curr, found_rate, None, warnings
        else:
            return None, None, f"Row {row_idx}: No exchange rate found for {curr} on {row_date}.", []


@transaction.atomic
def import_finance_sheets_data(
    invoices_file=None,
    jobs_file=None,
    spares_file=None,
    excel_file=None,
    invoices_sheet=None,
    jobs_sheet=None,
    spares_sheet=None,
    dry_run=False,
    user=None,
):
    """
    Imports Google Sheets data for Invoices, Jobs P&L, and Spares Accounting.
    Returns:
      {
        'created_invoices': int,
        'updated_invoices': int,
        'created_job_costs': int,
        'created_spares': int,
        'updated_spares': int,
        'skipped': int,
        'warnings': list of str,
        'errors': list of str,
        'preview': {'invoices': [...], 'jobs': [...], 'spares': [...]}
      }
    """
    if excel_file:
        if not invoices_file:
            invoices_file = excel_file
        if not jobs_file:
            jobs_file = excel_file
        if not spares_file:
            spares_file = excel_file

    res = {
        'created_invoices': 0,
        'updated_invoices': 0,
        'created_job_costs': 0,
        'created_spares': 0,
        'updated_spares': 0,
        'skipped': 0,
        'warnings': [],
        'errors': [],
        'preview': {
            'invoices': [],
            'jobs': [],
            'spares': [],
        }
    }

    # 1. PROCESS INVOICE COSTING
    if invoices_file:
        seen_invoices = set()
        try:
            row_idx = 0
            for row in parse_finance_file_rows(invoices_file, sheet_type='invoices', sheet_name=invoices_sheet):
                row_idx += 1
                raw_inv_no = _get_row_val(row, 'Invoice No', 'Invoice Number', 'Invoice', 'Inv No')
                inv_no = str(raw_inv_no).strip() if raw_inv_no else ''

                try:
                    with transaction.atomic():
                        if not inv_no:
                            res['skipped'] += 1
                            res['errors'].append(f"Row {row_idx} (<unknown>): Skipped due to missing Invoice No.")
                            continue

                        if inv_no in seen_invoices:
                            res['warnings'].append(f"Duplicate Invoice No '{inv_no}' found in file (Row {row_idx}).")
                        seen_invoices.add(inv_no)

                        if not re.match(r'^AMMS-INV-\d{2}-\d{4}(-R\d+)?$', inv_no, re.IGNORECASE):
                            res['warnings'].append(f"Invoice No '{inv_no}' does not match AMMS-INV-YY-NNNN format")

                        raw_date = _get_row_val(row, 'Invoice Date', 'Date', 'Inv Date')
                        inv_date = parse_sheet_date(raw_date)
                        if not inv_date:
                            res['skipped'] += 1
                            res['errors'].append(f"Row {row_idx} ({inv_no}): Invalid or missing invoice date '{raw_date}'.")
                            continue

                        service_ref = str(_get_row_val(row, 'Project / Service Ref', 'Project/Service Ref', 'Service Ref', 'Service Reference', 'Ref') or '').strip()
                        vessel = str(_get_row_val(row, 'VESSEL', 'Vessel', 'Vessel Name') or '').strip()
                        customer = str(_get_row_val(row, 'Customer', 'Client', 'Client Name') or '').strip()
                        amount_excl_vat = parse_amount(_get_row_val(row, 'Amount Without Tax', 'Amount Excl VAT', 'Amount (excl. tax)', 'Amount'))
                        credits_applied = parse_amount(_get_row_val(row, 'Credits Applied', 'Credits', 'Credit'))
                        collection_value = amount_excl_vat - credits_applied

                        project = None
                        if service_ref:
                            project = Project.objects.filter(service_reference__iexact=service_ref).first()
                        if project:
                            if not vessel:
                                vessel = project.vessel_name or ''
                            if not customer:
                                customer = project.client_name or ''

                        inv_type = 'SERVICE_JOB'
                        if 'COMM' in service_ref.upper():
                            inv_type = 'COMMUNICATION'
                        elif 'CALIBRAT' in service_ref.upper():
                            inv_type = 'CALIBRATION'

                        invoice_obj = Invoice.objects.filter(invoice_number__iexact=inv_no).first()
                        is_merged = False

                        if not invoice_obj and project:
                            legacy_candidates = Invoice.objects.filter(
                                project=project,
                                invoice_number__startswith='LEGACY-'
                            )
                            for cand in legacy_candidates:
                                if abs((cand.amount_excl_vat or Decimal('0.00')) - amount_excl_vat) <= Decimal('0.01'):
                                    invoice_obj = cand
                                    is_merged = True
                                    break

                        existing_currency = invoice_obj.currency if invoice_obj else None
                        existing_rate = invoice_obj.exchange_rate if invoice_obj else None

                        curr, rate, curr_err, curr_warns = resolve_import_currency_and_rate(
                            row,
                            ['Currency', 'Curr', 'Invoice Currency', 'Inv Currency'],
                            ['Rate', 'Exchange Rate', 'FX Rate', 'Invoice Rate', 'Inv Rate'],
                            inv_date,
                            row_idx,
                            existing_currency=existing_currency,
                            existing_rate=existing_rate,
                            record_label=inv_no,
                        )
                        if curr_err:
                            res['skipped'] += 1
                            res['errors'].append(f"Row {row_idx} ({inv_no}): {curr_err}" if f"Row {row_idx}" not in curr_err else curr_err)
                            continue
                        if curr_warns:
                            if isinstance(curr_warns, (list, tuple)):
                                res['warnings'].extend(curr_warns)
                            else:
                                res['warnings'].append(curr_warns)

                        raw_vat = _get_row_val(row, 'VAT', 'VAT Amount', 'Tax', 'Vat')
                        if raw_vat is not None and str(raw_vat).strip():
                            vat_val = parse_amount(raw_vat)
                            expected_vat = round(amount_excl_vat * Decimal('0.05'), 2)
                            if curr == BASE_CURRENCY:
                                if abs(vat_val - expected_vat) > Decimal('1.00'):
                                    res['warnings'].append(f"Row {row_idx} ({inv_no}): VAT discrepancy (found {vat_val:,.2f}, expected {expected_vat:,.2f} at 5%).")
                            else:
                                if vat_val > Decimal('0.00') and abs(vat_val - expected_vat) > Decimal('1.00'):
                                    res['warnings'].append(f"Row {row_idx} ({inv_no}): VAT discrepancy (found {vat_val:,.2f}, expected {expected_vat:,.2f} at 5%).")

                        status_raw = normalize_header(_get_row_val(row, 'Status')).strip()
                        is_cancelled = status_raw in ('cancelled', 'void')
                        is_paid = (status_raw == 'paid')
                        notes = str(_get_row_val(row, 'Notes', 'Note', 'Description') or '').strip()

                        if is_paid:
                            imported_amount_received = max(collection_value, Decimal('0.00'))
                        else:
                            imported_amount_received = Decimal('0.00')

                        if invoice_obj:
                            if is_merged:
                                old_legacy_num = invoice_obj.invoice_number
                                invoice_obj.invoice_number = inv_no
                                res['warnings'].append(f"Merged {old_legacy_num} into {inv_no}")

                            invoice_obj.invoice_date = inv_date
                            if project and not invoice_obj.project:
                                invoice_obj.project = project
                            if service_ref:
                                invoice_obj.service_ref = service_ref
                            if vessel:
                                invoice_obj.vessel = vessel
                            if customer:
                                invoice_obj.customer = customer
                            invoice_obj.currency = curr
                            invoice_obj.exchange_rate = rate
                            invoice_obj.amount_excl_vat = amount_excl_vat
                            invoice_obj.credits_applied = credits_applied
                            if is_paid:
                                invoice_obj.amount_received = max(invoice_obj.amount_received or Decimal('0.00'), imported_amount_received)
                            if is_cancelled:
                                invoice_obj.is_cancelled = True
                            if notes:
                                invoice_obj.notes = notes
                            invoice_obj.save()
                            res['updated_invoices'] += 1
                            action = 'Updated'
                        else:
                            invoice_obj, created = Invoice.objects.get_or_create(
                                invoice_number=inv_no,
                                defaults={
                                    'invoice_date': inv_date,
                                    'due_date': inv_date + timedelta(days=30),
                                    'project': project,
                                    'service_ref': service_ref,
                                    'vessel': vessel,
                                    'customer': customer,
                                    'invoice_type': inv_type,
                                    'currency': curr,
                                    'exchange_rate': rate,
                                    'amount_excl_vat': amount_excl_vat,
                                    'credits_applied': credits_applied,
                                    'amount_received': imported_amount_received,
                                    'is_cancelled': is_cancelled,
                                    'notes': notes,
                                    'created_by': user,
                                }
                            )
                            if created:
                                res['created_invoices'] += 1
                                action = 'Created'
                            else:
                                invoice_obj.invoice_date = inv_date
                                if project and not invoice_obj.project:
                                    invoice_obj.project = project
                                if service_ref:
                                    invoice_obj.service_ref = service_ref
                                if vessel:
                                    invoice_obj.vessel = vessel
                                if customer:
                                    invoice_obj.customer = customer
                                invoice_obj.currency = curr
                                invoice_obj.exchange_rate = rate
                                invoice_obj.amount_excl_vat = amount_excl_vat
                                invoice_obj.credits_applied = credits_applied
                                if is_paid:
                                    invoice_obj.amount_received = max(invoice_obj.amount_received or Decimal('0.00'), imported_amount_received)
                                if is_cancelled:
                                    invoice_obj.is_cancelled = True
                                if notes:
                                    invoice_obj.notes = notes
                                invoice_obj.save()
                                res['updated_invoices'] += 1
                                action = 'Updated'

                        res['preview']['invoices'].append({
                            'action': action,
                            'invoice_number': inv_no,
                            'invoice_date': inv_date,
                            'service_ref': service_ref,
                            'vessel': vessel,
                            'customer': customer,
                            'currency': curr,
                            'exchange_rate': rate,
                            'amount_excl_vat': amount_excl_vat,
                            'amount_aed': invoice_obj.amount_excl_vat_aed,
                            'credits_applied': credits_applied,
                            'status': invoice_obj.status,
                        })
                except Exception as e:
                    res['skipped'] += 1
                    res['errors'].append(f"Row {row_idx} ({inv_no or 'unknown'}): {str(e)}")
        except Exception as e:
            res['errors'].append(f"Invoices file error: {str(e)}")

    # 2. PROCESS JOBS P&L
    if jobs_file:
        seen_invoices = set()
        try:
            row_idx = 0
            for row in parse_finance_file_rows(jobs_file, sheet_type='jobs', sheet_name=jobs_sheet):
                row_idx += 1
                raw_inv_no = _get_row_val(row, 'Invoice No', 'Invoice Number', 'Invoice', 'Inv No')
                inv_no = str(raw_inv_no).strip() if raw_inv_no else ''

                try:
                    with transaction.atomic():
                        if not inv_no:
                            res['skipped'] += 1
                            res['errors'].append(f"Row {row_idx} (<unknown>): Skipped due to missing Invoice No.")
                            continue

                        if inv_no in seen_invoices:
                            res['warnings'].append(f"Duplicate Invoice No '{inv_no}' found in file (Row {row_idx}).")
                        seen_invoices.add(inv_no)

                        service_ref = str(_get_row_val(row, 'Service Ref', 'Project / Service Ref', 'Service Reference', 'Ref') or '').strip()
                        vessel = str(_get_row_val(row, 'Vessel', 'Vessel Name') or '').strip()
                        inv_amount = parse_amount(_get_row_val(row, 'Invoice', 'Invoice Amount', 'Amount'))
                        travel_port = str(_get_row_val(row, 'Travel Port', 'Port') or '').strip()
                        travel_cost = parse_amount(_get_row_val(row, 'Travel Cost', 'Travel'))
                        flight = parse_amount(_get_row_val(row, 'Flight', 'Flight Cost'))
                        misc = parse_amount(_get_row_val(row, 'Misc Cost', 'Misc'))
                        other = parse_amount(_get_row_val(row, 'Other', 'Other Cost'))
                        notes = str(_get_row_val(row, 'Notes', 'Note') or '').strip()

                        invoice_obj = Invoice.objects.filter(invoice_number__iexact=inv_no).first()
                        if not invoice_obj:
                            res['skipped'] += 1
                            res['errors'].append(f"Invoice {inv_no} not found — import Invoice Costing first")
                            res['warnings'].append(f"Invoice not found: {inv_no} — import Invoice Costing first")
                            continue

                        curr, rate, curr_err, curr_warns = resolve_import_currency_and_rate(
                            row,
                            ['Currency', 'Curr', 'Invoice Currency', 'Inv Currency'],
                            ['Rate', 'Exchange Rate', 'FX Rate', 'Invoice Rate', 'Inv Rate'],
                            invoice_obj.invoice_date,
                            row_idx
                        )
                        if curr_err:
                            res['skipped'] += 1
                            res['errors'].append(f"Row {row_idx} ({inv_no}): {curr_err}" if f"Row {row_idx}" not in curr_err else curr_err)
                            continue
                        if curr_warns:
                            if isinstance(curr_warns, (list, tuple)):
                                res['warnings'].extend(curr_warns)
                            else:
                                res['warnings'].append(curr_warns)

                        if service_ref and not invoice_obj.service_ref:
                            invoice_obj.service_ref = service_ref
                        if vessel and not invoice_obj.vessel:
                            invoice_obj.vessel = vessel
                        if notes and not invoice_obj.notes:
                            invoice_obj.notes = notes
                        invoice_obj.save()

                        # Check Amount mismatch warning against invoice collection value
                        inv_col_val = invoice_obj.collection_value
                        if inv_amount > Decimal('0.00') and abs(inv_amount - inv_col_val) > Decimal('0.01'):
                            vat_note = ""
                            if inv_col_val > Decimal('0.00') and abs((inv_amount / inv_col_val) - Decimal('1.05')) < Decimal('0.015'):
                                vat_note = " (looks like VAT 5%)"
                            elif inv_amount > Decimal('0.00') and abs((inv_col_val / inv_amount) - Decimal('1.05')) < Decimal('0.015'):
                                vat_note = " (looks like VAT 5%)"
                            res['warnings'].append(f"Amount mismatch {inv_no}: Jobs {inv_amount:,.2f} vs Invoice {inv_col_val:,.2f}{vat_note}")

                        # Delete ONLY IMPORT job costs for this invoice (preserving MANUAL and LEGACY)
                        invoice_obj.job_costs.filter(source='IMPORT').delete()

                        costs_created = []
                        if travel_cost > Decimal('0.00'):
                            JobCost.objects.create(
                                invoice=invoice_obj,
                                cost_type='TRAVEL',
                                amount=travel_cost,
                                currency=curr,
                                exchange_rate=rate,
                                travel_port=travel_port,
                                date=invoice_obj.invoice_date,
                                source='IMPORT',
                                created_by=user,
                            )
                            res['created_job_costs'] += 1
                            costs_created.append(f"Travel: {travel_cost}")

                        if flight > Decimal('0.00'):
                            JobCost.objects.create(
                                invoice=invoice_obj,
                                cost_type='FLIGHT',
                                amount=flight,
                                currency=curr,
                                exchange_rate=rate,
                                date=invoice_obj.invoice_date,
                                source='IMPORT',
                                created_by=user,
                            )
                            res['created_job_costs'] += 1
                            costs_created.append(f"Flight: {flight}")

                        if misc > Decimal('0.00'):
                            JobCost.objects.create(
                                invoice=invoice_obj,
                                cost_type='MISC',
                                amount=misc,
                                currency=curr,
                                exchange_rate=rate,
                                date=invoice_obj.invoice_date,
                                source='IMPORT',
                                created_by=user,
                            )
                            res['created_job_costs'] += 1
                            costs_created.append(f"Misc: {misc}")

                        if other > Decimal('0.00'):
                            JobCost.objects.create(
                                invoice=invoice_obj,
                                cost_type='OTHER',
                                amount=other,
                                currency=curr,
                                exchange_rate=rate,
                                description='Other costs',
                                date=invoice_obj.invoice_date,
                                source='IMPORT',
                                created_by=user,
                            )
                            res['created_job_costs'] += 1
                            costs_created.append(f"Other: {other}")

                        res['preview']['jobs'].append({
                            'invoice_number': inv_no,
                            'service_ref': service_ref or invoice_obj.service_ref,
                            'vessel': vessel or invoice_obj.vessel,
                            'costs_created': ', '.join(costs_created) or 'None',
                            'travel_port': travel_port,
                        })
                except Exception as e:
                    res['skipped'] += 1
                    res['errors'].append(f"Row {row_idx} ({inv_no or 'unknown'}): {str(e)}")
        except Exception as e:
            res['errors'].append(f"Jobs P&L file error: {str(e)}")

    # 3. PROCESS SPARES ACCOUNTING
    if spares_file:
        seen_invoices = set()
        try:
            row_idx = 0
            for row in parse_finance_file_rows(spares_file, sheet_type='spares', sheet_name=spares_sheet):
                row_idx += 1
                service_ref = str(_get_row_val(row, 'Service Ref', 'Service Reference', 'Ref') or '').strip()
                vessel_customer = str(_get_row_val(row, 'Vessel / Customer', 'Vessel/Customer', 'Vessel', 'Customer') or '').strip()
                source_raw = str(_get_row_val(row, 'Source') or '').strip().upper()
                source = 'SPARES_ONLY' if ('SPARE' in source_raw or 'ONLY' in source_raw) else 'SERVICE_JOB'
                purchase_cost = parse_amount(_get_row_val(row, 'Purchase Cost', 'Cost', 'Purchase'))
                selling_price = parse_amount(_get_row_val(row, 'Actual Selling Price', 'Selling Price', 'Selling', 'Price'))
                raw_inv_no = str(_get_row_val(row, 'Invoice No', 'Invoice Number', 'Invoice', 'Inv No') or '').strip()
                status_raw = str(_get_row_val(row, 'Status') or '').strip().upper()
                notes = str(_get_row_val(row, 'Notes', 'Note') or '').strip()

                try:
                    with transaction.atomic():
                        if not service_ref and not vessel_customer and not raw_inv_no:
                            res['skipped'] += 1
                            res['errors'].append(f"Row {row_idx} (<unknown>): Skipped due to lack of identifiers.")
                            continue

                        if raw_inv_no:
                            if raw_inv_no in seen_invoices:
                                res['warnings'].append(f"Duplicate Invoice No '{raw_inv_no}' found in file (Row {row_idx}).")
                            seen_invoices.add(raw_inv_no)

                        invoice_obj = None
                        if raw_inv_no:
                            invoice_obj = Invoice.objects.filter(invoice_number__iexact=raw_inv_no).first()
                            if not invoice_obj:
                                res['warnings'].append(f"Spares Invoice No '{raw_inv_no}' not found (Row {row_idx}, Ref: {service_ref}).")

                        if invoice_obj and source == 'SPARES_ONLY':
                            if invoice_obj.invoice_type == 'SERVICE_JOB':
                                invoice_obj.invoice_type = 'SPARES_ONLY'
                                invoice_obj.save(update_fields=['invoice_type'])

                        project = None
                        if service_ref:
                            project = Project.objects.filter(service_reference__iexact=service_ref).first()
                        if not project and invoice_obj and invoice_obj.project:
                            project = invoice_obj.project

                        status = 'QUOTED'
                        if invoice_obj:
                            status = 'INVOICED'
                        elif status_raw in ('QUOTED', 'ORDERED', 'DELIVERED', 'INVOICED'):
                            status = status_raw

                        spare_obj = None
                        if invoice_obj and service_ref:
                            spare_obj = SpareSale.objects.filter(invoice=invoice_obj, service_ref=service_ref).first()
                        elif service_ref:
                            spare_obj = SpareSale.objects.filter(service_ref=service_ref, purchase_cost=purchase_cost, selling_price=selling_price).first()
                        if not spare_obj and invoice_obj and invoice_obj.spare_sales.count() == 1:
                            spare_obj = invoice_obj.spare_sales.first()

                        existing_p_curr = spare_obj.purchase_currency if spare_obj else None
                        existing_p_rate = spare_obj.purchase_rate if spare_obj else None
                        existing_s_curr = spare_obj.selling_currency if spare_obj else None
                        existing_s_rate = spare_obj.selling_rate if spare_obj else None

                        sp_date = invoice_obj.invoice_date if invoice_obj and invoice_obj.invoice_date else timezone.localdate()
                        sp_label = raw_inv_no or service_ref or 'Spare'

                        p_curr, p_rate, p_err, p_warns = resolve_import_currency_and_rate(
                            row,
                            ['Purchase Currency', 'Purchase Curr', 'Cost Currency', 'Currency', 'Curr'],
                            ['Purchase Rate', 'Purchase Exchange Rate', 'Cost Rate', 'Rate', 'Exchange Rate', 'FX Rate'],
                            sp_date,
                            row_idx,
                            label="purchase",
                            existing_currency=existing_p_curr,
                            existing_rate=existing_p_rate,
                            record_label=sp_label,
                        )
                        if p_err:
                            res['skipped'] += 1
                            res['errors'].append(f"Row {row_idx} ({raw_inv_no or service_ref or 'unknown'}): {p_err}" if f"Row {row_idx}" not in p_err else p_err)
                            continue
                        if p_warns:
                            if isinstance(p_warns, (list, tuple)):
                                res['warnings'].extend(p_warns)
                            else:
                                res['warnings'].append(p_warns)

                        s_curr, s_rate, s_err, s_warns = resolve_import_currency_and_rate(
                            row,
                            ['Selling Currency', 'Selling Curr', 'Sell Currency', 'Currency', 'Curr'],
                            ['Selling Rate', 'Selling Exchange Rate', 'Sell Rate', 'Rate', 'Exchange Rate', 'FX Rate'],
                            sp_date,
                            row_idx,
                            label="selling",
                            existing_currency=existing_s_curr,
                            existing_rate=existing_s_rate,
                            record_label=sp_label,
                        )
                        if s_err:
                            res['skipped'] += 1
                            res['errors'].append(f"Row {row_idx} ({raw_inv_no or service_ref or 'unknown'}): {s_err}" if f"Row {row_idx}" not in s_err else s_err)
                            continue
                        if s_warns:
                            if isinstance(s_warns, (list, tuple)):
                                res['warnings'].extend(s_warns)
                            else:
                                res['warnings'].append(s_warns)

                        if spare_obj:
                            spare_obj.purchase_cost = purchase_cost
                            spare_obj.purchase_currency = p_curr
                            spare_obj.purchase_rate = p_rate
                            spare_obj.selling_price = selling_price
                            spare_obj.selling_currency = s_curr
                            spare_obj.selling_rate = s_rate
                            spare_obj.source = source
                            if vessel_customer:
                                spare_obj.vessel_customer = vessel_customer
                            if invoice_obj:
                                spare_obj.invoice = invoice_obj
                                spare_obj.status = 'INVOICED'
                            elif status:
                                spare_obj.status = status
                            if project:
                                spare_obj.project = project
                            if notes:
                                spare_obj.notes = notes
                            spare_obj.save()
                            res['updated_spares'] += 1
                            action = 'Updated'
                        else:
                            spare_obj = SpareSale.objects.create(
                                service_ref=service_ref,
                                vessel_customer=vessel_customer,
                                project=project,
                                invoice=invoice_obj,
                                source=source,
                                purchase_cost=purchase_cost,
                                purchase_currency=p_curr,
                                purchase_rate=p_rate,
                                selling_price=selling_price,
                                selling_currency=s_curr,
                                selling_rate=s_rate,
                                status=status,
                                notes=notes,
                                created_by=user,
                            )
                            res['created_spares'] += 1
                            action = 'Created'

                        res['preview']['spares'].append({
                            'action': action,
                            'service_ref': service_ref,
                            'vessel_customer': vessel_customer,
                            'purchase_currency': p_curr,
                            'purchase_rate': p_rate,
                            'purchase_cost': purchase_cost,
                            'purchase_cost_aed': spare_obj.purchase_cost_aed,
                            'selling_currency': s_curr,
                            'selling_rate': s_rate,
                            'selling_price': selling_price,
                            'selling_price_aed': spare_obj.selling_price_aed,
                            'profit': spare_obj.profit,
                            'markup_pct': spare_obj.markup_pct,
                            'invoice_number': raw_inv_no,
                            'status': spare_obj.status,
                            'alerts': spare_obj.alerts,
                        })
                except Exception as e:
                    res['skipped'] += 1
                    res['errors'].append(f"Row {row_idx} ({raw_inv_no or service_ref or 'unknown'}): {str(e)}")
        except Exception as e:
            res['errors'].append(f"Spares file error: {str(e)}")

    if dry_run:
        transaction.set_rollback(True)

    return res


# =====================================================================
# PHASE B: INVOICE COSTING, JOBS P&L, SPARES ACCOUNTING SERVICES
# =====================================================================

def get_next_invoice_number(target_year=None):
    """
    Generates the next company invoice number:
    AMMS-INV-<current 2-digit year>-<max existing 4-digit sequence + 1, zero-padded>
    e.g. AMMS-INV-26-0084
    Ignores -R suffixes and LEGACY-* numbers.
    """
    if target_year is None:
        target_year = timezone.localdate().year
    year_suffix = f"{int(target_year) % 100:02d}"
    prefix = f"AMMS-INV-{year_suffix}-"

    invoices = Invoice.objects.filter(invoice_number__istartswith=prefix).values_list('invoice_number', flat=True)
    max_seq = 0
    pattern = re.compile(rf'^{re.escape(prefix)}(\d{{4}})(?:-R\d+)?$', re.IGNORECASE)
    for num in invoices:
        m = pattern.match((num or '').strip())
        if m:
            seq = int(m.group(1))
            if seq > max_seq:
                max_seq = seq

    next_seq = max_seq + 1
    return f"{prefix}{next_seq:04d}"


def engineer_cost_for_projects(project_ids):
    """
    Computes total engineer hours and cost for each project in project_ids.
    Rule: service logs (normal + OT hours) plus attendance on the project,
    x salary/208, the same rule get_project_financials uses.
    An Attendance row is counted only when the same employee has NO EmployeeServiceLog
    on that project for the same date.
    Bulk-loads in a fixed number of queries (1 for EmployeeServiceLog, 1 for Attendance).
    Returns: {project_id: {'hours': Decimal, 'cost': Decimal}}
    """
    if not project_ids:
        return {}

    pids = list({p.id if hasattr(p, 'id') else p for p in project_ids if p is not None})
    if not pids:
        return {}

    result = {pid: {'hours': Decimal('0.00'), 'cost': Decimal('0.00')} for pid in pids}

    # Query 1: EmployeeServiceLog
    logs = (
        EmployeeServiceLog.objects.counting().filter(project_id__in=pids)
        .values('project_id', 'employee_id', 'date', 'total_hours', 'normal_cost', 'ot_cost')
    )
    logged_emp_proj_dates = set()
    for l in logs:
        pid = l['project_id']
        if pid in result:
            h = Decimal(str(l['total_hours'] or 0))
            c = (l['normal_cost'] or Decimal('0.00')) + (l['ot_cost'] or Decimal('0.00'))
            result[pid]['hours'] += h
            result[pid]['cost'] += c
            if l['employee_id'] and l['date']:
                logged_emp_proj_dates.add((l['employee_id'], pid, l['date']))

    # Query 2: Attendance
    att_records = (
        Attendance.objects.filter(project_id__in=pids)
        .values('project_id', 'employee_id', 'login_time', 'total_hours_of_work', 'employee__salary')
    )
    for a in att_records:
        pid = a['project_id']
        if pid in result:
            login_t = a['login_time']
            if login_t:
                att_date = timezone.localtime(login_t).date() if timezone.is_aware(login_t) else login_t.date()
            else:
                att_date = None

            # Count an Attendance row only when the same employee has NO EmployeeServiceLog on that project for the same date
            if att_date and (a['employee_id'], pid, att_date) in logged_emp_proj_dates:
                continue

            h = Decimal(str(a['total_hours_of_work'] or 0))
            sal = a['employee__salary'] or Decimal('0.00')
            hourly = sal / Decimal('208.00') if sal > Decimal('0.00') else Decimal('0.00')
            c = hourly * h
            result[pid]['hours'] += h
            result[pid]['cost'] += c

    for pid in result:
        result[pid]['hours'] = round(result[pid]['hours'], 2)
        result[pid]['cost'] = round(result[pid]['cost'], 2)

    return result


def get_projects_financials_map(projects_or_pids, eng_costs_map=None):
    """
    Bulk computes shared project financials:
    - invoiced_amount: sum of collection_value of the project's non-cancelled invoices
      (fallback to project.invoice_amount only if the project has no invoices; label "(legacy)")
    - employee_hours, employee_cost: service logs normal+OT cost + attendance on the project * salary/208
    - spares_cost: sum of SpareSale.purchase_cost linked to the project's invoices or to the project
    - spares_revenue: sum of SpareSale.selling_price (same rows)
    - job_costs: dict of JobCost of the project's invoices by type (travel, flight, misc, other, legacy)
    - other_job_costs: sum of all job costs
    - total_cost: employee_cost + spares_cost + other_job_costs
    - profit: invoiced_amount - total_cost
    - profit_pct: (profit / invoiced_amount * 100) if invoiced_amount else 0.0
    Returns: dict {project_id: financials_dict}
    """
    if not projects_or_pids:
        return {}

    pids = list({p.id if hasattr(p, 'id') else p for p in projects_or_pids if p is not None})
    if not pids:
        return {}

    proj_objs = {}
    for p in projects_or_pids:
        if hasattr(p, 'id'):
            proj_objs[p.id] = p

    missing_pids = [pid for pid in pids if pid not in proj_objs]
    if missing_pids:
        for p in Project.objects.filter(id__in=missing_pids):
            proj_objs[p.id] = p

    db_invoices = list(Invoice.objects.filter(project_id__in=pids, is_cancelled=False))
    invs_by_proj = defaultdict(list)
    for inv in db_invoices:
        invs_by_proj[inv.project_id].append(inv)

    if eng_costs_map is not None:
        eng_costs = eng_costs_map
    else:
        eng_costs = engineer_cost_for_projects(pids)

    spares = list(
        SpareSale.objects.filter(Q(project_id__in=pids) | Q(invoice__project_id__in=pids))
        .values('id', 'project_id', 'invoice__project_id', 'purchase_cost_aed', 'selling_price_aed')
    )
    spares_by_proj = defaultdict(lambda: {'cost': Decimal('0.00'), 'revenue': Decimal('0.00'), 'seen_ids': set()})
    for sp in spares:
        sp_id = sp['id']
        pc = sp['purchase_cost_aed'] or Decimal('0.00')
        sp_rev = sp['selling_price_aed'] or Decimal('0.00')
        target_pids = set()
        if sp['project_id'] in pids:
            target_pids.add(sp['project_id'])
        if sp['invoice__project_id'] in pids:
            target_pids.add(sp['invoice__project_id'])
        for t_pid in target_pids:
            if sp_id not in spares_by_proj[t_pid]['seen_ids']:
                spares_by_proj[t_pid]['seen_ids'].add(sp_id)
                spares_by_proj[t_pid]['cost'] += pc
                spares_by_proj[t_pid]['revenue'] += sp_rev

    job_costs_list = list(
        JobCost.objects.filter(invoice__project_id__in=pids)
        .values('id', 'invoice__project_id', 'cost_type', 'source', 'amount_aed', 'travel_port')
    )
    costs_by_proj = defaultdict(lambda: {
        'travel': Decimal('0.00'),
        'flight': Decimal('0.00'),
        'misc': Decimal('0.00'),
        'other': Decimal('0.00'),
        'legacy': Decimal('0.00'),
        'has_any_cost': False,
        'travel_ports': []
    })
    for c in job_costs_list:
        pid = c['invoice__project_id']
        amt = c['amount_aed'] or Decimal('0.00')
        ctype = c['cost_type']
        csrc = c['source']
        entry = costs_by_proj[pid]
        entry['has_any_cost'] = True
        if ctype == 'TRAVEL':
            entry['travel'] += amt
            port = (c.get('travel_port') or '').strip()
            if port and port not in entry['travel_ports']:
                entry['travel_ports'].append(port)
        elif ctype == 'FLIGHT':
            entry['flight'] += amt
        elif ctype == 'MISC':
            entry['misc'] += amt
        elif ctype == 'OTHER' and csrc != 'LEGACY':
            entry['other'] += amt
        if csrc == 'LEGACY':
            entry['legacy'] += amt

    result = {}
    for pid in pids:
        proj = proj_objs.get(pid)
        p_invs = invs_by_proj.get(pid, [])
        if p_invs:
            invoiced_amount = sum((inv.collection_value_aed for inv in p_invs), Decimal('0.00'))
            is_legacy_invoice = False
        else:
            invoiced_amount = (proj.invoice_amount or Decimal('0.00')) if proj else Decimal('0.00')
            is_legacy_invoice = True

        emp_info = eng_costs.get(pid, {'hours': Decimal('0.00'), 'cost': Decimal('0.00')})
        employee_hours = emp_info['hours']
        employee_cost = emp_info['cost']

        sp_info = spares_by_proj.get(pid, {'cost': Decimal('0.00'), 'revenue': Decimal('0.00')})
        spares_cost = sp_info['cost']
        spares_revenue = sp_info['revenue']

        c_info = costs_by_proj.get(pid, {
            'travel': Decimal('0.00'),
            'flight': Decimal('0.00'),
            'misc': Decimal('0.00'),
            'other': Decimal('0.00'),
            'legacy': Decimal('0.00'),
            'has_any_cost': False,
            'travel_ports': []
        })
        job_costs = {
            'travel': c_info['travel'],
            'flight': c_info['flight'],
            'misc': c_info['misc'],
            'other': c_info['other'],
            'legacy': c_info['legacy'],
        }
        if not p_invs and not c_info['has_any_cost'] and proj and (proj.purchase_and_expenses or Decimal('0.00')) > Decimal('0.00'):
            job_costs['legacy'] = proj.purchase_and_expenses or Decimal('0.00')

        job_costs['total'] = (
            job_costs['travel'] + job_costs['flight'] + job_costs['misc'] + job_costs['other'] + job_costs['legacy']
        )
        other_job_costs = job_costs['total']

        total_cost = employee_cost + spares_cost + other_job_costs
        profit = invoiced_amount - total_cost
        if invoiced_amount != Decimal('0.00'):
            profit_pct = round((profit / invoiced_amount) * Decimal('100.00'), 1)
        else:
            profit_pct = Decimal('0.0')

        result[pid] = {
            'invoiced_amount': invoiced_amount,
            'is_legacy_invoice': is_legacy_invoice,
            'employee_hours': employee_hours,
            'employee_cost': employee_cost,
            'spares_cost': spares_cost,
            'spares_revenue': spares_revenue,
            'job_costs': job_costs,
            'other_job_costs': other_job_costs,
            'total_cost': total_cost,
            'profit': profit,
            'profit_pct': profit_pct,
            'invoice_count': len(p_invs),
            'travel_ports': c_info.get('travel_ports', []),
        }

    return result


def get_project_financials(project):
    """
    Computes shared project financials:
    - invoiced_amount: sum of collection_value of the project's non-cancelled invoices
      (fallback to project.invoice_amount only if the project has no invoices; label "(legacy)")
    - employee_hours, employee_cost: service logs normal+OT cost + attendance on the project * salary/208
    - spares_cost: sum of SpareSale.purchase_cost linked to the project's invoices or to the project
    - spares_revenue: sum of SpareSale.selling_price (same rows)
    - job_costs: dict of JobCost of the project's invoices by type (travel, flight, misc, other, legacy)
    - other_job_costs: sum of all job costs
    - total_cost: employee_cost + spares_cost + other_job_costs
    - profit: invoiced_amount - total_cost
    - profit_pct: (profit / invoiced_amount * 100) if invoiced_amount else 0.0
    """
    if not project:
        return {}
    res = get_projects_financials_map([project])
    return res.get(project.id, {})


def compute_invoice_engineer_costs(invoices, eng_costs_map=None):
    """
    Computes engineer cost attributed to each invoice using engineer_cost_for_projects.
    If a project has multiple invoices, engineer cost is allocated across them
    weighted by invoice collection value / amount_excl_vat using the
    largest-remainder method in cents so the sum is exact down to the cent.
    Returns: dict {invoice.id: Decimal('cost')}
    """
    if not invoices:
        return {}

    inv_list = list(invoices)
    project_ids = list({inv.project_id for inv in inv_list if inv.project_id})
    if not project_ids:
        return {inv.id: Decimal('0.00') for inv in inv_list}

    if eng_costs_map is not None:
        proj_eng_costs = eng_costs_map
    else:
        proj_eng_costs = engineer_cost_for_projects(project_ids)

    db_invs = list(Invoice.objects.filter(project_id__in=project_ids, is_cancelled=False))
    db_invs_by_proj = defaultdict(list)
    for d in db_invs:
        db_invs_by_proj[d.project_id].append(d)

    cost_by_inv_id = {inv.id: Decimal('0.00') for inv in inv_list}

    for pid in project_ids:
        total_proj_eng_cost = proj_eng_costs.get(pid, {}).get('cost', Decimal('0.00'))
        if total_proj_eng_cost <= Decimal('0.00'):
            continue

        all_target_invs = db_invs_by_proj[pid]
        if not all_target_invs:
            continue

        if len(all_target_invs) == 1:
            inv_obj = all_target_invs[0]
            if inv_obj.id in cost_by_inv_id:
                cost_by_inv_id[inv_obj.id] = total_proj_eng_cost
            continue

        total_cents = int(round(total_proj_eng_cost * 100))
        weights = {}
        for target_inv in all_target_invs:
            w = target_inv.collection_value_aed if target_inv.collection_value_aed > Decimal('0.00') else (target_inv.amount_excl_vat_aed or Decimal('0.00'))
            weights[target_inv.id] = max(w, Decimal('0.00'))

        sum_w = sum(weights.values())
        if sum_w <= Decimal('0.00'):
            n = len(all_target_invs)
            base = total_cents // n
            rem = total_cents % n
            for idx, target_inv in enumerate(all_target_invs):
                allocated_cents = base + (1 if idx < rem else 0)
                if target_inv.id in cost_by_inv_id:
                    cost_by_inv_id[target_inv.id] = Decimal(allocated_cents) / Decimal('100.00')
        else:
            raw_shares = {iid: (weights[iid] / sum_w) * total_cents for iid in weights}
            floor_shares = {iid: int(math.floor(raw_shares[iid])) for iid in weights}
            rem = total_cents - sum(floor_shares.values())

            sorted_iids = sorted(
                weights.keys(),
                key=lambda iid: (raw_shares[iid] - floor_shares[iid], weights[iid], iid),
                reverse=True
            )
            for i in range(rem):
                floor_shares[sorted_iids[i]] += 1

            for iid, cents in floor_shares.items():
                if iid in cost_by_inv_id:
                    cost_by_inv_id[iid] = Decimal(cents) / Decimal('100.00')

    return cost_by_inv_id


def get_invoice_costing_data(filters=None, sort_by=None):
    """
    Returns (rows, totals) for Invoice Costing page.
    Columns: Status pill, Invoice Date, Project / Service Ref, Vessel, Invoice No,
    Amount (excl. tax), Credits, Spares Revenue, Spare Cost, Spares Profit, Other Cost,
    Total Net Profit, Collection Value, Over Due.
    """
    filters = filters or {}
    qs = Invoice.objects.all().select_related('project').prefetch_related('job_costs', 'spare_sales')

    start_date = filters.get('start_date')
    end_date = filters.get('end_date')
    month = filters.get('month')
    if month:
        if isinstance(month, str):
            try:
                parts = month.split('-')
                m_date = date(int(parts[0]), int(parts[1]), 1)
                f_day, l_day, _ = get_month_range(m_date)
                qs = qs.filter(invoice_date__range=(f_day, l_day))
            except (ValueError, IndexError):
                pass
        elif isinstance(month, date):
            f_day, l_day, _ = get_month_range(month)
            qs = qs.filter(invoice_date__range=(f_day, l_day))
    else:
        if start_date:
            qs = qs.filter(invoice_date__gte=start_date)
        if end_date:
            qs = qs.filter(invoice_date__lte=end_date)

    if filters.get('invoice_type') and filters['invoice_type'] != 'all':
        qs = qs.filter(invoice_type=filters['invoice_type'])

    if filters.get('search'):
        s = filters['search'].strip()
        qs = qs.filter(
            Q(invoice_number__icontains=s) |
            Q(service_ref__icontains=s) |
            Q(vessel__icontains=s) |
            Q(customer__icontains=s) |
            Q(project__name__icontains=s) |
            Q(project__client_name__icontains=s)
        )

    invoices = list(qs)

    status_filter = filters.get('status')
    if status_filter and status_filter != 'all':
        invoices = [inv for inv in invoices if inv.status.upper() == status_filter.upper()]

    eng_cost_map = compute_invoice_engineer_costs(invoices)

    rows = []
    for inv in invoices:
        amount_excl_tax = inv.amount_excl_vat or Decimal('0.00')
        amount_excl_tax_aed = inv.amount_excl_vat_aed or Decimal('0.00')
        credits = inv.credits_applied or Decimal('0.00')
        credits_aed = inv.credits_applied_aed or Decimal('0.00')
        coll_val = inv.collection_value
        coll_val_aed = inv.collection_value_aed

        spares = list(inv.spare_sales.all())
        spares_rev = sum((s.selling_price_aed or Decimal('0.00')) for s in spares)
        spare_cost = sum((s.purchase_cost_aed or Decimal('0.00')) for s in spares)
        spares_profit = spares_rev - spare_cost

        costs = list(inv.job_costs.all())
        other_cost = sum((c.amount_aed or Decimal('0.00')) for c in costs if c.cost_type in ('TRAVEL', 'FLIGHT', 'MISC', 'OTHER'))
        engineer_cost = eng_cost_map.get(inv.id, Decimal('0.00'))

        total_cost = engineer_cost + spare_cost + other_cost
        total_net_profit = coll_val_aed - total_cost

        over_due = inv.outstanding if inv.status == 'OVERDUE' else Decimal('0.00')
        over_due_aed = inv.outstanding_aed if inv.status == 'OVERDUE' else Decimal('0.00')
        is_loss = total_net_profit < Decimal('0.00')

        profit_disp = f"({abs(total_net_profit):,.2f})" if is_loss else f"{total_net_profit:,.2f}"

        rows.append({
            'invoice': inv,
            'id': inv.id,
            'status': inv.status,
            'invoice_date': inv.invoice_date,
            'due_date': inv.due_date,
            'service_ref': inv.service_ref or (inv.project.service_reference if inv.project else ''),
            'project': inv.project,
            'vessel': inv.vessel or (inv.project.vessel_name if inv.project else ''),
            'customer': inv.customer or (inv.project.client_name if inv.project else ''),
            'invoice_number': inv.invoice_number,
            'invoice_type': inv.invoice_type,
            'currency': inv.currency,
            'exchange_rate': inv.exchange_rate,
            'amount_excl_vat': amount_excl_tax,
            'amount_excl_vat_aed': amount_excl_tax_aed,
            'credits_applied': credits,
            'credits_applied_aed': credits_aed,
            'spares_revenue': spares_rev,
            'spare_cost': spare_cost,
            'spares_profit': spares_profit,
            'other_cost': other_cost,
            'engineer_cost': engineer_cost,
            'total_cost': total_cost,
            'total_net_profit': total_net_profit,
            'total_net_profit_display': profit_disp,
            'collection_value': coll_val,
            'collection_value_aed': coll_val_aed,
            'over_due': over_due,
            'over_due_aed': over_due_aed,
            'is_loss': is_loss,
            'is_legacy': bool(inv.invoice_number and inv.invoice_number.startswith('LEGACY-')),
        })

    # Sorting
    if sort_by:
        reverse = sort_by.startswith('-')
        field = sort_by.lstrip('-')
        money_fields = {
            'amount_excl_vat', 'credits_applied', 'spares_revenue', 'spare_cost',
            'spares_profit', 'other_cost', 'total_net_profit', 'collection_value', 'over_due'
        }
        if field in money_fields:
            rows.sort(key=lambda r: r.get(field, Decimal('0.00')), reverse=reverse)
        elif field == 'invoice_date':
            rows.sort(key=lambda r: (r['invoice_date'] is not None, r['invoice_date'] or date.min), reverse=reverse)
        elif field == 'status':
            rows.sort(key=lambda r: r['status'], reverse=reverse)
        elif field in ('invoice_number', 'service_ref', 'vessel', 'customer'):
            rows.sort(key=lambda r: (r.get(field) or '').lower(), reverse=reverse)
        else:
            rows.sort(key=lambda r: (r['invoice_date'] is not None, r['invoice_date'] or date.min), reverse=True)
    else:
        rows.sort(key=lambda r: (r['invoice_date'] is not None, r['invoice_date'] or date.min), reverse=True)

    totals = {
        'amount_excl_vat': sum((r['amount_excl_vat_aed'] for r in rows), Decimal('0.00')),
        'credits_applied': sum((r['credits_applied_aed'] for r in rows), Decimal('0.00')),
        'spares_revenue': sum((r['spares_revenue'] for r in rows), Decimal('0.00')),
        'spare_cost': sum((r['spare_cost'] for r in rows), Decimal('0.00')),
        'spares_profit': sum((r['spares_profit'] for r in rows), Decimal('0.00')),
        'other_cost': sum((r['other_cost'] for r in rows), Decimal('0.00')),
        'total_net_profit': sum((r['total_net_profit'] for r in rows), Decimal('0.00')),
        'collection_value': sum((r['collection_value_aed'] for r in rows), Decimal('0.00')),
        'over_due': sum((r['over_due_aed'] for r in rows), Decimal('0.00')),
    }
    totals['total_net_profit_display'] = f"({abs(totals['total_net_profit']):,.2f})" if totals['total_net_profit'] < 0 else f"{totals['total_net_profit']:,.2f}"

    return rows, totals


def invoices_excel_response(invoices_data, totals, filename_suffix='all'):
    """
    Generates an Excel workbook for the Invoice Costing page.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Invoice Costing"

    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    fill_header = PatternFill(start_color="1b314b", end_color="1b314b", fill_type="solid")
    align_header = Alignment(horizontal="center", vertical="center", wrap_text=True)

    font_data = Font(name="Calibri", size=10)
    font_total = Font(name="Calibri", size=11, bold=True)
    fill_total = PatternFill(start_color="F2F4F7", end_color="F2F4F7", fill_type="solid")
    fill_loss = PatternFill(start_color="FFF0F0", end_color="FFF0F0", fill_type="solid")

    thin_border = Border(
        left=Side(style="thin", color="E0E0E0"),
        right=Side(style="thin", color="E0E0E0"),
        top=Side(style="thin", color="E0E0E0"),
        bottom=Side(style="thin", color="E0E0E0"),
    )
    border_total = Border(
        top=Side(style="thin", color="1B314B"),
        bottom=Side(style="double", color="1B314B"),
    )

    align_left = Alignment(horizontal="left", vertical="center")
    align_center = Alignment(horizontal="center", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    headers = [
        "Sl", "Invoice #", "Invoice Date", "Due Date", "Project / Customer", "Vessel", "Type",
        "Currency", "Rate", "Amount Excl. VAT", "VAT Amount", "Credits Applied", "Received",
        "Amount Excl. VAT (AED)", "Collection Value (AED)", "Received (AED)", "Outstanding (AED)", "Status", "Days Overdue"
    ]

    ws.row_dimensions[1].height = 26
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_header

    current_row = 2
    for idx, r in enumerate(invoices_data):
        ws.row_dimensions[current_row].height = 20
        row_fill = fill_loss if r.get('is_loss') else None

        inv = r.get('invoice')
        inv_no = r.get('invoice_number') or (inv.invoice_number if inv else "—")
        inv_date = r['invoice_date'].strftime('%Y-%m-%d') if r.get('invoice_date') else (inv.invoice_date.strftime('%Y-%m-%d') if inv and inv.invoice_date else "—")
        due_date = r['due_date'].strftime('%Y-%m-%d') if r.get('due_date') else (inv.due_date.strftime('%Y-%m-%d') if inv and inv.due_date else "—")
        proj_cust = str(r.get('customer') or (inv.customer if inv else '') or r.get('service_ref') or "—")
        vessel = str(r.get('vessel') or (inv.vessel if inv else '') or "—")
        inv_type = inv.get_invoice_type_display() if inv and hasattr(inv, 'get_invoice_type_display') else str(r.get('invoice_type') or "SERVICE_JOB")

        currency = inv.currency if inv else str(r.get('currency') or 'AED')
        rate = float(inv.exchange_rate if inv else r.get('exchange_rate', 1))
        amount_excl_vat = float(inv.amount_excl_vat if inv else r.get('amount_excl_vat', 0))
        vat_amount = float(inv.vat_amount if inv else r.get('vat_amount', 0))
        credits_applied = float(inv.credits_applied if inv else r.get('credits_applied', 0))
        amount_received = float(inv.amount_received if inv else r.get('amount_received', 0))

        amount_excl_vat_aed = float(r.get('amount_excl_vat_aed', inv.amount_excl_vat_aed if inv else amount_excl_vat))
        collection_value_aed = float(r.get('collection_value_aed', inv.collection_value_aed if inv else (amount_excl_vat_aed - float(r.get('credits_applied_aed', 0)))))
        received_aed = float(inv.amount_received_aed if inv else r.get('amount_received_aed', 0))
        outstanding_aed = float(inv.outstanding_aed if inv else r.get('outstanding_aed', 0))
        status = str(r.get('status') or (inv.status if inv else "OPEN"))
        days_overdue = inv.days_overdue if inv and hasattr(inv, 'days_overdue') else r.get('days_overdue', 0)

        values = [
            (idx + 1, align_center, "int"),
            (inv_no, align_left, "text"),
            (inv_date, align_center, "text"),
            (due_date, align_center, "text"),
            (proj_cust, align_left, "text"),
            (vessel, align_left, "text"),
            (inv_type, align_center, "text"),
            (currency, align_center, "text"),
            (rate, align_right, "rate"),
            (amount_excl_vat, align_right, "0.00"),
            (vat_amount, align_right, "0.00"),
            (credits_applied, align_right, "0.00"),
            (amount_received, align_right, "0.00"),
            (amount_excl_vat_aed, align_right, "0.00"),
            (collection_value_aed, align_right, "0.00"),
            (received_aed, align_right, "0.00"),
            (outstanding_aed, align_right, "0.00"),
            (status, align_center, "text"),
            (days_overdue, align_center, "int"),
        ]

        for col_idx, (val, alignment, kind) in enumerate(values, start=1):
            cell = ws.cell(row=current_row, column=col_idx, value=val)
            cell.font = font_data
            cell.alignment = alignment
            cell.border = thin_border
            if row_fill:
                cell.fill = row_fill
            if kind == "0.00" and isinstance(val, (int, float)):
                cell.number_format = "#,##0.00"
            elif kind == "rate" and isinstance(val, (int, float)):
                cell.number_format = "#,##0.0000"
            elif kind == "int" and isinstance(val, (int, float)):
                cell.number_format = "#,##0"

        current_row += 1

    # Totals Row
    ws.row_dimensions[current_row].height = 24
    tot_amt_excl_aed = sum(float(r.get('amount_excl_vat_aed', r['invoice'].amount_excl_vat_aed if r.get('invoice') else 0)) for r in invoices_data)
    tot_coll_val_aed = sum(float(r.get('collection_value_aed', r['invoice'].collection_value_aed if r.get('invoice') else 0)) for r in invoices_data)
    tot_rec_aed = sum(float(r['invoice'].amount_received_aed if r.get('invoice') else r.get('amount_received_aed', 0)) for r in invoices_data)
    tot_out_aed = sum(float(r['invoice'].outstanding_aed if r.get('invoice') else r.get('outstanding_aed', 0)) for r in invoices_data)

    total_values = [
        ("TOTAL (AED)", align_left, "text"),
        ("", align_left, "text"),
        ("", align_center, "text"),
        ("", align_center, "text"),
        ("", align_left, "text"),
        ("", align_left, "text"),
        ("", align_center, "text"),
        ("", align_center, "text"),
        ("", align_right, "text"),
        ("", align_right, "text"),
        ("", align_right, "text"),
        ("", align_right, "text"),
        ("", align_right, "text"),
        (tot_amt_excl_aed, align_right, "0.00"),
        (tot_coll_val_aed, align_right, "0.00"),
        (tot_rec_aed, align_right, "0.00"),
        (tot_out_aed, align_right, "0.00"),
        ("", align_center, "text"),
        ("", align_center, "text"),
    ]

    for col_idx, (val, alignment, kind) in enumerate(total_values, start=1):
        cell = ws.cell(row=current_row, column=col_idx, value=val)
        cell.alignment = alignment
        cell.font = font_total
        cell.fill = fill_total
        cell.border = border_total
        if kind == "0.00" and isinstance(val, (int, float)):
            cell.number_format = "#,##0.00"

    ws.freeze_panes = "A2"

    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            val_str = str(cell.value or '')
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="invoices_costing_{filename_suffix}.xlsx"'
    return response


def get_jobs_pnl_data(filters=None, sort_by=None):
    """
    Returns (rows, totals) for Jobs P&L page.
    Columns: Service Ref, Vessel, Invoice No, Invoice (amount − credits), Engineer,
    Spare Cost, Travel Port, Travel Cost, Flight, Misc Cost, Other, Total Cost,
    Profit / (Loss), Margin %, Month, Notes.
    """
    filters = filters or {}
    qs = Invoice.objects.all().select_related('project').prefetch_related('job_costs', 'spare_sales')

    start_date = filters.get('start_date')
    end_date = filters.get('end_date')
    month = filters.get('month')
    if month:
        if isinstance(month, str):
            try:
                parts = month.split('-')
                m_date = date(int(parts[0]), int(parts[1]), 1)
                f_day, l_day, _ = get_month_range(m_date)
                qs = qs.filter(invoice_date__range=(f_day, l_day))
            except (ValueError, IndexError):
                pass
        elif isinstance(month, date):
            f_day, l_day, _ = get_month_range(month)
            qs = qs.filter(invoice_date__range=(f_day, l_day))
    else:
        if start_date:
            qs = qs.filter(invoice_date__gte=start_date)
        if end_date:
            qs = qs.filter(invoice_date__lte=end_date)

    if filters.get('invoice_type') and filters['invoice_type'] != 'all':
        qs = qs.filter(invoice_type=filters['invoice_type'])

    if filters.get('search'):
        s = filters['search'].strip()
        qs = qs.filter(
            Q(invoice_number__icontains=s) |
            Q(service_ref__icontains=s) |
            Q(vessel__icontains=s) |
            Q(customer__icontains=s) |
            Q(project__name__icontains=s) |
            Q(project__client_name__icontains=s)
        )

    invoices = list(qs)

    status_filter = filters.get('status')
    if status_filter and status_filter != 'all':
        invoices = [inv for inv in invoices if inv.status.upper() == status_filter.upper()]

    project_ids = list({inv.project_id for inv in invoices if inv.project_id})
    eng_costs = engineer_cost_for_projects(project_ids)
    eng_cost_map = compute_invoice_engineer_costs(invoices, eng_costs_map=eng_costs)

    # Bulk-load SpareSale rows with project set and invoice NULL in one query
    unlinked_spares_by_proj = defaultdict(Decimal)
    if project_ids:
        unlinked_qs = (
            SpareSale.objects.filter(project_id__in=project_ids, invoice__isnull=True)
            .values('project_id', 'purchase_cost_aed')
        )
        for sp in unlinked_qs:
            unlinked_spares_by_proj[sp['project_id']] += (sp['purchase_cost_aed'] or Decimal('0.00'))

    # Identify the project's latest non-cancelled invoice (highest invoice_date, then highest id)
    all_proj_invs = defaultdict(list)
    for inv in invoices:
        if inv.project_id:
            all_proj_invs[inv.project_id].append(inv)
    latest_inv_ids = {}
    for pid, p_inv_list in all_proj_invs.items():
        sorted_p_invs = sorted(p_inv_list, key=lambda x: (x.invoice_date or date.min, x.id), reverse=True)
        latest_inv_ids[pid] = sorted_p_invs[0].id

    project_objs = [inv.project for inv in invoices if inv.project]
    proj_fin_map = get_projects_financials_map(project_objs, eng_costs_map=eng_costs)

    rows = []
    for inv in invoices:
        net_invoice = inv.collection_value_aed
        engineer = eng_cost_map.get(inv.id, Decimal('0.00'))

        fin = proj_fin_map.get(inv.project_id)
        if fin and fin.get('invoice_count', 0) <= 1:
            engineer = fin['employee_cost']
            spare_cost = fin['spares_cost']
            travel_lines = [c for c in inv.job_costs.all() if c.cost_type == 'TRAVEL']
            ports = [c.travel_port.strip() for c in travel_lines if c.travel_port and c.travel_port.strip()]
            travel_port = ", ".join(dict.fromkeys(ports))
            travel_cost = fin['job_costs']['travel']
            flight = fin['job_costs']['flight']
            misc = fin['job_costs']['misc']
            other = fin['job_costs']['other'] + fin['job_costs']['legacy']
        else:
            spare_cost = sum((s.purchase_cost_aed or Decimal('0.00')) for s in inv.spare_sales.all())
            if inv.id == latest_inv_ids.get(inv.project_id):
                spare_cost += unlinked_spares_by_proj.get(inv.project_id, Decimal('0.00'))
            travel_lines = [c for c in inv.job_costs.all() if c.cost_type == 'TRAVEL']
            travel_cost = sum((c.amount_aed or Decimal('0.00')) for c in travel_lines)
            ports = [c.travel_port.strip() for c in travel_lines if c.travel_port and c.travel_port.strip()]
            travel_port = ", ".join(dict.fromkeys(ports))
            flight = sum((c.amount_aed or Decimal('0.00')) for c in inv.job_costs.all() if c.cost_type == 'FLIGHT')
            misc = sum((c.amount_aed or Decimal('0.00')) for c in inv.job_costs.all() if c.cost_type == 'MISC')
            other = sum((c.amount_aed or Decimal('0.00')) for c in inv.job_costs.all() if c.cost_type == 'OTHER' or c.source == 'LEGACY')

        total_cost = engineer + spare_cost + travel_cost + flight + misc + other
        profit = net_invoice - total_cost

        if net_invoice != Decimal('0.00'):
            margin_pct = (profit / net_invoice) * Decimal('100.00')
        else:
            margin_pct = Decimal('0.00')

        is_loss = profit < Decimal('0.00')
        profit_disp = f"({abs(profit):,.2f})" if is_loss else f"{profit:,.2f}"
        margin_disp = f"({abs(margin_pct):.1f}%)" if is_loss else f"{margin_pct:.1f}%"
        month_str = inv.invoice_date.strftime('%B %Y') if inv.invoice_date else '—'

        rows.append({
            'invoice': inv,
            'id': inv.id,
            'service_ref': inv.service_ref or (inv.project.service_reference if inv.project else '—'),
            'vessel': inv.vessel or (inv.project.vessel_name if inv.project else '—'),
            'customer': inv.customer or (inv.project.client_name if inv.project else '—'),
            'invoice_number': inv.invoice_number,
            'net_invoice': net_invoice,
            'engineer': engineer,
            'spare_cost': spare_cost,
            'travel_port': travel_port or '—',
            'travel_cost': travel_cost,
            'flight': flight,
            'misc': misc,
            'other': other,
            'total_cost': total_cost,
            'profit': profit,
            'profit_display': profit_disp,
            'margin_pct': margin_pct,
            'margin_pct_display': margin_disp,
            'month': month_str,
            'notes': inv.notes or '',
            'is_loss': is_loss,
        })

    # Sorting
    if sort_by:
        reverse = sort_by.startswith('-')
        field = sort_by.lstrip('-')
        money_fields = {
            'net_invoice', 'engineer', 'spare_cost', 'travel_cost', 'flight',
            'misc', 'other', 'total_cost', 'profit', 'margin_pct'
        }
        if field in money_fields:
            rows.sort(key=lambda r: r.get(field, Decimal('0.00')), reverse=reverse)
        elif field in ('invoice_number', 'service_ref', 'vessel'):
            rows.sort(key=lambda r: (r.get(field) or '').lower(), reverse=reverse)
        else:
            rows.sort(key=lambda r: (r['invoice'].invoice_date is not None, r['invoice'].invoice_date or date.min), reverse=True)
    else:
        rows.sort(key=lambda r: (r['invoice'].invoice_date is not None, r['invoice'].invoice_date or date.min), reverse=True)

    total_net_invoice = sum((r['net_invoice'] for r in rows), Decimal('0.00'))
    total_profit = sum((r['profit'] for r in rows), Decimal('0.00'))
    avg_margin_pct = (total_profit / total_net_invoice * Decimal('100.00')) if total_net_invoice != Decimal('0.00') else Decimal('0.00')

    totals = {
        'net_invoice': total_net_invoice,
        'engineer': sum((r['engineer'] for r in rows), Decimal('0.00')),
        'spare_cost': sum((r['spare_cost'] for r in rows), Decimal('0.00')),
        'travel_cost': sum((r['travel_cost'] for r in rows), Decimal('0.00')),
        'flight': sum((r['flight'] for r in rows), Decimal('0.00')),
        'misc': sum((r['misc'] for r in rows), Decimal('0.00')),
        'other': sum((r['other'] for r in rows), Decimal('0.00')),
        'total_cost': sum((r['total_cost'] for r in rows), Decimal('0.00')),
        'profit': total_profit,
        'profit_display': f"({abs(total_profit):,.2f})" if total_profit < 0 else f"{total_profit:,.2f}",
        'avg_margin_pct': avg_margin_pct,
        'avg_margin_pct_display': f"({abs(avg_margin_pct):.1f}%)" if avg_margin_pct < 0 else f"{avg_margin_pct:.1f}%",
    }

    return rows, totals


def jobs_pnl_excel_response(jobs_data, totals, filename_suffix='all'):
    """
    Generates an Excel workbook for the Jobs P&L page.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Jobs P&L"

    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    fill_header = PatternFill(start_color="1b314b", end_color="1b314b", fill_type="solid")
    align_header = Alignment(horizontal="center", vertical="center", wrap_text=True)

    font_data = Font(name="Calibri", size=10)
    font_total = Font(name="Calibri", size=11, bold=True)
    fill_total = PatternFill(start_color="F2F4F7", end_color="F2F4F7", fill_type="solid")
    fill_loss = PatternFill(start_color="FFF0F0", end_color="FFF0F0", fill_type="solid")

    thin_border = Border(
        left=Side(style="thin", color="E0E0E0"),
        right=Side(style="thin", color="E0E0E0"),
        top=Side(style="thin", color="E0E0E0"),
        bottom=Side(style="thin", color="E0E0E0"),
    )
    border_total = Border(
        top=Side(style="thin", color="1B314B"),
        bottom=Side(style="double", color="1B314B"),
    )

    align_left = Alignment(horizontal="left", vertical="center")
    align_center = Alignment(horizontal="center", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    headers = [
        "Sl", "Service Ref", "Vessel", "Invoice No", "Inv Currency", "Inv Rate", "Net Invoice", "Net Invoice (AED)",
        "Engineer (AED)", "Spare Cost (AED)", "Travel Port", "Travel Cost (AED)", "Flight (AED)", "Misc Cost (AED)",
        "Other (AED)", "Total Cost (AED)", "Profit / (Loss) (AED)", "Margin %", "Month", "Notes"
    ]

    ws.row_dimensions[1].height = 26
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_header

    current_row = 2
    for idx, r in enumerate(jobs_data):
        ws.row_dimensions[current_row].height = 20
        row_fill = fill_loss if r.get('is_loss') else None

        inv = r.get('invoice')
        inv_curr = inv.currency if inv else str(r.get('currency') or 'AED')
        inv_rate = float(inv.exchange_rate if inv else r.get('exchange_rate', 1))
        net_inv_orig = float(inv.collection_value if inv else r['net_invoice'])
        net_inv_aed = float(r['net_invoice'])

        values = [
            (idx + 1, align_center, "int"),
            (r.get('service_ref') or "—", align_left, "text"),
            (r.get('vessel') or "—", align_left, "text"),
            (r.get('invoice_number') or "—", align_left, "text"),
            (inv_curr, align_center, "text"),
            (inv_rate, align_right, "rate"),
            (net_inv_orig, align_right, "0.00"),
            (net_inv_aed, align_right, "0.00"),
            (float(r['engineer']), align_right, "0.00"),
            (float(r['spare_cost']), align_right, "0.00"),
            (r.get('travel_port') or "—", align_left, "text"),
            (float(r['travel_cost']), align_right, "0.00"),
            (float(r['flight']), align_right, "0.00"),
            (float(r['misc']), align_right, "0.00"),
            (float(r['other']), align_right, "0.00"),
            (float(r['total_cost']), align_right, "0.00"),
            (float(r['profit']), align_right, "0.00"),
            (float(r['margin_pct']) / 100.0, align_right, "pct"),
            (r.get('month') or "—", align_center, "text"),
            (r.get('notes') or "—", align_left, "text"),
        ]

        for col_idx, (val, alignment, kind) in enumerate(values, start=1):
            cell = ws.cell(row=current_row, column=col_idx, value=val)
            cell.font = font_data
            cell.alignment = alignment
            cell.border = thin_border
            if row_fill:
                cell.fill = row_fill
            if kind == "0.00" and isinstance(val, (int, float)):
                cell.number_format = "#,##0.00"
            elif kind == "rate" and isinstance(val, (int, float)):
                cell.number_format = "#,##0.0000"
            elif kind == "pct" and isinstance(val, (int, float)):
                cell.number_format = '0.0%'
            elif kind == "int" and isinstance(val, int):
                cell.number_format = "#,##0"

        current_row += 1

    # Totals Row
    ws.row_dimensions[current_row].height = 24
    total_values = [
        ("TOTAL (AED)", align_left, "text"),
        ("", align_left, "text"),
        ("", align_left, "text"),
        ("", align_left, "text"),
        ("", align_center, "text"),
        ("", align_right, "text"),
        ("", align_right, "text"),
        (float(totals.get('net_invoice', 0)), align_right, "0.00"),
        (float(totals.get('engineer', 0)), align_right, "0.00"),
        (float(totals.get('spare_cost', 0)), align_right, "0.00"),
        ("", align_left, "text"),
        (float(totals.get('travel_cost', 0)), align_right, "0.00"),
        (float(totals.get('flight', 0)), align_right, "0.00"),
        (float(totals.get('misc', 0)), align_right, "0.00"),
        (float(totals.get('other', 0)), align_right, "0.00"),
        (float(totals.get('total_cost', 0)), align_right, "0.00"),
        (float(totals.get('profit', 0)), align_right, "0.00"),
        (float(totals.get('avg_margin_pct', 0)) / 100.0, align_right, "pct"),
        ("", align_center, "text"),
        ("", align_left, "text"),
    ]

    for col_idx, (val, alignment, kind) in enumerate(total_values, start=1):
        cell = ws.cell(row=current_row, column=col_idx, value=val)
        cell.alignment = alignment
        cell.font = font_total
        cell.fill = fill_total
        cell.border = border_total
        if kind == "0.00" and isinstance(val, (int, float)):
            cell.number_format = "#,##0.00"
        elif kind == "pct" and isinstance(val, (int, float)):
            cell.number_format = '0.0%'

    ws.freeze_panes = "A2"

    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            val_str = str(cell.value or '')
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="jobs_pnl_{filename_suffix}.xlsx"'
    return response


def get_spares_accounting_data(filters=None, sort_by=None, alerts_only=False):
    """
    Returns (spares, summary) for Spares Accounting page.
    Columns: Service Ref, Vessel / Customer, Source, Purchase Cost, Actual Selling Price,
    Actual Spare Profit, Actual Markup %, Invoice No, Status pill, Notes, Alerts.
    Top cards: total purchase, total selling, total profit, average markup, number of rows with alerts.
    """
    filters = filters or {}
    qs = SpareSale.objects.all().select_related('project', 'invoice')

    if filters.get('search'):
        s = filters['search'].strip()
        qs = qs.filter(
            Q(service_ref__icontains=s) |
            Q(vessel_customer__icontains=s) |
            Q(notes__icontains=s) |
            Q(invoice__invoice_number__icontains=s)
        )

    if filters.get('status') and filters['status'] != 'all':
        qs = qs.filter(status=filters['status'])

    if filters.get('source') and filters['source'] != 'all':
        qs = qs.filter(source=filters['source'])

    spares = list(qs)

    # Pre-calculate invoice spares mismatch per invoice without N+1
    inv_spares_map = defaultdict(list)
    for s in spares:
        if s.invoice_id:
            inv_spares_map[s.invoice_id].append(s)

    for inv_id, sp_list in inv_spares_map.items():
        inv = sp_list[0].invoice
        if inv and inv.invoice_type == 'SPARES_ONLY':
            total_sp_selling = sum((sp.selling_price_aed or Decimal('0.00')) for sp in sp_list)
            is_mismatch = (total_sp_selling != inv.amount_excl_vat_aed)
            for sp in sp_list:
                sp._invoice_spares_mismatch = is_mismatch
        else:
            for sp in sp_list:
                sp._invoice_spares_mismatch = False

    for s in spares:
        if not s.invoice_id:
            s._invoice_spares_mismatch = False

    # Pre-calculate legacy purchases per invoice without N+1
    legacy_costs_map = {
        jc['invoice_id']: jc['amt']
        for jc in JobCost.objects.filter(
            source='LEGACY',
            description='Legacy purchases',
            invoice_id__in=[s.invoice_id for s in spares if s.invoice_id]
        ).values('invoice_id').annotate(amt=Sum('amount_aed'))
    }
    for s in spares:
        if s.invoice_id:
            s._legacy_purchases_cost = legacy_costs_map.get(s.invoice_id, Decimal('0.00'))
        else:
            s._legacy_purchases_cost = Decimal('0.00')

    if alerts_only or filters.get('alerts_only'):
        spares = [s for s in spares if s.alerts]

    # Summary
    total_purchase = sum((s.purchase_cost_aed or Decimal('0.00')) for s in spares)
    total_selling = sum((s.selling_price_aed or Decimal('0.00')) for s in spares)
    total_profit = sum((s.profit_aed or Decimal('0.00')) for s in spares)
    avg_markup = (total_profit / total_purchase * Decimal('100.00')) if total_purchase > Decimal('0.00') else Decimal('0.00')
    alerts_count = sum(1 for s in spares if s.alerts)

    summary = {
        'total_purchase': total_purchase,
        'total_selling': total_selling,
        'total_profit': total_profit,
        'avg_markup_pct': avg_markup,
        'alerts_count': alerts_count,
    }

    # Sorting
    if sort_by:
        reverse = sort_by.startswith('-')
        field = sort_by.lstrip('-')
        if field in ('purchase_cost', 'selling_price', 'profit'):
            spares.sort(key=lambda s: getattr(s, f"{field}_aed") or Decimal('0.00'), reverse=reverse)
        elif field == 'markup_pct':
            spares.sort(key=lambda s: s.markup_pct or Decimal('0.00'), reverse=reverse)
        elif field in ('service_ref', 'vessel_customer', 'status', 'source'):
            spares.sort(key=lambda s: (getattr(s, field) or '').lower(), reverse=reverse)
        else:
            spares.sort(key=lambda s: (s.created_at or timezone.now()), reverse=True)
    else:
        spares.sort(key=lambda s: (s.created_at or timezone.now()), reverse=True)

    return spares, summary


def spares_excel_response(spares_data, summary, filename_suffix='all'):
    """
    Generates an Excel workbook for the Spares Accounting page.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Spares Accounting"

    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    fill_header = PatternFill(start_color="1b314b", end_color="1b314b", fill_type="solid")
    align_header = Alignment(horizontal="center", vertical="center", wrap_text=True)

    font_data = Font(name="Calibri", size=10)
    font_total = Font(name="Calibri", size=11, bold=True)
    fill_total = PatternFill(start_color="F2F4F7", end_color="F2F4F7", fill_type="solid")

    thin_border = Border(
        left=Side(style="thin", color="E0E0E0"),
        right=Side(style="thin", color="E0E0E0"),
        top=Side(style="thin", color="E0E0E0"),
        bottom=Side(style="thin", color="E0E0E0"),
    )
    border_total = Border(
        top=Side(style="thin", color="1B314B"),
        bottom=Side(style="double", color="1B314B"),
    )

    align_left = Alignment(horizontal="left", vertical="center")
    align_center = Alignment(horizontal="center", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    headers = [
        "Sl", "Service Ref / PO", "Description", "Vessel / Customer", "Invoice #", "Source", "Status",
        "Purchase Curr", "Purchase Rate", "Purchase Cost", "Purchase Cost (AED)",
        "Selling Curr", "Selling Rate", "Selling Price", "Selling Price (AED)",
        "Profit (AED)", "Markup %", "Alerts"
    ]

    ws.row_dimensions[1].height = 26
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_header

    current_row = 2
    for idx, s in enumerate(spares_data):
        ws.row_dimensions[current_row].height = 20
        alerts_str = ", ".join(s.alerts) if s.alerts else "None"
        inv_no = s.invoice.invoice_number if s.invoice else "—"

        values = [
            (idx + 1, align_center, "int"),
            (s.service_ref or "—", align_left, "text"),
            (s.notes or "—", align_left, "text"),
            (s.vessel_customer or "—", align_left, "text"),
            (inv_no, align_left, "text"),
            (s.get_source_display() if hasattr(s, 'get_source_display') else s.source, align_center, "text"),
            (s.get_status_display() if hasattr(s, 'get_status_display') else s.status, align_center, "text"),
            (s.purchase_currency, align_center, "text"),
            (float(s.purchase_rate or 1), align_right, "rate"),
            (float(s.purchase_cost or 0), align_right, "0.00"),
            (float(s.purchase_cost_aed or 0), align_right, "0.00"),
            (s.selling_currency, align_center, "text"),
            (float(s.selling_rate or 1), align_right, "rate"),
            (float(s.selling_price or 0), align_right, "0.00"),
            (float(s.selling_price_aed or 0), align_right, "0.00"),
            (float(s.profit_aed or 0), align_right, "0.00"),
            (float(s.markup_pct or 0) / 100.0 if s.markup_pct is not None else 0.0, align_right, "pct"),
            (alerts_str, align_left, "text"),
        ]

        for col_idx, (val, alignment, kind) in enumerate(values, start=1):
            cell = ws.cell(row=current_row, column=col_idx, value=val)
            cell.font = font_data
            cell.alignment = alignment
            cell.border = thin_border
            if kind == "0.00" and isinstance(val, (int, float)):
                cell.number_format = "#,##0.00"
            elif kind == "rate" and isinstance(val, (int, float)):
                cell.number_format = "#,##0.0000"
            elif kind == "pct" and isinstance(val, (int, float)):
                cell.number_format = '0.0%'
            elif kind == "int" and isinstance(val, int):
                cell.number_format = "#,##0"

        current_row += 1

    # Totals Row
    ws.row_dimensions[current_row].height = 24
    total_values = [
        ("TOTAL (AED)", align_left, "text"),
        ("", align_left, "text"),
        ("", align_left, "text"),
        ("", align_left, "text"),
        ("", align_left, "text"),
        ("", align_center, "text"),
        ("", align_center, "text"),
        ("", align_center, "text"),
        ("", align_right, "text"),
        ("", align_right, "text"),
        (float(summary.get('total_purchase', 0)), align_right, "0.00"),
        ("", align_center, "text"),
        ("", align_right, "text"),
        ("", align_right, "text"),
        (float(summary.get('total_selling', 0)), align_right, "0.00"),
        (float(summary.get('total_profit', 0)), align_right, "0.00"),
        (float(summary.get('avg_markup_pct', 0)) / 100.0, align_right, "pct"),
        (f"{summary.get('alerts_count', 0)} Alerts", align_left, "text"),
    ]

    for col_idx, (val, alignment, kind) in enumerate(total_values, start=1):
        cell = ws.cell(row=current_row, column=col_idx, value=val)
        cell.alignment = alignment
        cell.font = font_total
        cell.fill = fill_total
        cell.border = border_total
        if kind == "0.00" and isinstance(val, (int, float)):
            cell.number_format = "#,##0.00"
        elif kind == "pct" and isinstance(val, (int, float)):
            cell.number_format = '0.0%'

    ws.freeze_panes = "A2"

    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            val_str = str(cell.value or '')
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="spares_accounting_{filename_suffix}.xlsx"'
    return response








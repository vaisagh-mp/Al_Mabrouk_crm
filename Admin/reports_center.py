import base64
import io
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from weasyprint import HTML

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone

from Admin.attendance_register import build_attendance_register, build_attendance_summary
from Admin.document_helpers import get_document_status
from Admin.leave_service import get_employee_leave_summary, is_leave_v2_active
from Admin.models import (
    AttendanceCorrection,
    Branch,
    CompanyDocument,
    Department,
    Employee,
    EmployeeDocument,
    Leave,
    LeaveBalance,
    LeaveLedger,
    LeaveType,
    OTApproval,
    SalaryStructure,
)
from Admin.permissions import can_view_salary
from finance.services import get_payroll_list_with_metrics, get_month_range
from service_logs.models import EmployeeServiceLog
from service_logs.ot import (
    calculate_office_ot_cost, split_day_ot, employee_office_attendances,
    OT_MULT_NORMAL, OT_MULT_SUNDAY, OT_MULT_PUBLIC_HOLIDAY,
)


REPORT_DEFINITIONS = [
    ('employee_master', {
        'title': '1. Employee Master',
        'day_level': False,
        'uses_period': False,
        'uses_month': False,
        'status_choices': [
            ('active', 'Active'),
            ('inactive', 'Inactive'),
            ('all', 'All'),
        ],
    }),
    ('daily_attendance', {
        'title': '2. Daily Attendance',
        'day_level': True,
        'uses_period': True,
        'uses_month': False,
        'status_choices': [
            ('OFFICE', 'Office'),
            ('VESSEL', 'Vessel'),
            ('OFFICE_VESSEL', 'Office+Vessel'),
            ('ABSENT', 'Absent'),
            ('MISSING_PUNCH', 'Missing Punch'),
            ('ANNUAL_LEAVE', 'Annual Leave'),
            ('SICK_LEAVE', 'Sick Leave'),
            ('PERSONAL_LEAVE', 'Personal Leave'),
            ('UNPAID_LEAVE', 'Unpaid Leave'),
            ('REST', 'REST / Off-day'),
            ('HOLIDAY', 'Holiday'),
        ],
    }),
    ('monthly_summary', {
        'title': '3. Monthly Attendance Summary',
        'day_level': False,
        'uses_period': True,
        'uses_month': True,
        'status_choices': [],
    }),
    ('missing_punch_corrections', {
        'title': '4. Missing Punch & Correction Requests',
        'day_level': True,
        'uses_period': True,
        'uses_month': False,
        'status_choices': [
            ('PENDING', 'Pending'),
            ('APPROVED', 'Approved'),
            ('REJECTED', 'Rejected'),
            ('CANCELLED', 'Cancelled'),
            ('MISSING_PUNCH', 'Unrequested Missing Punch'),
        ],
    }),
    ('leave_balances', {
        'title': '5. Leave Balances',
        'day_level': False,
        'uses_period': False,
        'uses_month': False,
        'status_choices': [],
    }),
    ('leave_history', {
        'title': '6. Leave History',
        'day_level': False,
        'uses_period': True,
        'uses_month': False,
        'status_choices': [
            ('PENDING', 'Pending'),
            ('CLARIFICATION', 'Clarification'),
            ('APPROVED', 'Approved'),
            ('REJECTED', 'Rejected'),
            ('CANCELLED', 'Cancelled'),
        ],
    }),
    ('overtime', {
        'title': '7. Overtime Report',
        'day_level': True,
        'uses_period': True,
        'uses_month': False,
        'status_choices': [
            ('PENDING', 'Pending'),
            ('APPROVED', 'Approved'),
            ('REJECTED', 'Rejected'),
        ],
    }),
    ('timesheets', {
        'title': '8. Vessel / Site Timesheets',
        'day_level': True,
        'uses_period': True,
        'uses_month': False,
        'status_choices': [
            ('DRAFT', 'Draft'),
            ('SUBMITTED', 'Submitted'),
            ('APPROVED', 'Approved'),
            ('REJECTED', 'Rejected'),
        ],
    }),
    ('document_expiry', {
        'title': '9. Document Expiry',
        'day_level': False,
        'uses_period': False,
        'uses_month': False,
        'status_choices': [
            ('expired', 'Expired'),
            ('30', 'Expiring <=30 days'),
            ('60', 'Expiring <=60 days'),
            ('90', 'Expiring <=90 days'),
            ('valid', 'Valid'),
        ],
    }),
    ('payroll_summary', {
        'title': '10. Payroll Summary',
        'day_level': False,
        'uses_period': False,
        'uses_month': True,
        'salary_only': True,
        'status_choices': [
            ('DRAFT', 'Draft'),
            ('APPROVED', 'Approved'),
            ('PAID', 'Paid'),
        ],
    }),
]

REPORT_META_MAP = dict(REPORT_DEFINITIONS)
ACCOUNTANT_REPORT_KEYS = {'payroll_summary', 'overtime'}


def get_viewer_report_role(user, requested_role=None):
    """
    Determines effective role for Reports Center: 'admin', 'hr', 'accountant', or 'manager'.
    Strictly enforces role boundaries per mount route and raises PermissionDenied otherwise.
    """
    if not user or not user.is_authenticated:
        raise PermissionDenied("Authentication required.")

    emp = getattr(user, 'employee_profile', None)
    is_su = bool(user.is_superuser)
    is_hr = bool(emp and emp.is_hr)
    is_acc = bool(emp and emp.is_accountant)
    is_mgr = bool(emp and emp.is_manager)

    if requested_role == 'admin':
        if not is_su:
            raise PermissionDenied("Admin reports require superuser permissions.")
        return 'admin'
    if requested_role == 'hr':
        if not (is_su or is_hr):
            raise PermissionDenied("HR reports require HR or Admin permissions.")
        return 'hr'
    if requested_role == 'accountant':
        if not (is_su or is_acc):
            raise PermissionDenied("Finance reports require Accountant or Admin permissions.")
        return 'accountant'
    if requested_role == 'manager':
        if not (is_su or is_mgr):
            raise PermissionDenied("Manager reports require Manager permissions.")
        return 'manager'

    if is_su:
        return 'admin'
    if is_hr:
        return 'hr'
    if is_acc:
        return 'accountant'
    if is_mgr:
        return 'manager'
    raise PermissionDenied("You do not have permission to access the Reports Center.")


def get_allowed_reports_for_role(role_key):
    if role_key in ('admin', 'hr'):
        return list(REPORT_DEFINITIONS)
    if role_key == 'accountant':
        return [(k, m) for k, m in REPORT_DEFINITIONS if k in ACCOUNTANT_REPORT_KEYS]
    if role_key == 'manager':
        return [(k, m) for k, m in REPORT_DEFINITIONS if not m.get('salary_only')]
    return []


def _get_scoped_employees_qs(user, role_key, dept_id=None, branch_id=None, emp_id=None, include_inactive=False):
    viewer_emp = getattr(user, 'employee_profile', None)
    qs = Employee.objects.select_related('user', 'department', 'branch', 'reporting_manager__user')
    if not include_inactive:
        qs = qs.filter(user__is_active=True)

    if role_key == 'manager':
        if not viewer_emp:
            return qs.none()
        qs = qs.filter(reporting_manager=viewer_emp)

    if dept_id:
        qs = qs.filter(department_id=dept_id)
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    if emp_id:
        qs = qs.filter(id=emp_id)

    return qs.order_by('user__first_name', 'user__username')


def parse_report_filters(request):
    today = timezone.localdate()
    default_start = today.replace(day=1)
    default_end = today

    month_param = (request.GET.get('month') or '').strip()
    start_param = (request.GET.get('start') or '').strip()
    end_param = (request.GET.get('end') or '').strip()

    month_date = None
    if month_param:
        try:
            y, m = map(int, month_param.split('-'))
            month_date = date(y, m, 1)
        except ValueError:
            month_date = date(today.year, today.month, 1)
    else:
        month_date = date(today.year, today.month, 1)

    if start_param or end_param:
        try:
            start_date = datetime.strptime(start_param, '%Y-%m-%d').date() if start_param else default_start
        except ValueError:
            start_date = default_start
        try:
            end_date = datetime.strptime(end_param, '%Y-%m-%d').date() if end_param else start_date
        except ValueError:
            end_date = start_date
    elif month_param:
        _, num_days = monthrange(month_date.year, month_date.month)
        start_date = month_date
        end_date = date(month_date.year, month_date.month, num_days)
    else:
        start_date = default_start
        end_date = default_end

    if start_date > end_date:
        start_date, end_date = end_date, start_date

    dept_raw = (request.GET.get('department') or '').strip()
    branch_raw = (request.GET.get('branch') or '').strip()
    emp_raw = (request.GET.get('employee') or '').strip()
    status_raw = (request.GET.get('status') or '').strip()

    return {
        'start_date': start_date,
        'end_date': end_date,
        'month_date': month_date,
        'month_str': month_date.strftime('%Y-%m'),
        'start_str': start_date.strftime('%Y-%m-%d'),
        'end_str': end_date.strftime('%Y-%m-%d'),
        'dept_id': int(dept_raw) if dept_raw.isdigit() else None,
        'branch_id': int(branch_raw) if branch_raw.isdigit() else None,
        'emp_id': int(emp_raw) if emp_raw.isdigit() else None,
        'dept_raw': dept_raw,
        'branch_raw': branch_raw,
        'emp_raw': emp_raw,
        'status': status_raw,
    }


def _filters_description(filters, report_key):
    parts = []
    if filters['dept_id']:
        d = Department.objects.filter(pk=filters['dept_id']).first()
        if d:
            parts.append(f"Department: {d.name}")
    if filters['branch_id']:
        b = Branch.objects.filter(pk=filters['branch_id']).first()
        if b:
            parts.append(f"Branch: {b.name}")
    if filters['emp_id']:
        e = Employee.objects.filter(pk=filters['emp_id']).select_related('user').first()
        if e:
            parts.append(f"Employee: {e.name}")
    if filters['status']:
        parts.append(f"Status: {filters['status']}")
    return ', '.join(parts) if parts else 'All'


def build_report_data(report_key, filters, user, role_key):
    """
    Reuses existing builders for each of the 10 reports.
    Returns dict: title, headers, rows, totals_row, period_label, filters_label, total_count.
    """
    allowed_keys = {k for k, _ in get_allowed_reports_for_role(role_key)}
    if report_key not in allowed_keys:
        raise PermissionDenied("You do not have permission to view this report.")

    meta = REPORT_META_MAP[report_key]
    show_salary = can_view_salary(user) and role_key != 'manager'

    start_date = filters['start_date']
    end_date = filters['end_date']
    month_date = filters['month_date']
    dept_id = filters['dept_id']
    branch_id = filters['branch_id']
    emp_id = filters['emp_id']
    status_filter = filters['status']

    period_label = (
        month_date.strftime('%B %Y') if meta.get('uses_month') and report_key == 'payroll_summary'
        else (f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}" if meta.get('uses_period') else 'Current / As of Today')
    )
    filters_label = _filters_description(filters, report_key)

    headers = []
    rows = []
    totals_row = None

    # 1. Employee Master
    if report_key == 'employee_master':
        include_inactive = status_filter in ('inactive', 'all')
        qs = _get_scoped_employees_qs(user, role_key, dept_id=dept_id, branch_id=branch_id, emp_id=emp_id, include_inactive=include_inactive)
        if status_filter == 'inactive':
            qs = qs.filter(user__is_active=False)
        elif status_filter != 'all':
            qs = qs.filter(user__is_active=True)

        headers = ['Code', 'Employee Name', 'Role', 'Department', 'Branch', 'Designation', 'Work Type', 'Join Date', 'Status', 'Phone / Email']
        if show_salary:
            headers.extend(['Salary (AED)'])

        for e in qs:
            row = [
                e.employee_code or '-',
                e.name,
                e.get_role(),
                e.department.name if e.department else '-',
                e.branch.name if e.branch else '-',
                e.rank or '-',
                e.get_work_type_display() if hasattr(e, 'get_work_type_display') else (e.work_type or '-'),
                e.date_of_join.strftime('%d/%m/%Y') if e.date_of_join else '-',
                'Active' if e.user.is_active else 'Inactive',
                e.phone_number or e.company_email or e.user.email or '-',
            ]
            if show_salary:
                row.append(f"{e.salary or Decimal('0.00'):.2f}")
            rows.append(row)

    # 2. Daily Attendance
    elif report_key == 'daily_attendance':
        scoped_emps = list(_get_scoped_employees_qs(user, role_key, dept_id=dept_id, branch_id=branch_id, emp_id=emp_id))
        emp_ids = [e.id for e in scoped_emps]
        reg_rows = build_attendance_register(
            start_date, end_date,
            department=dept_id, branch=branch_id,
            employee_ids=emp_ids if emp_ids else [-1],
            status_filter=status_filter,
        ) if emp_ids else []

        headers = ['Date', 'Code', 'Employee', 'Department', 'Status', 'Location', 'Punch In', 'Punch Out', 'Hours', 'OT Hours', 'Vessel / Job', 'Remarks']
        for r in reg_rows:
            emp = r['employee']
            rows.append([
                r['date'].strftime('%d/%m/%Y'),
                emp.employee_code or '-',
                r.get('name', ''),
                emp.department.name if emp.department else '-',
                r.get('status_label', ''),
                r.get('location_label', '-'),
                r['start'].strftime('%H:%M') if r.get('start') else '-',
                r['end'].strftime('%H:%M') if r.get('end') else '-',
                f"{Decimal(str(r.get('total_hours') or 0)):.2f}",
                f"{Decimal(str(r.get('ot_hours') or 0)):.2f}",
                ', '.join(r.get('vessel_job') or []) or '-',
                r.get('off_day_label') or '-',
            ])

    # 3. Monthly Attendance Summary
    elif report_key == 'monthly_summary':
        scoped_emps = list(_get_scoped_employees_qs(user, role_key, dept_id=dept_id, branch_id=branch_id, emp_id=emp_id))
        emp_ids = [e.id for e in scoped_emps]
        reg_rows = build_attendance_register(
            start_date, end_date,
            department=dept_id, branch=branch_id,
            employee_ids=emp_ids if emp_ids else [-1],
        ) if emp_ids else []
        sum_rows, totals = build_attendance_summary(reg_rows)

        headers = [
            'Code', 'Employee', 'Department', 'Scheduled Days', 'Present Days',
            'Attendance %', 'Office', 'Vessel', 'Office+Vessel', 'Leave',
            'Rest', 'Holidays', 'Missing Punch', 'Absent', 'Total Hours', 'OT Hours',
        ]
        for s in sum_rows:
            emp = s['employee']
            rows.append([
                emp.employee_code or '-',
                s['name'],
                emp.department.name if emp.department else '-',
                s['scheduled_working_days'],
                s['present_days'],
                f"{s['attendance_pct']}%",
                s['office_days'],
                s['vessel_days'],
                s['office_vessel_days'],
                s['total_leave_days'],
                s['rest_days'],
                s['holiday_days'],
                s['missing_punch_days'],
                s['absent_days'],
                f"{s['total_hours']:.2f}",
                f"{s['gross_ot_hours']:.2f}",
            ])
        if sum_rows:
            totals_row = [
                'TOTAL', '', '', totals['scheduled_working_days'], totals['present_days'],
                f"{totals['attendance_pct']}%", totals['office_days'], totals['vessel_days'],
                totals['office_vessel_days'], totals['total_leave_days'], totals['rest_days'],
                totals['holiday_days'], totals['missing_punch_days'], totals['absent_days'],
                f"{totals['total_hours']:.2f}", f"{totals['gross_ot_hours']:.2f}",
            ]

    # 4. Missing Punch & Correction Requests
    elif report_key == 'missing_punch_corrections':
        scoped_emps = list(_get_scoped_employees_qs(user, role_key, dept_id=dept_id, branch_id=branch_id, emp_id=emp_id))
        emp_ids = [e.id for e in scoped_emps]

        corr_qs = AttendanceCorrection.objects.filter(
            date__range=(start_date, end_date),
            employee_id__in=emp_ids if emp_ids else [-1],
        ).select_related('employee__user', 'employee__department', 'decided_by').order_by('-date', '-id')
        if status_filter and status_filter != 'MISSING_PUNCH':
            corr_qs = corr_qs.filter(status=status_filter)

        headers = ['Date', 'Code', 'Employee', 'Department', 'Source', 'Correction Type', 'Requested Times', 'Status', 'Remarks']

        corr_emp_dates = set()
        if status_filter != 'MISSING_PUNCH':
            for c in corr_qs:
                corr_emp_dates.add((c.employee_id, c.date))
                req_times = f"{c.requested_login.strftime('%H:%M') if c.requested_login else '--'} - {c.requested_logout.strftime('%H:%M') if c.requested_logout else '--'}"
                rows.append([
                    c.date.strftime('%d/%m/%Y'),
                    c.employee.employee_code or '-',
                    c.employee.name,
                    c.employee.department.name if c.employee.department else '-',
                    'Correction Request',
                    c.get_correction_type_display(),
                    req_times,
                    c.get_status_display(),
                    c.remarks or c.reason or '-',
                ])

        if not status_filter or status_filter == 'MISSING_PUNCH':
            reg_rows = build_attendance_register(
                start_date, end_date,
                department=dept_id, branch=branch_id,
                employee_ids=emp_ids if emp_ids else [-1],
            ) if emp_ids else []
            for r in reg_rows:
                emp = r['employee']
                if r.get('status_code') == 'MISSING_PUNCH' and (emp.id, r['date']) not in corr_emp_dates:
                    rows.append([
                        r['date'].strftime('%d/%m/%Y'),
                        emp.employee_code or '-',
                        r.get('name', ''),
                        emp.department.name if emp.department else '-',
                        'Register Missing Punch',
                        'Missing Punch',
                        f"{r['start'].strftime('%H:%M') if r.get('start') else '--'} - --",
                        'Unresolved',
                        r.get('off_day_label') or '-',
                    ])

    # 5. Leave Balances
    elif report_key == 'leave_balances':
        scoped_emps = list(_get_scoped_employees_qs(user, role_key, dept_id=dept_id, branch_id=branch_id, emp_id=emp_id))
        target_year = start_date.year
        headers = ['Code', 'Employee', 'Department', 'Leave Type', 'Year', 'Opening / Ent.', 'Accrued / Adj.', 'Taken', 'Pending', 'Available']

        if is_leave_v2_active():
            leave_types = list(LeaveType.objects.filter(is_active=True, uses_balance=True).order_by('sort_order', 'name'))
            emp_ids = [e.id for e in scoped_emps]
            year_start = date(target_year, 1, 1)
            year_end = date(target_year, 12, 31)

            ledger_entries = list(LeaveLedger.objects.filter(
                employee_id__in=emp_ids if emp_ids else [-1],
                leave_type__in=leave_types,
            ))
            pending_leaves = list(Leave.objects.filter(
                employee_id__in=emp_ids if emp_ids else [-1],
                leave_type_ref__in=leave_types,
                approval_status__in=['PENDING', 'CLARIFICATION'],
                from_date__year=target_year,
            ))

            # Map (emp_id, lt_id)
            led_map = defaultdict(lambda: {'opening': Decimal('0.00'), 'accrued': Decimal('0.00'), 'taken': Decimal('0.00'), 'avail': Decimal('0.00')})
            for le in ledger_entries:
                key = (le.employee_id, le.leave_type_id)
                if le.date <= year_end:
                    led_map[key]['avail'] += le.days
                if year_start <= le.date <= year_end:
                    if le.entry_type in ('OPENING', 'CARRY_FORWARD', 'ENTITLEMENT'):
                        led_map[key]['opening'] += le.days
                    elif le.entry_type in ('ACCRUAL', 'ADJUSTMENT'):
                        led_map[key]['accrued'] += le.days
                    elif le.entry_type == 'TAKEN':
                        led_map[key]['taken'] += abs(le.days)
                    elif le.entry_type == 'REVERSAL':
                        led_map[key]['taken'] -= le.days

            pend_map = defaultdict(Decimal)
            for lv in pending_leaves:
                pend_map[(lv.employee_id, lv.leave_type_ref_id)] += Decimal(str(lv.days or lv.no_of_days or 0))

            for e in scoped_emps:
                for lt in leave_types:
                    key = (e.id, lt.id)
                    m = led_map[key]
                    pend = pend_map[key]
                    avail = m['avail'] - pend
                    rows.append([
                        e.employee_code or '-',
                        e.name,
                        e.department.name if e.department else '-',
                        lt.name,
                        target_year,
                        f"{m['opening']:.2f}",
                        f"{m['accrued']:.2f}",
                        f"{max(m['taken'], Decimal('0.00')):.2f}",
                        f"{pend:.2f}",
                        f"{avail:.2f}",
                    ])
        else:
            user_ids = [e.user_id for e in scoped_emps]
            bal_map = {lb.user_id: lb for lb in LeaveBalance.objects.filter(user_id__in=user_ids)}
            taken_lvs = list(Leave.objects.filter(user_id__in=user_ids, from_date__year=target_year, approval_status__in=['APPROVED', 'PENDING']))
            taken_map = defaultdict(lambda: {'ANNUAL': Decimal('0'), 'SICK': Decimal('0'), 'ANNUAL_P': Decimal('0'), 'SICK_P': Decimal('0')})
            for lv in taken_lvs:
                is_ann = 'ANNUAL' in (lv.leave_type or '').upper()
                is_sick = 'SICK' in (lv.leave_type or '').upper()
                d_val = Decimal(str(lv.no_of_days or 0))
                if lv.approval_status == 'APPROVED':
                    if is_ann:
                        taken_map[lv.user_id]['ANNUAL'] += d_val
                    elif is_sick:
                        taken_map[lv.user_id]['SICK'] += d_val
                else:
                    if is_ann:
                        taken_map[lv.user_id]['ANNUAL_P'] += d_val
                    elif is_sick:
                        taken_map[lv.user_id]['SICK_P'] += d_val

            for e in scoped_emps:
                lb = bal_map.get(e.user_id)
                ann_open = Decimal(str(lb.annual_leave if lb else 0))
                sick_open = Decimal(str(lb.sick_leave if lb else 0))
                tm = taken_map[e.user_id]
                rows.append([
                    e.employee_code or '-', e.name, e.department.name if e.department else '-',
                    'Annual Leave', target_year, f"{ann_open:.2f}", '0.00',
                    f"{tm['ANNUAL']:.2f}", f"{tm['ANNUAL_P']:.2f}", f"{max(ann_open - tm['ANNUAL'] - tm['ANNUAL_P'], Decimal('0')):.2f}"
                ])
                rows.append([
                    e.employee_code or '-', e.name, e.department.name if e.department else '-',
                    'Sick Leave', target_year, f"{sick_open:.2f}", '0.00',
                    f"{tm['SICK']:.2f}", f"{tm['SICK_P']:.2f}", f"{max(sick_open - tm['SICK'] - tm['SICK_P'], Decimal('0')):.2f}"
                ])

    # 6. Leave History
    elif report_key == 'leave_history':
        scoped_emps = list(_get_scoped_employees_qs(user, role_key, dept_id=dept_id, branch_id=branch_id, emp_id=emp_id, include_inactive=True))
        user_ids = [e.user_id for e in scoped_emps]
        emp_ids = [e.id for e in scoped_emps]

        lv_qs = Leave.objects.filter(
            from_date__lte=end_date,
            to_date__gte=start_date,
        ).filter(
            Q(employee_id__in=emp_ids) | Q(employee__isnull=True, user_id__in=user_ids)
        ).select_related('user', 'employee__department', 'leave_type_ref', 'approver__user', 'decided_by').order_by('-from_date', '-id')

        if status_filter:
            lv_qs = lv_qs.filter(approval_status=status_filter)

        headers = ['Ref #', 'Code', 'Employee', 'Department', 'Leave Type', 'From Date', 'To Date', 'Days', 'Status', 'Decided By', 'Remarks']
        for lv in lv_qs:
            emp = lv.employee or getattr(lv.user, 'employee_profile', None)
            rows.append([
                lv.id,
                emp.employee_code if emp else '-',
                emp.name if emp else lv.user.username,
                emp.department.name if (emp and emp.department) else '-',
                lv.leave_type_ref.name if lv.leave_type_ref else lv.leave_type,
                lv.from_date.strftime('%d/%m/%Y'),
                lv.to_date.strftime('%d/%m/%Y'),
                f"{lv.days or lv.no_of_days}",
                lv.approval_status,
                (lv.decided_by.username if lv.decided_by else (lv.approver.user.username if lv.approver else '-')),
                lv.decision_remarks or lv.reason or '-',
            ])

    # 7. Overtime
    elif report_key == 'overtime':
        scoped_emps = list(_get_scoped_employees_qs(user, role_key, dept_id=dept_id, branch_id=branch_id, emp_id=emp_id))
        emp_ids = [e.id for e in scoped_emps]
        ts_approval_on = getattr(settings, 'HR_TIMESHEET_APPROVAL', False)

        headers = ['Date', 'Code', 'Employee', 'Department', 'OT Hours', 'Multiplier(s)', 'Approval Status']
        if show_salary:
            headers.append('OT Cost (AED)')

        total_ot_hours = Decimal('0.00')
        total_ot_cost = Decimal('0.00')

        ot_apps = list(OTApproval.objects.filter(
            employee_id__in=emp_ids if emp_ids else [-1],
            date__range=(start_date, end_date),
        ).select_related('employee__user', 'employee__department').order_by('-date', 'employee__user__first_name'))
        ot_app_map = {(o.employee_id, o.date): o for o in ot_apps}

        logs = list(EmployeeServiceLog.objects.filter(
            employee_id__in=emp_ids if emp_ids else [-1],
            date__range=(start_date, end_date),
        ).order_by('date', 'id'))
        logs_by_key = defaultdict(list)
        for l in logs:
            logs_by_key[(l.employee_id, l.date)].append(l)

        reg_rows = build_attendance_register(
            start_date, end_date,
            department=dept_id, branch=branch_id,
            employee_ids=emp_ids if emp_ids else [-1],
        ) if emp_ids else []

        # Merge OT from OTApproval + register rows with ot_hours > 0
        seen_keys = set()
        combined_items = []
        for o in ot_apps:
            seen_keys.add((o.employee_id, o.date))
            combined_items.append((o.date, o.employee, o.ot_hours, o.ot_cost, o.status))

        for r in reg_rows:
            emp = r['employee']
            ot_h = Decimal(str(r.get('ot_hours') or 0))
            if ot_h > 0 and (emp.id, r['date']) not in seen_keys:
                seen_keys.add((emp.id, r['date']))
                day_logs = logs_by_key.get((emp.id, r['date']), [])
                v_ot_cost = sum((l.ot_cost or Decimal('0.00')) for l in day_logs if not ts_approval_on or l.is_counting())
                o_ot_cost = calculate_office_ot_cost(emp.salary, [r], day_logs, employee=emp)
                est_cost = (v_ot_cost + o_ot_cost).quantize(Decimal('0.01'))
                combined_items.append((r['date'], emp, ot_h, est_cost, 'APPROVED' if not ts_approval_on else 'PENDING'))

        combined_items.sort(key=lambda x: (x[0], x[1].name.lower()), reverse=True)

        for d_val, emp, ot_h, ot_c, app_st in combined_items:
            if status_filter and app_st != status_filter:
                continue
            day_logs = logs_by_key.get((emp.id, d_val), [])
            mult_map = {
                'HOLIDAY': f"{OT_MULT_PUBLIC_HOLIDAY:.2f}x",
                'SUNDAY': f"{OT_MULT_SUNDAY:.2f}x",
                'WEEKDAY': f"{OT_MULT_NORMAL:.2f}x",
            }
            mults = sorted({mult_map.get(l.ot_type, f"{OT_MULT_NORMAL:.2f}x") for l in day_logs if getattr(l, 'ot_type', None)})
            mult_str = ', '.join(mults) if mults else ('1.50x' if d_val.weekday() == 6 else '1.25x')

            row = [
                d_val.strftime('%d/%m/%Y'),
                emp.employee_code or '-',
                emp.name,
                emp.department.name if emp.department else '-',
                f"{ot_h:.2f}",
                mult_str,
                app_st if ts_approval_on else 'Auto (Switch Off)',
            ]
            if show_salary:
                row.append(f"{ot_c:.2f}")
                total_ot_cost += Decimal(str(ot_c or 0))
            total_ot_hours += Decimal(str(ot_h or 0))
            rows.append(row)

        if rows:
            totals_row = ['TOTAL', '', '', '', f"{total_ot_hours:.2f}", '', '']
            if show_salary:
                totals_row.append(f"{total_ot_cost:.2f}")

    # 8. Vessel / Site Timesheets
    elif report_key == 'timesheets':
        scoped_emps = list(_get_scoped_employees_qs(user, role_key, dept_id=dept_id, branch_id=branch_id, emp_id=emp_id, include_inactive=True))
        emp_ids = [e.id for e in scoped_emps]

        ts_qs = EmployeeServiceLog.objects.filter(
            employee_id__in=emp_ids if emp_ids else [-1],
            date__range=(start_date, end_date),
        ).select_related('employee__user', 'employee__department', 'project', 'time_log__work_order').order_by('-date', 'employee__user__first_name', 'start_time')

        if status_filter:
            ts_qs = ts_qs.filter(approval_status=status_filter)

        headers = ['Date', 'Code', 'Employee', 'Department', 'Work Order', 'Project / Vessel', 'Start', 'Finish', 'Break (m)', 'Travel (m)', 'Hours', 'OT Hours', 'Status']
        tot_h = Decimal('0.00')
        tot_ot = Decimal('0.00')
        for l in ts_qs:
            wo_no = l.time_log.work_order.work_order_number if (l.time_log and l.time_log.work_order) else '-'
            proj_vessel = (l.project.name if l.project else '') or l.vessel_name or '-'
            brk = l.break_minutes or (l.time_log.break_minutes if l.time_log else 0)
            trv = l.travel_minutes or (l.time_log.travel_minutes if l.time_log else 0)
            hrs = l.total_hours or Decimal('0.00')
            ot = l.ot_hours or Decimal('0.00')
            tot_h += hrs
            tot_ot += ot
            rows.append([
                l.date.strftime('%d/%m/%Y'),
                l.employee.employee_code or '-',
                l.employee.name,
                l.employee.department.name if l.employee.department else '-',
                wo_no,
                proj_vessel,
                l.start_time.strftime('%H:%M') if l.start_time else '-',
                l.end_time.strftime('%H:%M') if l.end_time else '-',
                brk,
                trv,
                f"{hrs:.2f}",
                f"{ot:.2f}",
                l.approval_status,
            ])
        if rows:
            totals_row = ['TOTAL', '', '', '', '', '', '', '', '', '', f"{tot_h:.2f}", f"{tot_ot:.2f}", '']

    # 9. Document Expiry
    elif report_key == 'document_expiry':
        today = timezone.localdate()
        scoped_emps = list(_get_scoped_employees_qs(user, role_key, dept_id=dept_id, branch_id=branch_id, emp_id=emp_id))
        emp_ids = [e.id for e in scoped_emps]

        headers = ['Category', 'Owner / Employee', 'Department', 'Document Type', 'Title / No.', 'Issue Date', 'Expiry Date', 'Days Left', 'Status']
        emp_docs = EmployeeDocument.objects.filter(
            is_current=True,
            employee_id__in=emp_ids if emp_ids else [-1],
        ).select_related('employee__user', 'employee__department', 'doc_type').order_by('expiry_date', 'id')

        for doc in emp_docs:
            st = get_document_status(doc, as_of=today)
            if status_filter and st.get('bucket') != status_filter:
                continue
            rows.append([
                'Employee',
                doc.employee.name,
                doc.employee.department.name if doc.employee.department else '-',
                doc.doc_type.name if doc.doc_type else '-',
                doc.document_number or '-',
                doc.issue_date.strftime('%d/%m/%Y') if doc.issue_date else '-',
                doc.expiry_date.strftime('%d/%m/%Y') if doc.expiry_date else 'No Expiry',
                st['days'] if st['days'] is not None else '-',
                st['label'],
            ])

        # Company documents: HR / Admin ONLY (not manager)
        if role_key in ('admin', 'hr') and not dept_id and not branch_id and not emp_id:
            comp_docs = CompanyDocument.objects.filter(is_current=True).select_related('doc_type').order_by('expiry_date', 'id')
            for doc in comp_docs:
                st = get_document_status(doc, as_of=today)
                if status_filter and st.get('bucket') != status_filter:
                    continue
                rows.append([
                    'Company',
                    'Blessed Marine Services',
                    '-',
                    doc.doc_type.name if doc.doc_type else '-',
                    doc.title or doc.document_number or '-',
                    doc.issue_date.strftime('%d/%m/%Y') if doc.issue_date else '-',
                    doc.expiry_date.strftime('%d/%m/%Y') if doc.expiry_date else 'No Expiry',
                    st['days'] if st['days'] is not None else '-',
                    st['label'],
                ])

    # 10. Payroll Summary (can_view_salary only, never manager)
    elif report_key == 'payroll_summary':
        if not show_salary:
            raise PermissionDenied("Salary reports require salary-viewing permissions.")
        payrolls, _, _ = get_payroll_list_with_metrics(month_date)

        headers = ['Code', 'Employee', 'Department', 'Branch', 'Basic', 'Allowances', 'OT Amount', 'Gross', 'Absence Ded.', 'Other Ded./Adv.', 'Net Pay', 'Status']
        t_basic = Decimal('0.00')
        t_all = Decimal('0.00')
        t_ot = Decimal('0.00')
        t_gross = Decimal('0.00')
        t_abs = Decimal('0.00')
        t_ded = Decimal('0.00')
        t_net = Decimal('0.00')

        for p in payrolls:
            emp = p.employee
            if dept_id and emp.department_id != dept_id:
                continue
            if branch_id and emp.branch_id != branch_id:
                continue
            if emp_id and emp.id != emp_id:
                continue
            if status_filter and p.status != status_filter:
                continue

            eff_abs = p.effective_absence_deduction
            rows.append([
                emp.employee_code or '-',
                emp.name,
                emp.department.name if emp.department else '-',
                emp.branch.name if emp.branch else '-',
                f"{p.basic_salary or Decimal('0.00'):.2f}",
                f"{p.allowances or Decimal('0.00'):.2f}",
                f"{p.ot_amount or Decimal('0.00'):.2f}",
                f"{p.gross:.2f}",
                f"{eff_abs:.2f}",
                f"{p.deductions or Decimal('0.00'):.2f}",
                f"{p.net_pay:.2f}",
                p.status,
            ])
            t_basic += p.basic_salary or Decimal('0.00')
            t_all += p.allowances or Decimal('0.00')
            t_ot += p.ot_amount or Decimal('0.00')
            t_gross += p.gross
            t_abs += eff_abs
            t_ded += p.deductions or Decimal('0.00')
            t_net += p.net_pay

        if rows:
            totals_row = [
                'TOTAL', '', '', '',
                f"{t_basic:.2f}", f"{t_all:.2f}", f"{t_ot:.2f}",
                f"{t_gross:.2f}", f"{t_abs:.2f}", f"{t_ded:.2f}", f"{t_net:.2f}", '',
            ]

    return {
        'report_key': report_key,
        'title': meta['title'],
        'day_level': bool(meta.get('day_level')),
        'headers': headers,
        'rows': rows,
        'totals_row': totals_row,
        'period_label': period_label,
        'filters_label': filters_label,
        'total_count': len(rows),
    }


def export_report_excel(report_data, user):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = report_data['report_key'][:31]

    # Insert Logo in header area if available
    logo_path = finders.find('assets/images/reportlogo.png')
    text_col = 'A'
    if logo_path:
        try:
            img = XLImage(logo_path)
            img.height = 42
            img.width = 110
            ws.add_image(img, 'A1')
            text_col = 'C'
        except Exception:
            text_col = 'A'

    now_dt = timezone.localtime().strftime('%d/%m/%Y %H:%M')
    user_label = user.get_full_name() or user.username

    ws[f'{text_col}1'] = f"Blessed Marine Services — {report_data['title']}"
    ws[f'{text_col}1'].font = Font(size=13, bold=True, color='1F3A52')
    ws[f'{text_col}2'] = f"Period: {report_data['period_label']} | Filters: {report_data['filters_label']}"
    ws[f'{text_col}2'].font = Font(size=9, italic=True, color='334155')
    ws[f'{text_col}3'] = f"Generated: {now_dt} by {user_label} | Total Rows: {report_data['total_count']}"
    ws[f'{text_col}3'].font = Font(size=9, color='475569')

    start_row = 5
    header_fill = PatternFill(start_color='1F3A52', end_color='1F3A52', fill_type='solid')
    header_font = Font(bold=True, color='FFFFFF')
    thin = Side(border_style='thin', color='CBD5E1')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    headers = report_data['headers']
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=start_row, column=col_idx, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = border

    r_idx = start_row + 1
    for row in report_data['rows']:
        for col_idx, val in enumerate(row, 1):
            c = ws.cell(row=r_idx, column=col_idx, value=val)
            c.border = border
        r_idx += 1

    if report_data.get('totals_row'):
        tot_fill = PatternFill(start_color='E2E8F0', end_color='E2E8F0', fill_type='solid')
        tot_font = Font(bold=True, color='1F3A52')
        for col_idx, val in enumerate(report_data['totals_row'], 1):
            c = ws.cell(row=r_idx, column=col_idx, value=val)
            c.fill = tot_fill
            c.font = tot_font
            c.border = border

    for col in ws.columns:
        col_letter = col[0].column_letter
        max_len = max((len(str(c.value or '')) for c in col if c.row >= start_row), default=10)
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 42)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = f'attachment; filename="{report_data["report_key"]}.xlsx"'
    return resp


def export_report_pdf(report_data, user, print_mode=False):
    logo_path = finders.find('assets/images/reportlogo.png')
    logo_b64 = ''
    if logo_path:
        try:
            with open(logo_path, 'rb') as f:
                logo_b64 = base64.b64encode(f.read()).decode('ascii')
        except OSError:
            pass

    html_str = render_to_string('reports/report_pdf.html', {
        'logo_base64': logo_b64,
        'report_title': report_data['title'],
        'period_label': report_data['period_label'],
        'filters_label': report_data['filters_label'],
        'generated_at': timezone.localtime(),
        'generated_by': user.get_full_name() or user.username,
        'headers': report_data['headers'],
        'rows': report_data['rows'],
        'totals_row': report_data['totals_row'],
        'total_count': report_data['total_count'],
        'is_print_mode': print_mode,
    })

    if print_mode:
        return HttpResponse(html_str)

    pdf_bytes = HTML(string=html_str).write_pdf()
    resp = HttpResponse(pdf_bytes, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{report_data["report_key"]}.pdf"'
    return resp


import calendar
import io
import base64
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
import pytz
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import localtime
from weasyprint import HTML

from Admin.attendance_register import build_attendance_register, build_attendance_summary
from Admin.leave_service import is_leave_v2_active
from Admin.models import (
    Attendance,
    AttendanceCorrection,
    Branch,
    Department,
    Employee,
    EmployeeDocument,
    Holiday,
    Leave,
)
from Admin.office_staff_helpers import get_staff_attendance_context
from service_logs.models import EmployeeServiceLog


def _load_logo_base64():
    logo_path = finders.find('assets/images/reportlogo.png')
    if logo_path:
        try:
            with open(logo_path, 'rb') as f:
                raw = f.read()
                if raw:
                    return base64.b64encode(raw).decode('ascii')
        except OSError:
            pass
    return ''


def _export_dashboard_daily_excel(register_rows, target_date, dept_obj, branch_obj, user):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Daily Attendance {target_date.strftime('%Y-%m-%d')}"

    ws['A1'] = f"Daily Attendance Register - {target_date.strftime('%d %b %Y')}"
    ws['A1'].font = Font(size=14, bold=True, color='1F3A52')

    filters_desc = []
    if dept_obj:
        filters_desc.append(f"Department: {dept_obj.name}")
    if branch_obj:
        filters_desc.append(f"Branch: {branch_obj.name}")
    ws['A2'] = f"Filters: {', '.join(filters_desc) if filters_desc else 'All'} | Generated: {timezone.localtime().strftime('%d/%m/%Y %H:%M')} by {user.get_full_name() or user.username}"
    ws['A2'].font = Font(size=9, italic=True, color='475569')

    headers = [
        'Code', 'Employee', 'Department', 'Branch', 'Status', 'Location',
        'Punch In', 'Punch Out', 'Total Hours', 'OT Hours', 'Vessel / Job', 'Remarks',
    ]
    header_fill = PatternFill(start_color='1F3A52', end_color='1F3A52', fill_type='solid')
    header_font = Font(bold=True, color='FFFFFF')
    for col_idx, h in enumerate(headers, 1):
        c = ws.cell(row=4, column=col_idx, value=h)
        c.fill = header_fill
        c.font = header_font
        c.alignment = Alignment(horizontal='center')

    for idx, r in enumerate(register_rows, 5):
        emp = r['employee']
        ws.cell(row=idx, column=1, value=emp.employee_code or '')
        ws.cell(row=idx, column=2, value=r.get('name', ''))
        ws.cell(row=idx, column=3, value=emp.department.name if emp.department else '-')
        ws.cell(row=idx, column=4, value=emp.branch.name if emp.branch else '-')
        ws.cell(row=idx, column=5, value=r.get('status_label', ''))
        ws.cell(row=idx, column=6, value=r.get('location_label') or '-')
        ws.cell(row=idx, column=7, value=r['start'].strftime('%H:%M') if r.get('start') else '-')
        ws.cell(row=idx, column=8, value=r['end'].strftime('%H:%M') if r.get('end') else '-')
        ws.cell(row=idx, column=9, value=float(r.get('total_hours') or 0))
        ws.cell(row=idx, column=10, value=float(r.get('ot_hours') or 0))
        ws.cell(row=idx, column=11, value=', '.join(r.get('vessel_job') or []) or '-')
        ws.cell(row=idx, column=12, value=r.get('off_day_label') or '')

    for col in ws.columns:
        max_len = max((len(str(c.value or '')) for c in col), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = f'attachment; filename="hr_dashboard_daily_{target_date.strftime("%Y-%m-%d")}.xlsx"'
    return resp


def _export_dashboard_daily_pdf(register_rows, target_date, dept_obj, branch_obj, user):
    filters_desc = []
    if dept_obj:
        filters_desc.append(f"Department: {dept_obj.name}")
    if branch_obj:
        filters_desc.append(f"Branch: {branch_obj.name}")

    pdf_rows = []
    for r in register_rows:
        emp = r['employee']
        pdf_rows.append([
            emp.employee_code or '-',
            r.get('name', ''),
            emp.department.name if emp.department else '-',
            r.get('status_label', ''),
            r.get('location_label') or '-',
            r['start'].strftime('%H:%M') if r.get('start') else '-',
            r['end'].strftime('%H:%M') if r.get('end') else '-',
            f"{Decimal(str(r.get('total_hours') or 0)):.2f}",
            f"{Decimal(str(r.get('ot_hours') or 0)):.2f}",
        ])

    html_str = render_to_string('reports/report_pdf.html', {
        'logo_base64': _load_logo_base64(),
        'report_title': 'HR Dashboard — Daily Attendance',
        'period_label': target_date.strftime('%d %b %Y'),
        'filters_label': ', '.join(filters_desc) if filters_desc else 'All Departments & Branches',
        'generated_at': timezone.localtime(),
        'generated_by': user.get_full_name() or user.username,
        'headers': ['Code', 'Employee', 'Department', 'Status', 'Location', 'In', 'Out', 'Hours', 'OT'],
        'rows': pdf_rows,
        'total_count': len(pdf_rows),
    })
    pdf_bytes = HTML(string=html_str).write_pdf()
    resp = HttpResponse(pdf_bytes, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="hr_dashboard_daily_{target_date.strftime("%Y-%m-%d")}.pdf"'
    return resp


def build_hr_dashboard_context(request, role='hr'):
    today = timezone.localdate()
    date_param = (request.GET.get('date') or '').strip()
    try:
        target_date = datetime.strptime(date_param, '%Y-%m-%d').date() if date_param else today
    except ValueError:
        target_date = today

    dept_filter = (request.GET.get('department') or '').strip()
    branch_filter = (request.GET.get('branch') or '').strip()
    dept_id = int(dept_filter) if dept_filter.isdigit() else None
    branch_id = int(branch_filter) if branch_filter.isdigit() else None

    dept_obj = Department.objects.filter(pk=dept_id).first() if dept_id else None
    branch_obj = Branch.objects.filter(pk=branch_id).first() if branch_id else None

    # Role-specific target list URLs so links stay inside HR or Admin navigation
    if role == 'admin':
        emp_list_url = reverse('employee_list')
        reg_url = reverse('attendance_list_adminview')
        manage_leave_url = reverse('employee_manage_leave')
        att_status_url = reverse('admin_attendance_status')
        ts_approvals_url = reverse('timesheet_approvals_admin')
        docs_url = reverse('admin_documents_list')
        dashboard_url = reverse('admin_hr_dashboard')
    else:
        emp_list_url = reverse('hr_employee_list')
        reg_url = reverse('hr_attendance_list_view')
        manage_leave_url = reverse('hr_employee_manage_leave')
        att_status_url = reverse('hr_attendance_status')
        ts_approvals_url = reverse('timesheet_approvals_hr')
        docs_url = reverse('hr_documents_list')
        dashboard_url = reverse('hr-dashboard')

    d_str = target_date.strftime('%Y-%m-%d')
    dept_q = f"&department={dept_id}" if dept_id else ""
    branch_q = f"&branch={branch_id}" if branch_id else ""
    team_dept_q = f"&team_dept={dept_id}" if dept_id else ""
    team_branch_q = f"&team_branch={branch_id}" if branch_id else ""

    # 1. Single build_attendance_register call for target_date
    register_rows = build_attendance_register(
        target_date,
        target_date,
        department=dept_id,
        branch=branch_id,
    )
    # On a holiday / rest day with no work the register puts "Holiday" in Location too.
    # The Status column already says that, so Location is left blank here (screen, Excel, PDF).
    for r in register_rows:
        if r.get('location_label') == 'Holiday':
            r['location_label'] = ''

    # Counts from the single register call
    present_count = sum(1 for r in register_rows if r['status_code'] in ('OFFICE', 'VESSEL', 'OFFICE_VESSEL'))
    absent_count = sum(1 for r in register_rows if r['status_code'] == 'ABSENT')
    missing_punch_count = sum(1 for r in register_rows if r['status_code'] == 'MISSING_PUNCH')
    on_leave_count = sum(
        1 for r in register_rows
        if r['status_code'] in ('ANNUAL_LEAVE', 'SICK_LEAVE', 'PERSONAL_LEAVE', 'UNPAID_LEAVE')
    )
    vessel_count = sum(1 for r in register_rows if r['status_code'] in ('VESSEL', 'OFFICE_VESSEL'))
    rest_holiday_count = sum(
        1 for r in register_rows
        if r['status_code'] in ('REST', 'REST_DAY', 'SUNDAY', 'HOLIDAY')
    )

    # Card 1: Active staff count (matches employee_list?status=active)
    active_emp_qs = Employee.objects.filter(user__is_active=True)
    if dept_id:
        active_emp_qs = active_emp_qs.filter(department_id=dept_id)
    if branch_id:
        active_emp_qs = active_emp_qs.filter(branch_id=branch_id)
    active_staff_count = active_emp_qs.count()

    # Card 8: Pending leave requests (matches manage_leave?status=PENDING)
    viewer_emp = getattr(request.user, 'employee_profile', None)
    leave_v2_on = is_leave_v2_active()
    if leave_v2_on:
        pending_leave_qs = Leave.objects.filter(approval_status='PENDING').exclude(user=request.user)
        if viewer_emp:
            pending_leave_qs = pending_leave_qs.exclude(employee=viewer_emp)
        if dept_id:
            pending_leave_qs = pending_leave_qs.filter(employee__department_id=dept_id)
        if branch_id:
            pending_leave_qs = pending_leave_qs.filter(employee__branch_id=branch_id)
    else:
        pending_leave_qs = Leave.objects.filter(approval_status='PENDING')
        if dept_id:
            pending_leave_qs = pending_leave_qs.filter(user__employee_profile__department_id=dept_id)
        if branch_id:
            pending_leave_qs = pending_leave_qs.filter(user__employee_profile__branch_id=branch_id)
    pending_leave_count = pending_leave_qs.count()

    # Card 9: Pending corrections + pending timesheets
    corr_qs = AttendanceCorrection.objects.filter(status='PENDING')
    if dept_id:
        corr_qs = corr_qs.filter(employee__department_id=dept_id)
    if branch_id:
        corr_qs = corr_qs.filter(employee__branch_id=branch_id)
    pending_corrections_count = corr_qs.count()

    ts_qs = EmployeeServiceLog.objects.filter(approval_status='SUBMITTED')
    if dept_id:
        ts_qs = ts_qs.filter(employee__department_id=dept_id)
    if branch_id:
        ts_qs = ts_qs.filter(employee__branch_id=branch_id)
    pending_timesheets_count = ts_qs.count()

    # Card 10: Documents expired / <=30 days (matches documents_list?tab=employee&status=expired,30)
    doc_qs = EmployeeDocument.objects.filter(
        is_current=True,
        employee__user__is_active=True,
        doc_type__has_expiry=True,
        expiry_date__isnull=False,
        expiry_date__lte=today + timedelta(days=30),
    )
    if dept_id:
        doc_qs = doc_qs.filter(employee__department_id=dept_id)
    if branch_id:
        doc_qs = doc_qs.filter(employee__branch_id=branch_id)
    docs_expiring_count = doc_qs.count()

    engine_v2_on = bool(getattr(settings, 'HR_ATTENDANCE_ENGINE_V2', False))
    ts_approval_on = bool(getattr(settings, 'HR_TIMESHEET_APPROVAL', False))

    cards = {
        'active_staff': {
            'count': active_staff_count,
            'url': f"{emp_list_url}?status=active{dept_q}{branch_q}",
        },
        'present_today': {
            'count': present_count,
            'url': f"{reg_url}?start={d_str}&end={d_str}&status=OFFICE,VESSEL,OFFICE_VESSEL{dept_q}{branch_q}",
        },
        'absent': {
            'count': absent_count,
            'url': f"{reg_url}?start={d_str}&end={d_str}&status=ABSENT{dept_q}{branch_q}",
        },
        'missing_punch': {
            'visible': engine_v2_on,
            'count': missing_punch_count,
            'url': f"{reg_url}?start={d_str}&end={d_str}&status=MISSING_PUNCH{dept_q}{branch_q}",
        },
        'on_leave': {
            'count': on_leave_count,
            'url': f"{reg_url}?start={d_str}&end={d_str}&status=ANNUAL_LEAVE,SICK_LEAVE,PERSONAL_LEAVE,UNPAID_LEAVE{dept_q}{branch_q}",
        },
        'vessel': {
            'count': vessel_count,
            'url': f"{reg_url}?start={d_str}&end={d_str}&status=VESSEL,OFFICE_VESSEL{dept_q}{branch_q}",
        },
        'rest_holiday': {
            'count': rest_holiday_count,
            'url': f"{reg_url}?start={d_str}&end={d_str}&status=REST,REST_DAY,SUNDAY,HOLIDAY{dept_q}{branch_q}",
        },
        'pending_leave': {
            'count': pending_leave_count,
            'url': f"{manage_leave_url}?status=PENDING{dept_q}{branch_q}",
        },
        'pending_corrections': {
            'count': pending_corrections_count,
            'url': f"{att_status_url}?tab=team_requests&team_status=PENDING{team_dept_q}{team_branch_q}",
        },
        'pending_timesheets': {
            'visible': ts_approval_on,
            'count': pending_timesheets_count,
            'url': f"{ts_approvals_url}?status=SUBMITTED{dept_q}{branch_q}",
        },
        'docs_expiring': {
            'count': docs_expiring_count,
            'url': f"{docs_url}?tab=employee&status=expired,30{dept_q}{branch_q}",
        },
    }

    pending_sub_items = [
        {
            'key': 'pending-corrections',
            'label': 'Corrections',
            'count': pending_corrections_count,
            'url': cards['pending_corrections']['url'],
            'color': '#f97316',
        },
    ]
    if ts_approval_on:
        pending_sub_items.append({
            'key': 'pending-timesheets',
            'label': 'Timesheets',
            'count': pending_timesheets_count,
            'url': cards['pending_timesheets']['url'],
            'color': '#ea580c',
        })

    cards_list = [
        {
            'type': 'single',
            'key': 'active-staff',
            'label': 'Active Staff',
            'count': cards['active_staff']['count'],
            'url': cards['active_staff']['url'],
            'border_class': 'border-primary',
            'text_class': 'text-primary',
        },
        {
            'type': 'single',
            'key': 'present-today',
            'label': 'Present',
            'count': cards['present_today']['count'],
            'url': cards['present_today']['url'],
            'border_class': 'border-success',
            'text_class': 'text-success',
        },
        {
            'type': 'single',
            'key': 'absent',
            'label': 'Absent',
            'count': cards['absent']['count'],
            'url': cards['absent']['url'],
            'border_class': 'border-danger',
            'text_class': 'text-danger',
        },
    ]
    if engine_v2_on:
        cards_list.append({
            'type': 'single',
            'key': 'missing-punch',
            'label': 'Missing Punch',
            'count': cards['missing_punch']['count'],
            'url': cards['missing_punch']['url'],
            'border_class': 'border-warning',
            'text_class': 'text-warning',
        })
    cards_list.extend([
        {
            'type': 'single',
            'key': 'on-leave',
            'label': 'On Leave',
            'count': cards['on_leave']['count'],
            'url': cards['on_leave']['url'],
            'border_class': 'border-info',
            'text_class': 'text-info',
        },
        {
            'type': 'single',
            'key': 'vessel',
            'label': 'Vessel / Site',
            'count': cards['vessel']['count'],
            'url': cards['vessel']['url'],
            'color': '#6366f1',
        },
        {
            'type': 'single',
            'key': 'rest-holiday',
            'label': 'Rest Day / Holiday',
            'count': cards['rest_holiday']['count'],
            'url': cards['rest_holiday']['url'],
            'border_class': 'border-secondary',
            'text_class': 'text-secondary',
        },
        {
            'type': 'single',
            'key': 'pending-leave',
            'label': 'Pending Leave Requests',
            'count': cards['pending_leave']['count'],
            'url': cards['pending_leave']['url'],
            'color': '#8b5cf6',
        },
        {
            'type': 'dual',
            'key': 'pending-approvals',
            'label': 'Pending Approvals',
            'color': '#f97316',
            'sub_items': pending_sub_items,
        },
        {
            'type': 'single',
            'key': 'docs-expiring',
            'label': 'Docs Expired / ≤30d',
            'count': cards['docs_expiring']['count'],
            'url': cards['docs_expiring']['url'],
            'border_class': 'border-danger',
            'text_class': 'text-danger',
        },
    ])

    # Panel 1: My Attendance (only when logged-in user has an Employee profile)
    local_tz = pytz.timezone('Asia/Dubai')
    my_att_ctx = None
    today_attendance = None
    last_punch_in = "Not Punched In"
    last_punch_out = "Not Punched Out"
    my_month_summary = None
    if viewer_emp:
        my_att_ctx = get_staff_attendance_context(request, viewer_emp)
        today_attendance = my_att_ctx.get('user_attendance')
        if today_attendance and today_attendance.login_time:
            last_punch_in = localtime(today_attendance.login_time).astimezone(local_tz).strftime('%I:%M %p')
        if today_attendance and today_attendance.log_out_time:
            last_punch_out = localtime(today_attendance.log_out_time).astimezone(local_tz).strftime('%I:%M %p')

        cur_m_start = today.replace(day=1)
        cur_m_end = date(today.year, today.month, monthrange(today.year, today.month)[1])
        my_month_rows = build_attendance_register(cur_m_start, cur_m_end, employee_id=viewer_emp.id)
        sum_rows, _ = build_attendance_summary(my_month_rows)
        my_month_summary = sum_rows[0] if sum_rows else None

    # Panel 2 & 3: Leave summary (month of target_date) + Mini Calendar
    m_start = target_date.replace(day=1)
    _, num_days = monthrange(target_date.year, target_date.month)
    m_end = date(target_date.year, target_date.month, num_days)

    month_leaves_qs = Leave.objects.filter(
        from_date__lte=m_end,
        to_date__gte=m_start,
        approval_status__in=['APPROVED', 'PENDING'],
    ).select_related('leave_type_ref')
    if dept_id:
        month_leaves_qs = month_leaves_qs.filter(
            Q(employee__department_id=dept_id) | Q(employee__isnull=True, user__employee_profile__department_id=dept_id)
        )
    if branch_id:
        month_leaves_qs = month_leaves_qs.filter(
            Q(employee__branch_id=branch_id) | Q(employee__isnull=True, user__employee_profile__branch_id=branch_id)
        )
    month_leaves = list(month_leaves_qs)

    leave_by_type = defaultdict(lambda: {'type': '', 'approved_count': 0, 'approved_days': Decimal('0.0'), 'pending_count': 0, 'pending_days': Decimal('0.0')})
    leave_daily_counts = defaultdict(int)

    for lv in month_leaves:
        t_label = lv.leave_type_ref.name if lv.leave_type_ref else (lv.leave_type or 'Leave')
        entry = leave_by_type[t_label]
        entry['type'] = t_label
        days_val = Decimal(str(lv.days or lv.no_of_days or 0))
        if lv.approval_status == 'APPROVED':
            entry['approved_count'] += 1
            entry['approved_days'] += days_val
            span_start = max(lv.from_date, m_start)
            span_end = min(lv.to_date, m_end)
            cur = span_start
            while cur <= span_end:
                leave_daily_counts[cur] += 1
                cur += timedelta(days=1)
        elif lv.approval_status == 'PENDING':
            entry['pending_count'] += 1
            entry['pending_days'] += days_val

    leave_summary_rows = sorted(leave_by_type.values(), key=lambda x: x['type'])

    # Mini calendar holidays
    hol_qs = Holiday.objects.filter(is_active=True, date__range=(m_start, m_end))
    if branch_id:
        hol_qs = hol_qs.filter(Q(branch_id=branch_id) | Q(branch__isnull=True))
    holidays_map = {}
    for h in hol_qs:
        holidays_map[h.date] = h.description

    cal = calendar.Calendar(firstweekday=6)  # Sunday first
    calendar_weeks = []
    for week in cal.monthdatescalendar(target_date.year, target_date.month):
        week_cells = []
        for d in week:
            in_month = (d.month == target_date.month)
            d_iso = d.strftime('%Y-%m-%d')
            week_cells.append({
                'date': d,
                'day': d.day,
                'in_month': in_month,
                'is_today': (d == today),
                'is_selected': (d == target_date),
                'holiday': holidays_map.get(d, ''),
                'leave_count': leave_daily_counts.get(d, 0) if in_month else 0,
                'register_url': f"{reg_url}?start={d_iso}&end={d_iso}{dept_q}{branch_q}",
            })
        calendar_weeks.append(week_cells)

    # Panel 4: Today table (25 per page)
    paginator = Paginator(register_rows, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    departments = Department.objects.filter(is_active=True).order_by('sort_order', 'name')
    branches = Branch.objects.filter(is_active=True).order_by('sort_order', 'name')

    return {
        'role': role,
        'target_date': target_date,
        'selected_date': d_str,
        'selected_department': dept_filter,
        'selected_branch': branch_filter,
        'dept_obj': dept_obj,
        'branch_obj': branch_obj,
        'departments': departments,
        'branches': branches,
        'cards': cards,
        'cards_list': cards_list,
        'register_rows': register_rows,
        'page_obj': page_obj,
        'viewer_emp': viewer_emp,
        'user_attendance': today_attendance,
        'last_punch_in': last_punch_in,
        'last_punch_out': last_punch_out,
        'my_att_ctx': my_att_ctx,
        'my_month_summary': my_month_summary,
        'leave_summary_rows': leave_summary_rows,
        'calendar_weeks': calendar_weeks,
        'calendar_month_label': m_start.strftime('%B %Y'),
        'dashboard_url': dashboard_url,
        'reset_url': dashboard_url,
        'current_time': timezone.now().astimezone(local_tz).strftime('%I:%M %p, %d %b %Y'),
        'engine_v2_on': engine_v2_on,
    }


def render_hr_dashboard(request, role='hr', base_template='hr/hrbase.html'):
    ctx = build_hr_dashboard_context(request, role=role)
    export_fmt = (request.GET.get('export') or '').strip().lower()
    if export_fmt == 'excel':
        return _export_dashboard_daily_excel(
            ctx['register_rows'], ctx['target_date'], ctx['dept_obj'], ctx['branch_obj'], request.user
        )
    elif export_fmt == 'pdf':
        return _export_dashboard_daily_pdf(
            ctx['register_rows'], ctx['target_date'], ctx['dept_obj'], ctx['branch_obj'], request.user
        )
    ctx['base_template'] = base_template
    return render(request, 'hr/dashboard.html', ctx)


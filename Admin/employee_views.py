import io
from datetime import date, timedelta
from decimal import Decimal
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from weasyprint import HTML

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Q, Prefetch
from django.http import HttpResponse, HttpResponseForbidden, Http404
from django.shortcuts import render, get_object_or_404, redirect
from django.template.loader import render_to_string
from django.utils import timezone

from Admin.models import (
    Employee, Department, Branch, DutySchedule,
    DocumentType, EmployeeDocument, EmployeeChangeLog,
    AttendanceCorrection, Leave
)
from Admin.permissions import can_view_salary
from Admin.document_helpers import get_document_status, get_employee_overall_doc_badge
from Admin.attendance_register import build_attendance_register
from Admin.leave_service import get_employee_leave_summary


def render_employee_list(request, role, base_template):
    """
    Shared employee list view for Admin, HR, and Manager.
    - HR / Admin see all employees.
    - Manager sees only direct reports (reporting_manager == user.employee_profile).
    """
    user = request.user
    viewer_emp = getattr(user, 'employee_profile', None)

    # Permission check for role
    if role == 'manager':
        if not viewer_emp or not viewer_emp.is_manager:
            return HttpResponseForbidden("You are not authorized to view the manager employee list.")
        base_qs = Employee.objects.filter(reporting_manager=viewer_emp)
    elif role == 'hr':
        if not (user.is_superuser or (viewer_emp and viewer_emp.is_hr)):
            return HttpResponseForbidden("You are not authorized to view the HR employee list.")
        base_qs = Employee.objects.all()
    elif role == 'admin':
        if not (user.is_staff or user.is_superuser):
            return HttpResponseForbidden("You are not authorized to view the employee list.")
        base_qs = Employee.objects.all()
    else:
        base_qs = Employee.objects.all()

    qs = base_qs.select_related(
        'user', 'department', 'branch', 'reporting_manager__user'
    ).prefetch_related(
        Prefetch(
            'documents',
            queryset=EmployeeDocument.objects.filter(is_current=True).select_related('doc_type'),
            to_attr='prefetched_current_docs'
        )
    )

    # Filter Bar
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(user__first_name__icontains=q) |
            Q(user__last_name__icontains=q) |
            Q(user__username__icontains=q) |
            Q(employee_code__icontains=q) |
            Q(personal_email__icontains=q) |
            Q(company_email__icontains=q) |
            Q(phone_number__icontains=q) |
            Q(rank__icontains=q)
        )

    dept_id = request.GET.get('department')
    if dept_id:
        qs = qs.filter(department_id=dept_id)

    branch_id = request.GET.get('branch')
    if branch_id:
        qs = qs.filter(branch_id=branch_id)

    emp_role = request.GET.get('role')
    if emp_role == 'employee':
        qs = qs.filter(is_employee=True)
    elif emp_role == 'manager':
        qs = qs.filter(is_manager=True)
    elif emp_role == 'hr':
        qs = qs.filter(is_hr=True)
    elif emp_role == 'accountant':
        qs = qs.filter(is_accountant=True)
    elif emp_role == 'administration':
        qs = qs.filter(is_administration=True)

    emp_status = request.GET.get('employment_status')
    if emp_status:
        qs = qs.filter(employment_status=emp_status)

    work_type = request.GET.get('work_type')
    if work_type:
        qs = qs.filter(work_type=work_type)

    status_filter = request.GET.get('status', 'active')
    if status_filter == 'inactive':
        qs = qs.filter(user__is_active=False)
    elif status_filter == 'all':
        pass
    else:
        status_filter = 'active'
        qs = qs.filter(user__is_active=True)

    # Reporting manager (for HR/Admin only)
    if role != 'manager':
        mgr_id = request.GET.get('reporting_manager')
        if mgr_id:
            qs = qs.filter(reporting_manager_id=mgr_id)

    joined_from = request.GET.get('joined_from')
    if joined_from:
        qs = qs.filter(date_of_join__gte=joined_from)

    joined_to = request.GET.get('joined_to')
    if joined_to:
        qs = qs.filter(date_of_join__lte=joined_to)

    contract_ending = request.GET.get('contract_ending')
    if contract_ending in ('30', '60', '90'):
        today = timezone.localdate()
        max_end = today + timedelta(days=int(contract_ending))
        qs = qs.filter(contract_end_date__gte=today, contract_end_date__lte=max_end)

    # Sorting
    sort = request.GET.get('sort', 'code')
    order = request.GET.get('order', 'asc')
    order_prefix = '-' if order == 'desc' else ''

    sort_field_map = {
        'code': 'employee_code',
        'name': 'user__first_name',
        'department': 'department__name',
        'date_of_join': 'date_of_join',
    }
    sort_field = sort_field_map.get(sort, 'employee_code')
    qs = qs.order_by(f"{order_prefix}{sort_field}", '-id')

    # Exports (HR / Admin only)
    export_format = request.GET.get('export')
    if export_format in ('excel', 'pdf'):
        if role == 'manager':
            return HttpResponseForbidden("Export is only permitted for HR and Admin.")

        if export_format == 'excel':
            return _export_employees_excel(request, qs)
        elif export_format == 'pdf':
            return _export_employees_pdf(request, qs)

    # Pagination: 25 / page
    paginator = Paginator(qs, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Compute document badge for employees on this page
    required_types = list(DocumentType.objects.filter(
        category='EMPLOYEE', is_required=True, is_active=True
    ))
    for emp in page_obj:
        curr_docs = getattr(emp, 'prefetched_current_docs', None)
        emp.doc_badge = get_employee_overall_doc_badge(
            emp, required_types=required_types, current_docs=curr_docs
        )

    context = {
        'page_obj': page_obj,
        'role': role,
        'base_template': base_template,
        'can_view_salary': can_view_salary(user),
        'departments': Department.objects.filter(is_active=True),
        'branches': Branch.objects.filter(is_active=True),
        'reporting_managers': Employee.objects.filter(user__is_active=True).select_related('user'),
        # Active filter states
        'q': q,
        'selected_dept': dept_id,
        'selected_branch': branch_id,
        'selected_role': emp_role,
        'selected_status': emp_status,
        'selected_work_type': work_type,
        'status_filter': status_filter,
        'selected_manager': request.GET.get('reporting_manager', ''),
        'joined_from': joined_from or '',
        'joined_to': joined_to or '',
        'contract_ending': contract_ending or '',
        'sort': sort,
        'order': order,
    }
    return render(request, 'employees/employee_list.html', context)


def render_employee_profile(request, employee_id, role, base_template):
    """
    Shared employee profile view for Admin, HR, and Manager (7 tabs).
    """
    user = request.user
    viewer_emp = getattr(user, 'employee_profile', None)

    employee = get_object_or_404(
        Employee.objects.select_related('user', 'department', 'branch', 'reporting_manager__user'),
        pk=employee_id
    )

    # Scoping check for manager
    if role == 'manager':
        if not viewer_emp or employee.reporting_manager_id != viewer_emp.id:
            return HttpResponseForbidden("You are not authorized to view this employee's profile.")

    # Tab selection
    active_tab = request.GET.get('tab', 'overview').lower()
    valid_tabs = ['overview', 'employment', 'attendance', 'leave', 'salary', 'documents', 'history']
    if active_tab not in valid_tabs:
        active_tab = 'overview'

    user_can_view_salary = can_view_salary(user)

    # STRICT: 403 if unauthorized user requests salary tab
    if active_tab == 'salary' and not user_can_view_salary:
        return HttpResponseForbidden("You are not authorized to view salary details.")

    # Calculate age
    age = None
    if employee.date_of_birth:
        today = timezone.localdate()
        dob = employee.date_of_birth
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    context = {
        'employee': employee,
        'role': role,
        'base_template': base_template,
        'active_tab': active_tab,
        'can_view_salary': user_can_view_salary,
        'age': age,
    }

    # Tab 2: Employment details
    if active_tab == 'employment':
        context['current_schedule'] = employee.current_duty_schedule
        context['current_rest_rule'] = employee.current_rest_day_rule

    # Tab 3: Attendance details
    elif active_tab == 'attendance':
        from calendar import monthrange
        from Admin.attendance_register import build_attendance_summary

        today = timezone.localdate()
        month_param = request.GET.get('month')
        if month_param:
            try:
                parts = month_param.split('-')
                att_year, att_month = int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                att_year, att_month = today.year, today.month
        else:
            att_year, att_month = today.year, today.month

        selected_month_str = f"{att_year:04d}-{att_month:02d}"
        _, num_days = monthrange(att_year, att_month)
        month_start = date(att_year, att_month, 1)
        month_end = date(att_year, att_month, num_days)

        emp_rows = build_attendance_register(month_start, month_end, employee_ids=[employee.id])
        emp_rows.sort(key=lambda r: r['date'])
        summary_rows, _ = build_attendance_summary(emp_rows)
        emp_summary = summary_rows[0] if summary_rows else {}

        emp_att_data = {
            'summary': {
                'present': emp_summary.get('present_days', 0),
                'rest': emp_summary.get('rest_days', 0),
                'absent': emp_summary.get('absent_days', 0),
                'missing_punch': emp_summary.get('missing_punch_days', 0),
                'vessel_hours': emp_summary.get('vessel_hours', '0.0'),
                'ot_hours': emp_summary.get('gross_ot_hours', '0.0'),
            },
            'daily_days': [
                {
                    'date': r['date'],
                    'code': r['status_code'],
                    'status_label': r['status_label'],
                    'punch_in': r['start'].strftime('%H:%M') if r.get('start') else '—',
                    'punch_out': r['end'].strftime('%H:%M') if r.get('end') else '—',
                    'office_hours': r.get('total_hours', 0),
                    'vessel_hours': r.get('vessel_hours', 0),
                    'ot_hours': r.get('ot_hours', 0),
                    'notes': r.get('location_label', ''),
                }
                for r in emp_rows
            ]
        }

        context.update({
            'selected_month': selected_month_str,
            'att_year': att_year,
            'att_month': att_month,
            'emp_att_data': emp_att_data,
        })

    # Tab 4: Leave details
    elif active_tab == 'leave':
        curr_year = timezone.localdate().year
        leave_summary = get_employee_leave_summary(employee, year=curr_year)
        one_year_ago = timezone.localdate() - timedelta(days=365)
        leave_history = Leave.objects.filter(
            user=employee.user,
            from_date__gte=one_year_ago
        ).order_by('-created_at')

        context.update({
            'leave_year': curr_year,
            'leave_summary': leave_summary,
            'leave_history': leave_history,
        })

    # Tab 5: Salary details (can_view_salary only)
    elif active_tab == 'salary' and user_can_view_salary:
        context.update({
            'current_salary': employee.current_salary_structure,
            'upcoming_salary': employee.upcoming_salary_structure,
            'salary_history': employee.salary_structures.all().order_by('-effective_from'),
            'salary_increments': employee.salary_increments.all().select_related('approved_by').order_by('-effective_from', '-created_at'),
            'salary_changelogs': EmployeeChangeLog.objects.filter(
                employee=employee,
                field__in=['basic', 'housing_allowance', 'transport_allowance', 'other_allowances', 'total', 'salary']
            ).order_by('-changed_at'),
        })

    # Tab 6: Documents details
    elif active_tab == 'documents':
        curr_docs = list(EmployeeDocument.objects.filter(
            employee=employee, is_current=True
        ).select_related('doc_type', 'created_by').order_by('doc_type__sort_order', 'doc_type__name'))

        prev_docs = list(EmployeeDocument.objects.filter(
            employee=employee, is_current=False
        ).select_related('doc_type', 'created_by').order_by('-created_at'))

        can_manage_docs = user.is_superuser or (viewer_emp and viewer_emp.is_hr)
        can_download_docs = can_manage_docs or (employee.user_id == user.id)

        context.update({
            'current_documents': curr_docs,
            'previous_documents': prev_docs,
            'can_manage_docs': can_manage_docs,
            'can_download_docs': can_download_docs,
        })

    # Tab 7: History timeline
    elif active_tab == 'history':
        history_filter = request.GET.get('history_type', 'all')
        timeline_items = []

        # 1. Profile change logs
        if history_filter in ('all', 'profile'):
            profile_logs = EmployeeChangeLog.objects.filter(employee=employee)
            if not user_can_view_salary:
                profile_logs = profile_logs.exclude(
                    field__in=['basic', 'housing_allowance', 'transport_allowance', 'other_allowances', 'total', 'salary']
                )
            for log in profile_logs:
                timeline_items.append({
                    'type': 'profile',
                    'timestamp': log.changed_at,
                    'title': f"Field '{log.field}' changed",
                    'description': f"Changed from '{log.old_value or 'None'}' to '{log.new_value or 'None'}'",
                    'by': log.changed_by.get_full_name() if log.changed_by else (log.source or 'System'),
                    'badge_class': 'badge-secondary',
                })

        def _user_label(u):
            return (u.get_full_name() or u.username) if u else ''

        # 2. Attendance corrections
        if history_filter in ('all', 'attendance'):
            corrections = AttendanceCorrection.objects.filter(employee=employee).select_related('decided_by', 'submitted_by')
            for c in corrections:
                desc = f"{c.date:%d/%m/%Y} - {c.get_correction_type_display()}: {c.reason}"
                if c.remarks:
                    desc += f" ({c.remarks})"
                timeline_items.append({
                    'type': 'attendance',
                    'timestamp': c.decided_at or c.created_at,
                    'title': f"Attendance Correction: {c.get_status_display()}",
                    'description': desc,
                    'by': _user_label(c.decided_by) or 'Pending decision',
                    'badge_class': 'badge-info' if c.status == 'PENDING' else ('badge-success' if c.status == 'APPROVED' else 'badge-danger'),
                })

        # 3. Leave applications
        if history_filter in ('all', 'leave'):
            leaves = Leave.objects.filter(user=employee.user).select_related('approver__user', 'decided_by')
            for l in leaves:
                days = l.days if l.days is not None else l.no_of_days
                days_str = f"{float(days):g}" if days is not None else '-'
                if l.decided_by:
                    by = _user_label(l.decided_by)
                elif l.approver:
                    by = l.approver.name
                else:
                    by = 'Manager / HR'
                timeline_items.append({
                    'type': 'leave',
                    'timestamp': l.decided_at or l.updated_at or l.created_at or timezone.now(),
                    'title': f"Leave Request: {l.get_leave_type_display()}",
                    'description': f"{l.from_date:%d/%m/%Y} to {l.to_date:%d/%m/%Y} ({days_str} days) - {l.get_approval_status_display()}",
                    'by': by,
                    'badge_class': 'badge-success' if l.approval_status == 'APPROVED' else ('badge-danger' if l.approval_status == 'REJECTED' else 'badge-warning'),
                })

        # 4. Timesheet approvals (latest 50 decided or submitted service logs)
        if history_filter in ('all', 'timesheet'):
            from service_logs.models import EmployeeServiceLog
            logs = (EmployeeServiceLog.objects
                    .filter(employee=employee)
                    .exclude(approval_status='DRAFT')
                    .select_related('decided_by', 'time_log__work_order', 'project')
                    .order_by('-date', '-id')[:50])
            for s in logs:
                if s.time_log and s.time_log.work_order:
                    ref = s.time_log.work_order.work_order_number
                else:
                    ref = s.service_reference or (s.project.name if s.project else '')
                timeline_items.append({
                    'type': 'timesheet',
                    'timestamp': s.decided_at or s.submitted_at or s.created_at,
                    'title': f"Timesheet: {s.date:%d/%m/%Y} ({s.total_hours or 0} hrs)",
                    'description': f"Status: {s.get_approval_status_display()}" + (f" - {ref}" if ref else ''),
                    'by': _user_label(s.decided_by) or 'Supervisor',
                    'badge_class': 'badge-success' if s.approval_status == 'APPROVED' else ('badge-danger' if s.approval_status == 'REJECTED' else 'badge-secondary'),
                })

        # Sort all timeline items newest first
        timeline_items.sort(key=lambda x: x['timestamp'] or timezone.now(), reverse=True)
        context['timeline_items'] = timeline_items
        context['history_filter'] = history_filter

    return render(request, 'employees/employee_profile.html', context)


def _export_employees_excel(request, qs):
    """
    Exports filtered employees list to Excel.
    Salary columns are strictly included ONLY if can_view_salary(request.user).
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Employees"

    show_salary = can_view_salary(request.user)

    headers = [
        "Code", "Name", "Personal Email", "Company Email", "Phone",
        "Department", "Branch", "Role", "Designation / Rank",
        "Work Type", "Employment Status", "Date of Joining", "Contract End Date"
    ]
    if show_salary:
        headers.extend(["Basic", "Housing", "Transport", "Other", "Total Salary"])

    header_fill = PatternFill(start_color="1F3A52", end_color="1F3A52", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")

    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for emp in qs:
        struct = emp.current_salary_structure if show_salary else None
        row = [
            emp.employee_code or f"AMMS-{emp.id:04d}",
            emp.user.get_full_name() or emp.user.username,
            emp.personal_email or emp.user.email or "",
            emp.company_email or "",
            emp.phone_number or "",
            emp.department.name if emp.department else "",
            emp.branch.name if emp.branch else "",
            emp.get_role(),
            emp.rank or "",
            emp.get_work_type_display(),
            emp.get_employment_status_display(),
            emp.date_of_join.strftime('%Y-%m-%d') if emp.date_of_join else "",
            emp.contract_end_date.strftime('%Y-%m-%d') if emp.contract_end_date else "",
        ]
        if show_salary:
            if struct:
                row.extend([
                    float(struct.basic),
                    float(struct.housing_allowance),
                    float(struct.transport_allowance),
                    float(struct.other_allowances),
                    float(struct.total),
                ])
            else:
                sal = float(emp.salary or Decimal('0.00'))
                row.extend([sal, 0.0, 0.0, 0.0, sal])

        ws.append(row)

    # Auto-adjust column widths
    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"employees_{timezone.localdate().strftime('%Y%m%d')}.xlsx"
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _export_employees_pdf(request, qs):
    """
    Exports filtered employees roster to landscape PDF (no salary columns).
    """
    html_str = render_to_string('employees/employee_roster_pdf.html', {
        'employees': qs,
        'generated_at': timezone.now(),
        'generated_by': request.user.get_full_name() or request.user.username,
    })
    pdf_bytes = HTML(string=html_str).write_pdf()
    filename = f"employee_roster_{timezone.localdate().strftime('%Y%m%d')}.pdf"
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response

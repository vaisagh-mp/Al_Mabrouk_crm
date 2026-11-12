from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from Admin.models import Branch, Department
from Admin.notifications import get_base_template_for_user
from Admin.reports_center import (
    REPORT_META_MAP,
    _get_scoped_employees_qs,
    build_report_data,
    export_report_excel,
    export_report_pdf,
    get_allowed_reports_for_role,
    get_viewer_report_role,
    parse_report_filters,
)


@login_required
def reports_center_view(request, role=None):
    """
    Unified Reports Center view for HR, Admin, Accountant (salary reports only), and Manager (direct reports only, no salary).
    """
    role_key = get_viewer_report_role(request.user, requested_role=role)
    allowed_reports = get_allowed_reports_for_role(role_key)
    allowed_keys = [k for k, _ in allowed_reports]

    if not allowed_keys:
        raise PermissionDenied("No reports available for your role.")

    default_report = allowed_keys[0]
    selected_report = (request.GET.get('report') or default_report).strip()
    if selected_report not in REPORT_META_MAP:
        selected_report = default_report
    if selected_report not in allowed_keys:
        raise PermissionDenied("You do not have permission to access this report.")

    filters = parse_report_filters(request)
    report_data = build_report_data(selected_report, filters, request.user, role_key)
    meta = REPORT_META_MAP[selected_report]

    export_fmt = (request.GET.get('export') or '').strip().lower()
    days_span = (filters['end_date'] - filters['start_date']).days + 1
    pdf_too_wide_days = bool(meta.get('day_level') and days_span > 31)
    pdf_too_many_rows = report_data['total_count'] > 500
    pdf_blocked = pdf_too_wide_days or pdf_too_many_rows

    if export_fmt == 'excel':
        return export_report_excel(report_data, request.user)
    elif export_fmt == 'pdf':
        if pdf_too_wide_days:
            messages.warning(
                request,
                f"PDF export is limited to 31 days for day-level reports (selected range is {days_span} days). Please use Excel export."
            )
        elif pdf_too_many_rows:
            messages.warning(
                request,
                f"PDF export is limited to 500 rows (this report has {report_data['total_count']} rows). Please use Excel export."
            )
        else:
            return export_report_pdf(report_data, request.user, print_mode=False)
    elif export_fmt == 'print':
        return export_report_pdf(report_data, request.user, print_mode=True)

    # Filter bar options
    departments = Department.objects.filter(is_active=True).order_by('sort_order', 'name')
    branches = Branch.objects.filter(is_active=True).order_by('sort_order', 'name')
    filter_employees = _get_scoped_employees_qs(request.user, role_key)

    base_template_map = {
        'admin': 'Admin/adminbase.html',
        'hr': 'hr/hrbase.html',
        'accountant': 'finance/financebase.html',
        'manager': 'Manager/managerbase.html',
    }
    base_template = base_template_map.get(role_key) or get_base_template_for_user(request.user)

    preview_rows = report_data['rows'][:50]

    q_copy = request.GET.copy()
    if 'export' in q_copy:
        del q_copy['export']
    base_qs_str = q_copy.urlencode()

    return render(request, 'reports/reports_center.html', {
        'base_template': base_template,
        'role_key': role_key,
        'allowed_reports': allowed_reports,
        'selected_report': selected_report,
        'report_meta': meta,
        'report_data': report_data,
        'preview_rows': preview_rows,
        'total_count': report_data['total_count'],
        'preview_count': len(preview_rows),
        'pdf_blocked': pdf_blocked,
        'pdf_too_wide_days': pdf_too_wide_days,
        'pdf_too_many_rows': pdf_too_many_rows,
        'filters': filters,
        'selected_start': filters['start_str'],
        'selected_end': filters['end_str'],
        'selected_month': filters['month_str'],
        'selected_department': filters['dept_raw'],
        'selected_branch': filters['branch_raw'],
        'selected_employee': filters['emp_raw'],
        'selected_status': filters['status'],
        'departments': departments,
        'branches': branches,
        'filter_employees': filter_employees,
        'status_choices': meta.get('status_choices', []),
        'show_start_end': bool(meta.get('uses_period')),
        'show_month': bool(meta.get('uses_month') and not meta.get('uses_period')),
        'show_department': True,
        'show_branch': True,
        'show_employee': True,
        'show_status': bool(meta.get('status_choices')),
        'base_qs_str': base_qs_str,
        'reset_url': f"{request.path}?report={selected_report}",
    })


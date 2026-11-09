from Admin.leave_service import is_leave_v2_active
from collections import defaultdict
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Sum, Q, Count
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from urllib.parse import quote

from Admin.models import Attendance, Employee, Project, Leave, LeaveBalance, Notification
from Admin.attendance_autoclose import close_stale_sessions, capped_logout
from Admin.office_staff_helpers import (
    get_staff_attendance_context,
    handle_staff_punch_in,
    handle_staff_punch_out,
    handle_staff_apply_leave,
    fetch_user_notifications,
    mark_user_notifications_read,
    get_staff_profile_context,
    handle_staff_update_profile,
    handle_staff_update_travel_time,
)
from employee_data.forms import LeaveForm
from service_logs.models import EmployeeServiceLog
from .models import Expense, ExpenseCategory, Payroll, Invoice, JobCost, SpareSale, ExchangeRate
from .currency import NON_BASE_CURRENCY_CHOICES, get_rate
from .forms import (
    ExpenseForm,
    ExpenseCategoryForm,
    PayrollEditForm,
    InvoiceForm,
    JobCostForm,
    SpareSaleForm,
    RecordPaymentForm,
    ApplyCreditForm,
    ExchangeRateForm,
)
from .services import (
    generate_payroll,
    get_month_range,
    get_payroll_list_with_metrics,
    payroll_excel_response,
    expenses_excel_response,
    pnl_for_range,
    pnl_excel_response,
    get_projects_profitability,
    projects_profitability_excel_response,
    get_finance_start_month,
    get_attributed_revenue_by_employee,
    get_employee_cost_data,
    employee_cost_excel_response,
    get_receivables_data,
    receivables_excel_response,
    get_dashboard_data,
    import_finance_sheets_data,
    get_invoice_costing_data,
    invoices_excel_response,
    get_jobs_pnl_data,
    jobs_pnl_excel_response,
    get_spares_accounting_data,
    spares_excel_response,
    get_next_invoice_number,
    get_project_financials,
)
from .permissions import is_accountant, is_finance_admin


# =====================================================================
# DASHBOARD & PLACEHOLDERS
# =====================================================================

@login_required
@user_passes_test(is_accountant)
def fin_dashboard(request):
    """
    Finance Dashboard with 8 KPI cards, deltas, charts, client concentration,
    profitable/loss-making projects, break-even panel, expense-trend panel,
    and data-quality alerts.
    """
    period = request.GET.get('period') or 'this_month'
    start_date = parse_date(request.GET.get('start_date') or '')
    end_date = parse_date(request.GET.get('end_date') or '')

    if start_date and end_date and start_date > end_date:
        messages.error(request, "Start date cannot be after end date. Showing This Month.")
        period = 'this_month'
        start_date = None
        end_date = None

    data = get_dashboard_data(period=period, custom_start=start_date, custom_end=end_date, request=request)

    mini_chart_json = json.dumps(data['mini_chart'])
    cost_doughnut_json = json.dumps(data['cost_doughnut'])
    category_margins_json = json.dumps(data['category_margins'])

    context = {
        **data,
        'mini_chart_json': mini_chart_json,
        'cost_doughnut_json': cost_doughnut_json,
        'category_margins_json': category_margins_json,
    }
    return render(request, 'finance/dashboard.html', context)


def _resolve_pnl_dates(request):
    today = timezone.localdate()
    current_month_first = today.replace(day=1)
    fin_start_month = get_finance_start_month(request)
    period = request.GET.get('period') or 'this_year'
    year_str = request.GET.get('year')
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    granularity = 'month'
    if period == 'week':
        granularity = 'week'
    elif period == 'year':
        granularity = 'year'

    if start_date_str and end_date_str:
        start_date = parse_date(start_date_str) or date(today.year, 1, 1)
        end_date = parse_date(end_date_str) or today
        selected_year = start_date.year
        period = 'custom'
    elif period == 'this_month':
        start_date = current_month_first
        end_date = today
        selected_year = today.year
    elif period == 'last_month':
        last_m_end = current_month_first - timedelta(days=1)
        start_date = last_m_end.replace(day=1)
        end_date = last_m_end
        selected_year = start_date.year
    elif period == 'this_quarter':
        quarter_month = ((today.month - 1) // 3) * 3 + 1
        start_date = date(today.year, quarter_month, 1)
        end_date = today
        selected_year = today.year
    elif period in ('this_year', 'ytd', 'month'):
        start_date = date(today.year, 1, 1)
        end_date = date(today.year, 12, 31)
        selected_year = today.year
    elif period == 'last_year':
        start_date = date(today.year - 1, 1, 1)
        end_date = date(today.year - 1, 12, 31)
        selected_year = today.year - 1
    elif period == 'all_time':
        start_date = date(fin_start_month.year, fin_start_month.month, 1)
        end_date = today
        selected_year = today.year
    elif period == 'week':
        start_date = today - timedelta(weeks=12)
        end_date = today
        selected_year = today.year
    elif period == 'year':
        start_date = date(fin_start_month.year, 1, 1)
        end_date = date(today.year, 12, 31)
        selected_year = today.year
    elif year_str:
        try:
            selected_year = int(year_str)
        except ValueError:
            selected_year = today.year
        start_date = date(selected_year, 1, 1)
        end_date = date(selected_year, 12, 31)
    else:
        start_date = date(today.year, 1, 1)
        end_date = date(today.year, 12, 31)
        selected_year = today.year

    return start_date, end_date, period, granularity, selected_year


@login_required
@user_passes_test(is_accountant)
def fin_pnl(request):
    today = timezone.localdate()
    start_date, end_date, period, granularity, selected_year = _resolve_pnl_dates(request)

    # Monthly table data
    monthly_data = pnl_for_range(start_date, end_date, granularity='month', request=request)
    monthly_buckets = monthly_data['buckets']
    monthly_totals = monthly_data['totals']

    # Excel export
    if request.GET.get('export') == 'excel':
        suffix = f"{start_date}_{end_date}"
        return pnl_excel_response(monthly_buckets, monthly_totals, filename_suffix=suffix)

    # Chart data
    if period == 'week':
        chart_data = pnl_for_range(start_date, end_date, granularity='week', request=request)
    elif period == 'year':
        chart_data = pnl_for_range(start_date, end_date, granularity='year', request=request)
    else:
        chart_data = monthly_data

    chart_buckets = chart_data['buckets']
    chart_json = {
        'labels': [
            f"{b['label']} (YTD)" if b.get('ytd') else (f"{b['label']} (MTD)" if b.get('mtd') else b['label'])
            for b in chart_buckets
        ],
        'revenue': [float(b['revenue']) for b in chart_buckets],
        'total_cost': [float(b['total_cost']) for b in chart_buckets],
        'gross_profit': [float(b.get('gross_profit', 0)) for b in chart_buckets],
        'profit': [float(b['profit']) for b in chart_buckets],
    }

    years = list(range(today.year, today.year - 5, -1))

    get_params = request.GET.copy()
    params_export = get_params.copy()
    params_export.pop('export', None)
    querystring_for_export = params_export.urlencode()

    params_no_period = get_params.copy()
    params_no_period.pop('period', None)
    querystring_without_period = params_no_period.urlencode()

    params_no_sort = get_params.copy()
    params_no_sort.pop('sort', None)
    params_no_sort.pop('page', None)
    querystring_without_sort = params_no_sort.urlencode()

    params_no_page = get_params.copy()
    params_no_page.pop('page', None)
    querystring_without_page = params_no_page.urlencode()

    return render(request, 'finance/pnl.html', {
        'selected_year': selected_year,
        'start_date': start_date.strftime('%Y-%m-%d') if period == 'custom' else '',
        'end_date': end_date.strftime('%Y-%m-%d') if period == 'custom' else '',
        'period': period,
        'granularity': granularity,
        'buckets': monthly_buckets,
        'totals': monthly_totals,
        'chart_json': json.dumps(chart_json),
        'years': years,
        'querystring_for_export': querystring_for_export,
        'querystring_without_period': querystring_without_period,
        'querystring_without_sort': querystring_without_sort,
        'querystring_without_page': querystring_without_page,
    })


@login_required
@user_passes_test(is_accountant)
def fin_pnl_export_excel(request):
    start_date, end_date, period, granularity, selected_year = _resolve_pnl_dates(request)
    data = pnl_for_range(start_date, end_date, granularity=granularity, request=request)
    suffix = f"{period}_{start_date}_{end_date}"
    return pnl_excel_response(data['buckets'], data['totals'], filename_suffix=suffix)


@login_required
@user_passes_test(is_accountant)
def fin_projects(request):
    start_date = parse_date(request.GET.get('start_date') or '')
    end_date = parse_date(request.GET.get('end_date') or '')
    client = request.GET.get('client') or ''
    category = request.GET.get('category') or ''
    invoiced_status = request.GET.get('invoiced_status') or 'all'
    payment_status = request.GET.get('payment_status') or 'all'
    search = request.GET.get('search') or ''
    sort_by = request.GET.get('sort') or '-date'
    loss_only = request.GET.get('loss_only') == '1'

    filters = {
        'start_date': start_date,
        'end_date': end_date,
        'client': client,
        'category': category,
        'invoiced_status': invoiced_status,
        'payment_status': payment_status,
        'search': search,
        'loss_only': loss_only,
    }

    projects_data, totals = get_projects_profitability(filters=filters, sort_by=sort_by)

    clients = list(Project.objects.exclude(client_name='').values_list('client_name', flat=True).distinct().order_by('client_name'))
    categories = list(Project.objects.exclude(category='').values_list('category', flat=True).distinct().order_by('category'))

    get_params = request.GET.copy()
    params_no_sort = get_params.copy()
    params_no_sort.pop('sort', None)
    params_no_sort.pop('page', None)
    querystring_without_sort = params_no_sort.urlencode()

    params_no_page = get_params.copy()
    params_no_page.pop('page', None)
    querystring_without_page = params_no_page.urlencode()

    params_export = get_params.copy()
    params_export.pop('export', None)
    params_export.pop('page', None)
    querystring_for_export = params_export.urlencode()

    paginator = Paginator(projects_data, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    # 1 … 4 5 [6] 7 8 … 27 instead of listing every page
    page_range = list(paginator.get_elided_page_range(page_obj.number, on_each_side=2, on_ends=1))

    payment_statuses = [
        ('Paid', 'Paid'),
        ('Partial', 'Partial'),
        ('Open', 'Open'),
        ('Overdue', 'Overdue'),
        ('Not invoiced', 'Not invoiced'),
    ]

    return render(request, 'finance/projects.html', {
        'page_obj': page_obj,
        'page_range': page_range,
        'totals': totals,
        'clients': clients,
        'categories': categories,
        'payment_statuses': payment_statuses,
        'start_date': request.GET.get('start_date') or '',
        'end_date': request.GET.get('end_date') or '',
        'selected_client': client,
        'selected_category': category,
        'selected_invoiced_status': invoiced_status,
        'selected_payment_status': payment_status,
        'loss_only': loss_only,
        'search_query': search,
        'sort_by': sort_by,
        'querystring_without_sort': querystring_without_sort,
        'querystring_without_page': querystring_without_page,
        'querystring_for_export': querystring_for_export,
    })


@login_required
@user_passes_test(is_accountant)
def fin_projects_export_excel(request):
    filters = {
        'start_date': parse_date(request.GET.get('start_date') or ''),
        'end_date': parse_date(request.GET.get('end_date') or ''),
        'client': request.GET.get('client') or '',
        'category': request.GET.get('category') or '',
        'invoiced_status': request.GET.get('invoiced_status') or 'all',
        'payment_status': request.GET.get('payment_status') or 'all',
        'search': request.GET.get('search') or '',
        'loss_only': request.GET.get('loss_only') == '1',
    }
    sort_by = request.GET.get('sort') or '-date'
    projects_data, totals = get_projects_profitability(filters=filters, sort_by=sort_by)
    client = filters['client']
    suffix = f"{filters['payment_status']}_{client}".strip('_') or 'all'
    return projects_profitability_excel_response(projects_data, totals, filename_suffix=suffix)



@login_required
@user_passes_test(is_accountant)
def fin_project_detail(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    financials = get_project_financials(project)

    invoices = list(
        Invoice.objects.filter(project=project, is_cancelled=False)
        .order_by('-invoice_date', '-id')
    )
    if invoices:
        amount_received = sum((inv.amount_received_aed or Decimal('0.00') for inv in invoices), Decimal('0.00'))
        outstanding = sum((inv.outstanding_aed for inv in invoices), Decimal('0.00'))
    else:
        amount_received = project.amount_received or Decimal('0.00')
        outstanding = max((project.invoice_amount or Decimal('0.00')) - amount_received, Decimal('0.00'))

    service_logs = list(
        EmployeeServiceLog.objects.counting().filter(project=project)
        .select_related('employee', 'employee__user')
        .order_by('-date', 'start_time')
    )
    expenses = list(
        Expense.objects.filter(project=project)
        .select_related('category', 'created_by')
        .order_by('-date')
    )

    engineer_days = len(set((l.employee_id, l.date) for l in service_logs))

    cost_breakdown = {
        'revenue': financials.get('invoiced_amount', Decimal('0.00')),
        'is_legacy_invoice': financials.get('is_legacy_invoice', False),
        'amount_received': amount_received,
        'outstanding': outstanding,
        'employee_cost': financials.get('employee_cost', Decimal('0.00')),
        'spares_cost': financials.get('spares_cost', Decimal('0.00')),
        'other_job_costs': financials.get('other_job_costs', Decimal('0.00')),
        'total_cost': financials.get('total_cost', Decimal('0.00')),
        'profit': financials.get('profit', Decimal('0.00')),
        'margin_pct': financials.get('profit_pct'),
        'employee_hours': financials.get('employee_hours', Decimal('0.00')),
        'engineer_days': engineer_days,
        'financials': financials,
    }

    service_logs_totals = {
        'total_hours': sum((l.total_hours or Decimal('0.00') for l in service_logs), Decimal('0.00')),
        'ot_hours': sum((l.ot_hours or Decimal('0.00') for l in service_logs), Decimal('0.00')),
        'normal_cost': sum((l.normal_cost or Decimal('0.00') for l in service_logs), Decimal('0.00')),
        'ot_cost': sum((l.ot_cost or Decimal('0.00') for l in service_logs), Decimal('0.00')),
        'total_cost': sum((l.line_total_cost for l in service_logs), Decimal('0.00')),
    }

    return render(request, 'finance/project_detail.html', {
        'project': project,
        'cost_breakdown': cost_breakdown,
        'financials': financials,
        'invoices': invoices,
        'service_logs': service_logs,
        'service_logs_totals': service_logs_totals,
        'expenses': expenses,
    })



@login_required
@user_passes_test(is_accountant)
def fin_receivables(request):
    """
    Receivables aging analysis & payment recording.
    """
    client = request.GET.get('client') or ''
    bucket = request.GET.get('bucket') or 'all'
    search = request.GET.get('search') or ''
    sort_by = request.GET.get('sort') or '-days_outstanding'

    filters = {
        'client': client,
        'bucket': bucket,
        'search': search,
    }

    receivables, summary = get_receivables_data(filters=filters, sort_by=sort_by)

    get_params = request.GET.copy()
    params_no_sort = get_params.copy()
    params_no_sort.pop('sort', None)
    params_no_sort.pop('page', None)
    querystring_without_sort = params_no_sort.urlencode()

    params_export = get_params.copy()
    params_export.pop('export', None)
    params_export.pop('page', None)
    querystring_for_export = params_export.urlencode()

    params_no_page = get_params.copy()
    params_no_page.pop('page', None)
    querystring_without_page = params_no_page.urlencode()

    if request.GET.get('export') == 'excel':
        suffix = f"{bucket}_{client}".strip('_') or 'all'
        return receivables_excel_response(receivables, summary, filename_suffix=suffix)

    paginator = Paginator(receivables, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    clients = sorted(list(set(filter(None, Invoice.objects.filter(is_cancelled=False).values_list('customer', flat=True)))))

    return render(request, 'finance/receivables.html', {
        'page_obj': page_obj,
        'summary': summary,
        'clients': clients,
        'selected_client': client,
        'selected_bucket': bucket,
        'search_query': search,
        'sort_by': sort_by,
        'querystring_without_sort': querystring_without_sort,
        'querystring_for_export': querystring_for_export,
        'querystring_without_page': querystring_without_page,
    })


@login_required
@user_passes_test(is_accountant)
def fin_receivables_export_excel(request):
    client = request.GET.get('client') or ''
    bucket = request.GET.get('bucket') or 'all'
    search = request.GET.get('search') or ''
    sort_by = request.GET.get('sort') or '-days_outstanding'

    filters = {
        'client': client,
        'bucket': bucket,
        'search': search,
    }
    receivables, summary = get_receivables_data(filters=filters, sort_by=sort_by)
    suffix = f"{bucket}_{client}".strip('_') or 'all'
    return receivables_excel_response(receivables, summary, filename_suffix=suffix)


@login_required
@user_passes_test(is_accountant)
@require_POST
def fin_receivables_record_payment(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    amount_str = request.POST.get('amount')
    payment_date_str = request.POST.get('payment_date')

    try:
        amount = Decimal(amount_str or '0')
    except (InvalidOperation, ValueError):
        messages.error(request, "Invalid payment amount.")
        return redirect('fin_receivables')

    if amount <= Decimal('0.00'):
        messages.error(request, "Payment amount must be greater than zero.")
        return redirect('fin_receivables')

    curr_received = invoice.amount_received or Decimal('0.00')
    new_received = curr_received + amount
    coll_val = invoice.collection_value

    if new_received > coll_val:
        messages.error(request, f"Amount received ({new_received:.2f} {invoice.currency}) cannot exceed collection value ({coll_val:.2f} {invoice.currency}).")
        return redirect('fin_receivables')

    payment_date = parse_date(payment_date_str or '') or timezone.localdate()

    invoice.amount_received = new_received
    invoice.payment_received_date = payment_date
    invoice.save()

    if invoice.project:
        invoice.project.amount_received = invoice.amount_received_aed
        invoice.project.payment_received_date = payment_date
        if invoice.outstanding <= Decimal('0.00'):
            invoice.project.payment_status = 'PAID'
        elif new_received > Decimal('0.00'):
            invoice.project.payment_status = 'PARTIAL'
        else:
            invoice.project.payment_status = 'UNPAID'
        invoice.project.save()

    messages.success(request, f"Payment of {amount:,.2f} {invoice.currency} recorded for {invoice.invoice_number}.")
    return redirect('fin_receivables')



@login_required
@user_passes_test(is_accountant)
def fin_employee_cost(request):
    """
    Employee cost & billable utilisation overview.
    """
    today = timezone.localdate()
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    sort_by = request.GET.get('sort') or 'name'

    if start_date_str and end_date_str:
        start_date = parse_date(start_date_str) or date(today.year, today.month, 1)
        end_date = parse_date(end_date_str) or today
    else:
        start_date = date(today.year, today.month, 1)
        end_date = today

    rows, totals = get_employee_cost_data(start_date, end_date, request=request, sort_by=sort_by)

    get_params = request.GET.copy()
    params_export = get_params.copy()
    params_export.pop('export', None)
    querystring_for_export = params_export.urlencode()

    params_no_sort = get_params.copy()
    params_no_sort.pop('sort', None)
    querystring_without_sort = params_no_sort.urlencode()

    if request.GET.get('export') == 'excel':
        suffix = f"{start_date}_{end_date}"
        return employee_cost_excel_response(rows, totals, filename_suffix=suffix)

    return render(request, 'finance/employee_cost.html', {
        'rows': rows,
        'totals': totals,
        'start_date': start_date.strftime('%Y-%m-%d'),
        'end_date': end_date.strftime('%Y-%m-%d'),
        'sort_by': sort_by,
        'querystring_for_export': querystring_for_export,
        'querystring_without_sort': querystring_without_sort,
    })


@login_required
@user_passes_test(is_accountant)
def fin_employee_cost_export_excel(request):
    today = timezone.localdate()
    start_date = parse_date(request.GET.get('start_date') or '') or date(today.year, today.month, 1)
    end_date = parse_date(request.GET.get('end_date') or '') or today
    sort_by = request.GET.get('sort') or 'name'

    rows, totals = get_employee_cost_data(start_date, end_date, request=request, sort_by=sort_by)
    suffix = f"{start_date}_{end_date}"
    return employee_cost_excel_response(rows, totals, filename_suffix=suffix)


# =====================================================================
# PAYROLL
# =====================================================================

def _parse_month_param(month_param):
    if month_param:
        try:
            return datetime.strptime(month_param, '%Y-%m').date().replace(day=1)
        except ValueError:
            pass
    today = timezone.localdate()
    return today.replace(day=1)


@login_required
def fin_payroll(request):
    v2_payroll = getattr(settings, 'HR_PAYROLL_V2', False)
    if v2_payroll:
        from Admin.permissions import can_view_salary
        if not can_view_salary(request.user):
            raise PermissionDenied
    else:
        if not (request.user.is_superuser or (getattr(request.user, 'employee_profile', None) and request.user.employee_profile.is_accountant)):
            raise PermissionDenied

    month_param = request.GET.get('month')
    selected_month = _parse_month_param(month_param)
    first_day, last_day, _ = get_month_range(selected_month)

    payrolls, totals, _ = get_payroll_list_with_metrics(selected_month)

    has_draft = any(p.status == 'DRAFT' for p in payrolls)
    has_approved = any(p.status == 'APPROVED' for p in payrolls)

    can_manage_payroll = request.user.is_superuser or (getattr(request.user, 'employee_profile', None) and request.user.employee_profile.is_accountant)
    is_hr_viewer = getattr(request.user, 'employee_profile', None) and request.user.employee_profile.is_hr and not can_manage_payroll
    if is_hr_viewer:
        base_template = 'hr/hrbase.html'
    elif request.user.is_superuser and request.GET.get('from') == 'admin':
        base_template = 'Admin/adminbase.html'
    else:
        base_template = 'finance/financebase.html'

    missing_punch_warnings = []
    for p in payrolls:
        if getattr(p, 'missing_punch_days', 0) > 0:
            missing_punch_warnings.append({
                'name': p.employee.user.get_full_name() or p.employee.user.username,
                'username': p.employee.user.username,
                'dates': getattr(p, 'missing_punch_dates', []),
                'count': p.missing_punch_days,
            })

    checklist_submitted_timesheets = []
    checklist_draft_timesheets = []
    checklist_pending_ot_days = []
    timesheet_v2_on = getattr(settings, 'HR_TIMESHEET_APPROVAL', False)
    if timesheet_v2_on:
        from service_logs.models import EmployeeServiceLog
        from Admin.models import OTApproval
        submitted_logs = EmployeeServiceLog.objects.filter(
            date__range=(first_day, last_day),
            approval_status='SUBMITTED'
        ).select_related('employee__user')
        for l in submitted_logs:
            checklist_submitted_timesheets.append({
                'id': l.id,
                'name': l.employee.user.get_full_name() or l.employee.user.username,
                'date': l.date,
                'hours': l.total_hours,
            })

        draft_logs = EmployeeServiceLog.objects.filter(
            date__range=(first_day, last_day),
            approval_status='DRAFT'
        ).select_related('employee__user')
        for l in draft_logs:
            checklist_draft_timesheets.append({
                'id': l.id,
                'name': l.employee.user.get_full_name() or l.employee.user.username,
                'date': l.date,
                'hours': l.total_hours,
            })

        pending_ots = OTApproval.objects.filter(
            date__range=(first_day, last_day),
            status='PENDING'
        ).select_related('employee__user')
        for o in pending_ots:
            checklist_pending_ot_days.append({
                'id': o.id,
                'name': o.employee.user.get_full_name() or o.employee.user.username,
                'date': o.date,
                'hours': o.ot_hours,
                'cost': o.ot_cost,
            })

    return render(request, 'finance/payroll.html', {
        'selected_month': selected_month.strftime('%Y-%m'),
        'month_display': selected_month.strftime('%B %Y'),
        'payrolls': payrolls,
        'totals': totals,
        'has_draft': has_draft,
        'has_approved': has_approved,
        'missing_punch_warnings': missing_punch_warnings,
        'checklist_submitted_timesheets': checklist_submitted_timesheets,
        'checklist_draft_timesheets': checklist_draft_timesheets,
        'checklist_pending_ot_days': checklist_pending_ot_days,
        'timesheet_v2_on': timesheet_v2_on,
        'v2_payroll': v2_payroll,
        'can_manage_payroll': can_manage_payroll,
        'base_template': base_template,
    })


@login_required
@user_passes_test(is_accountant)
@require_POST
def fin_payroll_generate(request):
    month_param = request.POST.get('month')
    selected_month = _parse_month_param(month_param)
    created, updated, skipped = generate_payroll(selected_month, request.user)
    messages.success(
        request,
        f"Payroll for {selected_month.strftime('%B %Y')} updated: {created} created, {updated} updated, {skipped} skipped (approved/paid)."
    )
    return redirect(f"{redirect('fin_payroll').url}?month={selected_month.strftime('%Y-%m')}")


@login_required
@user_passes_test(is_accountant)
def fin_payroll_edit(request, payroll_id):
    payroll = get_object_or_404(Payroll.objects.select_related('employee', 'employee__user'), id=payroll_id)

    is_admin = request.user.is_superuser
    if payroll.status == 'PAID':
        messages.error(request, "Cannot edit payroll with status Paid. Paid records are locked.")
        return redirect(f"{redirect('fin_payroll').url}?month={payroll.month.strftime('%Y-%m')}")
    elif not is_admin and payroll.status != 'DRAFT':
        messages.error(request, f"Cannot edit payroll with status {payroll.get_status_display()}. Only DRAFT records can be modified.")
        return redirect(f"{redirect('fin_payroll').url}?month={payroll.month.strftime('%Y-%m')}")

    if request.method == 'POST':
        form = PayrollEditForm(request.POST, instance=payroll)
        if form.is_valid():
            form.save()
            messages.success(request, f"Payroll for {payroll.employee.user.get_full_name() or payroll.employee.user.username} updated.")
            return redirect(f"{redirect('fin_payroll').url}?month={payroll.month.strftime('%Y-%m')}")
    else:
        form = PayrollEditForm(instance=payroll)

    return render(request, 'finance/payroll_edit.html', {
        'form': form,
        'payroll': payroll,
    })


@login_required
@user_passes_test(is_finance_admin)
@require_POST
def fin_payroll_revert(request, payroll_id):
    payroll = get_object_or_404(Payroll.objects.select_related('employee', 'employee__user'), id=payroll_id)
    if payroll.status == 'APPROVED':
        payroll.status = 'DRAFT'
        payroll.save(update_fields=['status', 'updated_at'])
        for inst in payroll.advance_instalments.filter(status='SCHEDULED'):
            inst.payroll = None
            inst.save(update_fields=['payroll'])
        messages.success(request, f"Payroll for {payroll.employee.user.get_full_name() or payroll.employee.user.username} reverted to DRAFT.")
    else:
        messages.error(request, f"Cannot revert record with status {payroll.get_status_display()}. Only APPROVED records can be reverted.")
    return redirect(f"{redirect('fin_payroll').url}?month={payroll.month.strftime('%Y-%m')}")


@login_required
@user_passes_test(is_finance_admin)
@require_POST
def fin_payroll_delete(request, payroll_id):
    payroll = get_object_or_404(Payroll.objects.select_related('employee', 'employee__user'), id=payroll_id)
    if payroll.status == 'PAID':
        messages.error(request, "Cannot delete a payroll record with status PAID.")
    else:
        for inst in payroll.advance_instalments.all():
            if inst.status != 'DEDUCTED':
                inst.payroll = None
                inst.status = 'SCHEDULED'
                inst.save(update_fields=['payroll', 'status'])
        emp_name = payroll.employee.user.get_full_name() or payroll.employee.user.username
        payroll.delete()
        messages.success(request, f"Payroll record for {emp_name} deleted.")
    return redirect(f"{redirect('fin_payroll').url}?month={payroll.month.strftime('%Y-%m')}")


@login_required
@user_passes_test(is_accountant)
@require_POST
def fin_payroll_approve_all(request):
    month_param = request.POST.get('month')
    selected_month = _parse_month_param(month_param)
    from django.conf import settings
    v2_on = getattr(settings, 'HR_ATTENDANCE_ENGINE_V2', False)
    if v2_on:
        payrolls, _, _ = get_payroll_list_with_metrics(selected_month)
        draft_payrolls = [p for p in payrolls if p.status == 'DRAFT']
        unresolved = [p for p in draft_payrolls if getattr(p, 'missing_punch_days', 0) > 0]
        if unresolved:
            names = ", ".join((p.employee.user.get_full_name() or p.employee.user.username) for p in unresolved)
            messages.error(
                request,
                f"Cannot approve payroll for {selected_month.strftime('%B %Y')}: unresolved Missing Punch days exist for {names}. "
                f"Please resolve them in the attendance register before approving."
            )
            return redirect(f"{redirect('fin_payroll').url}?month={selected_month.strftime('%Y-%m')}")

    timesheet_v2_on = getattr(settings, 'HR_TIMESHEET_APPROVAL', False)
    if timesheet_v2_on:
        first_day, last_day, _ = get_month_range(selected_month)
        from service_logs.models import EmployeeServiceLog
        from Admin.models import OTApproval
        draft_emp_ids = list(Payroll.objects.filter(month=selected_month, status='DRAFT').values_list('employee_id', flat=True))
        if draft_emp_ids:
            submitted_logs = list(EmployeeServiceLog.objects.filter(
                employee_id__in=draft_emp_ids,
                date__range=(first_day, last_day),
                approval_status='SUBMITTED'
            ).select_related('employee__user'))
            if submitted_logs:
                names = ", ".join(sorted({(l.employee.user.get_full_name() or l.employee.user.username) for l in submitted_logs}))
                messages.error(
                    request,
                    f"Cannot approve payroll for {selected_month.strftime('%B %Y')}: unapproved submitted timesheet rows exist for {names}. "
                    f"Please approve or reject them on the Timesheet Approvals page first."
                )
                return redirect(f"{redirect('fin_payroll').url}?month={selected_month.strftime('%Y-%m')}")

            pending_ots = list(OTApproval.objects.filter(
                employee_id__in=draft_emp_ids,
                date__range=(first_day, last_day),
                status='PENDING'
            ).select_related('employee__user'))
            if pending_ots:
                names = ", ".join(sorted({(o.employee.user.get_full_name() or o.employee.user.username) for o in pending_ots}))
                messages.error(
                    request,
                    f"Cannot approve payroll for {selected_month.strftime('%B %Y')}: pending overtime approval days exist for {names}. "
                    f"Please approve or reject them on the OT Approval page first."
                )
                return redirect(f"{redirect('fin_payroll').url}?month={selected_month.strftime('%Y-%m')}")

    updated = Payroll.objects.filter(month=selected_month, status='DRAFT').update(status='APPROVED')
    messages.success(request, f"Approved {updated} payroll drafts for {selected_month.strftime('%B %Y')}.")
    return redirect(f"{redirect('fin_payroll').url}?month={selected_month.strftime('%Y-%m')}")


@login_required
@user_passes_test(is_accountant)
@require_POST
def fin_payroll_approve(request, payroll_id):
    payroll = get_object_or_404(Payroll.objects.select_related('employee', 'employee__user'), id=payroll_id)
    if payroll.status != 'DRAFT':
        messages.error(request, f"Cannot approve payroll with status {payroll.get_status_display()}.")
        return redirect(f"{redirect('fin_payroll').url}?month={payroll.month.strftime('%Y-%m')}")

    from django.conf import settings
    v2_on = getattr(settings, 'HR_ATTENDANCE_ENGINE_V2', False)
    if v2_on:
        payrolls, _, _ = get_payroll_list_with_metrics(payroll.month)
        matched = next((p for p in payrolls if p.id == payroll.id), None)
        if matched and getattr(matched, 'missing_punch_days', 0) > 0:
            emp_name = payroll.employee.user.get_full_name() or payroll.employee.user.username
            messages.error(
                request,
                f"Cannot approve payroll for {emp_name}: unresolved Missing Punch days exist for {payroll.month.strftime('%B %Y')}. "
                f"Please resolve them in the attendance register before approving."
            )
            return redirect(f"{redirect('fin_payroll').url}?month={payroll.month.strftime('%Y-%m')}")

    timesheet_v2_on = getattr(settings, 'HR_TIMESHEET_APPROVAL', False)
    if timesheet_v2_on:
        first_day, last_day, _ = get_month_range(payroll.month)
        from service_logs.models import EmployeeServiceLog
        from Admin.models import OTApproval
        emp_name = payroll.employee.user.get_full_name() or payroll.employee.user.username
        submitted_exists = EmployeeServiceLog.objects.filter(
            employee=payroll.employee,
            date__range=(first_day, last_day),
            approval_status='SUBMITTED'
        ).exists()
        if submitted_exists:
            messages.error(
                request,
                f"Cannot approve payroll for {emp_name}: unapproved submitted timesheet rows exist for {payroll.month.strftime('%B %Y')}. "
                f"Please approve or reject them on the Timesheet Approvals page first."
            )
            return redirect(f"{redirect('fin_payroll').url}?month={payroll.month.strftime('%Y-%m')}")

        pending_ot_exists = OTApproval.objects.filter(
            employee=payroll.employee,
            date__range=(first_day, last_day),
            status='PENDING'
        ).exists()
        if pending_ot_exists:
            messages.error(
                request,
                f"Cannot approve payroll for {emp_name}: pending overtime approval days exist for {payroll.month.strftime('%B %Y')}. "
                f"Please approve or reject them on the OT Approval page first."
            )
            return redirect(f"{redirect('fin_payroll').url}?month={payroll.month.strftime('%Y-%m')}")

    payroll.status = 'APPROVED'
    payroll.save(update_fields=['status', 'updated_at'])
    emp_name = payroll.employee.user.get_full_name() or payroll.employee.user.username
    messages.success(request, f"Payroll for {emp_name} approved.")
    return redirect(f"{redirect('fin_payroll').url}?month={payroll.month.strftime('%Y-%m')}")


@login_required
@user_passes_test(is_accountant)
@require_POST
def fin_payroll_mark_paid(request):
    month_param = request.POST.get('month')
    selected_month = _parse_month_param(month_param)
    today = timezone.localdate()
    draft_count = Payroll.objects.filter(month=selected_month, status='DRAFT').count()
    approved_payrolls = list(Payroll.objects.filter(month=selected_month, status='APPROVED').select_related('employee', 'employee__user'))
    updated = len(approved_payrolls)
    for p in approved_payrolls:
        p.status = 'PAID'
        p.paid_on = today
        p.save(update_fields=['status', 'paid_on'])

        # Advance instalments deducted & completed check
        for inst in p.advance_instalments.all():
            inst.status = 'DEDUCTED'
            inst.save(update_fields=['status'])
            adv = inst.advance
            if adv.balance <= Decimal('0.00'):
                adv.status = 'COMPLETED'
                adv.save(update_fields=['status'])

        # Auto-create payslip if V2 on
        if getattr(settings, 'HR_PAYROLL_V2', False):
            from finance.payroll_service import create_or_update_payslip
            create_or_update_payslip(p, generated_by=request.user)
            from Admin.models import Notification
            Notification.objects.create(
                recipient=p.employee.user,
                message=f"Your payslip for {p.month.strftime('%B %Y')} is ready."
            )

    if updated > 0 or draft_count == 0:
        messages.success(request, f"Marked {updated} payroll records as PAID for {selected_month.strftime('%B %Y')}.")
    if draft_count > 0:
        messages.warning(request, f"{draft_count} draft rows must be approved first.")
    return redirect(f"{redirect('fin_payroll').url}?month={selected_month.strftime('%Y-%m')}")


@login_required
def fin_payroll_export_excel(request):
    v2_payroll = getattr(settings, 'HR_PAYROLL_V2', False)
    if v2_payroll:
        from Admin.permissions import can_view_salary
        if not can_view_salary(request.user):
            raise PermissionDenied
    else:
        if not (request.user.is_superuser or (getattr(request.user, 'employee_profile', None) and request.user.employee_profile.is_accountant)):
            raise PermissionDenied
    month_param = request.GET.get('month')
    selected_month = _parse_month_param(month_param)
    payrolls, totals, _ = get_payroll_list_with_metrics(selected_month)
    return payroll_excel_response(payrolls, totals, selected_month)


# =====================================================================
# EXPENSES
# =====================================================================

def _filter_expenses(request):
    qs = Expense.objects.select_related('category', 'project', 'created_by').order_by('-date', '-id')

    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    if start_date_str:
        s_date = parse_date(start_date_str)
        if s_date:
            qs = qs.filter(date__gte=s_date)
    if end_date_str:
        e_date = parse_date(end_date_str)
        if e_date:
            qs = qs.filter(date__lte=e_date)

    category_id = request.GET.get('category')
    if category_id:
        qs = qs.filter(category_id=category_id)

    kind = request.GET.get('kind')
    if kind:
        qs = qs.filter(category__kind=kind)

    project_id = request.GET.get('project')
    if project_id:
        qs = qs.filter(project_id=project_id)

    search = request.GET.get('search')
    if search:
        search = search.strip()
        qs = qs.filter(Q(vendor__icontains=search) | Q(description__icontains=search) | Q(reference_no__icontains=search))

    return qs


@login_required
@user_passes_test(is_accountant)
def fin_expenses(request):
    qs = _filter_expenses(request)

    totals = qs.aggregate(
        total_amount=Sum('amount_aed'),
        total_vat=Sum('vat_amount_aed')
    )
    totals_data = {
        'amount': totals['total_amount'] or Decimal('0.00'),
        'vat_amount': totals['total_vat'] or Decimal('0.00'),
        'total_incl_vat': (totals['total_amount'] or Decimal('0.00')) + (totals['total_vat'] or Decimal('0.00')),
    }

    get_params = request.GET.copy()
    params_export = get_params.copy()
    params_export.pop('export', None)
    params_export.pop('page', None)
    querystring_for_export = params_export.urlencode()

    params_no_page = get_params.copy()
    params_no_page.pop('page', None)
    querystring_without_page = params_no_page.urlencode()

    paginator = Paginator(qs, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    categories = ExpenseCategory.objects.all().order_by('kind', 'sort_order', 'name')
    projects = Project.objects.exclude(status='CANCELLED').order_by('-id')

    return render(request, 'finance/expenses.html', {
        'page_obj': page_obj,
        'totals': totals_data,
        'categories': categories,
        'projects': projects,
        'selected_start_date': request.GET.get('start_date', ''),
        'selected_end_date': request.GET.get('end_date', ''),
        'selected_category': request.GET.get('category', ''),
        'selected_kind': request.GET.get('kind', ''),
        'selected_project': request.GET.get('project', ''),
        'selected_search': request.GET.get('search', ''),
        'querystring_for_export': querystring_for_export,
        'querystring_without_page': querystring_without_page,
    })


@login_required
@user_passes_test(is_accountant)
def fin_expense_add(request):
    if request.method == 'POST':
        form = ExpenseForm(request.POST, request.FILES)
        if form.is_valid():
            expense = form.save(commit=False)
            expense.created_by = request.user
            expense.save()
            messages.success(request, 'Expense created successfully.')
            return redirect('fin_expenses')
    else:
        form = ExpenseForm(initial={'date': timezone.localdate()})

    return render(request, 'finance/expense_form.html', {
        'form': form,
        'title': 'Add Expense',
    })


@login_required
@user_passes_test(is_accountant)
def fin_expense_edit(request, expense_id):
    expense = get_object_or_404(Expense, id=expense_id)
    if request.method == 'POST':
        form = ExpenseForm(request.POST, request.FILES, instance=expense)
        if form.is_valid():
            form.save()
            messages.success(request, 'Expense updated successfully.')
            return redirect('fin_expenses')
    else:
        form = ExpenseForm(instance=expense)

    return render(request, 'finance/expense_form.html', {
        'form': form,
        'expense': expense,
        'title': 'Edit Expense',
    })


@login_required
@user_passes_test(is_accountant)
@require_POST
def fin_expense_delete(request, expense_id):
    expense = get_object_or_404(Expense, id=expense_id)
    expense.delete()
    messages.success(request, 'Expense deleted successfully.')
    return redirect('fin_expenses')


@login_required
@user_passes_test(is_accountant)
def fin_expense_export_excel(request):
    qs = _filter_expenses(request)
    totals = qs.aggregate(
        amount=Sum('amount_aed'),
        vat_amount=Sum('vat_amount_aed')
    )
    totals_data = {
        'amount': totals['amount'] or Decimal('0.00'),
        'vat_amount': totals['vat_amount'] or Decimal('0.00'),
    }
    suffix = f"{request.GET.get('start_date', '')}_{request.GET.get('end_date', '')}".strip('_') or 'all'
    return expenses_excel_response(qs, totals_data, filename_suffix=suffix)


# =====================================================================
# PROJECT INVOICE EDIT
# =====================================================================

@login_required
@user_passes_test(is_accountant)
def fin_project_invoice_edit(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    raw_next = request.GET.get('next')
    if raw_next and url_has_allowed_host_and_scheme(url=raw_next, allowed_hosts={request.get_host()}):
        next_url = raw_next
    else:
        next_url = reverse('fin_projects')

    invoices = list(Invoice.objects.filter(project=project, is_cancelled=False).order_by('invoice_date', 'id'))
    count = len(invoices)
    if count == 1:
        edit_url = reverse('fin_invoice_edit', kwargs={'invoice_id': invoices[0].id})
        return redirect(f"{edit_url}?next={quote(next_url)}")
    elif count > 1:
        invoices_url = reverse('fin_invoices')
        return redirect(f"{invoices_url}?search={quote(project.name)}")
    else:
        add_url = reverse('fin_invoice_add')
        return redirect(f"{add_url}?project={project.id}&next={quote(next_url)}")


# =====================================================================
# DATA IMPORT (INVOICES, JOBS, SPARES)
# =====================================================================

@login_required
@user_passes_test(is_accountant)
def fin_import(request):
    preview_results = None
    if request.method == 'POST':
        action = request.POST.get('action', 'preview')
        dry_run = (action != 'execute')
        invoices_file = request.FILES.get('invoices_file')
        jobs_file = request.FILES.get('jobs_file')
        spares_file = request.FILES.get('spares_file')
        invoices_sheet = request.POST.get('invoices_sheet') or None
        jobs_sheet = request.POST.get('jobs_sheet') or None
        spares_sheet = request.POST.get('spares_sheet') or None

        if not any([invoices_file, jobs_file, spares_file]):
            messages.warning(request, "Please select at least one file to import (.xlsx or .csv).")
        else:
            preview_results = import_finance_sheets_data(
                invoices_file=invoices_file,
                jobs_file=jobs_file,
                spares_file=spares_file,
                invoices_sheet=invoices_sheet,
                jobs_sheet=jobs_sheet,
                spares_sheet=spares_sheet,
                dry_run=dry_run,
                user=request.user,
            )
            if not dry_run:
                messages.success(
                    request,
                    f"Successfully imported! Created: {preview_results['created_invoices']} invoices, "
                    f"{preview_results['created_job_costs']} job costs, {preview_results['created_spares']} spares. "
                    f"Updated: {preview_results['updated_invoices']} invoices, {preview_results['updated_spares']} spares."
                )

    return render(request, 'finance/import.html', {
        'preview_results': preview_results,
    })


# =====================================================================
# CATEGORIES SETTINGS
# =====================================================================

@login_required
@user_passes_test(is_accountant)
def fin_categories(request):
    categories = ExpenseCategory.objects.annotate(
        expenses_count=Count('expenses')
    ).order_by('kind', 'sort_order', 'name')
    return render(request, 'finance/categories.html', {'categories': categories})


@login_required
@user_passes_test(is_accountant)
def fin_category_add(request):
    if request.method == 'POST':
        form = ExpenseCategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Category created successfully.')
            return redirect('fin_categories')
    else:
        form = ExpenseCategoryForm()
    return render(request, 'finance/category_form.html', {'form': form, 'title': 'Add Category'})


@login_required
@user_passes_test(is_accountant)
def fin_category_edit(request, category_id):
    category = get_object_or_404(ExpenseCategory, id=category_id)
    if request.method == 'POST':
        form = ExpenseCategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, 'Category updated successfully.')
            return redirect('fin_categories')
    else:
        form = ExpenseCategoryForm(instance=category)
    return render(request, 'finance/category_form.html', {'form': form, 'category': category, 'title': 'Edit Category'})


@login_required
@user_passes_test(is_accountant)
@require_POST
def fin_category_toggle(request, category_id):
    category = get_object_or_404(ExpenseCategory, id=category_id)
    category.is_active = not category.is_active
    category.save(update_fields=['is_active'])
    status_str = "activated" if category.is_active else "deactivated"
    messages.success(request, f"Category '{category.name}' {status_str}.")
    return redirect('fin_categories')


@login_required
@user_passes_test(is_finance_admin)
@require_POST
def fin_category_delete(request, category_id):
    category = get_object_or_404(ExpenseCategory, id=category_id)
    if category.expenses.exists():
        messages.error(request, f"Cannot delete category '{category.name}' because expenses are linked to it. Deactivate instead.")
        return redirect('fin_categories')
    cat_name = category.name
    category.delete()
    messages.success(request, f"Category '{cat_name}' deleted successfully.")
    return redirect('fin_categories')


# =====================================================================
# ACCOUNTANT ATTENDANCE & LEAVE
# =====================================================================

@login_required
@user_passes_test(is_accountant)
def fin_render_attendance_page(request):
    if request.user.is_superuser:
        return redirect('admin-dashboard')
    try:
        employee = request.user.employee_profile
    except AttributeError:
        messages.error(request, "Your account does not have an employee profile.")
        return redirect('fin_dashboard')

    context = get_staff_attendance_context(request, employee)
    return render(request, 'finance/punchin.html', context)


@login_required
@user_passes_test(is_accountant)
def fin_log_in(request):
    if request.user.is_superuser:
        return redirect('admin-dashboard')
    if request.method == 'POST':
        try:
            employee = request.user.employee_profile
        except AttributeError:
            messages.error(request, "Your user is not associated with an employee record.")
            return redirect('fin_attendance_dashboard')

        handle_staff_punch_in(request, employee)
        return redirect('fin_attendance_status')

    return fin_render_attendance_page(request)


@login_required
@user_passes_test(is_accountant)
def fin_log_off(request, attendance_id):
    if request.user.is_superuser:
        return redirect('admin-dashboard')
    if request.method == 'POST':
        try:
            employee = request.user.employee_profile
        except AttributeError:
            messages.error(request, "Your user is not associated with an employee record.")
            return redirect('fin_attendance_dashboard')

        attendance = get_object_or_404(Attendance, id=attendance_id, employee=employee)
        handle_staff_punch_out(request, attendance)

    return redirect('fin_attendance_status')


@login_required
@user_passes_test(is_accountant)
def fin_attendance_status(request):
    if request.user.is_superuser:
        return redirect('admin-dashboard')
    try:
        employee = request.user.employee_profile
    except (AttributeError, Employee.DoesNotExist):
        messages.error(request, "Your account does not have an employee profile.")
        return redirect('fin_dashboard')

    close_stale_sessions(employee)
    from Admin.attendance_correction import render_role_attendance_status
    return render_role_attendance_status(request, "Finance", 'finance/financebase.html', template_name='finance/attendance_status.html')


@login_required
@user_passes_test(is_accountant)
def fin_apply_leave(request):
    if is_leave_v2_active():
        from Admin.leave_views import render_role_apply_leave
        return render_role_apply_leave(request, 'Accountant', 'finance/financebase.html', 'fin_leave_status')
    if request.user.is_superuser:
        return redirect('admin-dashboard')
    if request.method == 'POST':
        form = LeaveForm(request.POST, request.FILES)
        if form.is_valid():
            handle_staff_apply_leave(request, form)
            return redirect('fin_leave_status')
    else:
        form = LeaveForm()

    return render(request, 'finance/leave.html', {'form': form})


@login_required
@user_passes_test(is_accountant)
def fin_leave_status(request):
    if is_leave_v2_active():
        from Admin.leave_views import render_role_leave_status
        return render_role_leave_status(request, 'Accountant', 'finance/financebase.html')
    if request.user.is_superuser:
        return redirect('admin-dashboard')
    leaves_list = Leave.objects.filter(user=request.user).order_by('-from_date')
    paginator = Paginator(leaves_list, 10)
    page_number = request.GET.get('page')
    leaves = paginator.get_page(page_number)
    return render(request, 'finance/leavestatus.html', {'leaves': leaves})


@login_required
@user_passes_test(is_accountant)
def fin_leave_records(request):
    if is_leave_v2_active():
        from Admin.leave_views import render_role_leave_statement
        return render_role_leave_statement(request, 'Accountant', 'finance/financebase.html')
    if request.user.is_superuser:
        return redirect('admin-dashboard')
    leave_balance, _ = LeaveBalance.objects.get_or_create(user=request.user)
    leave_balances = {
        'ANNUAL LEAVE': leave_balance.annual_leave,
        'SICK LEAVE': leave_balance.sick_leave,
    }
    leave_summary = {
        lt: {'pending': 0, 'scheduled': 0, 'taken': 0, 'balance': leave_balances.get(lt, 0)}
        for lt in leave_balances
    }
    user_leaves = Leave.objects.filter(user=request.user)
    for leave in user_leaves:
        if leave.leave_type in leave_summary:
            if leave.approval_status == 'PENDING':
                leave_summary[leave.leave_type]['pending'] += leave.no_of_days
            elif leave.approval_status == 'APPROVED':
                leave_summary[leave.leave_type]['taken'] += leave.no_of_days
                leave_summary[leave.leave_type]['balance'] -= leave.no_of_days

    return render(request, 'finance/leaverecords.html', {'leave_summary': leave_summary})


@login_required
@user_passes_test(is_accountant)
def fin_upload_medical_certificate(request, leave_id):
    if request.user.is_superuser:
        return redirect('admin-dashboard')
    leave = get_object_or_404(Leave, id=leave_id, user=request.user)
    if request.method == 'POST':
        med_cert = request.FILES.get('medical_certificate')
        if med_cert:
            leave.medical_certificate = med_cert
            leave.save()
            if leave.from_date:
                days_since_start = (timezone.localdate() - leave.from_date).days
                if days_since_start > 0 and leave.leave_type == 'SICK LEAVE':
                    leave.leave_type = 'ANNUAL LEAVE'
                    leave.save()
                    messages.success(request, "Medical certificate uploaded late. Leave converted to Annual Leave.")
                else:
                    messages.success(request, "Medical certificate uploaded successfully.")
        return redirect('fin_leave_status')
    return redirect('fin_leave_status')


# =====================================================================
# NOTIFICATIONS, PROFILE & TRAVEL TIME
# =====================================================================

@login_required
@user_passes_test(is_accountant)
def fin_fetch_notifications(request):
    try:
        profile = request.user.employee_profile
    except (AttributeError, ObjectDoesNotExist):
        profile = None
    if request.user.is_superuser and not profile:
        return JsonResponse({"notifications": []})
    return JsonResponse({"notifications": fetch_user_notifications(request.user)})


@login_required
@user_passes_test(is_accountant)
def fin_mark_notifications_as_read(request):
    try:
        profile = request.user.employee_profile
    except (AttributeError, ObjectDoesNotExist):
        profile = None
    if request.user.is_superuser and not profile:
        return JsonResponse({"status": "ok"})
    return JsonResponse(mark_user_notifications_read(request.user))


@login_required
@user_passes_test(is_accountant)
def fin_profile_view(request):
    if request.user.is_superuser:
        return redirect('admin-dashboard')
    context = get_staff_profile_context(request.user)
    return render(request, 'finance/profile.html', context)


@login_required
@user_passes_test(is_accountant)
def fin_update_profile(request):
    if request.user.is_superuser:
        return redirect('admin-dashboard')
    return handle_staff_update_profile(request, 'fin_profile_view', 'finance/updateprofile.html')


@login_required
@user_passes_test(is_accountant)
def fin_update_travel_time(request, attendance_id):
    if request.user.is_superuser:
        return redirect('admin-dashboard')
    return handle_staff_update_travel_time(request, attendance_id, 'fin_attendance_status', 'finance/update_travel_time.html')


# =====================================================================
# PHASE B: INVOICE COSTING, JOBS P&L, SPARES ACCOUNTING VIEWS
# =====================================================================

@login_required
@user_passes_test(is_accountant)
def fin_invoices(request):
    month = request.GET.get('month') or ''
    start_date = parse_date(request.GET.get('start_date') or '')
    end_date = parse_date(request.GET.get('end_date') or '')
    status = request.GET.get('status') or 'all'
    invoice_type = request.GET.get('invoice_type') or 'all'
    search = request.GET.get('search') or ''
    sort_by = request.GET.get('sort') or '-invoice_date'

    filters = {
        'month': month,
        'start_date': start_date,
        'end_date': end_date,
        'status': status,
        'invoice_type': invoice_type,
        'search': search,
    }

    invoices_data, totals = get_invoice_costing_data(filters=filters, sort_by=sort_by)

    get_params = request.GET.copy()
    params_no_sort = get_params.copy()
    params_no_sort.pop('sort', None)
    params_no_sort.pop('page', None)
    querystring_without_sort = params_no_sort.urlencode()

    params_no_page = get_params.copy()
    params_no_page.pop('page', None)
    querystring_without_page = params_no_page.urlencode()

    params_export = get_params.copy()
    params_export.pop('export', None)
    params_export.pop('page', None)
    querystring_for_export = params_export.urlencode()

    paginator = Paginator(invoices_data, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    invoice_types = Invoice.INVOICE_TYPE_CHOICES
    statuses = [('all', 'All Statuses'), ('OPEN', 'Open'), ('PARTIAL', 'Partial'), ('PAID', 'Paid'), ('OVERDUE', 'Overdue'), ('CANCELLED', 'Cancelled')]

    return render(request, 'finance/invoices.html', {
        'page_obj': page_obj,
        'totals': totals,
        'selected_month': month,
        'start_date': request.GET.get('start_date') or '',
        'end_date': request.GET.get('end_date') or '',
        'selected_status': status,
        'selected_type': invoice_type,
        'search_query': search,
        'sort_by': sort_by,
        'invoice_types': invoice_types,
        'statuses': statuses,
        'querystring_without_sort': querystring_without_sort,
        'querystring_without_page': querystring_without_page,
        'querystring_for_export': querystring_for_export,
    })


@login_required
@user_passes_test(is_accountant)
def fin_invoice_add(request):
    raw_next = request.POST.get('next') or request.GET.get('next')
    if raw_next and url_has_allowed_host_and_scheme(url=raw_next, allowed_hosts={request.get_host()}):
        next_url = raw_next
    else:
        next_url = None

    if request.method == 'POST':
        form = InvoiceForm(request.POST)
        if form.is_valid():
            inv = form.save(commit=False)
            inv.created_by = request.user
            inv.save()
            messages.success(request, f"Invoice {inv.invoice_number} created successfully.")
            if next_url:
                return redirect(next_url)
            return redirect('fin_invoices')
    else:
        initial = {'invoice_number': get_next_invoice_number()}
        project_id = request.GET.get('project')
        if project_id:
            try:
                proj = Project.objects.get(id=project_id)
                initial['project'] = proj
                initial['customer'] = proj.client_name or ''
                initial['vessel'] = proj.vessel_name or ''
                initial['service_ref'] = getattr(proj, 'service_reference', '') or ''
                if proj.currency_code:
                    initial['currency'] = proj.currency_code
            except (Project.DoesNotExist, ValueError):
                pass
        form = InvoiceForm(initial=initial)

    project_currencies = {
        str(pid): (code or 'AED')
        for pid, code in Project.objects.values_list('id', 'currency_code')
    }

    return render(request, 'finance/invoice_form.html', {
        'form': form,
        'title': 'Add Invoice',
        'is_edit': False,
        'next': next_url,
        'project_currencies': project_currencies,
        'project_currencies_json': json.dumps(project_currencies),
    })


@login_required
@user_passes_test(is_accountant)
def fin_invoice_edit(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    raw_next = request.POST.get('next') or request.GET.get('next')
    if raw_next and url_has_allowed_host_and_scheme(url=raw_next, allowed_hosts={request.get_host()}):
        next_url = raw_next
    else:
        next_url = None

    if request.method == 'POST':
        form = InvoiceForm(request.POST, instance=invoice)
        if form.is_valid():
            form.save()
            messages.success(request, f"Invoice {invoice.invoice_number} updated successfully.")
            if next_url:
                return redirect(next_url)
            return redirect('fin_invoices')
    else:
        form = InvoiceForm(instance=invoice)

    project_currencies = {
        str(pid): (code or 'AED')
        for pid, code in Project.objects.values_list('id', 'currency_code')
    }

    return render(request, 'finance/invoice_form.html', {
        'form': form,
        'invoice': invoice,
        'title': f'Edit Invoice: {invoice.invoice_number}',
        'is_edit': True,
        'next': next_url,
        'project_currencies': project_currencies,
        'project_currencies_json': json.dumps(project_currencies),
    })


@login_required
@user_passes_test(is_accountant)
@require_POST
def fin_invoice_record_payment(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    amount_str = request.POST.get('amount_received') or request.POST.get('amount')
    date_str = request.POST.get('payment_received_date') or request.POST.get('payment_date')

    try:
        amount = Decimal(amount_str or '0')
    except (InvalidOperation, ValueError):
        messages.error(request, "Invalid payment amount.")
        return redirect('fin_invoices')

    if amount <= Decimal('0.00'):
        messages.error(request, "Payment amount must be greater than zero.")
        return redirect('fin_invoices')

    curr_received = invoice.amount_received or Decimal('0.00')
    new_received = curr_received + amount
    coll_val = invoice.collection_value

    if new_received > coll_val:
        messages.error(request, f"Amount received ({new_received:.2f} AED) cannot exceed collection value ({coll_val:.2f} AED).")
        return redirect('fin_invoices')

    payment_date = parse_date(date_str or '') or timezone.localdate()
    invoice.amount_received = new_received
    invoice.payment_received_date = payment_date
    invoice.save()

    messages.success(request, f"Recorded payment of AED {amount:,.2f} for {invoice.invoice_number}.")
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url:
        return redirect(next_url)
    return redirect('fin_invoices')


@login_required
@user_passes_test(is_accountant)
@require_POST
def fin_invoice_apply_credit(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    amount_str = request.POST.get('credit_amount')
    notes = request.POST.get('notes') or ''

    try:
        credit = Decimal(amount_str or '0')
    except (InvalidOperation, ValueError):
        messages.error(request, "Invalid credit amount.")
        return redirect('fin_invoices')

    if credit <= Decimal('0.00'):
        messages.error(request, "Credit amount must be greater than zero.")
        return redirect('fin_invoices')

    curr_credits = invoice.credits_applied or Decimal('0.00')
    new_credits = curr_credits + credit
    inv_amount = invoice.amount_excl_vat or Decimal('0.00')

    if new_credits > inv_amount:
        messages.error(request, f"Total credits ({new_credits:.2f} AED) cannot exceed invoice amount without tax ({inv_amount:.2f} AED).")
        return redirect('fin_invoices')

    invoice.credits_applied = new_credits
    if notes.strip():
        invoice.notes = f"{invoice.notes}\nCredit applied: {notes.strip()}".strip()
    invoice.save()

    messages.success(request, f"Applied credit of AED {credit:,.2f} to {invoice.invoice_number}.")
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url:
        return redirect(next_url)
    return redirect('fin_invoices')


@login_required
@user_passes_test(is_finance_admin)
@require_POST
def fin_invoice_delete(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    if (invoice.amount_received or Decimal('0.00')) > Decimal('0.00'):
        messages.error(request, "Invoice has payments recorded — cancel it instead.")
        return redirect('fin_invoices')

    inv_num = invoice.invoice_number
    with transaction.atomic():
        invoice.spare_sales.update(invoice=None)
        invoice.job_costs.all().delete()
        invoice.delete()

    messages.success(request, f"Invoice {inv_num} deleted successfully.")
    return redirect('fin_invoices')


@login_required
@user_passes_test(is_accountant)
def fin_invoices_export_excel(request):
    filters = {
        'month': request.GET.get('month') or '',
        'start_date': parse_date(request.GET.get('start_date') or ''),
        'end_date': parse_date(request.GET.get('end_date') or ''),
        'status': request.GET.get('status') or 'all',
        'invoice_type': request.GET.get('invoice_type') or 'all',
        'search': request.GET.get('search') or '',
    }
    sort_by = request.GET.get('sort') or '-invoice_date'
    invoices_data, totals = get_invoice_costing_data(filters=filters, sort_by=sort_by)
    suffix = f"{request.GET.get('start_date', '')}_{request.GET.get('end_date', '')}".strip('_') or 'all'
    return invoices_excel_response(invoices_data, totals, filename_suffix=suffix)


@login_required
@user_passes_test(is_accountant)
def fin_jobs_pnl(request):
    month = request.GET.get('month') or ''
    start_date = parse_date(request.GET.get('start_date') or '')
    end_date = parse_date(request.GET.get('end_date') or '')
    status = request.GET.get('status') or 'all'
    invoice_type = request.GET.get('invoice_type') or 'all'
    search = request.GET.get('search') or ''
    sort_by = request.GET.get('sort') or '-invoice_date'

    filters = {
        'month': month,
        'start_date': start_date,
        'end_date': end_date,
        'status': status,
        'invoice_type': invoice_type,
        'search': search,
    }

    jobs_data, totals = get_jobs_pnl_data(filters=filters, sort_by=sort_by)

    get_params = request.GET.copy()
    params_no_sort = get_params.copy()
    params_no_sort.pop('sort', None)
    params_no_sort.pop('page', None)
    querystring_without_sort = params_no_sort.urlencode()

    params_no_page = get_params.copy()
    params_no_page.pop('page', None)
    querystring_without_page = params_no_page.urlencode()

    params_export = get_params.copy()
    params_export.pop('export', None)
    params_export.pop('page', None)
    querystring_for_export = params_export.urlencode()

    paginator = Paginator(jobs_data, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    invoice_types = Invoice.INVOICE_TYPE_CHOICES
    statuses = [('all', 'All Statuses'), ('OPEN', 'Open'), ('PARTIAL', 'Partial'), ('PAID', 'Paid'), ('OVERDUE', 'Overdue'), ('CANCELLED', 'Cancelled')]

    return render(request, 'finance/jobs_pnl.html', {
        'page_obj': page_obj,
        'totals': totals,
        'selected_month': month,
        'start_date': request.GET.get('start_date') or '',
        'end_date': request.GET.get('end_date') or '',
        'selected_status': status,
        'selected_type': invoice_type,
        'search_query': search,
        'sort_by': sort_by,
        'invoice_types': invoice_types,
        'statuses': statuses,
        'querystring_without_sort': querystring_without_sort,
        'querystring_without_page': querystring_without_page,
        'querystring_for_export': querystring_for_export,
    })


@login_required
@user_passes_test(is_accountant)
def fin_job_costs(request, invoice_id):
    invoice = get_object_or_404(Invoice.objects.select_related('project'), id=invoice_id)
    if request.method == 'POST':
        form = JobCostForm(request.POST)
        if form.is_valid():
            cost = form.save(commit=False)
            cost.invoice = invoice
            cost.project = invoice.project
            cost.source = 'MANUAL'
            cost.created_by = request.user
            cost.save()
            messages.success(request, f"Added {cost.get_cost_type_display()} cost of AED {cost.amount:,.2f}.")
            return redirect('fin_job_costs', invoice_id=invoice.id)
    else:
        form = JobCostForm()

    costs = list(invoice.job_costs.all().order_by('-date', '-id'))
    total_costs = sum((c.amount_aed or Decimal('0.00')) for c in costs)

    return render(request, 'finance/job_costs.html', {
        'invoice': invoice,
        'costs': costs,
        'total_costs': total_costs,
        'form': form,
    })


@login_required
@user_passes_test(is_accountant)
@require_POST
def fin_job_cost_delete(request, cost_id):
    cost = get_object_or_404(JobCost, id=cost_id)
    inv_id = cost.invoice_id
    cost.delete()
    messages.success(request, "Job cost removed successfully.")
    if inv_id:
        return redirect('fin_job_costs', invoice_id=inv_id)
    return redirect('fin_jobs_pnl')


@login_required
@user_passes_test(is_accountant)
def fin_jobs_pnl_export_excel(request):
    filters = {
        'month': request.GET.get('month') or '',
        'start_date': parse_date(request.GET.get('start_date') or ''),
        'end_date': parse_date(request.GET.get('end_date') or ''),
        'status': request.GET.get('status') or 'all',
        'invoice_type': request.GET.get('invoice_type') or 'all',
        'search': request.GET.get('search') or '',
    }
    sort_by = request.GET.get('sort') or '-invoice_date'
    jobs_data, totals = get_jobs_pnl_data(filters=filters, sort_by=sort_by)
    suffix = f"{request.GET.get('start_date', '')}_{request.GET.get('end_date', '')}".strip('_') or 'all'
    return jobs_pnl_excel_response(jobs_data, totals, filename_suffix=suffix)


@login_required
@user_passes_test(is_accountant)
def fin_spares(request):
    search = request.GET.get('search') or ''
    status = request.GET.get('status') or 'all'
    source = request.GET.get('source') or 'all'
    alerts_only = request.GET.get('alerts_only') in ('1', 'true', 'True', 'on')
    sort_by = request.GET.get('sort') or '-created_at'

    filters = {
        'search': search,
        'status': status,
        'source': source,
        'alerts_only': alerts_only,
    }

    spares_data, summary = get_spares_accounting_data(filters=filters, sort_by=sort_by, alerts_only=alerts_only)

    get_params = request.GET.copy()
    params_no_sort = get_params.copy()
    params_no_sort.pop('sort', None)
    params_no_sort.pop('page', None)
    querystring_without_sort = params_no_sort.urlencode()

    params_no_page = get_params.copy()
    params_no_page.pop('page', None)
    querystring_without_page = params_no_page.urlencode()

    params_export = get_params.copy()
    params_export.pop('export', None)
    params_export.pop('page', None)
    querystring_for_export = params_export.urlencode()

    paginator = Paginator(spares_data, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    statuses = SpareSale.STATUS_CHOICES
    sources = SpareSale.SOURCE_CHOICES

    return render(request, 'finance/spares.html', {
        'page_obj': page_obj,
        'summary': summary,
        'search_query': search,
        'selected_status': status,
        'selected_source': source,
        'alerts_only': alerts_only,
        'sort_by': sort_by,
        'statuses': statuses,
        'sources': sources,
        'querystring_without_sort': querystring_without_sort,
        'querystring_without_page': querystring_without_page,
        'querystring_for_export': querystring_for_export,
    })


@login_required
@user_passes_test(is_accountant)
def fin_spare_add(request):
    if request.method == 'POST':
        form = SpareSaleForm(request.POST)
        if form.is_valid():
            spare = form.save(commit=False)
            spare.created_by = request.user
            spare.save()
            messages.success(request, f"Spare sale for {spare.service_ref or spare.vessel_customer} created.")
            if spare.invoice_id:
                legacy_costs = JobCost.objects.filter(invoice=spare.invoice, source='LEGACY', description='Legacy purchases')
                if legacy_costs.exists():
                    legacy_amt = sum(((c.amount_aed or Decimal('0.00')) for c in legacy_costs), Decimal('0.00'))
                    messages.warning(request, f"This invoice also has a Legacy purchases cost of AED {legacy_amt:,.2f} — delete it if it is the same spares cost, otherwise it is counted twice.")
            return redirect('fin_spares')
    else:
        form = SpareSaleForm()

    return render(request, 'finance/spare_form.html', {
        'form': form,
        'title': 'Add Spare Sale',
        'is_edit': False,
    })


@login_required
@user_passes_test(is_accountant)
def fin_spare_edit(request, spare_id):
    spare = get_object_or_404(SpareSale, id=spare_id)
    if request.method == 'POST':
        form = SpareSaleForm(request.POST, instance=spare)
        if form.is_valid():
            form.save()
            messages.success(request, f"Spare sale for {spare.service_ref or spare.vessel_customer} updated.")
            if spare.invoice_id:
                legacy_costs = JobCost.objects.filter(invoice=spare.invoice, source='LEGACY', description='Legacy purchases')
                if legacy_costs.exists():
                    legacy_amt = sum(((c.amount_aed or Decimal('0.00')) for c in legacy_costs), Decimal('0.00'))
                    messages.warning(request, f"This invoice also has a Legacy purchases cost of AED {legacy_amt:,.2f} — delete it if it is the same spares cost, otherwise it is counted twice.")
            return redirect('fin_spares')
    else:
        form = SpareSaleForm(instance=spare)

    legacy_warning = ""
    if spare.invoice_id:
        legacy_costs = JobCost.objects.filter(invoice=spare.invoice, source='LEGACY', description='Legacy purchases')
        if legacy_costs.exists():
            legacy_amt = sum(((c.amount_aed or Decimal('0.00')) for c in legacy_costs), Decimal('0.00'))
            legacy_warning = f"This invoice also has a Legacy purchases cost of AED {legacy_amt:,.2f} — delete it if it is the same spares cost, otherwise it is counted twice."

    return render(request, 'finance/spare_form.html', {
        'form': form,
        'spare': spare,
        'title': f'Edit Spare Sale: {spare.service_ref or spare.vessel_customer}',
        'is_edit': True,
        'legacy_warning': legacy_warning,
    })


@login_required
@user_passes_test(is_finance_admin)
@require_POST
def fin_spare_delete(request, spare_id):
    spare = get_object_or_404(SpareSale, id=spare_id)
    ref = spare.service_ref or spare.vessel_customer or str(spare.id)
    spare.delete()
    messages.success(request, f"Spare sale for {ref} deleted successfully.")
    return redirect('fin_spares')


@login_required
@user_passes_test(is_accountant)
def fin_spares_export_excel(request):
    filters = {
        'search': request.GET.get('search') or '',
        'status': request.GET.get('status') or 'all',
        'source': request.GET.get('source') or 'all',
        'alerts_only': request.GET.get('alerts_only') in ('1', 'true', 'True', 'on'),
    }
    sort_by = request.GET.get('sort') or '-created_at'
    spares_data, summary = get_spares_accounting_data(filters=filters, sort_by=sort_by, alerts_only=filters['alerts_only'])
    suffix = f"{request.GET.get('status', '')}_{request.GET.get('source', '')}".strip('_') or 'all'
    return spares_excel_response(spares_data, summary, filename_suffix=suffix)




@login_required
@user_passes_test(is_accountant)
def fin_exchange_rates(request):
    currency_filter = request.GET.get('currency', '').strip().upper()
    rates = ExchangeRate.objects.select_related('created_by').all()
    if currency_filter in dict(NON_BASE_CURRENCY_CHOICES):
        rates = rates.filter(currency=currency_filter)

    today = timezone.localdate()
    current_rate_ids = set()
    for code, _ in NON_BASE_CURRENCY_CHOICES:
        latest = (
            ExchangeRate.objects.filter(currency=code, is_active=True, effective_from__lte=today)
            .order_by('-effective_from', '-created_at')
            .first()
        )
        if latest:
            current_rate_ids.add(latest.id)

    return render(
        request,
        'finance/exchange_rates.html',
        {
            'rates': rates,
            'currencies': NON_BASE_CURRENCY_CHOICES,
            'current_currency': currency_filter,
            'current_rate_ids': current_rate_ids,
        },
    )


@login_required
@user_passes_test(is_accountant)
def fin_exchange_rate_add(request):
    if request.method == 'POST':
        form = ExchangeRateForm(request.POST)
        if form.is_valid():
            rate = form.save(commit=False)
            rate.created_by = request.user
            rate.save()
            messages.success(request, f"Exchange rate for {rate.currency} created successfully.")
            return redirect('fin_exchange_rates')
    else:
        initial = {'effective_from': timezone.localdate()}
        currency_param = request.GET.get('currency', '').strip().upper()
        if currency_param in dict(NON_BASE_CURRENCY_CHOICES):
            initial['currency'] = currency_param
        form = ExchangeRateForm(initial=initial)

    return render(
        request,
        'finance/exchange_rate_form.html',
        {
            'form': form,
            'title': 'Add Exchange Rate',
        },
    )


@login_required
@user_passes_test(is_accountant)
def fin_exchange_rate_edit(request, rate_id):
    rate = get_object_or_404(ExchangeRate, id=rate_id)
    if request.method == 'POST':
        form = ExchangeRateForm(request.POST, instance=rate)
        if form.is_valid():
            form.save()
            messages.success(request, f"Exchange rate for {rate.currency} updated successfully.")
            return redirect('fin_exchange_rates')
    else:
        form = ExchangeRateForm(instance=rate)

    return render(
        request,
        'finance/exchange_rate_form.html',
        {
            'form': form,
            'rate': rate,
            'title': 'Edit Exchange Rate',
        },
    )


@login_required
@user_passes_test(is_accountant)
@require_POST
def fin_exchange_rate_toggle(request, rate_id):
    rate = get_object_or_404(ExchangeRate, id=rate_id)
    rate.is_active = not rate.is_active
    rate.save(update_fields=['is_active'])
    status_str = "activated" if rate.is_active else "deactivated"
    messages.success(request, f"Exchange rate for {rate.currency} ({rate.effective_from}) {status_str}.")
    return redirect('fin_exchange_rates')


@login_required
@user_passes_test(is_finance_admin)
@require_POST
def fin_exchange_rate_delete(request, rate_id):
    rate = get_object_or_404(ExchangeRate, id=rate_id)
    cur = rate.currency
    eff = rate.effective_from
    rate.delete()
    messages.success(request, f"Exchange rate for {cur} ({eff}) deleted successfully.")
    return redirect('fin_exchange_rates')


@login_required
@user_passes_test(is_accountant)
def fin_api_exchange_rate(request):
    currency = request.GET.get('currency', '').strip().upper()
    date_str = request.GET.get('date', '').strip()

    if not currency:
        return JsonResponse({'rate': None, 'message': 'Currency is required.'}, status=400)

    if currency == 'AED':
        return JsonResponse({'rate': '1.000000'})

    parsed_date = parse_date(date_str) if date_str else timezone.localdate()
    if not parsed_date:
        parsed_date = timezone.localdate()

    rate = get_rate(currency, parsed_date)
    if rate is not None:
        return JsonResponse({'rate': f"{Decimal(rate):.6f}"})
    else:
        date_display = parsed_date.strftime('%Y-%m-%d')
        return JsonResponse({
            'rate': None,
            'message': f"No {currency} rate on or before {date_display} — add one in Settings → Exchange Rates",
        })

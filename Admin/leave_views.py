import io
import datetime
from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone

from Admin.models import (
    Employee, Department, Branch, Leave, LeaveType, LeavePolicy,
    LeaveLedger, LeaveYearReview, EmployeeChangeLog
)
from Admin.leave_service import (
    is_leave_v2_active,
    add_months,
    calculate_leave_working_days,
    get_employee_leave_balance,
    get_employee_pending_leave_days,
    get_employee_available_leave,
    get_employee_leave_summary,
    can_decide_leave,
    submit_leave_request,
    approve_leave_request,
    reject_leave_request,
    request_leave_clarification,
    respond_leave_clarification,
    cancel_leave_request,
    generate_opening_balances_template,
    process_opening_balances_excel,
    export_leave_list_excel,
    export_leave_statement_excel,
)


@login_required
def calculate_leave_days_api(request):
    """
    Live Day Count API endpoint (/api/leave/calculate-days/).
    Returns working days, excluded days (rest days & public holidays),
    current available balance, and probation restriction info.
    """
    emp_id = request.GET.get('employee_id') or request.POST.get('employee_id')
    if emp_id:
        emp = Employee.objects.filter(pk=emp_id).first()
    else:
        emp = getattr(request.user, 'employee_profile', None)

    if not emp and request.user.is_superuser:
        emp = Employee.objects.filter(user__is_active=True).first()

    if not emp:
        return JsonResponse({'status': 'error', 'message': 'Employee not found.'}, status=400)

    from_date_str = request.GET.get('from_date') or request.POST.get('from_date')
    to_date_str = request.GET.get('to_date') or request.POST.get('to_date')
    is_half_day = str(request.GET.get('is_half_day', '')).lower() in ('1', 'true', 'on', 'yes')
    half_day_part = request.GET.get('half_day_part') or request.POST.get('half_day_part')
    leave_type_id = request.GET.get('leave_type_id') or request.POST.get('leave_type_id')

    if not from_date_str or not to_date_str:
        return JsonResponse({'status': 'error', 'message': 'From and To dates are required.'}, status=400)

    try:
        from_date = datetime.datetime.strptime(from_date_str, '%Y-%m-%d').date()
        to_date = datetime.datetime.strptime(to_date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'status': 'error', 'message': 'Invalid date format (use YYYY-MM-DD).'}, status=400)

    if to_date < from_date:
        return JsonResponse({'status': 'error', 'message': 'To Date cannot be before From Date.'}, status=400)

    lt = None
    if leave_type_id:
        lt = LeaveType.objects.filter(pk=leave_type_id).first()

    try:
        working_days, excluded_days = calculate_leave_working_days(
            emp, from_date, to_date, is_half_day=is_half_day, leave_type=lt
        )
    except ValidationError as e:
        msg = e.message if hasattr(e, 'message') else str(e)
        return JsonResponse({'status': 'error', 'message': msg}, status=400)

    is_probation_blocked = False
    probation_msg = ""
    if lt and lt.code == 'ANNUAL' and emp.date_of_join:
        probation_end = add_months(emp.date_of_join, 6)
        if from_date < probation_end:
            is_probation_blocked = True
            probation_msg = "Annual leave is available after 6 months of service — apply as Unpaid Leave (needs management approval)."

    net_bal = get_employee_leave_balance(emp, lt) if lt else Decimal('0.00')
    pending_days = get_employee_pending_leave_days(emp, lt) if lt else Decimal('0.00')
    available_days = get_employee_available_leave(emp, lt) if lt else Decimal('0.00')

    return JsonResponse({
        'status': 'ok',
        'working_days': float(working_days),
        'excluded_days': excluded_days,
        'net_balance': float(net_bal),
        'pending_days': float(pending_days),
        'available_days': float(available_days),
        'is_probation_blocked': is_probation_blocked,
        'probation_message': probation_msg,
        'uses_balance': lt.uses_balance if lt else False,
        'allows_half_day': lt.allows_half_day if lt else False,
        'certificate_required_after_days': lt.certificate_required_after_days if lt else 0,
    })


def render_role_apply_leave(request, role, base_template, redirect_url_name):
    """
    Unified Apply Leave view handler used by all 6 apps.
    """
    user_emp = getattr(request.user, 'employee_profile', None)
    is_hr_or_admin = request.user.is_superuser or (user_emp and user_emp.is_hr)

    if request.method == 'POST':
        target_emp_id = request.POST.get('employee_id')
        if is_hr_or_admin and target_emp_id:
            target_emp = get_object_or_404(Employee, pk=target_emp_id)
        else:
            target_emp = user_emp

        if not target_emp:
            messages.error(request, "Employee profile not found.")
            return redirect(redirect_url_name)

        leave_type_id = request.POST.get('leave_type')
        from_date_str = request.POST.get('from_date')
        to_date_str = request.POST.get('to_date')
        reason = request.POST.get('reason', '').strip()
        is_half_day = request.POST.get('is_half_day') in ('1', 'true', 'on')
        half_day_part = request.POST.get('half_day_part')
        attachment = request.FILES.get('medical_certificate')
        hr_override = request.POST.get('hr_override') in ('1', 'true', 'on')
        hr_override_reason = request.POST.get('hr_override_reason', '').strip()

        try:
            lt = get_object_or_404(LeaveType, pk=leave_type_id)
            from_date = datetime.datetime.strptime(from_date_str, '%Y-%m-%d').date()
            to_date = datetime.datetime.strptime(to_date_str, '%Y-%m-%d').date()

            submit_leave_request(
                employee=target_emp,
                leave_type_ref=lt,
                from_date=from_date,
                to_date=to_date,
                reason=reason,
                is_half_day=is_half_day,
                half_day_part=half_day_part,
                attachment=attachment,
                hr_override=hr_override,
                hr_override_reason=hr_override_reason,
                submitted_by=request.user
            )
            messages.success(request, "Leave request submitted successfully.")
            return redirect(redirect_url_name)

        except (ValidationError, Exception) as e:
            msg = e.message if hasattr(e, 'message') else (", ".join(e.messages) if hasattr(e, 'messages') else str(e))
            messages.error(request, msg)

    # GET context
    summary = get_employee_leave_summary(user_emp) if user_emp else []
    leave_types = LeaveType.objects.filter(is_active=True).order_by('sort_order', 'name')
    all_employees = Employee.objects.filter(user__is_active=True).order_by('user__first_name', 'user__last_name', 'user__username') if is_hr_or_admin else []

    context = {
        'role': role,
        'base_template': base_template,
        'leave_summary': summary,
        'leave_types': leave_types,
        'is_hr_or_admin': is_hr_or_admin,
        'all_employees': all_employees,
        'current_employee': user_emp,
        'today': timezone.localdate(),
    }
    return render(request, 'leave/apply_leave.html', context)


def render_role_leave_status(request, role, base_template):
    """
    Unified My Leave Status view handler with clarification and cancel actions.
    """
    user_emp = getattr(request.user, 'employee_profile', None)

    if request.method == 'POST':
        action = request.POST.get('action')
        leave_id = request.POST.get('leave_id')
        leave = get_object_or_404(Leave, pk=leave_id)

        try:
            if action == 'respond_clarification':
                resp_text = request.POST.get('clarification_response', '').strip()
                respond_leave_clarification(leave, request.user, resp_text)
                messages.success(request, f"Clarification response submitted for leave #{leave.id}.")
            elif action == 'cancel':
                remarks = request.POST.get('remarks', '').strip()
                cancel_leave_request(leave, request.user, remarks)
                messages.success(request, f"Leave #{leave.id} has been cancelled.")
        except (ValidationError, PermissionDenied, Exception) as e:
            msg = e.message if hasattr(e, 'message') else (", ".join(e.messages) if hasattr(e, 'messages') else str(e))
            messages.error(request, msg)

    summary = get_employee_leave_summary(user_emp) if user_emp else []
    leaves_qs = Leave.objects.filter(
        Q(user=request.user) | Q(employee=user_emp)
    ).select_related('leave_type_ref', 'approver', 'decided_by').order_by('-from_date', '-id')

    tab = request.GET.get('tab', 'ALL')
    if tab == 'PENDING':
        leaves_qs = leaves_qs.filter(approval_status__in=['PENDING', 'CLARIFICATION'])
    elif tab == 'APPROVED':
        leaves_qs = leaves_qs.filter(approval_status='APPROVED')
    elif tab == 'HISTORY':
        leaves_qs = leaves_qs.filter(approval_status__in=['REJECTED', 'CANCELLED'])

    paginator = Paginator(leaves_qs, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'role': role,
        'base_template': base_template,
        'leave_summary': summary,
        'leaves': page_obj,
        'current_tab': tab,
    }
    return render(request, 'leave/leave_status.html', context)


def render_role_manage_leave(request, role, base_template):
    """
    Unified Approver Queue view handler for Managers, HR and Admin.
    """
    user_emp = getattr(request.user, 'employee_profile', None)
    is_hr_admin = request.user.is_superuser or (user_emp and user_emp.is_hr)

    if request.method == 'POST':
        action = request.POST.get('action')
        leave_id = request.POST.get('leave_id')
        remarks = request.POST.get('remarks', '').strip()
        leave = get_object_or_404(Leave, pk=leave_id)

        try:
            if action == 'approve':
                approve_leave_request(leave, request.user, remarks)
                messages.success(request, f"Leave request #{leave.id} for {leave.employee.name if leave.employee else leave.user.username} APPROVED.")
            elif action == 'reject':
                reject_leave_request(leave, request.user, remarks)
                messages.success(request, f"Leave request #{leave.id} REJECTED.")
            elif action == 'clarification':
                request_leave_clarification(leave, request.user, remarks)
                messages.success(request, f"Clarification requested for leave #{leave.id}.")
            elif action == 'cancel':
                cancel_leave_request(leave, request.user, remarks)
                messages.success(request, f"Leave #{leave.id} CANCELLED.")
        except (ValidationError, PermissionDenied, Exception) as e:
            msg = e.message if hasattr(e, 'message') else (", ".join(e.messages) if hasattr(e, 'messages') else str(e))
            messages.error(request, msg)

    # Base queryset: pending and clarification
    if is_hr_admin:
        base_qs = Leave.objects.filter(approval_status__in=['PENDING', 'CLARIFICATION'])
    else:
        base_qs = Leave.objects.filter(approver=user_emp, approval_status__in=['PENDING', 'CLARIFICATION'])

    # Exclude own requests (no self-approval)
    base_qs = base_qs.exclude(user=request.user)
    if user_emp:
        base_qs = base_qs.exclude(employee=user_emp)

    # Filters
    emp_filter = request.GET.get('employee')
    dept_filter = request.GET.get('department')
    branch_filter = request.GET.get('branch')
    type_filter = request.GET.get('leave_type')
    status_filter = request.GET.get('status')

    if status_filter in ('PENDING', 'CLARIFICATION'):
        base_qs = base_qs.filter(approval_status=status_filter)
    if emp_filter:
        base_qs = base_qs.filter(employee_id=emp_filter)
    if dept_filter:
        base_qs = base_qs.filter(employee__department_id=dept_filter)
    if branch_filter:
        base_qs = base_qs.filter(employee__branch_id=branch_filter)
    if type_filter:
        base_qs = base_qs.filter(leave_type_ref_id=type_filter)

    leaves = base_qs.select_related(
        'employee', 'employee__department', 'employee__branch',
        'leave_type_ref', 'approver', 'user'
    ).order_by('-from_date', '-id')

    paginator = Paginator(leaves, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    departments = Department.objects.filter(is_active=True).order_by('sort_order', 'name')
    branches = Branch.objects.filter(is_active=True).order_by('sort_order', 'name')
    leave_types = LeaveType.objects.filter(is_active=True).order_by('sort_order', 'name')

    context = {
        'role': role,
        'base_template': base_template,
        'leaves': page_obj,
        'departments': departments,
        'branches': branches,
        'leave_types': leave_types,
        'filter_emp': emp_filter,
        'filter_dept': dept_filter,
        'filter_branch': branch_filter,
        'filter_type': type_filter,
    }
    return render(request, 'leave/manage_leave.html', context)


def render_role_leave_list(request, role, base_template):
    """
    Unified Leave List view with filters and openpyxl / WeasyPrint exports.
    """
    user_emp = getattr(request.user, 'employee_profile', None)
    is_hr_admin = request.user.is_superuser or (user_emp and user_emp.is_hr)

    if is_hr_admin or (user_emp and user_emp.is_accountant):
        base_qs = Leave.objects.all()
    elif user_emp and user_emp.is_manager:
        base_qs = Leave.objects.filter(
            Q(approver=user_emp) | Q(employee__reporting_manager=user_emp) | Q(employee=user_emp) | Q(user=request.user)
        )
    else:
        base_qs = Leave.objects.filter(Q(employee=user_emp) | Q(user=request.user))

    # Filters
    emp_filter = request.GET.get('employee')
    dept_filter = request.GET.get('department')
    branch_filter = request.GET.get('branch')
    type_filter = request.GET.get('leave_type')
    status_filter = request.GET.get('status')
    start_filter = request.GET.get('start_date')
    end_filter = request.GET.get('end_date')

    if emp_filter:
        base_qs = base_qs.filter(employee_id=emp_filter)
    if dept_filter:
        base_qs = base_qs.filter(employee__department_id=dept_filter)
    if branch_filter:
        base_qs = base_qs.filter(employee__branch_id=branch_filter)
    if type_filter:
        base_qs = base_qs.filter(leave_type_ref_id=type_filter)
    if status_filter:
        base_qs = base_qs.filter(approval_status=status_filter)
    if start_filter:
        base_qs = base_qs.filter(to_date__gte=start_filter)
    if end_filter:
        base_qs = base_qs.filter(from_date__lte=end_filter)

    leaves_qs = base_qs.select_related(
        'employee', 'employee__department', 'employee__branch',
        'leave_type_ref', 'approver', 'decided_by', 'user'
    ).order_by('-from_date', '-id')

    # Export handling
    export_fmt = request.GET.get('export')
    if export_fmt == 'excel':
        stream = export_leave_list_excel(leaves_qs)
        resp = HttpResponse(stream.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename="leave_requests_{timezone.localdate().strftime("%Y%m%d")}.xlsx"'
        return resp
    elif export_fmt == 'pdf':
        from weasyprint import HTML
        html_str = render_to_string('leave/leave_list_pdf.html', {
            'leaves': leaves_qs,
            'generated_at': timezone.now(),
        })
        base_url = request.build_absolute_uri('/')
        pdf_bytes = HTML(string=html_str, base_url=base_url).write_pdf()
        resp = HttpResponse(pdf_bytes, content_type='application/pdf')
        resp['Content-Disposition'] = f'attachment; filename="leave_requests_{timezone.localdate().strftime("%Y%m%d")}.pdf"'
        return resp

    paginator = Paginator(leaves_qs, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    departments = Department.objects.filter(is_active=True).order_by('sort_order', 'name')
    branches = Branch.objects.filter(is_active=True).order_by('sort_order', 'name')
    leave_types = LeaveType.objects.filter(is_active=True).order_by('sort_order', 'name')

    context = {
        'role': role,
        'base_template': base_template,
        'leaves': page_obj,
        'departments': departments,
        'branches': branches,
        'leave_types': leave_types,
        'status_choices': Leave._meta.get_field('approval_status').choices,
        'filter_emp': emp_filter,
        'filter_dept': dept_filter,
        'filter_branch': branch_filter,
        'filter_type': type_filter,
        'filter_status': status_filter,
        'filter_start': start_filter,
        'filter_end': end_filter,
    }
    return render(request, 'leave/leave_list.html', context)


def render_role_leave_statement(request, role, base_template, employee_id=None):
    """
    Unified Leave Statement view: annual summary cards + running ledger entries.
    """
    user_emp = getattr(request.user, 'employee_profile', None)
    target_id = employee_id or request.GET.get('employee')

    if target_id:
        target_emp = get_object_or_404(Employee, pk=target_id)
    else:
        target_emp = user_emp

    if not target_emp:
        if request.user.is_superuser or (user_emp and (user_emp.is_hr or user_emp.is_accountant)):
            target_emp = Employee.objects.filter(user__is_active=True).first()
    if not target_emp:
        raise PermissionDenied("Employee profile not found.")

    # Permission check: self, reporting manager, HR, accountant, superuser
    can_view = False
    if request.user.is_superuser:
        can_view = True
    elif user_emp:
        if user_emp.id == target_emp.id:
            can_view = True
        elif user_emp.is_hr or user_emp.is_accountant:
            can_view = True
        elif target_emp.reporting_manager_id == user_emp.id:
            can_view = True

    if not can_view:
        raise PermissionDenied("You do not have permission to view this leave statement.")

    try:
        year = int(request.GET.get('year', timezone.localdate().year))
    except (ValueError, TypeError):
        year = timezone.localdate().year

    summary = get_employee_leave_summary(target_emp, year=year)
    entries = LeaveLedger.objects.filter(
        employee=target_emp, date__year=year
    ).select_related('leave_type', 'leave', 'created_by').order_by('date', 'id')

    prior_bal = LeaveLedger.objects.filter(
        employee=target_emp, date__lt=datetime.date(year, 1, 1)
    ).aggregate(s=Sum('days'))['s'] or Decimal('0.00')

    running_bal = prior_bal
    entries_with_bal = []
    for e in entries:
        running_bal += e.days
        entries_with_bal.append({'entry': e, 'running_balance': running_bal})

    export_fmt = request.GET.get('export')
    if export_fmt == 'excel':
        stream = export_leave_statement_excel(target_emp, year, summary, entries_with_bal)
        resp = HttpResponse(stream.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        code = target_emp.employee_code or f"AMMS-{target_emp.id:04d}"
        resp['Content-Disposition'] = f'attachment; filename="leave_statement_{code}_{year}.xlsx"'
        return resp
    elif export_fmt == 'pdf':
        from weasyprint import HTML
        html_str = render_to_string('leave/leave_statement_pdf.html', {
            'employee': target_emp,
            'year': year,
            'summary': summary,
            'entries': entries_with_bal,
            'generated_at': timezone.now(),
        })
        base_url = request.build_absolute_uri('/')
        pdf_bytes = HTML(string=html_str, base_url=base_url).write_pdf()
        code = target_emp.employee_code or f"AMMS-{target_emp.id:04d}"
        resp = HttpResponse(pdf_bytes, content_type='application/pdf')
        resp['Content-Disposition'] = f'attachment; filename="leave_statement_{code}_{year}.pdf"'
        return resp

    # List available years
    distinct_years = LeaveLedger.objects.filter(employee=target_emp).dates('date', 'year')
    years_available = sorted(list({d.year for d in distinct_years} | {timezone.localdate().year}), reverse=True)

    context = {
        'role': role,
        'base_template': base_template,
        'employee': target_emp,
        'year': year,
        'years_available': years_available,
        'leave_summary': summary,
        'entries': entries_with_bal,
    }
    return render(request, 'leave/leave_statement.html', context)


@login_required
def render_import_opening_balances(request, base_template):
    """
    Opening balances import view for HR and Admin.
    Supports Excel template download, preview with error highlighting, and atomic confirm.
    """
    user_emp = getattr(request.user, 'employee_profile', None)
    if not (request.user.is_superuser or (user_emp and user_emp.is_hr)):
        raise PermissionDenied("Only HR and Admin can import opening balances.")

    if request.GET.get('action') == 'template':
        stream = generate_opening_balances_template()
        resp = HttpResponse(stream.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = 'attachment; filename="opening_leave_balances_template.xlsx"'
        return resp

    import base64
    import uuid

    preview = None
    upload_token = ''
    redirect_name = 'hr_import_opening_balances' if 'hr' in base_template else 'admin_import_opening_balances'
    if request.method == 'POST':
        is_confirm = request.POST.get('confirm') == '1'
        excel_file = request.FILES.get('file')
        if is_confirm and not excel_file:
            # The preview step kept the uploaded file in the session; commit exactly that file.
            token = request.POST.get('upload_token', '')
            stored = request.session.pop(f'ob_upload_{token}', None) if token else None
            if stored:
                excel_file = io.BytesIO(base64.b64decode(stored))
            else:
                messages.error(request, "The preview has expired. Please upload the file again.")
                return redirect(redirect_name)
        if not excel_file:
            messages.error(request, "Please select an Excel file (.xlsx) to upload.")
        else:
            try:
                if is_confirm:
                    res = process_opening_balances_excel(excel_file, user=request.user, dry_run=False)
                    messages.success(request, f"Successfully imported {res['valid_count']} opening balance record(s).")
                    return redirect(redirect_name)
                raw = excel_file.read()
                preview = process_opening_balances_excel(io.BytesIO(raw), user=request.user, dry_run=True)
                if not preview['has_errors'] and preview['valid_count'] > 0:
                    upload_token = uuid.uuid4().hex
                    request.session[f'ob_upload_{upload_token}'] = base64.b64encode(raw).decode('ascii')
            except Exception as e:
                messages.error(request, f"Error processing file: {str(e)}")

    context = {
        'role': 'HR' if 'hr' in base_template else 'Admin',
        'base_template': base_template,
        'preview': preview,
        'upload_token': upload_token,
    }
    return render(request, 'leave/import_opening_balances.html', context)


@login_required
def render_year_end_reviews(request, base_template):
    """
    Year-End Leave Reviews view for HR and Admin (D10).
    Review unused days, carry over max 5, and resolve pending reviews with adjustments.
    """
    user_emp = getattr(request.user, 'employee_profile', None)
    if not (request.user.is_superuser or (user_emp and user_emp.is_hr)):
        raise PermissionDenied("Only HR and Admin can review year-end leave.")

    if request.method == 'POST':
        review_id = request.POST.get('review_id')
        resolution_note = request.POST.get('resolution_note', '').strip()
        adjustment_days = request.POST.get('adjustment_days')
        review = get_object_or_404(LeaveYearReview, pk=review_id)

        try:
            with transaction.atomic():
                review.status = 'RESOLVED'
                review.resolution_note = resolution_note
                review.resolved_by = request.user
                review.resolved_at = timezone.now()
                review.save()

                if adjustment_days and str(adjustment_days).strip():
                    adj_dec = Decimal(str(adjustment_days).strip())
                    if adj_dec != Decimal('0.00'):
                        annual_type = LeaveType.objects.filter(code='ANNUAL').first()
                        LeaveLedger.objects.create(
                            employee=review.employee,
                            leave_type=annual_type,
                            date=timezone.localdate(),
                            entry_type='ADJUSTMENT',
                            days=adj_dec,
                            note=f"Year {review.year} review adjustment: {resolution_note or 'HR resolution'}",
                            created_by=request.user
                        )

            messages.success(request, f"Year-end review for {review.employee.name} has been RESOLVED.")
            return redirect('hr_leave_year_reviews' if 'hr' in base_template else 'admin_leave_year_reviews')
        except Exception as e:
            messages.error(request, f"Error resolving review: {str(e)}")

    # Filter by year and status
    year_filter = request.GET.get('year')
    status_filter = request.GET.get('status', 'PENDING')

    reviews_qs = LeaveYearReview.objects.all().select_related('employee', 'resolved_by')
    if year_filter:
        reviews_qs = reviews_qs.filter(year=year_filter)
    if status_filter and status_filter != 'ALL':
        reviews_qs = reviews_qs.filter(status=status_filter)

    reviews = reviews_qs.order_by('-year', 'status', 'employee__user__first_name', 'employee__user__last_name', 'employee__user__username')
    years_available = sorted(list({r.year for r in LeaveYearReview.objects.all()} | {timezone.localdate().year}), reverse=True)

    context = {
        'role': 'HR' if 'hr' in base_template else 'Admin',
        'base_template': base_template,
        'reviews': reviews,
        'years_available': years_available,
        'filter_year': year_filter,
        'filter_status': status_filter,
    }
    return render(request, 'leave/year_end_reviews.html', context)


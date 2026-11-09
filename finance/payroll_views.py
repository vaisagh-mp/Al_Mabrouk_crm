import io
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import models
from django.http import HttpResponse, Http404, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from Admin.models import Employee, SalaryStructure, update_employee_salary_structure, check_payroll_lock_for_employee
from Admin.permissions import can_view_salary
from finance.models import (
    Payroll, PayrollLine, SalaryAdvance, AdvanceInstalment,
    SalaryIncrement, Payslip
)
from finance.payroll_service import (
    create_advance_schedule, create_or_update_payslip, add_months
)
from finance.services import get_month_range


def _parse_month_param(month_str):
    today = timezone.localdate()
    if not month_str:
        return today.replace(day=1)
    try:
        dt = datetime.strptime(month_str, '%Y-%m').date()
        return dt.replace(day=1)
    except (ValueError, TypeError):
        return today.replace(day=1)


def _get_base_template(request):
    profile = getattr(request.user, 'employee_profile', None)
    if profile and profile.is_hr and not (request.user.is_superuser or profile.is_accountant):
        return 'hr/hrbase.html'
    if request.user.is_superuser and request.GET.get('from') == 'admin':
        return 'Admin/adminbase.html'
    return 'finance/financebase.html'


# =====================================================================
# 1. SALARY STRUCTURES
# =====================================================================

@login_required
def salary_structures_list(request):
    if not can_view_salary(request.user):
        raise PermissionDenied

    search = request.GET.get('search', '').strip()
    employees = Employee.objects.filter(user__is_active=True).select_related('user', 'department').order_by('user__first_name', 'user__username')
    if search:
        employees = employees.filter(
            models.Q(user__first_name__icontains=search) |
            models.Q(user__last_name__icontains=search) |
            models.Q(employee_code__icontains=search)
        )

    today = timezone.localdate()
    rows = []
    for emp in employees:
        current_struct = emp.current_salary_structure

        upcoming_struct = emp.salary_structures.filter(
            effective_from__gt=today
        ).order_by('effective_from').first()

        rows.append({
            'employee': emp,
            'current': current_struct,
            'upcoming': upcoming_struct,
            'salary': emp.salary,
        })

    base_template = _get_base_template(request)
    return render(request, 'payroll/salary_structures.html', {
        'rows': rows,
        'search': search,
        'base_template': base_template,
    })


# =====================================================================
# 2. SALARY INCREMENTS
# =====================================================================

@login_required
def salary_increments_list(request):
    if not can_view_salary(request.user):
        raise PermissionDenied

    increments = SalaryIncrement.objects.select_related('employee__user', 'proposed_by', 'approved_by').order_by('-effective_from', '-created_at')
    status_filter = request.GET.get('status')
    if status_filter:
        increments = increments.filter(status=status_filter)

    active_employees = Employee.objects.filter(user__is_active=True).select_related('user').order_by('user__first_name', 'user__username')
    base_template = _get_base_template(request)
    return render(request, 'payroll/increments.html', {
        'increments': increments,
        'status_filter': status_filter,
        'active_employees': active_employees,
        'base_template': base_template,
        'is_admin': request.user.is_superuser,
    })


@login_required
@require_POST
def salary_increment_propose(request):
    if not can_view_salary(request.user):
        raise PermissionDenied

    emp_id = request.POST.get('employee_id')
    inc_type = request.POST.get('type', 'ANNUAL')
    effective_from_str = request.POST.get('effective_from')
    new_basic_str = request.POST.get('new_basic', '0.00')
    new_housing_str = request.POST.get('new_housing', '0.00')
    new_transport_str = request.POST.get('new_transport', '0.00')
    new_other_str = request.POST.get('new_other', '0.00')
    reason = request.POST.get('reason', '').strip()

    employee = get_object_or_404(Employee, id=emp_id)
    try:
        effective_from = datetime.strptime(effective_from_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        messages.error(request, "Invalid effective from date.")
        return redirect('salary_increments_list')

    from django.core.exceptions import ValidationError
    from Admin.models import check_payroll_lock_for_employee
    try:
        check_payroll_lock_for_employee(employee, effective_from, is_open_ended=True)
    except ValidationError as e:
        err_msg = e.messages[0] if hasattr(e, 'messages') else str(e)
        messages.error(request, f"Cannot propose increment: {err_msg}")
        return redirect('salary_increments_list')

    # Get current active structure snapshot
    curr = employee.current_salary_structure
    prev_struct = curr
    prev_basic = curr.basic if curr else (employee.salary or Decimal('0.00'))
    prev_housing = curr.housing_allowance if curr else Decimal('0.00')
    prev_transport = curr.transport_allowance if curr else Decimal('0.00')
    prev_other = curr.other_allowances if curr else Decimal('0.00')
    prev_total = curr.total if curr else (employee.salary or Decimal('0.00'))

    try:
        new_basic = Decimal(new_basic_str)
        new_housing = Decimal(new_housing_str)
        new_transport = Decimal(new_transport_str)
        new_other = Decimal(new_other_str)
    except (InvalidOperation, ValueError):
        messages.error(request, "Invalid salary component amounts.")
        return redirect('salary_increments_list')

    SalaryIncrement.objects.create(
        employee=employee,
        type=inc_type,
        effective_from=effective_from,
        previous_structure=prev_struct,
        previous_basic=prev_basic,
        previous_housing=prev_housing,
        previous_transport=prev_transport,
        previous_other=prev_other,
        previous_total=prev_total,
        new_basic=new_basic,
        new_housing=new_housing,
        new_transport=new_transport,
        new_other=new_other,
        reason=reason,
        status='PENDING',
        proposed_by=request.user,
    )
    messages.success(request, f"Salary increment proposal submitted for {employee.name}.")
    return redirect('salary_increments_list')


@login_required
@require_POST
def salary_increment_action(request, pk, action):
    # Admin approves (not the proposer)
    if not request.user.is_superuser:
        raise PermissionDenied

    increment = get_object_or_404(SalaryIncrement.objects.select_related('employee'), pk=pk)
    if increment.status != 'PENDING':
        messages.error(request, f"Increment is already {increment.get_status_display()}.")
        return redirect('salary_increments_list')

    if action == 'approve':
        if request.user == increment.proposed_by:
            messages.error(request, "Approver cannot be the same user who proposed the increment.")
            return redirect('salary_increments_list')

        from django.core.exceptions import ValidationError
        from Admin.models import check_payroll_lock_for_employee
        try:
            check_payroll_lock_for_employee(increment.employee, increment.effective_from, is_open_ended=True)
        except ValidationError as e:
            err_msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            messages.error(request, f"Cannot approve increment into locked payroll month: {err_msg}")
            return redirect('salary_increments_list')

        update_employee_salary_structure(
            employee=increment.employee,
            basic=increment.new_basic,
            housing_allowance=increment.new_housing,
            transport_allowance=increment.new_transport,
            other_allowances=increment.new_other,
            effective_from=increment.effective_from,
            user=request.user,
            notes=f"Increment ({increment.get_type_display()}): {increment.reason}"
        )
        increment.status = 'APPROVED'
        increment.approved_by = request.user
        increment.approved_at = timezone.now()
        increment.remarks = request.POST.get('remarks', '').strip()
        increment.save()
        messages.success(request, f"Salary increment for {increment.employee.name} approved.")

    elif action == 'reject':
        increment.status = 'REJECTED'
        increment.approved_by = request.user
        increment.approved_at = timezone.now()
        increment.remarks = request.POST.get('remarks', '').strip()
        increment.save()
        messages.info(request, f"Salary increment for {increment.employee.name} rejected.")

    return redirect('salary_increments_list')


# =====================================================================
# 3. SALARY ADVANCES
# =====================================================================

@login_required
def salary_advances_list(request):
    if not can_view_salary(request.user):
        raise PermissionDenied

    advances = SalaryAdvance.objects.select_related('employee__user', 'created_by', 'approved_by').prefetch_related('instalments_schedule').order_by('-request_date', '-id')
    status_filter = request.GET.get('status')
    emp_filter = request.GET.get('employee_id')
    if status_filter:
        advances = advances.filter(status=status_filter)
    if emp_filter:
        advances = advances.filter(employee_id=emp_filter)

    active_employees = Employee.objects.filter(user__is_active=True).select_related('user').order_by('user__first_name', 'user__username')
    base_template = _get_base_template(request)
    is_admin_or_acct = request.user.is_superuser or (getattr(request.user, 'employee_profile', None) and request.user.employee_profile.is_accountant)

    return render(request, 'payroll/advances.html', {
        'advances': advances,
        'status_filter': status_filter,
        'emp_filter': emp_filter,
        'active_employees': active_employees,
        'base_template': base_template,
        'is_admin_or_acct': is_admin_or_acct,
    })


@login_required
@require_POST
def salary_advance_request(request):
    if not can_view_salary(request.user):
        raise PermissionDenied

    emp_id = request.POST.get('employee_id')
    amount_str = request.POST.get('amount')
    instalments_str = request.POST.get('instalments', '1')
    start_month_str = request.POST.get('start_month')
    reason = request.POST.get('reason', '').strip()

    employee = get_object_or_404(Employee, id=emp_id)
    try:
        amount = Decimal(amount_str)
        instalments = int(instalments_str)
        start_month = datetime.strptime(start_month_str, '%Y-%m').date().replace(day=1)
        if amount <= Decimal('0.00') or instalments <= 0:
            raise ValueError
    except (ValueError, TypeError):
        messages.error(request, "Invalid amount, instalments, or start month.")
        return redirect('salary_advances_list')

    # Locked month check: cannot start advance in a locked payroll month
    if Payroll.objects.filter(employee=employee, month=start_month, status__in=['APPROVED', 'PAID']).exists():
        messages.error(request, "Cannot start advance in a month that already has Approved or Paid payroll.")
        return redirect('salary_advances_list')

    monthly_ded = round(amount / Decimal(instalments), 2)
    advance = SalaryAdvance.objects.create(
        employee=employee,
        amount=amount,
        instalments=instalments,
        monthly_deduction=monthly_ded,
        start_month=start_month,
        reason=reason,
        status='REQUESTED',
        created_by=request.user,
    )
    messages.success(request, f"Salary advance request #{advance.id} recorded for {employee.name}.")
    return redirect('salary_advances_list')


@login_required
def salary_advance_detail(request, pk):
    advance = get_object_or_404(SalaryAdvance.objects.select_related('employee__user', 'created_by', 'approved_by'), pk=pk)
    # Accessible to can_view_salary or own employee
    is_owner = (getattr(request.user, 'employee_profile', None) and advance.employee_id == request.user.employee_profile.id)
    if not (can_view_salary(request.user) or is_owner):
        raise PermissionDenied

    instalments = advance.instalments_schedule.all().order_by('month', 'sort_order')
    base_template = _get_base_template(request)
    is_admin_or_acct = request.user.is_superuser or (getattr(request.user, 'employee_profile', None) and request.user.employee_profile.is_accountant)

    return render(request, 'payroll/advance_detail.html', {
        'advance': advance,
        'instalments': instalments,
        'base_template': base_template,
        'is_admin_or_acct': is_admin_or_acct,
        'is_owner': is_owner,
    })


@login_required
@require_POST
def salary_advance_action(request, pk, action):
    # Admin or Accountant approves (not requester), Accounts marks disbursed (ACTIVE)
    is_admin_or_acct = request.user.is_superuser or (getattr(request.user, 'employee_profile', None) and request.user.employee_profile.is_accountant)
    if not is_admin_or_acct:
        raise PermissionDenied

    advance = get_object_or_404(SalaryAdvance, pk=pk)

    if action == 'approve':
        if advance.status != 'REQUESTED':
            messages.error(request, f"Cannot approve advance with status {advance.get_status_display()}.")
            return redirect('salary_advances_list')
        if request.user == advance.created_by:
            messages.error(request, "Approver cannot be the same user who requested the advance.")
            return redirect('salary_advances_list')
        advance.status = 'APPROVED'
        advance.approved_by = request.user
        advance.approved_at = timezone.now()
        advance.remarks = (advance.remarks or '') + f"\nApproved by {request.user.username} at {timezone.now().strftime('%d/%m/%Y %H:%M')}."
        advance.save()
        messages.success(request, f"Advance #{advance.id} approved.")

    elif action == 'disburse':
        if advance.status != 'APPROVED':
            messages.error(request, "Only APPROVED advances can be marked as disbursed.")
            return redirect('salary_advances_list')
        advance.status = 'ACTIVE'
        advance.disbursed_on = timezone.localdate()
        advance.save()
        create_advance_schedule(advance)
        messages.success(request, f"Advance #{advance.id} disbursed. Instalment schedule created starting {advance.start_month.strftime('%b %Y')}.")

    elif action == 'reject':
        if advance.status != 'REQUESTED':
            messages.error(request, f"Cannot reject advance with status {advance.get_status_display()}.")
            return redirect('salary_advances_list')
        advance.status = 'REJECTED'
        advance.approved_by = request.user
        advance.approved_at = timezone.now()
        advance.remarks = (advance.remarks or '') + f"\nRejected by {request.user.username}."
        advance.save()
        messages.info(request, f"Advance #{advance.id} rejected.")

    elif action == 'cancel':
        if advance.status in ('COMPLETED', 'ACTIVE'):
            messages.error(request, "Cannot cancel active or completed advance.")
            return redirect('salary_advances_list')
        advance.status = 'CANCELLED'
        advance.save(update_fields=['status'])
        messages.info(request, f"Advance #{advance.id} cancelled.")

    return redirect('salary_advances_list')


@login_required
@require_POST
def salary_advance_skip_instalment(request, pk):
    # HR / Accounts can Skip a future instalment (reason required -> moved to end)
    if not can_view_salary(request.user):
        raise PermissionDenied

    instalment = get_object_or_404(AdvanceInstalment.objects.select_related('advance'), pk=pk)
    if instalment.status != 'SCHEDULED':
        messages.error(request, f"Cannot skip instalment with status {instalment.get_status_display()}.")
        return redirect('salary_advance_detail', pk=instalment.advance_id)

    # Locked month check
    if Payroll.objects.filter(employee=instalment.advance.employee, month=instalment.month, status__in=['APPROVED', 'PAID']).exists():
        messages.error(request, "Cannot skip instalment in a month that already has Approved or Paid payroll.")
        return redirect('salary_advance_detail', pk=instalment.advance_id)

    reason = request.POST.get('skip_reason', '').strip()
    if not reason:
        messages.error(request, "Reason is required to skip an instalment.")
        return redirect('salary_advance_detail', pk=instalment.advance_id)

    # Move to end
    last_inst = instalment.advance.instalments_schedule.order_by('-month').first()
    if last_inst:
        next_m = add_months(last_inst.month, 1)
        next_order = last_inst.sort_order + 1
    else:
        next_m = add_months(instalment.month, 1)
        next_order = instalment.sort_order + 1

    instalment.status = 'SKIPPED'
    instalment.skip_reason = reason
    instalment.payroll = None
    instalment.save()

    AdvanceInstalment.objects.create(
        advance=instalment.advance,
        month=next_m,
        amount=instalment.amount,
        status='SCHEDULED',
        sort_order=next_order,
    )
    messages.success(request, f"Instalment for {instalment.month.strftime('%b %Y')} skipped and deferred to {next_m.strftime('%b %Y')}.")
    return redirect('salary_advance_detail', pk=instalment.advance_id)


# =====================================================================
# 4. PAYSLIPS
# =====================================================================

@login_required
def payslips_list(request):
    if not can_view_salary(request.user):
        raise PermissionDenied

    month_param = request.GET.get('month')
    selected_month = _parse_month_param(month_param)
    first_day, last_day, _ = get_month_range(selected_month)

    payrolls = list(
        Payroll.objects.filter(month=first_day)
        .select_related('employee', 'employee__user', 'payslip')
        .order_by('employee__user__first_name', 'employee__user__username')
    )

    status_filter = request.GET.get('status')
    if status_filter:
        payrolls = [p for p in payrolls if p.status == status_filter]

    base_template = _get_base_template(request)
    return render(request, 'payroll/payslips.html', {
        'selected_month': selected_month.strftime('%Y-%m'),
        'month_display': selected_month.strftime('%B %Y'),
        'payrolls': payrolls,
        'base_template': base_template,
        'status_filter': status_filter,
    })


@login_required
@require_POST
def payslip_generate_month(request):
    if not can_view_salary(request.user):
        raise PermissionDenied

    month_param = request.POST.get('month')
    selected_month = _parse_month_param(month_param)
    first_day, _, _ = get_month_range(selected_month)

    paid_payrolls = Payroll.objects.filter(month=first_day, status='PAID').select_related('employee', 'employee__user')
    count = 0
    for p in paid_payrolls:
        create_or_update_payslip(p, generated_by=request.user)
        count += 1

    messages.success(request, f"Generated {count} payslips for {selected_month.strftime('%B %Y')}.")
    return redirect(f"{reverse('payslips_list')}?month={selected_month.strftime('%Y-%m')}")


@login_required
@require_POST
def payslip_regenerate(request, pk):
    if not can_view_salary(request.user):
        raise PermissionDenied

    payslip = get_object_or_404(Payslip.objects.select_related('payroll', 'payroll__employee'), pk=pk)
    if payslip.payroll.status != 'PAID':
        messages.error(request, "Only PAID payroll records can have payslips regenerated.")
        return redirect('payslips_list')

    create_or_update_payslip(payslip.payroll, generated_by=request.user, bump_version=True)
    messages.success(request, f"Payslip {payslip.number} regenerated (v{payslip.version + 1}).")
    return redirect(f"{reverse('payslips_list')}?month={payslip.payroll.month.strftime('%Y-%m')}")


@login_required
def payslip_download(request, pk):
    payslip = get_object_or_404(Payslip.objects.select_related('payroll', 'payroll__employee'), pk=pk)

    # Permission check: owner, HR, Accountant, Admin
    can_download = (
        request.user.is_superuser or
        can_view_salary(request.user) or
        (payslip.payroll.employee.user_id == request.user.id)
    )
    if not can_download:
        raise PermissionDenied

    if not payslip.file:
        raise Http404("Payslip file not found.")

    try:
        content = payslip.file.read()
    except Exception:
        raise Http404("Unable to read payslip file.")

    response = HttpResponse(content, content_type='application/pdf')
    emp_code = payslip.payroll.employee.employee_code or f"AMMS-{payslip.payroll.employee.id:04d}"
    filename = f"payslip_{emp_code}_{payslip.payroll.month.strftime('%Y%m')}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


@login_required
def payslips_download_zip(request):
    if not can_view_salary(request.user):
        raise PermissionDenied

    month_param = request.GET.get('month')
    selected_month = _parse_month_param(month_param)
    first_day, _, _ = get_month_range(selected_month)

    payslips = list(
        Payslip.objects.filter(payroll__month=first_day)
        .select_related('payroll__employee', 'payroll__employee__user')
    )
    if not payslips:
        messages.warning(request, f"No payslips found for {selected_month.strftime('%B %Y')}.")
        return redirect(f"{reverse('payslips_list')}?month={selected_month.strftime('%Y-%m')}")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for ps in payslips:
            if ps.file:
                try:
                    emp_code = ps.payroll.employee.employee_code or f"AMMS-{ps.payroll.employee.id:04d}"
                    fn = f"payslip_{ps.number}_{emp_code}.pdf"
                    zf.writestr(fn, ps.file.read())
                except Exception:
                    pass

    buffer.seek(0)
    month_str = selected_month.strftime('%B_%Y')
    response = HttpResponse(buffer.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="payslips_{month_str}.zip"'
    return response


# =====================================================================
# 5. MY PAYSLIPS (EMPLOYEE SELF-SERVICE)
# =====================================================================

@login_required
def my_payslips(request):
    profile = getattr(request.user, 'employee_profile', None)
    if not profile:
        messages.info(request, "No employee profile found for your account.")
        return render(request, 'payroll/my_payslips.html', {
            'payrolls': [],
            'advances': [],
            'base_template': 'employee/employeebase.html',
        })

    payrolls = Payroll.objects.filter(
        employee=profile,
        status='PAID'
    ).select_related('payslip').order_by('-month')

    advances = SalaryAdvance.objects.filter(
        employee=profile
    ).prefetch_related('instalments_schedule').order_by('-request_date')

    # Select base template depending on role
    if profile.is_manager:
        base_template = 'Manager/managerbase.html'
    elif profile.is_hr:
        base_template = 'hr/hrbase.html'
    elif profile.is_accountant:
        base_template = 'finance/financebase.html'
    else:
        base_template = 'employee/employeebase.html'

    return render(request, 'payroll/my_payslips.html', {
        'employee': profile,
        'payrolls': payrolls,
        'advances': advances,
        'base_template': base_template,
    })


# =====================================================================
# 6. MANUAL PAYROLL LINES (DRAFT ROWS)
# =====================================================================

@login_required
@require_POST
def fin_payroll_add_line(request, payroll_id):
    # Accountant + Admin only
    is_admin_or_acct = request.user.is_superuser or (getattr(request.user, 'employee_profile', None) and request.user.employee_profile.is_accountant)
    if not is_admin_or_acct:
        raise PermissionDenied

    payroll = get_object_or_404(Payroll, id=payroll_id)
    if payroll.status != 'DRAFT':
        messages.error(request, "Manual lines can only be added to DRAFT payroll rows.")
        return redirect(f"{reverse('fin_payroll')}?month={payroll.month.strftime('%Y-%m')}")

    kind = request.POST.get('kind', 'DEDUCTION')
    code = request.POST.get('code', 'OTHER')
    desc = request.POST.get('description', '').strip()
    amount_str = request.POST.get('amount', '0.00')

    try:
        amount = Decimal(amount_str)
        if amount <= Decimal('0.00'):
            raise ValueError
    except (ValueError, InvalidOperation):
        messages.error(request, "Amount must be greater than zero.")
        return redirect(f"{reverse('fin_payroll')}?month={payroll.month.strftime('%Y-%m')}")

    PayrollLine.objects.create(
        payroll=payroll,
        kind=kind,
        code=code,
        description=desc,
        amount=amount,
        source='manual',
        created_by=request.user,
        sort_order=90,
    )

    # Sync totals
    manual_earnings = sum(l.amount for l in payroll.lines.filter(source='manual', kind='EARNING'))
    manual_deductions = sum(l.amount for l in payroll.lines.filter(source='manual', kind='DEDUCTION'))
    allowance_base = sum(l.amount for l in payroll.lines.filter(source='auto', kind='EARNING').exclude(code__in=['BASIC', 'OT']))
    advance_ded = sum(l.amount for l in payroll.lines.filter(code='ADVANCE'))

    payroll.allowances = allowance_base + manual_earnings
    payroll.deductions = advance_ded + manual_deductions
    payroll.save(update_fields=['allowances', 'deductions', 'updated_at'])

    messages.success(request, f"Manual {kind.lower()} line added to payroll for {payroll.employee.name}.")
    return redirect(f"{reverse('fin_payroll')}?month={payroll.month.strftime('%Y-%m')}")


@login_required
@require_POST
def fin_payroll_delete_line(request, line_id):
    is_admin_or_acct = request.user.is_superuser or (getattr(request.user, 'employee_profile', None) and request.user.employee_profile.is_accountant)
    if not is_admin_or_acct:
        raise PermissionDenied

    line = get_object_or_404(PayrollLine.objects.select_related('payroll'), id=line_id)
    payroll = line.payroll
    if payroll.status != 'DRAFT':
        messages.error(request, "Manual lines can only be deleted from DRAFT payroll rows.")
        return redirect(f"{reverse('fin_payroll')}?month={payroll.month.strftime('%Y-%m')}")

    line.delete()

    manual_earnings = sum(l.amount for l in payroll.lines.filter(source='manual', kind='EARNING'))
    manual_deductions = sum(l.amount for l in payroll.lines.filter(source='manual', kind='DEDUCTION'))
    allowance_base = sum(l.amount for l in payroll.lines.filter(source='auto', kind='EARNING').exclude(code__in=['BASIC', 'OT']))
    advance_ded = sum(l.amount for l in payroll.lines.filter(code='ADVANCE'))

    payroll.allowances = allowance_base + manual_earnings
    payroll.deductions = advance_ded + manual_deductions
    payroll.save(update_fields=['allowances', 'deductions', 'updated_at'])

    messages.success(request, "Payroll line deleted.")
    return redirect(f"{reverse('fin_payroll')}?month={payroll.month.strftime('%Y-%m')}")

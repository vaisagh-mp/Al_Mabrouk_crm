import io
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from datetime import datetime, date, timedelta
from calendar import monthrange
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone

from Admin.models import Employee, OTApproval, check_payroll_lock_for_employee
from service_logs.models import EmployeeServiceLog
from Admin.timesheet_service import (
    approve_service_log,
    reject_service_log,
    reopen_service_log,
    approve_ot_day,
    reject_ot_day,
    approve_all_ot_for_month,
    sync_month_ot_approvals,
    can_manage_timesheet,
)


def _check_timesheet_role(request, role):
    user = request.user
    if user.is_superuser:
        return True
    emp = getattr(user, 'employee_profile', None)
    if not emp:
        return False
    if role == 'admin':
        return user.is_superuser
    elif role == 'hr':
        return emp.is_hr
    elif role == 'manager':
        return emp.is_manager
    return False


@login_required
def timesheet_approvals_view(request, role='admin'):
    if not _check_timesheet_role(request, role):
        return HttpResponseForbidden("You do not have permission to access Timesheet Approvals.")

    user = request.user
    emp_profile = getattr(user, 'employee_profile', None)
    is_admin = user.is_superuser
    is_hr = bool(emp_profile and emp_profile.is_hr)

    # Base queryset
    qs = EmployeeServiceLog.objects.all().select_related(
        'employee__user',
        'employee__department',
        'employee__branch',
        'time_log__work_order',
        'project',
        'approver__user',
        'decided_by'
    )

    # If manager only (not Admin/HR), limit to direct reports (excluding self)
    if role == 'manager' and not (is_admin or is_hr):
        qs = qs.filter(employee__reporting_manager=emp_profile).exclude(employee=emp_profile)

    # Handle POST Actions
    if request.method == 'POST':
        action = request.POST.get('action')
        log_id = request.POST.get('log_id')

        try:
            if action == 'approve':
                log = get_object_or_404(EmployeeServiceLog, pk=log_id)
                approve_service_log(log, user)
                messages.success(request, f"Timesheet for {log.employee} on {log.date} approved successfully.")

            elif action == 'reject':
                log = get_object_or_404(EmployeeServiceLog, pk=log_id)
                remarks = request.POST.get('remarks', '').strip()
                reject_service_log(log, user, remarks)
                messages.success(request, f"Timesheet for {log.employee} on {log.date} rejected.")

            elif action == 'reopen':
                log = get_object_or_404(EmployeeServiceLog, pk=log_id)
                reason = request.POST.get('reason', '').strip()
                reopen_service_log(log, user, reason)
                messages.success(request, f"Timesheet for {log.employee} on {log.date} reopened to Draft.")

            elif action == 'bulk_approve':
                selected_ids = request.POST.getlist('selected_ids')
                if not selected_ids:
                    messages.warning(request, "No timesheets selected for bulk approval.")
                else:
                    success_count = 0
                    errors = []
                    for sid in selected_ids:
                        try:
                            l = EmployeeServiceLog.objects.filter(pk=sid).first()
                            if l:
                                approve_service_log(l, user)
                                success_count += 1
                        except (ValidationError, PermissionDenied) as e:
                            errors.append(str(e))
                    if success_count:
                        messages.success(request, f"Successfully approved {success_count} timesheet(s).")
                    if errors:
                        messages.error(request, f"Some rows could not be approved: {errors[0]}")

            elif action == 'bulk_reject':
                selected_ids = request.POST.getlist('selected_ids')
                remarks = request.POST.get('remarks', '').strip()
                if not remarks:
                    messages.error(request, "Remarks are required for bulk rejection.")
                elif not selected_ids:
                    messages.warning(request, "No timesheets selected for bulk rejection.")
                else:
                    success_count = 0
                    errors = []
                    for sid in selected_ids:
                        try:
                            l = EmployeeServiceLog.objects.filter(pk=sid).first()
                            if l:
                                reject_service_log(l, user, remarks)
                                success_count += 1
                        except (ValidationError, PermissionDenied) as e:
                            errors.append(str(e))
                    if success_count:
                        messages.success(request, f"Successfully rejected {success_count} timesheet(s).")
                    if errors:
                        messages.error(request, f"Some rows could not be rejected: {errors[0]}")

        except (ValidationError, PermissionDenied) as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f"Error processing action: {e}")

        # Redirect to clean POST
        querystring = request.META.get('QUERY_STRING', '')
        redirect_url = request.path + (f"?{querystring}" if querystring else '')
        return redirect(redirect_url)

    # Filters
    emp_filter = request.GET.get('employee', '').strip()
    vessel_filter = request.GET.get('vessel', '').strip()
    project_filter = request.GET.get('project', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    status_filter = request.GET.get('status', '').strip()

    if emp_filter.isdigit():
        qs = qs.filter(employee_id=int(emp_filter))

    if vessel_filter:
        qs = qs.filter(
            Q(vessel_name__icontains=vessel_filter) |
            Q(project__vessel_name__icontains=vessel_filter)
        )

    if project_filter:
        qs = qs.filter(
            Q(time_log__work_order__work_order_number__icontains=project_filter) |
            Q(project__name__icontains=project_filter) |
            Q(project__service_reference__icontains=project_filter) |
            Q(service_reference__icontains=project_filter)
        )

    if date_from:
        try:
            df = datetime.strptime(date_from, '%Y-%m-%d').date()
            qs = qs.filter(date__gte=df)
        except ValueError:
            pass

    if date_to:
        try:
            dt = datetime.strptime(date_to, '%Y-%m-%d').date()
            qs = qs.filter(date__lte=dt)
        except ValueError:
            pass

    if status_filter in ('DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED'):
        qs = qs.filter(approval_status=status_filter)

    qs = qs.order_by('-date', '-start_time', '-id')

    # Export handling
    export_format = request.GET.get('export', '').lower()
    if export_format == 'excel':
        return _export_timesheets_excel(qs)
    elif export_format == 'pdf':
        return _export_timesheets_pdf(request, qs)

    # Pagination
    paginator = Paginator(qs, 30)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Employee options for filter dropdown
    if role == 'manager' and not (is_admin or is_hr):
        employees_list = Employee.objects.filter(
            reporting_manager=emp_profile, user__is_active=True
        ).exclude(id=emp_profile.id).select_related('user').order_by('user__first_name')
    else:
        employees_list = Employee.objects.filter(
            user__is_active=True
        ).select_related('user').order_by('user__first_name')

    base_template = 'Admin/adminbase.html'
    if role == 'hr':
        base_template = 'hr/hrbase.html'
    elif role == 'manager':
        base_template = 'Manager/managerbase.html'

    context = {
        'page_obj': page_obj,
        'role': role,
        'base_template': base_template,
        'employees_list': employees_list,
        'selected_employee': emp_filter,
        'vessel_filter': vessel_filter,
        'project_filter': project_filter,
        'date_from': date_from,
        'date_to': date_to,
        'status_filter': status_filter,
        'is_admin_or_hr': is_admin or is_hr,
        'current_user_emp_id': emp_profile.id if emp_profile else None,
    }
    return render(request, 'timesheets/timesheet_approvals.html', context)


def _export_timesheets_excel(qs):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Timesheets"

    headers = [
        "Date", "Employee Code", "Employee Name", "Work Order", "Vessel",
        "Service Ref", "Start", "Finish", "Break (min)", "Travel (min)",
        "Hours", "OT Hours", "Status", "Description", "Approver", "Remarks"
    ]
    ws.append(headers)

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1F3A52", end_color="1F3A52", fill_type="solid")
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for l in qs:
        emp = l.employee
        emp_code = emp.employee_code if emp else ""
        emp_name = emp.name if emp else ""
        wo_no = (
            l.time_log.work_order.work_order_number
            if (l.time_log and l.time_log.work_order)
            else (l.project.service_reference if l.project else "")
        )
        vessel = l.vessel_name or (l.project.vessel_name if l.project else "")
        s_ref = l.service_reference or (l.project.service_reference if l.project else "")
        start_str = l.start_time.strftime('%H:%M') if l.start_time else ""
        end_str = l.end_time.strftime('%H:%M') if l.end_time else ""
        ot_h = float(l.ot_hours) if l.ot_hours else 0.0
        tot_h = float(l.total_hours) if l.total_hours else 0.0
        approver_str = str(l.approver) if l.approver else ""

        ws.append([
            l.date.strftime('%Y-%m-%d') if l.date else "",
            emp_code,
            emp_name,
            wo_no,
            vessel,
            s_ref,
            start_str,
            end_str,
            l.break_minutes or 0,
            l.travel_minutes or 0,
            tot_h,
            ot_h,
            l.approval_status,
            l.description or "",
            approver_str,
            l.decision_remarks or "",
        ])

    for col in ws.columns:
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        max_len = max(len(str(cell.value or '')) for cell in col)
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 10), 40)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="timesheets_export.xlsx"'
    return response


def _export_timesheets_pdf(request, qs):
    from weasyprint import HTML
    html_str = render_to_string('timesheets/timesheets_pdf.html', {
        'timesheets': qs[:200],
        'generated_at': timezone.now(),
    })
    base_url = request.build_absolute_uri('/')
    pdf_bytes = HTML(string=html_str, base_url=base_url).write_pdf()
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="timesheets_report.pdf"'
    return response


@login_required
def ot_approval_view(request, role='admin'):
    if not _check_timesheet_role(request, role):
        return HttpResponseForbidden("You do not have permission to access OT Approvals.")

    user = request.user
    emp_profile = getattr(user, 'employee_profile', None)
    is_admin = user.is_superuser
    is_hr = bool(emp_profile and emp_profile.is_hr)

    # Month picker
    today = timezone.localtime(timezone.now()).date()
    month_str = request.GET.get('month', today.strftime('%Y-%m'))
    try:
        dt = datetime.strptime(month_str, '%Y-%m').date()
    except ValueError:
        dt = today
        month_str = today.strftime('%Y-%m')

    start_date = dt.replace(day=1)
    _, last_day = monthrange(dt.year, dt.month)
    end_date = dt.replace(day=last_day)

    # Sync all OT records for this month
    sync_month_ot_approvals(start_date, end_date)

    # Base queryset
    qs = OTApproval.objects.filter(
        date__gte=start_date,
        date__lte=end_date,
    ).select_related(
        'employee__user',
        'employee__department',
        'employee__branch',
        'decided_by'
    )

    # If manager only (not Admin/HR), limit to direct reports (excluding self)
    if role == 'manager' and not (is_admin or is_hr):
        qs = qs.filter(employee__reporting_manager=emp_profile).exclude(employee=emp_profile)

    # Handle POST Actions
    if request.method == 'POST':
        action = request.POST.get('action')
        ot_id = request.POST.get('ot_id')

        try:
            if action == 'approve_ot':
                ot_app = get_object_or_404(OTApproval, pk=ot_id)
                approve_ot_day(ot_app, user)
                messages.success(request, f"OT for {ot_app.employee} on {ot_app.date} approved.")

            elif action == 'reject_ot':
                ot_app = get_object_or_404(OTApproval, pk=ot_id)
                remarks = request.POST.get('remarks', '').strip()
                reject_ot_day(ot_app, user, remarks)
                messages.success(request, f"OT for {ot_app.employee} on {ot_app.date} rejected.")

            elif action == 'approve_all_month':
                count = approve_all_ot_for_month(start_date, user)
                messages.success(request, f"Approved {count} OT record(s) for {start_date.strftime('%B %Y')}.")

        except (ValidationError, PermissionDenied) as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f"Error processing OT action: {e}")

        querystring = request.META.get('QUERY_STRING', '')
        redirect_url = request.path + (f"?{querystring}" if querystring else '')
        return redirect(redirect_url)

    # Filter by status if requested
    status_filter = request.GET.get('status', '').strip()
    if status_filter in ('PENDING', 'APPROVED', 'REJECTED'):
        qs = qs.filter(status=status_filter)

    qs = qs.order_by('-date', 'employee__user__first_name', 'employee__id')

    # Summary metrics
    total_ot_hours = sum((row.ot_hours or Decimal('0.00')) for row in qs)
    total_ot_cost = sum((row.ot_cost or Decimal('0.00')) for row in qs)
    pending_count = sum(1 for row in qs if row.status == 'PENDING')
    approved_count = sum(1 for row in qs if row.status == 'APPROVED')

    base_template = 'Admin/adminbase.html'
    if role == 'hr':
        base_template = 'hr/hrbase.html'
    elif role == 'manager':
        base_template = 'Manager/managerbase.html'

    context = {
        'ot_rows': qs,
        'selected_month': month_str,
        'month_label': dt.strftime('%B %Y'),
        'role': role,
        'base_template': base_template,
        'status_filter': status_filter,
        'total_ot_hours': total_ot_hours,
        'total_ot_cost': total_ot_cost,
        'pending_count': pending_count,
        'approved_count': approved_count,
        'is_admin_or_hr': is_admin or is_hr,
        'current_user_emp_id': emp_profile.id if emp_profile else None,
    }
    return render(request, 'timesheets/ot_approval.html', context)


@login_required
def employee_monthly_timesheet_pdf(request, employee_id, month_str):
    emp = get_object_or_404(Employee.objects.select_related('user', 'department', 'branch', 'reporting_manager__user'), pk=employee_id)
    user = request.user
    emp_profile = getattr(user, 'employee_profile', None)

    # Permission check: superuser, HR, the employee's manager, or self
    is_admin = user.is_superuser
    is_hr = bool(emp_profile and emp_profile.is_hr)
    is_manager = bool(emp_profile and emp.reporting_manager_id == emp_profile.id)
    is_self = (emp.user_id == user.id)

    if not (is_admin or is_hr or is_manager or is_self):
        return HttpResponseForbidden("You do not have permission to view this timesheet.")

    try:
        dt = datetime.strptime(month_str, '%Y-%m').date()
    except ValueError:
        raise Http404("Invalid month format.")

    start_date = dt.replace(day=1)
    _, last_day = monthrange(dt.year, dt.month)
    end_date = dt.replace(day=last_day)

    logs = (
        EmployeeServiceLog.objects.filter(employee=emp, date__gte=start_date, date__lte=end_date)
        .select_related('time_log__work_order', 'project', 'approver__user', 'decided_by')
        .order_by('date', 'start_time')
    )

    total_hours = sum((l.total_hours or Decimal('0.00')) for l in logs)
    total_ot_hours = sum((l.ot_hours or Decimal('0.00')) for l in logs)

    from weasyprint import HTML
    html_str = render_to_string('timesheets/employee_monthly_timesheet_pdf.html', {
        'employee': emp,
        'month_label': dt.strftime('%B %Y'),
        'month_str': month_str,
        'logs': logs,
        'total_hours': total_hours,
        'total_ot_hours': total_ot_hours,
        'generated_at': timezone.now(),
    })

    base_url = request.build_absolute_uri('/')
    pdf_bytes = HTML(string=html_str, base_url=base_url).write_pdf()
    emp_code = emp.employee_code or f"AMMS-{emp.id:04d}"
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="timesheet_{emp_code}_{month_str}.pdf"'
    return response


import datetime
from decimal import Decimal
from calendar import monthrange
from django.shortcuts import get_object_or_404, redirect, render
from django.core.exceptions import ValidationError, PermissionDenied
from django.utils import timezone
from django.db import transaction
from django.contrib import messages
from django.urls import reverse

from Admin.models import (
    Employee, Attendance, AttendanceCorrection, DailyAttendanceStatus,
    Notification, EmployeeChangeLog, check_payroll_lock_for_employee
)
from Admin.attendance_register import build_attendance_register
from service_logs.ot import recalculate_day_ot


def can_decide_correction(user, correction):
    """
    Returns True if user has permission to approve or reject the correction request.
    Rules (D12):
    - Nobody approves their own request.
    - Reporting manager approves/rejects.
    - HR (is_hr) and Admin (is_superuser) can decide any request (override).
    - No reporting manager -> HR/Admin only.
    """
    if not user.is_authenticated:
        return False

    # Nobody approves their own request
    if correction.employee and correction.employee.user_id == user.id:
        return False

    if user.is_superuser:
        return True

    emp_profile = getattr(user, 'employee_profile', None)
    if not emp_profile:
        return False

    if emp_profile.is_hr:
        return True

    if correction.approver_id == emp_profile.id:
        return True

    return False


def submit_correction_request(
    employee,
    date,
    correction_type,
    requested_login,
    requested_logout,
    location='BDO',
    reason='',
    attachment=None,
    submitted_by=None,
    attendance=None
):
    """
    Creates and validates a new AttendanceCorrection request.
    Notifies approver or HR/Admin queue.
    """
    if isinstance(date, str):
        date = datetime.datetime.strptime(date, '%Y-%m-%d').date()

    if attendance is None and correction_type != 'NO_PUNCH':
        attendance = Attendance.objects.filter(
            employee=employee,
            login_time__date=date
        ).order_by('-login_time').first()

    orig_in = attendance.login_time if attendance else None
    orig_out = attendance.log_out_time if attendance else None

    # Auto-assign approver to reporting manager if exists
    approver = employee.reporting_manager

    correction = AttendanceCorrection(
        employee=employee,
        date=date,
        correction_type=correction_type,
        attendance=attendance,
        original_login=orig_in,
        original_logout=orig_out,
        requested_login=requested_login,
        requested_logout=requested_logout,
        location=location or (attendance.location if attendance else 'BDO'),
        reason=reason,
        attachment=attachment,
        status='PENDING',
        approver=approver,
        submitted_by=submitted_by,
    )
    correction.full_clean()
    correction.save()

    # Notifications
    emp_name = employee.user.get_full_name() or employee.user.username
    date_str = date.strftime('%d/%m/%Y')
    notif_msg = f"Attendance correction request submitted by {emp_name} for {date_str} ({correction.get_correction_type_display()})."

    from Admin.notifications import notify, notify_role, get_user_role_link
    if approver and approver.user:
        notify(
            approver.user,
            'ATTENDANCE_CORRECTION',
            notif_msg,
            link=get_user_role_link(approver.user, 'correction_team', query='team_status=PENDING'),
            related=correction,
            actor=submitted_by,
        )
    else:
        notify_role(
            ['HR', 'ADMIN'],
            'ATTENDANCE_CORRECTION',
            notif_msg,
            link=lambda u: get_user_role_link(u, 'correction_team', query='team_status=PENDING'),
            related=correction,
            actor=submitted_by,
        )

    return correction


def approve_correction(request_user, correction, remarks=""):
    """
    Approves an AttendanceCorrection request within transaction.atomic.
    Applies punch updates or creates Attendance, recalculates OT, logs to EmployeeChangeLog.
    """
    if not can_decide_correction(request_user, correction):
        raise PermissionDenied("You do not have permission to approve this request.")

    check_payroll_lock_for_employee(correction.employee, correction.date, is_open_ended=False)

    with transaction.atomic():
        correction = AttendanceCorrection.objects.select_for_update().get(pk=correction.pk)
        if correction.status != 'PENDING':
            raise ValidationError("This request has already been decided or cancelled.")

        if correction.correction_type == 'NO_PUNCH':
            exists = Attendance.objects.filter(
                employee=correction.employee,
                login_time__date=correction.date
            ).exclude(status='REJECTED').exists()
            if exists:
                formatted_date = correction.date.strftime('%d/%m/%Y')
                raise ValidationError(f"A punch already exists for {formatted_date} — use Forgot punch-out or Wrong time instead.")

            att = Attendance.objects.create(
                employee=correction.employee,
                login_time=correction.requested_login,
                log_out_time=correction.requested_logout,
                location=correction.location,
                attendance_status='PRESENT',
                status='APPROVED',
                auto_punched_out=False,
                missing_punch_resolved=True,
            )
            correction.attendance = att
        else:
            att = correction.attendance
            if not att:
                att = Attendance.objects.filter(
                    employee=correction.employee,
                    login_time__date=correction.date
                ).first()

            if att:
                att.login_time = correction.requested_login
                att.log_out_time = correction.requested_logout
                att.location = correction.location
                att.status = 'APPROVED'
                att.auto_punched_out = False
                att.missing_punch_resolved = True
                att.save()
                correction.attendance = att
            else:
                att = Attendance.objects.create(
                    employee=correction.employee,
                    login_time=correction.requested_login,
                    log_out_time=correction.requested_logout,
                    location=correction.location,
                    attendance_status='PRESENT',
                    status='APPROVED',
                    auto_punched_out=False,
                    missing_punch_resolved=True,
                )
                correction.attendance = att

        # Recalculate OT for this employee and day
        recalculate_day_ot(correction.employee.id, correction.date)

        # Log to EmployeeChangeLog
        EmployeeChangeLog.objects.create(
            employee=correction.employee,
            field='attendance_punch',
            old_value=f"Login: {correction.original_login}, Logout: {correction.original_logout}",
            new_value=f"Login: {correction.requested_login}, Logout: {correction.requested_logout}",
            changed_by=request_user,
            source='Attendance correction'
        )

        correction.status = 'APPROVED'
        correction.decided_by = request_user
        correction.decided_at = timezone.now()
        correction.remarks = remarks or ''
        correction.save()

        # Notify employee
        if correction.employee and correction.employee.user:
            from Admin.notifications import notify, get_user_role_link
            notify(
                correction.employee.user,
                'ATTENDANCE_CORRECTION',
                f"Your attendance correction request for {correction.date.strftime('%d/%m/%Y')} has been approved.",
                link=get_user_role_link(correction.employee.user, 'correction_my'),
                related=correction,
                actor=request_user,
            )

    return correction


def reject_correction(request_user, correction, remarks):
    """
    Rejects an AttendanceCorrection request. Remarks are strictly required.
    NO_PUNCH: creates DailyAttendanceStatus ABSENT override.
    Others: keeps recorded punches, sets missing_punch_resolved=True so day stops blocking payroll.
    """
    if not can_decide_correction(request_user, correction):
        raise PermissionDenied("You do not have permission to reject this request.")

    if not remarks or not remarks.strip():
        raise ValidationError("Remarks are required for rejecting a correction request.")

    check_payroll_lock_for_employee(correction.employee, correction.date, is_open_ended=False)

    with transaction.atomic():
        correction = AttendanceCorrection.objects.select_for_update().get(pk=correction.pk)
        if correction.status != 'PENDING':
            raise ValidationError("This request has already been decided or cancelled.")

        remarks_clean = remarks.strip()

        if correction.correction_type == 'NO_PUNCH':
            DailyAttendanceStatus.objects.update_or_create(
                employee=correction.employee,
                date=correction.date,
                defaults={
                    'status': 'ABSENT',
                    'note': f"Correction rejected: {remarks_clean}"[:255],
                    'updated_by': request_user,
                }
            )
        else:
            # Mark attendance missing_punch_resolved=True
            Attendance.objects.filter(
                employee=correction.employee,
                login_time__date=correction.date
            ).update(missing_punch_resolved=True)

        correction.status = 'REJECTED'
        correction.decided_by = request_user
        correction.decided_at = timezone.now()
        correction.remarks = remarks_clean
        correction.save()

        # Notify employee
        if correction.employee and correction.employee.user:
            from Admin.notifications import notify, get_user_role_link
            notify(
                correction.employee.user,
                'ATTENDANCE_CORRECTION',
                f"Your attendance correction request for {correction.date.strftime('%d/%m/%Y')} was rejected: {remarks_clean}",
                link=get_user_role_link(correction.employee.user, 'correction_my'),
                related=correction,
                actor=request_user,
            )

    return correction


def cancel_correction(request_user, correction):
    """
    Cancels a pending correction request. Only the submitting employee can cancel.
    """
    if not (correction.employee and correction.employee.user_id == request_user.id):
        raise PermissionDenied("You can only cancel your own correction requests.")

    if correction.status != 'PENDING':
        raise ValidationError("Only pending requests can be cancelled.")

    correction.status = 'CANCELLED'
    correction.save(update_fields=['status', 'updated_at'])
    return correction


def get_shared_attendance_status_context(request, employee, role_name):
    """
    Builds context for role attendance-status pages (employee, manager, hr, finance, admin).
    """
    today = timezone.localdate()
    month_param = request.GET.get('month')
    if month_param:
        try:
            year, month = map(int, month_param.split('-'))
            start_date = datetime.date(year, month, 1)
            _, num_days = monthrange(year, month)
            end_date = datetime.date(year, month, num_days)
        except Exception:
            start_date = datetime.date(today.year, today.month, 1)
            _, num_days = monthrange(today.year, today.month)
            end_date = datetime.date(today.year, today.month, num_days)
            month_param = today.strftime('%Y-%m')
    else:
        start_date = datetime.date(today.year, today.month, 1)
        _, num_days = monthrange(today.year, today.month)
        end_date = datetime.date(today.year, today.month, num_days)
        month_param = today.strftime('%Y-%m')

    active_tab = request.GET.get('tab', 'my_attendance' if employee else 'team_requests')

    if employee:
        # Pending correction request dates for employee
        pending_corrections = AttendanceCorrection.objects.filter(
            employee=employee,
            status='PENDING'
        )
        pending_dates = {c.date: c for c in pending_corrections}

        # 1. My Attendance data
        all_rows = build_attendance_register(start_date, end_date, employee_id=employee.id)
        my_rows = [r for r in all_rows if r['employee'].id == employee.id]
        rows_by_date = {r['date']: r for r in my_rows}

        calendar_days = []
        cur_d = start_date
        while cur_d <= end_date:
            r = rows_by_date.get(cur_d)
            is_future = (cur_d > today)
            has_pending = (cur_d in pending_dates)
            pending_obj = pending_dates.get(cur_d)

            # Correction eligible if past/today and not pending and missing punch / absent / incomplete
            can_request = False
            if not is_future and not has_pending and r:
                code = r.get('status_code')
                has_inc = r.get('has_incomplete_punch')
                if code in ['MISSING_PUNCH', 'ABSENT'] or has_inc:
                    can_request = True
                elif code in ['OFFICE', 'VESSEL', 'OFFICE_VESSEL'] and r.get('auto_punched_out'):
                    can_request = True

            day_info = {
                'date': cur_d,
                'day_num': cur_d.day,
                'weekday': cur_d.strftime('%a'),
                'row': r,
                'is_future': is_future,
                'has_pending': has_pending,
                'pending_correction': pending_obj,
                'can_request_correction': can_request,
                'status_code': r['status_code'] if r else '',
                'status_label': r['status_label'] if r else '',
                'total_hours': r['total_hours'] if r else Decimal('0.00'),
                'location': r['location_label'] if r else '',
                'is_off_day': r.get('is_off_day', False) if r else False,
            }
            calendar_days.append(day_info)
            cur_d += datetime.timedelta(days=1)

        # 2. My Requests
        my_requests = AttendanceCorrection.objects.filter(
            employee=employee
        ).select_related('approver__user', 'decided_by', 'attendance').order_by('-created_at')
    else:
        my_rows = []
        calendar_days = []
        my_requests = []

    # 3. Team Requests (if manager or HR or superuser)
    is_hr = getattr(employee, 'is_hr', False) if employee else False
    is_admin = request.user.is_superuser
    has_reports = employee.direct_reports.filter(user__is_active=True).exists() if employee else False
    can_manage_team = (is_admin or is_hr or has_reports)

    team_pending = []
    team_decided = []
    team_requests_count = 0

    if can_manage_team:
        if is_admin or is_hr:
            base_team_qs = AttendanceCorrection.objects.all()
        else:
            base_team_qs = AttendanceCorrection.objects.filter(approver=employee)

        # Filter handling
        filter_status = request.GET.get('team_status', 'ALL')
        filter_emp = request.GET.get('team_employee')
        filter_dept = request.GET.get('team_dept')
        filter_branch = request.GET.get('team_branch')
        filter_start = request.GET.get('team_start')
        filter_end = request.GET.get('team_end')

        t_qs = base_team_qs.select_related(
            'employee', 'employee__user', 'employee__department', 'employee__branch',
            'approver__user', 'decided_by', 'attendance'
        )

        if filter_status and filter_status != 'ALL':
            t_qs = t_qs.filter(status=filter_status)
        if filter_emp:
            t_qs = t_qs.filter(employee_id=filter_emp)
        if filter_dept:
            t_qs = t_qs.filter(employee__department_id=filter_dept)
        if filter_branch:
            t_qs = t_qs.filter(employee__branch_id=filter_branch)
        if filter_start:
            try:
                s_d = datetime.datetime.strptime(filter_start, '%Y-%m-%d').date()
                t_qs = t_qs.filter(date__gte=s_d)
            except Exception:
                pass
        if filter_end:
            try:
                e_d = datetime.datetime.strptime(filter_end, '%Y-%m-%d').date()
                t_qs = t_qs.filter(date__lte=e_d)
            except Exception:
                pass

        all_team_reqs = list(t_qs.order_by('-created_at'))
        team_requests_count = len(all_team_reqs)
        team_pending = [c for c in all_team_reqs if c.status == 'PENDING']
        team_decided = [c for c in all_team_reqs if c.status != 'PENDING']

    from Admin.models import Department, Branch
    departments = Department.objects.filter(is_active=True).order_by('name')
    branches = Branch.objects.filter(is_active=True).order_by('name')

    return {
        'employee': employee,
        'role': role_name,
        'selected_month': month_param,
        'start_date': start_date,
        'end_date': end_date,
        'active_tab': active_tab,
        'calendar_days': calendar_days,
        'my_rows': my_rows,
        'my_requests': my_requests,
        'can_manage_team': can_manage_team,
        'team_pending': team_pending,
        'team_decided': team_decided,
        'team_requests_count': team_requests_count,
        'departments': departments,
        'branches': branches,
        'correction_types': AttendanceCorrection.CORRECTION_TYPE_CHOICES,
        'location_choices': Attendance.LOCATION_CHOICES,
    }


def handle_attendance_status_post(request, employee):
    """
    Handles POST actions for attendance status tabs:
    - request_correction
    - cancel_correction
    - approve_correction
    - reject_correction
    - bulk_approve
    """
    action = request.POST.get('action')
    tz = timezone.get_current_timezone()

    if action == 'request_correction':
        try:
            req_date_str = request.POST.get('date')
            correction_type = request.POST.get('correction_type')
            in_time_str = request.POST.get('requested_login')
            out_time_str = request.POST.get('requested_logout')
            location = request.POST.get('location', 'BDO')
            reason = request.POST.get('reason', '').strip()
            attachment = request.FILES.get('attachment')

            if not req_date_str or not in_time_str or not out_time_str or not reason:
                messages.error(request, "Date, requested times, and reason are required.")
                return redirect(request.get_full_path())

            req_date = datetime.datetime.strptime(req_date_str, '%Y-%m-%d').date()

            # Time parsing: handles either 'YYYY-MM-DDTHH:MM' or 'HH:MM'
            if 'T' in in_time_str:
                naive_in = datetime.datetime.strptime(in_time_str, '%Y-%m-%dT%H:%M')
            else:
                naive_in = datetime.datetime.combine(req_date, datetime.datetime.strptime(in_time_str, '%H:%M').time())

            if 'T' in out_time_str:
                naive_out = datetime.datetime.strptime(out_time_str, '%Y-%m-%dT%H:%M')
            else:
                out_t = datetime.datetime.strptime(out_time_str, '%H:%M').time()
                # If logout time < login time, it spans past midnight
                out_d = req_date + datetime.timedelta(days=1) if out_t <= naive_in.time() else req_date
                naive_out = datetime.datetime.combine(out_d, out_t)

            login_aware = timezone.make_aware(naive_in, tz)
            logout_aware = timezone.make_aware(naive_out, tz)

            # Target employee (HR/Admin can create on behalf of other employees)
            target_emp = employee
            emp_id = request.POST.get('employee_id')
            if emp_id and (request.user.is_superuser or getattr(employee, 'is_hr', False)):
                target_emp = get_object_or_404(Employee, id=emp_id)

            if not target_emp:
                messages.error(request, "Target employee is required.")
                return redirect(request.get_full_path())

            correction = submit_correction_request(
                employee=target_emp,
                date=req_date,
                correction_type=correction_type,
                requested_login=login_aware,
                requested_logout=logout_aware,
                location=location,
                reason=reason,
                attachment=attachment,
                submitted_by=request.user
            )

            # Approve immediately if requested by HR/Admin
            if request.POST.get('approve_immediately') and can_decide_correction(request.user, correction):
                approve_correction(request.user, correction, remarks="Approved upon creation by HR/Admin.")
                messages.success(request, f"Correction for {target_emp.user.username} on {req_date_str} created and approved.")
            else:
                messages.success(request, f"Correction request for {req_date_str} submitted successfully.")

        except ValidationError as e:
            messages.error(request, f"Submission error: {e}")
        except Exception as e:
            messages.error(request, f"An unexpected error occurred: {e}")

        redirect_url = request.get_full_path()
        if 'tab=' not in redirect_url and not request.POST.get('approve_immediately'):
            redirect_url += ('&' if '?' in redirect_url else '?') + 'tab=my_requests'
        return redirect(redirect_url)

    elif action == 'cancel_correction':
        try:
            cid = request.POST.get('correction_id')
            correction = get_object_or_404(AttendanceCorrection, id=cid)
            cancel_correction(request.user, correction)
            messages.success(request, f"Request #{cid} cancelled.")
        except PermissionDenied:
            messages.error(request, "Permission denied. You can only cancel your own pending requests.")
        except Exception as e:
            messages.error(request, f"Error: {e}")
        return redirect(request.get_full_path())

    elif action == 'approve_correction':
        try:
            cid = request.POST.get('correction_id')
            remarks = request.POST.get('remarks', '')
            correction = get_object_or_404(AttendanceCorrection, id=cid)
            approve_correction(request.user, correction, remarks=remarks)
            emp_name = correction.employee.user.get_full_name() or correction.employee.user.username
            messages.success(request, f"Approved correction request for {emp_name} ({correction.date}).")
        except PermissionDenied:
            messages.error(request, "Permission denied. You cannot approve this request.")
        except ValidationError as e:
            messages.error(request, f"Cannot approve: {e}")
        except Exception as e:
            messages.error(request, f"Error approving request: {e}")
        return redirect(request.get_full_path())

    elif action == 'reject_correction':
        try:
            cid = request.POST.get('correction_id')
            remarks = request.POST.get('remarks', '')
            correction = get_object_or_404(AttendanceCorrection, id=cid)
            reject_correction(request.user, correction, remarks=remarks)
            emp_name = correction.employee.user.get_full_name() or correction.employee.user.username
            messages.success(request, f"Rejected correction request for {emp_name} ({correction.date}).")
        except PermissionDenied:
            messages.error(request, "Permission denied. You cannot reject this request.")
        except ValidationError as e:
            messages.error(request, f"Cannot reject: {e}")
        except Exception as e:
            messages.error(request, f"Error rejecting request: {e}")
        return redirect(request.get_full_path())

    elif action == 'bulk_approve':
        cids = request.POST.getlist('selected_corrections')
        approved_count = 0
        error_count = 0
        for cid in cids:
            try:
                corr = AttendanceCorrection.objects.get(id=cid)
                if can_decide_correction(request.user, corr) and corr.status == 'PENDING':
                    approve_correction(request.user, corr, remarks="Bulk approved")
                    approved_count += 1
            except Exception:
                error_count += 1
        if approved_count > 0:
            messages.success(request, f"Bulk approved {approved_count} correction requests.")
        if error_count > 0:
            messages.warning(request, f"{error_count} requests could not be approved.")
        return redirect(request.get_full_path())

    return redirect(request.get_full_path())


def render_role_attendance_status(request, role_name, base_template, template_name=None, extra_context=None):
    """
    Renders attendance status page for any of the 5 roles.
    """
    if not request.user.is_authenticated:
        return redirect('custom-login')

    try:
        employee = request.user.employee_profile
    except (AttributeError, Employee.DoesNotExist):
        if request.user.is_superuser:
            employee = None
        else:
            messages.error(request, "Your account does not have an employee profile.")
            return redirect('custom-login')

    if request.method == 'POST':
        return handle_attendance_status_post(request, employee)

    context = get_shared_attendance_status_context(request, employee, role_name)
    context['base_template'] = base_template
    context['attendance_records'] = context.get('my_rows', [])
    context['attendances'] = context.get('my_rows', [])
    if extra_context:
        context.update(extra_context)

    target_template = template_name or 'attendance/role_attendance_status.html'
    return render(request, target_template, context)

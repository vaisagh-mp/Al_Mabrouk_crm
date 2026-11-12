from Admin.leave_service import is_leave_v2_active
from django.shortcuts import redirect, render
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
import calendar
from calendar import monthrange
from django.utils.timezone import localtime
from django.utils.dateparse import parse_datetime
import pytz
from datetime import date
from datetime import datetime
from django.db.models import Q, F, Sum
from django.utils.timezone import now
from django.db.models import Prefetch
from django.utils import timezone
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.auth.forms import UserCreationForm
from Admin.forms import EmployeeCreationForm, ProjectForm, ProjectAssignmentForm, ManagerEmployeeUpdateForm
from employee_data.forms import LeaveForm, EmployeeUpdateForm
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from Admin.models import Attendance, Employee, LeaveBalance, Project, TeamMemberStatus, Leave, ActivityLog, Notification, Team
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.views.decorators.http import require_POST
from service_logs.views import build_employee_analytics_context, export_employee_analytics_excel
from manager.views import compute_presence_data
from Admin.attendance_register import _register_context, handle_set_day_status, summary_csv_response, summary_excel_response
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
from Admin.staff_lifecycle import deactivate_employee, reactivate_employee, delete_blockers
from Admin.permissions import can_view_salary, can_manage_hr_settings



def is_hr(user):
    """Return True if the logged-in user is HR (or a superuser). Raise 403 otherwise."""
    if user.is_superuser:
        return True
    employee = getattr(user, "employee_profile", None)
    if employee is not None and getattr(employee, "is_hr", False):
        return True
    raise PermissionDenied


@login_required
@user_passes_test(is_hr)
def hr_log_in(request):
    if request.method == "POST":
        try:
            employee = get_object_or_404(Employee, user=request.user)
        except AttributeError:
            messages.error(request, "Your user is not associated with an employee record.")
            return redirect("hr_attendance_dashboard")

        handle_staff_punch_in(request, employee)
        return redirect("hr_attendance_status")

    return hr_render_attendance_page(request)

@login_required
@user_passes_test(is_hr)
def hr_log_off(request, attendance_id):
    if request.method == "POST":
        try:
            employee = get_object_or_404(Employee, user=request.user)
        except AttributeError:
            messages.error(request, "Your user is not associated with an employee record.")
            return redirect("hr_attendance_dashboard")
        
        attendance = get_object_or_404(Attendance, id=attendance_id, employee=employee)
        handle_staff_punch_out(request, attendance)

    return redirect("hr-dashboard")  # Redirect to the appropriate page

@login_required
@user_passes_test(is_hr)
def hr_render_attendance_page(request):
    employee = get_object_or_404(Employee, user=request.user)
    context = get_staff_attendance_context(request, employee)
    return render(request, "hr/punchin.html", context)



@login_required
@user_passes_test(is_hr)
def attendance(request):
    from Admin.attendance_correction import render_role_attendance_status
    return render_role_attendance_status(request, "HR", 'hr/hrbase.html', template_name='hr/attendance.html')

@login_required
@user_passes_test(is_hr)
def hr_update_travel_time(request, attendance_id):
    return handle_staff_update_travel_time(request, attendance_id, 'hr_attendance_status', 'hr/update_travel_time.html')

@login_required
@user_passes_test(is_hr)
def dashboard(request):
    user = request.user
    employee = getattr(user, 'employee_profile', None)

    if user.is_superuser or (employee and employee.is_hr):
        from Admin.hr_dashboard import render_hr_dashboard
        return render_hr_dashboard(request, role='hr', base_template='hr/hrbase.html')

    return render(request, 'employee/employee_dashboard.html')

# create employee
@login_required
@user_passes_test(is_hr)
def hr_create_employee(request):
    if request.method == 'POST':
        form = EmployeeCreationForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, 'Employee created successfully!')
                if 'save_and_add_another' in request.POST or 'save_add_another' in request.POST:
                    return redirect('hr_create_employee')
                return redirect('hr_employee_list')
            except Exception as e:
                messages.error(request, f'There was an error creating the employee: {e}')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = EmployeeCreationForm(user=request.user)

    return render(request, 'hr/create_employee.html', {
        'form': form,
        'can_view_salary': can_view_salary(request.user)
    })



@login_required
@user_passes_test(is_hr)
def hr_employee_list(request):
    from Admin.employee_views import render_employee_list
    return render_employee_list(request, role='hr', base_template='hr/hrbase.html')

@login_required
@user_passes_test(is_hr)
def hr_attendance_list_view(request):
    if request.method == 'POST':
        from Admin.attendance_correction import handle_attendance_status_post
        emp = getattr(request.user, 'employee_profile', None)
        return handle_attendance_status_post(request, emp)
    export_format = request.GET.get('export')
    if export_format in ('summary_excel', 'summary_csv', 'excel'):
        context = _register_context(request)
        return summary_excel_response(
            context['summary_rows_all'],
            context['summary_totals'],
            context['start'],
            context['end']
        )
    context = _register_context(request)
    context['role'] = 'hr'
    return render(request, 'hr/attendance_list.html', context)


@login_required
@user_passes_test(is_hr)
def hr_employee_attendance_month_view(request, employee_id, month):
    from Admin.attendance_register import employee_attendance_month_view_helper
    return employee_attendance_month_view_helper(request, employee_id, month, role='hr')


@login_required
@user_passes_test(is_hr)
def hr_set_day_status(request, employee_id, date):
    return handle_set_day_status(request, employee_id, date, role='hr')

@login_required
@user_passes_test(is_hr)
def hr_employee_leave_list(request):
    if is_leave_v2_active():
        from Admin.leave_views import render_role_leave_list
        return render_role_leave_list(request, 'HR', 'hr/hrbase.html')

    search_query = request.GET.get('search', '').strip()

    # Fetch only employee leave records
    leave_records = Leave.objects.select_related('user').order_by('-from_date')

    # Apply search filter (search by username)
    if search_query:
        leave_records = leave_records.filter(user__username__icontains=search_query)

    # Paginate leave records (10 per page)
    paginator = Paginator(leave_records, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'leave_records': page_obj,
        'search_query': search_query,
    }

    return render(request, 'hr/employee_leave_list.html', context)

@login_required
@user_passes_test(is_hr)
def hr_employee_manage_leave(request):
    if is_leave_v2_active():
        from Admin.leave_views import render_role_manage_leave
        return render_role_manage_leave(request, 'HR', 'hr/hrbase.html')

    search_query = request.GET.get('search', '').strip()

    # Fetch only employee leave requests
    pending_leaves = Leave.objects.filter(approval_status='PENDING').select_related('user')

    # Apply search/dept/branch filters
    if search_query:
        pending_leaves = pending_leaves.filter(user__username__icontains=search_query)
    dept_filter = request.GET.get('department')
    branch_filter = request.GET.get('branch')
    if dept_filter:
        pending_leaves = pending_leaves.filter(user__employee_profile__department_id=dept_filter)
    if branch_filter:
        pending_leaves = pending_leaves.filter(user__employee_profile__branch_id=branch_filter)

    # Paginate leave records (10 per page)
    paginator = Paginator(pending_leaves, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    if request.method == 'POST':
        leave_id = request.POST.get('leave_id')
        action = request.POST.get('action')  # APPROVE or REJECT
        rejection_reason = request.POST.get('rejection_reason', '')

        if not action:
            messages.error(request, "Action (Approve/Reject) must be selected!")
            return redirect('hr_employee_manage_leave')

        try:
            leave_request = Leave.objects.get(id=leave_id)

            if action == 'APPROVE':
                leave_request.approval_status = 'APPROVED'
                messages.success(request, "Leave request approved successfully!")
            elif action == 'REJECT':
                leave_request.approval_status = 'REJECTED'
                leave_request.reason += f"\nRejection Reason: {rejection_reason}"
                messages.success(request, "Leave request rejected successfully!")

            leave_request.save()

        except Leave.DoesNotExist:
            messages.error(request, "Leave request not found!")

    return render(request, 'hr/employee_manage_leave.html', {'leaves': page_obj})

@login_required
@user_passes_test(is_hr)
def hr_attendance_detail(request, pk):
    attendance = get_object_or_404(Attendance, pk=pk)
    return render(request, 'hr/attendance_detail.html', {'attendance': attendance})

# Edit attendance
@login_required
@user_passes_test(is_hr)
def hr_edit_attendance(request, attendance_id):
    attendance = get_object_or_404(Attendance, id=attendance_id)

    if request.method == 'POST':
        try:
            attendance.employee_id = request.POST.get('employee') or None
            attendance.project_id = request.POST.get('project') or None

            def parse_datetime_field(field_name):
                value = request.POST.get(field_name)
                if value and value.strip():
                    value = value.strip()
                    dt = parse_datetime(value)
                    if dt:
                        return timezone.make_aware(dt, timezone.get_current_timezone()) if timezone.is_naive(dt) else dt
                    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
                        try:
                            parsed = datetime.strptime(value, fmt)
                            return timezone.make_aware(parsed, timezone.get_current_timezone())
                        except ValueError:
                            pass
                return None

            attendance.login_time = parse_datetime_field('login_time')
            attendance.log_out_time = parse_datetime_field('log_out_time')
            attendance.travel_in_time = parse_datetime_field('travel_in_time')
            attendance.travel_out_time = parse_datetime_field('travel_out_time')

            attendance.location = request.POST.get('location') or None
            attendance.attendance_status = request.POST.get('attendance_status') or None
            attendance.status = request.POST.get('status') or 'PENDING'
            attendance.rejection_reason = request.POST.get('rejection_reason') or ''

            attendance.save()

            messages.success(request, 'Attendance record has been updated successfully.')
            return redirect('hr_attendance_list_view')
        except Exception as e:
            messages.error(request, f'Error updating attendance: {e}')

    employees = Employee.objects.all()
    projects = Project.objects.all()
    return render(request, 'hr/edit_attendance.html', {
        'attendance': attendance,
        'employees': employees,
        'projects': projects,
    })

# Delete attendance
@login_required
@user_passes_test(is_hr)
@require_POST
def hr_delete_attendance(request, attendance_id):
    attendance = get_object_or_404(Attendance, id=attendance_id)
    attendance.delete()
    # Add a success message
    messages.success(request, 'Attendance record has been deleted successfully.')
    return redirect('hr_attendance_list_view')


@login_required
@user_passes_test(is_hr)
def hr_employee_profile(request, employee_id):
    from Admin.employee_views import render_employee_profile
    return render_employee_profile(request, employee_id, role='hr', base_template='hr/hrbase.html')


@login_required
@user_passes_test(is_hr)
def hr_edit_employee(request, employee_id):
    """HR can edit an employee's profile"""
    try:
        employee = Employee.objects.select_related("user").get(id=employee_id)
    except Employee.DoesNotExist:
        messages.error(request, "Employee not found.")
        return redirect("hr_employee_list")

    if not employee.user:
        messages.error(request, "This employee does not have a linked user account.")
        return redirect("hr_employee_list")

    if request.method == "POST":
        form = ManagerEmployeeUpdateForm(request.POST, request.FILES, instance=employee, user=request.user)
        if form.is_valid():
            form.save(changed_by=request.user, source='HR edit')
            messages.success(request, "Employee profile updated successfully.")
            return redirect("hr_employee_list")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = ManagerEmployeeUpdateForm(instance=employee, user=request.user)

    return render(request, "hr/edit_employee.html", {
        "form": form,
        "employee": employee,
        "can_view_salary": can_view_salary(request.user)
    })



@login_required
@user_passes_test(is_hr)
@require_POST
def hr_delete_employee(request, employee_id):
    employee = get_object_or_404(Employee, id=employee_id)
    blockers = delete_blockers(employee)
    if blockers:
        messages.error(request, f"Cannot delete {employee.name}: has {', '.join(blockers)}. Deactivate instead.")
        return redirect('hr_employee_profile', employee_id=employee.id)

    employee.delete()
    messages.success(request, 'Employee deleted successfully!')
    return redirect('hr_employee_list')


@login_required
@user_passes_test(is_hr)
@require_POST
def hr_deactivate_employee(request, employee_id):
    employee = get_object_or_404(Employee, id=employee_id)
    if employee.user == request.user:
        messages.error(request, "You cannot deactivate your own account.")
        return redirect('hr_employee_profile', employee_id=employee.id)

    if employee.user and employee.user.is_superuser:
        messages.error(request, "HR cannot deactivate a superuser account.")
        return redirect('hr_employee_profile', employee_id=employee.id)

    summary = deactivate_employee(employee, request.user)
    messages.success(request, f"{employee.name} has been deactivated.")
    reassign = summary.get('managed_projects_to_reassign', []) + summary.get('managed_teams_to_reassign', [])
    if reassign:
        messages.warning(request, f"Reassign these projects/teams to another manager: {', '.join(reassign)}.")
    return redirect('hr_employee_profile', employee_id=employee.id)


@login_required
@user_passes_test(is_hr)
@require_POST
def hr_reactivate_employee(request, employee_id):
    employee = get_object_or_404(Employee, id=employee_id)
    reactivate_employee(employee, request.user)
    messages.success(request, f"{employee.name} has been reactivated. Add them back to teams if needed.")
    return redirect('hr_employee_profile', employee_id=employee.id)

@login_required
@user_passes_test(is_hr)
def hr_apply_leave(request):
    if is_leave_v2_active():
        from Admin.leave_views import render_role_apply_leave
        return render_role_apply_leave(request, 'HR', 'hr/hrbase.html', 'hr_leave_status')
    if request.method == 'POST':
        form = LeaveForm(request.POST, request.FILES)
        if form.is_valid():
            handle_staff_apply_leave(request, form)
            return redirect('hr_leave_status')
    else:
        form = LeaveForm()

    return render(request, 'hr/leave.html', {'form': form})

@login_required
@user_passes_test(is_hr)
def hr_upload_medical_certificate(request, leave_id):
    leave = Leave.objects.get(id=leave_id)
    if request.method == 'POST':
        medical_certificate = request.FILES.get('medical_certificate')

        if medical_certificate:
            leave.medical_certificate = medical_certificate
            leave.save()

            # Check if certificate is uploaded within 2 days from leave start date
            if leave.from_date:
                days_since_start = (timezone.now().date() - leave.from_date).days

                if days_since_start > 0:
                    # If uploaded after 2 days, change leave type to Annual Leave
                    if leave.leave_type == 'SICK LEAVE':
                        leave.leave_type = 'ANNUAL LEAVE'
                        leave.save()

                    messages.success(request, "Medical certificate uploaded late. Leave converted to Annual Leave.")
                else:
                    messages.success(request, "Medical certificate uploaded successfully.")

            return redirect('hr_leave_status')

    return redirect('hr_leave_status')  # Redirect if GET request or no file uploaded

@login_required
@user_passes_test(is_hr)
def hr_leave_status(request):
    """View to display the leave status of the logged-in user."""
    if is_leave_v2_active():
        from Admin.leave_views import render_role_leave_status
        return render_role_leave_status(request, 'HR', 'hr/hrbase.html')
    leaves_list = Leave.objects.filter(user=request.user).order_by("-from_date")
    
    # Paginate leave requests (10 per page)
    paginator = Paginator(leaves_list, 10)  
    page_number = request.GET.get("page")
    leaves = paginator.get_page(page_number)

    return render(request, "hr/leavestatus.html", {"leaves": leaves})

@login_required
@user_passes_test(is_hr)
def hr_leave_records(request):
    """View to display the leave records for the logged-in user."""
    if is_leave_v2_active():
        from Admin.leave_views import render_role_leave_statement
        return render_role_leave_statement(request, 'HR', 'hr/hrbase.html')

    # Retrieve leave balance from the database
    leave_balance = LeaveBalance.objects.filter(user=request.user).first()

    if not leave_balance:
        leave_balance = LeaveBalance.objects.create(user=request.user)  # Create default balance

    # Convert balance into a dictionary for easy access
    leave_balances = {
        "ANNUAL LEAVE": leave_balance.annual_leave,
        "SICK LEAVE": leave_balance.sick_leave,
        # "CASUAL LEAVE": leave_balance.casual_leave,
    }

    # Initialize summary dictionary
    leave_summary = {leave: {"pending": 0, "scheduled": 0, "taken": 0, "balance": leave_balances.get(leave, 0)} for leave in leave_balances}

    # Retrieve all leave requests for the user
    user_leaves = Leave.objects.filter(user=request.user)

    for leave in user_leaves:
        if leave.leave_type in leave_summary:
            if leave.approval_status == "PENDING":
                leave_summary[leave.leave_type]["pending"] += leave.no_of_days
            elif leave.approval_status == "APPROVED":
                leave_summary[leave.leave_type]["taken"] += leave.no_of_days
                leave_summary[leave.leave_type]["balance"] -= leave.no_of_days  # Deduct from balance

    return render(request, "hr/leaverecords.html", {"leave_summary": leave_summary})


@login_required
@user_passes_test(is_hr)
def hr_fetch_notifications(request):
    return JsonResponse({"notifications": fetch_user_notifications(request.user)})


@login_required
@user_passes_test(is_hr)
def hr_mark_notifications_as_read(request):
    """Mark all unread notifications for the logged-in manager as read."""
    return JsonResponse(mark_user_notifications_read(request.user))


@login_required
@user_passes_test(is_hr)
def hr_profile(request):
    context = get_staff_profile_context(request.user)
    return render(request, 'hr/profile.html', context)


@login_required
@user_passes_test(is_hr)
def hr_update_profile(request):
    return handle_staff_update_profile(request, 'hr_profile_view', 'hr/updateprofile.html')


@login_required
@user_passes_test(is_hr)
def hr_employee_analytics(request):
    context = build_employee_analytics_context(request)
    if (request.GET.get("export") or "").strip().lower() == "excel":
        return export_employee_analytics_excel(request, context)
    context["base_template"] = "hr/hrbase.html"
    context["hide_project_links"] = True
    context["register_url"] = reverse('hr_attendance_list_view')
    return render(request, "analytics/employee_based.html", context)


from functools import wraps
from django.http import HttpResponseForbidden
from Admin.permissions import can_manage_hr_settings
from Admin.models import Department, Branch
from hr.forms import DepartmentForm, BranchForm, DutyScheduleForm, HolidayForm
from Admin.models import (
    DutySchedule, EmployeeSchedule, RestDayRule, RosterDay, Holiday,
    Department, Branch, WEEKDAY_CHOICES,
    update_employee_schedule, update_employee_rest_day_rule,
    check_payroll_lock_for_employee, check_payroll_lock_for_holiday,
    EmployeeChangeLog
)
from finance.models import Payroll
from datetime import timedelta
from functools import wraps
from django.http import HttpResponseForbidden


def require_hr_settings(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('custom-login')
        if not can_manage_hr_settings(request.user):
            return HttpResponseForbidden("You do not have permission to manage HR settings.")
        return view_func(request, *args, **kwargs)
    return _wrapped


@require_hr_settings
def hr_departments_list(request):
    departments = Department.objects.all().order_by('sort_order', 'name')
    return render(request, 'hr/settings/departments.html', {'departments': departments})


@require_hr_settings
def hr_department_create(request):
    if request.method == 'POST':
        form = DepartmentForm(request.POST)
        if form.is_valid():
            dept = form.save()
            messages.success(request, f"Department '{dept.name}' created successfully.")
            return redirect('hr_departments')
    else:
        form = DepartmentForm()
    return render(request, 'hr/settings/department_form.html', {'form': form})


@require_hr_settings
def hr_department_edit(request, pk):
    dept = get_object_or_404(Department, pk=pk)
    if request.method == 'POST':
        form = DepartmentForm(request.POST, instance=dept)
        if form.is_valid():
            form.save()
            messages.success(request, f"Department '{dept.name}' updated successfully.")
            return redirect('hr_departments')
    else:
        form = DepartmentForm(instance=dept)
    return render(request, 'hr/settings/department_form.html', {'form': form})


@require_hr_settings
def hr_department_toggle_active(request, pk):
    dept = get_object_or_404(Department, pk=pk)
    if request.method == 'POST':
        dept.is_active = not dept.is_active
        dept.save(update_fields=['is_active'])
        status_str = "activated" if dept.is_active else "deactivated"
        messages.success(request, f"Department '{dept.name}' has been {status_str}.")
    return redirect('hr_departments')


@require_hr_settings
def hr_department_delete(request, pk):
    dept = get_object_or_404(Department, pk=pk)
    if dept.employees.exists():
        messages.error(request, f"Cannot delete department '{dept.name}' because it has assigned employees. Deactivate it instead.")
    else:
        dept.delete()
        messages.success(request, f"Department '{dept.name}' deleted successfully.")
    return redirect('hr_departments')


@require_hr_settings
def hr_branches_list(request):
    branches = Branch.objects.all().order_by('sort_order', 'name')
    return render(request, 'hr/settings/branches.html', {'branches': branches})


@require_hr_settings
def hr_branch_create(request):
    if request.method == 'POST':
        form = BranchForm(request.POST)
        if form.is_valid():
            branch = form.save()
            messages.success(request, f"Branch '{branch.name}' created successfully.")
            return redirect('hr_branches')
    else:
        form = BranchForm()
    return render(request, 'hr/settings/branch_form.html', {'form': form})


@require_hr_settings
def hr_branch_edit(request, pk):
    branch = get_object_or_404(Branch, pk=pk)
    if request.method == 'POST':
        form = BranchForm(request.POST, instance=branch)
        if form.is_valid():
            form.save()
            messages.success(request, f"Branch '{branch.name}' updated successfully.")
            return redirect('hr_branches')
    else:
        form = BranchForm(instance=branch)
    return render(request, 'hr/settings/branch_form.html', {'form': form})


@require_hr_settings
def hr_branch_toggle_active(request, pk):
    branch = get_object_or_404(Branch, pk=pk)
    if request.method == 'POST':
        branch.is_active = not branch.is_active
        branch.save(update_fields=['is_active'])
        status_str = "activated" if branch.is_active else "deactivated"
        messages.success(request, f"Branch '{branch.name}' has been {status_str}.")
    return redirect('hr_branches')


@require_hr_settings
def hr_branch_delete(request, pk):
    branch = get_object_or_404(Branch, pk=pk)
    if branch.employees.exists():
        messages.error(request, f"Cannot delete branch '{branch.name}' because it has assigned employees. Deactivate it instead.")
    else:
        branch.delete()
        messages.success(request, f"Branch '{branch.name}' deleted successfully.")
    return redirect('hr_branches')


@login_required
@user_passes_test(is_hr)
def hr_get_presence_data(request):
    try:
        month = int(request.GET.get("month", date.today().month))
        year = int(request.GET.get("year", date.today().year))
        if month < 1 or month > 12:
            return JsonResponse({"error": "Invalid month"}, status=400)
        employee = get_object_or_404(Employee, user=request.user)
        return JsonResponse(compute_presence_data(employee, year, month))
    except ValueError:
        return JsonResponse({"error": "Invalid month or year"}, status=400)


# ==============================================================================
# Shifts & Holidays
# ==============================================================================

# 1. Duty Schedules
@require_hr_settings
def hr_duty_schedules_list(request):
    schedules = DutySchedule.objects.all().order_by('sort_order', 'name')
    return render(request, 'hr/shifts/duty_schedules.html', {'schedules': schedules})


@require_hr_settings
def hr_duty_schedule_create(request):
    if request.method == 'POST':
        form = DutyScheduleForm(request.POST)
        if form.is_valid():
            sched = form.save()
            messages.success(request, f"Duty schedule '{sched.name}' created successfully.")
            return redirect('hr_duty_schedules')
    else:
        form = DutyScheduleForm()
    return render(request, 'hr/shifts/duty_schedule_form.html', {'form': form})


@require_hr_settings
def hr_duty_schedule_edit(request, pk):
    sched = get_object_or_404(DutySchedule, pk=pk)
    if request.method == 'POST':
        form = DutyScheduleForm(request.POST, instance=sched)
        if form.is_valid():
            sched = form.save()
            messages.success(request, f"Duty schedule '{sched.name}' updated successfully.")
            return redirect('hr_duty_schedules')
    else:
        form = DutyScheduleForm(instance=sched)
    return render(request, 'hr/shifts/duty_schedule_form.html', {'form': form})


@require_hr_settings
def hr_duty_schedule_toggle_active(request, pk):
    sched = get_object_or_404(DutySchedule, pk=pk)
    if request.method == 'POST':
        sched.is_active = not sched.is_active
        sched.save(update_fields=['is_active'])
        status_str = "activated" if sched.is_active else "deactivated"
        messages.success(request, f"Duty schedule '{sched.name}' has been {status_str}.")
    return redirect('hr_duty_schedules')


@require_hr_settings
def hr_duty_schedule_delete(request, pk):
    sched = get_object_or_404(DutySchedule, pk=pk)
    if sched.employee_schedules.exists():
        messages.error(request, f"Cannot delete duty schedule '{sched.name}' because it has assigned employees. Deactivate it instead.")
    else:
        sched.delete()
        messages.success(request, f"Duty schedule '{sched.name}' deleted successfully.")
    return redirect('hr_duty_schedules')


# 2. Weekly Rest Days
@require_hr_settings
def hr_weekly_rest_days_list(request):
    dept_id = request.GET.get('department')
    branch_id = request.GET.get('branch')
    rest_filter = request.GET.get('rest_day')

    today = timezone.localdate()

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'bulk_set':
            selected_ids = request.POST.getlist('selected_employees')
            new_rest_day = request.POST.get('rest_day')
            effective_from_str = request.POST.get('effective_from')
            if not selected_ids:
                messages.error(request, "No employees selected.")
            elif not effective_from_str:
                messages.error(request, "Effective from date is required.")
            else:
                try:
                    effective_from = datetime.strptime(effective_from_str, '%Y-%m-%d').date()
                    uses_roster = (new_rest_day == 'roster')
                    weekday = None if uses_roster else int(new_rest_day)

                    success_count = 0
                    errors = []
                    for emp_id in selected_ids:
                        emp = Employee.objects.filter(pk=emp_id).first()
                        if not emp:
                            continue
                        try:
                            update_employee_rest_day_rule(
                                emp,
                                rest_weekday=weekday,
                                uses_roster=uses_roster,
                                effective_from=effective_from,
                                user=request.user,
                                source='Rest day'
                            )
                            success_count += 1
                        except ValidationError as ve:
                            errors.append(f"{emp.user.get_full_name() or emp.employee_code}: {ve.message}")

                    if success_count:
                        messages.success(request, f"Updated rest day rule for {success_count} employee(s).")
                    if errors:
                        for err in errors:
                            messages.error(request, err)
                except ValueError:
                    messages.error(request, "Invalid date format.")
            return redirect(request.get_full_path())

        elif action == 'single_set':
            emp_id = request.POST.get('employee_id')
            new_rest_day = request.POST.get('rest_day')
            effective_from_str = request.POST.get('effective_from')
            emp = get_object_or_404(Employee, pk=emp_id)
            if not effective_from_str:
                messages.error(request, "Effective from date is required.")
            else:
                try:
                    effective_from = datetime.strptime(effective_from_str, '%Y-%m-%d').date()
                    uses_roster = (new_rest_day == 'roster')
                    weekday = None if uses_roster else int(new_rest_day)
                    update_employee_rest_day_rule(
                        emp,
                        rest_weekday=weekday,
                        uses_roster=uses_roster,
                        effective_from=effective_from,
                        user=request.user,
                        source='Rest day'
                    )
                    messages.success(request, f"Updated rest day rule for {emp.user.get_full_name() or emp.employee_code}.")
                except ValidationError as ve:
                    messages.error(request, ve.message)
                except ValueError:
                    messages.error(request, "Invalid date format.")
            return redirect(request.get_full_path())

    employees_qs = Employee.objects.filter(user__is_active=True, deactivated_at__isnull=True).select_related('user', 'department', 'branch').prefetch_related(
        Prefetch(
            'rest_day_rules',
            queryset=RestDayRule.objects.filter(
                effective_from__lte=today
            ).filter(
                Q(effective_to__isnull=True) | Q(effective_to__gte=today)
            ).order_by('-effective_from'),
            to_attr='active_rule_list'
        )
    ).order_by('employee_code', 'user__first_name')

    if dept_id:
        employees_qs = employees_qs.filter(department_id=dept_id)
    if branch_id:
        employees_qs = employees_qs.filter(branch_id=branch_id)

    employee_rows = []
    for emp in employees_qs:
        current_rule = emp.active_rule_list[0] if getattr(emp, 'active_rule_list', None) else None
        if current_rule:
            if current_rule.uses_roster:
                rule_display = "Roster"
                rule_code = "roster"
            else:
                rule_display = dict(WEEKDAY_CHOICES).get(current_rule.rest_weekday, "Sunday")
                rule_code = str(current_rule.rest_weekday)
        else:
            rule_display = "Sunday"
            rule_code = "6"

        if rest_filter:
            if rest_filter == 'roster' and rule_code != 'roster':
                continue
            elif rest_filter != 'roster' and rule_code != rest_filter:
                continue

        employee_rows.append({
            'employee': emp,
            'rule_display': rule_display,
            'rule_code': rule_code,
            'current_rule': current_rule,
        })

    departments = Department.objects.filter(is_active=True).order_by('name')
    branches = Branch.objects.filter(is_active=True).order_by('name')

    context = {
        'employee_rows': employee_rows,
        'departments': departments,
        'branches': branches,
        'weekday_choices': WEEKDAY_CHOICES,
        'selected_dept': int(dept_id) if dept_id and dept_id.isdigit() else None,
        'selected_branch': int(branch_id) if branch_id and branch_id.isdigit() else None,
        'selected_rest_day': rest_filter,
        'today': today,
    }
    return render(request, 'hr/shifts/rest_days.html', context)


@require_hr_settings
def hr_weekly_rest_day_history(request, employee_id):
    employee = get_object_or_404(Employee, pk=employee_id)
    rules = employee.rest_day_rules.all().select_related('created_by').order_by('-effective_from', '-created_at')
    logs = employee.change_logs.filter(field='rest_day').select_related('changed_by').order_by('-changed_at')
    return render(request, 'hr/shifts/rest_day_history.html', {
        'employee': employee,
        'rules': rules,
        'logs': logs,
    })


# 3. Roster
@require_hr_settings
def hr_roster_view(request):
    month_str = request.GET.get('month')
    today = timezone.localdate()
    if not month_str:
        month_str = today.strftime('%Y-%m')

    try:
        year, month = map(int, month_str.split('-'))
        start_date = date(year, month, 1)
        _, num_days = monthrange(year, month)
        end_date = date(year, month, num_days)
    except Exception:
        year, month = today.year, today.month
        start_date = date(year, month, 1)
        _, num_days = monthrange(year, month)
        end_date = date(year, month, num_days)
        month_str = start_date.strftime('%Y-%m')

    days = [date(year, month, d) for d in range(1, num_days + 1)]

    # Check if month is payroll-locked
    is_month_locked = Payroll.objects.filter(
        month=start_date,
        status__in=['APPROVED', 'PAID']
    ).exists()

    # Find employees with uses_roster=True
    roster_emp_ids = RestDayRule.objects.filter(
        uses_roster=True,
        effective_from__lte=end_date
    ).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=start_date)
    ).values_list('employee_id', flat=True).distinct()

    employees = Employee.objects.filter(
        id__in=roster_emp_ids,
        user__is_active=True,
        deactivated_at__isnull=True
    ).select_related('user', 'department', 'branch').order_by('employee_code', 'user__first_name')

    roster_days_qs = RosterDay.objects.filter(
        employee__in=employees,
        date__range=(start_date, end_date)
    )
    roster_map = {(rd.employee_id, rd.date): rd.day_type for rd in roster_days_qs}

    grid_rows = []
    for emp in employees:
        row_days = []
        for d in days:
            dtype = roster_map.get((emp.id, d), 'WORK')
            row_days.append({
                'date': d,
                'date_str': d.strftime('%Y-%m-%d'),
                'day_type': dtype,
            })
        grid_rows.append({
            'employee': emp,
            'days': row_days,
        })

    prev_month_date = (start_date - timedelta(days=1)).replace(day=1)
    next_month_date = (end_date + timedelta(days=1)).replace(day=1)

    context = {
        'month_str': month_str,
        'current_month_display': start_date.strftime('%B %Y'),
        'prev_month': prev_month_date.strftime('%Y-%m'),
        'next_month': next_month_date.strftime('%Y-%m'),
        'days': days,
        'grid_rows': grid_rows,
        'is_month_locked': is_month_locked,
        'employees': employees,
        'today': today,
    }
    return render(request, 'hr/shifts/roster.html', context)


@require_hr_settings
@require_POST
def hr_roster_toggle(request):
    emp_id = request.POST.get('employee_id')
    date_str = request.POST.get('date')
    if not emp_id or not date_str:
        return JsonResponse({'status': 'error', 'message': 'Missing parameters.'}, status=400)

    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'status': 'error', 'message': 'Invalid date format.'}, status=400)

    emp = get_object_or_404(Employee, pk=emp_id)

    try:
        check_payroll_lock_for_employee(emp, target_date)
    except ValidationError as ve:
        return JsonResponse({'status': 'error', 'message': ve.message}, status=400)

    roster_day = RosterDay.objects.filter(employee=emp, date=target_date).first()
    if roster_day:
        old_type = roster_day.day_type
        new_type = 'WORK' if old_type == 'REST' else 'REST'
        roster_day.day_type = new_type
        roster_day.save()
    else:
        old_type = 'WORK'
        new_type = 'REST'
        roster_day = RosterDay.objects.create(employee=emp, date=target_date, day_type=new_type)

    EmployeeChangeLog.objects.create(
        employee=emp,
        field=f'roster_{target_date}',
        old_value=old_type,
        new_value=new_type,
        changed_by=request.user,
        source='Roster'
    )

    return JsonResponse({
        'status': 'success',
        'employee_id': emp.id,
        'date': date_str,
        'day_type': new_type
    })


@require_hr_settings
@require_POST
def hr_roster_fill_pattern(request):
    emp_id = request.POST.get('employee_id')
    start_date_str = request.POST.get('start_date')
    end_date_str = request.POST.get('end_date')
    work_days_str = request.POST.get('work_days', '4')
    rest_days_str = request.POST.get('rest_days', '2')
    month_str = request.POST.get('month')

    emp = get_object_or_404(Employee, pk=emp_id)
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        work_days = int(work_days_str)
        rest_days = int(rest_days_str)
    except Exception:
        messages.error(request, "Invalid input parameters for pattern fill.")
        return redirect(f"{reverse('hr_roster')}?month={month_str}")

    if end_date < start_date:
        messages.error(request, "End date cannot be earlier than start date.")
        return redirect(f"{reverse('hr_roster')}?month={month_str}")

    # Check lock for the range
    curr = start_date
    while curr <= end_date:
        try:
            check_payroll_lock_for_employee(emp, curr)
        except ValidationError as ve:
            messages.error(request, f"Cannot fill pattern: {ve.message}")
            return redirect(f"{reverse('hr_roster')}?month={month_str}")
        curr += timedelta(days=1)

    cycle_length = work_days + rest_days
    curr = start_date
    day_idx = 0
    while curr <= end_date:
        pattern_pos = day_idx % cycle_length
        day_type = 'WORK' if pattern_pos < work_days else 'REST'
        rd, created = RosterDay.objects.get_or_create(
            employee=emp,
            date=curr,
            defaults={'day_type': day_type}
        )
        if not created and rd.day_type != day_type:
            old_type = rd.day_type
            rd.day_type = day_type
            rd.save()
            EmployeeChangeLog.objects.create(
                employee=emp,
                field=f'roster_{curr}',
                old_value=old_type,
                new_value=day_type,
                changed_by=request.user,
                source='Roster'
            )
        elif created:
            EmployeeChangeLog.objects.create(
                employee=emp,
                field=f'roster_{curr}',
                old_value='None',
                new_value=day_type,
                changed_by=request.user,
                source='Roster'
            )
        curr += timedelta(days=1)
        day_idx += 1

    messages.success(request, f"Pattern ({work_days} on, {rest_days} off) filled for {emp.user.get_full_name() or emp.employee_code}.")
    return redirect(f"{reverse('hr_roster')}?month={month_str}")


# 4. Public Holidays
@require_hr_settings
def hr_public_holidays_list(request):
    year_str = request.GET.get('year')
    today = timezone.localdate()
    try:
        year = int(year_str) if year_str else today.year
    except ValueError:
        year = today.year

    holidays = Holiday.objects.filter(date__year=year).select_related('branch').order_by('date')
    all_years = Holiday.objects.dates('date', 'year', order='DESC')
    year_list = sorted(list(set([y.year for y in all_years] + [today.year, today.year + 1])), reverse=True)

    return render(request, 'hr/shifts/holidays.html', {
        'holidays': holidays,
        'current_year': year,
        'year_list': year_list,
    })


@require_hr_settings
def hr_holiday_create(request):
    if request.method == 'POST':
        form = HolidayForm(request.POST)
        if form.is_valid():
            holiday = form.save()
            messages.success(request, f"Public holiday '{holiday.description}' created successfully.")
            return redirect(f"{reverse('hr_public_holidays')}?year={holiday.date.year}")
    else:
        form = HolidayForm()
    return render(request, 'hr/shifts/holiday_form.html', {'form': form})


@require_hr_settings
def hr_holiday_edit(request, pk):
    holiday = get_object_or_404(Holiday, pk=pk)
    if request.method == 'POST':
        form = HolidayForm(request.POST, instance=holiday)
        if form.is_valid():
            holiday = form.save()
            messages.success(request, f"Public holiday '{holiday.description}' updated successfully.")
            return redirect(f"{reverse('hr_public_holidays')}?year={holiday.date.year}")
    else:
        form = HolidayForm(instance=holiday)
    return render(request, 'hr/shifts/holiday_form.html', {'form': form})


@require_hr_settings
def hr_holiday_delete(request, pk):
    holiday = get_object_or_404(Holiday, pk=pk)
    year = holiday.date.year
    month_first = holiday.date.replace(day=1)
    if Payroll.objects.filter(month=month_first, status__in=['APPROVED', 'PAID']).exists():
        holiday.is_active = False
        holiday.save(update_fields=['is_active'])
        messages.warning(request, f"Payroll for {month_first.strftime('%b %Y')} is already approved; holiday was deactivated instead of deleted.")
    else:
        holiday.delete()
        messages.success(request, f"Public holiday '{holiday.description}' deleted successfully.")
    return redirect(f"{reverse('hr_public_holidays')}?year={year}")







@login_required
@user_passes_test(is_hr)
def hr_import_opening_balances(request):
    from Admin.leave_views import render_import_opening_balances
    return render_import_opening_balances(request, 'hr/hrbase.html')


@login_required
@user_passes_test(is_hr)
def hr_leave_year_reviews(request):
    from Admin.leave_views import render_year_end_reviews
    return render_year_end_reviews(request, 'hr/hrbase.html')


@login_required
@user_passes_test(is_hr)
def hr_leave_statement(request):
    from Admin.leave_views import render_role_leave_statement
    return render_role_leave_statement(request, 'HR', 'hr/hrbase.html')

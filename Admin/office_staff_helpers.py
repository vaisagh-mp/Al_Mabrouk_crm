from calendar import monthrange
from datetime import datetime, date
from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from Admin.models import Attendance, Employee, Project, Notification, TeamMemberStatus
from Admin.attendance_autoclose import close_stale_sessions, capped_logout
from employee_data.forms import EmployeeUpdateForm


def get_staff_attendance_context(request, employee):
    """
    Closes stale open sessions, notifies user if auto-closed,
    and returns context dictionary for rendering punch-in page.
    """
    stale_closed = close_stale_sessions(employee)
    if stale_closed:
        for d in getattr(stale_closed, 'dates', []):
            messages.warning(
                request,
                f"Your session from {d} was not punched out and was closed automatically at 12:00 AM."
            )

    user_attendance = Attendance.objects.filter(
        employee=employee,
        log_out_time__isnull=True
    ).first()

    attendance_status_choices = Attendance.ATTENDANCE_STATUS
    location_choices = Attendance.LOCATION_CHOICES
    projects = Project.objects.exclude(status='CANCELLED').order_by('-id')

    return {
        "attendance_status_choices": attendance_status_choices,
        "location_choices": location_choices,
        "projects": projects,
        "user_attendance": user_attendance,
    }


def handle_staff_punch_in(request, employee):
    """
    Processes POST request for staff punch in.
    Returns created Attendance instance or None if already logged in.
    """
    stale_closed = close_stale_sessions(employee)
    if stale_closed:
        for d in getattr(stale_closed, 'dates', []):
            messages.warning(
                request,
                f"Your session from {d} was not punched out and was closed automatically at 12:00 AM."
            )

    if Attendance.objects.filter(employee=employee, log_out_time__isnull=True).exists():
        messages.error(request, "You are already logged in. Please log off before logging in again.")
        return None

    project_id = request.POST.get("project") or None
    location = request.POST.get("location")
    attendance_status = request.POST.get("attendance_status")

    travel_in_time_str = request.POST.get("travel_in_time")
    travel_out_time_str = request.POST.get("travel_out_time")
    travel_in_time = parse_datetime(travel_in_time_str) if travel_in_time_str else None
    travel_out_time = parse_datetime(travel_out_time_str) if travel_out_time_str else None

    attendance = Attendance.objects.create(
        employee=employee,
        project_id=project_id,
        location=location,
        attendance_status=attendance_status,
        login_time=timezone.now(),
        travel_in_time=travel_in_time,
        travel_out_time=travel_out_time,
        status="APPROVED",
    )
    messages.success(request, "You have successfully logged in.")
    return attendance


def handle_staff_punch_out(request, attendance):
    """
    Processes POST request for staff punch out.
    Enforces midnight cutoff and records auto_punched_out flag if capped.
    """
    if attendance.log_out_time is None:
        logout_dt, was_capped = capped_logout(attendance)
        attendance.log_out_time = logout_dt
        if was_capped:
            attendance.auto_punched_out = True
        attendance.save()
        if was_capped:
            capped_date = timezone.localtime(logout_dt).date()
            messages.warning(
                request,
                f"This session was closed at 12:00 AM on {capped_date} because the punch-out was after midnight."
            )
        else:
            messages.success(request, "You have successfully logged off.")
        return True
    else:
        messages.error(request, "You have already logged off.")
        return False


def handle_staff_apply_leave(request, form):
    """
    Processes a valid LeaveForm for staff leave application,
    calculates number of days, saves leave, and notifies superusers.
    """
    leave_application = form.save(commit=False)
    leave_application.user = request.user
    leave_application.no_of_days = (leave_application.to_date - leave_application.from_date).days + 1
    leave_application.save()

    admins = User.objects.filter(is_superuser=True)
    for admin in admins:
        Notification.objects.create(
            recipient=admin,
            message=f"{request.user.username} has applied for {leave_application.leave_type} from {leave_application.from_date} to {leave_application.to_date}."
        )

    messages.success(request, "Leave request submitted successfully.")
    return leave_application


def fetch_user_notifications(user):
    """Fetch unread notifications for a user."""
    notifications = Notification.objects.filter(recipient=user, is_read=False).values("id", "message", "created_at")
    return list(notifications)


def mark_user_notifications_read(user):
    """Mark all unread notifications for a user as read."""
    notifications = Notification.objects.filter(recipient=user, is_read=False)
    if notifications.exists():
        notifications.update(is_read=True)
    return {"message": "Notifications marked as read"}


def get_staff_profile_context(user):
    """Build context for employee profile view."""
    employee = get_object_or_404(Employee, user=user)
    teams = employee.teams_assigned.all()
    team_member_statuses = TeamMemberStatus.objects.filter(employee=employee)

    completed_projects = team_member_statuses.filter(status='COMPLETED').count()
    pending_projects = team_member_statuses.exclude(Q(status='COMPLETED') | Q(status='ONGOING')).count()
    assigned_projects = team_member_statuses.filter(status='ASSIGN').count()

    all_projects = [
        {
            'project': status.team.project,
            'manager': status.team.project.manager,
            'status': status.status
        }
        for status in team_member_statuses
    ]

    current_year = date.today().year
    current_month = date.today().month
    total_days_in_month = monthrange(current_year, current_month)[1]

    total_attendance_records = Attendance.objects.filter(
        employee=employee,
        login_time__year=current_year,
        login_time__month=current_month
    ).count()

    approved_attendance_records = Attendance.objects.filter(
        employee=employee,
        status='APPROVED',
        login_time__year=current_year,
        login_time__month=current_month
    ).count()

    attendance_percentage = (
        (approved_attendance_records / total_days_in_month) * 100
        if total_attendance_records > 0
        else 0
    )

    total_projects = completed_projects + pending_projects + assigned_projects
    total_pending_projects = total_projects - completed_projects

    return {
        'employee': employee,
        'teams': teams,
        'all_projects': all_projects,
        'attendance_percentage': round(attendance_percentage, 1),
        'total_projects': total_projects,
        'completed_projects': completed_projects,
        'pending_projects': total_pending_projects,
    }


def handle_staff_update_profile(request, redirect_url_name, template_name):
    """Handle GET and POST for staff updating their own profile."""
    try:
        employee = request.user.employee_profile
    except (AttributeError, Employee.DoesNotExist):
        messages.error(request, "Your account does not have an employee profile.")
        return redirect(redirect_url_name)

    if request.method == "POST":
        form = EmployeeUpdateForm(request.POST, request.FILES, instance=employee)
        if form.is_valid():
            user = request.user
            user.first_name = form.cleaned_data["first_name"]
            user.last_name = form.cleaned_data["last_name"]
            user.email = form.cleaned_data["email"]

            if form.cleaned_data["password"]:
                user.set_password(form.cleaned_data["password"])

            user.save()
            emp = form.save(commit=False)
            emp.personal_email = form.cleaned_data["email"]
            emp.save()

            messages.success(request, "Your profile has been updated successfully.")
            return redirect(redirect_url_name)
    else:
        form = EmployeeUpdateForm(instance=employee, initial={
            "first_name": request.user.first_name,
            "last_name": request.user.last_name,
            "email": request.user.email,
        })

    return render(request, template_name, {"form": form})


def handle_staff_update_travel_time(request, attendance_id, redirect_url_name, template_name):
    """Handle editing travel time / login-logout timestamps on an attendance record."""
    attendance = get_object_or_404(Attendance, id=attendance_id)

    if request.method == 'POST':
        login_time = request.POST.get('login_time')
        log_out_time = request.POST.get('log_out_time')
        travel_in_time = request.POST.get('travel_in_time')
        travel_out_time = request.POST.get('travel_out_time')

        if login_time:
            attendance.login_time = datetime.strptime(login_time, '%Y-%m-%dT%H:%M')
        if log_out_time:
            attendance.log_out_time = datetime.strptime(log_out_time, '%Y-%m-%dT%H:%M')
        if travel_in_time:
            attendance.travel_in_time = datetime.strptime(travel_in_time, '%Y-%m-%dT%H:%M')
        if travel_out_time:
            attendance.travel_out_time = datetime.strptime(travel_out_time, '%Y-%m-%dT%H:%M')

        employee_id = request.POST.get('employee')
        if not employee_id:
            messages.error(request, "Employee is required.")
            return redirect(redirect_url_name)

        attendance.employee_id = employee_id
        attendance.rejection_reason = request.POST.get('rejection_reason')
        attendance.save()

        messages.success(request, 'Travel time has been updated successfully.')
        return redirect(redirect_url_name)

    employees = Employee.objects.all()
    projects = Project.objects.all()
    return render(request, template_name, {
        'attendance': attendance,
        'employees': employees,
        'projects': projects,
    })

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
from service_logs.views import build_employee_analytics_context
from manager.views import compute_presence_data
from Admin.attendance_register import _register_context, handle_set_day_status, summary_csv_response, summary_excel_response


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
        employee = get_object_or_404(Employee, user=request.user)
        project_id = request.POST.get("project")
        location = request.POST.get("location")
        attendance_status = request.POST.get("attendance_status")

        # Retrieve the travel times from the POST data
        travel_in_time_str = request.POST.get("travel_in_time")
        travel_out_time_str = request.POST.get("travel_out_time")
        travel_in_time = parse_datetime(travel_in_time_str) if travel_in_time_str else None
        travel_out_time = parse_datetime(travel_out_time_str) if travel_out_time_str else None

        attendance, created = Attendance.objects.get_or_create(
            employee=employee,
            project_id=project_id,
            log_out_time__isnull=True,  # Prevent multiple active punch-ins
            defaults={
                "location": location,
                "attendance_status": attendance_status,
                "login_time": now(),
                "travel_in_time": travel_in_time,  # from hidden field (or fallback to now)
                "travel_out_time": travel_out_time,
                "status": "APPROVED",
                "total_hours_of_work": 0,  # Default value
            },
        )

        if created:
            messages.success(request, "You have successfully logged in.")
        else:
            messages.error(request, "You are already logged in. Please log off before logging in again.")

        return redirect("hr_attendance_status")

    return hr_render_attendance_page(request)

@login_required
@user_passes_test(is_hr)
def hr_log_off(request, attendance_id):
    if request.method == "POST":
        # Get the Employee object associated with the logged-in user
        try:
            employee = get_object_or_404(Employee, user=request.user)
        except AttributeError:
            messages.error(request, "Your user is not associated with an employee record.")
            return redirect("hr_attendance_dashboard")
        
        # Get the Attendance record
        attendance = get_object_or_404(Attendance, id=attendance_id, employee=employee)

        # Update the log-out time
        if attendance.log_out_time is None:
            attendance.log_out_time = now()
            # attendance.travel_out_time = now()
            # Calculate total hours worked
            attendance.total_hours_of_work = (
                (attendance.log_out_time - attendance.login_time).total_seconds() / 3600
            )
            attendance.save()
            messages.success(request, "You have successfully logged off.")
        else:
            messages.error(request, "You have already logged off.")

    return redirect("hr-dashboard")  # Redirect to the appropriate page

@login_required
@user_passes_test(is_hr)
def hr_render_attendance_page(request):
    employee = get_object_or_404(Employee, user=request.user)
    user_attendance = Attendance.objects.filter(employee=employee, log_out_time__isnull=True).first()

    attendance_status_choices = Attendance.ATTENDANCE_STATUS
    location_choices = Attendance.LOCATION_CHOICES
    projects = Project.objects.all()

    return render(request, "hr/punchin.html", {
        "attendance_status_choices": attendance_status_choices,
        "location_choices": location_choices,
        "projects": projects,
        "user_attendance": user_attendance,
    })


@login_required
@user_passes_test(is_hr)
def attendance(request):
    employee = get_object_or_404(Employee, user=request.user)
    attendance_records = Attendance.objects.filter(employee=employee).order_by('-login_time')

    # Pagination settings
    paginator = Paginator(attendance_records, 10)  # Show 10 records per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'attendance_records': page_obj,  # Use `page_obj` instead of `attendance_records`
    }
    return render(request, 'hr/attendance.html', context)

@login_required
@user_passes_test(is_hr)
def hr_update_travel_time(request, attendance_id):
    attendance = get_object_or_404(Attendance, id=attendance_id)

    if request.method == 'POST':
        # Extract values for datetime fields only
        login_time = request.POST.get('login_time')
        log_out_time = request.POST.get('log_out_time')
        travel_in_time = request.POST.get('travel_in_time')
        travel_out_time = request.POST.get('travel_out_time')

        # Parse datetime fields
        if login_time:
            attendance.login_time = datetime.strptime(login_time, '%Y-%m-%dT%H:%M')
        if log_out_time:
            attendance.log_out_time = datetime.strptime(log_out_time, '%Y-%m-%dT%H:%M')
        if travel_in_time:
            attendance.travel_in_time = datetime.strptime(travel_in_time, '%Y-%m-%dT%H:%M')
        if travel_out_time:
            attendance.travel_out_time = datetime.strptime(travel_out_time, '%Y-%m-%dT%H:%M')

        # Handle other fields (but don't update project, attendance_status, and status)
        employee_id = request.POST.get('employee')
        if not employee_id:
            messages.error(request, "Employee is required.")
            return redirect('hr_attendance_status')

        attendance.employee_id = employee_id  # Ensure employee_id is provided
        # Exclude project, attendance_status, and status from form submission
        attendance.rejection_reason = request.POST.get('rejection_reason')
        
        # Don't update the "project", "attendance_status", and "status" fields

        attendance.save()

        messages.success(request, 'Travel time has been updated successfully.')
        return redirect('hr_attendance_status')

    employees = Employee.objects.all()
    projects = Project.objects.all()
    return render(request, 'hr/update_travel_time.html', {
        'attendance': attendance,
        'employees': employees,
        'projects': projects,
    })

@login_required
@user_passes_test(is_hr)
def dashboard(request):
    user = request.user
    employee = request.user.employee_profile

    # Superuser Admin Dashboard
    if user.is_superuser:
        return render(request, 'Admin/dashboard.html')

    # HR Dashboard
    elif employee.is_hr:
        hr = get_object_or_404(Employee, user=user)

        #Total Employees
        total_employees = Employee.objects.filter(
            Q(is_employee=True) | Q(is_manager=True) | Q(is_administration=True) | Q(is_hr=True)
        ).count()

        #Total Leave Requests
        total_leave_requests = Leave.objects.filter(approval_status="PENDING").count()

        #Today's Attendance Records
        today = timezone.now().date()
        today_attendance_records = Attendance.objects.filter(login_time__date=today)

        today_attendance = Attendance.objects.filter(
            employee=hr,
            login_time__date=today
        ).first()

        #Get Total Leave Balances
        leave_balances = LeaveBalance.objects.aggregate(
            total_annual_leave=Sum('annual_leave'),
            total_sick_leave=Sum('sick_leave'),
            # total_casual_leave=Sum('casual_leave')
        )

        # Set Punch In & Punch Out times
        local_tz = pytz.timezone('Asia/Dubai')

        last_punch_in = (
            localtime(today_attendance.login_time).astimezone(local_tz).strftime('%I:%M %p')
            if today_attendance else "Not Punched In"
        )

        last_punch_out = (
            localtime(today_attendance.log_out_time).astimezone(local_tz).strftime('%I:%M %p')
            if today_attendance and today_attendance.log_out_time else "Not Punched Out"
        )

        #Get Pending Leave Applications
        pending_leaves = Leave.objects.filter(approval_status="PENDING").count()

        #Get Attendance Statistics
        current_year = datetime.now().year
        current_month = datetime.now().month
        total_days_in_month = monthrange(current_year, current_month)[1]

        approved_attendance_records = Attendance.objects.filter(
            status='APPROVED',
            login_time__year=current_year,
            login_time__month=current_month
        ).count()

        attendance_percentage = (approved_attendance_records / total_days_in_month) * 100 if total_days_in_month else 0
        leave_requests = Leave.objects.filter(user=user, approval_status="PENDING").count()
        leave_balance = LeaveBalance.objects.filter(user=user).first()
        annual_leave_taken = Leave.objects.filter(
            user=user,
            approval_status="APPROVED",
            leave_type="ANNUAL LEAVE",
            from_date__year=today.year
        ).aggregate(total=Sum('no_of_days'))['total'] or 0
        sick_leave_taken = Leave.objects.filter(
            user=user,
            approval_status="APPROVED",
            leave_type="SICK LEAVE",
            from_date__year=today.year
        ).aggregate(total=Sum('no_of_days'))['total'] or 0
        leaves_taken = annual_leave_taken + sick_leave_taken
        balance_annual_leave = max((leave_balance.annual_leave if leave_balance else 0) - annual_leave_taken, 0)
        balance_sick_leave = max((leave_balance.sick_leave if leave_balance else 0) - sick_leave_taken, 0)
        total_leaves = (leave_balance.annual_leave + leave_balance.sick_leave) if leave_balance else 0

        # Fetching Workdays
        worked_days = hr.work_days
        absent_days = pending_leaves
        loss_of_pay_days = absent_days - total_leaves if absent_days > total_leaves else 0

        # Fetching project details
        project_details = Project.objects.annotate(
            leader_name=F('manager__user__username')
        ).values('id', 'name', 'leader_name', 'status', 'category', 'priority').order_by('-created_at')[:5]

        # Time Settings
        local_tz = pytz.timezone('Asia/Kolkata')
        current_time = timezone.now().astimezone(local_tz).strftime('%I:%M %p, %d %b %Y')

        chart_data = compute_presence_data(hr, today.year, today.month)
        months = [
            {"value": i, "name": calendar.month_name[i]}
            for i in range(1, 13)
        ]

        context = {
            'hr': hr,
            'total_employees': total_employees,
            'total_leave_requests': total_leave_requests,
            'leave_balances': leave_balances,
            'pending_leaves': pending_leaves,
            'today_attendance_records': today_attendance_records,
            'attendance_percentage': round(attendance_percentage, 1),
            'worked_days': worked_days,
            'absent_days': absent_days,
            'loss_of_pay_days': loss_of_pay_days,
            'project_details': project_details,
            'current_time': current_time,
            'total_leaves': total_leaves,
            'leaves_taken': leaves_taken,
            'annual_leave_taken': annual_leave_taken,
            'sick_leave_taken': sick_leave_taken,
            'balance_annual_leave': balance_annual_leave,
            'balance_sick_leave': balance_sick_leave,
            'leave_requests': leave_requests,
            'chart_data': chart_data,
            'months': months,
            'current_month': today.month,
            "last_punch_in": last_punch_in,
            "last_punch_out": last_punch_out,
            'user_attendance': today_attendance,
        }

        return render(request, 'hr/dashboard.html', context)

    # Employee Dashboard
    else:
        return render(request, 'employee/employee_dashboard.html')

# create employee
@login_required
@user_passes_test(is_hr)
def hr_create_employee(request):
    employee = request.user.employee_profile  
    if not employee.is_hr:
        return redirect('custom-login')  # Ensure 'custom-login' exists in urls.py

    if request.method == 'POST':
        form = EmployeeCreationForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, 'Employee created successfully!')
                return redirect('hr_create_employee')  # 🔹 Ensure this matches urls.py
            except Exception as e:
                messages.error(request, f'There was an error creating the employee: {e}')
        else:
            messages.error(request, 'Please correct the errors below.')
            print(form.errors)
    else:
        form = EmployeeCreationForm()

    return render(request, 'hr/create_employee.html', {'form': form})


@login_required
@user_passes_test(is_hr)
def hr_employee_list(request):
    employee = request.user.employee_profile 
    if not employee.is_hr:
        return redirect('login')

    # Filter ongoing project assignments
    ongoing_project_assignments = TeamMemberStatus.objects.filter(
        status='ONGOING'
    ).select_related('team__project')

    # Exclude managers and prefetch ongoing project assignments
    employees = Employee.objects.select_related('user').prefetch_related(
        Prefetch(
            'project_statuses',  # This matches the `related_name` in TeamMemberStatus
            queryset=ongoing_project_assignments,
            to_attr='assigned_projects'  # Prefetch results stored as 'assigned_projects'
        )
    )

    paginator = Paginator(employees, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'hr/employee_list.html', {'page_obj': page_obj})

@login_required
@user_passes_test(is_hr)
def hr_attendance_list_view(request):
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
def hr_set_day_status(request, employee_id, date):
    return handle_set_day_status(request, employee_id, date, role='hr')

@login_required
@user_passes_test(is_hr)
def hr_employee_leave_list(request):
    employee = request.user.employee_profile 
    if not employee.is_hr:
        return redirect('admin-dashboard')

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
    employee = request.user.employee_profile 
    if not employee.is_hr:
        return redirect('admin-dashboard')

    search_query = request.GET.get('search', '').strip()

    # Fetch only employee leave requests
    pending_leaves = Leave.objects.filter(approval_status='PENDING').select_related('user')

    # Apply search filter
    if search_query:
        pending_leaves = pending_leaves.filter(user__username__icontains=search_query)

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
    # Fetch the employee
    employee = get_object_or_404(Employee, pk=employee_id)
    teams = employee.teams_assigned.all()

    
    manager_projects = [] 

    if employee.is_manager:
        # Fetch projects managed by the manager
        manager_projects = Project.objects.filter(manager=employee).select_related('manager')

    team_member_statuses = TeamMemberStatus.objects.filter(employee=employee)
    manager_statuses = TeamMemberStatus.objects.filter(employee=employee.is_manager)

    # Count statuses for the employee's projects
    completed_projects = team_member_statuses.filter(status='COMPLETED').count()
    pending_projects = team_member_statuses.exclude(Q(status='COMPLETED') | Q(status='ONGOING')).count()   
    assigned_projects = team_member_statuses.filter(status='ASSIGN').count()
    
    # Count statuses for the employee's projects
    manager_completed_projects = Project.objects.filter(manager=employee, status='COMPLETED').count()
    manager_pending_projects =  Project.objects.filter(manager=employee).exclude(Q(status='COMPLETED') | Q(status='ONGOING')).count() 
    manager_assigned_projects = manager_statuses.filter(status='ASSIGN').count()

    # Get the related projects from the team member status
    all_projects = [
        {
            'project': status.team.project,
            'manager': status.team.project.manager,
            'status': status.status
        }
        for status in team_member_statuses
    ]

    # Calculate attendance percentage
    # total_attendance_records = Attendance.objects.filter(employee=employee).count()
    # approved_attendance_records = Attendance.objects.filter(
    #     employee=employee, status='APPROVED'
    # ).count()
    # attendance_percentage = (
    #     (approved_attendance_records / 30) * 100
    #     if total_attendance_records > 0
    #     else 0
    # )

    # Get the current year and month
    current_year = date.today().year
    current_month = date.today().month

    # Get the total number of days in the current month
    total_days_in_month = monthrange(current_year, current_month)[1]

    # Calculate total and approved attendance records for the current month
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

    # Calculate attendance percentage
    attendance_percentage = (
        (approved_attendance_records / total_days_in_month) * 100
        if total_attendance_records > 0
        else 0
    )

    # Count total projects and pending projects
    total_projects = completed_projects + pending_projects + assigned_projects
    total_pending_projects = total_projects - completed_projects

    # Context for template
    context = {
        'employee': employee,
        'teams': teams,
        'all_projects': all_projects,
        'manager_projects': manager_projects,
        'attendance_percentage': round(attendance_percentage, 1),
        'total_projects': total_projects,
        'completed_projects': completed_projects,
        'pending_projects': total_pending_projects,
        'manager_completed_projects': manager_completed_projects,
        'manager_pending_projects': manager_pending_projects,
        'manager_assigned_projects': manager_assigned_projects,
    }

    return render(request, 'hr/employee_profile.html', context)


@login_required
@user_passes_test(is_hr)
def hr_edit_employee(request, employee_id):
    """Admin can edit an employee's profile"""
    
    # Ensure we are fetching an Employee instance, NOT a User
    try:
        employee = Employee.objects.select_related("user").get(id=employee_id)
    except Employee.DoesNotExist:
        messages.error(request, "Employee not found.")
        return redirect("hr_employee_list")

    # Ensure employee has a related user
    if not employee.user:
        messages.error(request, "This employee does not have a linked user account.")
        return redirect("hr_employee_list")

    user = employee.user  # Safely access related User instance

    if request.method == "POST":
        form = ManagerEmployeeUpdateForm(request.POST, request.FILES, instance=employee)
        if form.is_valid():
            # Update User fields
            user.username = form.cleaned_data["username"]
            user.first_name = form.cleaned_data["first_name"]
            user.last_name = form.cleaned_data["last_name"]
            user.email = form.cleaned_data["email"]

            if form.cleaned_data.get("password"):
                user.set_password(form.cleaned_data["password"])

            user.save()

            # Update Employee fields
            employee.phone_number = form.cleaned_data["phone_number"]
            employee.rank = form.cleaned_data["rank"]
            employee.salary = form.cleaned_data["salary"]
            employee.date_of_birth = form.cleaned_data["date_of_birth"]
            employee.date_of_join = form.cleaned_data["date_of_join"]
            employee.work_days = form.cleaned_data["work_days"]
            employee.holidays = form.cleaned_data["holidays"]
            employee.overseas_days = form.cleaned_data["overseas_days"]
            employee.address = form.cleaned_data["address"]

            # Handle profile picture upload
            if "profile_picture" in request.FILES:
                employee.profile_picture = request.FILES["profile_picture"]

            employee.save()

            messages.success(request, "Employee profile updated successfully.")
            return redirect("hr_employee_list")

    else:
        form = ManagerEmployeeUpdateForm(instance=employee, initial={
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone_number": employee.phone_number,
            "rank": employee.rank,
            "salary": employee.salary,
            "date_of_birth": employee.date_of_birth,
            "date_of_join": employee.date_of_join,
            "work_days": employee.work_days,
            "holidays": employee.holidays,
            "overseas_days": employee.overseas_days,
            "address": employee.address,
        })

    return render(request, "hr/edit_employee.html", {"form": form, "employee": employee})


@login_required
@user_passes_test(is_hr)
@require_POST
def hr_delete_employee(request, employee_id):
    employee = get_object_or_404(Employee, id=employee_id)
    # Process deletion on POST request
    if request.method == 'POST':
        employee.delete()
        messages.success(request, 'Employee deleted successfully!')
        return redirect('hr_employee_list')

    # Redirect back if accessed via GET (optional)
    return redirect('hr_employee_list')

@login_required
@user_passes_test(is_hr)
def hr_apply_leave(request):
    if request.method == 'POST':
        form = LeaveForm(request.POST, request.FILES)
        if form.is_valid():
            leave_application = form.save(commit=False)
            leave_application.user = request.user  # Assign current user
            leave_application.no_of_days = (leave_application.to_date - leave_application.from_date).days + 1  # Include both dates
            leave_application.save()

            # Send notification to all admins
            admins = User.objects.filter(is_superuser=True)  # Get all admin users
            for admin in admins:
                Notification.objects.create(
                    recipient=admin,
                    message=f"{request.user.username} has applied for {leave_application.leave_type} from {leave_application.from_date} to {leave_application.to_date}."
                )

            messages.success(request, "Leave request submitted successfully.")
            return redirect('hr_leave_status')  # Redirect after success
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
    notifications = Notification.objects.filter(recipient=request.user, is_read=False).values("id", "message", "created_at")
    return JsonResponse({"notifications": list(notifications)})

@login_required
@user_passes_test(is_hr)
def hr_mark_notifications_as_read(request):
    """Mark all unread notifications for the logged-in manager as read."""
    notifications = Notification.objects.filter(recipient=request.user, is_read=False)
    
    if notifications.exists():
        notifications.update(is_read=True)  # Bulk update for efficiency
    
    return JsonResponse({"message": "Notifications marked as read"})


@login_required
@user_passes_test(is_hr)
def hr_profile(request):
    # Fetch the employee
    employee = get_object_or_404(Employee, user=request.user)
    teams = employee.teams_assigned.all()

    team_member_statuses = TeamMemberStatus.objects.filter(employee=employee)

    # Count statuses for the employee's projects
    completed_projects = team_member_statuses.filter(
        status='COMPLETED').count()
    pending_projects = team_member_statuses.exclude(
        Q(status='COMPLETED') | Q(status='ONGOING')).count()
    assigned_projects = team_member_statuses.filter(status='ASSIGN').count()

    # Get the related projects from the team member status
    all_projects = [
        {
            'project': status.team.project,
            'manager': status.team.project.manager,
            'status': status.status
        }
        for status in team_member_statuses
    ]

    # Calculate attendance percentage
    # total_attendance_records = Attendance.objects.filter(employee=employee).count()
    # approved_attendance_records = Attendance.objects.filter(
    #     employee=employee, status='APPROVED'
    # ).count()
    # attendance_percentage = (
    #     (approved_attendance_records / 30) * 100
    #     if total_attendance_records > 0
    #     else 0
    # )

    # Get the current year and month
    current_year = date.today().year
    current_month = date.today().month

    # Get the total number of days in the current month
    total_days_in_month = monthrange(current_year, current_month)[1]

    # Calculate total and approved attendance records for the current month
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

    # Calculate attendance percentage
    attendance_percentage = (
        (approved_attendance_records / total_days_in_month) * 100
        if total_attendance_records > 0
        else 0
    )

    # Count total projects and pending projects
    total_projects = completed_projects + pending_projects + assigned_projects
    total_pending_projects = total_projects - completed_projects

    # Context for template
    context = {
        'employee': employee,
        'teams': teams,
        'all_projects': all_projects,
        'attendance_percentage': round(attendance_percentage, 1),
        'total_projects': total_projects,
        'completed_projects': completed_projects,
        'pending_projects': total_pending_projects,
    }

    return render(request, 'hr/profile.html', context)


@login_required
@user_passes_test(is_hr)
def hr_update_profile(request):
    employee = request.user.employee_profile  # Get the Employee model related to the logged-in user

    if request.method == "POST":
        form = EmployeeUpdateForm(request.POST, request.FILES, instance=employee)
        if form.is_valid():
            user = request.user
            user.first_name = form.cleaned_data["first_name"]
            user.last_name = form.cleaned_data["last_name"]
            user.email = form.cleaned_data["email"]

            # Save new password if provided
            if form.cleaned_data["password"]:
                user.set_password(form.cleaned_data["password"])

            user.save()
            form.save()

            messages.success(request, "Your profile has been updated successfully.")
            return redirect("hr_profile_view")

    else:
        form = EmployeeUpdateForm(instance=employee, initial={
            "first_name": request.user.first_name,
            "last_name": request.user.last_name,
            "email": request.user.email,
        })

    return render(request, "hr/updateprofile.html", {"form": form})


@login_required
@user_passes_test(is_hr)
def hr_employee_analytics(request):
    context = build_employee_analytics_context(request)
    context["base_template"] = "hr/hrbase.html"
    context["hide_project_links"] = True
    context["register_url"] = reverse('hr_attendance_list_view')
    return render(request, "analytics/employee_based.html", context)


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



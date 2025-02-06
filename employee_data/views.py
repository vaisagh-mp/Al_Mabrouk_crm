from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from calendar import monthrange
from django.utils.timezone import now
from django.utils.dateparse import parse_datetime
from django.http import JsonResponse
from django.core.paginator import Paginator
from .forms import LeaveForm, EmployeeUpdateForm
from datetime import date
from django.db.models import Sum
from django.db.models import Q, F
from datetime import datetime
from django.contrib import messages
from Admin.models import Employee, Attendance, ProjectAssignment, Project, Team, TeamMemberStatus, ActivityLog, Leave, LeaveBalance
from django.utils import timezone
from django.utils.timezone import localtime
import pytz

@login_required
def employee_dashboard(request):
    employee = get_object_or_404(Employee, user=request.user)

    # Fetch attendance records for the employee
    attendance_records = Attendance.objects.filter(employee=employee).order_by('-login_time')

    today = datetime.now().date()
    today_attendance = attendance_records.filter(login_time__date=today).first()

    # Convert to local timezone (Asia/Kolkata)
    local_tz = pytz.timezone('Asia/Kolkata')
    now = timezone.now().astimezone(local_tz)

    # Punch In/Out Details
    if today_attendance:
        last_punch_in = localtime(today_attendance.login_time).astimezone(local_tz).strftime('%I:%M %p')
        last_punch_out = (
            localtime(today_attendance.log_out_time).astimezone(local_tz).strftime('%I:%M %p')
            if today_attendance.log_out_time else "Not Punched Out"
        )
    else:
        last_punch_in = "Not Punched In"
        last_punch_out = "Not Punched Out"

    # Fetch Leave Balance (default values if not found)
    leave_balance = LeaveBalance.objects.filter(user=request.user).first()
    total_leaves = leave_balance.annual_leave + leave_balance.sick_leave + leave_balance.casual_leave if leave_balance else 0

    # Calculate Leaves Taken, Absent Days, and Pending Requests
    current_year = now.year
    leaves_taken = Leave.objects.filter(user=request.user, approval_status="APPROVED", from_date__year=current_year).aggregate(total=Sum('no_of_days'))['total'] or 0
    absent_days = Leave.objects.filter(user=request.user, approval_status="APPROVED", from_date__year=current_year).aggregate(total=Sum('no_of_days'))['total'] or 0
    leave_requests = Leave.objects.filter(user=request.user, approval_status="PENDING").count()

    # Get total Annual Leave taken
    annual_leave_taken = Leave.objects.filter(
        user=request.user,
        approval_status="APPROVED",
        leave_type="ANNUAL LEAVE",
        from_date__year=current_year
    ).aggregate(total=Sum('no_of_days'))['total'] or 0

    # Get total Sick Leave taken
    sick_leave_taken = Leave.objects.filter(
        user=request.user,
        approval_status="APPROVED",
        leave_type="SICK LEAVE",
        from_date__year=current_year
    ).aggregate(total=Sum('no_of_days'))['total'] or 0

    # Get total Casual Leave taken
    casual_leave_taken = Leave.objects.filter(
        user=request.user,
        approval_status="APPROVED",
        leave_type="CASUAL LEAVE",
        from_date__year=current_year
    ).aggregate(total=Sum('no_of_days'))['total'] or 0



    # Fetch Employee Work Days
    worked_days = employee.work_days

    # Loss of Pay Days = Absent Days
    loss_of_pay_days = absent_days - total_leaves if absent_days > total_leaves else 0

    # Calculate Attendance Percentage
    current_month = now.month
    total_days_in_month = monthrange(current_year, current_month)[1]
    approved_attendance_records = Attendance.objects.filter(
        employee=employee,
        status='APPROVED',
        login_time__year=current_year,
        login_time__month=current_month
    ).count()
    attendance_percentage = (approved_attendance_records / total_days_in_month) * 100

    # Fetch assigned work
    assigned_work = TeamMemberStatus.objects.filter(employee=employee, status='ASSIGN').select_related('team__project')
    projects = TeamMemberStatus.objects.filter(employee=employee).select_related('team__project')
    total_projects = projects.count()
    pending_projects = projects.exclude(team__project__status='COMPLETED').count()
    completed_projects = assigned_work.filter(team__project__status='COMPLETED').count()

    context = {
        'employee': employee,
        'attendance_records': attendance_records,
        'attendance_percentage': attendance_percentage,
        'current_time': now.strftime('%I:%M %p, %d %b %Y'),
        'last_punch_in': last_punch_in,
        'last_punch_out': last_punch_out,
        'total_projects': total_projects,
        'pending_projects': pending_projects,
        'completed_projects': completed_projects,
        'total_leaves': total_leaves,
        'leaves_taken': leaves_taken,
        'absent_days': absent_days,
        'annual_leave_taken': annual_leave_taken,
        'sick_leave_taken': sick_leave_taken,
        'casual_leave_taken': casual_leave_taken,
        'leave_requests': leave_requests,
        'worked_days': worked_days,
        'loss_of_pay_days': loss_of_pay_days,
    }

    return render(request, 'employee/employee_dashboard.html', context)

@login_required
def submit_attendance_request(request):
    # Ensure the user has an employee profile
    if not hasattr(request.user, 'employee_profile'):
        return redirect('dashboard')

    # Get the employee object for both GET and POST requests
    employee = request.user.employee_profile

    if request.method == 'POST':
        location = request.POST.get('location')
        attendance_status = request.POST.get('attendance_status')
        login_time = request.POST.get('login_time')
        log_out_time = request.POST.get('log_out_time')
        project_id = request.POST.get('project')

        # Calculate total hours if times are provided
        total_hours_of_work = 0
        if login_time and log_out_time:
            # Convert the string to datetime objects
            login_time_obj = datetime.strptime(login_time, "%Y-%m-%dT%H:%M")
            log_out_time_obj = datetime.strptime(log_out_time, "%Y-%m-%dT%H:%M")

            # Calculate the total hours of work
            delta = log_out_time_obj - login_time_obj
            total_hours_of_work = delta.total_seconds() / 3600  # Convert seconds to hours

        # Fetch the project instance if a project ID is provided
        project = None
        if project_id:
            try:
                project = Project.objects.get(id=project_id)
            except Project.DoesNotExist:
                messages.error(request, "Selected project does not exist.")
                return redirect('submit_attendance_request')

        # Create the attendance request
        Attendance.objects.create(
            employee=employee,
            login_time=login_time_obj,
            log_out_time=log_out_time_obj,
            total_hours_of_work=total_hours_of_work,
            location=location,
            attendance_status=attendance_status,
            project=project,  # Include the project field
            status='PENDING',
        )
        messages.success(request, "Attendance request submitted successfully!")
        return redirect('employee_dashboard')
    
    # Fetch projects assigned to the current employee via projectassignment
    assigned_projects = Project.objects.filter(projectassignment__employee=employee)

    # Add context for location, attendance status, and project choices
    context = {
        'location_choices': Attendance.LOCATION_CHOICES,
        'attendance_status_choices': Attendance.ATTENDANCE_STATUS,
        'projects': assigned_projects,  # Pass projects for the form dropdown
    }

    return render(request, 'employee/submit_attendance_request.html', context)

@login_required
def update_project_status(request, project_id):
    if request.method == "POST":
        # Fetch the project
        project = get_object_or_404(Project, id=project_id)
        
        # Get the logged-in employee's TeamMemberStatus for the assigned project
        employee = request.user.employee_profile   # Assuming 'Employee' is the related model for User
        team_member_status = get_object_or_404(TeamMemberStatus, team__project=project, employee=employee)

        # Get the new status from the POST data
        new_status = request.POST.get("status")
        
        # Ensure the status is valid based on STATUS_CHOICES from TeamMemberStatus
        if new_status in dict(TeamMemberStatus.STATUS_CHOICES):
            # Update the status for this employee's TeamMemberStatus record
            team_member_status.status = new_status
            team_member_status.save()
            messages.success(request, f"Status for project '{project.name}' updated to {team_member_status.get_status_display()}.")
        else:
            messages.error(request, "Invalid status selected.")
    
    return redirect("employee_dashboard")  # Redirect back to the employee dashboard

def attendance_list(request):
    employee = get_object_or_404(Employee, user=request.user)
    attendance_records = Attendance.objects.filter(employee=employee).order_by('-login_time')

    # Pagination settings
    paginator = Paginator(attendance_records, 10)  # Show 10 records per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'attendance_records': page_obj,  # Use `page_obj` instead of `attendance_records`
    }
    return render(request, 'employee/attendance_list.html', context)

@login_required
def profile(request):
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

    return render(request, 'employee/profile.html', context)

@login_required
def update_profile(request):
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
            return redirect("profile_view")

    else:
        form = EmployeeUpdateForm(instance=employee, initial={
            "first_name": request.user.first_name,
            "last_name": request.user.last_name,
            "email": request.user.email,
        })

    return render(request, "employee/updateprofile.html", {"form": form})

@login_required
def projects(request):
    # Get the currently logged-in user
    current_user = request.user

    # Retrieve the related employee object
    employee = current_user.employee_profile

    # Fetch the employee's assigned projects through TeamMemberStatus
    team_member_statuses = TeamMemberStatus.objects.filter(employee=employee)

    # Prepare the project data
    project_data = [
        {
            "id": status.team.project.id,
            "name": status.team.project.name,
            "category": status.team.project.category,
            "code": status.team.project.code,
            "status": status.status,
            "invoice": status.team.project.invoice_amount,
            "currency": status.team.project.currency_code,
            "purchase_and_expenses": status.team.project.purchase_and_expenses,
        }
        for status in team_member_statuses
    ]

    # Paginate the projects (Show 5 projects per page)
    paginator = Paginator(project_data, 5)  
    page_number = request.GET.get('page')
    paginated_projects = paginator.get_page(page_number)

    # Context for the template
    context = {
        "projects": paginated_projects,
    }

    return render(request, 'employee/project_list.html', context)

@login_required
def project_details(request, project_id):
    # Get the specific project by ID
    project = get_object_or_404(Project, id=project_id)
    employee = get_object_or_404(Employee, user=request.user)
    statuses = TeamMemberStatus.objects.filter(
        team__project__id=project_id,
        employee=employee  # Filtering by current logged-in employee
    ).order_by('-last_updated')
    logs = ActivityLog.objects.filter(team_member_status__employee_id=employee.id).order_by('-changed_at')


    # Fetch teams associated with the project
    teams = project.teams.all()

    status_choices = TeamMemberStatus.STATUS_CHOICES

    engineers = []
    engineer_salaries = {}
    total_engineer_salary = 0  # Initialize the total salary variable

    # Dictionary to store total project hours per engineer
    engineer_project_hours = {}

    for team in teams:
        # Iterate through each employee in the team
        for engineer in team.employees.all():
            engineers.append(engineer.user.username)
            # Assuming the salary is stored on the Employee model
            engineer_salaries[engineer.user.username] = engineer.salary
            total_engineer_salary += engineer.salary  # Add salary to the total

            # Calculate total project hours for this engineer based on attendance
            attendance_records = Attendance.objects.filter(
                employee=engineer, project=project)
            total_hours = sum(
                record.total_hours_of_work or 0 for record in attendance_records)
            engineer_project_hours[engineer.user.username] = round(
                total_hours, 2)  # Store the total hours worked for this project
    total_expenses = project.calculate_expenses()

    # Calculate profit
    profit = project.calculate_profit()

    work_days = project.calculate_total_work_days()

    # Prepare data for the project
    project_data = {
        "project_name": project.name,
        "project_manager": project.manager,
        "code": project.code,
        "category": project.category,
        "purchase_and_expenses": project.purchase_and_expenses,
        "invoice_amount": project.invoice_amount,
        "engineers": engineers,
        "engineer_salaries": engineer_salaries,  # Include engineer salaries
        "engineer_project_hours": engineer_project_hours,  # Total hours for each engineer
        "total_work_days": work_days,  # Rounded to 2 decimal points
        # Rounded to 2 decimal points
        # "total_project_hours": round(total_project_hours, 2),
        "currency_code": project.currency_code,
        "total_expenses": round(total_expenses, 2),  # Rounded total expenses
        "profit": profit,
        "status": project.status,
        # Total salary for all engineers
        "total_engineer_salary": round(total_engineer_salary, 2),
        "project_create": project.created_at,
        "deadline_date": project.deadline_date,
        "statuses": statuses,
        'logs': logs,
        "project_id": project.id,
        "status_choices": status_choices,
    }

    # Pass the data to the template
    context = {
        "project_data": project_data
    }

    return render(request, 'employee/project_details.html', context)

@login_required
def update_team_member_status(request, project_id):
    if request.method == "POST":
        employee = get_object_or_404(Employee, user=request.user)
        status = request.POST.get("status")
        remark = request.POST.get("remark", "")

        # Fetch the TeamMemberStatus entry
        team_member_status = TeamMemberStatus.objects.filter(
            team__project_id=project_id,
            employee=employee
        ).first()

        if team_member_status:
            team_member_status.status = status
            team_member_status.notes = remark
            team_member_status.save()

            messages.success(request, "Status updated successfully.")
        else:
            messages.error(request, "No team member status found for this project.")

    return redirect('project_details', project_id=project_id)

@login_required
def log_in(request):
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
                "status": "PENDING",
                "total_hours_of_work": 0,  # Default value
            },
        )

        if created:
            messages.success(request, "You have successfully logged in.")
        else:
            messages.error(request, "You are already logged in. Please log off before logging in again.")

        return redirect("employee_dashboard")

    return render_attendance_page(request)

@login_required
def log_off(request, attendance_id):
    if request.method == "POST":
        # Get the Employee object associated with the logged-in user
        try:
            employee = get_object_or_404(Employee, user=request.user)
        except AttributeError:
            messages.error(request, "Your user is not associated with an employee record.")
            return redirect("attendance_dashboard")
        
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

    return redirect("employee_dashboard")  # Redirect to the appropriate page

@login_required
def render_attendance_page(request):
    employee = get_object_or_404(Employee, user=request.user)
    user_attendance = Attendance.objects.filter(employee=employee, log_out_time__isnull=True).first()

    attendance_status_choices = Attendance.ATTENDANCE_STATUS
    location_choices = Attendance.LOCATION_CHOICES
    projects = Project.objects.all()

    return render(request, "employee/punchin.html", {
        "attendance_status_choices": attendance_status_choices,
        "location_choices": location_choices,
        "projects": projects,
        "user_attendance": user_attendance,
    })


@login_required
def apply_leave(request):
    if request.method == 'POST':
        form = LeaveForm(request.POST)
        if form.is_valid():
            leave_application = form.save(commit=False)
            leave_application.user = request.user  # Assign current user
            leave_application.no_of_days = (leave_application.to_date - leave_application.from_date).days + 1  # Include both dates
            leave_application.save()

            messages.success(request, "Leave request submitted successfully.")
            return redirect('employee_dashboard')  # Redirect after success

    else:
        form = LeaveForm()

    return render(request, 'employee/leave.html', {'form': form})

@login_required
def my_leave_status(request):
    """View to display the leave status of the logged-in user."""
    leaves_list = Leave.objects.filter(user=request.user).order_by("-from_date")
    
    # Paginate leave requests (10 per page)
    paginator = Paginator(leaves_list, 10)  
    page_number = request.GET.get("page")
    leaves = paginator.get_page(page_number)

    return render(request, "employee/leavestatus.html", {"leaves": leaves})

@login_required
def leave_records(request):
    """View to display the leave records for the logged-in user."""

    # Retrieve leave balance from the database
    leave_balance = LeaveBalance.objects.filter(user=request.user).first()

    if not leave_balance:
        leave_balance = LeaveBalance.objects.create(user=request.user)  # Create default balance

    # Convert balance into a dictionary for easy access
    leave_balances = {
        "ANNUAL LEAVE": leave_balance.annual_leave,
        "SICK LEAVE": leave_balance.sick_leave,
        "CASUAL LEAVE": leave_balance.casual_leave,
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

    return render(request, "employee/leaverecords.html", {"leave_summary": leave_summary})
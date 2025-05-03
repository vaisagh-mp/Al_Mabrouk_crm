from django.shortcuts import redirect, render, get_object_or_404
from django.contrib.auth.models import User
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from calendar import monthrange
from django.utils.dateparse import parse_datetime
from django.utils.timezone import now
from django.utils.timezone import localtime
from django.utils import timezone
from django.db.models import Sum
from datetime import date
import pytz
from datetime import datetime
from django.db.models import Q, F
from django.db.models import Prefetch
from django.core.paginator import Paginator
from Admin.forms import ProjectAssignmentForm
from employee_data.forms import EmployeeUpdateForm, LeaveForm
from .forms import TeamForm
from django.contrib import messages
from django.http import JsonResponse
from Admin.models import Attendance, Project, Team, TeamMemberStatus, Employee, ActivityLog, Leave, LeaveBalance, Notification


# Home
@login_required
def dashboard(request):
    user = request.user

    # Superuser Admin Dashboard
    if user.is_superuser:
        return render(request, 'Admin/dashboard.html')

    # Manager Dashboard
    elif user.is_staff:
        manager = get_object_or_404(Employee, user=user)

        # Get today's date
        today = timezone.now().date()

        # Get today's attendance record (if exists)
        today_attendance = Attendance.objects.filter(
            employee=manager,
            login_time__date=today
        ).first()

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

        # Fetch projects assigned to the manager
        # assigned_projects = Project.objects.filter(manager=manager, status="ASSIGN").order_by('-created_at')[:10]
        total_projects = Project.objects.filter(manager=manager).count()
        completed_projects = Project.objects.filter(status='COMPLETED').count()
        # 10 latest assigned projects
        assigned_projects = Project.objects.filter(manager=manager, status="ASSIGN").order_by('-created_at')[:2]
        # Count total assigned projects that are not completed
        pending_projects = Project.objects.filter(manager=manager, status="ASSIGN").exclude(status='COMPLETED').count()


        # Fetch teams managed by the manager
        teams = Team.objects.filter(manager=manager)
        total_teams = teams.count()

        # Attendance Stats (Manager's own attendance)
        current_year = datetime.now().year
        current_month = datetime.now().month
        total_days_in_month = monthrange(current_year, current_month)[1]

        approved_attendance_records = Attendance.objects.filter(
            employee=manager,
            status='APPROVED',
            login_time__year=current_year,
            login_time__month=current_month
        ).count()

        attendance_percentage = (approved_attendance_records / total_days_in_month) * 100 if total_days_in_month else 0

        # Manager's Leave Stats
        leave_balance = LeaveBalance.objects.filter(user=user).first()
        total_leaves = leave_balance.annual_leave + leave_balance.sick_leave + leave_balance.casual_leave if leave_balance else 0

        # Leaves Taken
        annual_leave_taken = Leave.objects.filter(
            user=user, approval_status="APPROVED", leave_type="ANNUAL LEAVE", from_date__year=current_year
        ).aggregate(total=Sum('no_of_days'))['total'] or 0

        sick_leave_taken = Leave.objects.filter(
            user=user, approval_status="APPROVED", leave_type="SICK LEAVE", from_date__year=current_year
        ).aggregate(total=Sum('no_of_days'))['total'] or 0

        casual_leave_taken = Leave.objects.filter(
            user=user, approval_status="APPROVED", leave_type="CASUAL LEAVE", from_date__year=current_year
        ).aggregate(total=Sum('no_of_days'))['total'] or 0

        leaves_taken = annual_leave_taken + sick_leave_taken + casual_leave_taken

        leave_requests = Leave.objects.filter(user=user, approval_status="PENDING").count()

        # Fetching Workdays
        worked_days = manager.work_days
        absent_days = leaves_taken
        loss_of_pay_days = absent_days - total_leaves if absent_days > total_leaves else 0

        context = {
            'manager': manager,
            'assigned_projects': assigned_projects,
            'total_projects': total_projects,
            'completed_projects': completed_projects,
            'pending_projects': pending_projects,
            'total_teams': total_teams,
            'attendance_percentage': round(attendance_percentage, 1),
            'total_leaves': total_leaves,
            'leaves_taken': leaves_taken,
            'annual_leave_taken': annual_leave_taken,
            'sick_leave_taken': sick_leave_taken,
            'casual_leave_taken': casual_leave_taken,
            'leave_requests': leave_requests,
            'worked_days': worked_days,
            'absent_days': absent_days,
            'loss_of_pay_days': loss_of_pay_days,
            "last_punch_in": last_punch_in,
            "last_punch_out": last_punch_out,
            'user_attendance': today_attendance,
            "current_time": timezone.now().astimezone(local_tz).strftime('%I:%M %p, %d %b %Y')
        }

        return render(request, 'Manager/dashboard.html', context)

    # Employee Dashboard
    else:
        return render(request, 'employee/employee_dashboard.html')
    
    
@login_required
def manager_profile(request):
    # Fetch the manager's employee profile
    manager = get_object_or_404(Employee, user=request.user, is_manager=True)

    # Fetch teams managed by the manager
    teams = Team.objects.filter(manager=manager).prefetch_related('employees')

    # Fetch projects managed by the manager
    projects = Project.objects.filter(manager=manager)

    # Calculate the total number of employees under the manager
    total_employees = sum(team.employees.count() for team in teams)

    # Get the current year and month
    current_year = date.today().year
    current_month = date.today().month

    # Get the total number of days in the current month
    total_days_in_month = monthrange(current_year, current_month)[1]

    # Calculate total attendance records for employees under the manager
    employee_attendance_records = Attendance.objects.filter(
        employee__teams_assigned__manager=manager,
        login_time__year=current_year,
        login_time__month=current_month
    ).count()

    approved_attendance_records = Attendance.objects.filter(
        employee__teams_assigned__manager=manager,
        status='APPROVED',
        login_time__year=current_year,
        login_time__month=current_month
    ).count()

    # Calculate attendance percentage
    attendance_percentage = (
        (approved_attendance_records / total_days_in_month) * 100
        if employee_attendance_records > 0
        else 0
    )

    # Count completed, pending, and assigned projects
    completed_projects = projects.filter(status='COMPLETED').count()
    pending_projects = projects.exclude(status='COMPLETED').count()

    # Prepare data for each project
    managed_projects = [
        {
            "name": project.name,
            "code": project.code,
            "status": project.status,
            "team": project.teams.first(),  # Fetch first assigned team
        }
        for project in projects
    ]

    # Context for the template
    context = {
        "manager": manager,
        "teams": teams,
        "total_employees": total_employees,
        "attendance_percentage": round(attendance_percentage, 1),
        "completed_projects": completed_projects,
        "pending_projects": pending_projects,
        "managed_projects": managed_projects,
    }

    return render(request, 'Manager/manager_profile.html', context)

@login_required
def update_manager_profile(request):
    manager = request.user.employee_profile  # Get the Manager profile

    if not manager.is_manager:
        messages.error(request, "You are not authorized to access this page.")
        return redirect("manager_dashboard")  # Redirect to manager dashboard if unauthorized

    if request.method == "POST":
        form = EmployeeUpdateForm(request.POST, request.FILES, instance=manager)
        if form.is_valid():
            user = request.user
            user.first_name = form.cleaned_data["first_name"]
            user.last_name = form.cleaned_data["last_name"]
            user.email = form.cleaned_data["email"]

            # Save new password if provided
            if form.cleaned_data["password"]:
                user.set_password(form.cleaned_data["password"])

            user.save()

            # Save the manager form (rank & salary included)
            form.save()

            messages.success(request, "Your profile has been updated successfully.")
            return redirect("manager_profile")  # Redirect to manager profile view

    else:
        form = EmployeeUpdateForm(instance=manager, initial={
            "first_name": request.user.first_name,
            "last_name": request.user.last_name,
            "email": request.user.email,
        })

    return render(request, "Manager/manager_profile_update.html", {"form": form})

@login_required
def update_project_status(request, project_id):
    if request.method == "POST":
        project = get_object_or_404(Project, id=project_id)
        new_status = request.POST.get("status")
        if new_status in dict(Project.STATUS_CHOICES):
            project.status = new_status
            project.save()
            messages.success(
                request, f"Status for project '{project.name}' updated to {project.get_status_display()}.")
        else:
            messages.error(request, "Invalid status selected.")
    return redirect("manager-dashboard")  # Adjust the redirect as needed

# List all teams
@login_required
def team_list(request):
    search_query = request.GET.get('search', '').strip()  # Get the search input

    # Filter teams by name based on search input
    if search_query:
        teams = Team.objects.filter(name__icontains=search_query)
    else:
        teams = Team.objects.all().order_by('-project__created_at')

    # Pagination (10 teams per page)
    paginator = Paginator(teams, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'team/team_list.html', {'teams': page_obj, 'search_query': search_query})

# Show details of a team
@login_required
def team_detail(request, pk):
    team = get_object_or_404(Team, pk=pk)
    return render(request, 'team/team_detail.html', {'team': team})

# Create a new team
@login_required
def team_create(request):
    if request.method == 'POST':
        form = TeamForm(request.POST, user=request.user)  # Pass the logged-in user
        if form.is_valid():
            form.save()
            return redirect('team-list')
    else:
        form = TeamForm(user=request.user)

    return render(request, 'team/add_team.html', {'form': form})

# Update an existing team
@login_required
def team_update(request, pk):
    team = get_object_or_404(Team, pk=pk)
    if request.method == 'POST':
        form = TeamForm(request.POST, instance=team)
        if form.is_valid():
            form.save()
            # Redirect to the updated team's detail page
            return redirect('team-list')
    else:
        form = TeamForm(instance=team)
    return render(request, 'team/update_team.html', {'form': form})

# Delete an existing team
@login_required
def team_delete(request, team_id):
    team = get_object_or_404(Team, id=team_id)
    team.delete()
    return redirect('team-list')

@login_required
def manage_attendance_requests(request):
    if not request.user.is_staff:  # Ensure the user is a manager
        return redirect('dashboard')

    pending_requests = Attendance.objects.filter(status='PENDING')

    if request.method == 'POST':
        attendance_id = request.POST.get('attendance_id')
        action = request.POST.get('action')  # APPROVE or REJECT
        # Get the rejection reason if provided
        rejection_reason = request.POST.get('rejection_reason', '')

        if not action:  # Handle case where 'action' is None or missing
            messages.error(
                request, "Action (Approve/Reject) must be selected!")
            return redirect('manage_attendance_requests')

        try:
            attendance = Attendance.objects.get(id=attendance_id)
            if action == 'APPROVE':
                attendance.status = 'APPROVED'
                # Mark as present by default
                attendance.attendance_status = attendance.attendance_status
            elif action == 'REJECT':
                attendance.status = 'REJECTED'
                attendance.attendance_status = 'LEAVE'
                attendance.rejection_reason = rejection_reason  # Store the rejection reason
                attendance.rejected_by = request.user
            attendance.save()
            messages.success(
                request, f"Attendance {action.lower()}ed successfully!")
        except Attendance.DoesNotExist:
            messages.error(request, "Attendance record not found!")

    return render(request, 'Manager/manage_attendance_requests.html', {'requests': pending_requests})

@login_required
def project_assignment_create(request):
    if request.method == 'POST':
        form = ProjectAssignmentForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('manager-dashboard')
    else:
        form = ProjectAssignmentForm()
    return render(request, 'Manager/project_assignment_form.html', {'form': form})

# Employee List
@login_required
def manager_employee_list(request):
    if request.user.is_staff and not request.user.is_superuser:
        # Fetch the logged-in manager
        manager = Employee.objects.filter(
            user=request.user, is_manager=True).first()
        if not manager:
            return render(request, 'error.html', {'message': 'You are not authorized to view this page.'})

        # Fetch employees associated with the manager's teams
        teams = Team.objects.filter(manager=manager).prefetch_related(
            Prefetch(
                'employees',  # Prefetch employees related to the manager's teams
                queryset=Employee.objects.filter(is_employee=True).select_related('user').prefetch_related(
                    Prefetch(
                        # Prefetch the project status (TeamMemberStatus) related to the employee
                        'project_statuses',
                        queryset=TeamMemberStatus.objects.filter(
                            status='ONGOING').select_related('team__project'),
                        to_attr='ongoing_projects'  # Store the result as 'ongoing_projects'
                    )
                ),
                to_attr='team_employees'  # Store the result as 'team_employees'
            )
        )

        # Flatten the list of employees across all teams
        employees = []
        for team in teams:
            employees.extend(team.team_employees)

        # Remove duplicates (if employees are in multiple teams)
        employees = list(set(employees))

        # Paginate the employees
        paginator = Paginator(employees, 10)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        return render(request, 'Manager/employee_list.html', {'page_obj': page_obj, 'teams': teams})
    else:
        # Redirect non-staff users or superusers
        return render(request, 'error.html', {'message': 'Access denied.'})

# Employee attendance list
@login_required
def attendance_list(request):
    search_query = request.GET.get('search', '')

    # Get the current manager
    try:
        # Access the related Employee object
        current_manager = request.user.employee_profile
    except Employee.DoesNotExist:
        # Handle the case where the user doesn't have an associated Employee profile
        # Redirect to a page that shows an error or a 'no profile' message
        return redirect('no_employee_profile')

    # Ensure the logged-in user is a manager
    if not current_manager.is_manager:
        # If the user is not a manager, redirect to an error page
        return redirect('no_permission_page')

    # Fetch teams managed by the current manager and prefetch employees and their attendance
    teams = Team.objects.filter(manager=current_manager).prefetch_related(
        Prefetch(
            'employees',
            queryset=Employee.objects.filter(is_employee=True).select_related('user').prefetch_related(
                'teams_assigned',  # Fetch teams directly
                Prefetch(
                    'attendance_set',  
                    queryset=Attendance.objects.select_related('employee', 'project').order_by('-login_time'),
                    to_attr='attendance_records'
                )
            ),
            to_attr='team_employees'
        )
    )


    # Flatten the list of employees across all teams
    employees = []
    for team in teams:
        employees.extend(team.team_employees)

    # Remove duplicates (if employees are in multiple teams)
    employees = list(set(employees))

    # Combine all attendance records into a single queryset-like list
    attendance_records = []
    for employee in employees:
        attendance_records.extend(employee.attendance_records)

    # Apply search filtering
    if search_query:
        attendance_records = [
            record for record in attendance_records
            if search_query.lower() in record.employee.user.username.lower()
        ]

    # Sort the records by login time (descending)
    attendance_records = sorted(
        attendance_records, key=lambda x: x.login_time, reverse=True)

    # Paginate the attendance records
    paginator = Paginator(attendance_records, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Render the template with the filtered and paginated attendance records
    context = {
        'attendance_records': page_obj,
        'search_query': search_query,
    }
    return render(request, 'Manager/attendance_list.html', context)

@login_required
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
    return render(request, 'Manager/attendance.html', context)

# Employee attendance details
@login_required
def attendance_detail(request, pk):
    attendance = get_object_or_404(Attendance, pk=pk)
    return render(request, 'Manager/attendance_detail.html', {'attendance': attendance})

# Manage attendance
@login_required
def manage_attendance(request):
    # Get the current manager
    try:
        # Access the related Employee object
        current_manager = request.user.employee_profile
    except Employee.DoesNotExist:
        # Handle the case where the user doesn't have an associated Employee profile
        # Redirect to a page that shows an error or a 'no profile' message
        return redirect('no_employee_profile')

    # Ensure the logged-in user is a manager
    if not current_manager.is_manager:
        # If the user is not a manager, redirect to an error page
        return redirect('no_permission_page')

    # Fetch teams managed by the current manager and prefetch employees
    teams = Team.objects.filter(manager=current_manager).prefetch_related(
        Prefetch(
            'employees',
            queryset=Employee.objects.filter(
                is_employee=True).select_related('user'),
            to_attr='team_employees'  # Store the result as 'team_employees'
        )
    )

    # Flatten the list of employees across all teams
    employees = []
    for team in teams:
        employees.extend(team.team_employees)

    # Remove duplicates (if employees are in multiple teams)
    employees = list(set(employees))

    # Fetch pending attendance requests for employees under the manager's teams
    pending_requests = Attendance.objects.filter(
        employee__in=employees,
        status='PENDING'
    ).select_related('employee', 'employee__user')  # Prefetch related fields for performance optimization

    if request.method == 'POST':
        # Get form data from the POST request
        attendance_id = request.POST.get('attendance_id')
        action = request.POST.get('action')  # APPROVE or REJECT
        rejection_reason = request.POST.get('rejection_reason', '')

        if not action:  # Handle case where 'action' is None or missing
            messages.error(
                request, "Action (Approve/Reject) must be selected!")
            return redirect('manage_attendance')

        try:
            # Find the attendance record
            attendance = Attendance.objects.get(id=attendance_id)

            if attendance.employee not in employees:
                messages.error(
                    request, "You are not authorized to manage this attendance record!")
                return redirect('manage_attendance')

            if action == 'APPROVE':
                attendance.status = 'APPROVED'
                attendance.attendance_status = 'PRESENT'
                messages.success(request, "Attendance approved successfully!")
            elif action == 'REJECT':
                attendance.status = 'REJECTED'
                attendance.attendance_status = 'LEAVE'
                attendance.rejection_reason = rejection_reason  # Store the rejection reason
                attendance.rejected_by = request.user
                messages.success(request, "Attendance rejected successfully!")

            attendance.save()

        except Attendance.DoesNotExist:
            messages.error(request, "Attendance record not found!")

    return render(request, 'Manager/manage_attendance.html', {'requests': pending_requests})

# Manage Leave
@login_required
def manage_leave(request):
    # Get the current manager
    try:
        current_manager = request.user.employee_profile
    except Employee.DoesNotExist:
        return redirect('no_employee_profile')

    # Ensure the logged-in user is a manager
    if not current_manager.is_manager:
        return redirect('no_permission_page')

    # Fetch teams managed by the current manager and their employees
    teams = current_manager.managed_teams.prefetch_related('employees')
    employees = set()
    for team in teams:
        employees.update(team.employees.all())

    # Fetch pending leave requests for employees under the manager's teams
    pending_leaves = Leave.objects.filter(user__employee_profile__in=employees, approval_status='PENDING')

    # Pagination (10 leave requests per page)
    paginator = Paginator(pending_leaves, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    if request.method == 'POST':
        leave_id = request.POST.get('leave_id')
        action = request.POST.get('action')  # APPROVE or REJECT
        rejection_reason = request.POST.get('rejection_reason', '')

        if not action:
            messages.error(request, "Action (Approve/Reject) must be selected!")
            return redirect('manage_leave')

        try:
            leave_request = Leave.objects.get(id=leave_id)

            if leave_request.user.employee_profile not in employees:
                messages.error(request, "You are not authorized to manage this leave request!")
                return redirect('manage_leave')

            if action == 'APPROVE':
                leave_request.approval_status = 'APPROVED'
                messages.success(request, "Leave request approved successfully!")
            elif action == 'REJECT':
                leave_request.approval_status = 'REJECTED'
                leave_request.reason += f"\nRejection Reason: {rejection_reason}"  # Store rejection reason
                messages.success(request, "Leave request rejected successfully!")

            leave_request.save()

        except Leave.DoesNotExist:
            messages.error(request, "Leave request not found!")

    return render(request, 'Manager/manage_leave.html', {'leaves': page_obj})

# Leave list
@login_required
def leave_list(request):
    search_query = request.GET.get('search', '').strip()

    # Get the current manager
    try:
        current_manager = request.user.employee_profile
    except Employee.DoesNotExist:
        return redirect('no_employee_profile')

    # Ensure the logged-in user is a manager
    if not current_manager.is_manager:
        return redirect('no_permission_page')

    # Fetch teams managed by the current manager
    teams = Team.objects.filter(manager=current_manager).prefetch_related('employees')
    
    # Collect employees from managed teams
    employees = set()
    for team in teams:
        employees.update(team.employees.all())

    # Fetch leave records for employees under the manager
    leave_records = Leave.objects.filter(user__employee_profile__in=employees).select_related('user')

    # Apply search filter
    if search_query:
        leave_records = leave_records.filter(user__username__icontains=search_query)

    # Sort leave records by date (latest first)
    leave_records = leave_records.order_by('-from_date')

    # Paginate leave records (10 per page)
    paginator = Paginator(leave_records, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'leave_records': page_obj,
        'search_query': search_query,
    }

    return render(request, 'Manager/leave_list.html', context)


# Employee profile
@login_required
def employee_profile(request, employee_id):
    # Fetch the employee
    employee = get_object_or_404(Employee, pk=employee_id)
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

    return render(request, 'Manager/employee_profile.html', context)


@login_required
def project_list_view(request):
    current_user = request.user
    employee = current_user.employee_profile

    # Get search query
    search_query = request.GET.get('search', '').strip()

    # Filter projects only if the user is a manager
    if employee.is_manager:
        projects = Project.objects.filter(manager=employee).order_by('-created_at')

        # Apply search filter
        if search_query:
            projects = projects.filter(
                Q(name__icontains=search_query) |
                Q(code__icontains=search_query) |
                Q(client_name__icontains=search_query)
            )

        # Convert to dictionary values for efficiency
        projects = projects.values(
            "id", "name", "category", "code", "status",
            "invoice_amount", "currency_code", "purchase_and_expenses"
        )
    else:
        projects = []

    # Pagination (10 projects per page)
    paginator = Paginator(projects, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        "projects": page_obj,
        "search_query": search_query,  # Send search query back to template
    }

    return render(request, 'Manager/project_list.html', context)

# project summary
@login_required 
def project_summary_view(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    employee = get_object_or_404(Employee, user=request.user)

    # Fetch logs from team members
    team_logs = ActivityLog.objects.filter(team_member_status__team__project=project)
    
    # Fetch logs where the manager updated the project status
    manager_logs = ActivityLog.objects.filter(project=project)
    
    client_name = project.client_name
    # Merge both logs and order by `changed_at`
    logs = (team_logs | manager_logs).order_by('-changed_at')

    # Handle File Upload
    if request.method == "POST" and request.FILES.get("attachment"):
        project.attachment = request.FILES["attachment"]
        project.save()

    statuses = TeamMemberStatus.objects.filter(team__project=project).order_by('-last_updated')
    status_choices = TeamMemberStatus.STATUS_CHOICES
    teams = project.teams.all()

    engineers = []
    engineer_salaries = {}
    total_engineer_salary = 0
    engineer_project_hours = {}

    for team in teams:
        for engineer in team.employees.all():
            engineers.append(engineer.user.username)
            engineer_salaries[engineer.user.username] = engineer.salary
            total_engineer_salary += engineer.salary

            attendance_records = Attendance.objects.filter(employee=engineer, project=project)
            total_hours = sum(record.total_hours_of_work or 0 for record in attendance_records)
            engineer_project_hours[engineer.user.username] = round(total_hours, 2)

    total_expenses = project.calculate_expenses()
    profit = project.calculate_profit()
    work_days = project.calculate_total_work_days()

    project_data = {
        "project_name": project.name,
        "client_name": project.client_name,
        "project_manager": project.manager,
        "code": project.code,
        "category": project.category,
        "purchase_and_expenses": project.purchase_and_expenses,
        "invoice_amount": project.invoice_amount,
        "engineers": engineers,
        "engineer_salaries": engineer_salaries,
        "engineer_project_hours": engineer_project_hours,
        "total_work_days": work_days,
        "currency_code": project.currency_code,
        "total_expenses": round(total_expenses, 2),
        "profit": profit,
        "status": project.status,
        "total_engineer_salary": round(total_engineer_salary, 2),
        "project_create": project.created_at,
        "deadline_date": project.deadline_date,
        "statuses": statuses,
        "logs": logs,  # Now includes both manager & team member logs
        "project_id": project.id,
        "status_choices": status_choices,
        "attachment_url": project.attachment.url if project.attachment else None,   
    }

    context = {"project_data": project_data}
    return render(request, 'Manager/project.html', context)


@login_required
def update_team_manager_status(request, project_id):
    if request.method == "POST":
        employee = get_object_or_404(Employee, user=request.user)
        status = request.POST.get("status")
        remark = request.POST.get("remark", "")

        # Fetch the TeamMemberStatus entry
        team_member_status = Project.objects.filter(
            id=project_id,
            manager=employee
        ).first()

        if team_member_status:
            team_member_status.status = status
            team_member_status.notes = remark
            team_member_status.save()

            messages.success(request, "Status updated successfully.")
        else:
            messages.error(request, "No team member status found for this project.")

    return redirect('project-summary-view', project_id=project_id)


# Edit attendance
@login_required
def manager_edit_attendance(request, attendance_id):
    attendance = get_object_or_404(Attendance, id=attendance_id)

    if request.method == 'POST':
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

        # Ensure the employee_id is set
        employee_id = request.POST.get('employee')
        if not employee_id:
            # Handle missing employee_id, e.g., raise an error or set a default
            messages.error(request, "Employee is required.")
            return redirect('manager_edit_attendance', attendance_id=attendance.id)

        attendance.employee_id = employee_id  # Ensure employee_id is provided
        attendance.project_id = request.POST.get('project')
        attendance.location = request.POST.get('location')
        attendance.attendance_status = request.POST.get('attendance_status')
        attendance.status = request.POST.get('status', 'default_status')  # Default value for status if not provided
        attendance.rejection_reason = request.POST.get('rejection_reason')

        attendance.save()

        messages.success(request, 'Attendance record has been updated successfully.')
        return redirect('attendance_list')

    employees = Employee.objects.all()
    projects = Project.objects.all()
    return render(request, 'Manager/manager_edit_attendance.html', {
        'attendance': attendance,
        'employees': employees,
        'projects': projects,
    })

def manager_update_travel_time(request, attendance_id):
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
            return redirect('manager_edit_attendance', attendance_id=attendance.id)

        attendance.employee_id = employee_id  # Ensure employee_id is provided
        # Exclude project, attendance_status, and status from form submission
        attendance.rejection_reason = request.POST.get('rejection_reason')
        
        # Don't update the "project", "attendance_status", and "status" fields

        attendance.save()

        messages.success(request, 'Travel time has been updated successfully.')
        return redirect('attendance_status')

    employees = Employee.objects.all()
    projects = Project.objects.all()
    return render(request, 'Manager/update_travel_time.html', {
        'attendance': attendance,
        'employees': employees,
        'projects': projects,
    })

# Delete attendance
@login_required
def manager_delete_attendance(request, attendance_id):
    attendance = get_object_or_404(Attendance, id=attendance_id)
    attendance.delete()
    # Add a success message
    messages.success(request, 'Attendance record has been deleted successfully.')
    return redirect('attendance_list')

@login_required
def manager_apply_leave(request):
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
            return redirect('manager_leave_status')  # Redirect after success
    else:
        form = LeaveForm()

    return render(request, 'Manager/leave.html', {'form': form})

@login_required
def manager_upload_medical_certificate(request, leave_id):
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

            return redirect('manager_leave_status')

    return redirect('manager_leave_status')  # Redirect if GET request or no file uploaded

@login_required
def manager_leave_status(request):
    """View to display the leave status of the logged-in user."""
    leaves_list = Leave.objects.filter(user=request.user).order_by("-from_date")
    
    # Paginate leave requests (10 per page)
    paginator = Paginator(leaves_list, 10)  
    page_number = request.GET.get("page")
    leaves = paginator.get_page(page_number)

    return render(request, "Manager/leavestatus.html", {"leaves": leaves})

@login_required
def manager_leave_records(request):
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

    return render(request, "Manager/leaverecords.html", {"leave_summary": leave_summary})


@login_required
def manager_log_in(request):
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

        return redirect("attendance_status")

    return manager_render_attendance_page(request)

@login_required
def manager_log_off(request, attendance_id):
    if request.method == "POST":
        # Get the Employee object associated with the logged-in user
        try:
            employee = get_object_or_404(Employee, user=request.user)
        except AttributeError:
            messages.error(request, "Your user is not associated with an employee record.")
            return redirect("manager_attendance_dashboard")
        
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

    return redirect("manager-dashboard")  # Redirect to the appropriate page

@login_required
def manager_render_attendance_page(request):
    employee = get_object_or_404(Employee, user=request.user)
    user_attendance = Attendance.objects.filter(employee=employee, log_out_time__isnull=True).first()

    attendance_status_choices = Attendance.ATTENDANCE_STATUS
    location_choices = Attendance.LOCATION_CHOICES
    projects = Project.objects.all()

    return render(request, "Manager/punchin.html", {
        "attendance_status_choices": attendance_status_choices,
        "location_choices": location_choices,
        "projects": projects,
        "user_attendance": user_attendance,
    })


@login_required
def manager_fetch_notifications(request):
    notifications = Notification.objects.filter(recipient=request.user, is_read=False).values("id", "message", "created_at")
    return JsonResponse({"notifications": list(notifications)})

@login_required
def manager_mark_notifications_as_read(request):
    """Mark all unread notifications for the logged-in manager as read."""
    notifications = Notification.objects.filter(recipient=request.user, is_read=False)
    
    if notifications.exists():
        notifications.update(is_read=True)  # Bulk update for efficiency
    
    return JsonResponse({"message": "Notifications marked as read"})
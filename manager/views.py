from django.shortcuts import redirect, render, get_object_or_404
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from calendar import monthrange
from datetime import date
from django.db.models import Q, F
from django.db.models import Prefetch
from django.core.paginator import Paginator
from Admin.forms import ProjectAssignmentForm
from employee_data.forms import EmployeeUpdateForm
from .forms import TeamForm
from django.contrib import messages
from Admin.models import Attendance, Project, Team, TeamMemberStatus, Employee, ActivityLog, Leave


# Home
def dashboard(request):
    if request.user.is_superuser:  # For admin users
        return render(request, 'Admin/dashboard.html')
    elif request.user.is_staff:  # For staff/manager users
        assigned_projects = Project.objects.filter(manager__user=request.user)
        return render(request, 'Manager/dashboard.html', {'assigned_projects': assigned_projects})
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
        teams = Team.objects.all()

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

# Employee attendance details
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
        projects = Project.objects.filter(manager=employee)

        # Apply search filter
        if search_query:
            projects = projects.filter(
                name__icontains=search_query
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
    print('team_logs:', team_logs)
    # Fetch logs where the manager updated the project status
    manager_logs = ActivityLog.objects.filter(project=project)
    print('manager LOGS:', manager_logs)

    # Merge both logs and order by `changed_at`
    logs = (team_logs | manager_logs).order_by('-changed_at')

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
        attendance.employee_id = request.POST.get('employee')
        attendance.project_id = request.POST.get('project')
        attendance.login_time = request.POST.get('login_time')
        attendance.log_out_time = request.POST.get('log_out_time')
        attendance.location = request.POST.get('location')
        attendance.attendance_status = request.POST.get('attendance_status')
        attendance.status = request.POST.get('status')
        attendance.rejection_reason = request.POST.get('rejection_reason')
        attendance.travel_in_time = request.POST.get('travel_in_time')
        attendance.travel_out_time = request.POST.get('travel_out_time')
        attendance.save()

        messages.success(request, 'Attendance record has been Updated successfully.')
        return redirect('attendance_list_view')

    employees = Employee.objects.all()
    projects = Project.objects.all()
    return render(request, 'manager/manager_edit_attendance.html', {
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
from django.shortcuts import redirect, render, get_object_or_404
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from calendar import monthrange
from datetime import date
from django.db.models import Q, F
from django.db.models import Prefetch
from django.core.paginator import Paginator
from Admin.forms import ProjectAssignmentForm
from .forms import TeamForm
from django.contrib import messages
from Admin.models import Attendance, Project, Team, TeamMemberStatus, Employee


# Home
def dashboard(request):
    if request.user.is_superuser:  # For admin users
        return render(request, 'Admin/dashboard.html')
    elif request.user.is_staff:  # For staff/manager users
        assigned_projects = Project.objects.filter(manager__user=request.user)
        return render(request, 'Manager/dashboard.html', {'assigned_projects': assigned_projects})
    else:
        return render(request, 'employee/employee_dashboard.html')

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
    teams = Team.objects.all()  # Or filter based on some condition if needed
    return render(request, 'team/team_list.html', {'teams': teams})

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
            'employees',  # Prefetch employees related to the manager's teams
            queryset=Employee.objects.filter(is_employee=True).select_related('user').prefetch_related(
                Prefetch(
                    'attendance_set',  # Prefetch attendance records for each employee
                    queryset=Attendance.objects.select_related(
                        'employee', 'project').order_by('-login_time'),
                    to_attr='attendance_records'  # Store the result as 'attendance_records'
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
    # Get the currently logged-in user
    current_user = request.user

    # Retrieve the related employee object
    employee = current_user.employee_profile

    # Check if the user is a manager
    if employee.is_manager:
        # Fetch all projects managed by the employee
        projects = Project.objects.filter(manager=employee)

        # Prepare the project data
        project_data = [
            {
                "id": project.id,
                "name": project.name,
                "category": project.category,
                "code": project.code,
                "status": project.status,
                "invoice": project.invoice_amount,
                "currency": project.currency_code,
                "purchase_and_expenses": project.purchase_and_expenses,
            }
            for project in projects
        ]
    else:
        # If not a manager, set project_data to an empty list or an appropriate message
        project_data = []

    context = {
        "projects": project_data,
    }

    return render(request, 'Manager/project_list.html', context)

# project summary
@login_required
def project_summary_view(request, project_id):
    # Get the specific project by ID
    project = get_object_or_404(Project, id=project_id)
    statuses = TeamMemberStatus.objects.filter(team__project__id=project_id).order_by('-last_updated')

    # Fetch teams associated with the project
    teams = project.teams.all()

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
    }

    # Pass the data to the template
    context = {
        "project_data": project_data
    }

    return render(request, 'Manager/project.html', context)

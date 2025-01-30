from django.shortcuts import redirect, render
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from calendar import monthrange
from datetime import date
from django.db.models import Q, F
from django.db.models import Sum
from django.db.models import Prefetch
from django.http import HttpResponse
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.auth.forms import UserCreationForm
from .forms import EmployeeCreationForm, ProjectForm, ProjectAssignmentForm
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from .models import Attendance, Employee, ProjectAssignment, Project, TeamMemberStatus


# Home
def dashboard(request):
    if request.user.is_superuser:  # For admin users
        total_projects = Project.objects.count()
        active_employees = Employee.objects.filter(user__is_active=True).count()
        total_revenue = Project.objects.aggregate(Sum('invoice_amount'))['invoice_amount__sum'] or 0
        pending_invoices = Project.objects.filter(invoice_amount=0).count()

        # Fetching project details with manager name
        project_details = Project.objects.annotate(
            leader_name=F('manager__user__username')  # Adjust field if necessary
        ).values('name', 'leader_name', 'status')

        context = {
            'total_projects': total_projects,
            'active_employees': active_employees,
            'total_revenue': total_revenue,
            'pending_invoices': pending_invoices,
            'project_details': project_details,
        }
        return render(request, 'Admin/dashboard.html', context)
    elif request.user.is_staff:  # For staff/manager users
        return render(request, 'Manager/dashboard.html')
    else:  # For other users (if applicable)
        return redirect('home')

# create employee
@login_required
def create_employee(request):
    if not request.user.is_staff:  # Restrict to admin or staff users
        return redirect('dashboard')  # Redirect to the admin homepage

    if request.method == 'POST':
        form = EmployeeCreationForm(request.POST)
        if form.is_valid():
            try:
                # Create the user
                user = form.save(commit=False)
                user.set_password(form.cleaned_data['password'])
                user.save()
                
                # Get the form data for Employee
                is_employee = form.cleaned_data.get('is_employee')
                is_manager = form.cleaned_data.get('is_manager')
                rank = form.cleaned_data.get('rank')
                salary = form.cleaned_data.get('salary')

                # Ensure either is_employee or is_manager is selected (not both)
                if is_employee and not is_manager:
                    user.is_employee = True
                elif is_manager and not is_employee:
                    user.is_manager = True
                    user.is_staff = True  # Make manager a staff member
                else:
                    user.is_employee = False
                    user.is_manager = False

                user.save()  # Save user changes

                # Create employee profile
                employee = Employee.objects.create(
                    user=user,
                    rank=rank,
                    salary=salary,
                    is_employee=is_employee,
                    is_manager=is_manager
                )

                messages.success(request, 'Employee created successfully!')
                return redirect('create-employee')
            except Exception as e:
                messages.error(request, 'There was an error creating the employee.')
        else:
            # If the form is not valid, print errors
            print(form.errors)

    else:
        form = EmployeeCreationForm()

    return render(request, 'Admin/create_employee.html', {'form': form})

# employee list
@login_required
def employee_list(request):
    if not request.user.is_staff:
        return redirect('login')

    # Filter ongoing project assignments
    ongoing_project_assignments = TeamMemberStatus.objects.filter(
        status='ONGOING'
    ).select_related('team__project')

    # Exclude managers and prefetch ongoing project assignments
    employees = Employee.objects.exclude(is_manager=True).select_related('user').prefetch_related(
        Prefetch(
            'project_statuses',  # This matches the `related_name` in TeamMemberStatus
            queryset=ongoing_project_assignments,
            to_attr='assigned_projects'  # Prefetch results stored as 'assigned_projects'
        )
    )

    paginator = Paginator(employees, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'Admin/employee_list.html', {'page_obj': page_obj})

@login_required
def manager_list(request):
    if not request.user.is_staff:
        return redirect('login')

    # Filter projects with status 'ONGOING' and a manager assigned
    ongoing_project_assignments = Project.objects.filter(
        status='ONGOING'
    ).select_related('manager')

    # Fetch managers with their assigned ongoing projects
    managers = Employee.objects.filter(is_manager=True).select_related('user').prefetch_related(
        Prefetch(
            'managed_projects',  # Related name from the 'manager' ForeignKey in Project
            queryset=ongoing_project_assignments,
            to_attr='assigned_projects'  # Store prefetch results in 'assigned_projects'
        )
    )

    # Debugging output
    for manager in managers:
        print(f"Manager: {manager.user.get_full_name()}, Assigned Projects: {manager.assigned_projects}")

    # Paginate the manager list
    paginator = Paginator(managers, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'Admin/manager_list.html', {'page_obj': page_obj})

# edit employee
@login_required
def edit_employee(request, employee_id):
    if not request.user.is_staff:  # Restrict to admin or staff users
        return redirect('login')

    employee = get_object_or_404(Employee, id=employee_id)
    if request.method == 'POST':
        form = EmployeeCreationForm(request.POST, instance=employee)
        if form.is_valid():
            form.save()
            messages.success(request, 'Employee updated successfully!')
            # Redirect to a list of employees or dashboard
            return redirect('employee-list')
    else:
        form = EmployeeCreationForm(instance=employee)

    return render(request, 'admin/edit_employee.html', {'form': form, 'employee': employee})

# delete employee
@login_required
def delete_employee(request, employee_id):
    if not request.user.is_staff:  # Restrict to admin or staff users
        return redirect('login')

    employee = get_object_or_404(Employee, id=employee_id)
    if request.method == 'POST':
        employee.delete()
        messages.success(request, 'Employee deleted successfully!')
        # Redirect to a list of employees or dashboard
        return redirect('employee-list')

    return render(request, 'admin/confirm_delete_employee.html', {'employee': employee})

# attendance list
@login_required
def attendance_list_view(request):
    search_query = request.GET.get('search', '')
    attendance_records = Attendance.objects.select_related('employee')

    if search_query:
        attendance_records = attendance_records.filter(
            employee__user__username__icontains=search_query
        ).order_by('-login_time')

    paginator = Paginator(attendance_records, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'attendance_records': page_obj,
        'search_query': search_query,
    }
    return render(request, 'Admin/attendance_list.html', context)

def attendance_detail(request, pk):
    attendance = get_object_or_404(Attendance, pk=pk)
    return render(request, 'Admin/attendance_detail.html', {'attendance': attendance})

# Manage attendance
def manage_attendance(request):
    # Ensure the user is a superuser (or manager)
    if not request.user.is_superuser:
        return redirect('admin-dashboard')

    pending_requests = Attendance.objects.filter(status='PENDING')

    if request.method == 'POST':
        # Get form data from the POST request
        attendance_id = request.POST.get('attendance_id')
        action = request.POST.get('action')  # APPROVE or REJECT
        # Get rejection reason if provided
        rejection_reason = request.POST.get('rejection_reason', '')

        if not action:  # Handle case where 'action' is None or missing
            messages.error(
                request, "Action (Approve/Reject) must be selected!")
            return redirect('manage_attendance')

        try:
            # Find the attendance record
            attendance = Attendance.objects.get(id=attendance_id)

            if action == 'APPROVE':
                attendance.status = 'APPROVED'
                # or whatever the approved status should be
                attendance.attendance_status = 'PRESENT'
                messages.success(request, f"Attendance approved successfully!")
            elif action == 'REJECT':
                attendance.status = 'REJECTED'
                # Adjust based on the actual status for rejection
                attendance.attendance_status = 'LEAVE'
                attendance.rejection_reason = rejection_reason  # Store the rejection reason
                attendance.rejected_by = request.user
                messages.success(request, f"Attendance rejected successfully!")

            attendance.save()

        except Attendance.DoesNotExist:
            messages.error(request, "Attendance record not found!")

    return render(request, 'Admin/manage_attendance.html', {'requests': pending_requests})

# Edit attendance
@login_required
def edit_attendance(request, attendance_id):
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
    return render(request, 'Admin/edit_attendance.html', {
        'attendance': attendance,
        'employees': employees,
        'projects': projects,
    })

# Delete attendance
@login_required
def delete_attendance(request, attendance_id):
    attendance = get_object_or_404(Attendance, id=attendance_id)
    attendance.delete()
    # Add a success message
    messages.success(request, 'Attendance record has been deleted successfully.')
    return redirect('attendance_list_view')

# project list view
@login_required
def project_list_view(request):
    # Query all projects
    projects = Project.objects.all()

    # Prepare project data
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

    form = ProjectForm()

    # Pass the data to the template
    context = {
        "projects": project_data,
        'form': form,
    }

    return render(request, 'Admin/project_list.html', context)

# project summary
@login_required
def project_summary_view(request, project_id):
    # Get the specific project by ID
    project = get_object_or_404(Project, id=project_id)

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

    # Calculate total work hours from all teams (as a whole project)
    # total_work_hours = sum(
    #     (team.time_stop - team.time_start).total_seconds() / 3600
    #     for team in teams
    # )

    # Calculate total project duration (Total Project Hours)
    # if teams.exists():
    #     project_start = min(
    #         team.time_start for team in teams)
    #     project_end = max(team.time_stop for team in teams)
    #     # Total duration in hours
    #     total_project_hours = (
    #         project_end - project_start).total_seconds() / 3600
    # else:
    #     total_project_hours = 0

    # Calculate total expenses (will call the method which includes hours worked)
    total_expenses = project.calculate_expenses()

    # Calculate profit
    profit = project.calculate_profit()

    work_days = project.calculate_total_work_days()

    # Prepare data for the project
    project_data = {
        "project_name": project.name,
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
    }

    # Pass the data to the template
    context = {
        "project_data": project_data
    }

    return render(request, 'Admin/project.html', context)

# add project
@login_required
def add_project(request):
    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            form.save()
            return JsonResponse({'success': True})
        else:
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)
    return render(request, 'Admin/project_list.html', {'form': form})

@login_required
def edit_project(request, project_id):
    project = get_object_or_404(Project, id=project_id)  # Fetch the project or return 404

    if request.method == 'POST':
        form = ProjectForm(request.POST, instance=project)  # Bind form with existing project
        if form.is_valid():
            form.save()
            return redirect('project-list')
        else:
            form = ProjectForm(instance=project)

    # For GET request, render the form pre-filled with the project's data
    return render(request, 'Admin/project_edit.html', {'form': ProjectForm(instance=project)})

@login_required
def delete_project(request, project_id):
    project = get_object_or_404(Project, id=project_id) 
    project.delete() 
    return redirect('project-list')

# project_assignment_list
@login_required
def project_assignment_list(request):
    assignments = ProjectAssignment.objects.all()

    # Calculate total time for each assignment
    for assignment in assignments:
        # Calculate time difference
        total_time = assignment.time_stop - assignment.time_start
        assignment.total_time = total_time

    projects = Project.objects.filter(status='PENDING')
    employees = Employee.objects.all()

    return render(request, 'Admin/project_assignment_list.html', {'assignments': assignments, 'projects': projects,'employees': employees})

# project_assignment_create
@login_required
def project_assignment_create(request):
    if request.method == 'POST':
        form = ProjectAssignmentForm(request.POST)
        if form.is_valid():
            form.save()
            # Update with your actual URL
            return redirect('project-assignment-list')
    else:
        form = ProjectAssignmentForm()

    return render(request, 'Admin/project_assignment_list.html', {'form': form})

# project_assignment_update
@login_required
def project_assignment_update(request, pk):
    assignment = get_object_or_404(ProjectAssignment, pk=pk)
    if request.method == 'POST':
        form = ProjectAssignmentForm(request.POST, instance=assignment)
        if form.is_valid():
            form.save()
            return redirect('project-assignment-list')
    else:
        form = ProjectAssignmentForm(instance=assignment)
    return render(request, 'Admin/project_assignment_form.html', {'form': form})

# project_assignment_delete
@login_required
def project_assignment_delete(request, pk):
    assignment = get_object_or_404(ProjectAssignment, pk=pk)
    assignment.delete()
    return redirect('project-assignment-list')

@login_required
def employee_profile(request, employee_id):
    # Fetch the employee
    employee = get_object_or_404(Employee, pk=employee_id)
    teams = employee.teams_assigned.all()

    team_member_statuses = TeamMemberStatus.objects.filter(employee=employee)

    # Count statuses for the employee's projects
    completed_projects = team_member_statuses.filter(status='COMPLETED').count()
    pending_projects = team_member_statuses.exclude(Q(status='COMPLETED') | Q(status='ONGOING')).count()   
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

    return render(request, 'Admin/employee_profile.html', context)

@login_required
def manager_profile(request, manager_id):
    employee = get_object_or_404(Employee, pk=manager_id)

    if employee.is_manager:
        # Fetch projects managed by the manager
        all_projects = Project.objects.filter(manager=employee).select_related('manager')
    else:
        pass

    total_attendance_records = Attendance.objects.filter(employee=employee).count()
    approved_attendance_records = Attendance.objects.filter(
        employee=employee, status='APPROVED'
    ).count()
    attendance_percentage = (
        (approved_attendance_records / total_attendance_records) * 100
        if total_attendance_records > 0
        else 0
    )
    # Count total projects and pending projects
    completed_projects = Project.objects.filter(manager=employee, status='COMPLETED').count()
    pending_projects = Project.objects.filter(manager=employee).exclude(Q(status='COMPLETED') | Q(status='ONGOING')).count()
    total_projects = completed_projects + pending_projects
    total_pending_projects = total_projects - completed_projects

    context = {
        'employee': employee,
        'all_projects': all_projects,
        'attendance_percentage': round(attendance_percentage, 1),
        'total_projects': total_projects,
        'completed_projects': completed_projects,
        'pending_projects': total_pending_projects,
        'is_manager': employee.is_manager,  # Check if the employee is a manager or not
    }

    return render(request, 'Admin/manager_profile.html', context)


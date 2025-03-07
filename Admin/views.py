from django.shortcuts import redirect, render
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from calendar import monthrange
from datetime import date
from django.db.models import Q, F, Sum
from django.utils.timezone import now
from django.db.models import Prefetch
from django.http import HttpResponse
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.auth.forms import UserCreationForm
from .forms import EmployeeCreationForm, ProjectForm, ProjectAssignmentForm, ManagerEmployeeUpdateForm
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from .models import Attendance, Employee, ProjectAssignment, Project, TeamMemberStatus, Leave, ActivityLog, Notification


# Home
@login_required
def dashboard(request):
    if request.user.is_superuser:  # Admin users
        # Get total projects
        total_projects = Project.objects.count()
        
        # Active Employees
        active_employees = Employee.objects.filter(user__is_active=True).count()
        
        # Total Revenue (Invoice Amount Sum)
        total_revenue = Project.objects.aggregate(Sum('invoice_amount'))['invoice_amount__sum'] or 0
        
        # Total Expenses (Purchase & Expenses Sum)
        total_expenses = Project.objects.aggregate(Sum('purchase_and_expenses'))['purchase_and_expenses__sum'] or 0
        
        # Total Profit = Revenue - Expenses
        total_profit = total_revenue - total_expenses
        
        # Pending Invoices (Projects where invoice_amount is 0)
        pending_invoices = Project.objects.filter(invoice_amount=0).count()
        
        # Project Status Counts
        ongoing_projects = Project.objects.filter(status='ONGOING').count()
        completed_projects = Project.objects.filter(status='COMPLETED').count()
        
        # Overdue Projects (where deadline_date has passed but status is not completed)
        overdue_projects = Project.objects.filter(deadline_date__lt=now().date(), status__in=['ONGOING', 'PENDING']).count()
        
        # Project Completion Percentage
        completion_percentage = (completed_projects / total_projects * 100) if total_projects > 0 else 0
        
        # Fetching project details with manager name
        project_details = Project.objects.annotate(
            leader_name=F('manager__user__username')
        ).values('id', 'name', 'leader_name', 'status', 'category', 'priority').order_by('-created_at')[:5]

        context = {
            'total_projects': total_projects,
            'active_employees': active_employees,
            'total_revenue': round(total_revenue, 2),
            'total_expenses': round(total_expenses, 2),
            'total_profit': round(total_profit, 2),
            'pending_invoices': pending_invoices,
            'project_details': project_details,
            'ongoing_projects': ongoing_projects,
            'completed_projects': completed_projects,
            'overdue_projects': overdue_projects,
            'completion_percentage': round(completion_percentage, 2),
            "now": now()
        }
        return render(request, 'Admin/dashboard.html', context)
    
    elif request.user.is_staff:  # Manager users
        return render(request, 'Manager/dashboard.html')

    else:  # Other users
        return redirect('custom-login')

# create employee
@login_required
def create_employee(request):
    if not request.user.is_staff:
        return redirect('dashboard')
    
    if request.method == 'POST':
        form = EmployeeCreationForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, 'Employee created successfully!')
                return redirect('employee_list')
            except Exception as e:
                messages.error(request, f'There was an error creating the employee: {e}')
        else:
            messages.error(request, 'Please correct the errors below.')
            print(form.errors)
    else:
        form = EmployeeCreationForm()

    return render(request, 'Admin/create_employee.html', {'form': form})

# employee list
@login_required
def employee_list(request):
    if not request.user.is_staff:
        return redirect('custom-login')

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
    ).order_by('-user__date_joined')

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

@login_required
def manager_attendance_list_view(request):
    search_query = request.GET.get('search', '')
    attendance_records = Attendance.objects.select_related('employee').filter(employee__is_manager=True)

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
    return render(request, 'Admin/manager_attendance_list.html', context)


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
    
    pending_requests = Attendance.objects.filter(
        status='PENDING',
        employee__is_employee=True  # Ensures only employees' attendance requests
    ).select_related('employee', 'employee__user')

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

def manage_manager_attendance(request):
    # Ensure the user is a superuser (or manager)
    if not request.user.is_superuser:
        return redirect('admin-dashboard')
    
    pending_requests = Attendance.objects.filter(
        status='PENDING',
        employee__is_manager=True  # Ensures only employees' attendance requests
    ).select_related('employee', 'employee__user')

    if request.method == 'POST':
        # Get form data from the POST request
        attendance_id = request.POST.get('attendance_id')
        action = request.POST.get('action')  # APPROVE or REJECT
        # Get rejection reason if provided
        rejection_reason = request.POST.get('rejection_reason', '')

        if not action:  # Handle case where 'action' is None or missing
            messages.error(
                request, "Action (Approve/Reject) must be selected!")
            return redirect('manage_manager_attendance')

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

    return render(request, 'Admin/manage_manager_attendance.html', {'requests': pending_requests})

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
        return redirect('attendance_list_adminview')

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
    return redirect('attendance_list_adminview')

# project list view
@login_required
def project_list_view(request):
    # Query all projects
    projects = Project.objects.all()

    search_query = request.GET.get('search', '').strip()

    if search_query:
            projects = projects.filter(
                name__icontains=search_query
            )

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

    # Pagination (10 projects per page)
    paginator = Paginator(project_data, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Pass the data to the template
    context = {
        "projects": page_obj,
        "search_query": search_query,
    }

    return render(request, 'Admin/project_list.html', context)


@login_required
def admin_project_summary_view(request, project_id):
    # Ensure only admins can access this page
    if not request.user.is_superuser:
        messages.error(request, "You do not have permission to access this page.")
        return redirect("admin_dashboard")  # Redirect to admin dashboard

    project = get_object_or_404(Project, id=project_id)

    # Fetch logs from both team members and manager
    team_logs = ActivityLog.objects.filter(team_member_status__team__project=project)
    manager_logs = ActivityLog.objects.filter(project=project)

    # Merge and sort logs
    logs = (team_logs | manager_logs).order_by('-changed_at')

    # Fetch team statuses
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
    profit_percent = project.calculate_profit_percentage()
    work_days = project.calculate_total_work_days()

    project_data = {
        "project_name": project.name,
        "client_name": project.client_name,
        "project_manager": project.manager.user.username,
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
        "profit_percent": profit_percent,
        "status": project.status,
        "total_engineer_salary": round(total_engineer_salary, 2),
        "project_create": project.created_at,
        "deadline_date": project.deadline_date,
        "statuses": statuses,
        "logs": logs,
        "project_id": project.id,
        "status_choices": status_choices,
    }

    context = {"project_data": project_data}
    return render(request, 'Admin/project.html', context)

# add project
@login_required
def add_project(request):
    form = ProjectForm()

    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Project added successfully!")
            return redirect('project-list')
        else:
            messages.error(request, "There was an error adding the project. Please check the form.") 

    return render(request, 'Admin/add_project.html', {'form': form})

@login_required
def edit_project(request, project_id):
    project = get_object_or_404(Project, id=project_id)  # Fetch the project or return 404

    if request.method == 'POST':
        form = ProjectForm(request.POST, instance=project)  # Bind form with existing project
        if form.is_valid():
            form.save()
            messages.success(request, "Project updated successfully!") 
            return redirect('project-list')
        else:
            messages.error(request, "There was an error updating the project. Please check the form.")
    else:
        form = ProjectForm(instance=project)

    # For GET request, render the form pre-filled with the project's data
    return render(request, 'Admin/project_edit.html', {'form': form, 'project': project})

@login_required
def delete_project(request, project_id):
    project = get_object_or_404(Project, id=project_id)  
    project.delete()
    messages.success(request, "Project deleted successfully!")
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

    return render(request, 'Admin/employee_profile.html', context)

def is_admin(user):
    """Check if user is an admin"""
    return user.is_superuser

@login_required
@user_passes_test(is_admin)
def admin_edit_employee(request, employee_id):
    """Admin can edit an employee's profile"""
    
    # Ensure we are fetching an Employee instance, NOT a User
    try:
        employee = Employee.objects.select_related("user").get(id=employee_id)
    except Employee.DoesNotExist:
        messages.error(request, "Employee not found.")
        return redirect("employee_list")

    # Ensure employee has a related user
    if not employee.user:
        messages.error(request, "This employee does not have a linked user account.")
        return redirect("employee_list")

    user = employee.user  # Safely access related User instance

    if request.method == "POST":
        form = ManagerEmployeeUpdateForm(request.POST, request.FILES, instance=employee)
        if form.is_valid():
            # Update User fields
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
            return redirect("employee_list")

    else:
        form = ManagerEmployeeUpdateForm(instance=employee, initial={
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone_number": employee.phone_number,
            "rank": employee.rank,
            "salary": employee.salary,
            "date_of_birth": employee.date_of_birth.strftime("%Y-%m-%d") if employee.date_of_birth else '',
            "date_of_join": employee.date_of_join.strftime("%Y-%m-%d") if employee.date_of_join else '',
            "work_days": employee.work_days,
            "holidays": employee.holidays,
            "overseas_days": employee.overseas_days,
            "address": employee.address,
        })

    return render(request, "Admin/edit_employee.html", {"form": form, "employee": employee})


@login_required
def admin_delete_employee(request, employee_id):
    if not request.user.is_staff:  # Ensure only admins or staff can delete
        return redirect('login')

    employee = get_object_or_404(Employee, id=employee_id)
    
    # Process deletion on POST request
    if request.method == 'POST':
        employee.delete()
        messages.success(request, 'Employee deleted successfully!')
        return redirect('employee_list')

    # Redirect back if accessed via GET (optional)
    return redirect('employee_list')


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

@login_required
def admin_edit_manager(request, manager_id):
    """Admin can edit employee details"""
    employee = get_object_or_404(Employee, pk=manager_id)

    if request.method == "POST":
        form = ManagerEmployeeUpdateForm(request.POST, request.FILES, instance=employee)
        if form.is_valid():
            user = employee.user
            user.first_name = form.cleaned_data["first_name"]
            user.last_name = form.cleaned_data["last_name"]
            user.email = form.cleaned_data["email"]

            # Save new password if provided
            if form.cleaned_data["password"]:
                user.set_password(form.cleaned_data["password"])

            user.save()
            form.save()

            messages.success(request, "Employee profile updated successfully.")
            return redirect("manager_list")  # Redirect to employee list page

    else:
        form = ManagerEmployeeUpdateForm(instance=employee, initial={
            "first_name": employee.user.first_name,
            "last_name": employee.user.last_name,
            "email": employee.user.email,
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

    return render(request, "Admin/edit_manager.html", {"form": form, "employee": employee})


@login_required
def admin_delete_manager(request, manager_id):
    if not request.user.is_staff:  # Ensure only admins or staff can delete
        return redirect('login')

    employee = get_object_or_404(Employee, id=manager_id)
    
    # Process deletion on POST request
    if request.method == 'POST':
        employee.delete()
        messages.success(request, 'Manager deleted successfully!')
        return redirect('manager_list')

    # Redirect back if accessed via GET (optional)
    return redirect('manager_list')


@login_required
def employee_leave_list(request):
    if not request.user.is_superuser:
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

    return render(request, 'Admin/employee_leave_list.html', context)

@login_required
def manager_leave_list(request):
    if not request.user.is_superuser:
        return redirect('admin-dashboard')

    search_query = request.GET.get('search', '').strip()

    # Fetch only manager leave records
    leave_records = Leave.objects.filter(user__employee_profile__is_manager=True).select_related('user').order_by('-from_date')

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

    return render(request, 'Admin/manager_leave_list.html', context)


@login_required
def employee_manage_leave(request):
    if not request.user.is_superuser:
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
            return redirect('employee_manage_leave')

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

    return render(request, 'Admin/employee_manage_leave.html', {'leaves': page_obj})


@login_required
def manager_manage_leave(request):
    if not request.user.is_superuser:
        return redirect('admin-dashboard')

    search_query = request.GET.get('search', '').strip()

    # Fetch only manager leave requests
    pending_leaves = Leave.objects.filter(user__employee_profile__is_manager=True, approval_status='PENDING').select_related('user')

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
            return redirect('manager_manage_leave')

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

    return render(request, 'Admin/manager_manage_leave.html', {'leaves': page_obj})


@login_required
def admin_notifications(request):
    """View to display all notifications with search and pagination."""
    search_query = request.GET.get("search", "")
    
    # Filter notifications based on search query
    notifications = Notification.objects.all().order_by("-created_at")
    if search_query:
        notifications = notifications.filter(recipient__username__icontains=search_query)

    # Pagination (10 notifications per page)
    paginator = Paginator(notifications, 10)  
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "Admin/notifications.html", {
        "notifications": page_obj,
        "search_query": search_query,
    })

@login_required
def admin_mark_all_notifications(request):
    """Mark all notifications as read."""
    if request.method == "POST":
        Notification.objects.filter(is_read=False).update(is_read=True)
        return redirect("admin-notifications")

@login_required
def admin_mark_single_notification(request, notification_id):
    """Mark a single notification as read."""
    notification = get_object_or_404(Notification, id=notification_id)
    if request.method == "POST":
        notification.is_read = True
        notification.save()
    return redirect("admin-notifications")


@login_required
def fetch_notifications(request):
    unread_count = Notification.objects.filter(is_read=False).count()
    return JsonResponse({"unread_count": unread_count})

@login_required
def mark_notifications_as_read(request):
    request.user.notifications.update(is_read=True)
    return JsonResponse({"message": "Notifications marked as read"})

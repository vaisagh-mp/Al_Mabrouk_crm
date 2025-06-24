from django.shortcuts import redirect, render
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from calendar import monthrange
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.http import HttpResponseForbidden
from datetime import date
from datetime import timedelta
from django.db.models import Q, F, Sum, Count
from django.utils.timezone import now
from django.db.models import Prefetch
from django.http import HttpResponse
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.auth.forms import UserCreationForm
from .forms import EmployeeCreationForm, ProjectForm, ProjectAssignmentForm, ManagerEmployeeUpdateForm
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
from .models import Attendance, Employee, ProjectAssignment, Project, TeamMemberStatus, Leave, ActivityLog, Notification, ProjectAttachment, Holiday, Team


# Home
@login_required
def dashboard(request):
    if request.user.is_superuser:  # Admin users

        today = now().date()
        first_day_this_month = today.replace(day=1)
        last_day_last_month = first_day_this_month - timedelta(days=1)
        first_day_last_month = last_day_last_month.replace(day=1)

        # Get total projects
        total_projects = Project.objects.count()

        # Project count last month
        projects_last_month = Project.objects.filter(
            created_at__date__gte=first_day_last_month,
            created_at__date__lte=last_day_last_month
        ).count()

        # Project count this month
        projects_this_month = Project.objects.filter(
            created_at__date__gte=first_day_this_month
        ).count()

        # Change percentage
        if projects_last_month > 0:
            change_percentage = ((projects_this_month - projects_last_month) / projects_last_month) * 100
        else:
            change_percentage = 100 if projects_this_month > 0 else 0

        today = now().date()
        start_of_this_month = today.replace(day=1)
        start_of_last_month = (start_of_this_month - timedelta(days=1)).replace(day=1)
        end_of_last_month = start_of_this_month - timedelta(days=1)
        
        # Active Employees
        active_employees = Employee.objects.filter(user__is_active=True).count()

        # Employees added last month
        employees_last_month = Employee.objects.filter(
            user__date_joined__date__gte=start_of_last_month,
            user__date_joined__date__lte=end_of_last_month
        ).count()

        # Calculate change percentage
        if employees_last_month > 0:
            emp_change_percent = ((active_employees - employees_last_month) / employees_last_month) * 100
            emp_change_percent = min(emp_change_percent, 100)  # Cap at 100%
        else:
            emp_change_percent = 100 if active_employees > 0 else 0
        
        # Total Revenue (Invoice Amount Sum)
        total_revenue = Project.objects.aggregate(Sum('invoice_amount'))['invoice_amount__sum'] or 0
        
        # Total Expenses (Purchase & Expenses Sum)
        total_expenses = Project.objects.aggregate(Sum('purchase_and_expenses'))['purchase_and_expenses__sum'] or 0
        
        # Total Profit = Revenue - Expenses
        total_profit = total_revenue - total_expenses

        # Current Month Revenue and Expenses
        current_revenue = Project.objects.filter(
            created_at__date__gte=start_of_this_month
        ).aggregate(Sum('invoice_amount'))['invoice_amount__sum'] or 0

        current_expenses = Project.objects.filter(
            created_at__date__gte=start_of_this_month
        ).aggregate(Sum('purchase_and_expenses'))['purchase_and_expenses__sum'] or 0

        current_profit = current_revenue - current_expenses

        # Last Month Revenue and Expenses
        last_revenue = Project.objects.filter(
            created_at__date__gte=start_of_last_month,
            created_at__date__lte=end_of_last_month
        ).aggregate(Sum('invoice_amount'))['invoice_amount__sum'] or 0

        last_expenses = Project.objects.filter(
            created_at__date__gte=start_of_last_month,
            created_at__date__lte=end_of_last_month
        ).aggregate(Sum('purchase_and_expenses'))['purchase_and_expenses__sum'] or 0

        last_profit = last_revenue - last_expenses

        # Profit Change %
        if last_profit != 0:
            profit_change_percent = ((current_profit - last_profit) / last_profit) * 100
        else:
            profit_change_percent = 0

        # Pending Invoices (Projects where invoice_amount is 0)
        pending_invoices = Project.objects.filter(invoice_amount=0).count()
        
        # Project Status Counts
        ongoing_projects = Project.objects.filter(status='ONGOING').count()
        completed_projects = Project.objects.filter(status='COMPLETED').count()
        
        # Overdue Projects (where deadline_date has passed but status is not completed)
        overdue_projects = Project.objects.filter(deadline_date__lt=now().date(), status__in=['ONGOING', 'PENDING','HOLD','ASSIGN']).count()
        
        # Project Completion Percentage
        completion_percentage = (completed_projects / total_projects * 100) if total_projects > 0 else 0
        
        # Fetching project details with manager name
        project_details = Project.objects.annotate(
            leader_name=F('manager__user__username')
        ).values('id', 'name', 'leader_name', 'status', 'category', 'priority').order_by('-created_at')[:5]

        # Unique client count (ignores empty values)
        client_count = Project.objects.exclude(client_name="").values('client_name').distinct().count()

        # Client count this month
        clients_this_month = Project.objects.filter(
            created_at__date__gte=first_day_this_month
        ).exclude(client_name="").values('client_name').distinct().count()

        # Client count last month
        clients_last_month = Project.objects.filter(
            created_at__date__gte=first_day_last_month,
            created_at__date__lte=last_day_last_month
        ).exclude(client_name="").values('client_name').distinct().count()

        # Calculate client growth %
        if clients_last_month > 0:
            client_change_percent = ((clients_this_month - clients_last_month) / clients_last_month) * 100
        else:
            client_change_percent = 100 if clients_this_month > 0 else 0

        context = {
            "role": "Admin",
            'total_projects': total_projects,
            'change_percentage': round(change_percentage, 2),
            'change_percentage_abs': abs(round(change_percentage, 2)),
            'emp_change_percent': round(emp_change_percent, 2),
            'emp_change_percent_abs': abs(round(emp_change_percent, 2)),
            'active_employees': active_employees,
            'total_revenue': round(total_revenue, 2),
            'total_expenses': round(total_expenses, 2),
            'total_profit': round(total_profit, 2),
            'current_profit': round(current_profit, 2),
            'profit_change_percent': round(profit_change_percent, 2),
            'profit_change_percent_abs': abs(round(profit_change_percent, 2)),
            'pending_invoices': pending_invoices,
            'project_details': project_details,
            'ongoing_projects': ongoing_projects,
            'completed_projects': completed_projects,
            'overdue_projects': overdue_projects,
            'completion_percentage': round(completion_percentage, 2),
            'client_count': client_count,
            'client_change_percent': round(client_change_percent, 2),
            'client_change_percent_abs': abs(round(client_change_percent, 2)),
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
    else:
        form = EmployeeCreationForm()

    return render(request, 'Admin/create_employee.html', {'form': form, 'role': 'Admin'})

# employee list
@login_required
def employee_list(request):
    if not request.user.is_staff:
        return redirect('custom-login')

    # Annotate both team-based and manager-based ongoing projects
    employees = Employee.objects.select_related('user').annotate(
        team_ongoing_count=Count(
            'project_statuses',
            filter=Q(project_statuses__status='ONGOING'),
            distinct=True
        ),
        manager_ongoing_count=Count(
            'managed_projects',
            filter=Q(managed_projects__status='ONGOING'),
            distinct=True
        ),
        ongoing_project_count=F('team_ongoing_count') + F('manager_ongoing_count')
    ).prefetch_related(
        Prefetch(
            'project_statuses',
            queryset=TeamMemberStatus.objects.filter(status='ONGOING').select_related('team__project'),
            to_attr='assigned_projects'
        ),
        Prefetch(
            'managed_projects',
            queryset=Project.objects.filter(status='ONGOING'),
            to_attr='ongoing_managed_projects'
        )
    ).order_by('-user__date_joined')

    paginator = Paginator(employees, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'Admin/employee_list.html', {
        'page_obj': page_obj, 'role': 'Admin'
    })

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

    return render(request, 'Admin/manager_list.html', {'page_obj': page_obj, 'role': 'Admin'})

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
        'role': 'Admin',
        'attendance_records': page_obj,
        'search_query': search_query,
    }
    return render(request, 'Admin/manager_attendance_list.html', context)


# attendance list
@login_required
def attendance_list_view(request):
    search_query = request.GET.get('search', '')
    attendance_records = Attendance.objects.select_related('employee').order_by('-login_time')

    if search_query:
        attendance_records = attendance_records.filter(
            employee__user__username__icontains=search_query
        ).order_by('-login_time')

    paginator = Paginator(attendance_records, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'role': 'Admin',
        'attendance_records': page_obj,
        'search_query': search_query,
    }
    return render(request, 'Admin/attendance_list.html', context)

def attendance_detail(request, pk):
    attendance = get_object_or_404(Attendance, pk=pk)
    return render(request, 'Admin/attendance_detail.html', {'attendance': attendance, 'role': 'Admin'})

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

    return render(request, 'Admin/manage_attendance.html', {'requests': pending_requests, 'role': 'Admin'})


# Only allow superusers (admins)
def admin_required(user):
    return user.is_authenticated and user.is_superuser

@login_required
@user_passes_test(admin_required)
def admin_manage_project_status(request):
    # Get all pending statuses
    pending_status_list = TeamMemberStatus.objects.filter(
        manager_approval_status='PENDING'
    ).select_related('employee', 'team', 'team__project').order_by('-last_updated')

    # PAGINATION: Show 10 per page
    paginator = Paginator(pending_status_list, 10)  # Show 10 per page
    page = request.GET.get('page')

    try:
        pending_statuses = paginator.page(page)
    except PageNotAnInteger:
        pending_statuses = paginator.page(1)
    except EmptyPage:
        pending_statuses = paginator.page(paginator.num_pages)

    # Handle POST (approve/reject)
    if request.method == 'POST':
        tms_id = request.POST.get('tms_id')
        action = request.POST.get('action')
        rejection_reason = request.POST.get('rejection_reason', '')

        try:
            tms = TeamMemberStatus.objects.get(id=tms_id)
            if action == 'APPROVE':
                tms.manager_approval_status = 'APPROVED'
                messages.success(request, f"Status for {tms.employee.user.username} approved.")
            elif action == 'REJECT':
                tms.manager_approval_status = 'REJECTED'
                tms.rejection_reason = rejection_reason
                tms.status = 'ONGOING'
                messages.success(request, f"Status for {tms.employee.user.username} rejected.")
            else:
                messages.error(request, "Invalid action.")
            tms.save()

            Notification.objects.create(
                recipient=tms.employee.user,
                message=f"Your project status '{tms.status}' was {tms.manager_approval_status.lower()} by the admin."
            )

        except TeamMemberStatus.DoesNotExist:
            messages.error(request, "Team member status not found.")

        return redirect('admin_manage_project_status')

    return render(request, 'Admin/manage_project_status.html', {
        'pending_statuses': pending_statuses, 'role': 'Admin'
    })


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

    return render(request, 'Admin/manage_manager_attendance.html', {'requests': pending_requests, 'role': 'Admin'})

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
        'role': 'Admin',
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
    projects = Project.objects.all().order_by('-created_at')

    # Get filters
    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '')

    # Apply search filter
    if search_query:
        projects = projects.filter(
            Q(name__icontains=search_query) |
            Q(code__icontains=search_query) |
            Q(client_name__icontains=search_query)
        )

    # Apply status filter
    if status_filter:
        projects = projects.filter(status=status_filter)

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

    paginator = Paginator(project_data, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'role': 'Admin',
        "projects": page_obj,
        "search_query": search_query,
        "status_filter": status_filter,
        "status_choices": Project.STATUS_CHOICES,
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
        "job_card": project.job_card.url if project.job_card else None,
        "attachment_file": project.attachment.url if project.attachment else None,
    }

    context = {"project_data": project_data, 'role': 'Admin'}
    return render(request, 'Admin/project.html', context)


@login_required
def admin_project_attachments_view(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    attachments = ProjectAttachment.objects.filter(project=project).order_by('-uploaded_at')

    context = {
        'role': 'Admin',
        'project': project,
        'attachments': attachments,
    }
    return render(request, 'Admin/project_attachments.html', context)

@login_required
def admin_delete_project_attachment(request, attachment_id):
    attachment = get_object_or_404(ProjectAttachment, id=attachment_id)
    user = request.user

    # Allow only admin or project manager to delete
    is_admin = user.is_superuser
    is_manager = (
        hasattr(user, 'employee_profile') and 
        attachment.project.manager == user.employee_profile
    )

    if not (is_admin or is_manager):
        messages.error(request, "You do not have permission to delete this file.")
        return redirect('admin_project_attachments_view', project_id=attachment.project.id)

    # Perform deletion
    project_id = attachment.project.id
    attachment.file.delete()
    attachment.delete()
    messages.success(request, "Attachment deleted successfully.")
    return redirect('admin_project_attachments_view', project_id=project_id)

# add project
@login_required
def add_project(request):
    form = ProjectForm()

    if request.method == 'POST':
        form = ProjectForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, "Project added successfully!")
            return redirect('project-list')
        else:
            messages.error(request, "There was an error adding the project. Please check the form.") 

    return render(request, 'Admin/add_project.html', {'form': form,'role': 'Admin'})

@login_required
def edit_project(request, project_id):
    project = get_object_or_404(Project, id=project_id)  # Fetch the project or return 404

    if request.method == 'POST':
        form = ProjectForm(request.POST, request.FILES, instance=project)  # Bind form with existing project
        if form.is_valid():
            form.save()
            messages.success(request, "Project updated successfully!") 
            return redirect('project-list')
        else:
            messages.error(request, "There was an error updating the project. Please check the form.")
    else:
        form = ProjectForm(instance=project)

    # For GET request, render the form pre-filled with the project's data
    return render(request, 'Admin/project_edit.html', {'form': form, 'project': project, 'role': 'Admin'})

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

    return render(request, 'Admin/project_assignment_list.html', {'assignments': assignments, 'projects': projects,'employees': employees, 'role': 'Admin'})

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
    # Get the employee
    employee = get_object_or_404(Employee, pk=employee_id)
    teams = employee.teams_assigned.all()

    team_member_statuses = TeamMemberStatus.objects.filter(employee=employee)

    # Project status counts (as team member)
    completed_projects = team_member_statuses.filter(status='COMPLETED').count()
    pending_projects = team_member_statuses.exclude(status='COMPLETED').count()
    assigned_projects = team_member_statuses.filter(status='ASSIGN').count()
    total_projects = completed_projects + pending_projects + assigned_projects

    # All assigned projects for display
    all_projects = [
        {
            'project': status.team.project,
            'manager': status.team.project.manager,
            'status': status.status
        }
        for status in team_member_statuses
    ]

    # ---- Attendance % Calculation ----
    today = date.today()
    first_day_this_month = today.replace(day=1)
    
    # Safely determine work start date
    if employee.date_of_join:
        work_start_date = max(employee.date_of_join, first_day_this_month)
    else:
        work_start_date = first_day_this_month

    # Get all weekdays (Mon-Sat) between work_start_date and today
    all_days = [work_start_date + timedelta(days=i) for i in range((today - work_start_date).days + 1)]
    weekdays = [d for d in all_days if d.weekday() < 6]

    # Remove holidays
    holidays = Holiday.objects.filter(date__range=(work_start_date, today)).values_list('date', flat=True)
    working_days_this_month = [d for d in weekdays if d not in holidays]
    total_working_days = len(working_days_this_month)

    # Attendance records
    approved_attendance = Attendance.objects.filter(employee=employee, status="APPROVED", login_time__date__gte=work_start_date)

    # Working hours
    full_day_hours = 10
    half_day_min_hours = 5

    full_days = approved_attendance.filter(total_hours_of_work__gte=full_day_hours).values('login_time__date').distinct().count()
    half_days = approved_attendance.filter(
        total_hours_of_work__gte=half_day_min_hours,
        total_hours_of_work__lt=full_day_hours
    ).values('login_time__date').distinct().count()

    attendance_percentage = (
        ((full_days + 0.5 * half_days) / total_working_days * 100)
        if total_working_days > 0 else 0
    )

    # ---- Manager Projects ----
    manager_projects = []
    manager_completed_projects = 0
    manager_pending_projects = 0
    manager_assigned_projects = 0

    if employee.is_manager:
        manager_projects = Project.objects.filter(manager=employee)
        manager_completed_projects = manager_projects.filter(status='COMPLETED').count()
        manager_pending_projects = manager_projects.exclude(status__in=['COMPLETED']).count()
        manager_assigned_projects = manager_projects.filter(status='ASSIGN').count()

    # Context
    context = {
        'role': 'Admin',
        'employee': employee,
        'teams': teams,
        'all_projects': all_projects,
        'attendance_percentage': round(attendance_percentage, 1),

        # Team member stats
        'total_projects': total_projects,
        'completed_projects': completed_projects,
        'pending_projects': pending_projects,

        # Manager stats
        'manager_projects': manager_projects,
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
    
    try:
        employee = Employee.objects.select_related("user").get(id=employee_id)
    except Employee.DoesNotExist:
        messages.error(request, "Employee not found.")
        return redirect("employee_list")

    if not employee.user:
        messages.error(request, "This employee does not have a linked user account.")
        return redirect("employee_list")

    user = employee.user

    if request.method == "POST":
        form = ManagerEmployeeUpdateForm(request.POST, request.FILES, instance=employee)
        if form.is_valid():
            print("Form is valid")  # Debugging
            # Update User fields
            user.username = form.cleaned_data["username"]
            user.first_name = form.cleaned_data["first_name"]
            user.last_name = form.cleaned_data["last_name"]
            user.email = form.cleaned_data["email"]

            # Handle password change
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

            # Reset all role fields before setting the new role
            employee.is_employee = False
            employee.is_manager = False
            employee.is_administration = False
            employee.is_hr = False

            # Update role based on form input
            selected_role = request.POST.get("role")
            if selected_role == "employee":
                employee.is_employee = True
                user.is_staff = False  # Regular employee
            elif selected_role == "manager":
                employee.is_manager = True
                user.is_staff = True   # Manager is staff
            elif selected_role == "administration":
                employee.is_administration = True
                user.is_staff = True   # Admin is staff
            elif selected_role == "hr":
                employee.is_hr = True
                user.is_staff = True   # HR is staff

            employee.save()
            user.save()

            messages.success(request, "Employee profile updated successfully.")
            return redirect("employee_list")

    else:
        initial_role = None  # Default to None
        if employee.is_employee:
            initial_role = "employee"
        elif employee.is_manager:
            initial_role = "manager"
        elif employee.is_administration:
            initial_role = "administration"
        elif employee.is_hr:
            initial_role = "hr"
    
        form = ManagerEmployeeUpdateForm(instance=employee, initial={
            "username": user.username,
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
            "role": initial_role  # Ensure role is set
        })


    return render(request, "Admin/edit_employee.html", {"form": form, "employee": employee, 'role': 'Admin'})

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
    # Get manager object by ID
    manager = get_object_or_404(Employee, pk=manager_id, is_manager=True)
    today = date.today()

    # --- TEAMS AND EMPLOYEES ---
    teams = Team.objects.filter(manager=manager).prefetch_related('employees')
    total_employees = sum(team.employees.count() for team in teams)

    # --- PROJECTS ---
    projects = Project.objects.filter(manager=manager)
    completed_projects = projects.filter(status='COMPLETED').count()
    pending_projects = projects.exclude(status='COMPLETED').count()
    managed_projects = [
        {"name": p.name, "code": p.code, "status": p.status, "team": p.teams.first()}
        for p in projects
    ]

    # --- DATE RANGE ---
    current_year = today.year
    current_month = today.month
    first_day_of_month = today.replace(day=1)
    last_day_of_month = today.replace(day=monthrange(current_year, current_month)[1])

    # --- HELPER: WORKING DAYS ---
    def get_working_days(start, end):
        all_days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        weekdays = [d for d in all_days if d.weekday() < 6]
        holidays = set(Holiday.objects.filter(date__range=(start, end)).values_list('date', flat=True))
        return [d for d in weekdays if d not in holidays]

    # --- TEAM ATTENDANCE ---
    team_attendance_qs = Attendance.objects.filter(
        employee__teams_assigned__manager=manager,
        status='APPROVED',
        login_time__date__range=(first_day_of_month, last_day_of_month)
    ).distinct()

    full_day_hours = 10
    half_day_min_hours = 5

    team_full_days = team_attendance_qs.filter(total_hours_of_work__gte=full_day_hours).values('employee', 'login_time__date').distinct().count()
    team_half_days = team_attendance_qs.filter(
        total_hours_of_work__gte=half_day_min_hours,
        total_hours_of_work__lt=full_day_hours
    ).values('employee', 'login_time__date').distinct().count()

    working_days = get_working_days(first_day_of_month, last_day_of_month)
    team_expected_days = len(working_days) * total_employees
    team_total_attendance = team_full_days + 0.5 * team_half_days

    team_attendance_percentage = round((team_total_attendance / team_expected_days) * 100, 2) if team_expected_days > 0 else 0

    # --- MANAGER'S PERSONAL ATTENDANCE ---
    work_start_date = max(manager.date_of_join, first_day_of_month)
    working_days_manager = get_working_days(work_start_date, today)
    total_working_days_manager = len(working_days_manager)

    manager_attendance_qs = Attendance.objects.filter(
        employee=manager,
        status='APPROVED',
        login_time__date__gte=work_start_date
    )

    manager_full_days = manager_attendance_qs.filter(total_hours_of_work__gte=full_day_hours).values('login_time__date').distinct().count()
    manager_half_days = manager_attendance_qs.filter(
        total_hours_of_work__gte=half_day_min_hours,
        total_hours_of_work__lt=full_day_hours
    ).values('login_time__date').distinct().count()

    manager_attendance_percentage = round(((manager_full_days + 0.5 * manager_half_days) / total_working_days_manager) * 100, 2) if total_working_days_manager > 0 else 0

    # --- CONTEXT ---
    context = {
        'role': 'Admin',
        "manager": manager,
        "teams": teams,
        "total_employees": total_employees,
        "completed_projects": completed_projects,
        "pending_projects": pending_projects,
        "managed_projects": managed_projects,
        "attendance_percentage_current_month": team_attendance_percentage,
        "manager_attendance_percentage": manager_attendance_percentage,
        "is_manager": True,
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

    return render(request, "Admin/edit_manager.html", {"form": form, "employee": employee, 'role': 'Admin'})


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
        'role': 'Admin',
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
        'role': 'Admin',
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

    return render(request, 'Admin/employee_manage_leave.html', {'leaves': page_obj, 'role': 'Admin'})


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

    return render(request, 'Admin/manager_manage_leave.html', {'leaves': page_obj, 'role': 'Admin'})


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
        'role': 'Admin'
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


@login_required
def change_password(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden("You are not authorized to change the password.")

    if request.method == 'POST':
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            return redirect('admin-dashboard')
    else:
        form = PasswordChangeForm(user=request.user)
    
    return render(request, 'Admin/change_password.html', {'form': form, 'role': 'Admin'})
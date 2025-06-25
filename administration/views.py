from django.shortcuts import render, redirect
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils.timezone import now
from calendar import monthrange
from django.utils.timezone import localtime
from django.utils.dateparse import parse_datetime
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import datetime, timedelta, time
from calendar import monthrange
from django.db.models import Q, F, Sum
from datetime import date
import pytz
from django.http import JsonResponse
from employee_data.forms import LeaveForm,EmployeeUpdateForm
from datetime import datetime
from django.contrib import messages
from django.core.paginator import Paginator
from Admin.forms import EmployeeCreationForm, ProjectForm, ProjectAssignmentForm, ManagerEmployeeUpdateForm
from Admin.models import Project, TeamMemberStatus, ActivityLog, Attendance,Employee,LeaveBalance, Team, Leave, Notification, Holiday, ProjectAttachment


@login_required
def admstrn_log_in(request):
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

        return redirect("admstrn_attendance_status")

    return admstrn_render_attendance_page(request)

@login_required
def admstrn_log_off(request, attendance_id):
    if request.method == "POST":
        # Get the Employee object associated with the logged-in user
        try:
            employee = get_object_or_404(Employee, user=request.user)
        except AttributeError:
            messages.error(request, "Your user is not associated with an employee record.")
            return redirect("admstrn_attendance_dashboard")
        
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

    return redirect("admstrn-dashboard")  # Redirect to the appropriate page

@login_required
def admstrn_render_attendance_page(request):
    employee = get_object_or_404(Employee, user=request.user)
    user_attendance = Attendance.objects.filter(employee=employee, log_out_time__isnull=True).first()

    attendance_status_choices = Attendance.ATTENDANCE_STATUS
    location_choices = Attendance.LOCATION_CHOICES
    projects = Project.objects.all()

    return render(request, "administration/punchin.html", {
        'role': 'Administration',
        "attendance_status_choices": attendance_status_choices,
        "location_choices": location_choices,
        "projects": projects,
        "user_attendance": user_attendance,
    })


@login_required
def attendance(request):
    employee = get_object_or_404(Employee, user=request.user)
    attendance_records = Attendance.objects.filter(employee=employee).order_by('-login_time')

    # Pagination settings
    paginator = Paginator(attendance_records, 10)  # Show 10 records per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'role': 'Administration',
        'attendance_records': page_obj,  # Use `page_obj` instead of `attendance_records`
    }
    return render(request, 'administration/attendance.html', context)

def admstrn_update_travel_time(request, attendance_id):
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
            return redirect('admstrn_attendance_status')

        attendance.employee_id = employee_id  # Ensure employee_id is provided
        # Exclude project, attendance_status, and status from form submission
        attendance.rejection_reason = request.POST.get('rejection_reason')
        
        # Don't update the "project", "attendance_status", and "status" fields

        attendance.save()

        messages.success(request, 'Travel time has been updated successfully.')
        return redirect('admstrn_attendance_status')

    employees = Employee.objects.all()
    projects = Project.objects.all()
    return render(request, 'administration/update_travel_time.html', {
        'role': 'Administration',
        'attendance': attendance,
        'employees': employees,
        'projects': projects,
    })

@login_required
def dashboard(request):
    user = request.user
    employee = request.user.employee_profile

    # Superuser Admin Dashboard
    if user.is_superuser:
        return render(request, 'Admin/dashboard.html')
 
    # Manager Dashboard
    elif employee.is_administration:
        manager = get_object_or_404(Employee, user=user)
        total_projects = Project.objects.count()

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

        first_day_this_month = today.replace(day=1)
        last_day_last_month = first_day_this_month - timedelta(days=1)
        first_day_last_month = last_day_last_month.replace(day=1)

        def get_working_days(start, end):
            all_days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
            weekdays = [d for d in all_days if d.weekday() < 6]
            holidays = Holiday.objects.filter(date__range=(start, end)).values_list('date', flat=True)
            return len([d for d in weekdays if d not in holidays])
        
        approved_attendance = Attendance.objects.filter(employee=manager, status='APPROVED')
        approved_attendance_this_month = approved_attendance.filter(login_time__date__gte=first_day_this_month)
        approved_attendance_last_month = approved_attendance.filter(login_time__date__gte=first_day_last_month,         login_time__date__lt=first_day_this_month)

        working_days_this_month = get_working_days(first_day_this_month, today)
        working_days_last_month = get_working_days(first_day_last_month, last_day_last_month)

        def calculate_attendance_percent(qs, working_days):
            full = qs.filter(total_hours_of_work__gte=10).values('login_time__date').distinct().count()
            half = qs.filter(total_hours_of_work__gte=5, total_hours_of_work__lt=10).values('login_time__date').distinct().     count()
            return round(((full + 0.5 * half) / working_days) * 100, 2) if working_days > 0 else 0

        attendance_percentage_current_month = calculate_attendance_percent(approved_attendance_this_month,      working_days_this_month)
        attendance_last_month_percent = calculate_attendance_percent(approved_attendance_last_month,        working_days_last_month)


        # Fetch projects assigned to the manager
        assigned_projects = Project.objects.filter(manager=manager, status="ASSIGN")
        total_projects =  Project.objects.count()
        completed_projects = Project.objects.filter(status='COMPLETED').count()
        pending_projects =  Project.objects.filter(status='ONGOING').count()

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
        total_leaves = leave_balance.annual_leave + leave_balance.sick_leave if leave_balance else 0


        # Leaves Taken
        annual_leave_taken = Leave.objects.filter(
            user=user, approval_status="APPROVED", leave_type="ANNUAL LEAVE", from_date__year=current_year
        ).aggregate(total=Sum('no_of_days'))['total'] or 0

        sick_leave_taken = Leave.objects.filter(
            user=user, approval_status="APPROVED", leave_type="SICK LEAVE", from_date__year=current_year
        ).aggregate(total=Sum('no_of_days'))['total'] or 0

        leaves_taken = annual_leave_taken + sick_leave_taken

        leave_requests = Leave.objects.filter(user=user, approval_status="PENDING").count()

        # Fetching Workdays
        worked_days = manager.work_days
        absent_days = leaves_taken
        loss_of_pay_days = absent_days - total_leaves if absent_days > total_leaves else 0

        # Fetching project details with manager name
        project_details = Project.objects.annotate(
            leader_name=F('manager__user__username')
        ).values('id', 'name', 'leader_name', 'status', 'category', 'priority').order_by('-created_at')[:5]

        on_time_count = late_count = wfh_count = 0
        for att in approved_attendance:
            if att.login_time:
                login_time = localtime(att.login_time).time()
                if time(9, 0) <= login_time <= time(9, 15):
                    on_time_count += 1
                elif login_time > time(9, 15):
                    late_count += 1
            if att.attendance_status == 'WORK FROM HOME':
                wfh_count += 1

        joining_date = manager.date_of_join or today.replace(month=1, day=1)
        all_dates = [joining_date + timedelta(days=i) for i in range((today - joining_date).days + 1)]
        weekdays = [d for d in all_dates if d.weekday() < 6]
        holidays = Holiday.objects.filter(date__range=(joining_date, today)).values_list('date', flat=True)
        total_employment_working_days = len([d for d in weekdays if d not in holidays])
        absent_days_count = total_employment_working_days - (on_time_count + late_count + wfh_count + annual_leave_taken +      sick_leave_taken)
        absent_days_count = max(absent_days_count, 0)

        def calculate_growth(current, previous):
            if previous == 0:
                return 100 if current > 0 else 0
            return ((current - previous) / previous) * 100

        projects_this_month = Project.objects.filter(created_at__gte=first_day_this_month).count()
        projects_last_month = Project.objects.filter(created_at__gte=first_day_last_month,      created_at__lt=first_day_this_month).count()
        project_growth_percentage = calculate_growth(projects_this_month, projects_last_month)

        pending_projects_this_month = Project.objects.filter(status='ASSIGN', created_at__gte=first_day_this_month).count()
        pending_projects_last_month = Project.objects.filter(status='ASSIGN', created_at__gte=first_day_last_month,         created_at__lt=first_day_this_month).count()
        pending_growth_percentage = calculate_growth(pending_projects_this_month, pending_projects_last_month)

        completed_projects_this_month = Project.objects.filter(status='COMPLETED', created_at__gte=first_day_this_month).       count()
        completed_projects_last_month = Project.objects.filter(status='COMPLETED', created_at__gte=first_day_last_month,        created_at__lt=first_day_this_month).count()
        completed_growth_percentage = calculate_growth(completed_projects_this_month, completed_projects_last_month)

        ongoing_projects_this_month = Project.objects.filter(
            status='ONGOING',
            created_at__gte=first_day_this_month
        ).count()

        ongoing_projects_last_month = Project.objects.filter(
            status='ONGOING',
            created_at__gte=first_day_last_month,
            created_at__lt=first_day_this_month
        ).count()

        ongoing_growth_percentage = calculate_growth(ongoing_projects_this_month, ongoing_projects_last_month)

        attendance_growth_percentage = calculate_growth(attendance_percentage_current_month, attendance_last_month_percent)

        balance_annual_leave = leave_balance.annual_leave if leave_balance else 0
        balance_sick_leave = leave_balance.sick_leave if leave_balance else 0
        
        context = {
            'role': 'Administration',
            'manager': manager,
            'project_details': project_details,
            'assigned_projects': assigned_projects,
            'total_projects': total_projects,
            'completed_projects': completed_projects,
            'pending_projects': pending_projects,
            'total_teams': total_teams,
            'attendance_percentage': round(attendance_percentage, 1),
            'attendance_percentage_current_month': round(attendance_percentage_current_month, 2),
            'total_leaves': total_leaves,
            'leaves_taken': leaves_taken,
            'annual_leave_taken': annual_leave_taken,
            'sick_leave_taken': sick_leave_taken,
            'balance_annual_leave': balance_annual_leave,
            'balance_sick_leave': balance_sick_leave,
            'leave_requests': leave_requests,
            'worked_days': worked_days,
            'absent_days': absent_days,
            'loss_of_pay_days': loss_of_pay_days,
            "last_punch_in": last_punch_in,
            "last_punch_out": last_punch_out,
            'user_attendance': today_attendance,
            "current_time": timezone.now().astimezone(local_tz).strftime('%I:%M %p, %d %b %Y'),
            'chart_data': {
                'on_time': on_time_count,
                'late': late_count,
                'wfh': wfh_count,
                'absent': absent_days_count,
                'sick': sick_leave_taken,
            },
            'project_growth_percentage': round(projects_this_month - projects_last_month, 2),
            'ongoing_growth_percentage': round(ongoing_growth_percentage, 2),
            'pending_growth_percentage': abs(round(calculate_growth(pending_projects_this_month,pending_projects_last_month),              2)),
            'completed_growth_percentage': round(calculate_growth(completed_projects_this_month,               completed_projects_last_month), 2),
            'attendance_growth_percentage': abs(round(attendance_growth_percentage, 2)),
            'attendance_growth_positive': attendance_growth_percentage >= 0,
                    }

        return render(request, 'administration/dashboard.html', context)

    # Employee Dashboard
    else:
        return render(request, 'employee/employee_dashboard.html')

# Add Project
@login_required
def admstrn_add_project(request):
    form = ProjectForm()

    if request.method == 'POST':
        form = ProjectForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect('admstrn_project_list_view')
        else:
            form = ProjectForm()

    # No pagination code; simply render the form
    return render(request, 'administration/add_project.html', {'form': form, 'role': 'Administration',})


# project list view
@login_required
def admstrn_project_list_view(request):
    # Query all projects
    projects = Project.objects.all().order_by('-created_at')

    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '')

    if search_query:
            projects = projects.filter(
                name__icontains=search_query
            )

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

    # Pagination (10 projects per page)
    paginator = Paginator(project_data, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Pass the data to the template
    context = {
        'role': 'Administration',
        "projects": page_obj,
        "search_query": search_query,
        "status_filter": status_filter,
        "status_choices": Project.STATUS_CHOICES,
    }

    return render(request, 'administration/project_list.html', context)


@login_required
def admstrn_project_summary_view(request, project_id):
    # Ensure only admins can access this page
    employee = request.user.employee_profile 
    if not employee.is_administration:
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

    context = {"project_data": project_data,'role': 'Administration',}
    return render(request, 'administration/projectsummary.html', context)

@login_required
def admstrn_project_attachments_view(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    attachments = ProjectAttachment.objects.filter(project=project).order_by('-uploaded_at')

    context = {
        'role': 'Administration',
        'project': project,
        'attachments': attachments,
    }
    return render(request, 'administration/project_attachments.html', context)

@login_required
def admstrn_delete_project_attachment(request, attachment_id):
    attachment = get_object_or_404(ProjectAttachment, id=attachment_id)
    user = request.user

    # Allow only admin or project manager to delete
    is_admin = user.is_administration
    is_manager = (
        hasattr(user, 'employee_profile') and 
        attachment.project.manager == user.employee_profile
    )

    if not (is_admin or is_manager):
        messages.error(request, "You do not have permission to delete this file.")
        return redirect('admstrn_project_attachments_view', project_id=attachment.project.id)

    # Perform deletion
    project_id = attachment.project.id
    attachment.file.delete()
    attachment.delete()
    messages.success(request, "Attachment deleted successfully.")
    return redirect('admstrn_project_attachments_view', project_id=project_id)

@login_required
def admstrn_edit_project(request, project_id):
    project = get_object_or_404(Project, id=project_id)  # Fetch the project or return 404

    if request.method == 'POST':
        form = ProjectForm(request.POST, request.FILES, instance=project)  # Bind form with existing project
        if form.is_valid():
            form.save()
            return redirect('admstrn_project_list_view')
        else:
            form = ProjectForm(instance=project)

    # For GET request, render the form pre-filled with the project's data
    return render(request, 'administration/project_edit.html', {'form': ProjectForm(instance=project),'role': 'Administration',})


@login_required
def admstrn_delete_project(request, project_id):
    project = get_object_or_404(Project, id=project_id) 
    project.delete() 
    return redirect('admstrn_project_list_view')


@login_required
def admstrn_apply_leave(request):
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
            return redirect('admstrn_leave_status')  # Redirect after success
    else:
        form = LeaveForm()

    return render(request, 'administration/leave.html', {'form': form, 'role': 'Administration',})

@login_required
def admstrn_upload_medical_certificate(request, leave_id):
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

            return redirect('admstrn_leave_status')

    return redirect('admstrn_leave_status')  # Redirect if GET request or no file uploaded


@login_required
def admstrn_leave_status(request):
    """View to display the leave status of the logged-in user."""
    leaves_list = Leave.objects.filter(user=request.user).order_by("-from_date")
    
    # Paginate leave requests (10 per page)
    paginator = Paginator(leaves_list, 10)  
    page_number = request.GET.get("page")
    leaves = paginator.get_page(page_number)

    return render(request, "administration/leavestatus.html", {"leaves": leaves})

@login_required
def admstrn_leave_records(request):
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

    return render(request, "administration/leaverecords.html", {"leave_summary": leave_summary, 'role': 'Administration',})

@login_required
def admstrn_fetch_notifications(request):
    notifications = Notification.objects.filter(recipient=request.user, is_read=False).values("id", "message", "created_at")
    return JsonResponse({"notifications": list(notifications)})

@login_required
def admstrn_mark_notifications_as_read(request):
    """Mark all unread notifications for the logged-in manager as read."""
    notifications = Notification.objects.filter(recipient=request.user, is_read=False)
    
    if notifications.exists():
        notifications.update(is_read=True)  # Bulk update for efficiency
    
    return JsonResponse({"message": "Notifications marked as read"})


@login_required
def admstrn_profile(request):
    employee = get_object_or_404(Employee, user=request.user)
    teams = employee.teams_assigned.all()

    # TeamMemberStatuses related to this employee
    team_member_statuses = TeamMemberStatus.objects.filter(employee=employee)

    # Projects related to the employee's teams
    all_projects = Project.objects.all()

    # Project status counts
    total_projects =  Project.objects.count()
    completed_projects = Project.objects.filter(status='COMPLETED').count()
    ongoing_projects = Project.objects.filter(status='ONGOING').count()
    total_pending_projects =  Project.objects.exclude(status='COMPLETED').count()

    # Attendance percentage calculation
    current_year = date.today().year
    current_month = date.today().month
    total_days_in_month = monthrange(current_year, current_month)[1]

    approved_attendance_records = Attendance.objects.filter(
        employee=employee,
        status='APPROVED',
        login_time__year=current_year,
        login_time__month=current_month
    ).count()

    attendance_percentage = (approved_attendance_records / total_days_in_month) * 100 if total_days_in_month else 0

    context = {
        'role': 'Administration',
        'employee': employee,
        'teams': teams,
        'all_projects': all_projects,
        'attendance_percentage': round(attendance_percentage, 1),
        'total_projects': total_projects,
        'ongoing_projects': ongoing_projects,
        'completed_projects': completed_projects,
        'pending_projects': total_pending_projects,
    }

    return render(request, 'administration/profile.html', context)


@login_required
def admstrn_update_profile(request):
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
            return redirect("admstrn_profile_view")

    else:
        form = EmployeeUpdateForm(instance=employee, initial={
            "first_name": request.user.first_name,
            "last_name": request.user.last_name,
            "email": request.user.email,
        })

    return render(request, "administration/updateprofile.html", {"form": form, 'role': 'Administration',})

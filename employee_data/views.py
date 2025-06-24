from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from calendar import monthrange
from django.utils.timezone import now,localdate
from django.utils.dateparse import parse_datetime
from django.http import JsonResponse, HttpResponseForbidden
from django.core.paginator import Paginator
from .forms import LeaveForm, EmployeeUpdateForm
from datetime import date, timedelta,time
from django.db.models import Sum
from django.db.models import Q, F
from datetime import datetime
from django.contrib import messages
from Admin.models import Employee, Attendance, ProjectAssignment, Project, Team, TeamMemberStatus, ActivityLog, Leave, LeaveBalance, Notification, ProjectAttachment, Holiday
from django.utils import timezone
from django.utils.timezone import localtime
import pytz

@login_required
def employee_dashboard(request):
    user = request.user
    if hasattr(user, 'employee_profile') and user.employee_profile.is_employee:
        employee = get_object_or_404(Employee, user=user)

        today = date.today()
        first_day_this_month = today.replace(day=1)

        # Attendance records
        attendance_records = Attendance.objects.filter(employee=employee).order_by('-login_time')
        today_attendance = attendance_records.filter(login_time__date=today).first()

        # Local time
        local_tz = pytz.timezone('Asia/Dubai')
        current_time = now().astimezone(local_tz)

        # Punch display
        if today_attendance:
            last_punch_in = localtime(today_attendance.login_time).astimezone(local_tz).strftime('%I:%M %p')
            last_punch_out = (
                localtime(today_attendance.log_out_time).astimezone(local_tz).strftime('%I:%M %p')
                if today_attendance.log_out_time else "Not Punched Out"
            )
        else:
            last_punch_in = "Not Punched In"
            last_punch_out = "Not Punched Out"

        # Leave calculations
        leave_balance = LeaveBalance.objects.filter(user=user).first()
        annual_leave_taken = Leave.objects.filter(user=user, approval_status="APPROVED", leave_type="ANNUAL LEAVE").aggregate(total=Sum('no_of_days'))['total'] or 0
        sick_leave_taken = Leave.objects.filter(user=user, approval_status="APPROVED", leave_type="SICK LEAVE").aggregate(total=Sum('no_of_days'))['total'] or 0
        total_leaves = (leave_balance.annual_leave + leave_balance.sick_leave) if leave_balance else 0
        leaves_taken = annual_leave_taken + sick_leave_taken
        balance_annual_leave = max(leave_balance.annual_leave - annual_leave_taken, 0) if leave_balance else 0
        balance_sick_leave = max(leave_balance.sick_leave - sick_leave_taken, 0) if leave_balance else 0
        loss_of_pay_days = leaves_taken - total_leaves if leaves_taken > total_leaves else 0

        # Total employment working days
        joining_date = employee.date_of_join or today.replace(month=1, day=1)
        all_dates = [joining_date + timedelta(days=i) for i in range((today - joining_date).days + 1)]
        weekdays = [d for d in all_dates if d.weekday() < 6]
        holidays = Holiday.objects.filter(date__range=(joining_date, today)).values_list('date', flat=True)
        total_employment_working_days = len([d for d in weekdays if d not in holidays])

        # Attendance classification
        approved_attendance = Attendance.objects.filter(employee=employee, status="APPROVED")
        on_time_count = 0
        late_count = 0
        for att in approved_attendance:
            if att.login_time:
                login_t = localtime(att.login_time).time()
                if time(9, 0) <= login_t <= time(9, 15):
                    on_time_count += 1
                elif login_t > time(9, 15):
                    late_count += 1

        wfh_count = approved_attendance.filter(attendance_status='WORK FROM HOME').count()
        absent_days_count = total_employment_working_days - (on_time_count + late_count + wfh_count + annual_leave_taken + sick_leave_taken)
        absent_days_count = max(absent_days_count, 0)

        # Attendance % (overall)
        full_day_hours = 10
        half_day_min_hours = 5
        full_days = approved_attendance.filter(total_hours_of_work__gte=full_day_hours).values('login_time__date').distinct().count()
        half_days = approved_attendance.filter(total_hours_of_work__gte=half_day_min_hours, total_hours_of_work__lt=full_day_hours).values('login_time__date').distinct().count()
        attendance_percentage = ((full_days + 0.5 * half_days) / total_employment_working_days * 100) if total_employment_working_days > 0 else 0

        # Projects
        assigned_work = TeamMemberStatus.objects.filter(employee=employee, status='ASSIGN').select_related('team__project')[:10]
        projects = TeamMemberStatus.objects.filter(employee=employee).select_related('team__project')
        total_projects = projects.count()
        pending_projects = projects.exclude(status='COMPLETED').count()
        completed_projects = projects.filter(status='COMPLETED').count()

        # Project growth
        last_day_last_month = first_day_this_month - timedelta(days=1)
        first_day_last_month = last_day_last_month.replace(day=1)

        project_ids = projects.values_list('team__project__id', flat=True).distinct()
        projects_this_month = Project.objects.filter(id__in=project_ids, created_at__gte=first_day_this_month).count()
        projects_last_month = Project.objects.filter(id__in=project_ids, created_at__gte=first_day_last_month, created_at__lt=first_day_this_month).count()

        pending_projects_this_month = TeamMemberStatus.objects.filter(
            employee=employee, status='ASSIGN', team__project__created_at__gte=first_day_this_month
        ).values('team__project').distinct().count()

        pending_projects_last_month = TeamMemberStatus.objects.filter(
            employee=employee, status='ASSIGN',
            team__project__created_at__gte=first_day_last_month,
            team__project__created_at__lt=first_day_this_month
        ).values('team__project').distinct().count()

        completed_projects_this_month = TeamMemberStatus.objects.filter(
            employee=employee, status='COMPLETED', team__project__created_at__gte=first_day_this_month
        ).values('team__project').distinct().count()

        completed_projects_last_month = TeamMemberStatus.objects.filter(
            employee=employee, status='COMPLETED',
            team__project__created_at__gte=first_day_last_month,
            team__project__created_at__lt=first_day_this_month
        ).values('team__project').distinct().count()

        def calculate_growth(current, previous):
            if previous == 0:
                return 100 if current > 0 else 0
            return ((current - previous) / previous) * 100

        project_growth_percentage = calculate_growth(projects_this_month, projects_last_month)
        pending_growth_percentage = calculate_growth(pending_projects_this_month, pending_projects_last_month)
        completed_growth_percentage = calculate_growth(completed_projects_this_month, completed_projects_last_month)

        # === Updated Current Month Attendance Calculation ===
        if employee.date_of_join:
            attendance_start_date = max(employee.date_of_join, first_day_this_month)
        else:
            attendance_start_date = first_day_this_month
        attendance_this_month = approved_attendance.filter(login_time__date__gte=attendance_start_date)

        all_days = [attendance_start_date + timedelta(days=i) for i in range((today - attendance_start_date).days + 1)]
        weekdays = [d for d in all_days if d.weekday() < 6]
        holidays = set(Holiday.objects.filter(date__range=(attendance_start_date, today)).values_list('date', flat=True))
        working_days_this_month = [d for d in weekdays if d not in holidays]
        total_working_days_this_month = len(working_days_this_month)

        full_days_current_month = attendance_this_month.filter(
            total_hours_of_work__gte=full_day_hours
        ).values('login_time__date').distinct().count()

        half_days_current_month = attendance_this_month.filter(
            total_hours_of_work__gte=half_day_min_hours,
            total_hours_of_work__lt=full_day_hours
        ).values('login_time__date').distinct().count()

        attendance_percentage_current_month = (
            (full_days_current_month + 0.5 * half_days_current_month) / total_working_days_this_month * 100
            if total_working_days_this_month > 0 else 0
        )

        # Attendance growth compared to last month
        def calculate_attendance_percent(attendance_qs, total_working_days):
            full = attendance_qs.filter(total_hours_of_work__gte=full_day_hours).values('login_time__date').distinct().count()
            half = attendance_qs.filter(total_hours_of_work__gte=half_day_min_hours, total_hours_of_work__lt=full_day_hours).values('login_time__date').distinct().count()
            return round((full + 0.5 * half) / total_working_days * 100, 2) if total_working_days > 0 else 0

        def get_working_days(start, end):
            all_days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
            weekdays = [d for d in all_days if d.weekday() < 6]
            holidays = set(Holiday.objects.filter(date__range=(start, end)).values_list('date', flat=True))
            return len([d for d in weekdays if d not in holidays])

        working_days_last_month = get_working_days(first_day_last_month, last_day_last_month)
        attendance_last_month = approved_attendance.filter(login_time__date__gte=first_day_last_month, login_time__date__lt=first_day_this_month)
        attendance_last_month_percent = calculate_attendance_percent(attendance_last_month, working_days_last_month)
        raw_growth = calculate_growth(attendance_percentage_current_month, attendance_last_month_percent)
        attendance_growth_percentage = min(abs(round(raw_growth, 2)), 100)

        # Final Context
        context = {
            "role": "Engineer",
            'employee': employee,
            'assigned_work': assigned_work,
            'attendance_records': attendance_records,
            'attendance_percentage': round(attendance_percentage, 2),
            'attendance_percentage_current_month': round(attendance_percentage_current_month, 2),
            'current_time': current_time.strftime('%I:%M %p, %d %b %Y'),
            'last_punch_in': last_punch_in,
            'last_punch_out': last_punch_out,
            'total_projects': total_projects,
            'pending_projects': pending_projects,
            'completed_projects': completed_projects,
            'total_leaves': total_leaves,
            'leaves_taken': leaves_taken,
            'absent_days': leaves_taken,
            'annual_leave_taken': annual_leave_taken,
            'sick_leave_taken': sick_leave_taken,
            'balance_annual_leave': balance_annual_leave,
            'balance_sick_leave': balance_sick_leave,
            'leave_requests': Leave.objects.filter(user=user, approval_status="PENDING").count(),
            'worked_days': round(employee.work_days, 2),
            'loss_of_pay_days': loss_of_pay_days,
            'user_attendance': today_attendance,
            'project_growth_percentage': round(project_growth_percentage, 2),
            'pending_growth_percentage': abs(round(pending_growth_percentage, 2)),
            'completed_growth_percentage': round(completed_growth_percentage, 2),
            'attendance_growth_percentage': abs(round(attendance_growth_percentage, 2)),
            'attendance_growth_positive': attendance_growth_percentage >= 0,
            'chart_data': {
                'on_time': on_time_count,
                'late': late_count,
                'wfh': wfh_count,
                'absent': absent_days_count,
                'sick': sick_leave_taken,
            },
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
            status='APPROVED',
        )
        messages.success(request, "Attendance request submitted successfully!")
        return redirect('employee_dashboard')
    
    # Fetch projects assigned to the current employee via projectassignment
    assigned_projects = Project.objects.filter(projectassignment__employee=employee)

    # Add context for location, attendance status, and project choices
    context = {
        "role": "Engineer",
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

@login_required
def attendance_list(request):
    employee = get_object_or_404(Employee, user=request.user)
    attendance_records = Attendance.objects.filter(employee=employee).order_by('-login_time')

    # Pagination settings
    paginator = Paginator(attendance_records, 10)  # Show 10 records per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        "role": "Engineer",
        'attendance_records': page_obj,  # Use `page_obj` instead of `attendance_records`
    }
    return render(request, 'employee/attendance_list.html', context)

@login_required
def profile(request):
    employee = get_object_or_404(Employee, user=request.user)
    teams = employee.teams_assigned.all()
    team_member_statuses = TeamMemberStatus.objects.filter(employee=employee)

    # Project status counts
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

    # Attendance Percentage Calculation for current month only
    approved_attendance = Attendance.objects.filter(employee=employee, status="APPROVED")

    # Get current date and month range
    today = date.today()
    first_day_this_month = today.replace(day=1)

    # Use the later of join date or start of this month
    work_start_date = max(employee.date_of_join, first_day_this_month)

    # Get working days from join date/1st of month to today (Mon–Sat)
    all_days = [work_start_date + timedelta(days=i) for i in range((today - work_start_date).days + 1)]
    weekdays = [d for d in all_days if d.weekday() < 6]

    holidays = Holiday.objects.filter(date__range=(work_start_date, today)).values_list('date', flat=True)
    working_days_this_month = [d for d in weekdays if d not in holidays]
    total_working_days_this_month = len(working_days_this_month)

    # Define working hours
    full_day_hours = 10
    half_day_min_hours = 5

    # Full and half days worked in current month
    attendance_this_month = approved_attendance.filter(login_time__date__gte=work_start_date)
    full_days = attendance_this_month.filter(total_hours_of_work__gte=full_day_hours).values('login_time__date').distinct().count()
    half_days = attendance_this_month.filter(
        total_hours_of_work__gte=half_day_min_hours,
        total_hours_of_work__lt=full_day_hours
    ).values('login_time__date').distinct().count()

    # Attendance %
    attendance_percentage = ((full_days + 0.5 * half_days) / total_working_days_this_month * 100) if total_working_days_this_month > 0 else 0

    context = {
        "role": "Engineer",
        'employee': employee,
        'teams': teams,
        'all_projects': all_projects,
        'attendance_percentage': round(attendance_percentage, 1),
        'total_projects': total_projects,
        'completed_projects': completed_projects,
        'pending_projects': pending_projects,
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

    return render(request, "employee/updateprofile.html", {"form": form, "role": "Engineer",})

@login_required
def projects(request):
    current_user = request.user
    status_filter = request.GET.get('status', '')

    employee = current_user.employee_profile

    # Base queryset
    team_member_statuses = TeamMemberStatus.objects.filter(
        employee=employee
    ).order_by('-team__project__created_at')

    # Apply status filter
    if status_filter:
        if status_filter == 'PENDING':
            team_member_statuses = team_member_statuses.filter(manager_approval_status='PENDING')
        else:
            team_member_statuses = team_member_statuses.filter(status=status_filter)

    # Prepare project data
    project_data = [
        {
            "id": status.team.project.id,
            "name": status.team.project.name,
            "category": status.team.project.category,
            "code": status.team.project.code,
            "status": "PENDING" if status.manager_approval_status == "PENDING" else status.status,
            "invoice": status.team.project.invoice_amount,
            "currency": status.team.project.currency_code,
            "purchase_and_expenses": status.team.project.purchase_and_expenses,
        }
        for status in team_member_statuses
    ]

    # Pagination
    paginator = Paginator(project_data, 5)
    page_number = request.GET.get('page')
    paginated_projects = paginator.get_page(page_number)

    context = {
        "role": "Engineer",
        "projects": paginated_projects,
        "status_filter": status_filter,
        "status_choices": Project.STATUS_CHOICES,
    }

    return render(request, 'employee/project_list.html', context)

@login_required
def project_details(request, project_id):
    # Get the specific project by ID
    project = get_object_or_404(Project, id=project_id)
    employee = get_object_or_404(Employee, user=request.user)

    # Fetch logs from team members
    team_logs = ActivityLog.objects.filter(team_member_status__team__project=project)

    # Fetch logs where the manager updated the project status
    manager_logs = ActivityLog.objects.filter(project=project)

    # Merge both logs and order by `changed_at`
    logs = (team_logs | manager_logs).distinct().order_by('-changed_at')

    MAX_UPLOAD_SIZE = 49 * 1024 * 1024  # 49 MB in bytes

    # Handle multiple file uploads
    if request.method == "POST":
        if request.FILES.getlist("attachments"):
            uploaded_files = request.FILES.getlist("attachments")
            for file in uploaded_files:
                if file.size > MAX_UPLOAD_SIZE:
                    messages.error(request, f"File {file.name} exceeds the 49MB limit.")
                    continue

                ProjectAttachment.objects.create(
                    project=project,
                    file=file,
                    uploaded_by=employee
                )

                # Find the matching TeamMemberStatus for logging
        tms = TeamMemberStatus.objects.filter(
            team__project=project,
            employee=employee
        ).first()
    
        ActivityLog.objects.create(
            project=project,
            team_member_status=tms,
            new_status="Attachment Uploaded",
            changed_by=employee
        )
   
    statuses = TeamMemberStatus.objects.filter(
        team__project__id=project_id,
        employee=employee  # Filtering by current logged-in employee
    ).order_by('-last_updated')

    logs = ActivityLog.objects.filter(
        team_member_status__employee_id=employee.id,
        team_member_status__team__project_id=project_id
    ).order_by('-changed_at')



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

    attachments = ProjectAttachment.objects.filter(project=project)
    # Prepare data for the project
    project_data = {
        "project_name": project.name,
        "client_name": project.client_name,
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
        "attachments": attachments,
        "job_card": project.job_card.url if project.job_card else None,
    }

    # Pass the data to the template
    context = {
        "role": "Engineer",
        "project_data": project_data
    }

    return render(request, 'employee/project_details.html', context)


# project attachments
@login_required
def emp_project_attachments_view(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    attachments = ProjectAttachment.objects.filter(project=project).order_by('-uploaded_at')

    context = {
        "role": "Engineer",
        'project': project,
        'attachments': attachments,
    }
    return render(request, 'employee/project_attachments.html', context)


@login_required
def update_team_member_status(request, project_id):
    if request.method == "POST":
        employee = get_object_or_404(Employee, user=request.user)
        new_status = request.POST.get("status")
        remark      = request.POST.get("remark", "")

        # Fetch TeamMemberStatus entry for this employee & project
        team_member_status = TeamMemberStatus.objects.filter(
            team__project_id=project_id,
            employee=employee
        ).first()

        if not team_member_status:
            messages.error(request, "No team‑member status found for this project.")
            return redirect('project_details', project_id=project_id)

        # Only continue if the status really changed
        if team_member_status.status != new_status:
            team_member_status.status                  = new_status
            team_member_status.notes                   = remark
            team_member_status.manager_approval_status = "PENDING"   # reset approval
            team_member_status.rejection_reason        = ""          # clear old reason
            team_member_status.save()

            messages.success(
                request,
                f"Status updated to {team_member_status.get_status_display()} "
                "and sent for manager approval."
            )
        else:
            messages.info(request, "Status unchanged – nothing to update.")

    return redirect('project_details', project_id=project_id)


@login_required
def resubmit_project_status(request):
    if request.method == 'POST':
        status_id = request.POST.get('status_id')
        new_status = request.POST.get('status')
        notes = request.POST.get('notes', '')

        tms = get_object_or_404(TeamMemberStatus, id=status_id)

        if tms.employee.user != request.user:
            return HttpResponseForbidden("You are not authorized to update this status.")

        if tms.status != new_status:
            tms.status = new_status
            tms.notes = notes or tms.notes
            tms.manager_approval_status = 'PENDING'
            tms.rejection_reason = ''
            tms.save()
            messages.success(request, "Status re-submitted for manager approval.")
        else:
            messages.info(request, "No changes made to the status.")

        return redirect('project_details', project_id=tms.team.project.id)

    return HttpResponseForbidden("Only POST requests are allowed.")

@login_required
def log_in(request):
    if request.method == "POST":
        employee = get_object_or_404(Employee, user=request.user)
        project_id = request.POST.get("project")
        location = request.POST.get("location")
        attendance_status = request.POST.get("attendance_status")

        travel_in_time_str = request.POST.get("travel_in_time")
        travel_out_time_str = request.POST.get("travel_out_time")
        travel_in_time = parse_datetime(travel_in_time_str) if travel_in_time_str else None
        travel_out_time = parse_datetime(travel_out_time_str) if travel_out_time_str else None

        today = localdate()

        # Check if employee already has an attendance record today
        already_logged_today = Attendance.objects.filter(
            employee=employee,
            login_time__date=today
        ).exists()

        if already_logged_today:
            messages.error(request, "You have already submitted attendance today.")
            return redirect("attendance_list_view")

        # Create new attendance record
        Attendance.objects.create(
            employee=employee,
            project_id=project_id,
            location=location,
            attendance_status=attendance_status,
            login_time=now(),
            travel_in_time=travel_in_time,
            travel_out_time=travel_out_time,
            status="APPROVED",
            total_hours_of_work=0,
        )

        messages.success(request, "You have successfully logged in.")
        return redirect("attendance_list_view")

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

    return redirect("attendance_list_view")  # Redirect to the appropriate page

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
        "role": "Engineer"
    })

@login_required
def employee_update_travel_time(request, attendance_id):
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
    return render(request, 'employee/update_travel_time.html', {
        'attendance': attendance,
        'employees': employees,
        'projects': projects,
        "role": "Engineer"
    })

@login_required
def apply_leave(request):
    if request.method == 'POST':
        form = LeaveForm(request.POST,request.FILES)
        if form.is_valid():
            leave_application = form.save(commit=False)
            leave_application.user = request.user  # Assign current user
            leave_application.no_of_days = (leave_application.to_date - leave_application.from_date).days + 1  # Include both dates
            leave_application.save()

            messages.success(request, "Leave request submitted successfully.")
            return redirect('my_leave')  # Redirect after success

    else:
        form = LeaveForm()

    return render(request, 'employee/leave.html', {'form': form, "role": "Engineer",})

@login_required
def employee_upload_medical_certificate(request, leave_id):
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

            return redirect('my_leave')

    return redirect('my_leave')  # Redirect if GET request or no file uploaded


@login_required
def my_leave_status(request):
    """View to display the leave status of the logged-in user."""
    leaves_list = Leave.objects.filter(user=request.user).order_by("-from_date")
    
    # Paginate leave requests (10 per page)
    paginator = Paginator(leaves_list, 10)  
    page_number = request.GET.get("page")
    leaves = paginator.get_page(page_number)

    return render(request, "employee/leavestatus.html", {"leaves": leaves, "role": "Engineer",})

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

    return render(request, "employee/leaverecords.html", {"leave_summary": leave_summary, "role": "Engineer",})


@login_required
def employee_fetch_notifications(request):
    """Fetch unread notifications for the logged-in employee."""
    notifications = Notification.objects.filter(recipient=request.user, is_read=False).values("id", "message", "created_at")
    return JsonResponse({"notifications": list(notifications)})

@login_required
def employee_mark_notifications_as_read(request):
    """Mark all unread notifications for the logged-in employee as read."""
    notifications = Notification.objects.filter(recipient=request.user, is_read=False)
    
    if notifications.exists():
        notifications.update(is_read=True)  # Bulk update for efficiency
    
    return JsonResponse({"message": "Notifications marked as read"})


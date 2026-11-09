from Admin.leave_service import is_leave_v2_active
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render, get_object_or_404
from django.contrib.auth.models import User
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from calendar import monthrange
from django.utils.dateparse import parse_datetime
from django.utils.timezone import now
from django.utils.timezone import localtime
from django.template.loader import get_template
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse
from django.http import HttpResponse
from django.core.files.storage import default_storage
from weasyprint import HTML
from django.utils import timezone
from django.db.models import Sum
from datetime import date, time, timedelta
from django.contrib.staticfiles import finders
from collections import defaultdict
from decimal import Decimal
from finance.currency import CURRENCY_CHOICES
from pathlib import Path
import base64
import io
from PIL import Image
import json
import pytz
from datetime import datetime
from django.db.models import Q, F
from django.db.models import Prefetch
from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils.dateparse import parse_date, parse_time
from django.forms import modelformset_factory
from Admin.forms import ProjectAssignmentForm, WorkOrderForm, WorkOrderForm, WorkOrderDetailForm, SpareForm, ToolForm, DocumentForm, VesselForm, ProjectForm
from Admin.work_order_helpers import clean_time_rows, clean_quantity, apply_detail_dates
from Admin.upload_helpers import validate_project_attachment
from employee_data.forms import EmployeeUpdateForm, LeaveForm
from .forms import TeamForm
from django.contrib import messages
from django.http import JsonResponse
from Admin.models import Attendance, Project, Team, TeamMemberStatus, Employee, ActivityLog, Leave, LeaveBalance, Notification, ProjectAttachment, Holiday, WorkOrder, WorkOrderDetail, Spare, Tool, Document, Vessel, WorkOrderImage, WorkOrderTime, SpareConsumed
from service_logs.models import EmployeeServiceLog
import calendar
from Admin.attendance_autoclose import close_stale_sessions, capped_logout
from Admin.work_order_helpers import get_work_order_time_summary, _js
from Admin.work_order_sync import sync_work_order_from_project


# Home
@login_required
def dashboard(request):
    user = request.user

    if user.is_superuser:
        return render(request, 'Admin/dashboard.html')

    elif user.is_staff:
        manager = get_object_or_404(Employee, user=user)
        today = timezone.now().date()
        local_tz = pytz.timezone('Asia/Dubai')

        open_session_exists = Attendance.objects.filter(
            employee=manager,
            log_out_time__isnull=True,
            login_time__date=today
        ).exists()

        # Get today's latest attendance record (either open or last closed)
        today_attendance = Attendance.objects.filter(employee=manager, login_time__date=today).order_by('-login_time').first()


        last_punch_in = localtime(today_attendance.login_time).astimezone(local_tz).strftime('%I:%M %p') if today_attendance else "Not Punched In"
        last_punch_out = localtime(today_attendance.log_out_time).astimezone(local_tz).strftime('%I:%M %p') if today_attendance and today_attendance.log_out_time else "Not Punched Out"

        # Projects
        projects = Project.objects.filter(manager=manager)
        total_projects = projects.count()
        completed_projects = projects.filter(status='COMPLETED').count()
        pending_projects = projects.exclude(status='COMPLETED').count()
        assigned_projects = projects.filter(status='ASSIGN').order_by('-created_at')[:2]

        # Team count
        teams = Team.objects.filter(manager=manager)
        total_teams = teams.count()

        # Attendance (current month)
        current_year = today.year
        current_month = today.month
        first_day_this_month = today.replace(day=1)
        last_day_last_month = first_day_this_month - timedelta(days=1)
        first_day_last_month = last_day_last_month.replace(day=1)

        total_days_in_month = monthrange(current_year, current_month)[1]
        approved_attendance = Attendance.objects.filter(employee=manager, status='APPROVED')
        approved_attendance_this_month = approved_attendance.filter(
            login_time__date__gte=first_day_this_month,
            login_time__date__lte=today
        ).order_by('login_time')

        approved_attendance_last_month = approved_attendance.filter(
            login_time__date__gte=first_day_last_month,
            login_time__date__lt=first_day_this_month
        )

        # Working days logic
        def get_working_days(start, end):
            all_days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
            weekdays = [d for d in all_days if d.weekday() < 6]
            holidays = Holiday.objects.filter(date__range=(start, end)).values_list('date', flat=True)
            return len([d for d in weekdays if d not in holidays])

        working_days_this_month = get_working_days(first_day_this_month, today)
        working_days_last_month = get_working_days(first_day_last_month, last_day_last_month)

        # Full/Half day counts
        full_day_hours = 10
        half_day_min_hours = 5

        def calculate_attendance_percent(qs, working_days):
            full = qs.filter(total_hours_of_work__gte=full_day_hours).values('login_time__date').distinct().count()
            half = qs.filter(total_hours_of_work__gte=half_day_min_hours, total_hours_of_work__lt=full_day_hours).values('login_time__date').distinct().count()
            return round(((full + 0.5 * half) / working_days) * 100, 2) if working_days > 0 else 0

        attendance_percentage = calculate_attendance_percent(approved_attendance, get_working_days(manager.date_of_join or first_day_this_month, today))
        attendance_percentage_current_month = calculate_attendance_percent(approved_attendance_this_month, working_days_this_month)
        attendance_last_month_percent = calculate_attendance_percent(approved_attendance_last_month, working_days_last_month)

        # Growth calculation
        def calculate_growth(current, previous):
            if previous == 0:
                return 100 if current > 0 else 0
            return ((current - previous) / previous) * 100

        # Project Growth
        projects_this_month = projects.filter(created_at__gte=first_day_this_month).count()
        projects_last_month = projects.filter(created_at__gte=first_day_last_month, created_at__lt=first_day_this_month).count()
        project_growth_percentage = calculate_growth(projects_this_month, projects_last_month)

        pending_projects_this_month = projects.filter(status='ASSIGN', created_at__gte=first_day_this_month).count()
        pending_projects_last_month = projects.filter(status='ASSIGN', created_at__gte=first_day_last_month, created_at__lt=first_day_this_month).count()
        pending_growth_percentage = calculate_growth(pending_projects_this_month, pending_projects_last_month)

        completed_projects_this_month = projects.filter(status='COMPLETED', created_at__gte=first_day_this_month).count()
        completed_projects_last_month = projects.filter(status='COMPLETED', created_at__gte=first_day_last_month, created_at__lt=first_day_this_month).count()
        completed_growth_percentage = calculate_growth(completed_projects_this_month, completed_projects_last_month)

        attendance_growth_percentage = calculate_growth(attendance_percentage_current_month, attendance_last_month_percent)

        # Chart Data [new]
        first_login_per_day = {}
        for att in approved_attendance_this_month:
            day = att.login_time.date()
            if day not in first_login_per_day:
                first_login_per_day[day] = att  # only earliest login per day

        on_time_days = set()
        late_days = set()
        wfh_days = set()

        for att in first_login_per_day.values():
            if att.login_time:
                login_time_val = localtime(att.login_time).time()
                if login_time_val <= time(9, 15):
                    on_time_days.add(att.login_time.date())
                else:
                    late_days.add(att.login_time.date())
            if att.attendance_status == 'WORK FROM HOME':
                wfh_days.add(att.login_time.date())

        on_time_count = len(on_time_days)
        late_count = len(late_days)
        wfh_count = len(wfh_days)

        # Total employment working days
        employee = get_object_or_404(Employee, user=user)
        joining_date = employee.date_of_join or today.replace(month=1, day=1)
        all_dates = [joining_date + timedelta(days=i) for i in range((today - joining_date).days + 1)]
        weekdays = [d for d in all_dates if d.weekday() < 6]
        holidays = Holiday.objects.filter(date__range=(joining_date, today)).values_list('date', flat=True)
        total_employment_working_days = len([d for d in weekdays if d not in holidays])
                

        # Leave Balances
        leave_balance = LeaveBalance.objects.filter(user=user).first()
        annual_leave_taken = Leave.objects.filter(user=user, approval_status="APPROVED", leave_type="ANNUAL LEAVE").aggregate(total=Sum('no_of_days'))['total'] or 0
        sick_leave_taken = Leave.objects.filter(
            user=request.user,
            approval_status="APPROVED",
            leave_type="SICK LEAVE",
            from_date__gte=first_day_this_month,
            from_date__lte=today
        ).aggregate(total=Sum('no_of_days'))['total'] or 0

        balance_annual_leave = max((leave_balance.annual_leave if leave_balance else 0) - annual_leave_taken, 0)
        balance_sick_leave = max((leave_balance.sick_leave if leave_balance else 0) - sick_leave_taken, 0)

        # [New]
        absent_days_count = working_days_this_month - (
            on_time_count + late_count + wfh_count + sick_leave_taken
        )
        absent_days_count = max(absent_days_count, 0)

        # Define leave stats
        total_leaves = (leave_balance.annual_leave + leave_balance.sick_leave) if leave_balance else 0
        leaves_taken = annual_leave_taken + sick_leave_taken
        leave_requests = Leave.objects.filter(user=user, approval_status="PENDING").count()
        
        # Work & LOP days
        worked_days = manager.work_days
        loss_of_pay_days = leaves_taken - total_leaves if leaves_taken > total_leaves else 0

        months = [
            {"value": i, "name": calendar.month_name[i]}
            for i in range(1, 13)
        ]

        # ✅ Calculate total worked seconds for today (before context)
        today_records = Attendance.objects.filter(employee=manager, login_time__date=today)

        total_worked_seconds = 0
        for rec in today_records:
            if rec.login_time and rec.log_out_time:
                total_worked_seconds += int((rec.log_out_time - rec.login_time).total_seconds())

        open_session = today_records.filter(log_out_time__isnull=True).order_by('-login_time').first()

        context = {
            "role": "Manager",
            "months": months,
            "current_month": today.month,
            'manager': manager,
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
            'worked_days': round(worked_days, 2),
            'absent_days': absent_days_count,
            'loss_of_pay_days': loss_of_pay_days,
            "last_punch_in": last_punch_in,
            "last_punch_out": last_punch_out,
            "has_open_session": open_session_exists,
            'user_attendance': today_attendance,
            "current_time": timezone.now().astimezone(local_tz).strftime('%I:%M %p, %d %b %Y'),
            'chart_data': {
                'on_time': on_time_count,
                'late': late_count,
                'wfh': wfh_count,
                'absent': absent_days_count,
                'sick': sick_leave_taken,
            },

            'total_worked_seconds': total_worked_seconds,
            'open_login_time': open_session.login_time if open_session else None,

            'project_growth_percentage': round(projects_this_month - projects_last_month, 2),
            'pending_growth_percentage': abs(round(calculate_growth(pending_projects_this_month,pending_projects_last_month), 2)),
            'completed_growth_percentage': round(calculate_growth(completed_projects_this_month, completed_projects_last_month), 2),
            'attendance_growth_percentage': abs(round(attendance_growth_percentage, 2)),
            'attendance_growth_positive': attendance_growth_percentage >= 0,
        }

        return render(request, 'Manager/dashboard.html', context)

    return render(request, 'employee/employee_dashboard.html')  

def compute_presence_data(employee, year, month):
    user = employee.user
    today = timezone.now().date()

    # Date range for selected month
    first_day = date(year, month, 1)
    last_day = date(year, month, monthrange(year, month)[1])

    # If current month is selected, count only until today
    if year == today.year and month == today.month:
        last_day = today

    # Helper: Working days logic (Mon–Sat except holidays)
    def get_working_days(start, end):
        all_days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        weekdays = [d for d in all_days if d.weekday() < 6]
        holidays = set(Holiday.objects.filter(date__range=(start, end)).values_list('date', flat=True))
        return len([d for d in weekdays if d not in holidays])

    working_days_this_month = get_working_days(first_day, last_day)

    # Approved attendance for this month
    approved_attendance = Attendance.objects.filter(
        employee=employee,
        status="APPROVED",
        login_time__date__gte=first_day,
        login_time__date__lte=last_day
    ).order_by('login_time')

    # First login per day logic
    first_login_per_day = {}
    for att in approved_attendance:
        day = att.login_time.date()
        if day not in first_login_per_day:
            first_login_per_day[day] = att

    on_time_days = set()
    late_days = set()
    wfh_days = set()

    for att in first_login_per_day.values():
        if att.login_time:
            login_t = localtime(att.login_time).time()
            if login_t <= time(9, 15):
                on_time_days.add(att.login_time.date())
            else:
                late_days.add(att.login_time.date())
        if att.attendance_status == 'WORK FROM HOME':
            wfh_days.add(att.login_time.date())

    on_time_count = len(on_time_days)
    late_count = len(late_days)
    wfh_count = len(wfh_days)

    # Sick leave for that month
    sick_leave_taken = Leave.objects.filter(
        user=user,
        approval_status="APPROVED",
        leave_type="SICK LEAVE",
        from_date__gte=first_day,
        from_date__lte=last_day
    ).aggregate(total=Sum('no_of_days'))['total'] or 0

    # Absent days formula
    absent_days_count = working_days_this_month - (
        on_time_count + late_count + wfh_count + sick_leave_taken
    )
    absent_days_count = max(absent_days_count, 0)

    return {
        "on_time": on_time_count,
        "late": late_count,
        "wfh": wfh_count,
        "absent": absent_days_count,
        "sick": sick_leave_taken
    }


@login_required
def get_presence_data(request):
    try:
        # Month & year from request (default: current month/year)
        month = int(request.GET.get("month", date.today().month))
        year = int(request.GET.get("year", date.today().year))

        # Validate month/year
        if month < 1 or month > 12:
            return JsonResponse({"error": "Invalid month"}, status=400)

        employee = get_object_or_404(Employee, user=request.user)
        return JsonResponse(compute_presence_data(employee, year, month))

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def manager_profile(request):
    manager = get_object_or_404(Employee, user=request.user, is_manager=True)
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

    # --- HOLIDAYS AND WORKING DAYS ---
    def get_working_days(start, end):
        all_days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        weekdays = [d for d in all_days if d.weekday() < 6]
        holidays = set(Holiday.objects.filter(date__range=(start, end)).values_list('date', flat=True))
        return [d for d in weekdays if d not in holidays]

    # --- TEAM ATTENDANCE (ALL EMPLOYEES) ---
    team_attendance_qs = Attendance.objects.filter(
        employee__teams_assigned__manager=manager,
        status='APPROVED',
        login_time__date__range=(first_day_of_month, last_day_of_month)
    )

    full_day_hours = 10
    half_day_min_hours = 5

    team_full_days = team_attendance_qs.filter(total_hours_of_work__gte=full_day_hours).values('employee', 'login_time__date').distinct().count()
    team_half_days = team_attendance_qs.filter(total_hours_of_work__gte=half_day_min_hours, total_hours_of_work__lt=full_day_hours).values('employee', 'login_time__date').distinct().count()

    working_days = get_working_days(first_day_of_month, last_day_of_month)
    team_expected_days = len(working_days) * total_employees
    team_total_attendance = team_full_days + 0.5 * team_half_days
    team_attendance_percentage = round((team_total_attendance / team_expected_days) * 100, 2) if team_expected_days > 0 else 0

    # --- MANAGER PERSONAL ATTENDANCE ---
    if manager.date_of_join:
        work_start_date = max(manager.date_of_join, first_day_of_month)
    else:
        work_start_date = first_day_of_month
    working_days_manager = get_working_days(work_start_date, today)
    manager_attendance_qs = Attendance.objects.filter(employee=manager, status='APPROVED', login_time__date__gte=work_start_date)

    manager_full_days = manager_attendance_qs.filter(total_hours_of_work__gte=full_day_hours).values('login_time__date').distinct().count()
    manager_half_days = manager_attendance_qs.filter(total_hours_of_work__gte=half_day_min_hours, total_hours_of_work__lt=full_day_hours).values('login_time__date').distinct().count()
    total_working_days_manager = len(working_days_manager)

    manager_attendance_percentage = round(((manager_full_days + 0.5 * manager_half_days) / total_working_days_manager) * 100, 2) if total_working_days_manager > 0 else 0
    manager_attendance_ct = Attendance.objects.filter(employee=manager, status='APPROVED')
    overseas_attendance = manager_attendance_ct.filter(project__category="OVERSEAS").count()
    anchorage_attendance = manager_attendance_ct.filter(project__category="ANCHORAGE").count()
    at_berth_attendance = manager_attendance_ct.filter(project__category="AT_BERTH").count()

    # --- CONTEXT ---
    context = {
        "role": "Manager",
        "manager": manager,
        "teams": teams,
        "total_employees": total_employees,
        "completed_projects": completed_projects,
        "pending_projects": pending_projects,
        "managed_projects": managed_projects,
        "attendance_percentage_current_month": team_attendance_percentage,
        "manager_attendance_percentage": manager_attendance_percentage,

        "overseas_attendance": overseas_attendance,
        "anchorage_attendance": anchorage_attendance,
        "at_berth_attendance": at_berth_attendance,
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
            mgr = form.save(commit=False)
            mgr.personal_email = form.cleaned_data["email"]
            mgr.save()

            messages.success(request, "Your profile has been updated successfully.")
            return redirect("manager_profile")  # Redirect to manager profile view

    else:
        form = EmployeeUpdateForm(instance=manager, initial={
            "first_name": request.user.first_name,
            "last_name": request.user.last_name,
            "email": request.user.email,
        })

    return render(request, "Manager/manager_profile_update.html", {"form": form,"role": "Manager",})

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

@login_required
def manager_add_project(request):
    if not hasattr(request.user, 'employee_profile') or not request.user.employee_profile.is_manager:
        messages.error(request, "You are not authorized to add projects.")
        return redirect('custom-login')

    if request.method == 'POST':
        form = ProjectForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            with transaction.atomic():
                # Save Project
                project = form.save(commit=False)
                project.manager = request.user.employee_profile
                project.save()

                ActivityLog.objects.create(
                    project=project,
                    previous_status="Created",
                    new_status=project.status,
                    notes=f"Project '{project.name}' created with status '{project.status}'.",
                    changed_by=request.user.employee_profile,
                    changed_by_name=request.user.username
                )

                # Create Team only if team_name is provided
                team_name = form.cleaned_data.get('team_name')
                employees = form.cleaned_data.get('employees')

                if team_name:  
                    team = Team.objects.create(
                        name=team_name,
                        manager=project.manager,
                        project=project
                    )
                    if employees:
                        team.employees.set(employees)

                # Auto-create Work Order
                wo_fields = {
                    'imo_no': form.cleaned_data.get('imo_no'),
                    'location': form.cleaned_data.get('location'),
                    'assigned_date': form.cleaned_data.get('assigned_date'),
                    'job_scope': form.cleaned_data.get('job_scope'),
                    'job_instructions': form.cleaned_data.get('job_instructions'),
                }
                images = request.FILES.getlist('project_images') or request.FILES.getlist('work_order_images')
                work_order, created = sync_work_order_from_project(
                    project=project,
                    wo_fields=wo_fields,
                    images=images,
                    by_user=request.user
                )

            action_word = "created" if created else "updated"
            messages.success(request, f"Project saved. Work order {work_order.work_order_number} {action_word}.")
            return redirect('project_list')
        else:
            messages.error(request, "There was an error creating the project. Please check the form.")
    else:
        form = ProjectForm(user=request.user)

    return render(request, 'Manager/add_project.html', {'form': form, 'role': 'Manager'})
# Edit project
@login_required
def manager_edit_project(request, project_id):
    # Ensure project belongs to this manager
    project = get_object_or_404(Project, id=project_id, manager=request.user.employee_profile)

    if request.method == 'POST':
        form = ProjectForm(request.POST, request.FILES, instance=project)
        if form.is_valid():
            with transaction.atomic():
                project = form.save()

                wo_fields = {
                    'imo_no': form.cleaned_data.get('imo_no'),
                    'location': form.cleaned_data.get('location'),
                    'assigned_date': form.cleaned_data.get('assigned_date'),
                    'job_scope': form.cleaned_data.get('job_scope'),
                    'job_instructions': form.cleaned_data.get('job_instructions'),
                }
                images = request.FILES.getlist('project_images') or request.FILES.getlist('work_order_images')
                work_order, created = sync_work_order_from_project(
                    project=project,
                    wo_fields=wo_fields,
                    images=images,
                    by_user=request.user
                )

            action_word = "created" if created else "updated"
            messages.success(request, f"Project saved. Work order {work_order.work_order_number} {action_word}.")
            return redirect('project_list')
        else:
            messages.error(request, "There was an error updating the project. Please check the form.")
    else:
        form = ProjectForm(instance=project)

    return render(request, 'Manager/project_edit.html', {
        'form': form,
        'project': project,
        'role': 'Manager'
    })

# Delete project
@login_required
def manager_delete_project(request, project_id):
    # Ensure project belongs to this manager
    project = get_object_or_404(Project, id=project_id, manager=request.user.employee_profile)
    project.delete()
    messages.success(request, "Project deleted successfully!")
    return redirect('project_list')

# List all teams
@login_required
def team_list(request):
    search_query = request.GET.get('search', '').strip()

    # Get the currently logged-in manager's employee profile
    manager = get_object_or_404(Employee, user=request.user, is_manager=True)

    # Filter only the teams assigned to this manager
    if search_query:
        teams = Team.objects.filter(manager=manager, name__icontains=search_query)
    else:
        teams = Team.objects.filter(manager=manager).order_by('-created_at')

    # Pagination
    paginator = Paginator(teams, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'team/team_list.html', {
        'teams': page_obj,
        'search_query': search_query,
        "role": "Manager"
    })

# Show details of a team
@login_required
def team_detail(request, pk):
    team = get_object_or_404(Team, pk=pk)
    return render(request, 'team/team_detail.html', {'team': team, "role": "Manager",})

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

    return render(request, 'team/add_team.html', {'form': form, "role": "Manager",})

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
    return render(request, 'team/update_team.html', {'form': form, "role": "Manager",})

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

    if request.method == 'GET':
        messages.info(request, "Note: Attendance corrections are now managed under Attendance Status -> Team Requests.")

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

    return render(request, 'Manager/manage_attendance_requests.html', {'requests': pending_requests,"role": "Manager",})

@login_required
def project_assignment_create(request):
    if request.method == 'POST':
        form = ProjectAssignmentForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('manager-dashboard')
    else:
        form = ProjectAssignmentForm()
    return render(request, 'Manager/project_assignment_form.html', {'form': form, "role": "Manager",})

# Employee List
@login_required
@login_required
def manager_employee_list(request):
    from Admin.employee_views import render_employee_list
    return render_employee_list(request, role='manager', base_template='Manager/managerbase.html')

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
    
    for record in attendance_records:
        record.matching_teams = record.employee.teams_assigned.filter(project=record.project)

    # Paginate the attendance records
    paginator = Paginator(attendance_records, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Render the template with the filtered and paginated attendance records
    context = {
        "role": "Manager",
        'attendance_records': page_obj,
        'search_query': search_query,
    }
    return render(request, 'Manager/attendance_list.html', context)

@login_required
def attendance(request):
    from Admin.attendance_correction import render_role_attendance_status
    return render_role_attendance_status(request, "Manager", 'Manager/managerbase.html', template_name='Manager/attendance.html')

# Employee attendance details
@login_required
def attendance_detail(request, pk):
    attendance = get_object_or_404(Attendance, pk=pk)
    return render(request, 'Manager/attendance_detail.html', {'attendance': attendance, "role": "Manager",})

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

    return render(request, 'Manager/manage_attendance.html', {'requests': pending_requests,"role": "Manager",})

# Manage Leave
@login_required
def manage_leave(request):
    user_emp = getattr(request.user, 'employee_profile', None)
    if not (request.user.is_superuser or (user_emp and user_emp.is_manager)):
        raise PermissionDenied("You do not have permission to access this page.")
    if is_leave_v2_active():
        from Admin.leave_views import render_role_manage_leave
        return render_role_manage_leave(request, 'Manager', 'Manager/managerbase.html')
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

    return render(request, 'Manager/manage_leave.html', {'leaves': page_obj, "role": "Manager",})

# Leave list
@login_required
def leave_list(request):
    user_emp = getattr(request.user, 'employee_profile', None)
    if not (request.user.is_superuser or (user_emp and user_emp.is_manager)):
        raise PermissionDenied("You do not have permission to access this page.")
    if is_leave_v2_active():
        from Admin.leave_views import render_role_leave_list
        return render_role_leave_list(request, 'Manager', 'Manager/managerbase.html')
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
        "role": "Manager",
        'leave_records': page_obj,
        'search_query': search_query,
    }

    return render(request, 'Manager/leave_list.html', context)


# Employee profile
@login_required
def employee_profile(request, employee_id):
    from Admin.employee_views import render_employee_profile
    return render_employee_profile(request, employee_id, role='manager', base_template='Manager/managerbase.html')


@login_required
def project_list_view(request):
    current_user = request.user
    employee = current_user.employee_profile

    # Get search query
    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '')

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

        # Apply status filter
        if status_filter:
            projects = projects.filter(status=status_filter)

        # Convert to dictionary values for efficiency
        projects = projects.values(
            "id", "name", "category", "code", "vessel_name", "status",
            "invoice_amount", "currency_code", "purchase_and_expenses"
        )
    else:
        projects = []

    # Pagination (10 projects per page)
    paginator = Paginator(projects, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        "role": "Manager",
        "projects": page_obj,
        "search_query": search_query,  # Send search query back to template
        "status_filter": status_filter,
        "status_choices": Project.STATUS_CHOICES,
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

    team = project.teams.first()

    # Merge both logs and order by `changed_at`
    logs = (team_logs | manager_logs).distinct().order_by('-changed_at')

    MAX_UPLOAD_SIZE = 49 * 1024 * 1024  # 49 MB in bytes

    # Handle multiple file uploads
    if request.method == "POST":
        if request.FILES.getlist("attachments"):
            uploaded_files = request.FILES.getlist("attachments")
            for file in uploaded_files:
                err = validate_project_attachment(file)
                if err:
                    messages.error(request, err)
                    continue

                ProjectAttachment.objects.create(
                    project=project,
                    file=file,
                    uploaded_by=employee
                )

                ActivityLog.objects.create(
                    project=project,
                    previous_status="No Attachment",
                    new_status="Attachment Uploaded",
                    notes=f"{file.name} uploaded by manager.",
                    changed_by=employee
                )

        elif request.FILES.get("job_card"):
            job_card_file = request.FILES["job_card"]
            project.job_card = job_card_file
            project.save()

    # Statuses and team setup
    statuses = TeamMemberStatus.objects.filter(team__project=project).order_by('-last_updated')
    status_choices = TeamMemberStatus.STATUS_CHOICES
    teams = project.teams.all()
    attendance_qs = Attendance.objects.filter(project=project).select_related("employee", "employee__user")
    service_logs_qs = EmployeeServiceLog.objects.filter(project=project).select_related("employee", "employee__user")

    engineers = []
    engineer_salaries = {}
    total_engineer_salary = Decimal(0)
    engineer_project_hours = {}

    for log in service_logs_qs:
        emp = log.employee
        if emp and emp.user:
            username = emp.user.username
            if username not in engineers:
                engineers.append(username)
                engineer_salaries[username] = emp.salary or Decimal(0)
                total_engineer_salary += (emp.salary or Decimal(0))
                engineer_project_hours[username] = Decimal(0)
            engineer_project_hours[username] += round(Decimal(log.total_hours or 0), 2)

    for record in attendance_qs:
        emp = record.employee
        if emp and emp.user:
            username = emp.user.username
            if username not in engineers:
                engineers.append(username)
                engineer_salaries[username] = emp.salary or Decimal(0)
                total_engineer_salary += (emp.salary or Decimal(0))
                engineer_project_hours[username] = Decimal(0)
            engineer_project_hours[username] += round(Decimal(record.total_hours_of_work or 0), 2)

    # Include assigned team employees if not already in list
    for team_obj in teams:
        for emp in team_obj.employees.all():
            if emp and emp.user and emp.user.username not in engineers:
                engineers.append(emp.user.username)
                engineer_salaries[emp.user.username] = emp.salary or Decimal(0)
                total_engineer_salary += (emp.salary or Decimal(0))
                engineer_project_hours[emp.user.username] = Decimal(0)

    # Calculate Total Work Days
    log_dates = set(service_logs_qs.order_by().values_list("date", flat=True).distinct())
    att_dates = set(a.login_time.date() for a in attendance_qs if a.login_time)
    worked_dates = log_dates.union(att_dates)

    if worked_dates:
        work_days = len(worked_dates)
    else:
        work_days = project.calculate_total_work_days()

    # Get all attachments
    attachments = ProjectAttachment.objects.filter(project=project)
    try:
        work_order = project.workorder  # OneToOne relation via related_name
    except WorkOrder.DoesNotExist:
        work_order = None

    # Get completed date (latest COMPLETED status)
    completed_log = (
        logs.filter(new_status="COMPLETED")
            .order_by("-changed_at")
            .first()
    )

    completed_date = completed_log.changed_at if completed_log else None

    project_data = {
        "project_name": project.name,
        "client_name": project.client_name,
        "vessel_name": project.vessel_name,
        "service_reference": project.service_reference,
        "project_manager": project.manager,
        "code": project.code,
        "category": project.category,
        "engineers": engineers,
        "engineer_salaries": engineer_salaries,
        "engineer_project_hours": engineer_project_hours,
        "total_work_days": work_days,
        "currency_code": project.currency_code,
        "priority": project.priority,
        "status": project.status,
        "completed_date": completed_date,
        "total_engineer_salary": round(total_engineer_salary, 2),
        "project_create": project.created_at,
        "deadline_date": project.deadline_date,
        "statuses": statuses,
        "logs": logs,
        "project_id": project.id,
        "work_order": work_order,
        "status_choices": status_choices,
        "attachments": attachments,
        "job_card": project.job_card.url if project.job_card else None,
        "service_reference": project.service_reference,
        "project": project,
    }

    context = {"project_data": project_data, "team": team, "role": "Manager"}
    return render(request, 'Manager/project.html', context)

# project attachments
@login_required
def project_attachments_view(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    attachments = ProjectAttachment.objects.filter(project=project).order_by('-uploaded_at')

    context = {
        "role": "Manager",
        'project': project,
        'attachments': attachments,
    }
    return render(request, 'Manager/project_attachments.html', context)

@login_required
def delete_project_attachment(request, attachment_id):
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
        return redirect('project_attachments_view', project_id=attachment.project.id)

    # Perform deletion
    project_id = attachment.project.id
    attachment.file.delete()
    attachment.delete()
    messages.success(request, "Attachment deleted successfully.")
    return redirect('project_attachments_view', project_id=project_id)

# manage project
@login_required
def manage_project_status(request):
    try:
        manager = request.user.employee_profile
    except Employee.DoesNotExist:
        return redirect('no_employee_profile')

    if not manager.is_manager:
        return redirect('no_permission_page')

    # Get all teams managed by this manager
    teams = manager.managed_teams.prefetch_related('team_members_status')

    # Get all team member statuses with status 'COMPLETED' and not yet approved/rejected
    pending_statuses = TeamMemberStatus.objects.filter(
        team__in=teams,
        manager_approval_status='PENDING'
    ).select_related('employee', 'team', 'team__project').order_by('-last_updated')

    if request.method == 'POST':
        tms_id = request.POST.get('tms_id')
        action = request.POST.get('action')  # APPROVE or REJECT
        rejection_reason = request.POST.get('rejection_reason', '')

        try:
            tms = TeamMemberStatus.objects.get(id=tms_id)

            if tms.team not in teams:
                messages.error(request, "You are not authorized to manage this status.")
                return redirect('manage_project_status')

            if action == 'APPROVE':
                tms.manager_approval_status = 'APPROVED'
                messages.success(request, f"Status for {tms.employee.user.username} approved.")
            elif action == 'REJECT':
                tms.manager_approval_status = 'REJECTED'
                tms.rejection_reason = rejection_reason
                tms.status = 'ONGOING'  # Rollback status or choose appropriate fallback
                messages.success(request, f"Status for {tms.employee.user.username} rejected.")
            else:
                messages.error(request, "Invalid action.")

            tms.save()

            # Notify employee
            Notification.objects.create(
                recipient=tms.employee.user,
                message=f"Your project status '{tms.status}' was {tms.manager_approval_status.lower()} by the manager."
            )

        except TeamMemberStatus.DoesNotExist:
            messages.error(request, "Team member status not found.")

        return redirect('manage_project_status')

    return render(request, 'Manager/manage_project_status.html', {
        'pending_statuses': pending_statuses, "role": "Manager",
    })

@login_required
def update_team_manager_status(request, project_id):
    if request.method == "POST":
        employee = get_object_or_404(Employee, user=request.user)
        status = request.POST.get("status")
        remark = request.POST.get("remark", "")
        purchase_and_expenses = request.POST.get("invoice_amount")
        currency_code = request.POST.get("currency_code")

        # Validate status
        if not status:
            messages.error(request, "Please select a status before updating.")
            return redirect('project-summary-view', project_id=project_id)

        project = get_object_or_404(Project, id=project_id, manager=employee)

        previous_status = project.status
        project.status = status

        # Save invoice info if status is completed
        if status == "COMPLETED":
            if purchase_and_expenses:
                project.purchase_and_expenses = purchase_and_expenses
            else:
                project.purchase_and_expenses = 0
            if currency_code and currency_code in dict(CURRENCY_CHOICES):
                project.currency_code = currency_code

        project.save()

        ActivityLog.objects.create(
            project=project,
            previous_status=previous_status,
            new_status=status,
            notes=remark,
            changed_by=employee
        )

        messages.success(request, "Project status updated successfully.")
    else:
        messages.error(request, "Invalid request method.")

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
        attendance.project_id = request.POST.get('project') or None
        attendance.location = request.POST.get('location') or None
        attendance.attendance_status = request.POST.get('attendance_status') or None
        attendance.status = request.POST.get('status', 'default_status')  # Default value for status if not provided
        attendance.rejection_reason = request.POST.get('rejection_reason') or ''

        attendance.save()

        messages.success(request, 'Attendance record has been updated successfully.')
        return redirect('attendance_list')

    employees = Employee.objects.all()
    projects = Project.objects.all()
    return render(request, 'Manager/manager_edit_attendance.html', {
        'attendance': attendance,
        'employees': employees,
        'projects': projects,
        "role": "Manager"
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
        "role": "Manager"
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
    if is_leave_v2_active():
        from Admin.leave_views import render_role_apply_leave
        return render_role_apply_leave(request, 'Manager', 'Manager/managerbase.html', 'manager_leave_status')
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

    return render(request, 'Manager/leave.html', {'form': form, "role": "Manager",})

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
    if is_leave_v2_active():
        from Admin.leave_views import render_role_leave_status
        return render_role_leave_status(request, 'Manager', 'Manager/managerbase.html')
    leaves_list = Leave.objects.filter(user=request.user).order_by("-from_date")
    
    # Paginate leave requests (10 per page)
    paginator = Paginator(leaves_list, 10)  
    page_number = request.GET.get("page")
    leaves = paginator.get_page(page_number)

    return render(request, "Manager/leavestatus.html", {"leaves": leaves,"role": "Manager",})

@login_required
def manager_leave_records(request):
    """View to display the leave records for the logged-in user."""
    if is_leave_v2_active():
        from Admin.leave_views import render_role_leave_statement
        return render_role_leave_statement(request, 'Manager', 'Manager/managerbase.html')

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

    return render(request, "Manager/leaverecords.html", {"leave_summary": leave_summary, "role": "Manager",})


@login_required
def manager_log_in(request):
    if request.method == "POST":
        employee = get_object_or_404(Employee, user=request.user)
        stale_closed = close_stale_sessions(employee)
        if stale_closed:
            for d in getattr(stale_closed, 'dates', []):
                messages.warning(request, f"Your session from {d} was not punched out and was closed automatically at 12:00 AM.")

        project_id = request.POST.get("project")
        location = request.POST.get("location")
        attendance_status = request.POST.get("attendance_status")
        vessel_id = request.POST.get("vessel")  # Get selected vessel ID

        # Retrieve travel times
        travel_in_time_str = request.POST.get("travel_in_time")
        travel_out_time_str = request.POST.get("travel_out_time")
        travel_in_time = parse_datetime(travel_in_time_str) if travel_in_time_str else None
        travel_out_time = parse_datetime(travel_out_time_str) if travel_out_time_str else None

        # Close any same-day open sessions with now()
        open_attendance = Attendance.objects.filter(employee=employee, log_out_time__isnull=True)
        for record in open_attendance:
            record.log_out_time = now()
            record.save()

        # Create a new login record (no get_or_create restriction now)
        Attendance.objects.create(
            employee=employee,
            project_id=project_id,
            location=location,
            attendance_status=attendance_status,
            login_time=now(),
            travel_in_time=travel_in_time,
            travel_out_time=travel_out_time,
            status="APPROVED",
            vessel_id=vessel_id
        )

        messages.success(request, "You have successfully logged in for this session.")
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
            logout_dt, was_capped = capped_logout(attendance)
            attendance.log_out_time = logout_dt
            if was_capped:
                attendance.auto_punched_out = True
            attendance.save()
            if was_capped:
                capped_date = timezone.localtime(logout_dt).date()
                messages.warning(
                    request,
                    f"This session was closed at 12:00 AM on {capped_date} because the punch-out was after midnight."
                )
            else:
                messages.success(request, "You have successfully logged off.")
        else:
            messages.error(request, "You have already logged off.")

    return redirect("manager-dashboard")  # Redirect to the appropriate page

@login_required
def manager_render_attendance_page(request):
    employee = get_object_or_404(Employee, user=request.user)
    stale_closed = close_stale_sessions(employee)
    if stale_closed:
        for d in getattr(stale_closed, 'dates', []):
            messages.warning(request, f"Your session from {d} was not punched out and was closed automatically at 12:00 AM.")

    user_attendance = Attendance.objects.filter(
        employee=employee, 
        log_out_time__isnull=True
    ).order_by('-login_time').first()

    attendance_status_choices = Attendance.ATTENDANCE_STATUS
    location_choices = Attendance.LOCATION_CHOICES
    projects = Project.objects.all()
    vessels = Vessel.objects.all()

    return render(request, "Manager/punchin.html", {
        "attendance_status_choices": attendance_status_choices,
        "location_choices": location_choices,
        "projects": projects,
        "vessels": vessels,
        "user_attendance": user_attendance,
        "role": "Manager"
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


@login_required
def create_work_order_view(request, project_id):
    if not hasattr(request.user, 'employee_profile') or not request.user.employee_profile.is_manager:
        return redirect('custom-login')

    project = get_object_or_404(Project, id=project_id)
    try:
        work_order = project.workorder
    except (WorkOrder.DoesNotExist, AttributeError):
        work_order = WorkOrder.objects.filter(project=project).first()

    if work_order:
        return redirect('view_work_order', pk=work_order.pk)

    # Old project without work order -> create it with the helper from project's data (assigned_date = today)
    with transaction.atomic():
        work_order, _ = sync_work_order_from_project(
            project=project,
            wo_fields={'assigned_date': timezone.now().date()},
            by_user=request.user
        )

    messages.info(request, "Work order created. Please complete IMO number, location, and job scope.")
    return redirect('manager-update-work-order', pk=work_order.pk)


@login_required
def view_work_order(request, pk):
    work_order = get_object_or_404(WorkOrder.objects.select_related('project'), pk=pk)
    work_order_detail = WorkOrderDetail.objects.filter(work_order=work_order).first()

    live_members = []
    if work_order.project:
        if hasattr(work_order.project, "teams"):  # many teams
            live_members = [
                emp.user for team in work_order.project.teams.all()
                for emp in team.employees.all()
            ]
        elif hasattr(work_order.project, "team"):  # single team
            live_members = [emp.user for emp in work_order.project.team.employees.all()]

    # FIX: use the correct field
    all_members = work_order.all_members.all()

    time_entries, member_totals, total_hours = get_work_order_time_summary(work_order)

    from service_logs.models import EmployeeServiceLog
    try:
        _viewer_emp = request.user.employee_profile
    except Exception:
        _viewer_emp = None

    # Manager sees all logs; others see only their own
    _sl_qs = EmployeeServiceLog.objects.filter(
        time_log__work_order=work_order
    ).select_related('employee', 'employee__user').order_by('date', 'start_time')
    if not (_viewer_emp and _viewer_emp.is_manager):
        _sl_qs = _sl_qs.filter(employee=_viewer_emp) if _viewer_emp else _sl_qs.none()

    return render(request, 'Manager/view_work_order.html', {
        'work_order': work_order,
        'work_order_detail': work_order_detail,
        'live_members': live_members,
        'all_members': all_members,
        'time_entries': time_entries,
        'member_totals': member_totals,
        'calculated_hours': total_hours,
        'service_logs': _sl_qs,
    })


@login_required
def manager_update_work_order_view(request, pk):
    work_order = get_object_or_404(WorkOrder, pk=pk)

    try:
        manager_emp = request.user.employee_profile
    except Exception:
        manager_emp = None

    if not manager_emp:
        messages.error(request, "Your user has no employee profile — ask Admin to create one.")
        return redirect('view_work_order', pk=pk)

    work_order_detail, _ = WorkOrderDetail.objects.get_or_create(work_order=work_order)

    # Load allowed employees once (active employees only for assignment)
    emps = {
        e.id: e
        for e in Employee.objects.filter(
            Q(user__in=work_order.job_assigned_to.all()) | Q(pk=manager_emp.pk)
        ).filter(user__is_active=True).select_related('user')
    }

    # Allowed for save includes emps PLUS every employee who already has a WorkOrderTime row
    existing_time_emp_ids = set(
        WorkOrderTime.objects.filter(work_order=work_order, employee__isnull=False)
        .values_list('employee_id', flat=True)
    )
    existing_emps = {
        e.id: e
        for e in Employee.objects.filter(id__in=existing_time_emp_ids).select_related('user')
    }
    allowed_for_save = {**emps, **existing_emps}

    if request.method == 'POST':
        try:
            with transaction.atomic():
                saved_vessel = work_order.vessel
                saved_client = work_order.client
                saved_imo_no = work_order.imo_no
                saved_location = work_order.location
                saved_assigned_date = work_order.assigned_date
                saved_job_scope = work_order.job_scope
                saved_job_instructions = work_order.job_instructions
                saved_project_description = work_order.project_description

                form = WorkOrderForm(request.POST, request.FILES, instance=work_order)

                if form.is_valid():
                    work_order = form.save(commit=False)

                    # Header fields are read-only here and managed only on the project
                    work_order.vessel = saved_vessel
                    work_order.client = saved_client
                    work_order.imo_no = saved_imo_no
                    work_order.location = saved_location
                    work_order.assigned_date = saved_assigned_date
                    work_order.project_description = saved_project_description

                    scope_changed = False
                    instruction_changed = False

                    if 'job_scope' in request.POST:
                        new_job_scope = request.POST.get('job_scope', '').strip()
                        if (saved_job_scope or '').strip() != new_job_scope:
                            scope_changed = True
                        work_order.job_scope = new_job_scope
                    else:
                        work_order.job_scope = saved_job_scope

                    if 'job_instructions' in request.POST:
                        new_job_instructions = request.POST.get('job_instructions', '').strip()
                        if (saved_job_instructions or '').strip() != new_job_instructions:
                            instruction_changed = True
                        work_order.job_instructions = new_job_instructions
                    else:
                        work_order.job_instructions = saved_job_instructions

                    work_order.save()

                    if work_order.project:
                        changed_by = getattr(request.user, 'employee_profile', None)
                        changed_by_name = request.user.username
                        if scope_changed:
                            ActivityLog.objects.create(
                                project=work_order.project,
                                previous_status="Work Order",
                                new_status="Job Scope Updated",
                                notes=f"Job Scope updated on work order by {request.user.username}",
                                changed_by=changed_by,
                                changed_by_name=changed_by_name,
                            )
                        if instruction_changed:
                            ActivityLog.objects.create(
                                project=work_order.project,
                                previous_status="Work Order",
                                new_status="Job Instruction Updated",
                                notes=f"Job Instruction updated on work order by {request.user.username}",
                                changed_by=changed_by,
                                changed_by_name=changed_by_name,
                            )

                    _wo_warnings = []
                    apply_detail_dates(work_order_detail, request.POST, _wo_warnings)

                    work_order_detail.save()

                    # ---------------------------
                    # Sync WorkOrderTime entries in place (preserving submitted/approved rows)
                    # ---------------------------
                    from Admin.work_order_helpers import sync_work_order_times
                    _action = request.POST.get('action', 'save_draft')
                    time_employees = request.POST.getlist('time_employee[]') or request.POST.getlist('time_employee')
                    time_dates = request.POST.getlist('time_date[]') or request.POST.getlist('time_date')
                    time_starts = request.POST.getlist('start_time[]') or request.POST.getlist('start_time')
                    time_finishes = request.POST.getlist('finish_time[]') or request.POST.getlist('finish_time')

                    # Save multiple WorkOrderTime rows
                    _time_rows, _time_warnings = clean_time_rows(
                        time_dates,
                        time_starts,
                        time_finishes,
                        time_employees,
                        row_ids=request.POST.getlist('time_row_id[]'),
                        break_minutes=request.POST.getlist('break_minutes[]'),
                        travel_minutes=request.POST.getlist('travel_minutes[]'),
                        descriptions=request.POST.getlist('time_description[]'),
                        attachments=request.FILES.getlist('time_attachment[]'),
                    )
                    _wo_warnings.extend(_time_warnings)
                    _new_time_logs = sync_work_order_times(
                        work_order=work_order,
                        time_rows=_time_rows,
                        allowed_for_save=allowed_for_save,
                        default_employee=manager_emp,
                        action=_action,
                        user=request.user,
                        warnings=_wo_warnings,
                    )

                    # ---------------------------
                    # Clear & re-save Spares, Tools, Documents
                    # ---------------------------
                    Spare.objects.filter(work_order=work_order).delete()
                    SpareConsumed.objects.filter(work_order=work_order).delete()
                    Tool.objects.filter(work_order=work_order).delete()
                    Document.objects.filter(work_order=work_order).delete()

                    # Save Spares
                    for name, unit, qty in zip(
                        request.POST.getlist('spare_name[]'),
                        request.POST.getlist('spare_unit[]'),
                        request.POST.getlist('spare_quantity[]')
                    ):
                        if name.strip():
                            _q = clean_quantity(qty, f"Spare '{name.strip()}'", _wo_warnings)
                            if _q is None:
                                continue
                            Spare.objects.create(
                                work_order=work_order,
                                name=name.strip(),
                                unit=unit.strip(),
                                quantity=_q
                            )
                    
                    # Save new consumed spares
                    for name, unit, qty in zip(
                        request.POST.getlist('consumed_spare_name[]'),
                        request.POST.getlist('consumed_spare_unit[]'),
                        request.POST.getlist('consumed_spare_quantity[]')
                    ):
                        if name.strip():
                            _q = clean_quantity(qty, f"Consumed spare '{name.strip()}'", _wo_warnings)
                            if _q is None:
                                continue
                            SpareConsumed.objects.create(
                                work_order=work_order,
                                name=name.strip(),
                                unit=unit.strip(),
                                quantity=_q
                            )

                    # Save Tools
                    for name, qty in zip(
                        request.POST.getlist('tool_name[]'),
                        request.POST.getlist('tool_quantity[]')
                    ):
                        if name.strip():
                            _q = clean_quantity(qty, f"Tool '{name.strip()}'", _wo_warnings)
                            if _q is None:
                                continue
                            Tool.objects.create(
                                work_order=work_order,
                                name=name.strip(),
                                quantity=_q
                            )

                    # Save Documents
                    for name, status in zip(
                        request.POST.getlist('doc_name[]'),
                        request.POST.getlist('doc_status[]')
                    ):
                        if name.strip():
                            Document.objects.create(
                                work_order=work_order,
                                name=name.strip(),
                                status=status.strip()
                            )

                    # ---------------------------
                    # Update existing image name & description
                    # ---------------------------
                    for key, value in request.POST.items():
                        if key.startswith('existing_image_names['):
                            img_id = key.replace('existing_image_names[', '').replace(']', '')

                            try:
                                img = WorkOrderImage.objects.get(id=img_id, work_order=work_order)
                                img.name = value.strip() or img.name

                                desc_key = f'existing_image_descriptions[{img_id}]'
                                img.description = request.POST.get(desc_key, '').strip()

                                img.save()
                            except WorkOrderImage.DoesNotExist:
                                pass
                            
                        
                    # Save uploaded project images
                    image_names = request.POST.getlist('image_names[]')
                    image_descriptions = request.POST.getlist('image_descriptions[]')
                    all_files = request.FILES.getlist('project_images[]')

                    file_cursor = 0

                    for name, desc in zip(image_names, image_descriptions):
                        # Each row may have multiple images selected
                        row_files = all_files[file_cursor:]

                        for f in row_files:
                            WorkOrderImage.objects.create(
                                work_order=work_order,
                                image=f,
                                name=name.strip() or "Untitled Image",
                                description=desc.strip()
                            )

                        file_cursor += len(row_files)


                    # Delete selected images
                    delete_ids = request.POST.getlist('delete_image_ids')
                    if delete_ids:
                        WorkOrderImage.objects.filter(id__in=delete_ids, work_order=work_order).delete()

                    messages.success(request, "Work order updated successfully.")
                    for _w in _wo_warnings:
                        messages.warning(request, _w)
                    if _new_time_logs:
                        messages.info(request, "Service Log entries updated.")
                    return redirect('view_work_order', pk=pk)

                else:
                    messages.error(request, "Work Order form has errors.")

        except Exception as e:
            messages.error(request, f"Error saving work order: {str(e)}")

    else:
        form = WorkOrderForm(instance=work_order)
        try:
            form.fields['vessel'].initial = (
                work_order.project.vessel_name
                if work_order.project and work_order.project.vessel_name
                else ""
            )
        except AttributeError:
            form.fields['vessel'].initial = ""

    manager_name = request.user.get_full_name() or request.user.username
    assigned_members = [
        {'id': manager_emp.id, 'name': f"{manager_name} (Manager)"}
    ]
    seen_emp_ids = {manager_emp.id}

    user_to_emp = {e.user_id: e for e in emps.values()}
    for u in work_order.job_assigned_to.filter(is_active=True):
        emp = user_to_emp.get(u.id)
        if emp and emp.id not in seen_emp_ids:
            seen_emp_ids.add(emp.id)
            name = u.get_full_name() or u.username
            assigned_members.append({'id': emp.id, 'name': name})

    times_data = []
    assigned_member_ids = {m['id'] for m in assigned_members}
    for t in WorkOrderTime.objects.filter(work_order=work_order).select_related('employee__user', 'service_log'):
        name = ''
        extra_member = None
        if t.employee and t.employee.user:
            name = t.employee.user.get_full_name() or t.employee.user.username
            if t.employee_id not in assigned_member_ids:
                if not t.employee.user.is_active:
                    label = f"{name} (inactive)"
                else:
                    label = f"{name} (not on team)"
                extra_member = {'id': t.employee_id, 'name': label}
        slog = getattr(t, 'service_log', None)
        times_data.append({
            'id': t.id,
            'date': t.date.isoformat() if t.date else '',
            'start_time': t.start_time.strftime('%H:%M') if t.start_time else '',
            'finish_time': t.finish_time.strftime('%H:%M') if t.finish_time else '',
            'break_minutes': t.break_minutes or 0,
            'travel_minutes': t.travel_minutes or 0,
            'description': t.description or '',
            'attachment_url': t.attachment.url if t.attachment else '',
            'status': getattr(slog, 'approval_status', 'DRAFT'),
            'remarks': getattr(slog, 'decision_remarks', '') or '',
            'employee_id': t.employee_id or '',
            'member_name': extra_member['name'] if extra_member else name,
            'extra_member': extra_member,
        })

    context = {
        'form': form,
        'work_order': work_order,
        'work_order_detail': work_order_detail,
        'time_logs': WorkOrderTime.objects.filter(work_order=work_order),
        'times': _js(times_data),
        'assigned_members': _js(assigned_members),
        'manager_emp_id': manager_emp.id,
        'spares': _js(list(Spare.objects.filter(work_order=work_order).values())),
        'spares_consumed': _js(
            list(SpareConsumed.objects.filter(work_order=work_order).values(
                'name', 'unit', 'quantity'
            ))
        ),
        'tools': _js(list(Tool.objects.filter(work_order=work_order).values())),
        'documents': _js(list(Document.objects.filter(work_order=work_order).values())),
    }

    return render(request, 'Manager/update_work_order.html', context)


# @login_required
# def download_work_order_pdf(request, pk):
#     work_order = get_object_or_404(WorkOrder, pk=pk)
#     work_order_detail = WorkOrderDetail.objects.filter(work_order=work_order).first()

#     # Get the full static file path
#     logo_path = finders.find('assets/images/reportlogo.png')

#     # Encode logo in base64
#     with open(logo_path, 'rb') as img_file:
#         logo_data = base64.b64encode(img_file.read()).decode()

#     # Separate Live vs All Members
#     live_members = []
#     if work_order.project:
#         if hasattr(work_order.project, "teams"):  # many teams
#             live_members = [
#                 emp.user for team in work_order.project.teams.all()
#                 for emp in team.employees.all()
#             ]
#         elif hasattr(work_order.project, "team"):  # single team
#             live_members = [emp.user for emp in work_order.project.team.employees.all()]
#     all_members = work_order.all_members.all()

#     # Convert project images to base64
#     image_groups = defaultdict(list)

#     for img in work_order.images.all():
#         if not img.image or not default_storage.exists(img.image.name):
#             continue  # skip missing files

#         with default_storage.open(img.image.name, 'rb') as f:
#             base64_data = base64.b64encode(f.read()).decode('ascii')

#         ext = Path(img.image.name).suffix.lower()
#         mime = 'jpeg' if ext in ['.jpg', '.jpeg'] else 'png'

#         image_groups[img.name].append({
#             'base64': base64_data,
#             'mime': mime,
#             'description': img.description,
#         })

#     total_hours = (
#         WorkOrderTime.objects.filter(work_order=work_order)
#         .aggregate(total=Sum('estimated_hours'))['total'] or 0
#     )

#     # Convert Decimal to float for template rendering
#     total_hours = float(total_hours)

#     html_string = render_to_string('Manager/work_order_pdf.html', {
#         'work_order': work_order,
#         'work_order_detail': work_order_detail,
#         'logo_base64': logo_data,
#         'live_members': live_members,
#         'all_members': all_members,
#         'calculated_hours': total_hours,
#         'image_groups': dict(image_groups),
#     })

#     html = HTML(string=html_string)
#     pdf = html.write_pdf()

#     response = HttpResponse(pdf, content_type='application/pdf')
#     response['Content-Disposition'] = (
#         f'attachment; filename="work_order_{work_order.work_order_number}.pdf"'
#     )
#     return response


@login_required
def download_work_order_pdf(request, pk):
    work_order = get_object_or_404(WorkOrder.objects.select_related('project'), pk=pk)
    work_order_detail = WorkOrderDetail.objects.filter(work_order=work_order).first()

    # Logo (safe)
    logo_path = finders.find('assets/images/reportlogo.png')
    logo_data = ""
    if logo_path:
        with open(logo_path, 'rb') as img_file:
            raw = img_file.read()
            if raw:
                logo_data = base64.b64encode(raw).decode()

    # Members
    live_members = []
    if work_order.project and hasattr(work_order.project, "teams"):
        live_members = [
            emp.user
            for team in work_order.project.teams.all()
            for emp in team.employees.all()
        ]

    all_members = work_order.all_members.all()

    # Images — resize to save memory and prevent OOM / 504 timeouts
    # Original images can be 5+ MB each; resizing to max 800px and JPEG 70%
    # reduces ~155 MB of raw data down to ~2-3 MB total.
    PDF_IMAGE_MAX_SIZE = (800, 800)   # max width/height in pixels
    PDF_IMAGE_QUALITY = 70            # JPEG quality (1-100)

    image_groups = defaultdict(list)

    for img in work_order.images.all():
        if not img.image or not default_storage.exists(img.image.name):
            continue

        try:
            with default_storage.open(img.image.name, 'rb') as f:
                pil_img = Image.open(f)
                pil_img.load()  # force read so we can close the file

            # Convert RGBA/P to RGB for JPEG output
            if pil_img.mode in ('RGBA', 'P'):
                pil_img = pil_img.convert('RGB')

            # Resize only if larger than the max
            pil_img.thumbnail(PDF_IMAGE_MAX_SIZE, Image.LANCZOS)

            # Write resized image to an in-memory buffer as JPEG
            buf = io.BytesIO()
            pil_img.save(buf, format='JPEG', quality=PDF_IMAGE_QUALITY, optimize=True)
            buf.seek(0)

            base64_data = base64.b64encode(buf.getvalue()).decode('ascii')
            buf.close()
        except Exception:
            # Skip any image that can't be processed
            continue

        image_groups[img.name].append({
            'base64': base64_data,
            'mime': 'jpeg',
            'description': img.description,
        })

    time_entries, member_totals, total_hours = get_work_order_time_summary(work_order)

    html_string = render_to_string(
        'Manager/work_order_pdf.html',
        {
            'work_order': work_order,
            'work_order_detail': work_order_detail,
            'logo_base64': logo_data,
            'live_members': live_members,
            'all_members': all_members,
            'time_entries': time_entries,
            'member_totals': member_totals,
            'calculated_hours': float(total_hours),
            'image_groups': dict(image_groups),
        }
    )

    try:
        html = HTML(
            string=html_string,
            base_url=request.build_absolute_uri('/')
        )
        pdf = html.write_pdf()
    except Exception as e:
        return HttpResponse(f"PDF generation failed: {e}", status=500)

    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="work_order_{work_order.work_order_number}.pdf"'
    )
    return response


# Main view for list + forms
def manager_vessel_list(request):
    vessels = Vessel.objects.all()
    search_query = request.GET.get('search', '')
    if search_query:
        vessels = vessels.filter(name__icontains=search_query)

    paginator = Paginator(vessels, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    form = VesselForm()
    return render(request, 'Manager/vessel.html', {
        'vessels': page_obj,
        'projects': page_obj,
        'form': form,
        'search_query': search_query,
    })


# Create vessel (AJAX)
def manager_vessel_create(request):
    if request.method == 'POST':
        form = VesselForm(request.POST)
        if form.is_valid():
            vessel = form.save()
            return JsonResponse({'success': True, 'id': vessel.id, 'name': vessel.name})
        else:
            return JsonResponse({'success': False, 'errors': form.errors})
    return JsonResponse({'success': False, 'error': 'Invalid request'})


# Update vessel (AJAX)
def manager_vessel_update(request, pk):
    vessel = get_object_or_404(Vessel, pk=pk)
    if request.method == 'POST':
        form = VesselForm(request.POST, instance=vessel)
        if form.is_valid():
            vessel = form.save()
            return JsonResponse({'success': True, 'id': vessel.id, 'name': vessel.name})
        else:
            return JsonResponse({'success': False, 'errors': form.errors})
    else:
        form = VesselForm(instance=vessel)
        return JsonResponse({'name': vessel.name})


# Delete vessel (AJAX)
@csrf_exempt
def manager_vessel_delete(request, pk):
    if request.method == 'POST':
        vessel = get_object_or_404(Vessel, pk=pk)
        vessel.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Invalid request'})


@login_required
def manager_search_redirect_view(request):
    search_query = request.GET.get('search', '').strip()

    if not search_query:
        return redirect('project_list')  # fallback

    # 1. Search Project (name, code, client_name)
    matching_projects = Project.objects.filter(
        Q(name__icontains=search_query) |
        Q(code__icontains=search_query) |
        Q(client_name__icontains=search_query)
    )

    if matching_projects.count() == 1:
        return redirect('project-summary-view', project_id=matching_projects.first().id)
    elif matching_projects.exists():
        # optionally pass query param to filter project list
        return redirect(f"{reverse('project_list')}?search={search_query}")

    # 2. Search Vessel by name
    matching_vessels = Vessel.objects.filter(
        name__icontains=search_query
    )

    if matching_vessels.exists():
        return redirect(f"{reverse('manager_vessel_list')}?search={search_query}")
    # 3. Search Vessel by name
    matching_vessels = Team.objects.filter(
        name__icontains=search_query
    )

    if matching_vessels.exists():
        return redirect(f"{reverse('team-list')}?search={search_query}")

    # 4. Search Attendance by location or vessel name or project code
    matching_attendance = Attendance.objects.filter(
        Q(location__icontains=search_query) |
        Q(vessel__name__icontains=search_query) |
        Q(project__name__icontains=search_query) |
        Q(project__code__icontains=search_query)
    ).distinct()

    if matching_attendance.exists():
        return redirect(f"{reverse('attendance_list')}?search={search_query}")

    # Default fallback
    return redirect('project_list')    
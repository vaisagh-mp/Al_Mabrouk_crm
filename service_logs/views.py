from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.shortcuts import redirect
from .models import EmployeeServiceLog
from Admin.models import Project, Employee, ActivityLog, Attendance
from Admin.attendance_register import build_attendance_register
from Admin.attendance_analytics import build_attendance_analytics
from Admin.attendance_autoclose import close_stale_sessions
from .ot import calculate_office_ot_cost
from django.urls import reverse
from django.http import HttpResponseForbidden
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404
from django.db.models import Sum, Count, Avg, Q, Max
from finance.permissions import can_view_finance
from finance.services import get_projects_financials_map
from django.db.models.functions import TruncWeek, TruncMonth, TruncYear
from decimal import Decimal
from collections import defaultdict
from django.utils import timezone
from django.utils.timezone import now
from datetime import datetime, timedelta, date, time
from calendar import monthrange
from django.http import JsonResponse
import json
from django.http import HttpResponseBadRequest
from .utils import is_admin, is_manager, is_employee


@login_required
def service_log_list(request):
    user = request.user

    if is_admin(user):
        template = "service_logs/admin_list.html"
        logs_qs = EmployeeServiceLog.objects.select_related(
            "employee", "project"
        ).order_by("-date")

    elif is_manager(user):
        template = "service_logs/manager_list.html"
        logs_qs = EmployeeServiceLog.objects.select_related(
            "employee", "project"
        ).order_by("-date")

    elif is_employee(user):
        template = "service_logs/employee_list.html"
        logs_qs = EmployeeServiceLog.objects.filter(
            employee=user.employee_profile
        ).order_by("-date")

    else:
        return HttpResponseForbidden("Access denied.")

    # ---------- PAGINATION ----------
    paginator = Paginator(logs_qs, 10)  # 10 rows per page
    page_number = request.GET.get("page")
    logs = paginator.get_page(page_number)

    return render(
        request,
        template,
        {
            "logs": logs,
            "page_obj": logs,
        }
    )

@login_required
def service_log_create(request):
    user = request.user

    if is_admin(user):
        return HttpResponseForbidden("Admins cannot create service logs.")

    try:
        employee = user.employee_profile
    except:
        return HttpResponseForbidden("Employee profile not found.")
    
    if is_manager(user):
        template = "service_logs/manager_create.html"
        projects = Project.objects.filter(
            Q(manager=employee) | Q(teams__manager=employee)
        ).exclude(status="CANCELLED").distinct().order_by("name")
    elif is_employee(user):
        template = "service_logs/employee_create.html"
        projects = Project.objects.filter(
            teams__employees=employee
        ).exclude(status="CANCELLED").distinct().order_by("name")
    else:
        return HttpResponseForbidden("Access denied.")

    if request.method == "POST":
        try:
            # ✅ Convert strings to proper types
            date = datetime.strptime(request.POST.get("date"), "%Y-%m-%d").date()
            start_time = datetime.strptime(request.POST.get("start_time"), "%H:%M").time()
            end_time = datetime.strptime(request.POST.get("end_time"), "%H:%M").time()
        except (TypeError, ValueError):
            return HttpResponseForbidden("Invalid date or time format.")

        from Admin.models import check_payroll_lock_for_employee
        from Admin.timesheet_helpers import check_timesheet_overlap
        from django.core.exceptions import ValidationError
        try:
            check_payroll_lock_for_employee(employee, date)
        except ValidationError as e:
            return HttpResponseForbidden(str(e.message if hasattr(e, 'message') else e))

        try:
            break_minutes = max(0, int(request.POST.get("break_minutes") or 0))
        except (ValueError, TypeError):
            break_minutes = 0

        try:
            travel_minutes = max(0, int(request.POST.get("travel_minutes") or 0))
        except (ValueError, TypeError):
            travel_minutes = 0

        # Break < duration check
        dt_start = datetime.combine(date, start_time)
        dt_end = datetime.combine(date, end_time)
        if dt_end < dt_start:
            dt_end += timedelta(days=1)
        dur_mins = int((dt_end - dt_start).total_seconds() // 60)
        if break_minutes >= dur_mins:
            return HttpResponseForbidden("Break duration must be less than working duration.")

        overlap_err = check_timesheet_overlap(employee.id, date, start_time, end_time)
        if overlap_err:
            return HttpResponseForbidden(overlap_err)

        action = request.POST.get("action", "save_draft")
        approval_status = 'DRAFT'
        submitted_at = None
        approver = None
        if action == 'submit_approval':
            approval_status = 'SUBMITTED'
            from django.utils import timezone
            submitted_at = timezone.now()
            approver = employee.reporting_manager

        EmployeeServiceLog.objects.create(
            employee=employee,
            project_id=request.POST.get("project"),
            date=date,                      # ✅ date object
            vessel_name=request.POST.get("vessel_name") or "",
            port=request.POST.get("port") or "",
            service_type=request.POST.get("service_type") or "JOB",
            service_reference=request.POST.get("service_reference") or "",
            start_time=start_time,          # ✅ time object
            end_time=end_time,              # ✅ time object
            break_minutes=break_minutes,
            travel_minutes=travel_minutes,
            approval_status=approval_status,
            submitted_at=submitted_at,
            approver=approver,
            location_type=request.POST.get("location_type") or "ANCHORAGE",
            is_holiday=request.POST.get("is_holiday") == "on",
        )

        return redirect("service_log_list")

    return render(request, template, {"projects": projects})


@login_required
def service_log_edit(request, log_id):
    user = request.user
    log = get_object_or_404(EmployeeServiceLog, id=log_id)

    if log.approval_status == 'APPROVED':
        return HttpResponseForbidden("Cannot edit an APPROVED service log. Please ask Admin/HR to reopen it.")

    if is_admin(user):
        template = "service_logs/admin_edit.html"
        projects = Project.objects.exclude(status="CANCELLED").order_by("name")
    elif is_manager(user):
        template = "service_logs/manager_edit.html"
        try:
            employee = user.employee_profile
            projects = Project.objects.filter(
                Q(manager=employee) | Q(teams__manager=employee)
            ).exclude(status="CANCELLED").distinct().order_by("name")
        except:
            projects = Project.objects.exclude(status="CANCELLED").order_by("name")
    elif is_employee(user):
        try:
            employee = user.employee_profile
        except:
            return HttpResponseForbidden("Employee profile not found.")
        if log.employee != employee:
            return HttpResponseForbidden("You can only edit your own logs.")
        template = "service_logs/employee_edit.html"
        projects = Project.objects.filter(
            teams__employees=employee
        ).exclude(status="CANCELLED").distinct().order_by("name")
    else:
        return HttpResponseForbidden("Access denied.")

    if request.method == "POST":
        try:
            date_val = datetime.strptime(request.POST.get("date"), "%Y-%m-%d").date()
            start_time = datetime.strptime(request.POST.get("start_time"), "%H:%M").time()
            end_time = datetime.strptime(request.POST.get("end_time"), "%H:%M").time()
        except (TypeError, ValueError):
            return HttpResponseForbidden("Invalid date or time format.")

        from Admin.models import check_payroll_lock_for_employee
        from Admin.timesheet_helpers import check_timesheet_overlap
        from django.core.exceptions import ValidationError
        try:
            check_payroll_lock_for_employee(log.employee, date_val)
        except ValidationError as e:
            return HttpResponseForbidden(str(e.message if hasattr(e, 'message') else e))

        try:
            break_minutes = max(0, int(request.POST.get("break_minutes") or 0))
        except (ValueError, TypeError):
            break_minutes = 0

        try:
            travel_minutes = max(0, int(request.POST.get("travel_minutes") or 0))
        except (ValueError, TypeError):
            travel_minutes = 0

        dt_start = datetime.combine(date_val, start_time)
        dt_end = datetime.combine(date_val, end_time)
        if dt_end < dt_start:
            dt_end += timedelta(days=1)
        dur_mins = int((dt_end - dt_start).total_seconds() // 60)
        if break_minutes >= dur_mins:
            return HttpResponseForbidden("Break duration must be less than working duration.")

        overlap_err = check_timesheet_overlap(log.employee_id, date_val, start_time, end_time, exclude_service_log_id=log.pk)
        if overlap_err:
            return HttpResponseForbidden(overlap_err)

        log.date = date_val
        log.start_time = start_time
        log.end_time = end_time
        log.break_minutes = break_minutes
        log.travel_minutes = travel_minutes
        log.project_id = request.POST.get("project")
        log.vessel_name = request.POST.get("vessel_name") or ""
        log.port = request.POST.get("port") or ""
        log.service_type = request.POST.get("service_type") or "JOB"
        log.service_reference = request.POST.get("service_reference") or ""
        log.location_type = request.POST.get("location_type") or "ANCHORAGE"
        log.is_holiday = request.POST.get("is_holiday") == "on"

        action = request.POST.get("action", "save_draft")
        if action == 'submit_approval':
            log.approval_status = 'SUBMITTED'
            from django.utils import timezone
            log.submitted_at = timezone.now()
            log.approver = log.employee.reporting_manager if log.employee else None
            log.decision_remarks = ''
        elif log.approval_status == 'REJECTED':
            # Kept as draft when edited
            log.approval_status = 'DRAFT'

        log.save()
        return redirect("service_log_list")

    context = {"log": log, "projects": projects}
    return render(request, template, context)

@login_required
def service_log_delete(request, pk):
    if request.method != "POST":
        return HttpResponseForbidden("Invalid request")

    log = get_object_or_404(EmployeeServiceLog, pk=pk)

    # Optional permission check
    if not (is_admin(request.user) or is_manager(request.user) or log.employee.user == request.user):
        return HttpResponseForbidden("Access denied")

    if log.approval_status in ('SUBMITTED', 'APPROVED'):
        return HttpResponseForbidden("Cannot delete a SUBMITTED or APPROVED service log.")

    log.delete()
    return redirect("service_log_list")




@login_required
def service_log_utilization(request):
    if not is_admin(request.user):
        return HttpResponseForbidden("Admins only.")

    date_param = request.GET.get("date")
    today = now().date()

    # Default: current month
    start_date = today.replace(day=1)
    end_date = today.replace(day=monthrange(today.year, today.month)[1])

    if date_param:
        try:
            if "to" in date_param:
                start_str, end_str = date_param.split(" to ")
                start_date = datetime.strptime(start_str.strip(), "%Y-%m-%d").date()
                end_date = datetime.strptime(end_str.strip(), "%Y-%m-%d").date()
            else:
                selected = datetime.strptime(date_param, "%Y-%m-%d").date()
                start_date = selected.replace(day=1)
                end_date = selected.replace(
                    day=monthrange(selected.year, selected.month)[1]
                )
        except ValueError:
            pass

    logs = EmployeeServiceLog.objects.counting().filter(
        date__range=(start_date, end_date)
    )

    summary_qs = logs.values(
        "employee__user__username"
    ).annotate(
        total_hours=Sum("total_hours"),
        normal_cost=Sum("normal_cost"),
        ot_cost=Sum("ot_cost"),
    ).order_by("employee__user__username")

    capacity = Decimal(26 * 8)

    # Convert queryset to list so we can add utilization
    summary_list = []
    for row in summary_qs:
        row["utilization_percent"] = round(
            (row["total_hours"] / capacity) * 100 if row["total_hours"] else 0,
            2,
        )
        summary_list.append(row)

    # ---------------- PAGINATION ----------------
    paginator = Paginator(summary_list, 10)  # 10 rows per page
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "service_logs/utilization.html",
        {
            "summary": page_obj,          # paginated data
            "page_obj": page_obj,
            "selected_date": f"{start_date} to {end_date}",
        },
    )



@login_required
def analytics_project(request):
    user = request.user
    if not (is_admin(user) or is_manager(user)):
        return HttpResponseForbidden("Access denied")

    # ---------- Date Filter ----------
    date_str = request.GET.get("date")
    if date_str and " to " in date_str:
        start, end = date_str.split(" to ")
        start_date = datetime.strptime(start, "%Y-%m-%d").date()
        end_date = datetime.strptime(end, "%Y-%m-%d").date()
    elif date_str:
        start_date = end_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    else:
        today = now().date()
        start_date = today.replace(day=1)
        end_date = today

    logs = EmployeeServiceLog.objects.counting().select_related(
        "employee", "employee__user", "project"
    ).filter(date__range=(start_date, end_date))

    # =====================================================
    # PROJECT ANALYTICS
    # =====================================================
    # Aggregate service-log hours/costs per project in one query
    proj_log_aggs = list(
        logs.values('project_id')
        .annotate(
            total_hours=Sum('total_hours'),
            normal_cost=Sum('normal_cost'),
            ot_cost=Sum('ot_cost'),
        )
    )
    proj_log_map = {
        row['project_id']: {
            'total_hours': row['total_hours'] or Decimal('0.00'),
            'normal_cost': row['normal_cost'] or Decimal('0.00'),
            'ot_cost': row['ot_cost'] or Decimal('0.00'),
        }
        for row in proj_log_aggs
    }

    # Aggregate per employee in one query
    emp_log_aggs = list(
        logs.values('project_id', 'employee_id', 'employee__user__username')
        .annotate(
            hours=Sum('total_hours'),
            ot_hours=Sum('ot_hours'),
            normal_cost=Sum('normal_cost'),
            ot_cost=Sum('ot_cost'),
        )
        .order_by('employee__user__username')
    )
    emp_log_map = defaultdict(list)
    for row in emp_log_aggs:
        hrs = row['hours'] or Decimal('0.00')
        ot_hrs = row['ot_hours'] or Decimal('0.00')
        n_cost = row['normal_cost'] or Decimal('0.00')
        o_cost = row['ot_cost'] or Decimal('0.00')
        utilization = round((hrs / Decimal(26 * 8)) * 100, 2) if hrs else 0
        emp_log_map[row['project_id']].append({
            "name": row['employee__user__username'],
            "hours": hrs,
            "ot_hours": ot_hrs,
            "utilization": utilization,
            "normal_cost": n_cost,
            "ot_cost": o_cost,
            "total_cost": n_cost + o_cost,
        })

    project_ids = [pid for pid in proj_log_map.keys() if pid]
    projects = list(Project.objects.filter(id__in=project_ids))

    financials_map = get_projects_financials_map(projects)

    project_analytics = []
    total_hours_all = Decimal('0.00')
    total_cost_all = Decimal('0.00')
    total_revenue_all = Decimal('0.00')
    total_profit_all = Decimal('0.00')
    total_employee_cost_all = Decimal('0.00')

    client_stats = defaultdict(lambda: {"projects": 0, "profit": Decimal('0.00')})

    user_can_view_finance = can_view_finance(request.user)

    for project in projects:
        p_log = proj_log_map.get(project.id, {
            'total_hours': Decimal('0.00'),
            'normal_cost': Decimal('0.00'),
            'ot_cost': Decimal('0.00'),
        })
        total_hours = p_log['total_hours']
        emp_cost = p_log['normal_cost'] + p_log['ot_cost']
        total_employee_cost_all += emp_cost
        total_hours_all += total_hours

        fin = financials_map.get(project.id, {})
        project_emp_cost = fin.get('employee_cost', Decimal('0.00'))
        revenue = fin.get('invoiced_amount', Decimal('0.00')) if user_can_view_finance else None
        is_legacy = bool(fin.get('is_legacy_invoice') and (fin.get('invoiced_amount') or Decimal('0.00')) > Decimal('0.00')) if user_can_view_finance else False
        expense = fin.get('total_cost', Decimal('0.00')) if user_can_view_finance else None
        spares_cost = fin.get('spares_cost', Decimal('0.00')) if user_can_view_finance else None
        other_job_costs = fin.get('other_job_costs', Decimal('0.00')) if user_can_view_finance else None
        profit = fin.get('profit', Decimal('0.00')) if user_can_view_finance else None
        profit_percent = fin.get('profit_pct') if (user_can_view_finance and (fin.get('invoiced_amount') or Decimal('0.00')) > Decimal('0.00')) else None

        if user_can_view_finance:
            total_cost_all += fin.get('total_cost', Decimal('0.00'))
            total_revenue_all += fin.get('invoiced_amount', Decimal('0.00'))
            total_profit_all += fin.get('profit', Decimal('0.00'))

            client_name = project.client_name or "N/A"
            client_stats[client_name]["projects"] += 1
            client_stats[client_name]["profit"] += fin.get('profit', Decimal('0.00'))
        else:
            client_name = project.client_name or "N/A"
            client_stats[client_name]["projects"] += 1

        employees_data = emp_log_map.get(project.id, [])

        project_analytics.append({
            "project": project,
            "total_hours": total_hours,
            "period_employee_cost": emp_cost,
            "employee_cost": project_emp_cost,
            "spares_cost": spares_cost,
            "other_job_costs": other_job_costs,
            "expense": expense,
            "revenue": revenue,
            "is_legacy_invoice": is_legacy,
            "profit": profit,
            "profit_percent": profit_percent,
            "employees": employees_data,
        })

    # ---------- SORTING ----------
    sort = request.GET.get("sort", "desc")  # default high → low

    if user_can_view_finance:
        project_analytics_sorted = sorted(
            project_analytics,
            key=lambda x: x["profit"] if x["profit"] is not None else Decimal('0.00'),
            reverse=True if sort == "desc" else False
        )
    else:
        project_analytics_sorted = sorted(
            project_analytics,
            key=lambda x: x["employee_cost"],
            reverse=True if sort == "desc" else False
        )

    # ---------- PAGINATION ----------
    paginator = Paginator(project_analytics_sorted, 10)  # 10 projects per page
    page_number = request.GET.get("page")
    projects_page = paginator.get_page(page_number)

    # ---------- TOP 5 CLIENTS ----------
    top_clients = sorted(
        [
            {
                "name": name,
                "projects": data["projects"],
                "profit": data["profit"] if user_can_view_finance else None,
            }
            for name, data in client_stats.items()
        ],
        key=lambda x: x["profit"] if user_can_view_finance else x["projects"],
        reverse=True
    )[:5]

    # =====================================================
    # 🔹 CHART DATA
    # =====================================================
    range_type = request.GET.get("range", "month")

    def get_key(d):
        if range_type == "day":
            return d.strftime("%Y-%m-%d")
        if range_type == "week":
            return f"{d.year}-W{d.isocalendar()[1]}"
        if range_type == "year":
            return str(d.year)
        return d.strftime("%Y-%m")  # month (default)

    current_map = defaultdict(lambda: {"projects": set(), "revenue": Decimal(0)})
    last_year_map = defaultdict(lambda: {"projects": set(), "revenue": Decimal(0)})

    log_dates_pids = list(logs.values_list('date', 'project_id'))
    for log_date, pid in log_dates_pids:
        key = get_key(log_date)
        if pid not in current_map[key]["projects"]:
            current_map[key]["projects"].add(pid)
            if user_can_view_finance:
                current_map[key]["revenue"] += financials_map.get(pid, {}).get('invoiced_amount', Decimal('0.00'))

    last_year_start = start_date.replace(year=start_date.year - 1)
    last_year_end = end_date.replace(year=end_date.year - 1)

    last_year_logs = list(
        EmployeeServiceLog.objects.counting().filter(
            date__range=(last_year_start, last_year_end)
        ).values_list('date', 'project_id')
    )
    last_year_pids = list({pid for _, pid in last_year_logs if pid})
    last_year_fin_map = get_projects_financials_map(last_year_pids) if (user_can_view_finance and last_year_pids) else {}

    for log_date, pid in last_year_logs:
        shifted_date = log_date.replace(year=log_date.year + 1)
        key = get_key(shifted_date)

        if pid not in last_year_map[key]["projects"]:
            last_year_map[key]["projects"].add(pid)
            if user_can_view_finance:
                last_year_map[key]["revenue"] += last_year_fin_map.get(pid, {}).get('invoiced_amount', Decimal('0.00'))

    labels = sorted(current_map.keys())

    projects_current = [len(current_map[k]["projects"]) for k in labels]
    projects_last_year = [len(last_year_map[k]["projects"]) for k in labels]

    if user_can_view_finance:
        revenue_current = [float(current_map[k]["revenue"]) for k in labels]
        revenue_last_year = [float(last_year_map[k]["revenue"]) for k in labels]
    else:
        revenue_current = []
        revenue_last_year = []

    # =====================================================
    # 🔹 SERVICE COMPLETION TABLE DATA (with Custom Date Picker & Pagination)
    # =====================================================
    sc_date_str = request.GET.get("sc_date", "").strip()
    sc_start_date = None
    sc_end_date = None

    if sc_date_str and " to " in sc_date_str:
        try:
            s_part, e_part = sc_date_str.split(" to ")
            sc_start_date = datetime.strptime(s_part.strip(), "%Y-%m-%d").date()
            sc_end_date = datetime.strptime(e_part.strip(), "%Y-%m-%d").date()
        except ValueError:
            pass
    elif sc_date_str:
        try:
            sc_start_date = sc_end_date = datetime.strptime(sc_date_str.strip(), "%Y-%m-%d").date()
        except ValueError:
            pass

    all_projects = list(Project.objects.exclude(status="CANCELLED").order_by("-created_at"))
    all_pids = [p.id for p in all_projects]

    last_dates_qs = EmployeeServiceLog.objects.counting().filter(project_id__in=all_pids).values('project_id').annotate(last_date=Max('date'))
    last_date_map = {row['project_id']: row['last_date'] for row in last_dates_qs}

    logs_with_ref = (
        EmployeeServiceLog.objects.counting().filter(project_id__in=all_pids, service_reference__isnull=False)
        .exclude(service_reference="")
        .order_by('project_id', '-date')
        .values('project_id', 'service_reference', 'vessel_name')
    )
    ref_map = {}
    for row in logs_with_ref:
        if row['project_id'] not in ref_map:
            ref_map[row['project_id']] = (row['service_reference'], row['vessel_name'])

    completed_logs = (
        ActivityLog.objects.filter(project_id__in=all_pids, new_status="COMPLETED")
        .order_by('project_id', '-changed_at')
        .values('project_id', 'changed_at')
    )
    comp_date_map = {}
    for row in completed_logs:
        if row['project_id'] not in comp_date_map:
            comp_date_map[row['project_id']] = row['changed_at'].date()

    profit_map = {item["project"].id: (item["profit"], item["profit_percent"]) for item in project_analytics}

    service_completions = []
    for proj in all_projects:
        last_att_date = last_date_map.get(proj.id)
        s_ref, v_name = ref_map.get(proj.id, (None, None))
        service_ref = s_ref or proj.code or "-"
        vessel = proj.vessel_name or v_name or "-"

        comp_date = comp_date_map.get(proj.id) or (proj.deadline_date if proj.status == "COMPLETED" else None)

        # Custom date range filter for Service Completion
        if sc_start_date or sc_end_date:
            dates_to_check = [d for d in [comp_date, last_att_date, proj.created_at.date() if proj.created_at else None] if d]
            if not dates_to_check:
                continue

            match = False
            for d in dates_to_check:
                if sc_start_date and sc_end_date:
                    if sc_start_date <= d <= sc_end_date:
                        match = True
                        break
                elif sc_start_date and d >= sc_start_date:
                    match = True
                    break
                elif sc_end_date and d <= sc_end_date:
                    match = True
                    break
            if not match:
                continue

        if user_can_view_finance:
            p_profit, p_profit_pct = profit_map.get(proj.id, (Decimal('0.00'), None))
        else:
            p_profit = None
            p_profit_pct = None

        service_completions.append({
            "service_ref": service_ref,
            "vessel": vessel,
            "project": proj,
            "last_attendance_date": last_att_date,
            "status": proj.status,
            "completion_date": comp_date,
            "profit": p_profit,
            "profit_percent": p_profit_pct,
        })

    # Pagination for Service Completion (10 per page)
    sc_paginator = Paginator(service_completions, 10)
    sc_page = request.GET.get("sc_page", 1)
    service_completions_page = sc_paginator.get_page(sc_page)

    return render(
        request,
        "analytics/project_based.html",
        {
            "can_view_finance": user_can_view_finance,
            "project_analytics": project_analytics,
            "projects_page": projects_page,
            "sort": sort,
            "top_clients": top_clients,
            "service_completions_page": service_completions_page,
            "sc_date": sc_date_str,

            # KPI values
            "kpi_total_projects": len(project_analytics),
            "kpi_total_hours": total_hours_all,
            "kpi_total_cost": total_cost_all if user_can_view_finance else None,
            "kpi_revenue": total_revenue_all if user_can_view_finance else None,
            "kpi_profit": total_profit_all if user_can_view_finance else None,
            "kpi_expense": total_cost_all if user_can_view_finance else None,
            "kpi_employee_cost": total_employee_cost_all,

            # 🔹 charts
            "labels": labels,
            "projects_current": projects_current,
            "projects_last_year": projects_last_year,
            "revenue_current": revenue_current,
            "revenue_last_year": revenue_last_year,
            "range": range_type,

            "start_date": start_date,
            "end_date": end_date,
        }
    )


@login_required
def project_drilldown(request, project_id):
    if not (is_admin(request.user) or is_manager(request.user)):
        return JsonResponse({"error": "Unauthorized"}, status=403)

    logs = EmployeeServiceLog.objects.counting().filter(project_id=project_id)

    data = []

    for emp_id in logs.order_by().values_list("employee_id", flat=True).distinct():
        emp_logs = logs.filter(employee_id=emp_id)
        emp = emp_logs.first().employee

        hours = emp_logs.aggregate(h=Sum("total_hours"))["h"] or 0
        ot = emp_logs.aggregate(o=Sum("ot_hours"))["o"] or 0
        normal = emp_logs.aggregate(n=Sum("normal_cost"))["n"] or 0
        ot_cost = emp_logs.aggregate(o=Sum("ot_cost"))["o"] or 0

        utilization = round((hours / Decimal(26 * 8)) * 100, 2) if hours else 0

        data.append({
            "name": emp.user.username,
            "hours": float(hours),
            "ot": float(ot),
            "utilization": utilization,
            "normal_cost": float(normal),
            "ot_cost": float(ot_cost),
            "total_cost": float(normal + ot_cost),
        })

    return JsonResponse(data, safe=False)


def _parse_filter_date(date_text):
    if not date_text:
        return None
    date_text = date_text.strip()
    formats = [
        "%Y-%m-%d",
        "%b. %d, %Y",
        "%b %d, %Y",
        "%B %d, %Y",
        "%d %b %Y",
        "%d %B %Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d-%m-%Y",
        "%Y/%m/%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_text, fmt).date()
        except ValueError:
            continue
    return None


def build_employee_analytics_context(request):
    close_stale_sessions()
    # ---------- Date Filter ----------
    date_str = (request.GET.get("date") or "").strip()
    start_date, end_date = None, None
    if date_str and " to " in date_str:
        s_part, e_part = date_str.split(" to ", 1)
        start_date = _parse_filter_date(s_part)
        end_date = _parse_filter_date(e_part)
    elif date_str:
        parsed = _parse_filter_date(date_str)
        if parsed:
            start_date = end_date = parsed

    if start_date and not end_date:
        end_date = start_date
    elif end_date and not start_date:
        start_date = end_date
    elif start_date and end_date and start_date > end_date:
        start_date, end_date = end_date, start_date

    if not start_date or not end_date:
        today = now().date()
        start_date = today.replace(day=1)
        end_date = today

    # 1. Source Data
    register_rows = build_attendance_register(start_date, end_date)

    logs = list(
        EmployeeServiceLog.objects.counting().select_related(
            "employee", "employee__user", "project"
        ).filter(date__range=(start_date, end_date))
    )

    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(end_date, time.max), tz)

    attendances_with_project = list(
        Attendance.objects.filter(
            login_time__gte=start_dt,
            login_time__lte=end_dt,
            project__isnull=False
        ).exclude(status='REJECTED').select_related('project')
    )

    logs_by_emp = defaultdict(list)
    for l in logs:
        logs_by_emp[l.employee_id].append(l)

    atts_by_emp = defaultdict(list)
    for a in attendances_with_project:
        atts_by_emp[a.employee_id].append(a)

    all_employees = list(
        Employee.objects.filter(
            user__is_active=True
        ).filter(
            Q(is_employee=True) | Q(is_manager=True) | Q(is_administration=True) | Q(is_hr=True) | Q(is_accountant=True)
        ).select_related('user').order_by('user__username')
    )

    rows_by_emp = defaultdict(list)
    for r in register_rows:
        rows_by_emp[r['employee'].id].append(r)

    # =====================================================
    # 2. PER-EMPLOYEE METRICS
    # =====================================================
    employee_analytics = []

    for emp in all_employees:
        emp_rows = rows_by_emp.get(emp.id, [])
        emp_logs = logs_by_emp.get(emp.id, [])

        vessel_hours = sum((l.total_hours or Decimal(0)) for l in emp_logs)

        emp_logs_by_date = defaultdict(list)
        for l in emp_logs:
            emp_logs_by_date[l.date].append(l)

        office_hours = Decimal(0)
        office_normal_cost = Decimal(0)
        office_ot_cost = Decimal(0)

        rate = (emp.salary / Decimal(26 * 8)) if emp.salary else Decimal(0)

        for r in emp_rows:
            st = r['status_code']
            if st == 'OFFICE':
                day_office = r['total_hours'] or Decimal(0)
                office_hours += day_office
                if rate > 0 and day_office > 0:
                    day_normal = min(day_office, Decimal(10))
                    office_normal_cost += day_normal * rate
            elif st == 'OFFICE_VESSEL':
                day_log_hours = sum((l.total_hours or Decimal(0)) for l in emp_logs_by_date.get(r['date'], []))
                day_office = max((r['total_hours'] or Decimal(0)) - day_log_hours, Decimal(0))
                office_hours += day_office
                if rate > 0 and day_office > 0:
                    off_ot = r.get('office_ot_hours') or Decimal(0)
                    off_normal = max(day_office - off_ot, Decimal(0))
                    office_normal_cost += off_normal * rate
            else:
                day_office = Decimal(0)

        office_ot_cost = calculate_office_ot_cost(emp.salary, emp_rows, emp_logs, employee=emp)

        total_hours = vessel_hours + office_hours

        ot_hours = sum((r['ot_hours'] or Decimal(0)) for r in emp_rows)

        vessel_normal_cost = sum((l.normal_cost or Decimal(0)) for l in emp_logs)
        vessel_ot_cost = sum((l.ot_cost or Decimal(0)) for l in emp_logs)

        normal_cost = vessel_normal_cost + office_normal_cost
        ot_cost = vessel_ot_cost + office_ot_cost
        total_cost = normal_cost + ot_cost


        days_worked = sum(1 for r in emp_rows if r['status_code'] in ('OFFICE', 'VESSEL', 'OFFICE_VESSEL'))
        days_absent = sum(1 for r in emp_rows if r['status_code'] == 'ABSENT')
        days_leave = sum(1 for r in emp_rows if r['status_code'] in ('ANNUAL_LEAVE', 'SICK_LEAVE', 'PERSONAL_LEAVE'))
        days_training = sum(1 for r in emp_rows if r['status_code'] == 'TRAINING')
        days_rest = sum(1 for r in emp_rows if r['status_code'] == 'REST')

        working_days = sum(
            1 for r in emp_rows
            if (r['status_code'] not in ('SUNDAY', 'REST_DAY', 'HOLIDAY', 'NONE')) or r['is_off_day'] or ((r['total_hours'] or Decimal(0)) > 0)
        )

        capacity_hours = working_days * 8
        utilization = (
            round((total_hours / Decimal(capacity_hours)) * 100, 2)
            if capacity_hours > 0 else Decimal('0.00')
        )

        att_denom = working_days - days_leave - days_training
        attendance_rate = (
            round((Decimal(days_worked) / Decimal(att_denom)) * 100, 2)
            if att_denom > 0 else Decimal('0.00')
        )

        if vessel_hours > 0 and office_hours == 0:
            work_type = "Vessel"
        elif office_hours > 0 and vessel_hours == 0:
            work_type = "Office"
        elif vessel_hours > 0 and office_hours > 0:
            work_type = "Mixed"
        else:
            work_type = "—"

        p_logs_by_id = defaultdict(list)
        for l in emp_logs:
            if l.project_id:
                p_logs_by_id[l.project_id].append(l)

        p_atts_by_id = defaultdict(list)
        for a in atts_by_emp.get(emp.id, []):
            if a.project_id:
                p_atts_by_id[a.project_id].append(a)

        all_pids = set(p_logs_by_id.keys()) | set(p_atts_by_id.keys())
        project_data = []
        for pid in all_pids:
            p_logs = p_logs_by_id.get(pid, [])
            p_atts = p_atts_by_id.get(pid, [])

            proj_obj = p_logs[0].project if p_logs else (p_atts[0].project if p_atts else None)
            proj_name = proj_obj.name if proj_obj else "-"

            p_l_hours = sum((l.total_hours or Decimal(0)) for l in p_logs)
            p_l_ot = sum((l.ot_hours or Decimal(0)) for l in p_logs)
            p_l_cost = sum(((l.normal_cost or Decimal(0)) + (l.ot_cost or Decimal(0))) for l in p_logs)

            p_a_hours = sum((a.total_hours_of_work or Decimal(0)) for a in p_atts)
            p_a_cost = (p_a_hours * rate) if rate > 0 else Decimal(0)

            project_data.append({
                "name": proj_name,
                "hours": p_l_hours + p_a_hours,
                "ot_hours": p_l_ot,
                "cost": p_l_cost + p_a_cost,
            })

        employee_analytics.append({
            "employee": emp,
            "work_type": work_type,
            "office_hours": office_hours,
            "vessel_hours": vessel_hours,
            "total_hours": total_hours,
            "ot_hours": ot_hours,
            "days_worked": days_worked,
            "days_absent": days_absent,
            "days_leave": days_leave,
            "days_training": days_training,
            "days_rest": days_rest,
            "working_days": working_days,
            "capacity_hours": capacity_hours,
            "attendance_rate": attendance_rate,
            "utilization": utilization,
            "normal_cost": normal_cost,
            "ot_cost": ot_cost,
            "total_cost": total_cost,
            "projects": project_data,
        })

    # =====================================================
    # 3. WORK TYPE FILTER & KPIS
    # =====================================================
    work_type_filter = (request.GET.get("work_type") or "").strip()
    if work_type_filter and work_type_filter.lower() != "all":
        filtered_analytics = [e for e in employee_analytics if e["work_type"].lower() == work_type_filter.lower()]
    else:
        filtered_analytics = employee_analytics

    kpi_total_employees = len(filtered_analytics)
    kpi_total_hours = sum(e["total_hours"] for e in filtered_analytics)
    kpi_office_hours = sum(e["office_hours"] for e in filtered_analytics)
    kpi_vessel_hours = sum(e["vessel_hours"] for e in filtered_analytics)
    kpi_total_cost = sum(e["total_cost"] for e in filtered_analytics)
    kpi_total_ot = sum(e["ot_hours"] for e in filtered_analytics)

    kpi_total_projects = len(set(
        p["name"]
        for e in filtered_analytics
        for p in e["projects"]
        if p["name"] and p["name"] != "-"
    ))

    total_capacity = sum(e["capacity_hours"] for e in filtered_analytics)
    kpi_avg_utilization = (
        (kpi_total_hours / Decimal(total_capacity)) * 100
        if total_capacity else Decimal(0)
    )

    total_worked_days = sum(e["days_worked"] for e in filtered_analytics)
    total_expected_days = sum(max(e["working_days"] - e["days_leave"] - e["days_training"], 0) for e in filtered_analytics)
    kpi_avg_attendance_rate = (
        (Decimal(total_worked_days) / Decimal(total_expected_days)) * 100
        if total_expected_days else Decimal(0)
    )

    # ---------- UTILIZATION TREND ----------
    trend = request.GET.get("trend", "month")
    filtered_emp_ids = {e["employee"].id for e in filtered_analytics}
    trend_rows = [r for r in register_rows if r["employee"].id in filtered_emp_ids]

    period_data = defaultdict(lambda: {'hours': Decimal(0), 'working_days': 0})

    for r in trend_rows:
        d = r['date']
        if trend == "week":
            period_key = d - timedelta(days=d.weekday())
        elif trend == "year":
            period_key = date(d.year, 1, 1)
        else:  # month (default)
            period_key = date(d.year, d.month, 1)

        period_data[period_key]['hours'] += (r['total_hours'] or Decimal(0))
        is_working = (r['status_code'] not in ('SUNDAY', 'REST_DAY', 'HOLIDAY', 'NONE')) or r['is_off_day'] or ((r['total_hours'] or Decimal(0)) > 0)
        if is_working:
            period_data[period_key]['working_days'] += 1

    trend_labels = []
    trend_utilization = []

    if trend == "week":
        label_fmt = "%d %b"
    elif trend == "year":
        label_fmt = "%Y"
    else:
        label_fmt = "%b %Y"

    for p_key in sorted(period_data.keys()):
        trend_labels.append(p_key.strftime(label_fmt))
        p_cap = period_data[p_key]['working_days'] * 8
        u = (
            (period_data[p_key]['hours'] / Decimal(p_cap)) * 100
            if p_cap > 0 else Decimal(0)
        )
        trend_utilization.append(float(round(u, 2)))

    # ---------- COST EFFICIENCY (skip 0 hours) ----------
    employee_labels = []
    employee_cost_efficiency = []

    for e in filtered_analytics:
        if e["total_hours"] > 0:
            employee_labels.append(e["employee"].user.username)
            efficiency = (
                (e["total_cost"] / e["total_hours"])
                if e["total_hours"] else 0
            )
            employee_cost_efficiency.append(float(round(efficiency, 2)))

    # =====================================================
    # PAGINATION (10 employees per page)
    # =====================================================
    page_number = request.GET.get("page", 1)
    paginator = Paginator(filtered_analytics, 10)
    page_obj = paginator.get_page(page_number)

    # =====================================================
    # 🔹 VESSEL VISITS TABLE DATA (with Custom Date Picker & Pagination)
    # =====================================================
    vv_date_str = (request.GET.get("vv_date") or "").strip()
    vv_start_date = None
    vv_end_date = None

    if vv_date_str and " to " in vv_date_str:
        s_part, e_part = vv_date_str.split(" to ", 1)
        vv_start_date = _parse_filter_date(s_part)
        vv_end_date = _parse_filter_date(e_part)
    elif vv_date_str:
        vv_parsed = _parse_filter_date(vv_date_str)
        if vv_parsed:
            vv_start_date = vv_end_date = vv_parsed

    if vv_start_date and not vv_end_date:
        vv_end_date = vv_start_date
    elif vv_end_date and not vv_start_date:
        vv_start_date = vv_end_date
    elif vv_start_date and vv_end_date and vv_start_date > vv_end_date:
        vv_start_date, vv_end_date = vv_end_date, vv_start_date

    vv_logs = EmployeeServiceLog.objects.counting().select_related("employee", "employee__user", "project").all()
    if vv_start_date and vv_end_date:
        vv_logs = vv_logs.filter(date__range=(vv_start_date, vv_end_date))
    elif vv_start_date:
        vv_logs = vv_logs.filter(date=vv_start_date)

    vv_logs = vv_logs.order_by("-date", "employee__user__username")

    # Map of (employee_id, date) -> list of all logs for that employee on that date
    day_logs_map = defaultdict(list)
    for l in EmployeeServiceLog.objects.all():
        day_logs_map[(l.employee_id, l.date)].append(l)

    vessel_visits = []
    for l in vv_logs:
        day_visits = day_logs_map[(l.employee_id, l.date)]
        v_count = len(day_visits)
        if v_count <= 1:
            continue

        vessel = (l.project.vessel_name if (l.project and l.project.vessel_name) else l.vessel_name) or "-"
        s_ref = l.service_reference or (l.project.code if l.project else "-")
        emp = l.employee
        designation = emp.rank or emp.get_role() if emp else "-"

        port = l.port or ""
        loc_type = l.get_location_type_display() if l.location_type else ""
        if port and loc_type:
            location = f"{port} ({loc_type})"
        else:
            location = port or loc_type or "-"

        vessel_visits.append({
            "date": l.date,
            "employee_name": emp.user.username if emp and emp.user else "Unknown",
            "designation": designation,
            "service_ref": s_ref,
            "vessel": vessel,
            "location": location,
            "port": port,
            "location_type": loc_type,
            "total_hours": l.total_hours,
            "project_id": l.project_id,
            "is_multiple_vessels": True,
            "vessel_count": v_count,
        })

    vv_paginator = Paginator(vessel_visits, 10)
    vv_page = request.GET.get("vv_page", 1)
    vessel_visits_page = vv_paginator.get_page(vv_page)

    tab = (request.GET.get("tab") or "performance").strip().lower()
    if tab != "attendance":
        tab = "performance"

    att = None
    if tab == "attendance":
        days_in_range = end_date - start_date
        prev_end = start_date - timedelta(days=1)
        prev_start = prev_end - days_in_range
        prev_rows = build_attendance_register(prev_start, prev_end)
        employees_filter_ids = {e["employee"].id for e in filtered_analytics}
        att = build_attendance_analytics(
            register_rows,
            prev_rows,
            employees_filter_ids,
            start_date,
            end_date
        )

    q_perf = request.GET.copy()
    q_perf["tab"] = "performance"
    q_att = request.GET.copy()
    q_att["tab"] = "attendance"

    try:
        register_url = reverse('attendance_list_adminview') if request.user.is_superuser else None
    except Exception:
        register_url = None

    return {
        "tab": tab,
        "att": att,
        "querystring_perf": q_perf.urlencode(),
        "querystring_att": q_att.urlencode(),
        "register_url": register_url,
        "employee_analytics": page_obj,
        "page_obj": page_obj,
        "vessel_visits_page": vessel_visits_page,
        "vv_date": vv_date_str,

        # KPI values
        "kpi_total_employees": kpi_total_employees,
        "kpi_total_hours": kpi_total_hours,
        "kpi_office_hours": kpi_office_hours,
        "kpi_vessel_hours": kpi_vessel_hours,
        "kpi_total_cost": kpi_total_cost,
        "kpi_total_ot": kpi_total_ot,
        "kpi_total_projects": kpi_total_projects,
        "kpi_avg_utilization": round(kpi_avg_utilization, 2),
        "kpi_avg_attendance_rate": round(kpi_avg_attendance_rate, 2),

        "trend": trend,
        "trend_labels": json.dumps(trend_labels),
        "trend_utilization": json.dumps(trend_utilization),
        "employee_labels": json.dumps(employee_labels),
        "employee_cost_efficiency": json.dumps(employee_cost_efficiency),

        "work_type": work_type_filter,
        "start_date": start_date,
        "end_date": end_date,
    }


@login_required
def analytics_employee(request):
    user = request.user
    if not (is_admin(user) or is_manager(user)):
        return HttpResponseForbidden("Access denied")

    context = build_employee_analytics_context(request)
    return render(request, "analytics/employee_based.html", context)



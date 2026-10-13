from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.shortcuts import redirect
from .models import EmployeeServiceLog
from Admin.models import Project, Employee, ActivityLog, Attendance
from Admin.attendance_register import build_attendance_register
from Admin.attendance_analytics import build_attendance_analytics
from django.urls import reverse
from django.http import HttpResponseForbidden
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404
from django.db.models import Sum, Count, Avg, Q
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

        EmployeeServiceLog.objects.create(
            employee=employee,
            project_id=request.POST.get("project"),
            date=date,                      # ✅ date object
            vessel_name=request.POST.get("vessel_name"),
            port=request.POST.get("port"),
            service_type=request.POST.get("service_type"),
            service_reference=request.POST.get("service_reference"),
            start_time=start_time,          # ✅ time object
            end_time=end_time,              # ✅ time object
            location_type=request.POST.get("location_type"),
            is_holiday=request.POST.get("is_holiday") == "on",
        )

        return redirect("service_log_list")

    return render(request, template, {"projects": projects})


@login_required
def service_log_edit(request, log_id):
    user = request.user
    log = get_object_or_404(EmployeeServiceLog, id=log_id)

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
            log.date = datetime.strptime(request.POST.get("date"), "%Y-%m-%d").date()
            log.start_time = datetime.strptime(request.POST.get("start_time"), "%H:%M").time()
            log.end_time = datetime.strptime(request.POST.get("end_time"), "%H:%M").time()
        except (TypeError, ValueError):
            return HttpResponseForbidden("Invalid date or time format.")

        log.project_id = request.POST.get("project")
        log.vessel_name = request.POST.get("vessel_name")
        log.port = request.POST.get("port")
        log.service_type = request.POST.get("service_type")
        log.service_reference = request.POST.get("service_reference")
        log.location_type = request.POST.get("location_type")
        log.is_holiday = request.POST.get("is_holiday") == "on"

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

    logs = EmployeeServiceLog.objects.filter(
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

    logs = EmployeeServiceLog.objects.select_related(
        "employee", "employee__user", "project"
    ).filter(date__range=(start_date, end_date))

    # =====================================================
    # PROJECT ANALYTICS
    # =====================================================
    project_analytics = []

    projects = Project.objects.filter(service_logs__in=logs).distinct()

    total_hours_all = Decimal(0)
    total_cost_all = Decimal(0)
    total_revenue_all = Decimal(0)
    total_profit_all = Decimal(0)
    total_employee_cost_all = Decimal(0)

    client_stats = defaultdict(lambda: {"projects": 0, "profit": Decimal(0)})

    for project in projects:
        project_logs = logs.filter(project=project)

        total_hours = project_logs.aggregate(
            h=Sum("total_hours")
        )["h"] or Decimal(0)

        normal_cost = project_logs.aggregate(
            n=Sum("normal_cost")
        )["n"] or Decimal(0)

        ot_cost = project_logs.aggregate(
            o=Sum("ot_cost")
        )["o"] or Decimal(0)

        total_cost = normal_cost + ot_cost + project.purchase_and_expenses
        total_employee_cost_all += (normal_cost + ot_cost)
        revenue = project.invoice_amount
        profit = revenue - total_cost
        profit_percent = (profit / revenue) * 100 if revenue else Decimal(0)

        # KPI accumulation
        total_hours_all += total_hours
        total_cost_all += total_cost
        total_revenue_all += revenue
        total_profit_all += profit

        # Client aggregation
        client_name = project.client_name or "N/A"
        client_stats[client_name]["projects"] += 1
        client_stats[client_name]["profit"] += profit

        # Employee breakdown
        employees_data = []
        for emp_id in project_logs.order_by().values_list("employee_id", flat=True).distinct():
            emp_logs = project_logs.filter(employee_id=emp_id)
            emp = emp_logs.first().employee

            hours = emp_logs.aggregate(h=Sum("total_hours"))["h"] or Decimal(0)
            ot_hours = emp_logs.aggregate(o=Sum("ot_hours"))["o"] or Decimal(0)
            n_cost = emp_logs.aggregate(n=Sum("normal_cost"))["n"] or Decimal(0)
            o_cost = emp_logs.aggregate(o=Sum("ot_cost"))["o"] or Decimal(0)

            utilization = round((hours / Decimal(26 * 8)) * 100, 2) if hours else 0

            employees_data.append({
                "name": emp.user.username,
                "hours": hours,
                "ot_hours": ot_hours,
                "utilization": utilization,
                "normal_cost": n_cost,
                "ot_cost": o_cost,
                "total_cost": n_cost + o_cost,
            })

        project_analytics.append({
            "project": project,
            "total_hours": total_hours,
            "employee_cost": normal_cost + ot_cost,   # employee only
            "expense": total_cost,                     # employee + purchase
            "revenue": revenue,
            "profit": profit,
            "profit_percent": profit_percent,
            "employees": employees_data,
        })

    # ---------- SORTING ----------
    sort = request.GET.get("sort", "desc")  # default high → low

    project_analytics_sorted = sorted(
        project_analytics,
        key=lambda x: x["profit"],
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
                "profit": data["profit"],
            }
            for name, data in client_stats.items()
        ],
        key=lambda x: x["profit"],
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

    for log in logs:
        key = get_key(log.date)
        if log.project_id not in current_map[key]["projects"]:
            current_map[key]["projects"].add(log.project_id)
            current_map[key]["revenue"] += log.project.invoice_amount

    last_year_start = start_date.replace(year=start_date.year - 1)
    last_year_end = end_date.replace(year=end_date.year - 1)

    last_year_logs = EmployeeServiceLog.objects.select_related("project").filter(
        date__range=(last_year_start, last_year_end)
    )

    for log in last_year_logs:
        shifted_date = log.date.replace(year=log.date.year + 1)
        key = get_key(shifted_date)

        if log.project_id not in last_year_map[key]["projects"]:
            last_year_map[key]["projects"].add(log.project_id)
            last_year_map[key]["revenue"] += log.project.invoice_amount

    labels = sorted(current_map.keys())

    projects_current = [len(current_map[k]["projects"]) for k in labels]
    projects_last_year = [len(last_year_map[k]["projects"]) for k in labels]

    revenue_current = [float(current_map[k]["revenue"]) for k in labels]
    revenue_last_year = [float(last_year_map[k]["revenue"]) for k in labels]

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

    all_projects = Project.objects.exclude(status="CANCELLED").order_by("-created_at")
    profit_map = {item["project"].id: (item["profit"], item["profit_percent"]) for item in project_analytics}

    service_completions = []
    for proj in all_projects:
        proj_logs = EmployeeServiceLog.objects.filter(project=proj)
        last_att_date = proj_logs.order_by("-date").values_list("date", flat=True).first()

        service_ref = (
            proj_logs.filter(service_reference__isnull=False)
            .exclude(service_reference="")
            .order_by("-date")
            .values_list("service_reference", flat=True)
            .first()
            or proj.code
            or "-"
        )
        vessel = proj.vessel_name or (proj_logs.first().vessel_name if proj_logs.first() else "-")

        completed_log = (
            ActivityLog.objects.filter(project=proj, new_status="COMPLETED")
            .order_by("-changed_at")
            .first()
        )
        comp_date = completed_log.changed_at.date() if completed_log else (proj.deadline_date if proj.status == "COMPLETED" else None)

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

        p_profit, p_profit_pct = profit_map.get(proj.id, (Decimal(0), Decimal(0)))

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
            "project_analytics": project_analytics,
            "projects_page": projects_page,
            "sort": sort,
            "top_clients": top_clients,
            "service_completions_page": service_completions_page,
            "sc_date": sc_date_str,

            # KPI values
            "kpi_total_projects": len(project_analytics),
            "kpi_total_hours": total_hours_all,
            "kpi_total_cost": total_cost_all,
            "kpi_revenue": total_revenue_all,
            "kpi_profit": total_profit_all,
            "kpi_expense": total_cost_all,
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

    logs = EmployeeServiceLog.objects.filter(project_id=project_id)

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
        EmployeeServiceLog.objects.select_related(
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
            Q(is_employee=True) | Q(is_manager=True) | Q(is_administration=True) | Q(is_hr=True)
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
            elif st == 'OFFICE_VESSEL':
                day_log_hours = sum((l.total_hours or Decimal(0)) for l in emp_logs_by_date.get(r['date'], []))
                day_office = max((r['total_hours'] or Decimal(0)) - day_log_hours, Decimal(0))
            else:
                day_office = Decimal(0)

            office_hours += day_office

            if rate > 0 and day_office > 0:
                day_normal = min(day_office, Decimal(8))
                day_ot = max(day_office - Decimal(8), Decimal(0))
                mult = Decimal('2.0') if r['is_off_day'] else Decimal('1.5')
                office_normal_cost += day_normal * rate
                office_ot_cost += day_ot * rate * mult

        total_hours = vessel_hours + office_hours

        ot_hours = sum(
            (r['ot_hours'] or Decimal(0))
            for r in emp_rows
            if r['status_code'] in ('OFFICE', 'VESSEL', 'OFFICE_VESSEL')
        )

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
            if (r['status_code'] not in ('SUNDAY', 'HOLIDAY')) or r['is_off_day'] or ((r['total_hours'] or Decimal(0)) > 0)
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
        is_working = (r['status_code'] not in ('SUNDAY', 'HOLIDAY')) or r['is_off_day'] or ((r['total_hours'] or Decimal(0)) > 0)
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

    vv_logs = EmployeeServiceLog.objects.select_related("employee", "employee__user", "project").all()
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



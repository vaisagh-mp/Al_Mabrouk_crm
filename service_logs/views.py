from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.shortcuts import redirect
from .models import EmployeeServiceLog
from Admin.models import Project, Employee
from django.http import HttpResponseForbidden
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404
from django.db.models import Sum, Count, Avg
from django.db.models.functions import TruncWeek, TruncMonth, TruncYear
from decimal import Decimal
from collections import defaultdict
from django.utils.timezone import now
from datetime import datetime
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
    elif is_employee(user):
        template = "service_logs/employee_create.html"
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

    return render(request, template)


@login_required
def service_log_edit(request, log_id):
    user = request.user

    if is_admin(user):
        return HttpResponseForbidden("Admins cannot edit service logs.")

    try:
        employee = user.employee_profile
    except:
        return HttpResponseForbidden("Employee profile not found.")

    log = get_object_or_404(EmployeeServiceLog, id=log_id)

    # 🔐 Permission check
    if is_employee(user) and log.employee != employee:
        return HttpResponseForbidden("You can only edit your own logs.")

    if is_manager(user):
        template = "service_logs/manager_edit.html"
    else:
        template = "service_logs/employee_edit.html"

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

    return render(request, template, {"log": log})

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
        for emp_id in project_logs.values_list("employee", flat=True).distinct():
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

    return render(
        request,
        "analytics/project_based.html",
        {
            "project_analytics": project_analytics,
            "projects_page": projects_page,
            "sort": sort,
            "top_clients": top_clients,

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

    for emp_id in logs.values_list("employee", flat=True).distinct():
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


@login_required
def analytics_employee(request):
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
    # EMPLOYEE ANALYTICS (ALL EMPLOYEES)
    # =====================================================
    employee_analytics = []

    all_employees = Employee.objects.select_related("user").all()

    for emp in all_employees:
        emp_logs = logs.filter(employee=emp)

        total_hours = emp_logs.aggregate(h=Sum("total_hours"))["h"] or Decimal(0)
        ot_hours = emp_logs.aggregate(o=Sum("ot_hours"))["o"] or Decimal(0)
        normal_cost = emp_logs.aggregate(n=Sum("normal_cost"))["n"] or Decimal(0)
        ot_cost = emp_logs.aggregate(o=Sum("ot_cost"))["o"] or Decimal(0)

        utilization = (
            round((total_hours / Decimal(26 * 8)) * 100, 2)
            if total_hours else Decimal(0)
        )

        project_data = []
        for pid in emp_logs.values_list("project", flat=True).distinct():
            p_logs = emp_logs.filter(project_id=pid)
            project = p_logs.first().project if p_logs.exists() else None

            project_data.append({
                "name": project.name if project else "-",
                "hours": p_logs.aggregate(h=Sum("total_hours"))["h"] or Decimal(0),
                "ot_hours": p_logs.aggregate(o=Sum("ot_hours"))["o"] or Decimal(0),
                "cost": (
                    (p_logs.aggregate(n=Sum("normal_cost"))["n"] or Decimal(0)) +
                    (p_logs.aggregate(o=Sum("ot_cost"))["o"] or Decimal(0))
                ),
            })

        employee_analytics.append({
            "employee": emp,
            "total_hours": total_hours,
            "ot_hours": ot_hours,
            "utilization": utilization,
            "normal_cost": normal_cost,
            "ot_cost": ot_cost,
            "total_cost": normal_cost + ot_cost,
            "projects": project_data,
        })

    # =====================================================
    # KPI CALCULATIONS (CORRECT)
    # =====================================================
    kpi_total_employees = all_employees.count()

    kpi_total_hours = sum(e["total_hours"] for e in employee_analytics)
    kpi_total_cost = sum(e["total_cost"] for e in employee_analytics)
    kpi_total_ot = sum(e["ot_hours"] for e in employee_analytics)

    kpi_total_projects = len(set(
        p["name"]
        for e in employee_analytics
        for p in e["projects"]
        if p["name"] and p["name"] != "-"
    ))

    kpi_avg_utilization = (
        sum(e["utilization"] for e in employee_analytics) / kpi_total_employees
        if kpi_total_employees else Decimal(0)
    )

    # ---------- UTILIZATION TREND (MONTHLY) ----------

    trend = request.GET.get("trend", "month")

    if trend == "week":
        trunc_func = TruncWeek
        label_fmt = "%d %b"
        capacity = Decimal(5 * 8 * kpi_total_employees)   # 5 working days
    elif trend == "year":
        trunc_func = TruncYear
        label_fmt = "%Y"
        capacity = Decimal(12 * 26 * 8 * kpi_total_employees)
    else:  # month (default)
        trunc_func = TruncMonth
        label_fmt = "%b %Y"
        capacity = Decimal(26 * 8 * kpi_total_employees)

    trend_qs = (
        logs
        .annotate(period=trunc_func("date"))
        .values("period")
        .annotate(total_hours=Sum("total_hours"))
        .order_by("period")
    )

    trend_labels = []
    trend_utilization = []

    MONTHLY_CAPACITY = Decimal(26 * 8 * kpi_total_employees) if kpi_total_employees else 0

    for row in trend_qs:
        trend_labels.append(row["period"].strftime(label_fmt))

        util = (
            (row["total_hours"] / capacity) * 100
            if capacity else 0
        )

        trend_utilization.append(float(round(util, 2)))
    
    employee_labels = []
    employee_cost_efficiency = []

    for e in employee_analytics:
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
    paginator = Paginator(employee_analytics, 10)
    page_obj = paginator.get_page(page_number)


    return render(
        request,
        "analytics/employee_based.html",
        {
            "employee_analytics": page_obj,
            "page_obj": page_obj,

            # KPI values
            "kpi_total_employees": kpi_total_employees,
            "kpi_total_hours": kpi_total_hours,
            "kpi_total_cost": kpi_total_cost,
            "kpi_total_ot": kpi_total_ot,
            "kpi_total_projects": kpi_total_projects,
            "kpi_avg_utilization": round(kpi_avg_utilization, 2),

            "trend": trend,
            "trend_labels": json.dumps(trend_labels),
            "trend_utilization": json.dumps(trend_utilization),
            "employee_labels": json.dumps(employee_labels),
            "employee_cost_efficiency": json.dumps(employee_cost_efficiency),

            "start_date": start_date,
            "end_date": end_date,
        }
    )



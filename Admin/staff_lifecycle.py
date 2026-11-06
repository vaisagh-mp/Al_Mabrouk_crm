from django.db import transaction
from django.utils import timezone
from Admin.models import Project, Team, WorkOrder, WorkOrderTime, Attendance, Leave


def delete_blockers(employee) -> list[str]:
    """
    Returns human-readable reasons why this employee cannot be deleted.
    Blocks deletion if the employee or user has any historical activity or active management roles.
    """
    blockers = []

    # 1. Service logs
    try:
        from service_logs.models import EmployeeServiceLog
        sl_count = EmployeeServiceLog.objects.filter(employee=employee).count()
        if sl_count:
            blockers.append(f"{sl_count} service log{'s' if sl_count != 1 else ''}")
    except Exception:
        pass

    # 2. Payroll months
    try:
        from finance.models import Payroll
        pr_count = Payroll.objects.filter(employee=employee).count()
        if pr_count:
            blockers.append(f"{pr_count} payroll month{'s' if pr_count != 1 else ''}")
    except Exception:
        pass

    # 3. Attendance records
    att_count = Attendance.objects.filter(employee=employee).count()
    if att_count:
        blockers.append(f"{att_count} attendance record{'s' if att_count != 1 else ''}")

    # 4. Work orders created
    if employee.user_id:
        wo_created = WorkOrder.objects.filter(created_by_id=employee.user_id).count()
        if wo_created:
            blockers.append(f"{wo_created} work order{'s' if wo_created != 1 else ''} created")

    # 5. Work order time rows
    wot_count = WorkOrderTime.objects.filter(employee=employee).count()
    if wot_count:
        blockers.append(f"{wot_count} work order time log{'s' if wot_count != 1 else ''}")

    # 6. Teams managed
    tm_count = Team.objects.filter(manager=employee).count()
    if tm_count:
        blockers.append(f"{tm_count} team{'s' if tm_count != 1 else ''} managed")

    # 7. Projects managed
    pm_count = Project.objects.filter(manager=employee).count()
    if pm_count:
        blockers.append(f"{pm_count} project{'s' if pm_count != 1 else ''} managed")

    # 8. Leave records
    if employee.user_id:
        leave_count = Leave.objects.filter(user_id=employee.user_id).count()
        if leave_count:
            blockers.append(f"{leave_count} leave record{'s' if leave_count != 1 else ''}")

    return blockers


@transaction.atomic
def deactivate_employee(employee, by_user) -> dict:
    """
    Deactivates an employee:
      - Sets user.is_active = False (blocks login).
      - Records deactivated_at and deactivated_by on the Employee profile.
      - Closes any open attendance session (log_out_time = now).
      - Removes employee from teams on active projects (not COMPLETED or CANCELLED),
        triggering signals that remove from live workorder.job_assigned_to and clean TeamMemberStatus.
      - Directly removes user from workorder.job_assigned_to on active projects.
      - Keeps user in WorkOrder.all_members (historical record).
      - Leaves Project.manager and Team.manager untouched, flagging them for manual reassignment.
    """
    user = employee.user
    if user:
        user.is_active = False
        user.save(update_fields=['is_active'])

    now = timezone.now()
    employee.deactivated_at = now
    employee.deactivated_by = by_user
    employee.save(update_fields=['deactivated_at', 'deactivated_by'])

    # Close open attendance sessions with auto punch-out cutoff
    from Admin.attendance_autoclose import capped_logout

    open_attendances = Attendance.objects.filter(employee=employee, log_out_time__isnull=True)
    closed_sessions = 0
    for att in open_attendances:
        logout_dt, was_capped = capped_logout(att, now)
        att.log_out_time = logout_dt
        if hasattr(att, 'auto_punched_out'):
            att.auto_punched_out = was_capped
        att.save()
        closed_sessions += 1

    # Active projects (status NOT IN ('COMPLETED', 'CANCELLED'))
    active_projects = Project.objects.exclude(status__in=['COMPLETED', 'CANCELLED'])

    # Remove from teams on active projects
    teams_left = []
    active_teams = Team.objects.filter(project__in=active_projects, employees=employee).select_related('project')
    for team in active_teams:
        teams_left.append(f"{team.name} ({team.project.name})")
        team.employees.remove(employee)

    # Remove from live members (job_assigned_to) on active projects
    work_orders_removed = []
    if user:
        active_wos = WorkOrder.objects.filter(project__in=active_projects, job_assigned_to=user)
        for wo in active_wos:
            wo_name = wo.work_order_number or f"WO #{wo.id}"
            work_orders_removed.append(wo_name)
            wo.job_assigned_to.remove(user)

    # Manager warnings (do NOT change Project.manager or Team.manager)
    managed_projects = list(
        Project.objects.filter(manager=employee).exclude(status__in=['COMPLETED', 'CANCELLED']).values_list('name', flat=True)
    )
    managed_teams = list(
        Team.objects.filter(manager=employee, project__in=active_projects).values_list('name', flat=True)
    )

    return {
        'closed_attendance_sessions': closed_sessions,
        'teams_left': teams_left,
        'work_orders_removed': work_orders_removed,
        'managed_projects_to_reassign': managed_projects,
        'managed_teams_to_reassign': managed_teams,
    }


@transaction.atomic
def reactivate_employee(employee, by_user) -> dict:
    """
    Reactivates an employee:
      - Sets user.is_active = True.
      - Clears deactivated_at and deactivated_by.
      - Does NOT re-add to teams or work orders.
    """
    user = employee.user
    if user:
        user.is_active = True
        user.save(update_fields=['is_active'])

    employee.deactivated_at = None
    employee.deactivated_by = None
    employee.save(update_fields=['deactivated_at', 'deactivated_by'])

    return {
        'message': "Reactivated. Add them back to teams if needed."
    }


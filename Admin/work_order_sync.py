import datetime
from django.utils import timezone
from django.contrib.auth.models import User
from Admin.models import WorkOrder, WorkOrderDetail, WorkOrderImage, Team, Employee


def sync_work_order_from_project(project, wo_fields=None, images=None, by_user=None):
    """
    Auto-creates or updates the OneToOne WorkOrder for a given Project.
    Copies vessel, client, and project_description directly from the project.
    Writes imo_no, location, assigned_date, job_scope, job_instructions from wo_fields.
    Syncs job_assigned_to from active team members; accumulates all_members.
    Saves uploaded images and ensures WorkOrderDetail exists.
    Returns (work_order, created).
    """
    wo_fields = wo_fields or {}

    work_order = getattr(project, 'workorder', None)
    if work_order is None:
        work_order = WorkOrder.objects.filter(project=project).first()

    created = False
    if work_order is None:
        created = True
        work_order = WorkOrder(project=project)
        if by_user:
            work_order.created_by = by_user
        elif project.manager and project.manager.user:
            work_order.created_by = project.manager.user
        else:
            work_order.created_by = User.objects.filter(is_superuser=True).first()

    # Always copy from the project (single source, not editable on the work order)
    work_order.vessel = project.vessel_name or "N/A"
    work_order.client = project.client_name or ""
    work_order.project_description = project.job_description or ""

    # Write fields from form if provided
    if 'imo_no' in wo_fields:
        work_order.imo_no = wo_fields.get('imo_no') or ""
    if 'location' in wo_fields:
        work_order.location = wo_fields.get('location') or ""
    if 'assigned_date' in wo_fields and wo_fields['assigned_date']:
        work_order.assigned_date = wo_fields['assigned_date']
    elif created and not work_order.assigned_date:
        work_order.assigned_date = timezone.now().date()

    if 'job_scope' in wo_fields:
        work_order.job_scope = wo_fields.get('job_scope') or ""
    if 'job_instructions' in wo_fields:
        work_order.job_instructions = wo_fields.get('job_instructions') or ""

    if created and by_user and not work_order.created_by:
        work_order.created_by = by_user

    work_order.save()

    # job_assigned_to: active users of the project's teams; all_members: add them (never remove)
    teams = Team.objects.filter(project=project)
    team_members = Employee.objects.filter(teams_assigned__in=teams, user__is_active=True).distinct()
    assigned_users = User.objects.filter(employee_profile__in=team_members, is_active=True).distinct()

    work_order.job_assigned_to.set(assigned_users)
    if assigned_users.exists():
        work_order.all_members.add(*assigned_users)

    # Save uploaded images as WorkOrderImage rows
    if images:
        for img in images:
            if img:
                WorkOrderImage.objects.create(work_order=work_order, image=img)

    # Ensure WorkOrderDetail exists
    WorkOrderDetail.objects.get_or_create(work_order=work_order)

    return work_order, created


from django.db.models.signals import post_save, pre_save, m2m_changed
from django.dispatch import receiver
from .models import Project, Notification, Team, TeamMemberStatus, Employee, Leave
from django.contrib.auth.models import User


# Signal to notify manager when a new project is assigned
@receiver(post_save, sender=Project)
def notify_manager_on_project_creation(sender, instance, created, **kwargs):
    if created and instance.manager:
        Notification.objects.create(
            recipient=instance.manager.user,  # Assuming Employee model has a OneToOne relation with User
            message=f"A new project '{instance.name}' has been assigned to you."
        )

# Signal to notify admin when project status changes
@receiver(pre_save, sender=Project)
def notify_admin_on_status_change(sender, instance, **kwargs):
    if instance.pk:  # Ensure this is an update and not a new creation
        old_project = Project.objects.get(pk=instance.pk)
        if old_project.status != instance.status:
            admin_users = User.objects.filter(is_superuser=True)  # Notify all superusers (admins)
            for admin in admin_users:
                Notification.objects.create(
                    recipient=admin,
                    message=f"Project '{instance.name}' status changed from '{old_project.status}' to '{instance.status}'."
                )


# Send a notification to employees when a new team is created
@receiver(m2m_changed, sender=Team.employees.through)
def notify_employees_on_team_assignment(sender, instance, action, pk_set, **kwargs):
    """
    Notify employees when they are added to a team.
    """
    if action == "post_add":  # Only trigger when employees are added
        for employee_id in pk_set:
            employee = Employee.objects.get(pk=employee_id)
            Notification.objects.create(
                recipient=employee.user,
                message=f"You have been added to the team '{instance.name}' for project '{instance.project.name}'."
            )

# Send a notification to the manager when project status changes
@receiver(pre_save, sender=TeamMemberStatus)
def notify_manager_and_admin_on_status_change(sender, instance, **kwargs):
    """Notify the team manager and all administration users when a team member's status changes."""
    if instance.pk:  # Ensure this is an update, not a new creation
        old_status = TeamMemberStatus.objects.get(pk=instance.pk).status
        if old_status != instance.status:
            message = f"Employee '{instance.employee.user.username}' status in project '{instance.team.project.name}' changed from '{old_status}' to '{instance.status}'."

            # Notify the manager
            if instance.team.manager:
                manager = instance.team.manager.user
                if not Notification.objects.filter(recipient=manager, message=message).exists():
                    Notification.objects.create(recipient=manager, message=message)

            # Notify all administration users
            admin_users = Employee.objects.filter(is_administration=True).select_related("user")
            for admin in admin_users:
                if not Notification.objects.filter(recipient=admin.user, message=message).exists():
                    Notification.objects.create(recipient=admin.user, message=message)
                    
@receiver(post_save, sender=Leave)
def notify_manager_and_hr_on_leave_application(sender, instance, created, **kwargs):
    """Notify the team manager and all HR users when an employee applies for leave."""
    if created:  # Trigger only when a new leave application is created
        # Get the employee instance
        employee = Employee.objects.get(user=instance.user)

        # Find the team where the employee belongs (assuming one team per employee)
        user_team = Team.objects.filter(employees=employee).first()

        # Notification message
        message = f"Employee '{instance.user.username}' has applied for {instance.leave_type} from {instance.from_date} to {instance.to_date}."

        # Notify the manager
        if user_team and user_team.manager:
            manager = user_team.manager.user  # Get manager's User instance
            if not Notification.objects.filter(recipient=manager, message=message).exists():
                Notification.objects.create(recipient=manager, message=message)

        # Notify all HR users
        hr_users = Employee.objects.filter(is_hr=True).select_related("user")
        for hr in hr_users:
            if not Notification.objects.filter(recipient=hr.user, message=message).exists():
                Notification.objects.create(recipient=hr.user, message=message)

@receiver(pre_save, sender=Leave)
def notify_employee_on_leave_status_change(sender, instance, **kwargs):
    """Notify employee when their leave request is approved or rejected."""
    if instance.pk:  # Ensure it's an update (not a new leave request)
        try:
            old_leave = Leave.objects.get(pk=instance.pk)
            if old_leave.approval_status != instance.approval_status and instance.approval_status in ['APPROVED', 'REJECTED']:
                Notification.objects.create(
                    recipient=instance.user,
                    message=f"Your leave request for {instance.leave_type} from {instance.from_date} to {instance.to_date} has been {instance.approval_status.lower()}."
                )
        except Leave.DoesNotExist:
            pass  # If the leave does not exist yet, skip
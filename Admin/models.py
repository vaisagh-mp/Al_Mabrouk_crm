from django.db import models
from django.db.models.signals import post_save, m2m_changed, pre_save, post_delete
from django.dispatch import receiver
from django.contrib.auth.models import User
from datetime import datetime
from datetime import timedelta
from django.db.models import Q
from decimal import Decimal


class Project(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('ASSIGN', 'Assign'),
        ('ONGOING', 'Ongoing'),
        ('HOLD', 'Hold'),
        ('CANCELLED', 'Cancelled'),
        ('COMPLETED', 'Completed'),
    ]

    CATEGORY_CHOICES = [
        ('OVERSEAS', 'Overseas'),
        ('ANCHORAGE', 'Anchorage'),
        ('HOLIDAY_WORKING', 'Holiday Working'),
    ]

    PRIORITY_CHOICES = [
        ('HIGH', 'High'),
        ('MEDIUM', 'Medium'),
        ('LOW', 'Low'),
    ]

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100, unique=True)
    client_name = models.CharField(max_length=255, default="", blank=True)
    purchase_and_expenses = models.DecimalField(
        max_digits=12, decimal_places=2, default=0.00)
    invoice_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0.00)
    currency_code = models.CharField(max_length=10, default="AED")
    status = models.CharField(
        max_length=100, choices=STATUS_CHOICES, default='PENDING')
    category = models.CharField(
        max_length=50, choices=CATEGORY_CHOICES, default='OVERSEAS')
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='MEDIUM')
    job_description = models.TextField(blank=True, null=True)
    manager = models.ForeignKey(
        'Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_projects',
        limit_choices_to={'is_manager': True}
    )
    created_at = models.DateTimeField(auto_now_add=True)
    deadline_date = models.DateField(null=True, blank=True)
    attachment = models.FileField(upload_to='project_attachments/', blank=True, null=True)

    def save(self, *args, **kwargs):
        # Automatically set status to 'ASSIGN' if a manager is assigned
        if self.manager and self.status == 'PENDING':
            self.status = 'ASSIGN'
        # Call the parent class's save method
        super().save(*args, **kwargs)

    def save(self, *args, **kwargs):
        if self.pk:
            old_project = Project.objects.get(pk=self.pk)
            if old_project.status != self.status:
                ActivityLog.objects.create(
                    project=self,
                    previous_status=old_project.status,
                    new_status=self.status,
                    changed_by=self.manager
                )
        super().save(*args, **kwargs)


    def __str__(self):
        return f"{self.name} ({self.code})"


    def calculate_expenses(self):
        """
        Calculate total project expenses:
        - Purchase and material expenses
        - Employee work costs (attendance-based time spent * hourly salary)
        """
        total_expenses = self.purchase_and_expenses  # Start with the purchase and material expenses
        project_assignments = self.projectassignment_set.all()
    
        for assignment in project_assignments:
            # Get the employee
            employee = assignment.employee
    
            # Fetch attendance records for the project and employee
            attendance_records = Attendance.objects.filter(employee=employee, project=self)
    
            # Calculate the total hours worked from attendance
            total_hours_worked = sum(record.total_hours_of_work or 0 for record in attendance_records)
    
            # Convert total_hours_worked to Decimal for precise calculations
            total_hours_worked = Decimal(total_hours_worked)
    
            # Add to total expenses (employee's work cost based on attendance)
            total_expenses += total_hours_worked * Decimal(employee.salary)
    
        return total_expenses


    def calculate_profit(self):
        """
        Calculate profit as:
        Invoice Amount - Total Expenses
        """
        return self.invoice_amount - self.calculate_expenses()
    
    def calculate_profit_percentage(self):
        """
        Calculate profit percentage as:
        (Invoice Amount - Total Expenses) / Invoice Amount * 100
        """
        profit = self.invoice_amount - self.calculate_expenses()
        if self.invoice_amount == 0:
            return 0  # Avoid division by zero
        return (profit / self.invoice_amount) * 100

    def calculate_total_work_days(self):
        """
        Calculate the total work days for this project as the difference 
        between the deadline_date and the created_at date.
        """
        if self.deadline_date and self.created_at:
            return (self.deadline_date - self.created_at.date()).days
        return 0  # Return 0 if either date is missing
    
    def calculate_revevenue(self):
        return self.invoice_amount - self.calculate_expenses()
    
    def calculate_revenue_percentage(self):
        """
        Calculate revenue percentage as:
        (Invoice Amount - Total Expenses) / Invoice Amount * 100
        """
        revenue = self.invoice_amount - self.calculate_expenses()
        if self.invoice_amount == 0:
            return 0  # Avoid division by zero
        return (revenue / self.invoice_amount) * 100


    def __str__(self):
        return self.name

class Team(models.Model):
    name = models.CharField(max_length=255)
    manager = models.ForeignKey(
        'Employee', on_delete=models.CASCADE, related_name='managed_teams',
        limit_choices_to={'is_manager': True}
    )
    employees = models.ManyToManyField(
        'Employee', related_name='teams_assigned',
        limit_choices_to={'is_employee': True}
    )
    project = models.ForeignKey(
        'Project', on_delete=models.CASCADE, related_name='teams'
    )

    def save(self, *args, **kwargs):
        # Update the project status to 'ASSIGN' if it's not already set
        if self.project.status != 'ASSIGN':
            self.project.status = 'ASSIGN'
            self.project.save()

        # Save the Team instance
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} (Managed by: {self.manager.user.username})"
    
# Signal to create TeamMemberStatus for each employee when a Team is saved
@receiver(m2m_changed, sender=Team.employees.through)
def create_team_member_status(sender, instance, action, reverse, model, pk_set, **kwargs):
    if action == 'post_add':  # When employees are added to the team
        for employee_id in pk_set:
            employee = model.objects.get(id=employee_id)
            # Create TeamMemberStatus for the employee in the team
            TeamMemberStatus.objects.get_or_create(
                team=instance,
                employee=employee,
                status='ASSIGN'
            )

class Employee(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='employee_profile')
    
    is_employee = models.BooleanField(default=False)
    is_manager = models.BooleanField(default=False)
    is_administration = models.BooleanField(default=False)
    is_hr = models.BooleanField(default=False)

    rank = models.CharField(max_length=255, blank=True, null=True)
    salary = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    work_days = models.DecimalField(max_digits=10, decimal_places=2, default=0, blank=True)
    holidays = models.IntegerField(null=True, blank=True)
    overseas_days = models.IntegerField(default=0)
    date_of_birth = models.DateField(null=True, blank=True)
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    date_of_join = models.DateField(null=True, blank=True)
    address = models.TextField(blank=True, null=True)
    profile_picture = models.ImageField(upload_to="profile_pictures/", null=True, blank=True)

    def calculate_work_days(self):
        """Automatically calculate total work days based on attendance records."""
        total_hours = sum(
            attendance.total_hours_of_work or 0 for attendance in self.attendance_set.all()
        )
        self.work_days = total_hours / 10  # assuming 10 hours = 1 workday
        self.save()

    def get_role(self):
        """Returns the role based on boolean flags."""
        if self.is_manager:
            return "Manager"
        if self.is_employee:
            return "Employee"
        if self.is_administration:
            return "Administration"
        if self.is_hr:
            return "HR"
        return "Unknown"
    
    def delete(self, *args, **kwargs):
        user = self.user  # Get associated user
        super().delete(*args, **kwargs)  # Delete employee
        user.delete()  # Delete user manually

    def __str__(self):
        return f"{self.user.username} - {self.get_role()} - {self.rank}"

class ProjectAssignment(models.Model):
    """
    Intermediate model to manage Employee-to-Project assignments.
    """
    project = models.ForeignKey('Project', on_delete=models.CASCADE)
    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, default=1)
    time_start = models.DateField() 
    time_stop = models.DateField()  

    def save(self, *args, **kwargs):
        # Update the project status to 'ASSIGN' if it's not already set
        if self.project.status != 'ASSIGN':
            self.project.status = 'ASSIGN'
            self.project.save()

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee.user.username} on {self.project.name}"

class Attendance(models.Model):
    LOCATION_CHOICES = [
        ('DMC', 'DMC Warehouse'),
        ('FUJ', 'FUJ'),
        ('KFK', 'KFK'),
        ('ABU', 'Abu Dhabi'),
        ('BDO', 'Bur Dubai Office'),
        ('OVERSEAS', 'Overseas'),
    ]

    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]

    ATTENDANCE_STATUS = [
        ('PRESENT', 'Present'),
        ('WORK FROM HOME', 'Work from Home'),
    ]

    employee = models.ForeignKey(
        Employee, related_name='attendance_set', on_delete=models.CASCADE
    )
    project = models.ForeignKey(
        'Project', on_delete=models.SET_NULL, null=True, blank=True, related_name='attendance_set'
    )  # New field added
    login_time = models.DateTimeField(null=True, blank=True)
    log_out_time = models.DateTimeField(null=True, blank=True)
    total_hours_of_work = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    location = models.CharField(max_length=20, choices=LOCATION_CHOICES, null=True, blank=True)
    attendance_status = models.CharField(max_length=20, choices=ATTENDANCE_STATUS, null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    rejection_reason = models.TextField(null=True, blank=True)
    rejected_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='rejected_attendance')
    travel_in_time = models.DateTimeField(null=True, blank=True)
    travel_out_time = models.DateTimeField(null=True, blank=True)
    total_travel_time = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    def save(self, *args, **kwargs):
        """Calculate total travel time before saving."""
        if self.travel_in_time and self.travel_out_time:
            if isinstance(self.travel_in_time, str):
                self.travel_in_time = datetime.strptime(self.travel_in_time, '%Y-%m-%dT%H:%M')
            if isinstance(self.travel_out_time, str):
                self.travel_out_time = datetime.strptime(self.travel_out_time, '%Y-%m-%dT%H:%M')
            
            travel_duration = self.travel_out_time - self.travel_in_time
            self.total_travel_time = round(travel_duration.total_seconds() / 3600, 2)  # Convert to hours
        else:
            self.total_travel_time = None  # Reset if travel_out_time is not set

        super().save(*args, **kwargs)

    def __str__(self):
        return f"Attendance for {self.employee.user.username} - {self.status}"
    
@receiver(post_delete, sender=Attendance)
def update_work_days_after_delete(sender, instance, **kwargs):
    """Recalculate work_days when an Attendance record is deleted."""
    instance.employee.calculate_work_days()

class Leave(models.Model):
    LEAVE_TYPE_CHOICES = [
        ('SICK LEAVE', 'Sick Leave'),
        ('ANNUAL LEAVE', 'Annual Leave'),
        ('CASUAL LEAVE', 'Casual Leave'),
    ]

    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)  # Assuming you're using the default User model
    leave_type = models.CharField(max_length=50, choices=LEAVE_TYPE_CHOICES)
    from_date = models.DateField()
    to_date = models.DateField()
    reason = models.TextField()
    no_of_days = models.IntegerField(editable=False)  # Automatically calculated
    approval_status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    medical_certificate = models.FileField(upload_to='medical_certificates/', null=True, blank=True)

    def save(self, *args, **kwargs):
        # Calculate leave duration
        leave_days = (self.to_date - self.from_date).days + 1
        # Ensure minimum leave is 1 day
        self.no_of_days = leave_days + 1 if leave_days == 0 else leave_days
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} - {self.leave_type} from {self.from_date} to {self.to_date} ({self.approval_status})"

class LeaveBalance(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="leave_balance")
    annual_leave = models.IntegerField(default=24)
    sick_leave = models.IntegerField(default=12) 
    casual_leave = models.IntegerField(default=6)

    def __str__(self):
        return f"{self.user.username} - Leave Balance"
    
class TeamMemberStatus(models.Model):
    STATUS_CHOICES = [
        ('ASSIGN', 'Assign'),
        ('ONGOING', 'Ongoing'),
        ('HOLD', 'Hold'),
        ('CANCELLED', 'Cancelled'),
        ('COMPLETED', 'Completed'),
    ]

    team = models.ForeignKey('Team', on_delete=models.CASCADE, related_name='team_members_status')
    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, related_name='project_statuses')
    status = models.CharField(max_length=100, choices=STATUS_CHOICES, default='ASSIGN')
    notes = models.TextField(max_length=2000, null=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('team', 'employee')

    def __str__(self):
        return f"{self.employee.user} - {self.team.project.name} ({self.status})"

    def save(self, *args, **kwargs):
        """ Log changes before saving and notify the team manager """
        if self.pk:  # Check if it's an update, not a new object
            old_status = TeamMemberStatus.objects.get(pk=self.pk).status
            if old_status != self.status:
                # Log activity
                ActivityLog.objects.create(
                    team_member_status=self,
                    previous_status=old_status,
                    new_status=self.status,
                    notes=self.notes
                )

                # Send notification to the team manager
                if self.team.manager:
                    Notification.objects.create(
                        recipient=self.team.manager.user,
                        message=f"Employee '{self.employee.user.username}' status in project '{self.team.project.name}' changed from '{old_status}' to '{self.status}'."
                    )

        super().save(*args, **kwargs)

class ActivityLog(models.Model):
    team_member_status = models.ForeignKey(
        'TeamMemberStatus', on_delete=models.CASCADE, null=True, blank=True,
        related_name='activity_logs'
    )
    project = models.ForeignKey(
        'Project', on_delete=models.CASCADE, null=True, blank=True, 
        related_name='project_logs'  # New relation for manager logs
    )
    previous_status = models.CharField(max_length=100, default="UNKNOWN")
    new_status = models.CharField(max_length=100)
    notes = models.TextField(null=True, blank=True)
    changed_by = models.ForeignKey(
        'Employee', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='status_changes'
    )  # To track who changed the status
    changed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.changed_by.user.username if self.changed_by else 'Unknown'} changed {self.previous_status} → {self.new_status}"


# Signal to update the employee's work_days after saving an attendance record
@receiver(post_save, sender=Attendance)
def update_employee_work_days(sender, instance, **kwargs):
    """
    Automatically recalculate work days when an attendance record is saved.
    """
    employee = instance.employee
    employee.calculate_work_days()


class Notification(models.Model):
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Notification for {self.recipient.username} - {self.message[:20]}"
    
    
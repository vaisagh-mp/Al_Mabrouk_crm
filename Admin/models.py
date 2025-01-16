from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
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

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100, unique=True)
    purchase_and_expenses = models.DecimalField(
        max_digits=12, decimal_places=2, default=0.00)
    invoice_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0.00)
    currency_code = models.CharField(max_length=10, default="USD")
    status = models.CharField(
        max_length=100, choices=STATUS_CHOICES, default='PENDING')
    category = models.CharField(
        max_length=50, choices=CATEGORY_CHOICES, default='OVERSEAS')  # New field added

    def __str__(self):
        return f"{self.name} ({self.code})"

    from decimal import Decimal

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
    
    def calculate_total_work_days(self):
        """
        Calculate the total work days for this project by summing the
        differences between time_stop and time_start for all project assignments.
        """
        total_duration = timedelta()  # Initialize total duration as 0

        # Iterate through all assignments related to this project
        for assignment in self.projectassignment_set.all():
            if assignment.time_start and assignment.time_stop:
                # Calculate the duration of each assignment
                total_duration += assignment.time_stop - assignment.time_start

        # Convert total duration to days
        total_days = total_duration.days  # Total days is just the difference in days

        # Return total work days as an integer
        return total_days

    def __str__(self):
        return self.name


class Employee(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='employee_profile')
    is_employee = models.BooleanField(default=False)
    is_manager = models.BooleanField(default=False)
    rank = models.CharField(max_length=255, blank=True, null=True)
    salary = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    work_days = models.DecimalField(max_digits=10, decimal_places=2, default=0, blank=True)  # Calculated field
    holidays = models.IntegerField(null=True, blank=True)
    overseas_days = models.IntegerField(default=0)

    def calculate_work_days(self):
        """
        Automatically calculate total work days based on attendance records.
        """
        total_hours = sum(
            attendance.total_hours_of_work for attendance in self.attendance_set.all())
        self.work_days = total_hours / 9  # 10 hours = 1 workday
        self.save()

    def __str__(self):
        return f"{self.user.username} - {self.rank}"


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
        ('LEAVE', 'Leave'),
        ('WORK FROM HOME', 'Work from Home'),
        ('SICK LEAVE', 'Sick Leave'),
        ('ANNUAL LEAVE', 'Annual Leave'),
        ('CASUAL LEAVE', 'Casual Leave'),
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

    def __str__(self):
        return f"Attendance for {self.employee.user.username} - {self.status}"



# Signal to update the employee's work_days after saving an attendance record
@receiver(post_save, sender=Attendance)
def update_employee_work_days(sender, instance, **kwargs):
    """
    Automatically recalculate work days when an attendance record is saved.
    """
    employee = instance.employee
    employee.calculate_work_days()

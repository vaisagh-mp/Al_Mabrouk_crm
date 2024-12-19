from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from datetime import timedelta
from django.db.models import Q


class Project(models.Model):
    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('INACTIVE', 'Inactive'),
    ]
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100, unique=True)
    purchase_and_expenses = models.DecimalField(
        max_digits=12, decimal_places=2, default=0.00)
    invoice_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0.00)
    currency_code = models.CharField(max_length=10, default="USD")
    status = models.CharField(
        max_length=100, choices=STATUS_CHOICES, default='ACTIVE')

    def calculate_expenses(self):
        """
        Calculate total project expenses:
        - Purchase and material expenses
        - Employee work costs (time spent * hourly salary)
        """
        total_expenses = self.purchase_and_expenses
        project_assignments = self.projectassignment_set.all()

        for assignment in project_assignments:
            # Total hours worked by the employee on this project
            hours_worked = sum(
                attendance.total_hours_of_work
                for attendance in assignment.employee.attendance_set.filter(
                    Q(login_time__gte=assignment.time_start) & Q(
                        log_out_time__lte=assignment.time_stop)
                )
            )
            # Add to total expenses
            total_expenses += hours_worked * assignment.employee.salary

        return total_expenses

    def calculate_profit(self):
        """
        Calculate profit as:
        Invoice Amount - Total Expenses
        """
        return self.invoice_amount - self.calculate_expenses()
    
    def calculate_total_work_hours(self):
        """
        Calculate the total work hours for this project by summing the
        differences between time_stop and time_start for all project assignments.
        """
        total_duration = timedelta()  # Initialize total duration as 0

        # Iterate through all assignments related to this project
        for assignment in self.projectassignment_set.all():
            if assignment.time_start and assignment.time_stop:
                # Calculate the duration of each assignment
                total_duration += assignment.time_stop - assignment.time_start

        # Convert total duration to hours as a decimal value
        total_hours = total_duration.total_seconds() / 3600
        return round(total_hours, 2)  # Round to 2 decimal places

    def __str__(self):
        return self.name


class Employee(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='employee_profile')
    rank = models.CharField(max_length=255)
    salary = models.DecimalField(
        max_digits=10, decimal_places=2)  # Hourly salary
    work_days = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, blank=True)  # Calculated field
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
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    time_start = models.DateTimeField()
    time_stop = models.DateTimeField()

    def __str__(self):
        return f"{self.employee.user.username} on {self.project.name}"


class Attendance(models.Model):
    STATUS_CHOICES = [
        ('DMC', 'DMC Warehouse'),
        ('FUJ', 'FUJ'),
        ('KFK', 'KFK'),
        ('ABU', 'Abu Dhabi'),
        ('BDO', 'Bur Dubai Office'),
        ('OVERSEAS', 'Overseas'),
        ('WORK FROM HOME', 'Work from Home'),
        ('SICK LEAVE', 'Sick Leave'),
        ('ANNUAL LEAVE', 'Annual Leave'),
    ]

    employee = models.ForeignKey(
        Employee, related_name='attendance_set', on_delete=models.CASCADE)
    login_time = models.DateTimeField()
    log_out_time = models.DateTimeField()
    total_hours_of_work = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)

    def save(self, *args, **kwargs):
        """
        Automatically calculate total hours of work when saving an attendance record.
        """
        if self.login_time and self.log_out_time:
            delta = self.log_out_time - self.login_time
            self.total_hours_of_work = delta.total_seconds() / 3600  # Convert seconds to hours
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Attendance - {self.employee.user.username} on {self.login_time.date()}"


# Signal to update the employee's work_days after saving an attendance record
@receiver(post_save, sender=Attendance)
def update_employee_work_days(sender, instance, **kwargs):
    """
    Automatically recalculate work days when an attendance record is saved.
    """
    employee = instance.employee
    employee.calculate_work_days()

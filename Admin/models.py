from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User

class Project(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Employee(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='employee_profile')
    rank = models.CharField(max_length=255)
    salary = models.DecimalField(max_digits=10, decimal_places=2)
    work_days = models.DecimalField(max_digits=10, decimal_places=2, default=0, blank=True)  # Calculated field
    holidays = models.IntegerField(null=True, blank=True)
    overseas_days = models.IntegerField(default=0)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    project_purchase_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    project_invoice = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    def calculate_work_days(self):
        # Automatically calculate total work days from attendance records
        total_hours = sum(attendance.total_hours_of_work for attendance in self.attendance_set.all())
        self.work_days = total_hours / 10  # 10 hours = 1 day
        self.save()

    def __str__(self):
        return f"{self.user.username} - {self.rank}"
    class Meta:
        verbose_name = "Employee"
        verbose_name_plural = "Employee Details"


class Attendance(models.Model):
    STATUS_CHOICES = [
        ('DMC', 'DMC Warehouse'),
        ('FUJ', 'FUJ'),
        ('KFK', 'KFK'),
        ('ABU', 'Abu Dhabi'),
        ('BDO', 'Bur Dubai Office '),
        ('OVERSEAS', 'Overseas'),
        ('WORK FROM HOME', 'work from home'),
        ('SICK LEAVE', 'sick leave'),
        ('ANNUAL LEAVE', 'annual leave'),
        # Add more status as needed
    ]

    employee = models.ForeignKey(Employee, related_name='attendance_set', on_delete=models.CASCADE)
    login_time = models.DateTimeField()
    log_out_time = models.DateTimeField()
    total_hours_of_work = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)

    def save(self, *args, **kwargs):
        if self.login_time and self.log_out_time:
            delta = self.log_out_time - self.login_time
            self.total_hours_of_work = delta.total_seconds() / 3600  # Convert seconds to hours
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Attendance - {self.employee.user.username} on {self.login_time.date()}"

    class Meta:
        verbose_name = "Attendance"
        verbose_name_plural = "Attendances"


# Signal to update the employee's work_days after saving an attendance record
@receiver(post_save, sender=Attendance)
def update_employee_work_days(sender, instance, **kwargs):
    # After each attendance record is saved, update the employee's work_days
    employee = instance.employee
    employee.calculate_work_days()

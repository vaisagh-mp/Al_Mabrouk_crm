from django.db import models
from Admin.models import Project, Employee
from datetime import datetime, timedelta
from decimal import Decimal


class EmployeeServiceLog(models.Model):

    SERVICE_TYPE_CHOICES = [
        ('INSPECTION', 'Inspection'),
        ('JOB', 'Job'),
    ]

    OT_TYPE_CHOICES = [
        ('WEEKDAY', 'Week Day'),
        ('HOLIDAY', 'Holiday'),
    ]

    LOCATION_TYPE_CHOICES = [
        ('ANCHORAGE', 'Anchorage'),
        ('SAILING', 'Sailing'),
    ]

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='service_logs'
    )

    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='service_logs'
    )

    date = models.DateField()

    vessel_name = models.CharField(max_length=255)
    port = models.CharField(max_length=255)

    service_type = models.CharField(
        max_length=20,
        choices=SERVICE_TYPE_CHOICES
    )

    service_reference = models.CharField(
        max_length=100,
        blank=True,
        null=True
    )

    start_time = models.TimeField()
    end_time = models.TimeField()

    total_hours = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        editable=False
    )

    ot_hours = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        editable=False
    )

    ot_type = models.CharField(
        max_length=20,
        choices=OT_TYPE_CHOICES,
        default='WEEKDAY',
        null=True,
        blank=True
    )

    location_type = models.CharField(
        max_length=20,
        choices=LOCATION_TYPE_CHOICES
    )

    is_holiday = models.BooleanField(default=False)

    normal_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        editable=False
    )

    ot_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        editable=False
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']
        indexes = [
            models.Index(fields=['employee', 'date']),
            models.Index(fields=['project']),
        ]

    def save(self, *args, **kwargs):
        
    
        start_dt = datetime.combine(self.date, self.start_time)
        end_dt = datetime.combine(self.date, self.end_time)
    
        if end_dt < start_dt:
            end_dt += timedelta(days=1)
    
        hours = Decimal((end_dt - start_dt).total_seconds() / 3600)
        self.total_hours = round(hours, 2)
    
        # =====================
        # OT CALCULATION
        # =====================
        if self.total_hours > 8:
            self.ot_hours = round(self.total_hours - 8, 2)
    
            # 🔴 FORCE OT TYPE
            self.ot_type = 'HOLIDAY' if self.is_holiday else 'WEEKDAY'
        else:
            self.ot_hours = Decimal(0)
            self.ot_type = None
            self.is_holiday = False
    
        # =====================
        # COST CALCULATION
        # =====================
        if self.employee.salary:
            monthly_salary = self.employee.salary
            hourly_rate = monthly_salary / Decimal(26 * 8)
    
            self.normal_cost = min(self.total_hours, 8) * hourly_rate
    
            if self.ot_hours > 0:
                ot_multiplier = Decimal(2) if self.is_holiday else Decimal(1.5)
                self.ot_cost = self.ot_hours * hourly_rate * ot_multiplier
            else:
                self.ot_cost = Decimal(0)
    
        super().save(*args, **kwargs)
    
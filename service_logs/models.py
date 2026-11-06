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
        ('SUNDAY', 'Sunday'),
        ('HOLIDAY', 'Public Holiday'),
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

    vessel_name = models.CharField(max_length=255, blank=True, default="")
    port = models.CharField(max_length=255, blank=True, default="")

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
    end_time = models.TimeField(null=True, blank=True)

    total_hours = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        editable=False,
        default=0
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
        choices=LOCATION_TYPE_CHOICES,
        default='ANCHORAGE'
    )

    is_holiday = models.BooleanField(default=False)

    normal_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        editable=False,
        default=0
    )

    ot_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        editable=False,
        default=0
    )

    # Link back to the WorkOrderTime that triggered auto-creation
    time_log = models.OneToOneField(
        'Admin.WorkOrderTime',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='service_log'
    )

    is_auto_created = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']
        indexes = [
            models.Index(fields=['employee', 'date']),
            models.Index(fields=['project']),
        ]

    @property
    def line_total_cost(self):
        return (self.normal_cost or Decimal('0')) + (self.ot_cost or Decimal('0'))

    @property
    def ot_mismatch(self):
        ot_h = self.ot_hours or Decimal('0')
        ot_c = self.ot_cost or Decimal('0')
        return (ot_c > Decimal('0') and ot_h == Decimal('0')) or (ot_h > Decimal('0') and ot_c == Decimal('0'))

    def save(self, *args, **kwargs):
        from service_logs.ot import recalculate_day_ot

        calculated_fields = {'total_hours', 'ot_hours', 'ot_type', 'normal_cost', 'ot_cost'}
        if 'update_fields' in kwargs and kwargs['update_fields'] is not None:
            kwargs['update_fields'] = set(kwargs['update_fields']) | calculated_fields

        old_emp_id = None
        old_date = None
        if self.pk:
            try:
                old_inst = EmployeeServiceLog.objects.only('employee_id', 'date').get(pk=self.pk)
                old_emp_id = old_inst.employee_id
                old_date = old_inst.date
            except EmployeeServiceLog.DoesNotExist:
                pass

        # If no end_time yet, zero everything out and skip calculation
        if self.end_time is None:
            self.total_hours = Decimal('0')
            self.ot_hours = Decimal('0')
            self.ot_type = None
            self.normal_cost = Decimal('0')
            self.ot_cost = Decimal('0')
            super().save(*args, **kwargs)
            recalculate_day_ot(self.employee_id, self.date)
            if (old_emp_id and old_date) and (old_emp_id != self.employee_id or old_date != self.date):
                recalculate_day_ot(old_emp_id, old_date)
            self.refresh_from_db(fields=['ot_hours', 'ot_type', 'normal_cost', 'ot_cost'])
            return

        start_dt = datetime.combine(self.date, self.start_time)
        end_dt = datetime.combine(self.date, self.end_time)

        if end_dt < start_dt:
            end_dt += timedelta(days=1)

        hours = Decimal(str((end_dt - start_dt).total_seconds() / 3600))
        self.total_hours = round(hours, 2)

        super().save(*args, **kwargs)

        recalculate_day_ot(self.employee_id, self.date)
        if (old_emp_id and old_date) and (old_emp_id != self.employee_id or old_date != self.date):
            recalculate_day_ot(old_emp_id, old_date)

        self.refresh_from_db(fields=['ot_hours', 'ot_type', 'normal_cost', 'ot_cost'])
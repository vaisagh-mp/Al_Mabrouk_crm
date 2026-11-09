from django.db import models
from django.db.models import Q
from django.contrib.auth.models import User
from Admin.models import Project, Employee
from datetime import datetime, timedelta
from decimal import Decimal


class EmployeeServiceLogQuerySet(models.QuerySet):
    def counting(self):
        from django.conf import settings
        if not getattr(settings, 'HR_TIMESHEET_APPROVAL', False):
            return self
        approval_from = getattr(settings, 'HR_TIMESHEET_APPROVAL_FROM', None)
        if approval_from:
            from datetime import date, datetime
            from django.utils.dateparse import parse_date
            app_from = approval_from if isinstance(approval_from, (date, datetime)) else parse_date(str(approval_from))
            if isinstance(app_from, datetime):
                app_from = app_from.date()
            if app_from:
                return self.filter(Q(approval_status='APPROVED') | Q(date__lt=app_from))
        return self.filter(approval_status='APPROVED')


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

    APPROVAL_STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('SUBMITTED', 'Submitted'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
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

    break_minutes = models.PositiveIntegerField(default=0)
    travel_minutes = models.PositiveIntegerField(default=0)

    approval_status = models.CharField(
        max_length=20,
        choices=APPROVAL_STATUS_CHOICES,
        default='DRAFT'
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    approver = models.ForeignKey(
        'Admin.Employee',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='service_logs_to_approve'
    )
    decided_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='decided_service_logs'
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_remarks = models.TextField(blank=True, default="")

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

    objects = EmployeeServiceLogQuerySet.as_manager()

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

    def is_counting(self):
        from django.conf import settings
        if not getattr(settings, 'HR_TIMESHEET_APPROVAL', False):
            return True
        approval_from = getattr(settings, 'HR_TIMESHEET_APPROVAL_FROM', None)
        if approval_from:
            from datetime import date, datetime
            from django.utils.dateparse import parse_date
            app_from = approval_from if isinstance(approval_from, (date, datetime)) else parse_date(str(approval_from))
            if isinstance(app_from, datetime):
                app_from = app_from.date()
            if app_from and self.date < app_from:
                return True
        return self.approval_status == 'APPROVED'

    def clean(self):
        super().clean()
        if self.date and self.start_time and self.end_time:
            start_dt = datetime.combine(self.date, self.start_time)
            end_dt = datetime.combine(self.date, self.end_time)
            if end_dt < start_dt:
                end_dt += timedelta(days=1)
            duration_minutes = int((end_dt - start_dt).total_seconds() // 60)
            if (self.break_minutes or 0) >= duration_minutes:
                from django.core.exceptions import ValidationError
                raise ValidationError("Break duration must be less than working duration.")

        if self.employee_id and self.date and self.start_time and self.end_time:
            from Admin.timesheet_helpers import check_timesheet_overlap
            err = check_timesheet_overlap(
                self.employee_id, self.date, self.start_time, self.end_time,
                exclude_service_log_id=self.pk,
                exclude_time_log_id=self.time_log_id
            )
            if err:
                from django.core.exceptions import ValidationError
                raise ValidationError(err)


    def save(self, *args, **kwargs):
        from service_logs.ot import recalculate_day_ot
        from django.conf import settings

        if not getattr(settings, 'HR_TIMESHEET_APPROVAL', False):
            if self.approval_status in ('DRAFT', None, ''):
                self.approval_status = 'APPROVED'
                if 'update_fields' in kwargs and kwargs['update_fields'] is not None:
                    kwargs['update_fields'] = set(kwargs['update_fields']) | {'approval_status'}

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

        dur_mins = Decimal(str(int((end_dt - start_dt).total_seconds() // 60)))
        brk = Decimal(str(self.break_minutes or 0))
        trv = Decimal(str(self.travel_minutes or 0))
        from django.conf import settings
        travel_counts = getattr(settings, 'HR_TRAVEL_COUNTS_AS_WORK', True)
        work_mins = dur_mins - brk + (trv if travel_counts else Decimal('0'))
        hours = max(Decimal('0'), work_mins / Decimal('60'))
        self.total_hours = round(hours, 2)

        super().save(*args, **kwargs)

        recalculate_day_ot(self.employee_id, self.date)
        if (old_emp_id and old_date) and (old_emp_id != self.employee_id or old_date != self.date):
            recalculate_day_ot(old_emp_id, old_date)

        self.refresh_from_db(fields=['ot_hours', 'ot_type', 'normal_cost', 'ot_cost'])
import os
from django.db import models
from django.core.exceptions import ValidationError
from django.utils.deconstruct import deconstructible
from django.core.files.storage import FileSystemStorage
from django.db.models.functions import Substr, Cast
from django.db.models import Max, IntegerField
from django.db.models.signals import post_save, m2m_changed, pre_save, post_delete
from django.dispatch import receiver
from django.contrib.auth.models import User
from datetime import datetime
from datetime import timedelta
from datetime import datetime
from django.db.models import Q
from decimal import Decimal


class Project(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('ASSIGN', 'Assign'),
        ('ONGOING', 'Ongoing'),
        ('QUOTED', 'Quoted'),
        ('HOLD', 'Hold'),
        ('CANCELLED', 'Cancelled'),
        ('COMPLETED', 'Completed'),
    ]

    CATEGORY_CHOICES = [
        ('OVERSEAS', 'Overseas'),
        ('ANCHORAGE', 'Anchorage'),
        ('HOLIDAY_WORKING', 'Holiday Working'),
        ('AT_BERTH', 'At Berth'),
    ]

    PRIORITY_CHOICES = [
        ('HIGH', 'High'),
        ('MEDIUM', 'Medium'),
        ('LOW', 'Low'),
    ]

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100, unique=True, blank=True, null=True)
    service_reference = models.CharField(max_length=100, blank=True, null=True)
    client_name = models.CharField(max_length=255, default="", blank=True)
    vessel_name = models.CharField(max_length=255, default="", blank=True)
    # DEPRECATED: Replaced by finance.JobCost lines. Kept for backwards compatibility.
    purchase_and_expenses = models.DecimalField(
        max_digits=12, decimal_places=2, default=0.00)
    # DEPRECATED: Replaced by finance.Invoice. Kept for backwards compatibility; stop writing in new code.
    invoice_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0.00)
    currency_code = models.CharField(max_length=10, default="AED")
    status = models.CharField(
        max_length=100, choices=STATUS_CHOICES, default='PENDING')
    category = models.CharField(
        max_length=50, choices=CATEGORY_CHOICES, default='AT_BERTH')
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='MEDIUM')
    job_description = models.TextField(blank=True, null=True)
    manager = models.ForeignKey(
        'Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_projects',
        limit_choices_to={'is_manager': True}
    )
    created_at = models.DateTimeField(auto_now_add=True)
    deadline_date = models.DateField(null=True, blank=True)
    attachment = models.FileField(upload_to='project_attachments/', blank=True, null=True)
    job_card = models.FileField(upload_to='job_cards/', blank=True, null=True)

    PAYMENT_STATUS_CHOICES = [
        ('UNPAID', 'Unpaid'),
        ('PARTIAL', 'Partial'),
        ('PAID', 'Paid'),
    ]
    # DEPRECATED: Replaced by finance.Invoice. Kept for backwards compatibility; stop writing in new code.
    invoice_number = models.CharField(max_length=50, blank=True, null=True)
    invoice_date = models.DateField(null=True, blank=True)
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='UNPAID')
    amount_received = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    payment_received_date = models.DateField(null=True, blank=True)

    @property
    def outstanding_amount(self):
        inv = self.invoice_amount or Decimal('0.00')
        rec = self.amount_received or Decimal('0.00')
        return max(inv - rec, Decimal('0.00'))


    def save(self, *args, **kwargs):
        # Automatically set status to 'ASSIGN' if a manager is assigned
        if self.manager and self.status == 'PENDING':
            self.status = 'ASSIGN'
        # Call the parent class's save method
        super().save(*args, **kwargs)

    # def save(self, *args, **kwargs):
        # log_entries = []
    
        # if self.pk:
            # old_project = Project.objects.get(pk=self.pk)
    
            # 1. Log status change
            # if old_project.status != self.status:
            #     log_entries.append(ActivityLog(
            #         project=self,
            #         previous_status=old_project.status,
            #         new_status=self.status,
            #         notes=f"Project status changed to {self.status}",
            #         changed_by=self.manager
            #     ))
    
            # 2. Log attachment change
            # if old_project.attachment != self.attachment:
            #     prev_status = "Attachment Exists" if old_project.attachment else "No Attachment"
            #     new_status = "Attachment Uploaded"
            #     notes = (
            #         f"Attachment replaced with {self.attachment.name} by manager."
            #         if old_project.attachment else
            #         f"{self.attachment.name} uploaded by manager."
            #     )
            #     log_entries.append(ActivityLog(
            #         project=self,
            #         previous_status=prev_status,
            #         new_status=new_status,
            #         notes=notes,
            #         changed_by=self.manager
            #     ))
    
        # Save the project first
        # super().save(*args, **kwargs)
    
        # Save logs after saving the project (so foreign key is valid)
        # if log_entries:
        #     ActivityLog.objects.bulk_create(log_entries)
    


    def __str__(self):
        return f"{self.name} ({self.code})"


    def calculate_expenses(self):
        """
        Calculate total project expenses:
        - Purchase and material expenses
        (Temporarily excluding Employee work costs)
        """
        total_expenses = self.purchase_and_expenses  # Only material & purchase costs

        # --- Temporarily disabled employee cost calculation ---
        # project_assignments = self.projectassignment_set.all()
        # for assignment in project_assignments:
        #     employee = assignment.employee
        #     attendance_records = Attendance.objects.filter(employee=employee, project=self)
        #     total_hours_worked = sum(record.total_hours_of_work or 0 for record in attendance_records)
        #     total_hours_worked = Decimal(total_hours_worked)
        #     total_expenses += total_hours_worked * Decimal(employee.salary)

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
    
    def calculate_revenue(self):
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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
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
def update_work_order_assignees(sender, instance, action, reverse, model, pk_set, **kwargs):
    if action == 'post_add':
        for employee_id in pk_set:
            employee = model.objects.get(id=employee_id)

            # 1. Create TeamMemberStatus
            TeamMemberStatus.objects.update_or_create(
                team=instance,
                employee=employee,
                defaults={'status': 'ASSIGN'}
            )

            # 2. Add to WorkOrder
            if hasattr(instance.project, 'workorder') and employee.user:
                instance.project.workorder.job_assigned_to.add(employee.user)

    elif action == 'post_remove':
        for employee_id in pk_set:
            employee = model.objects.get(id=employee_id)

            # Remove from WorkOrder
            if hasattr(instance.project, 'workorder') and employee.user:
                instance.project.workorder.job_assigned_to.remove(employee.user)

@receiver(m2m_changed, sender=Team.employees.through)
def sync_team_members_status(sender, instance, action, reverse, pk_set, **kwargs):
    """
    Keep TeamMemberStatus in sync when employees are added/removed from a Team
    """
    if action == "post_add":
        # Add missing TeamMemberStatus for new employees
        for employee_id in pk_set:
            TeamMemberStatus.objects.get_or_create(
                team=instance,
                employee_id=employee_id,
                defaults={'status': 'ASSIGN'}
            )

    elif action == "post_remove":
        # Delete TeamMemberStatus for removed employees
        TeamMemberStatus.objects.filter(
            team=instance,
            employee_id__in=pk_set
        ).delete()

    elif action == "post_clear":
        # If all employees removed, clear statuses too
        TeamMemberStatus.objects.filter(team=instance).delete()


class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class Branch(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


EMPLOYMENT_STATUS_CHOICES = [
    ('ACTIVE', 'Active'),
    ('PROBATION', 'Probation'),
    ('NOTICE_PERIOD', 'Notice Period'),
    ('RESIGNED', 'Resigned'),
]

WORK_TYPE_CHOICES = [
    ('OFFICE', 'Office'),
    ('VESSEL', 'Vessel'),
    ('OFFICE_VESSEL', 'Office & Vessel'),
]


def generate_next_employee_code():
    """
    Generate sequential AMMS-0001 code.
    Race-safe with select_for_update.
    """
    import re
    from django.db import transaction
    with transaction.atomic():
        codes = list(Employee.objects.select_for_update().filter(employee_code__startswith='AMMS-').values_list('employee_code', flat=True))
        max_num = 0
        for code in codes:
            m = re.match(r'^AMMS-(\d+)$', code or '')
            if m:
                val = int(m.group(1))
                if val > max_num:
                    max_num = val
        return f"AMMS-{max_num + 1:04d}"


class Employee(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='employee_profile')
    
    is_employee = models.BooleanField(default=False)
    is_manager = models.BooleanField(default=False)
    is_administration = models.BooleanField(default=False)
    is_hr = models.BooleanField(default=False)
    is_accountant = models.BooleanField(default=False)

    employee_code = models.CharField(max_length=20, unique=True, null=True, blank=True)
    personal_email = models.EmailField(blank=True, null=True)
    company_email = models.EmailField(blank=True, null=True)
    nationality = models.CharField(max_length=100, blank=True, null=True)
    emergency_contact_number = models.CharField(max_length=20, blank=True, null=True)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='employees')
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name='employees')
    reporting_manager = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='direct_reports')
    EMPLOYMENT_STATUS_CHOICES = EMPLOYMENT_STATUS_CHOICES
    WORK_TYPE_CHOICES = WORK_TYPE_CHOICES
    employment_status = models.CharField(max_length=20, choices=EMPLOYMENT_STATUS_CHOICES, default='ACTIVE')
    work_type = models.CharField(max_length=20, choices=WORK_TYPE_CHOICES, default='OFFICE')
    contract_start_date = models.DateField(null=True, blank=True)
    contract_end_date = models.DateField(null=True, blank=True)
    notice_start_date = models.DateField(null=True, blank=True)
    last_working_day = models.DateField(null=True, blank=True)

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
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='deactivated_employees')

    @property
    def current_salary_structure(self):
        from django.utils import timezone
        today = timezone.localdate()
        return self.salary_structures.filter(
            effective_from__lte=today
        ).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gte=today)
        ).order_by('-effective_from', '-created_at').first()

    @property
    def upcoming_salary_structure(self):
        from django.utils import timezone
        today = timezone.localdate()
        return self.salary_structures.filter(
            effective_from__gt=today
        ).order_by('effective_from').first()

    def save(self, *args, **kwargs):
        if not self.employee_code:
            self.employee_code = generate_next_employee_code()
        super().save(*args, **kwargs)


    @property
    def name(self):
        if self.user:
            return self.user.get_full_name() or self.user.username
        return f"Employee #{self.pk}"

    def calculate_work_days(self):
        """
        Calculates total work days as total_hours / 10,
        allowing for fractional values (e.g., 0.5 for half day).
        """
        total_hours = sum(
            attendance.total_hours_of_work or 0
            for attendance in self.attendance_set.filter(status="APPROVED")
        )
        self.work_days = round(total_hours / 10, 2)  # Keep 2 decimal places for precision
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
        if self.is_accountant:
            return "Accountant"
        return "Unknown"

    def clean(self):
        super().clean()
        if self.notice_start_date and self.last_working_day and self.last_working_day < self.notice_start_date:
            raise ValidationError({'last_working_day': "Last working day cannot be earlier than notice start date."})
        if self.employment_status == 'NOTICE_PERIOD' and not self.notice_start_date:
            raise ValidationError({'notice_start_date': "Notice start date is required when employment status is Notice Period."})
        if self.employment_status == 'RESIGNED' and not self.last_working_day:
            raise ValidationError({'last_working_day': "Last working day is required when employment status is Resigned."})

    def delete(self, *args, **kwargs):
        user = self.user  # Get associated user
        super().delete(*args, **kwargs)  # Delete employee
        user.delete()  # Delete user manually

    def __str__(self):
        return f"{self.user.username} - {self.get_role()} - {self.rank}"

    @property
    def current_duty_schedule(self):
        from django.utils import timezone
        today = timezone.localdate()
        sched = self.schedules.filter(
            effective_from__lte=today
        ).filter(
            models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=today)
        ).order_by('-effective_from', '-created_at').first()
        return sched.duty_schedule if sched else None

    @property
    def current_rest_day_rule(self):
        from django.utils import timezone
        today = timezone.localdate()
        rule = self.rest_day_rules.filter(
            effective_from__lte=today
        ).filter(
            models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=today)
        ).order_by('-effective_from', '-created_at').first()
        return rule

    @property
    def current_rest_day_display(self):
        rule = self.current_rest_day_rule
        if not rule:
            return "Sunday"
        if rule.uses_roster:
            return "Roster"
        if rule.rest_weekday is not None:
            return dict(WEEKDAY_CHOICES).get(rule.rest_weekday, "Sunday")
        return "Sunday"


class SalaryStructure(models.Model):
    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, related_name='salary_structures')
    basic = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    housing_allowance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    transport_allowance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    other_allowances = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_salary_structures')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-effective_from', '-created_at']

    def clean(self):
        super().clean()
        if self.effective_to and self.effective_from and self.effective_to < self.effective_from:
            from django.core.exceptions import ValidationError
            raise ValidationError("effective_to cannot be earlier than effective_from.")

    def save(self, *args, **kwargs):
        if self.effective_to and self.effective_from and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot be earlier than effective_from.")
        self.basic = Decimal(str(self.basic or '0.00'))
        self.housing_allowance = Decimal(str(self.housing_allowance or '0.00'))
        self.transport_allowance = Decimal(str(self.transport_allowance or '0.00'))
        self.other_allowances = Decimal(str(self.other_allowances or '0.00'))
        self.total = self.basic + self.housing_allowance + self.transport_allowance + self.other_allowances
        super().save(*args, **kwargs)
        if self.employee_id:
            from django.utils import timezone
            today = timezone.localdate()
            if self.effective_from <= today and (self.effective_to is None or self.effective_to >= today):
                Employee.objects.filter(pk=self.employee_id).update(salary=self.total)
                if hasattr(self.employee, 'salary'):
                    self.employee.salary = self.total

    def __str__(self):
        return f"{self.employee} ({self.effective_from} - {self.effective_to or 'present'}): {self.total}"


class EmployeeChangeLog(models.Model):
    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, related_name='change_logs')
    field = models.CharField(max_length=100)
    old_value = models.TextField(null=True, blank=True)
    new_value = models.TextField(null=True, blank=True)
    changed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='employee_change_logs')
    changed_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=100)

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return f"{self.employee} - {self.field}: {self.old_value} -> {self.new_value} ({self.source})"


def update_employee_salary_structure(employee, basic, housing_allowance=0, transport_allowance=0, other_allowances=0, effective_from=None, user=None, notes="", old_salary=None):
    """
    Updates or creates salary structure for employee:
    - (a) close every structure covering new_from (open or with effective_to >= new_from and effective_from < new_from) at new_from - 1
    - (b) if a structure starts after new_from, set the new structure's effective_to = that start - 1. Never leave two structures covering the same day.
    - Sets employee.salary = new total (if current)
    - Writes EmployeeChangeLog entries
    """
    from datetime import timedelta
    from django.utils import timezone
    from django.db import transaction

    if effective_from is None:
        effective_from = timezone.localdate()

    basic = Decimal(str(basic or '0.00'))
    housing = Decimal(str(housing_allowance or '0.00'))
    transport = Decimal(str(transport_allowance or '0.00'))
    other = Decimal(str(other_allowances or '0.00'))
    new_total = basic + housing + transport + other

    with transaction.atomic():
        if old_salary is not None:
            old_total = Decimal(str(old_salary))
        else:
            old_total = employee.salary or Decimal('0.00')

        day_before = effective_from - timedelta(days=1)

        # (a) close every structure covering new_from (open or with effective_to >= new_from and effective_from < new_from) at new_from - 1
        covering_structs = employee.salary_structures.filter(
            effective_from__lt=effective_from
        ).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gte=effective_from)
        )
        for cs in covering_structs:
            cs.effective_to = day_before
            cs.save(update_fields=['effective_to'])

        # Never leave two structures covering the same day: remove any existing starting on same day
        employee.salary_structures.filter(effective_from=effective_from).delete()

        # (b) if a structure starts after new_from, set the new structure's effective_to = that start - 1
        next_struct = employee.salary_structures.filter(
            effective_from__gt=effective_from
        ).order_by('effective_from').first()

        new_effective_to = (next_struct.effective_from - timedelta(days=1)) if next_struct else None

        new_struct = SalaryStructure.objects.create(
            employee=employee,
            basic=basic,
            housing_allowance=housing,
            transport_allowance=transport,
            other_allowances=other,
            total=new_total,
            effective_from=effective_from,
            effective_to=new_effective_to,
            notes=notes,
            created_by=user
        )

        today = timezone.localdate()
        if effective_from <= today and (new_effective_to is None or new_effective_to >= today):
            employee.salary = new_total
            Employee.objects.filter(pk=employee.pk).update(salary=new_total)

            EmployeeChangeLog.objects.create(
                employee=employee,
                field='salary',
                old_value=str(old_total),
                new_value=str(new_total),
                changed_by=user,
                source='Salary'
            )

    return new_struct


def log_employee_changes(employee, old_dict, new_dict, changed_by=None, source='HR edit'):
    """
    Logs changes to employee fields in EmployeeChangeLog.
    """
    logs = []
    for field, new_val in new_dict.items():
        old_val = old_dict.get(field)
        if str(old_val if old_val is not None else '') != str(new_val if new_val is not None else ''):
            logs.append(EmployeeChangeLog(
                employee=employee,
                field=field,
                old_value=str(old_val) if old_val is not None else '',
                new_value=str(new_val) if new_val is not None else '',
                changed_by=changed_by,
                source=source
            ))
    if logs:
        EmployeeChangeLog.objects.bulk_create(logs)
    return logs


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


class Vessel(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Attendance(models.Model):
    LOCATION_CHOICES = [
        ('DMC', 'DMC Warehouse'),
        ('DMC_PRT', 'DMC Port'),
        ('FUJ', 'FUJ'),
        ('KFK', 'KFK'),
        ('ABU', 'Abu Dhabi'),
        ('BDO', 'Bur Dubai Office'),
        ('SHJ_KP', 'Sharjah Khalid Port'),
        ('SHJ_HP', 'Sharjah Hamriya Port'),
        ('AJM_PRT', 'Ajman Port'),
        ('ABU_KP', 'Abu Dhabi Khalifa Port'),
        ('ABU_MP', 'Abu Dhabi Mussafah Port'),
        ('RAK_JZP', 'RAK Al Jazeera Port'),
        ('RAK_MCP', 'RAK Maritime City'),
        ('RAK_SRP', 'RAK Steven Rock Port'),
        ('DXB_DMC', 'Dubai Maritime City Port'),
        ('DXB_PR', 'Port Rashid'),
        ('DXB_HP', 'Dubai Hamriya Port'),
        ('DXB_MHJ', 'Dubai Marina Harbor Jumeirah'),
        ('DXB_AJD', 'Al Jadaf Port Dubai'),
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
    vessel = models.ForeignKey(
    Vessel, on_delete=models.SET_NULL, null=True, blank=True, related_name='attendance_records')
    attendance_status = models.CharField(max_length=20, choices=ATTENDANCE_STATUS, null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    rejection_reason = models.TextField(null=True, blank=True)
    rejected_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='rejected_attendance')
    travel_in_time = models.DateTimeField(null=True, blank=True)
    travel_out_time = models.DateTimeField(null=True, blank=True)
    total_travel_time = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    auto_punched_out = models.BooleanField(default=False)
    missing_punch_resolved = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        """Calculate total travel time and total work time before saving."""
        # Total Travel Time
        if self.travel_in_time and self.travel_out_time:
            if isinstance(self.travel_in_time, str):
                self.travel_in_time = datetime.strptime(self.travel_in_time, '%Y-%m-%dT%H:%M')
            if isinstance(self.travel_out_time, str):
                self.travel_out_time = datetime.strptime(self.travel_out_time, '%Y-%m-%dT%H:%M')

            travel_duration = self.travel_out_time - self.travel_in_time
            self.total_travel_time = round(travel_duration.total_seconds() / 3600, 2)
        else:
            self.total_travel_time = None

        # Total Work Hours
        if self.login_time and self.log_out_time:
            work_duration = self.log_out_time - self.login_time
            self.total_hours_of_work = round(work_duration.total_seconds() / 3600, 2)
        else:
            self.total_hours_of_work = None

        super().save(*args, **kwargs)

        # Recalculate work_days only if attendance is approved
        if self.status == "APPROVED":
            self.employee.calculate_work_days()

    def __str__(self):
        return f"Attendance for {self.employee.user.username} - {self.status}"
    
@receiver(post_delete, sender=Attendance)
def update_work_days_after_delete(sender, instance, **kwargs):
    """Recalculate work_days when an Attendance record is deleted."""
    instance.employee.calculate_work_days()

class LeaveType(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    is_paid = models.BooleanField(default=True)
    uses_balance = models.BooleanField(default=True)
    allows_half_day = models.BooleanField(default=False)
    certificate_required_after_days = models.IntegerField(default=0)
    register_code = models.CharField(max_length=50, default='ANNUAL_LEAVE')
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return f"{self.name} ({self.code})"


class LeavePolicy(models.Model):
    leave_type = models.OneToOneField(LeaveType, on_delete=models.CASCADE, related_name='policy')
    accrual_rule = models.CharField(max_length=50, blank=True, default='')  # 'UAE_ANNUAL'
    yearly_entitlement = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    carry_forward_max = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    leave_year_start = models.IntegerField(default=1)  # Month 1 = January
    leave_year_end = models.IntegerField(default=12)  # Month 12 = December
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Policy for {self.leave_type.code}"


class Leave(models.Model):
    LEAVE_TYPE_CHOICES = [
        ('SICK LEAVE', 'Sick Leave'),
        ('ANNUAL LEAVE', 'Annual Leave'),
        ('PERSONAL LEAVE', 'Personal Leave'),
        ('UNPAID LEAVE', 'Unpaid Leave'),
    ]

    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('CLARIFICATION', 'Clarification Requested'),
        ('CANCELLED', 'Cancelled'),
    ]
    APPROVAL_STATUS_CHOICES = STATUS_CHOICES

    user = models.ForeignKey(User, on_delete=models.CASCADE)  # Assuming you're using the default User model
    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, null=True, blank=True, related_name='leaves')
    leave_type = models.CharField(max_length=50, choices=LEAVE_TYPE_CHOICES)
    leave_type_ref = models.ForeignKey(LeaveType, on_delete=models.SET_NULL, null=True, blank=True, related_name='leaves')
    from_date = models.DateField()
    to_date = models.DateField()
    is_half_day = models.BooleanField(default=False)
    half_day_part = models.CharField(max_length=5, choices=[('AM', 'Morning (AM)'), ('PM', 'Afternoon (PM)')], null=True, blank=True)
    days = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    reason = models.TextField()
    no_of_days = models.IntegerField(editable=False, null=True, blank=True)  # Automatically calculated / V1 compatibility
    approval_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    medical_certificate = models.FileField(upload_to='medical_certificates/', null=True, blank=True)
    approver = models.ForeignKey('Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_leaves')
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='decided_leaves')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_remarks = models.TextField(blank=True, default='')
    clarification_request = models.TextField(blank=True, default='')
    clarification_response = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.employee and self.user_id:
            self.employee = getattr(self.user, 'employee_profile', None)
        if not self.user_id and self.employee_id:
            self.user = self.employee.user
        if self.is_half_day:
            self.days = Decimal('0.50')
            self.no_of_days = 1
        elif self.days is None:
            leave_days = (self.to_date - self.from_date).days + 1
            self.days = Decimal(str(leave_days if leave_days > 0 else 1))
            self.no_of_days = int(self.days)
        else:
            self.no_of_days = int(self.days.quantize(Decimal('1'), rounding='ROUND_UP')) if self.days else 1

        if self.leave_type_ref and not self.leave_type:
            self.leave_type = self.leave_type_ref.name.upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} - {self.leave_type} from {self.from_date} to {self.to_date} ({self.approval_status})"


class LeaveLedger(models.Model):
    ENTRY_TYPE_CHOICES = [
        ('OPENING', 'Opening Balance'),
        ('ACCRUAL', 'Monthly Accrual'),
        ('ENTITLEMENT', 'Annual Entitlement'),
        ('TAKEN', 'Leave Taken'),
        ('REVERSAL', 'Leave Reversal'),
        ('ADJUSTMENT', 'Manual Adjustment'),
        ('CARRY_FORWARD', 'Year-End Carry Forward'),
        ('YEAR_END_CLOSE', 'Year-End Close'),
        ('YEAR_END_EXCESS', 'Year-End Excess'),
        ('EXPIRED', 'Expired Entitlement'),
    ]

    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, related_name='leave_ledger_entries')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name='ledger_entries')
    date = models.DateField()
    entry_type = models.CharField(max_length=30, choices=ENTRY_TYPE_CHOICES)
    days = models.DecimalField(max_digits=6, decimal_places=2)  # Signed: positive credits, negative debits
    leave = models.ForeignKey('Leave', on_delete=models.SET_NULL, null=True, blank=True, related_name='ledger_entries')
    period = models.DateField(null=True, blank=True)  # First day of month for ACCRUAL, or 01/01 of year for ENTITLEMENT
    note = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def clean(self):
        super().clean()
        if self.days is not None:
            remainder = (self.days * Decimal('2')) % Decimal('1')
            if remainder != Decimal('0'):
                raise ValidationError({'days': "Leave days must be in increments of 0.5."})
        if self.entry_type == 'ACCRUAL' and self.period:
            qs = LeaveLedger.objects.filter(
                employee=self.employee,
                leave_type=self.leave_type,
                entry_type='ACCRUAL',
                period=self.period
            )
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({'period': "An accrual entry already exists for this employee, leave type, and period."})
        if self.entry_type == 'ENTITLEMENT' and self.period:
            qs = LeaveLedger.objects.filter(
                employee=self.employee,
                leave_type=self.leave_type,
                entry_type='ENTITLEMENT',
                period=self.period
            )
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({'period': "An entitlement entry already exists for this employee, leave type, and period."})

    def __str__(self):
        emp_name = self.employee.user.username if self.employee and self.employee.user else f"Emp #{self.employee_id}"
        return f"{emp_name} - {self.leave_type.code} - {self.entry_type} ({self.days} days) on {self.date}"


class LeaveYearReview(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('RESOLVED', 'Resolved'),
    ]

    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, related_name='leave_year_reviews')
    year = models.IntegerField()
    unused_days = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'))
    carried_days = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'))
    excess_days = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    resolution_note = models.TextField(blank=True, default='')
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('employee', 'year')
        ordering = ['-year', 'employee__user__first_name']

    def __str__(self):
        return f"Year {self.year} Review for {self.employee}: {self.excess_days} excess days ({self.status})"

class LeaveBalance(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="leave_balance")
    annual_leave = models.IntegerField(default=30)
    sick_leave = models.IntegerField(default=12)

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

    APPROVAL_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]

    team = models.ForeignKey('Team', on_delete=models.CASCADE, related_name='team_members_status')
    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, related_name='project_statuses')
    status = models.CharField(max_length=100, choices=STATUS_CHOICES, default='ASSIGN')
    notes = models.TextField(max_length=2000, null=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True)

    manager_approval_status = models.CharField(max_length=10, choices=APPROVAL_CHOICES, default='PENDING')
    rejection_reason = models.TextField(null=True, blank=True)

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
    changed_by_name = models.CharField(max_length=255, null=True, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        if self.changed_by:
            return f"{self.changed_by.user.first_name} changed to {self.new_status}"
        return f"{self.changed_by_name} changed to {self.new_status}"

class ProjectAttachment(models.Model):
    project = models.ForeignKey(Project, related_name='attachments', on_delete=models.CASCADE)
    file = models.FileField(upload_to='project_attachments/')
    uploaded_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

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
    
class Holiday(models.Model):
    date = models.DateField()
    description = models.CharField(max_length=255)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True, related_name='holidays')
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [('date', 'branch')]
        ordering = ['-date']

    def __str__(self):
        branch_str = f" ({self.branch.name})" if self.branch else " (All branches)"
        return f"{self.date} - {self.description}{branch_str}"



class WorkOrder(models.Model):
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='workorder', null=True, blank=True)
    work_order_number = models.CharField(max_length=100, unique=True, null=True, blank=True)
    vessel = models.CharField(max_length=100, null=True, blank=True)
    client = models.CharField(max_length=100, null=True, blank=True)
    imo_no = models.CharField(max_length=50, null=True, blank=True)
    location = models.CharField(max_length=100, null=True, blank=True)
    assigned_date = models.DateField(null=True, blank=True)
    job_scope = models.TextField(null=True, blank=True)
    job_instructions = models.TextField(null=True, blank=True)
    project_description = models.TextField(null=True, blank=True)
    job_assigned_to = models.ManyToManyField(User, related_name='assigned_work_orders', blank=True)
    all_members = models.ManyToManyField(
        User, related_name='all_assigned_work_orders', blank=True
    ) 
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_work_orders', null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.work_order_number:
            year_suffix = str(datetime.now().year)[-2:]  # e.g. "25"
            prefix = f"AMMS-{year_suffix}"

            # Extract the last 3 digits (sequence number) as integer and find the max
            last_number = (
                WorkOrder.objects
                .filter(work_order_number__startswith=prefix)
                .annotate(seq=Cast(Substr('work_order_number', len(prefix) + 2, 3), IntegerField()))
                .aggregate(max_seq=Max('seq'))['max_seq']
            )

            next_number = (last_number or 0) + 1
            self.work_order_number = f"{prefix}-{next_number:03d}"

        super().save(*args, **kwargs)

class WorkOrderTime(models.Model):
    work_order = models.ForeignKey(WorkOrder, related_name="times", on_delete=models.CASCADE)
    date = models.DateField()
    start_time = models.TimeField()
    finish_time = models.TimeField(null=True, blank=True)
    break_minutes = models.PositiveIntegerField(default=0)
    travel_minutes = models.PositiveIntegerField(default=0)
    description = models.TextField(blank=True, default="")
    attachment = models.FileField(upload_to="timesheets/", null=True, blank=True)
    estimated_hours = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, editable=False
    )
    employee = models.ForeignKey(
        'Employee', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='work_order_times'
    )

    def clean(self):
        super().clean()
        if self.date and self.start_time and self.finish_time:
            start_dt = datetime.combine(self.date, self.start_time)
            finish_dt = datetime.combine(self.date, self.finish_time)
            if finish_dt < start_dt:
                finish_dt += timedelta(days=1)
            duration_minutes = int((finish_dt - start_dt).total_seconds() // 60)
            if (self.break_minutes or 0) >= duration_minutes:
                raise ValidationError("Break duration must be less than working duration.")

        if self.attachment:
            from Admin.timesheet_helpers import validate_timesheet_attachment
            validate_timesheet_attachment(self.attachment)

        if self.employee_id and self.date and self.start_time and self.finish_time:
            from Admin.timesheet_helpers import check_timesheet_overlap
            err = check_timesheet_overlap(
                self.employee_id, self.date, self.start_time, self.finish_time,
                exclude_time_log_id=self.pk
            )
            if err:
                raise ValidationError(err)

    def save(self, *args, **kwargs):
        if self.date and self.start_time and self.finish_time:
            start_dt = datetime.combine(self.date, self.start_time)
            finish_dt = datetime.combine(self.date, self.finish_time)
            if finish_dt < start_dt:  # Handle overnight
                finish_dt += timedelta(days=1)
            duration_minutes = Decimal(str(int((finish_dt - start_dt).total_seconds() // 60)))
            brk = Decimal(str(self.break_minutes or 0))
            trv = Decimal(str(self.travel_minutes or 0))
            from django.conf import settings
            travel_counts = getattr(settings, 'HR_TRAVEL_COUNTS_AS_WORK', True)
            worked_minutes = duration_minutes - brk + (trv if travel_counts else Decimal('0'))
            hours = max(Decimal('0'), worked_minutes / Decimal('60'))
            self.estimated_hours = round(hours, 2)  # store as Decimal(XX.XX)
        super().save(*args, **kwargs)
    
class WorkOrderDetail(models.Model):
    work_order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name='detail')
    start_date = models.DateField(null=True, blank=True)
    completion_date = models.DateField(null=True, blank=True)
    estimated_hours = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    finish_time = models.TimeField(null=True, blank=True)
    

class Spare(models.Model):
    work_order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name='spares')
    name = models.CharField(max_length=100)
    unit = models.CharField(max_length=50)
    quantity = models.IntegerField()

class SpareConsumed(models.Model):
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name='consumed_spares'
    )
    name = models.CharField(max_length=100)
    unit = models.CharField(max_length=50)
    quantity = models.IntegerField()

    def __str__(self):
        return f"{self.name} ({self.quantity} {self.unit})"

class Tool(models.Model):
    work_order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name='tools')
    name = models.CharField(max_length=100)
    quantity = models.IntegerField()

class Document(models.Model):
    work_order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name='documents')
    name = models.CharField(max_length=100)
    status = models.CharField(max_length=100)

class WorkOrderImage(models.Model):
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name='images'
    )
    name = models.CharField(max_length=100, default='Untitled Image')
    description = models.TextField(null=True, blank=True)
    image = models.ImageField(upload_to='work_order_images/')

    def __str__(self):
        return self.name


class DailyAttendanceStatus(models.Model):
    STATUS_CHOICES = [
        ('OFFICE', 'Office'), ('VESSEL', 'Vessel'), ('OFFICE_VESSEL', 'Office+Vessel'),
        ('ABSENT', 'Absent'), ('ANNUAL_LEAVE', 'Annual Leave'), ('SICK_LEAVE', 'Sick Leave'),
        ('PERSONAL_LEAVE', 'Personal Leave'), ('TRAINING', 'Training'), ('REST', 'REST'),
        ('SUNDAY', 'Sunday'), ('HOLIDAY', 'Holiday'),
        ('REST_DAY', 'Rest Day'),
    ]
    LOCATION_CHOICES = [
        ('OFFICE', 'Office'), ('PORT', 'Port'), ('ANCHORAGE', 'Anchorage'),
        ('SAILING', 'Sailing'), ('TRAVEL', 'Travel'), ('HOLIDAY', 'Holiday'),
    ]
    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, related_name='daily_statuses')
    date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    location = models.CharField(max_length=20, choices=LOCATION_CHOICES, blank=True, null=True)
    note = models.CharField(max_length=255, blank=True, default="")
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'date')
        ordering = ['-date']


class DutySchedule(models.Model):
    name = models.CharField(max_length=150, unique=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    break_minutes = models.PositiveIntegerField(default=0)
    expected_hours = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'name']

    def clean(self):
        super().clean()
        if self.start_time and self.end_time:
            from datetime import datetime, date, timedelta
            start_dt = datetime.combine(date.today(), self.start_time)
            end_dt = datetime.combine(date.today(), self.end_time)
            if end_dt < start_dt:
                end_dt += timedelta(days=1)
            total_sec = max((end_dt - start_dt).total_seconds() - (self.break_minutes * 60), 0)
            self.expected_hours = Decimal(str(round(total_sec / 3600.0, 2)))

    def save(self, *args, **kwargs):
        if self.start_time and self.end_time:
            from datetime import datetime, date, timedelta
            start_dt = datetime.combine(date.today(), self.start_time)
            end_dt = datetime.combine(date.today(), self.end_time)
            if end_dt < start_dt:
                end_dt += timedelta(days=1)
            total_sec = max((end_dt - start_dt).total_seconds() - (self.break_minutes * 60), 0)
            self.expected_hours = Decimal(str(round(total_sec / 3600.0, 2)))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.start_time.strftime('%H:%M')} - {self.end_time.strftime('%H:%M')})"


class EmployeeSchedule(models.Model):
    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, related_name='schedules')
    duty_schedule = models.ForeignKey(DutySchedule, on_delete=models.CASCADE, related_name='employee_schedules')
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_employee_schedules')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-effective_from', '-created_at']

    def clean(self):
        super().clean()
        if self.effective_to and self.effective_from and self.effective_to < self.effective_from:
            raise ValidationError("effective_to cannot be earlier than effective_from.")

    def save(self, *args, **kwargs):
        if self.effective_to and self.effective_from and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot be earlier than effective_from.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee}: {self.duty_schedule.name} ({self.effective_from} - {self.effective_to or 'open'})"


WEEKDAY_CHOICES = [
    (0, 'Monday'),
    (1, 'Tuesday'),
    (2, 'Wednesday'),
    (3, 'Thursday'),
    (4, 'Friday'),
    (5, 'Saturday'),
    (6, 'Sunday'),
]


class RestDayRule(models.Model):
    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, related_name='rest_day_rules')
    rest_weekday = models.IntegerField(null=True, blank=True, choices=WEEKDAY_CHOICES)
    uses_roster = models.BooleanField(default=False)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_rest_day_rules')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-effective_from', '-created_at']

    def clean(self):
        super().clean()
        if self.effective_to and self.effective_from and self.effective_to < self.effective_from:
            raise ValidationError("effective_to cannot be earlier than effective_from.")

    def save(self, *args, **kwargs):
        if self.effective_to and self.effective_from and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot be earlier than effective_from.")
        super().save(*args, **kwargs)

    def __str__(self):
        if self.uses_roster:
            desc = "Roster"
        elif self.rest_weekday is not None:
            desc = dict(WEEKDAY_CHOICES).get(self.rest_weekday, f"Day {self.rest_weekday}")
        else:
            desc = "Sunday"
        return f"{self.employee}: {desc} ({self.effective_from} - {self.effective_to or 'open'})"


class RosterDay(models.Model):
    DAY_TYPE_CHOICES = [
        ('REST', 'REST'),
        ('WORK', 'WORK'),
    ]
    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, related_name='roster_days')
    date = models.DateField()
    day_type = models.CharField(max_length=10, choices=DAY_TYPE_CHOICES)
    note = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        unique_together = [('employee', 'date')]
        ordering = ['-date']

    def __str__(self):
        return f"{self.employee} - {self.date}: {self.day_type}"


def check_payroll_lock_for_employee(employee, target_date, is_open_ended=False):
    """
    Reject any schedule/rest-day/roster change whose date falls in a month where
    that employee has an APPROVED or PAID Payroll row.
    For open-ended rules (EmployeeSchedule, RestDayRule), reject if ANY approved/paid
    payroll exists with month >= first day of effective_from's month.
    Error: 'Payroll for MMM YYYY is already approved; changes must start from dd/mm/YYYY.'
    """
    from finance.models import Payroll
    from datetime import timedelta
    month_first = target_date.replace(day=1)
    if is_open_ended:
        locked_payroll = Payroll.objects.filter(
            employee=employee,
            month__gte=month_first,
            status__in=['APPROVED', 'PAID']
        ).order_by('-month').first()
    else:
        locked_payroll = Payroll.objects.filter(
            employee=employee,
            month=month_first,
            status__in=['APPROVED', 'PAID']
        ).first()

    if locked_payroll:
        locked_month = locked_payroll.month
        next_month_start = (locked_month + timedelta(days=32)).replace(day=1)
        raise ValidationError(
            f"Payroll for {locked_month.strftime('%b %Y')} is already approved; changes must start from {next_month_start.strftime('%d/%m/%Y')}."
        )


def check_payroll_lock_for_holiday(target_date):
    """
    Reject any holiday change whose date falls in a month where ANY employee
    has an APPROVED or PAID Payroll row.
    Error: 'Payroll for MMM YYYY is already approved; changes must start from dd/mm/YYYY.'
    """
    from finance.models import Payroll
    from datetime import timedelta
    month_first = target_date.replace(day=1)
    locked_payroll = Payroll.objects.filter(
        month=month_first,
        status__in=['APPROVED', 'PAID']
    ).first()
    if locked_payroll:
        next_month_start = (month_first + timedelta(days=32)).replace(day=1)
        raise ValidationError(
            f"Payroll for {month_first.strftime('%b %Y')} is already approved; changes must start from {next_month_start.strftime('%d/%m/%Y')}."
        )


def update_employee_schedule(employee, duty_schedule, effective_from=None, user=None, source='Schedule'):
    from datetime import timedelta
    from django.utils import timezone
    if effective_from is None:
        effective_from = timezone.localdate()
    check_payroll_lock_for_employee(employee, effective_from, is_open_ended=True)

    open_sched = employee.schedules.filter(effective_to__isnull=True).order_by('-effective_from').first()
    if open_sched:
        if effective_from < open_sched.effective_from:
            raise ValidationError(
                f"Start date must be on or after {open_sched.effective_from.strftime('%d/%m/%Y')}, the start of the current schedule."
            )
        old_name = open_sched.duty_schedule.name if open_sched.duty_schedule else None
        if effective_from == open_sched.effective_from:
            open_sched.duty_schedule = duty_schedule
            open_sched.created_by = user
            open_sched.save()
            new_sched = open_sched
        else:
            open_sched.effective_to = effective_from - timedelta(days=1)
            open_sched.save()
            new_sched = EmployeeSchedule.objects.create(
                employee=employee,
                duty_schedule=duty_schedule,
                effective_from=effective_from,
                created_by=user
            )
    else:
        old_name = None
        new_sched = EmployeeSchedule.objects.create(
            employee=employee,
            duty_schedule=duty_schedule,
            effective_from=effective_from,
            created_by=user
        )

    new_name = duty_schedule.name if duty_schedule else None
    if old_name != new_name:
        EmployeeChangeLog.objects.create(
            employee=employee,
            field='duty_schedule',
            old_value=str(old_name),
            new_value=str(new_name),
            changed_by=user,
            source=source
        )
    return new_sched


def update_employee_rest_day_rule(employee, rest_weekday=6, uses_roster=False, effective_from=None, user=None, source='Rest day'):
    from datetime import timedelta
    from django.utils import timezone
    if effective_from is None:
        effective_from = timezone.localdate()
    check_payroll_lock_for_employee(employee, effective_from, is_open_ended=True)

    open_rule = employee.rest_day_rules.filter(effective_to__isnull=True).order_by('-effective_from').first()
    if open_rule:
        if effective_from < open_rule.effective_from:
            raise ValidationError(
                f"Start date must be on or after {open_rule.effective_from.strftime('%d/%m/%Y')}, the start of the current rest day rule."
            )
        old_desc = "Roster" if open_rule.uses_roster else dict(WEEKDAY_CHOICES).get(open_rule.rest_weekday, "Sunday")
        if effective_from == open_rule.effective_from:
            open_rule.rest_weekday = rest_weekday
            open_rule.uses_roster = uses_roster
            open_rule.created_by = user
            open_rule.save()
            new_rule = open_rule
        else:
            open_rule.effective_to = effective_from - timedelta(days=1)
            open_rule.save()
            new_rule = RestDayRule.objects.create(
                employee=employee,
                rest_weekday=rest_weekday,
                uses_roster=uses_roster,
                effective_from=effective_from,
                created_by=user
            )
    else:
        old_desc = "Sunday"
        new_rule = RestDayRule.objects.create(
            employee=employee,
            rest_weekday=rest_weekday,
            uses_roster=uses_roster,
            effective_from=effective_from,
            created_by=user
        )

    new_desc = "Roster" if uses_roster else dict(WEEKDAY_CHOICES).get(rest_weekday, "Sunday")
    if old_desc != new_desc:
        EmployeeChangeLog.objects.create(
            employee=employee,
            field='rest_day',
            old_value=old_desc,
            new_value=new_desc,
            changed_by=user,
            source=source
        )
    return new_rule


class AttendanceCorrection(models.Model):
    CORRECTION_TYPE_CHOICES = [
        ('FORGOT_IN', 'Forgot In'),
        ('FORGOT_OUT', 'Forgot Out'),
        ('NO_PUNCH', 'No Punch (Worked but did not punch)'),
        ('WRONG_TIME', 'Wrong Time'),
    ]
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('CANCELLED', 'Cancelled'),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='attendance_corrections')
    date = models.DateField()
    correction_type = models.CharField(max_length=20, choices=CORRECTION_TYPE_CHOICES)
    attendance = models.ForeignKey(Attendance, on_delete=models.SET_NULL, null=True, blank=True, related_name='correction_requests')
    original_login = models.DateTimeField(null=True, blank=True)
    original_logout = models.DateTimeField(null=True, blank=True)
    requested_login = models.DateTimeField()
    requested_logout = models.DateTimeField()
    location = models.CharField(max_length=20, choices=Attendance.LOCATION_CHOICES, default='BDO')
    reason = models.TextField()
    attachment = models.FileField(upload_to='attendance_corrections/%Y/%m/', null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    approver = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_corrections')
    submitted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='submitted_corrections')
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='decided_corrections')
    decided_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def clean(self):
        super().clean()
        from django.utils import timezone
        today = timezone.localdate()
        if self.date and self.date > today:
            raise ValidationError({'date': "Correction date cannot be in the future."})
        if self.employee_id and self.employee.date_of_join and self.date < self.employee.date_of_join:
            raise ValidationError({'date': "Correction date cannot be before employee's joining date."})
        if self.employee_id and self.date:
            check_payroll_lock_for_employee(self.employee, self.date, is_open_ended=False)
        if self.requested_login and self.requested_logout:
            if self.requested_logout <= self.requested_login:
                raise ValidationError({'requested_logout': "Requested logout time must be after requested login time."})
            from Admin.attendance_autoclose import session_cutoff
            cutoff = session_cutoff(self.requested_login)
            if self.requested_logout > cutoff:
                raise ValidationError({'requested_logout': "Requested logout time exceeds session cutoff (midnight of the following day)."})
        if self.employee_id and self.date and self.status == 'PENDING':
            qs = AttendanceCorrection.objects.filter(employee_id=self.employee_id, date=self.date, status='PENDING')
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError("There is already a pending correction request for this employee and date.")
        if self.employee_id and self.date and self.correction_type == 'NO_PUNCH':
            exists = Attendance.objects.filter(
                employee_id=self.employee_id,
                login_time__date=self.date
            ).exclude(status='REJECTED').exists()
            if exists:
                formatted_date = self.date.strftime('%d/%m/%Y')
                raise ValidationError(f"A punch already exists for {formatted_date} — use Forgot punch-out or Wrong time instead.")
        if self.attachment and hasattr(self.attachment, 'size') and self.attachment.size:
            if self.attachment.size > 5 * 1024 * 1024:
                raise ValidationError({'attachment': "Attachment file size cannot exceed 5 MB."})
            ext = self.attachment.name.split('.')[-1].lower() if '.' in self.attachment.name else ''
            if ext not in ['pdf', 'jpg', 'jpeg', 'png']:
                raise ValidationError({'attachment': "Only PDF, JPG, and PNG files are allowed as attachments."})

    def __str__(self):
        emp_name = self.employee.user.username if self.employee and self.employee.user else f"Emp #{self.employee_id}"
        return f"Correction for {emp_name} on {self.date} ({self.correction_type} - {self.status})"


class OTApproval(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]

    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, related_name='ot_approvals')
    date = models.DateField()
    ot_hours = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    ot_cost = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='decided_ot_approvals')
    decided_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(blank=True, default='')

    class Meta:
        unique_together = ('employee', 'date')
        ordering = ['-date', 'employee']

    def __str__(self):
        return f"{self.employee} - {self.date}: {self.ot_hours}h / {self.ot_cost} ({self.status})"


@deconstructible
class PrivateFileSystemStorage(FileSystemStorage):
    """
    Storage backend for confidential documents stored outside MEDIA_ROOT.
    Files stored here are never reachable via /media/ URLs.
    """
    def __init__(self, *args, **kwargs):
        from django.conf import settings
        kwargs.setdefault('location', getattr(settings, 'PRIVATE_MEDIA_ROOT', os.path.join(settings.BASE_DIR, 'private_media')))
        super().__init__(*args, **kwargs)


private_storage = PrivateFileSystemStorage()


def private_employee_document_upload_path(instance, filename):
    import uuid
    ext = os.path.splitext(filename)[1].lower()
    return f"employee_docs/{uuid.uuid4().hex}{ext}"


def private_company_document_upload_path(instance, filename):
    import uuid
    ext = os.path.splitext(filename)[1].lower()
    return f"company_docs/{uuid.uuid4().hex}{ext}"


class DocumentType(models.Model):
    CATEGORY_CHOICES = [
        ('EMPLOYEE', 'Employee'),
        ('COMPANY', 'Company'),
    ]

    name = models.CharField(max_length=150)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='EMPLOYEE')
    has_expiry = models.BooleanField(default=True)
    reminder_days = models.CharField(max_length=50, default="90,60,30")
    is_required = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ['category', 'sort_order', 'name']
        unique_together = ('name', 'category')

    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"

    def get_reminder_days_list(self):
        result = set()
        for d in (self.reminder_days or "").split(','):
            d = d.strip()
            if d.isdigit():
                result.add(int(d))
        result.add(0)
        return sorted(result, reverse=True)


class EmployeeDocument(models.Model):
    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, related_name='documents')
    doc_type = models.ForeignKey(DocumentType, on_delete=models.PROTECT, related_name='employee_documents')
    document_number = models.CharField(max_length=100, blank=True)
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    issuing_authority = models.CharField(max_length=200, blank=True)
    file = models.FileField(upload_to=private_employee_document_upload_path, storage=private_storage, null=True, blank=True)
    notes = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    previous = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='renewals')
    is_current = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_employee_documents')
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='uploaded_employee_documents')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['employee', 'doc_type', '-version']

    def save(self, *args, **kwargs):
        if self.created_by and not self.uploaded_by:
            self.uploaded_by = self.created_by
        if self.uploaded_by and not self.created_by:
            self.created_by = self.uploaded_by
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.doc_type and self.doc_type.has_expiry and not self.expiry_date:
            raise ValidationError({'expiry_date': f"Expiry date is required for {self.doc_type.name}."})
        if self.file:
            from Admin.document_helpers import validate_document_file
            validate_document_file(self.file)

    def __str__(self):
        return f"{self.employee.name} - {self.doc_type.name} v{self.version} ({'Current' if self.is_current else 'Archived'})"

    @property
    def status_info(self):
        from Admin.document_helpers import get_document_status
        return get_document_status(self)


class CompanyDocument(models.Model):
    branch = models.ForeignKey('Branch', on_delete=models.SET_NULL, null=True, blank=True, related_name='company_documents')
    title = models.CharField(max_length=200, blank=True)
    doc_type = models.ForeignKey(DocumentType, on_delete=models.PROTECT, related_name='company_documents')
    document_number = models.CharField(max_length=100, blank=True)
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    issuing_authority = models.CharField(max_length=200, blank=True)
    file = models.FileField(upload_to=private_company_document_upload_path, storage=private_storage, null=True, blank=True)
    notes = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    previous = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='renewals')
    is_current = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_company_documents')
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='uploaded_company_documents')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['doc_type', '-version']

    def save(self, *args, **kwargs):
        if self.created_by and not self.uploaded_by:
            self.uploaded_by = self.created_by
        if self.uploaded_by and not self.created_by:
            self.created_by = self.uploaded_by
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.doc_type and self.doc_type.has_expiry and not self.expiry_date:
            raise ValidationError({'expiry_date': f"Expiry date is required for {self.doc_type.name}."})
        if self.file:
            from Admin.document_helpers import validate_document_file
            validate_document_file(self.file)

    def __str__(self):
        return f"{self.title or self.doc_type.name} v{self.version} ({'Current' if self.is_current else 'Archived'})"

    @property
    def status_info(self):
        from Admin.document_helpers import get_document_status
        return get_document_status(self)


class DocumentReminderLog(models.Model):
    CATEGORY_CHOICES = [
        ('EMPLOYEE', 'Employee'),
        ('COMPANY', 'Company'),
    ]

    document_type_category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='EMPLOYEE')
    employee_document = models.ForeignKey('EmployeeDocument', on_delete=models.CASCADE, null=True, blank=True, related_name='reminder_logs')
    company_document = models.ForeignKey('CompanyDocument', on_delete=models.CASCADE, null=True, blank=True, related_name='reminder_logs')
    threshold_days = models.IntegerField(default=30)
    sent_at = models.DateTimeField(auto_now_add=True)
    recipient_count = models.IntegerField(default=1)

    # Legacy fields
    kind = models.CharField(max_length=20, choices=CATEGORY_CHOICES, null=True, blank=True)
    doc_id = models.PositiveIntegerField(null=True, blank=True)
    threshold = models.IntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['employee_document', 'threshold_days'],
                name='unique_emp_doc_threshold',
                condition=models.Q(employee_document__isnull=False)
            ),
            models.UniqueConstraint(
                fields=['company_document', 'threshold_days'],
                name='unique_comp_doc_threshold',
                condition=models.Q(company_document__isnull=False)
            ),
        ]
        ordering = ['-sent_at']

    def save(self, *args, **kwargs):
        if self.document_type_category:
            self.kind = self.document_type_category
        if self.threshold_days is not None:
            self.threshold = self.threshold_days
        if self.employee_document_id:
            self.doc_id = self.employee_document_id
        elif self.company_document_id:
            self.doc_id = self.company_document_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.document_type_category} Doc - {self.threshold_days}d at {self.sent_at}"


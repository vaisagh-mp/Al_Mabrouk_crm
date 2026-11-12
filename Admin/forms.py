from decimal import Decimal
from django import forms
from django.utils.timezone import now
from django.utils import timezone
from django.db.models import Q
from django.contrib.auth.models import User
from .models import (
    Project, ProjectAssignment, Employee, Leave, WorkOrder, WorkOrderDetail,
    Spare, Tool, Document, Team, Vessel, Department, Branch, SalaryStructure,
    EmployeeChangeLog, EMPLOYMENT_STATUS_CHOICES, WORK_TYPE_CHOICES,
    DutySchedule, EmployeeSchedule, RestDayRule, RosterDay, WEEKDAY_CHOICES,
    update_employee_salary_structure, update_employee_schedule, update_employee_rest_day_rule,
    check_payroll_lock_for_employee, log_employee_changes
)
from finance.currency import CURRENCY_CHOICES

class EmployeeCreationForm(forms.ModelForm):
    ROLE_CHOICES = [
        ('employee', 'Employee'),
        ('manager', 'Manager'),
        ('administration', 'Administration'),
        ('hr', 'HR'),
        ('accountant', 'Accountant'),
    ]

    # Section 1 Personal
    first_name = forms.CharField(max_length=150, required=True, label="First Name")
    last_name = forms.CharField(max_length=150, required=False, label="Last Name")
    phone_number = forms.CharField(max_length=15, required=False, label="Mobile Number")
    personal_email = forms.EmailField(required=True, label="Personal Email")
    company_email = forms.EmailField(required=False, label="Company Email")
    date_of_birth = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label="Date of Birth")
    nationality = forms.CharField(max_length=100, required=False, label="Nationality")
    emergency_contact_number = forms.CharField(max_length=20, required=False, label="Emergency Contact Number")
    address = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), required=False, label="Address")
    profile_picture = forms.ImageField(required=False, label="Profile Picture")

    # Section 2 Employment
    department = forms.ModelChoiceField(
        queryset=Department.objects.filter(is_active=True),
        required=False,
        label="Department"
    )
    branch = forms.ModelChoiceField(
        queryset=Branch.objects.filter(is_active=True),
        required=False,
        label="Branch"
    )
    rank = forms.CharField(max_length=255, required=False, label="Designation / Rank")
    reporting_manager = forms.ModelChoiceField(
        queryset=Employee.objects.filter(user__is_active=True).select_related('user'),
        required=False,
        label="Reporting Manager"
    )
    date_of_join = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label="Date of Joining")
    employment_status = forms.ChoiceField(choices=EMPLOYMENT_STATUS_CHOICES, initial='ACTIVE', required=False, label="Employment Status")
    work_type = forms.ChoiceField(choices=WORK_TYPE_CHOICES, initial='OFFICE', required=False, label="Work Type")
    contract_start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label="Contract Start Date")
    contract_end_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label="Contract End Date")
    notice_start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label="Notice Start Date")
    last_working_day = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label="Last Working Day")
    duty_schedule = forms.ModelChoiceField(
        queryset=DutySchedule.objects.filter(is_active=True),
        required=False,
        label="Duty Schedule"
    )
    weekly_rest_day = forms.TypedChoiceField(
        choices=WEEKDAY_CHOICES,
        coerce=int,
        initial=6,
        required=False,
        label="Weekly Rest Day"
    )
    rest_day_effective_from = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        required=False,
        label="Rest Day Effective From"
    )

    # Section 3 Salary (HR / Accountant / Superuser only)
    basic = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2, required=False, initial=Decimal('0.00'), label="Basic")
    housing_allowance = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2, required=False, initial=Decimal('0.00'), label="Housing")
    transport_allowance = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2, required=False, initial=Decimal('0.00'), label="Transport")
    other_allowances = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2, required=False, initial=Decimal('0.00'), label="Other")
    total = forms.DecimalField(max_digits=12, decimal_places=2, required=False, widget=forms.TextInput(attrs={'readonly': 'readonly'}), label="Total")

    # Legacy fields
    salary = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2, required=False)
    email = forms.EmailField(required=False)

    # Section 4 CRM Login
    username = forms.CharField(max_length=150, required=True, label="Username")
    role = forms.ChoiceField(
        choices=ROLE_CHOICES,
        widget=forms.RadioSelect,
        required=True,
        label="Select Role"
    )
    password = forms.CharField(widget=forms.PasswordInput, required=True, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, required=True, label="Confirm Password")
    is_active = forms.BooleanField(initial=True, required=False, label="Account Active")

    class Meta:
        model = User
        fields = [
            'username', 'first_name', 'last_name', 'email', 'password', 'password2',
            'role', 'rank', 'salary', 'date_of_birth', 'phone_number', 'date_of_join', 'address', 'profile_picture'
        ]

    def __init__(self, *args, user=None, **kwargs):
        self.request_user = user
        super().__init__(*args, **kwargs)
        from Admin.permissions import can_view_salary
        self.can_edit_salary = can_view_salary(user) if user is not None else True
        if not self.can_edit_salary:
            for f in ['basic', 'housing_allowance', 'transport_allowance', 'other_allowances', 'total', 'salary']:
                if f in self.fields:
                    del self.fields[f]

        if 'department' in self.fields:
            self.fields['department'].queryset = Department.objects.filter(is_active=True)
        if 'branch' in self.fields:
            self.fields['branch'].queryset = Branch.objects.filter(is_active=True)
        if 'reporting_manager' in self.fields:
            self.fields['reporting_manager'].queryset = Employee.objects.filter(user__is_active=True).select_related('user')

    def clean(self):
        cleaned_data = super().clean()
        username = cleaned_data.get("username")
        password = cleaned_data.get("password")
        password2 = cleaned_data.get("password2")
        role = cleaned_data.get("role")
        start = cleaned_data.get("contract_start_date")
        end = cleaned_data.get("contract_end_date")

        if not cleaned_data.get('personal_email') and cleaned_data.get('email'):
            cleaned_data['personal_email'] = cleaned_data.get('email')
        elif cleaned_data.get('personal_email') and not cleaned_data.get('email'):
            cleaned_data['email'] = cleaned_data.get('personal_email')

        if not cleaned_data.get('employment_status'):
            cleaned_data['employment_status'] = 'ACTIVE'
        if not cleaned_data.get('work_type'):
            cleaned_data['work_type'] = 'OFFICE'

        if username and User.objects.filter(username=username).exists():
            self.add_error('username', "A user with that username already exists.")

        if password and password2 and password != password2:
            self.add_error('password2', "Passwords do not match.")

        if start and end and end < start:
            self.add_error('contract_end_date', "Contract end date must be on or after contract start date.")

        notice_start = cleaned_data.get("notice_start_date")
        last_working = cleaned_data.get("last_working_day")
        status = cleaned_data.get("employment_status")
        if notice_start and last_working and last_working < notice_start:
            self.add_error("last_working_day", "Last working day cannot be earlier than notice start date.")
        if status == 'NOTICE_PERIOD' and not notice_start:
            self.add_error("notice_start_date", "Notice start date is required when status is Notice Period.")
        if status == 'RESIGNED' and not last_working:
            self.add_error("last_working_day", "Last working day is required when status is Resigned.")

        if self.can_edit_salary:
            if cleaned_data.get('salary') is not None and cleaned_data.get('basic') is None:
                cleaned_data['basic'] = cleaned_data.get('salary')

            basic = cleaned_data.get("basic") or Decimal('0.00')
            housing = cleaned_data.get("housing_allowance") or Decimal('0.00')
            transport = cleaned_data.get("transport_allowance") or Decimal('0.00')
            other = cleaned_data.get("other_allowances") or Decimal('0.00')
            total = basic + housing + transport + other
            cleaned_data['total'] = total

            if role and (cleaned_data.get("basic") is None or cleaned_data.get("basic") < 0):
                self.add_error('basic', "Basic salary is required for employees.")
        else:
            cleaned_data['total'] = Decimal('0.00')

        if role and not cleaned_data.get("rank"):
            self.add_error('rank', "Designation / Rank is required for all roles.")

        return cleaned_data

    def save(self, commit=True):
        username = self.cleaned_data["username"]
        email = self.cleaned_data.get("personal_email") or self.cleaned_data.get("email")
        first_name = self.cleaned_data.get("first_name", "")
        last_name = self.cleaned_data.get("last_name", "")
        password = self.cleaned_data.get("password")
        role = self.cleaned_data.get("role")
        is_active = self.cleaned_data.get("is_active", True)

        user = User(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            is_active=is_active,
        )
        if password:
            user.set_password(password)
        if role in ["manager", "hr", "accountant", "administration"]:
            user.is_staff = True

        if commit:
            user.save()

            total_salary = self.cleaned_data.get("total") if self.can_edit_salary else Decimal('0.00')

            employee = Employee.objects.create(
                user=user,
                is_employee=(role == "employee"),
                is_manager=(role == "manager"),
                is_administration=(role == "administration"),
                is_hr=(role == "hr"),
                is_accountant=(role == "accountant"),
                personal_email=email,
                company_email=self.cleaned_data.get("company_email"),
                phone_number=self.cleaned_data.get("phone_number"),
                date_of_birth=self.cleaned_data.get("date_of_birth"),
                nationality=self.cleaned_data.get("nationality"),
                emergency_contact_number=self.cleaned_data.get("emergency_contact_number"),
                department=self.cleaned_data.get("department"),
                branch=self.cleaned_data.get("branch"),
                reporting_manager=self.cleaned_data.get("reporting_manager"),
                date_of_join=self.cleaned_data.get("date_of_join"),
                employment_status=self.cleaned_data.get("employment_status") or 'ACTIVE',
                work_type=self.cleaned_data.get("work_type") or 'OFFICE',
                contract_start_date=self.cleaned_data.get("contract_start_date"),
                contract_end_date=self.cleaned_data.get("contract_end_date"),
                notice_start_date=self.cleaned_data.get("notice_start_date"),
                last_working_day=self.cleaned_data.get("last_working_day"),
                rank=self.cleaned_data.get("rank"),
                salary=total_salary,
                address=self.cleaned_data.get("address"),
                profile_picture=self.cleaned_data.get("profile_picture"),
            )

            if self.can_edit_salary and total_salary > 0:
                eff_from = employee.date_of_join or timezone.localdate()
                SalaryStructure.objects.create(
                    employee=employee,
                    basic=self.cleaned_data.get("basic") or Decimal('0.00'),
                    housing_allowance=self.cleaned_data.get("housing_allowance") or Decimal('0.00'),
                    transport_allowance=self.cleaned_data.get("transport_allowance") or Decimal('0.00'),
                    other_allowances=self.cleaned_data.get("other_allowances") or Decimal('0.00'),
                    total=total_salary,
                    effective_from=eff_from,
                    created_by=self.request_user,
                    notes="Initial salary structure on employee creation"
                )

            duty_sched = self.cleaned_data.get('duty_schedule')
            if duty_sched:
                update_employee_schedule(
                    employee=employee,
                    duty_schedule=duty_sched,
                    effective_from=employee.date_of_join or timezone.localdate(),
                    user=self.request_user,
                    source='Create'
                )

            rest_day = self.cleaned_data.get('weekly_rest_day')
            if rest_day in (None, ''):
                rest_day = 6
            eff_from = self.cleaned_data.get('rest_day_effective_from') or employee.date_of_join or timezone.localdate()
            try:
                rest_day_int = int(rest_day)
            except (ValueError, TypeError):
                rest_day_int = 6
            update_employee_rest_day_rule(
                employee=employee,
                rest_weekday=rest_day_int,
                uses_roster=False,
                effective_from=eff_from,
                user=self.request_user,
                source='Create'
            )

            EmployeeChangeLog.objects.create(
                employee=employee,
                field='account',
                old_value=None,
                new_value=f"Employee created with role {role}",
                changed_by=self.request_user,
                source='Create'
            )

            user.employee_profile = employee
            return user
        return user


# Define a form for the Project model
class ProjectForm(forms.ModelForm):

    # Extra Team fields (optional)
    team_name = forms.CharField(label="Team Name", required=False)
    employees = forms.ModelMultipleChoiceField(
        queryset=Employee.objects.filter(is_employee=True, user__is_active=True),
        widget=forms.CheckboxSelectMultiple,
        required=False
    )

    # Extra Work Order fields (optional, assigned_date defaults to today)
    imo_no = forms.CharField(
        max_length=50,
        required=False,
        label="IMO No",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter IMO number'})
    )
    location = forms.CharField(
        max_length=100,
        required=False,
        label="Location",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter location'})
    )
    assigned_date = forms.DateField(
        required=False,
        label="Assigned Date",
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        input_formats=['%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y'],
    )
    job_scope = forms.CharField(
        required=False,
        label="Job Scope",
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Enter job scope'})
    )
    job_instructions = forms.CharField(
        required=False,
        label="Job Instructions",
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Enter instructions'})
    )

    currency_code = forms.ChoiceField(
        choices=CURRENCY_CHOICES,
        initial='AED',
        required=True,
        label="Currency (for invoices)",
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    
    class Meta:
        model = Project
        fields = [
            'name', 'code', 'service_reference', 'client_name', 'vessel_name',
            'purchase_and_expenses', 'invoice_amount', 'currency_code', 'status',
            'category', 'manager', 'deadline_date', 'priority', 'job_description', 'job_card',
        ]
        widgets = {
            'priority': forms.RadioSelect(choices=Project.PRIORITY_CHOICES),
            'job_description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
        }


    def clean_code(self):
        code = self.cleaned_data.get("code")

        if code is None:
            return None

        # Normalize input
        normalized = code.strip().lower()

        if normalized in ("none", "null", "nil", "nill", ""):
            return None

        return code.strip()

    def clean_priority(self):
        priority = self.cleaned_data.get("priority")

        if priority in [None, "", "none", "null"]:
            return "MEDIUM"  # or None if you want

        return priority

    def clean_assigned_date(self):
        d = self.cleaned_data.get("assigned_date")
        if not d:
            return now().date()
        return d
    
    def __init__(self, *args, **kwargs):
        # Pop user if passed
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        today = now().date()

        managers_on_leave = Leave.objects.filter(
            leave_type='ANNUAL LEAVE',
            approval_status='APPROVED',
            from_date__lte=today,
            to_date__gte=today
        ).values_list('user_id', flat=True)

        self.fields['manager'].queryset = Employee.objects.filter(
            is_manager=True,
            user__is_active=True,
        ).exclude(user__id__in=managers_on_leave)

        # If current user is a manager, hide the field and auto-fill
        if self.user and hasattr(self.user, 'employee_profile') and self.user.employee_profile.is_manager:
            self.fields['manager'].widget = forms.HiddenInput()
            self.fields['manager'].required = False

        # Work Order initial values
        wo = None
        if self.instance and self.instance.pk:
            try:
                wo = self.instance.workorder
            except (WorkOrder.DoesNotExist, AttributeError):
                wo = None

        if wo:
            self.fields['imo_no'].initial = wo.imo_no
            self.fields['location'].initial = wo.location
            self.fields['assigned_date'].initial = wo.assigned_date or today
            self.fields['job_scope'].initial = wo.job_scope
            self.fields['job_instructions'].initial = wo.job_instructions
        else:
            self.fields['assigned_date'].initial = today




    
class ProjectAssignmentForm(forms.ModelForm):
    class Meta:
        model = ProjectAssignment
        fields = ['project', 'employee', 'time_start', 'time_stop']
        widgets = {
            'time_start': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'time_stop': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }


class AdminTeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ['name', 'manager', 'project', 'employees']
        widgets = {
            'employees': forms.SelectMultiple(attrs={'class': 'form-control', 'size': 8}),
            'project': forms.Select(attrs={'class': 'form-control'}),
            'manager': forms.Select(attrs={'class': 'form-control'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter team name'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Only Managers in the manager field
        self.fields['manager'].queryset = Employee.objects.filter(is_manager=True, user__is_active=True).select_related("user")

        # Only Employees in employees field
        self.fields['employees'].queryset = Employee.objects.filter(is_employee=True, user__is_active=True).select_related("user")

        # Show all projects (or you can filter active ones if needed)
        self.fields['project'].queryset = Project.objects.all()

class ManagerEmployeeUpdateForm(forms.ModelForm):
    ROLE_CHOICES = [
        ('employee', 'Employee'),
        ('manager', 'Manager'),
        ('administration', 'Administration'),
        ('hr', 'HR'),
        ('accountant', 'Accountant'),
    ]

    # Section 1 Personal
    first_name = forms.CharField(max_length=150, required=True, label="First Name")
    last_name = forms.CharField(max_length=150, required=False, label="Last Name")
    phone_number = forms.CharField(max_length=15, required=False, label="Mobile Number")
    personal_email = forms.EmailField(required=False, label="Personal Email")
    company_email = forms.EmailField(required=False, label="Company Email")
    date_of_birth = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label="Date of Birth")
    nationality = forms.CharField(max_length=100, required=False, label="Nationality")
    emergency_contact_number = forms.CharField(max_length=20, required=False, label="Emergency Contact Number")
    address = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), required=False, label="Address")
    profile_picture = forms.ImageField(required=False, label="Profile Picture")

    # Section 2 Employment
    department = forms.ModelChoiceField(queryset=Department.objects.none(), required=False, label="Department")
    branch = forms.ModelChoiceField(queryset=Branch.objects.none(), required=False, label="Branch")
    rank = forms.CharField(max_length=255, required=False, label="Designation / Rank")
    reporting_manager = forms.ModelChoiceField(queryset=Employee.objects.none(), required=False, label="Reporting Manager")
    date_of_join = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label="Date of Joining")
    employment_status = forms.ChoiceField(choices=EMPLOYMENT_STATUS_CHOICES, initial='ACTIVE', required=False, label="Employment Status")
    work_type = forms.ChoiceField(choices=WORK_TYPE_CHOICES, initial='OFFICE', required=False, label="Work Type")
    contract_start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label="Contract Start Date")
    contract_end_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label="Contract End Date")
    notice_start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label="Notice Start Date")
    last_working_day = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label="Last Working Day")
    duty_schedule = forms.ModelChoiceField(queryset=DutySchedule.objects.none(), required=False, label="Duty Schedule")
    weekly_rest_day = forms.TypedChoiceField(choices=WEEKDAY_CHOICES, coerce=int, required=False, label="Weekly Rest Day")
    rest_day_effective_from = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label="Rest Day Effective From")

    # Section 3 Salary (HR / Accountant / Superuser only)
    basic = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2, required=False, label="Basic")
    housing_allowance = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2, required=False, initial=Decimal('0.00'), label="Housing")
    transport_allowance = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2, required=False, initial=Decimal('0.00'), label="Transport")
    other_allowances = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2, required=False, initial=Decimal('0.00'), label="Other")
    total = forms.DecimalField(max_digits=12, decimal_places=2, required=False, widget=forms.TextInput(attrs={'readonly': 'readonly'}), label="Total")
    effective_from = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label="Effective From")

    # Legacy fields
    salary = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2, required=False)
    email = forms.EmailField(required=False)
    work_days = forms.DecimalField(max_digits=10, decimal_places=2, required=False)
    holidays = forms.IntegerField(required=False)
    overseas_days = forms.IntegerField(required=False)

    # Section 4 CRM Login
    username = forms.CharField(max_length=150, required=False, label="Username")
    role = forms.ChoiceField(choices=ROLE_CHOICES, widget=forms.RadioSelect, required=False, label="Role")
    password = forms.CharField(widget=forms.PasswordInput, required=False, label="Password")
    confirm_password = forms.CharField(widget=forms.PasswordInput, required=False, label="Confirm Password")
    is_active = forms.BooleanField(initial=True, required=False, label="Account Active")

    class Meta:
        model = Employee
        fields = [
            "username", "first_name", "last_name", "email", "personal_email", "company_email",
            "phone_number", "nationality", "emergency_contact_number",
            "department", "branch", "reporting_manager", "employment_status", "work_type",
            "contract_start_date", "contract_end_date", "notice_start_date", "last_working_day",
            "rank", "salary", "basic", "housing_allowance", "transport_allowance", "other_allowances",
            "date_of_birth", "date_of_join", "work_days", "holidays", "overseas_days",
            "address", "profile_picture", "role", "is_active"
        ]

    def __init__(self, *args, user=None, **kwargs):
        self.request_user = user
        super().__init__(*args, **kwargs)
        from Admin.permissions import can_view_salary
        self.can_edit_salary = can_view_salary(user) if user is not None else True

        if self.instance and self.instance.pk:
            self._original_salary = self.instance.salary or Decimal('0.00')
            self._original_emp_data = {
                'phone_number': self.instance.phone_number,
                'personal_email': self.instance.personal_email,
                'company_email': self.instance.company_email,
                'date_of_birth': self.instance.date_of_birth,
                'nationality': self.instance.nationality,
                'emergency_contact_number': self.instance.emergency_contact_number,
                'department': self.instance.department,
                'branch': self.instance.branch,
                'reporting_manager': self.instance.reporting_manager,
                'date_of_join': self.instance.date_of_join,
                'employment_status': self.instance.employment_status,
                'work_type': self.instance.work_type,
                'contract_start_date': self.instance.contract_start_date,
                'contract_end_date': self.instance.contract_end_date,
                'notice_start_date': self.instance.notice_start_date,
                'last_working_day': self.instance.last_working_day,
                'rank': self.instance.rank,
                'address': self.instance.address,
                'role': self.instance.get_role(),
            }
            u = getattr(self.instance, 'user', None)
            if u:
                if 'username' in self.fields and not self.initial.get('username'):
                    self.initial['username'] = u.username
                if 'first_name' in self.fields and not self.initial.get('first_name'):
                    self.initial['first_name'] = u.first_name
                if 'last_name' in self.fields and not self.initial.get('last_name'):
                    self.initial['last_name'] = u.last_name
                if 'personal_email' in self.fields and not self.initial.get('personal_email'):
                    self.initial['personal_email'] = self.instance.personal_email or u.email
                if 'email' in self.fields and not self.initial.get('email'):
                    self.initial['email'] = u.email
                if 'is_active' in self.fields and 'is_active' not in self.initial:
                    self.initial['is_active'] = u.is_active

            if 'role' in self.fields and not self.initial.get('role'):
                role_val = 'employee'
                if self.instance.is_manager:
                    role_val = 'manager'
                elif self.instance.is_administration:
                    role_val = 'administration'
                elif self.instance.is_hr:
                    role_val = 'hr'
                elif self.instance.is_accountant:
                    role_val = 'accountant'
                self.initial['role'] = role_val

            struct = self.instance.current_salary_structure
            if struct:
                if 'basic' in self.fields and not self.initial.get('basic'):
                    self.initial['basic'] = struct.basic
                if 'housing_allowance' in self.fields and not self.initial.get('housing_allowance'):
                    self.initial['housing_allowance'] = struct.housing_allowance
                if 'transport_allowance' in self.fields and not self.initial.get('transport_allowance'):
                    self.initial['transport_allowance'] = struct.transport_allowance
                if 'other_allowances' in self.fields and not self.initial.get('other_allowances'):
                    self.initial['other_allowances'] = struct.other_allowances
                if 'total' in self.fields and not self.initial.get('total'):
                    self.initial['total'] = struct.total
            else:
                if 'basic' in self.fields and not self.initial.get('basic'):
                    self.initial['basic'] = self.instance.salary or Decimal('0.00')
                if 'total' in self.fields and not self.initial.get('total'):
                    self.initial['total'] = self.instance.salary or Decimal('0.00')

            if 'effective_from' in self.fields and not self.initial.get('effective_from'):
                from django.utils import timezone
                self.initial['effective_from'] = timezone.localdate().strftime('%Y-%m-%d')

        if not self.can_edit_salary:
            for f in ['basic', 'housing_allowance', 'transport_allowance', 'other_allowances', 'total', 'salary', 'effective_from']:
                if f in self.fields:
                    del self.fields[f]

        dept_qs = Department.objects.filter(is_active=True)
        branch_qs = Branch.objects.filter(is_active=True)
        if self.instance and self.instance.pk:
            if self.instance.department_id:
                dept_qs = Department.objects.filter(Q(is_active=True) | Q(pk=self.instance.department_id))
            if self.instance.branch_id:
                branch_qs = Branch.objects.filter(Q(is_active=True) | Q(pk=self.instance.branch_id))

        if 'department' in self.fields:
            self.fields['department'].queryset = dept_qs
        if 'branch' in self.fields:
            self.fields['branch'].queryset = branch_qs

        mgr_qs = Employee.objects.filter(user__is_active=True).select_related('user')
        if 'reporting_manager' in self.fields:
            self.fields['reporting_manager'].queryset = mgr_qs

        sched_qs = DutySchedule.objects.filter(is_active=True)
        curr_sched = self.instance.current_duty_schedule if self.instance and self.instance.pk else None
        if curr_sched:
            sched_qs = sched_qs | DutySchedule.objects.filter(pk=curr_sched.pk)
        if 'duty_schedule' in self.fields:
            self.fields['duty_schedule'].queryset = sched_qs.distinct()
            if not self.initial.get('duty_schedule'):
                self.initial['duty_schedule'] = curr_sched

        curr_rule = self.instance.current_rest_day_rule if self.instance and self.instance.pk else None
        if 'weekly_rest_day' in self.fields and 'weekly_rest_day' not in self.initial:
            if curr_rule and curr_rule.rest_weekday is not None:
                self.initial['weekly_rest_day'] = curr_rule.rest_weekday
            else:
                self.initial['weekly_rest_day'] = 6
        if 'rest_day_effective_from' in self.fields and not self.initial.get('rest_day_effective_from'):
            from django.utils import timezone
            self.initial['rest_day_effective_from'] = timezone.localdate().strftime('%Y-%m-%d')

    def clean_reporting_manager(self):
        mgr = self.cleaned_data.get('reporting_manager')
        if mgr and self.instance and self.instance.pk and mgr.pk == self.instance.pk:
            raise forms.ValidationError("An employee cannot be their own reporting manager.")
        return mgr

    def clean(self):
        cleaned_data = super().clean()
        username = cleaned_data.get("username")
        if not username and self.instance and self.instance.user:
            username = self.instance.user.username
            cleaned_data['username'] = username



        if not cleaned_data.get('employment_status'):
            cleaned_data['employment_status'] = self.instance.employment_status if self.instance else 'ACTIVE'
        if not cleaned_data.get('work_type'):
            cleaned_data['work_type'] = self.instance.work_type if self.instance else 'OFFICE'

        password = cleaned_data.get("password")
        confirm_password = cleaned_data.get("confirm_password")
        reporting_manager = cleaned_data.get("reporting_manager")
        start = cleaned_data.get("contract_start_date")
        end = cleaned_data.get("contract_end_date")

        if not cleaned_data.get('personal_email') and cleaned_data.get('email'):
            cleaned_data['personal_email'] = cleaned_data.get('email')
        elif cleaned_data.get('personal_email') and not cleaned_data.get('email'):
            cleaned_data['email'] = cleaned_data.get('personal_email')

        if username and self.instance and self.instance.user:
            if User.objects.filter(username=username).exclude(pk=self.instance.user.pk).exists():
                self.add_error("username", "A user with that username already exists.")

        if reporting_manager and self.instance and reporting_manager.pk == self.instance.pk:
            self.add_error("reporting_manager", "An employee cannot be their own reporting manager.")

        if start and end and end < start:
            self.add_error("contract_end_date", "Contract end date must be on or after contract start date.")

        notice_start = cleaned_data.get("notice_start_date")
        last_working = cleaned_data.get("last_working_day")
        status = cleaned_data.get("employment_status")
        if notice_start and last_working and last_working < notice_start:
            self.add_error("last_working_day", "Last working day cannot be earlier than notice start date.")
        if status == 'NOTICE_PERIOD' and not notice_start:
            self.add_error("notice_start_date", "Notice start date is required when status is Notice Period.")
        if status == 'RESIGNED' and not last_working:
            self.add_error("last_working_day", "Last working day is required when status is Resigned.")

        if self.instance and self.instance.pk:
            from django.utils import timezone
            eff_from = cleaned_data.get('rest_day_effective_from') or timezone.localdate()
            curr_rule = self.instance.current_rest_day_rule
            curr_rest = curr_rule.rest_weekday if curr_rule and not curr_rule.uses_roster else None
            rest_val = cleaned_data.get('weekly_rest_day')
            rest_changed = False
            if rest_val not in (None, ''):
                try:
                    rest_changed = (curr_rest != int(rest_val))
                except (ValueError, TypeError):
                    rest_changed = False
            sched_changed = ('duty_schedule' in cleaned_data and cleaned_data.get('duty_schedule') != self.instance.current_duty_schedule)

            if rest_changed or sched_changed:
                try:
                    check_payroll_lock_for_employee(self.instance, eff_from, is_open_ended=True)
                except forms.ValidationError as e:
                    self.add_error('rest_day_effective_from', e)

        if password and password != confirm_password:
            self.add_error("confirm_password", "Passwords do not match.")

        if self.can_edit_salary:
            if cleaned_data.get('salary') is not None and cleaned_data.get('basic') is None:
                cleaned_data['basic'] = cleaned_data.get('salary')

            basic = cleaned_data.get("basic") or Decimal('0.00')
            housing = cleaned_data.get("housing_allowance") or Decimal('0.00')
            transport = cleaned_data.get("transport_allowance") or Decimal('0.00')
            other = cleaned_data.get("other_allowances") or Decimal('0.00')
            cleaned_data['total'] = basic + housing + transport + other
            cleaned_data['salary'] = cleaned_data['total']

            eff_from = cleaned_data.get("effective_from")
            if self.instance and self.instance.pk and eff_from:
                curr_open = self.instance.salary_structures.filter(effective_to__isnull=True).order_by('-effective_from').first()
                if not curr_open:
                    curr_open = self.instance.current_salary_structure
                if curr_open and curr_open.effective_from and eff_from < curr_open.effective_from:
                    self.add_error('effective_from', f"Start date must be on or after {curr_open.effective_from.strftime('%d/%m/%Y')}, the start of the current salary.")

        return cleaned_data

    def save(self, commit=True, changed_by=None, source='HR edit'):
        from django.utils import timezone
        from Admin.models import update_employee_salary_structure, log_employee_changes, EmployeeChangeLog
        from Admin.staff_lifecycle import deactivate_employee, reactivate_employee

        if changed_by is None:
            changed_by = self.request_user

        old_salary = getattr(self, '_original_salary', self.instance.salary or Decimal('0.00'))
        old_emp_data = getattr(self, '_original_emp_data', {
            'phone_number': self.instance.phone_number,
            'personal_email': self.instance.personal_email,
            'company_email': self.instance.company_email,
            'date_of_birth': self.instance.date_of_birth,
            'nationality': self.instance.nationality,
            'emergency_contact_number': self.instance.emergency_contact_number,
            'department': self.instance.department,
            'branch': self.instance.branch,
            'reporting_manager': self.instance.reporting_manager,
            'date_of_join': self.instance.date_of_join,
            'employment_status': self.instance.employment_status,
            'work_type': self.instance.work_type,
            'contract_start_date': self.instance.contract_start_date,
            'contract_end_date': self.instance.contract_end_date,
            'notice_start_date': self.instance.notice_start_date,
            'last_working_day': self.instance.last_working_day,
            'rank': self.instance.rank,
            'address': self.instance.address,
            'role': self.instance.get_role(),
        })

        employee = super().save(commit=False)
        user = employee.user

        if user:
            user.username = self.cleaned_data.get("username", user.username)
            user.first_name = self.cleaned_data.get("first_name", user.first_name)
            user.last_name = self.cleaned_data.get("last_name", user.last_name)
            user.email = self.cleaned_data.get("personal_email") or user.email
            if self.cleaned_data.get("password"):
                user.set_password(self.cleaned_data["password"])

        if 'role' in self.data:
            role = self.cleaned_data.get("role")
            if role:
                employee.is_employee = (role == "employee")
                employee.is_manager = (role == "manager")
                employee.is_administration = (role == "administration")
                employee.is_hr = (role == "hr")
                employee.is_accountant = (role == "accountant")
                if user:
                    user.is_staff = (role in ["manager", "hr", "accountant", "administration"])

        if commit:
            if user:
                user.save()

            has_is_active = ('is_active_present' in self.data) or ('is_active' in self.data)
            if has_is_active and user:
                is_active_input = self.cleaned_data.get("is_active")
                if user.is_active and not is_active_input:
                    deactivate_employee(employee, by_user=changed_by)
                    EmployeeChangeLog.objects.create(
                        employee=employee,
                        field='is_active',
                        old_value='True',
                        new_value='False',
                        changed_by=changed_by,
                        source=source
                    )
                elif not user.is_active and is_active_input:
                    reactivate_employee(employee, by_user=changed_by)
                    EmployeeChangeLog.objects.create(
                        employee=employee,
                        field='is_active',
                        old_value='False',
                        new_value='True',
                        changed_by=changed_by,
                        source=source
                    )

            employee.save()

            if self.can_edit_salary and 'basic' in self.cleaned_data:
                basic = self.cleaned_data.get("basic") or Decimal('0.00')
                housing = self.cleaned_data.get("housing_allowance") or Decimal('0.00')
                transport = self.cleaned_data.get("transport_allowance") or Decimal('0.00')
                other = self.cleaned_data.get("other_allowances") or Decimal('0.00')

                eff_from = self.cleaned_data.get("effective_from") or timezone.localdate()

                curr = employee.current_salary_structure
                salary_changed = False
                if not curr:
                    salary_changed = True
                else:
                    if (curr.basic != basic or curr.housing_allowance != housing or
                        curr.transport_allowance != transport or curr.other_allowances != other):
                        salary_changed = True

                if salary_changed:
                    update_employee_salary_structure(
                        employee=employee,
                        basic=basic,
                        housing_allowance=housing,
                        transport_allowance=transport,
                        other_allowances=other,
                        effective_from=eff_from,
                        user=changed_by,
                        notes=f"Updated via {source}",
                        old_salary=old_salary
                    )

            if 'duty_schedule' in self.cleaned_data:
                sched = self.cleaned_data.get('duty_schedule')
                eff_from = self.cleaned_data.get('rest_day_effective_from') or timezone.localdate()
                if sched != employee.current_duty_schedule:
                    update_employee_schedule(employee, sched, effective_from=eff_from, user=changed_by, source=source)

            if 'weekly_rest_day' in self.cleaned_data:
                rest_val = self.cleaned_data.get('weekly_rest_day')
                if rest_val not in (None, ''):
                    try:
                        val_int = int(rest_val)
                        eff_from = self.cleaned_data.get('rest_day_effective_from') or timezone.localdate()
                        curr_rule = employee.current_rest_day_rule
                        curr_rest = curr_rule.rest_weekday if curr_rule and not curr_rule.uses_roster else None
                        if curr_rest != val_int:
                            update_employee_rest_day_rule(employee, rest_weekday=val_int, uses_roster=False, effective_from=eff_from, user=changed_by, source=source)
                    except (ValueError, TypeError):
                        pass

            new_emp_data = {
                'phone_number': employee.phone_number,
                'personal_email': employee.personal_email,
                'company_email': employee.company_email,
                'date_of_birth': employee.date_of_birth,
                'nationality': employee.nationality,
                'emergency_contact_number': employee.emergency_contact_number,
                'department': employee.department,
                'branch': employee.branch,
                'reporting_manager': employee.reporting_manager,
                'date_of_join': employee.date_of_join,
                'employment_status': employee.employment_status,
                'work_type': employee.work_type,
                'contract_start_date': employee.contract_start_date,
                'contract_end_date': employee.contract_end_date,
                'notice_start_date': employee.notice_start_date,
                'last_working_day': employee.last_working_day,
                'rank': employee.rank,
                'address': employee.address,
                'role': employee.get_role(),
            }
            log_employee_changes(employee, old_emp_data, new_emp_data, changed_by=changed_by, source=source)

        return employee



class WorkOrderForm(forms.ModelForm):
    assigned_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}),
        input_formats=['%Y-%m-%d'],
    )

    class Meta:
        model = WorkOrder
        fields = [
            'vessel', 'client', 'imo_no',
            'location', 'assigned_date', 'job_scope', 'job_instructions',
            'project_description',
        ]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        self.project = kwargs.pop('project', None)
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False

class WorkOrderDetailForm(forms.ModelForm):
    class Meta:
        model = WorkOrderDetail
        fields = '__all__'

class EngineerWorkOrderDetailForm(forms.ModelForm):
    class Meta:
        model = WorkOrderDetail
        fields = ['start_time', 'finish_time']

class SpareForm(forms.ModelForm):
    class Meta:
        model = Spare
        fields = ['name', 'unit', 'quantity']

class ToolForm(forms.ModelForm):
    class Meta:
        model = Tool
        fields = ['name', 'quantity']

class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ['name', 'status']


class VesselForm(forms.ModelForm):
    class Meta:
        model = Vessel
        fields = ['name']
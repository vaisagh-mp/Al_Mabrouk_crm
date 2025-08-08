from django import forms
from django.utils.timezone import now
from django.contrib.auth.models import User
from .models import Project, ProjectAssignment, Employee, Leave, WorkOrder, WorkOrderDetail, Spare, Tool, Document, Team, Vessel

class EmployeeCreationForm(forms.ModelForm):
    ROLE_CHOICES = [
        ('employee', 'Employee'),
        ('manager', 'Manager'),
        ('administration', 'Administration'),
        ('hr', 'HR'),
    ]

    role = forms.ChoiceField(
        choices=ROLE_CHOICES,
        widget=forms.RadioSelect,
        required=True,
        label="Select Role"
    )

    email = forms.EmailField(required=True)
    password = forms.CharField(widget=forms.PasswordInput, required=True)
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm Password", required=True)
    
    salary = forms.DecimalField()
    rank = forms.CharField()
    date_of_birth = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False)
    phone_number = forms.CharField(max_length=15, required=False, label="Phone Number")
    date_of_join = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label="Date of Joining")
    address = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), required=False, label="Address")
    profile_picture = forms.ImageField(required=False, label="Profile Picture")

    class Meta:
        model = User
        fields = [
            'username', 'first_name', 'last_name', 'email', 'password', 'password2',
            'role', 'rank', 'salary', 'date_of_birth', 'phone_number', 'date_of_join', 'address', 'profile_picture'
        ]

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        password2 = cleaned_data.get("password2")
        role = cleaned_data.get("role")

        # Check if passwords match
        if password and password2 and password != password2:
            self.add_error('password2', "Passwords do not match.")

        # Require salary if a role is selected
        if role and not cleaned_data.get("salary"):
            self.add_error('salary', "Salary is required for employees.")

        # Require rank for all roles
        if role and not cleaned_data.get("rank"):
            self.add_error('rank', "Rank is required for all roles.")

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if self.cleaned_data.get("role") == "manager":
            user.is_staff = True  # Mark as staff if manager

        if commit:
            user.save()

            # Create the Employee profile linked to this user
            Employee.objects.create(
                user=user,
                is_employee=(self.cleaned_data.get("role") == "employee"),
                is_manager=(self.cleaned_data.get("role") == "manager"),
                is_administration=(self.cleaned_data.get("role") == "administration"),
                is_hr=(self.cleaned_data.get("role") == "hr"),
                date_of_birth=self.cleaned_data.get("date_of_birth"),
                phone_number=self.cleaned_data.get("phone_number"),
                date_of_join=self.cleaned_data.get("date_of_join"),
                address=self.cleaned_data.get("address"),
                profile_picture=self.cleaned_data.get("profile_picture"),
                rank=self.cleaned_data.get("rank"),
                salary=self.cleaned_data.get("salary"),
            )

        return user


# Define a form for the Project model
class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = [
            'name', 'code', 'client_name', 'vessel_name', 'purchase_and_expenses',
            'invoice_amount', 'currency_code', 'status', 'category',
            'manager', 'deadline_date', 'priority', 'job_description', 'job_card',
        ]
        widgets = {
            'priority': forms.RadioSelect(choices=Project.PRIORITY_CHOICES),
            'job_description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
        }

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
            is_manager=True
        ).exclude(user__id__in=managers_on_leave)

        # If current user is a manager, hide the field and auto-fill
        if self.user and hasattr(self.user, 'employee_profile') and self.user.employee_profile.is_manager:
            self.fields['manager'].widget = forms.HiddenInput()
            self.fields['manager'].required = False
      
    
class ProjectAssignmentForm(forms.ModelForm):
    class Meta:
        model = ProjectAssignment
        fields = ['project', 'employee', 'time_start', 'time_stop']
        widgets = {
            'time_start': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'time_stop': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }


class ManagerEmployeeUpdateForm(forms.ModelForm):
    ROLE_CHOICES = [
        ('employee', 'Employee'),
        ('manager', 'Manager'),
        ('administration', 'Administration'),
        ('hr', 'HR'),
    ]

    role = forms.ChoiceField(
        choices=ROLE_CHOICES,
        widget=forms.RadioSelect,
        required=True
    )

    username = forms.CharField(max_length=150, required=True)
    current_password = forms.CharField(widget=forms.PasswordInput, required=False)
    password = forms.CharField(widget=forms.PasswordInput, required=False)
    confirm_password = forms.CharField(widget=forms.PasswordInput, required=False)

    first_name = forms.CharField(max_length=30, required=True)
    last_name = forms.CharField(max_length=30, required=False)
    email = forms.EmailField(required=True)
    phone_number = forms.CharField(max_length=15, required=True)
    rank = forms.CharField(max_length=100, required=False)
    salary = forms.DecimalField(max_digits=10, decimal_places=2, required=False)
    date_of_birth = forms.DateField(required=False)
    date_of_join = forms.DateField(required=False)
    work_days = forms.DecimalField(max_digits=10, decimal_places=2, required=False)
    holidays = forms.IntegerField(required=False)
    overseas_days = forms.IntegerField(required=False)
    address = forms.CharField(widget=forms.Textarea, required=False)
    profile_picture = forms.ImageField(required=False)


    class Meta:
        model = Employee
        fields = [
            "username", "first_name", "last_name", "email", "phone_number", "rank", 
            "salary", "date_of_birth", "date_of_join", "work_days", 
            "holidays", "overseas_days", "address", "profile_picture", "role"
        ]

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        confirm_password = cleaned_data.get("confirm_password")

        if password and password != confirm_password:
            self.add_error("confirm_password", "Passwords do not match.")

        return cleaned_data
    
    def save(self, commit=True):
        employee = super().save(commit=False)
        role = self.cleaned_data.get("role")

        # Reset all role fields
        employee.is_employee = False
        employee.is_manager = False
        employee.is_administration = False
        employee.is_hr = False

        # Set the selected role
        if role == "employee":
            employee.is_employee = True
        elif role == "manager":
            employee.is_manager = True
        elif role == "administration":
            employee.is_administration = True
        elif role == "hr":
            employee.is_hr = True

        if commit:
            employee.save()
        return employee


class WorkOrderForm(forms.ModelForm):
    assigned_date = forms.DateField(
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
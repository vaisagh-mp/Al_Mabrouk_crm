from django import forms
from django.utils.timezone import now
from django.contrib.auth.models import User
from .models import Project, ProjectAssignment, Employee, Leave

class EmployeeCreationForm(forms.ModelForm):
    # Fields for the User model
    email = forms.EmailField(required=True)
    password = forms.CharField(widget=forms.PasswordInput, required=True)
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm Password", required=True)

    # Extra fields for the Employee profile
    is_employee = forms.BooleanField(required=False)
    is_manager = forms.BooleanField(required=False)
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
            'is_employee', 'is_manager', 'rank', 'salary',
            'date_of_birth', 'phone_number', 'date_of_join', 'address', 'profile_picture'
        ]

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        password2 = cleaned_data.get("password2")
        is_employee = cleaned_data.get("is_employee")
        is_manager = cleaned_data.get("is_manager")

        # Check if passwords match
        if password and password2 and password != password2:
            self.add_error('password2', "Passwords do not match.")

        # Only one of is_employee or is_manager must be selected
        if is_employee and is_manager:
            raise forms.ValidationError("Select either 'Is Employee?' or 'Is Manager?', not both.")
        if not is_employee and not is_manager:
            raise forms.ValidationError("You must select either 'Is Employee?' or 'Is Manager?'.")

        # Require salary if the user is an employee
        if is_employee and not cleaned_data.get("salary"):
            self.add_error('salary', "Salary is required for employees.")

        # Require rank for both employee and manager
        if (is_employee or is_manager) and not cleaned_data.get("rank"):
            self.add_error('rank', "Rank is required for both Employee or Manager.")

        return cleaned_data

    def save(self, commit=True):
        # Save the User instance
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if self.cleaned_data.get("is_manager"):
            user.is_staff = True  # Mark as staff if manager

        if commit:
            user.save()

            # Create the Employee profile linked to this user
            employee = Employee.objects.create(
                user=user,
                date_of_birth=self.cleaned_data.get("date_of_birth"),
                phone_number=self.cleaned_data.get("phone_number"),
                date_of_join=self.cleaned_data.get("date_of_join"),
                address=self.cleaned_data.get("address"),
                profile_picture=self.cleaned_data.get("profile_picture"),
                rank=self.cleaned_data.get("rank"),
                salary=self.cleaned_data.get("salary"),
                is_employee=self.cleaned_data.get("is_employee"),
                is_manager=self.cleaned_data.get("is_manager")
            )
            employee.save()

        return user

# Define a form for the Project model
class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = [
            'name', 'code', 'client_name', 'purchase_and_expenses',
            'invoice_amount', 'currency_code', 'status', 'category',
            'manager', 'deadline_date', 'priority','job_description',
        ]
        widgets = {
            'priority': forms.RadioSelect(choices=Project.PRIORITY_CHOICES),
            'job_description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        today = now().date()  # Get the current date

        # Get managers who are **currently on approved annual leave**
        managers_on_leave = Leave.objects.filter(
            leave_type='ANNUAL LEAVE',
            approval_status='APPROVED',
            from_date__lte=today,  # Leave starts on or before today
            to_date__gte=today      # Leave ends on or after today
        ).values_list('user_id', flat=True)  # Get IDs of users on leave

        # Filter the manager queryset to exclude those on leave
        self.fields['manager'].queryset = Employee.objects.filter(
            is_manager=True
        ).exclude(user__id__in=managers_on_leave)
       
    
class ProjectAssignmentForm(forms.ModelForm):
    class Meta:
        model = ProjectAssignment
        fields = ['project', 'employee', 'time_start', 'time_stop']
        widgets = {
            'time_start': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'time_stop': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }


class ManagerEmployeeUpdateForm(forms.ModelForm):
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
    password = forms.CharField(widget=forms.PasswordInput, required=False)
    confirm_password = forms.CharField(widget=forms.PasswordInput, required=False)
    profile_picture = forms.ImageField(required=False)

    class Meta:
        model = Employee
        fields = [
            "first_name", "last_name", "email", "phone_number", "rank", 
            "salary", "date_of_birth", "date_of_join", "work_days", 
            "holidays", "overseas_days", "address", "profile_picture"
        ]

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        confirm_password = cleaned_data.get("confirm_password")

        if password and password != confirm_password:
            self.add_error("confirm_password", "Passwords do not match.")

        return cleaned_data

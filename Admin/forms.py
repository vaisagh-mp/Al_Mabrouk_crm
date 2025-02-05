from django import forms
from django.contrib.auth.models import User
from .models import Project, ProjectAssignment, Employee

class EmployeeCreationForm(forms.ModelForm):
    email = forms.EmailField(required=True)
    password = forms.CharField(widget=forms.PasswordInput, required=True)
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm Password", required=True)
    is_employee = forms.BooleanField(required=False, label="Is Employee?")
    is_manager = forms.BooleanField(required=False, label="Is Manager?")
    salary = forms.DecimalField(max_digits=10, decimal_places=2, required=False, label="Salary (if Employee)")
    rank = forms.CharField(max_length=255, required=False, label="Rank (for Employee or Manager)")

    # New fields
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

        # Ensure passwords match
        if password and password2 and password != password2:
            self.add_error('password2', "Passwords do not match.")

        # Ensure only one of is_employee or is_manager is selected
        if is_employee and is_manager:
            raise forms.ValidationError("You can select either 'Is Employee?' or 'Is Manager?', but not both.")
        if not is_employee and not is_manager:
            raise forms.ValidationError("You must select either 'Is Employee?' or 'Is Manager?'.")

        # Ensure salary is provided if the user is an employee
        if is_employee and not cleaned_data.get("salary"):
            self.add_error('salary', "Salary is required for employees.")

        # Ensure rank is provided for both employee or manager
        if (is_employee or is_manager) and not cleaned_data.get("rank"):
            self.add_error('rank', "Rank is required for both Employee or Manager.")

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])

        is_manager = self.cleaned_data.get("is_manager")
        rank = self.cleaned_data.get("rank")

        if is_manager:
            user.is_staff = True  # Set user as staff if they are a manager

        if commit:
            user.save()

            # Create the Employee profile
            employee = Employee.objects.create(
                user=user,
                date_of_birth=self.cleaned_data.get("date_of_birth"),
                phone_number=self.cleaned_data.get("phone_number"),
                date_of_join=self.cleaned_data.get("date_of_join"),
                address=self.cleaned_data.get("address"),
                profile_picture=self.cleaned_data.get("profile_picture")
            )

            # Assign rank if provided
            employee.rank = rank

            if self.cleaned_data.get("is_employee"):
                salary = self.cleaned_data.get("salary")
                employee.salary = salary
                print(f"Employee created with rank: {rank} and salary: {salary}")
            else:
                print(f"Manager created with rank: {rank}")

            employee.save()

        return user
    

# Define a form for the Project model
class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['name', 'code', 'purchase_and_expenses', 'invoice_amount', 'currency_code', 'status', 'category']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'code': forms.TextInput(attrs={'class': 'form-control'}),
            'purchase_and_expenses': forms.TextInput(attrs={'class': 'form-control'}),
            'invoice_amount': forms.TextInput(attrs={'class': 'form-control'}),
            'currency_code': forms.TextInput(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
        }
    

class ProjectAssignmentForm(forms.ModelForm):
    class Meta:
        model = ProjectAssignment
        fields = ['project', 'employee', 'time_start', 'time_stop']
        widgets = {
            'time_start': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'time_stop': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }
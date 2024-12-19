from django import forms
from django.contrib.auth.models import User
from .models import Project, ProjectAssignment

class EmployeeCreationForm(forms.ModelForm):
    email = forms.EmailField(required=True)
    password = forms.CharField(widget=forms.PasswordInput, required=True)
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm Password", required=True)

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'password', 'password2']

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        password2 = cleaned_data.get("password2")

        if password and password2 and password != password2:
            self.add_error('password2', "Passwords do not match.")
        return cleaned_data
    

# Define a form for the Project model
class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = '__all__'

    def clean_status(self):
        status = self.cleaned_data['status']
        valid_choices = [choice[0] for choice in Project.STATUS_CHOICES]
        if status.upper() in valid_choices:
            return status.upper()
        raise forms.ValidationError("Invalid status value")
    

class ProjectAssignmentForm(forms.ModelForm):
    class Meta:
        model = ProjectAssignment
        fields = ['project', 'employee', 'time_start', 'time_stop']
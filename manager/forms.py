from django import forms
from Admin.models import Team, Employee, Project

class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ['name', 'manager', 'employees', 'project']

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)  # Get the logged-in user
        super().__init__(*args, **kwargs)

        # Filter only managers from Employee model
        self.fields['manager'].queryset = Employee.objects.filter(is_manager=True).select_related('user')

        # Filter only employees from Employee model
        self.fields['employees'].queryset = Employee.objects.filter(is_employee=True).select_related('user')

        # Pre-fill manager field if the user is a manager
        if user and hasattr(user, 'employee_profile') and user.employee_profile.is_manager:
            self.fields['manager'].initial = user.employee_profile
            self.fields['manager'].widget.attrs['readonly'] = True  # Make it non-editable
            self.fields['project'].queryset = Project.objects.filter(manager=user.employee_profile)


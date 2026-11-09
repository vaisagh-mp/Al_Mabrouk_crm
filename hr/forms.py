from django import forms
from Admin.models import Department, Branch, DutySchedule, Holiday, WEEKDAY_CHOICES


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ['name', 'sort_order', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter department name'}),
            'sort_order': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '0'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class BranchForm(forms.ModelForm):
    class Meta:
        model = Branch
        fields = ['name', 'sort_order', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter branch name'}),
            'sort_order': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '0'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class DutyScheduleForm(forms.ModelForm):
    class Meta:
        model = DutySchedule
        fields = ['name', 'start_time', 'end_time', 'break_minutes', 'sort_order', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Standard 8am - 5pm'}),
            'start_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'end_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'break_minutes': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'sort_order': forms.NumberInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class HolidayForm(forms.ModelForm):
    class Meta:
        model = Holiday
        fields = ['date', 'description', 'branch', 'is_active']
        labels = {
            'description': 'Name',
        }
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'description': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Holiday name'}),
            'branch': forms.Select(attrs={'class': 'form-select'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        new_date = cleaned_data.get('date')
        if new_date:
            from Admin.models import check_payroll_lock_for_holiday
            from django.core.exceptions import ValidationError
            if self.instance and self.instance.pk and self.instance.date and self.instance.date != new_date:
                try:
                    check_payroll_lock_for_holiday(self.instance.date)
                except ValidationError as e:
                    self.add_error('date', e.message)
            try:
                check_payroll_lock_for_holiday(new_date)
            except ValidationError as e:
                self.add_error('date', e.message)
        return cleaned_data

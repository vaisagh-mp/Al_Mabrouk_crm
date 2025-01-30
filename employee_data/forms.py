from django import forms
from Admin.models import Leave
from datetime import timedelta

class LeaveForm(forms.ModelForm):
    class Meta:
        model = Leave
        fields = ['leave_type', 'from_date', 'to_date', 'reason']

    def clean(self):
        cleaned_data = super().clean()
        from_date = cleaned_data.get('from_date')
        to_date = cleaned_data.get('to_date')

        # Ensure to_date is not before from_date
        if from_date and to_date and to_date < from_date:
            raise forms.ValidationError("To date cannot be earlier than From date.")

        # Calculate the number of days
        no_of_days = (to_date - from_date).days
        cleaned_data['no_of_days'] = no_of_days

        return cleaned_data

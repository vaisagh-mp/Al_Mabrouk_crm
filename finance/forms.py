import re
from django import forms
from decimal import Decimal
from django.core.exceptions import ValidationError

from Admin.models import Project
from .models import Expense, ExpenseCategory, Payroll, Invoice, JobCost, SpareSale



class PayrollEditForm(forms.ModelForm):
    class Meta:
        model = Payroll
        fields = ['allowances', 'deductions', 'absence_waived', 'notes']
        labels = {
            'absence_waived': 'Waive absence deduction',
        }
        widgets = {
            'absence_waived': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'allowances': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'deductions': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def clean_allowances(self):
        val = self.cleaned_data.get('allowances') or Decimal('0.00')
        if val < 0:
            raise ValidationError('Allowances cannot be negative.')
        return val

    def clean_deductions(self):
        val = self.cleaned_data.get('deductions') or Decimal('0.00')
        if val < 0:
            raise ValidationError('Deductions cannot be negative.')
        return val

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('absence_waived') and not (cleaned.get('notes') or '').strip():
            self.add_error('notes', 'Please add a note explaining why the absence deduction is waived.')
        return cleaned


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = [
            'date', 'category', 'amount', 'vat_amount', 'description',
            'vendor', 'reference_no', 'project', 'attachment'
        ]
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'vat_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'vendor': forms.TextInput(attrs={'class': 'form-control'}),
            'reference_no': forms.TextInput(attrs={'class': 'form-control'}),
            'project': forms.Select(attrs={'class': 'form-select'}),
            'attachment': forms.FileInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = ExpenseCategory.objects.filter(is_active=True).order_by('kind', 'sort_order', 'name')
        self.fields['project'].queryset = Project.objects.exclude(status='CANCELLED').order_by('-id')

    def clean(self):
        cleaned_data = super().clean()
        category = cleaned_data.get('category')
        project = cleaned_data.get('project')
        amount = cleaned_data.get('amount')
        vat_amount = cleaned_data.get('vat_amount')

        if amount is not None and amount < 0:
            self.add_error('amount', 'Amount must be greater than or equal to 0.')
        if vat_amount is not None and vat_amount < 0:
            self.add_error('vat_amount', 'VAT amount must be greater than or equal to 0.')

        if category and category.kind == 'PROJECT' and not project:
            self.add_error('project', 'Project is required for project-related expenses.')

        return cleaned_data


class ExpenseCategoryForm(forms.ModelForm):
    class Meta:
        model = ExpenseCategory
        fields = ['name', 'kind', 'sort_order', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'kind': forms.Select(attrs={'class': 'form-select'}),
            'sort_order': forms.NumberInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }




class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = [
            'invoice_number',
            'invoice_date',
            'due_date',
            'project',
            'service_ref',
            'vessel',
            'customer',
            'invoice_type',
            'amount_excl_vat',
            'vat_amount',
            'credits_applied',
            'is_cancelled',
            'notes',
        ]
        widgets = {
            'invoice_number': forms.TextInput(attrs={'class': 'form-control'}),
            'invoice_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'due_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'project': forms.Select(attrs={'class': 'form-select'}),
            'service_ref': forms.TextInput(attrs={'class': 'form-control'}),
            'vessel': forms.TextInput(attrs={'class': 'form-control'}),
            'customer': forms.TextInput(attrs={'class': 'form-control'}),
            'invoice_type': forms.Select(attrs={'class': 'form-select'}),
            'amount_excl_vat': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'vat_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'credits_applied': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'is_cancelled': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['project'].queryset = Project.objects.exclude(status='CANCELLED').order_by('-id')
        self.fields['project'].required = False
        self.fields['due_date'].required = False
        self.fields['vat_amount'].required = False
        self.fields['credits_applied'].required = False
        self.fields['service_ref'].required = False
        self.fields['vessel'].required = False
        self.fields['customer'].required = False
        self.fields['notes'].required = False

    def clean_invoice_number(self):
        val = (self.cleaned_data.get('invoice_number') or '').strip().upper()
        if self.instance and self.instance.pk:
            orig = (self.instance.invoice_number or '').strip().upper()
            if not orig.startswith('LEGACY-') and orig == val:
                return val

        if not re.match(r'^AMMS-INV-\d{2}-\d{4}(-R\d+)?$', val, re.IGNORECASE):
            raise ValidationError("Invoice No must look like AMMS-INV-26-0080 or AMMS-INV-26-0083-R1")
        return val

    def clean(self):
        cleaned_data = super().clean()
        amount = cleaned_data.get('amount_excl_vat') or Decimal('0.00')
        credits = cleaned_data.get('credits_applied') or Decimal('0.00')
        vat = cleaned_data.get('vat_amount') or Decimal('0.00')

        if amount < Decimal('0.00'):
            self.add_error('amount_excl_vat', 'Amount without tax cannot be negative.')
        if credits < Decimal('0.00'):
            self.add_error('credits_applied', 'Credits applied cannot be negative.')
        if vat < Decimal('0.00'):
            self.add_error('vat_amount', 'VAT amount cannot be negative.')
        if credits > amount:
            self.add_error('credits_applied', 'Credits applied cannot exceed invoice amount without tax.')

        project = cleaned_data.get('project')
        if project:
            if not cleaned_data.get('service_ref') and project.service_reference:
                cleaned_data['service_ref'] = project.service_reference
            if not cleaned_data.get('vessel') and project.vessel_name:
                cleaned_data['vessel'] = project.vessel_name
            if not cleaned_data.get('customer') and project.client_name:
                cleaned_data['customer'] = project.client_name

        return cleaned_data


class RecordPaymentForm(forms.Form):
    amount_received = forms.DecimalField(
        decimal_places=2,
        max_digits=14,
        min_value=Decimal('0.01'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )
    payment_received_date = forms.DateField(
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )


class ApplyCreditForm(forms.Form):
    credit_amount = forms.DecimalField(
        decimal_places=2,
        max_digits=14,
        min_value=Decimal('0.01'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2})
    )


class JobCostForm(forms.ModelForm):
    class Meta:
        model = JobCost
        fields = ['cost_type', 'amount', 'travel_port', 'description', 'date']
        widgets = {
            'cost_type': forms.Select(attrs={'class': 'form-select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'travel_port': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.TextInput(attrs={'class': 'form-control'}),
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['travel_port'].required = False
        self.fields['description'].required = False
        self.fields['date'].required = False


    def clean_amount(self):
        val = self.cleaned_data.get('amount') or Decimal('0.00')
        if val < Decimal('0.00'):
            raise ValidationError('Cost amount cannot be negative.')
        return val


class SpareSaleForm(forms.ModelForm):
    class Meta:
        model = SpareSale
        fields = [
            'service_ref',
            'vessel_customer',
            'project',
            'invoice',
            'source',
            'purchase_cost',
            'selling_price',
            'status',
            'notes',
        ]
        widgets = {
            'service_ref': forms.TextInput(attrs={'class': 'form-control'}),
            'vessel_customer': forms.TextInput(attrs={'class': 'form-control'}),
            'project': forms.Select(attrs={'class': 'form-select'}),
            'invoice': forms.Select(attrs={'class': 'form-select'}),
            'source': forms.Select(attrs={'class': 'form-select'}),
            'purchase_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'selling_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['project'].queryset = Project.objects.exclude(status='CANCELLED').order_by('-id')
        self.fields['project'].required = False
        self.fields['invoice'].queryset = Invoice.objects.filter(is_cancelled=False).order_by('-invoice_date')
        self.fields['invoice'].required = False
        self.fields['service_ref'].required = False
        self.fields['vessel_customer'].required = False
        self.fields['notes'].required = False

    def clean_purchase_cost(self):
        val = self.cleaned_data.get('purchase_cost') or Decimal('0.00')
        if val < Decimal('0.00'):
            raise ValidationError('Purchase cost cannot be negative.')
        return val

    def clean_selling_price(self):
        val = self.cleaned_data.get('selling_price') or Decimal('0.00')
        if val < Decimal('0.00'):
            raise ValidationError('Selling price cannot be negative.')
        return val


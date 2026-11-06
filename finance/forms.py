import re
from django import forms
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from Admin.models import Project
from .models import Expense, ExpenseCategory, Payroll, Invoice, JobCost, SpareSale, ExchangeRate
from .currency import CURRENCY_CHOICES, get_rate


def get_form_currency_choices():
    """
    Return available currency choices based on the FINANCE_MULTI_CURRENCY feature switch.
    """
    if getattr(settings, 'FINANCE_MULTI_CURRENCY', False):
        return CURRENCY_CHOICES
    return [('AED', 'AED – UAE Dirham')]


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
        val = self.cleaned_data.get('allowances')
        if val is not None and val < 0:
            raise ValidationError('Allowances cannot be negative.')
        return val or Decimal('0.00')

    def clean_deductions(self):
        val = self.cleaned_data.get('deductions')
        if val is not None and val < 0:
            raise ValidationError('Deductions cannot be negative.')
        return val or Decimal('0.00')

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('absence_waived') and not (cleaned.get('notes') or '').strip():
            self.add_error('notes', 'Please add a note explaining why the absence deduction is waived.')
        return cleaned


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = [
            'date', 'category', 'amount', 'currency', 'exchange_rate', 'vat_amount', 'description',
            'vendor', 'reference_no', 'project', 'attachment'
        ]
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'currency': forms.Select(attrs={'class': 'form-select', 'id': 'id_currency'}),
            'exchange_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001', 'id': 'id_exchange_rate'}),
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
        self.fields['currency'].choices = get_form_currency_choices()
        self.fields['currency'].required = False
        self.fields['exchange_rate'].required = False
        if not self.instance.pk:
            self.fields['currency'].initial = 'AED'
            self.fields['exchange_rate'].initial = Decimal('1.000000')

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

        currency = cleaned_data.get('currency') or 'AED'
        cleaned_data['currency'] = currency
        rate = cleaned_data.get('exchange_rate')
        if currency != 'AED':
            if not rate or rate <= Decimal('0'):
                self.add_error('exchange_rate', 'Enter the exchange rate to AED')
        else:
            cleaned_data['exchange_rate'] = Decimal('1.000000')

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
            'currency',
            'exchange_rate',
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
            'currency': forms.Select(attrs={'class': 'form-select', 'id': 'id_currency'}),
            'exchange_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001', 'id': 'id_exchange_rate'}),
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
        self.fields['currency'].choices = get_form_currency_choices()
        self.fields['currency'].required = False
        self.fields['exchange_rate'].required = False

        if not self.instance.pk:
            self.fields['currency'].initial = 'AED'
            self.fields['exchange_rate'].initial = Decimal('1.000000')
            project_val = self.initial.get('project')
            if project_val:
                proj = project_val if hasattr(project_val, 'currency_code') else Project.objects.filter(pk=project_val).first()
                if proj and proj.currency_code and getattr(settings, 'FINANCE_MULTI_CURRENCY', False):
                    self.fields['currency'].initial = proj.currency_code
                    inv_date = self.initial.get('invoice_date') or timezone.localdate()
                    rate = get_rate(proj.currency_code, inv_date)
                    if rate:
                        self.fields['exchange_rate'].initial = rate

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

        currency = cleaned_data.get('currency') or 'AED'
        cleaned_data['currency'] = currency
        rate = cleaned_data.get('exchange_rate')
        if currency != 'AED':
            if not rate or rate <= Decimal('0'):
                self.add_error('exchange_rate', 'Enter the exchange rate to AED')
        else:
            cleaned_data['exchange_rate'] = Decimal('1.000000')

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

    def __init__(self, *args, invoice=None, **kwargs):
        super().__init__(*args, **kwargs)
        cur = getattr(invoice, 'currency', 'AED') if invoice else 'AED'
        self.fields['amount_received'].label = f"Amount received ({cur})"


class ApplyCreditForm(forms.Form):
    credit_amount = forms.DecimalField(
        decimal_places=2,
        max_digits=14,
        min_value=Decimal('0.01'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Reason for credit note'})
    )

    def __init__(self, *args, invoice=None, **kwargs):
        super().__init__(*args, **kwargs)
        cur = getattr(invoice, 'currency', 'AED') if invoice else 'AED'
        self.fields['credit_amount'].label = f"Credits to apply ({cur})"


class JobCostForm(forms.ModelForm):
    class Meta:
        model = JobCost
        fields = ['cost_type', 'amount', 'currency', 'exchange_rate', 'travel_port', 'description', 'date']
        widgets = {
            'cost_type': forms.Select(attrs={'class': 'form-select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'currency': forms.Select(attrs={'class': 'form-select', 'id': 'id_job_cost_currency'}),
            'exchange_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001', 'id': 'id_job_cost_rate'}),
            'travel_port': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.TextInput(attrs={'class': 'form-control'}),
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['currency'].choices = get_form_currency_choices()
        self.fields['currency'].required = False
        self.fields['exchange_rate'].required = False
        if not self.instance.pk:
            self.fields['currency'].initial = 'AED'
            self.fields['exchange_rate'].initial = Decimal('1.000000')

    def clean(self):
        cleaned_data = super().clean()
        currency = cleaned_data.get('currency') or 'AED'
        cleaned_data['currency'] = currency
        rate = cleaned_data.get('exchange_rate')
        if currency != 'AED':
            if not rate or rate <= Decimal('0'):
                self.add_error('exchange_rate', 'Enter the exchange rate to AED')
        else:
            cleaned_data['exchange_rate'] = Decimal('1.000000')
        return cleaned_data


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
            'purchase_currency',
            'purchase_rate',
            'selling_price',
            'selling_currency',
            'selling_rate',
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
            'purchase_currency': forms.Select(attrs={'class': 'form-select', 'id': 'id_purchase_currency'}),
            'purchase_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001', 'id': 'id_purchase_rate'}),
            'selling_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'selling_currency': forms.Select(attrs={'class': 'form-select', 'id': 'id_selling_currency'}),
            'selling_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001', 'id': 'id_selling_rate'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = get_form_currency_choices()
        self.fields['purchase_currency'].choices = choices
        self.fields['selling_currency'].choices = choices
        self.fields['purchase_currency'].required = False
        self.fields['selling_currency'].required = False
        self.fields['purchase_rate'].required = False
        self.fields['selling_rate'].required = False
        if not self.instance.pk:
            self.fields['purchase_currency'].initial = 'AED'
            self.fields['selling_currency'].initial = 'AED'
            self.fields['purchase_rate'].initial = Decimal('1.000000')
            self.fields['selling_rate'].initial = Decimal('1.000000')
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

    def clean(self):
        cleaned_data = super().clean()
        p_cur = cleaned_data.get('purchase_currency') or 'AED'
        cleaned_data['purchase_currency'] = p_cur
        p_rate = cleaned_data.get('purchase_rate')
        if p_cur != 'AED':
            if not p_rate or p_rate <= Decimal('0'):
                self.add_error('purchase_rate', 'Enter the exchange rate to AED')
        else:
            cleaned_data['purchase_rate'] = Decimal('1.000000')

        s_cur = cleaned_data.get('selling_currency') or 'AED'
        cleaned_data['selling_currency'] = s_cur
        s_rate = cleaned_data.get('selling_rate')
        if s_cur != 'AED':
            if not s_rate or s_rate <= Decimal('0'):
                self.add_error('selling_rate', 'Enter the exchange rate to AED')
        else:
            cleaned_data['selling_rate'] = Decimal('1.000000')

        return cleaned_data


class ExchangeRateForm(forms.ModelForm):
    class Meta:
        model = ExchangeRate
        fields = ['currency', 'rate_to_aed', 'effective_from', 'notes', 'is_active']
        labels = {
            'currency': 'Currency',
            'rate_to_aed': 'Rate to AED',
            'effective_from': 'Effective From',
            'notes': 'Notes',
            'is_active': 'Active',
        }
        widgets = {
            'currency': forms.Select(attrs={'class': 'form-select'}),
            'rate_to_aed': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001', 'min': '0.000001', 'placeholder': 'e.g. 3.672500'}),
            'effective_from': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'notes': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional remarks e.g. Fixed peg'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_rate_to_aed(self):
        rate = self.cleaned_data.get('rate_to_aed')
        if rate is not None and rate <= Decimal('0'):
            raise ValidationError("Rate must be greater than zero.")
        return rate

    def clean(self):
        cleaned_data = super().clean()
        currency = cleaned_data.get('currency')
        effective_from = cleaned_data.get('effective_from')
        if currency and effective_from:
            qs = ExchangeRate.objects.filter(currency=currency, effective_from=effective_from)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError(f"An exchange rate for {currency} effective from {effective_from} already exists.")
        return cleaned_data

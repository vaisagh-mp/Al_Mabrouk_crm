from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from .currency import CURRENCY_CHOICES, NON_BASE_CURRENCY_CHOICES, to_aed


class ExpenseCategory(models.Model):
    KIND_CHOICES = [
        ('OVERHEAD', 'Overhead'),
        ('PROJECT', 'Project'),
    ]
    name = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default='OVERHEAD')
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name_plural = 'Expense Categories'

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"


class Expense(models.Model):
    date = models.DateField(db_index=True)
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name='expenses')
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    vat_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    description = models.TextField(blank=True)
    vendor = models.CharField(max_length=255, blank=True)
    reference_no = models.CharField(max_length=100, blank=True)
    project = models.ForeignKey('Admin.Project', on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses')
    attachment = models.FileField(upload_to='expense_attachments/', blank=True, null=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default='AED')
    exchange_rate = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal('1.000000'))
    amount_aed = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    vat_amount_aed = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return f"{self.date} - {self.category.name} - {self.currency} {self.amount}"

    def clean(self):
        super().clean()
        if self.amount is not None and self.amount < 0:
            raise ValidationError({'amount': 'Amount must be greater than or equal to 0.'})
        if self.vat_amount is not None and self.vat_amount < 0:
            raise ValidationError({'vat_amount': 'VAT amount must be greater than or equal to 0.'})
        if self.category_id:
            if self.category.kind == 'PROJECT' and not self.project:
                raise ValidationError({'project': 'Project is required for project-related expenses.'})
        if self.currency != 'AED':
            if not self.exchange_rate or self.exchange_rate <= Decimal('0'):
                raise ValidationError({'exchange_rate': 'Rate must be greater than zero.'})

    def save(self, *args, **kwargs):
        if self.currency == 'AED':
            self.exchange_rate = Decimal('1.000000')
        self.amount_aed = to_aed(self.amount, self.exchange_rate)
        self.vat_amount_aed = to_aed(self.vat_amount, self.exchange_rate)
        super().save(*args, **kwargs)


class Payroll(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('APPROVED', 'Approved'),
        ('PAID', 'Paid'),
    ]
    employee = models.ForeignKey('Admin.Employee', on_delete=models.CASCADE, related_name='payrolls')
    month = models.DateField()  # Always 1st of month
    basic_salary = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    allowances = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    ot_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    deductions = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    absent_days_unpaid = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    absence_deduction = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    absence_waived = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    paid_on = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='payrolls_created')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'month')
        ordering = ['-month', 'employee__user__first_name', 'employee__user__username']

    def __str__(self):
        emp_name = self.employee.user.get_full_name() or self.employee.user.username
        return f"{emp_name} - {self.month.strftime('%b %Y')} ({self.status})"

    @property
    def gross(self):
        return (self.basic_salary or Decimal('0.00')) + (self.allowances or Decimal('0.00')) + (self.ot_amount or Decimal('0.00'))

    @property
    def effective_absence_deduction(self):
        return Decimal('0.00') if self.absence_waived else (self.absence_deduction or Decimal('0.00'))

    @property
    def net_pay(self):
        return self.gross - (self.deductions or Decimal('0.00')) - self.effective_absence_deduction

    def clean(self):
        super().clean()
        if self.month and self.month.day != 1:
            raise ValidationError({'month': 'Month must be the 1st of the month.'})
        for field in ['basic_salary', 'allowances', 'ot_amount', 'deductions', 'absent_days_unpaid', 'absence_deduction']:
            val = getattr(self, field)
            if val is not None and val < 0:
                raise ValidationError({field: f'{field} must be greater than or equal to 0.'})


class Invoice(models.Model):
    INVOICE_TYPE_CHOICES = [
        ('SERVICE_JOB', 'Service Job'),
        ('SPARES_ONLY', 'Spares Only'),
        ('COMMUNICATION', 'Communication'),
        ('CALIBRATION', 'Calibration'),
        ('OTHER', 'Other'),
    ]

    invoice_number = models.CharField(max_length=100, unique=True, db_index=True)
    invoice_date = models.DateField(db_index=True)
    due_date = models.DateField(db_index=True, null=True, blank=True)
    project = models.ForeignKey('Admin.Project', null=True, blank=True, on_delete=models.SET_NULL, related_name='invoices')
    service_ref = models.CharField(max_length=100, blank=True, default='')
    vessel = models.CharField(max_length=255, blank=True, default='')
    customer = models.CharField(max_length=255, blank=True, default='')
    invoice_type = models.CharField(max_length=30, choices=INVOICE_TYPE_CHOICES, default='SERVICE_JOB')
    amount_excl_vat = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    vat_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    credits_applied = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    amount_received = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    payment_received_date = models.DateField(null=True, blank=True)
    is_cancelled = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_invoices')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default='AED')
    exchange_rate = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal('1.000000'))
    amount_excl_vat_aed = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    vat_amount_aed = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    credits_applied_aed = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    amount_received_aed = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        ordering = ['-invoice_date', '-id']

    def __str__(self):
        return f"{self.invoice_number} ({self.customer or 'No Customer'}) - {self.currency} {self.amount_excl_vat}"

    def clean(self):
        super().clean()
        if self.currency != 'AED':
            if not self.exchange_rate or self.exchange_rate <= Decimal('0'):
                raise ValidationError({'exchange_rate': 'Rate must be greater than zero.'})

    def save(self, *args, **kwargs):
        if self.project:
            if not self.service_ref and getattr(self.project, 'service_reference', None):
                self.service_ref = self.project.service_reference or ''
            if not self.vessel and getattr(self.project, 'vessel_name', None):
                self.vessel = self.project.vessel_name or ''
            if not self.customer and getattr(self.project, 'client_name', None):
                self.customer = self.project.client_name or ''
        if not self.due_date and self.invoice_date:
            import datetime
            self.due_date = self.invoice_date + datetime.timedelta(days=30)

        if self.currency == 'AED':
            self.exchange_rate = Decimal('1.000000')
        self.amount_excl_vat_aed = to_aed(self.amount_excl_vat, self.exchange_rate)
        self.vat_amount_aed = to_aed(self.vat_amount, self.exchange_rate)
        self.credits_applied_aed = to_aed(self.credits_applied, self.exchange_rate)
        self.amount_received_aed = to_aed(self.amount_received, self.exchange_rate)

        super().save(*args, **kwargs)

    @property
    def collection_value_aed(self):
        return (self.amount_excl_vat_aed or Decimal('0.00')) - (self.credits_applied_aed or Decimal('0.00'))

    @property
    def outstanding_aed(self):
        return max(self.collection_value_aed - (self.amount_received_aed or Decimal('0.00')), Decimal('0.00'))

    @property
    def collection_value(self):
        return (self.amount_excl_vat or Decimal('0.00')) - (self.credits_applied or Decimal('0.00'))

    @property
    def outstanding(self):
        return max(self.collection_value - (self.amount_received or Decimal('0.00')), Decimal('0.00'))

    @property
    def status(self):
        from django.utils import timezone
        if self.is_cancelled:
            return 'CANCELLED'
        if self.outstanding <= Decimal('0.00'):
            return 'PAID'
        today = timezone.localdate()
        if self.due_date and today > self.due_date and self.outstanding > Decimal('0.00'):
            return 'OVERDUE'
        if (self.amount_received or Decimal('0.00')) > Decimal('0.00'):
            return 'PARTIAL'
        return 'OPEN'

    @property
    def month(self):
        return self.invoice_date.month if self.invoice_date else None


class JobCost(models.Model):
    COST_TYPE_CHOICES = [
        ('TRAVEL', 'Travel'),
        ('FLIGHT', 'Flight'),
        ('MISC', 'Misc'),
        ('OTHER', 'Other'),
    ]

    SOURCE_CHOICES = [
        ('MANUAL', 'Manual'),
        ('IMPORT', 'Import'),
        ('LEGACY', 'Legacy'),
    ]

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='job_costs')
    cost_type = models.CharField(max_length=20, choices=COST_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    travel_port = models.CharField(max_length=100, blank=True, default='')
    description = models.CharField(max_length=255, blank=True, default='')
    date = models.DateField(null=True, blank=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='MANUAL')
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='job_costs_created')
    created_at = models.DateTimeField(auto_now_add=True)

    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default='AED')
    exchange_rate = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal('1.000000'))
    amount_aed = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        ordering = ['date', 'id']

    def __str__(self):
        return f"{self.invoice.invoice_number} - {self.get_cost_type_display()}: {self.currency} {self.amount}"

    def clean(self):
        super().clean()
        if self.currency != 'AED':
            if not self.exchange_rate or self.exchange_rate <= Decimal('0'):
                raise ValidationError({'exchange_rate': 'Rate must be greater than zero.'})

    def save(self, *args, **kwargs):
        if not self.date and self.invoice_id and getattr(self.invoice, 'invoice_date', None):
            self.date = self.invoice.invoice_date
        if self.currency == 'AED':
            self.exchange_rate = Decimal('1.000000')
        self.amount_aed = to_aed(self.amount, self.exchange_rate)
        super().save(*args, **kwargs)


class SpareSale(models.Model):
    SOURCE_CHOICES = [
        ('SERVICE_JOB', 'Service Job'),
        ('SPARES_ONLY', 'Spares Only'),
    ]

    STATUS_CHOICES = [
        ('QUOTED', 'Quoted'),
        ('ORDERED', 'Ordered'),
        ('DELIVERED', 'Delivered'),
        ('INVOICED', 'Invoiced'),
    ]

    service_ref = models.CharField(max_length=100, blank=True, default='')
    vessel_customer = models.CharField(max_length=255, blank=True, default='')
    project = models.ForeignKey('Admin.Project', null=True, blank=True, on_delete=models.SET_NULL, related_name='spare_sales')
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.SET_NULL, related_name='spare_sales')
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='SERVICE_JOB')
    purchase_cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    selling_price = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='QUOTED')
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='spare_sales_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    purchase_currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default='AED')
    purchase_rate = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal('1.000000'))
    purchase_cost_aed = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    selling_currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default='AED')
    selling_rate = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal('1.000000'))
    selling_price_aed = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f"{self.service_ref or 'No Ref'} - {self.vessel_customer}: Purchase {self.purchase_currency} {self.purchase_cost}, Selling {self.selling_currency} {self.selling_price}"

    def clean(self):
        super().clean()
        errors = {}
        if self.purchase_currency != 'AED':
            if not self.purchase_rate or self.purchase_rate <= Decimal('0'):
                errors['purchase_rate'] = 'Rate must be greater than zero.'
        if self.selling_currency != 'AED':
            if not self.selling_rate or self.selling_rate <= Decimal('0'):
                errors['selling_rate'] = 'Rate must be greater than zero.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.invoice_id:
            self.status = 'INVOICED'
            if not self.service_ref and getattr(self.invoice, 'service_ref', None):
                self.service_ref = self.invoice.service_ref
            if not self.vessel_customer:
                vessel = getattr(self.invoice, 'vessel', '') or ''
                cust = getattr(self.invoice, 'customer', '') or ''
                self.vessel_customer = f"{vessel} / {cust}".strip(' /')
            leg_costs = JobCost.objects.filter(invoice_id=self.invoice_id, source='LEGACY', description='Legacy purchases')
            self._legacy_purchases_cost = sum(((c.amount_aed or Decimal('0.00')) for c in leg_costs), Decimal('0.00'))

        if self.purchase_currency == 'AED':
            self.purchase_rate = Decimal('1.000000')
        if self.selling_currency == 'AED':
            self.selling_rate = Decimal('1.000000')

        self.purchase_cost_aed = to_aed(self.purchase_cost, self.purchase_rate)
        self.selling_price_aed = to_aed(self.selling_price, self.selling_rate)
        super().save(*args, **kwargs)

    @property
    def profit_aed(self):
        return (self.selling_price_aed or Decimal('0.00')) - (self.purchase_cost_aed or Decimal('0.00'))

    @property
    def profit(self):
        return self.profit_aed

    @property
    def markup_pct(self):
        cost = self.purchase_cost_aed or Decimal('0.00')
        if cost > Decimal('0.00'):
            return (self.profit_aed / cost) * Decimal('100.00')
        return None

    @property
    def alerts(self):
        from django.utils import timezone
        alerts_list = []
        cost = self.purchase_cost_aed or Decimal('0.00')
        selling = self.selling_price_aed or Decimal('0.00')

        if self.profit_aed < Decimal('0.00'):
            alerts_list.append("Loss on spares")
        if self.markup_pct is not None and self.markup_pct < Decimal('10.00'):
            alerts_list.append("Low markup")
        if self.markup_pct is not None and self.markup_pct > Decimal('200.00'):
            alerts_list.append("Check markup")
        if not self.invoice_id and self.created_at:
            if (timezone.localdate() - self.created_at.date()).days > 14:
                alerts_list.append("Not invoiced")
        if cost <= Decimal('0.00'):
            alerts_list.append("Missing purchase cost")
        if selling <= Decimal('0.00'):
            alerts_list.append("Missing selling price")

        if hasattr(self, '_invoice_spares_mismatch'):
            if self._invoice_spares_mismatch:
                alerts_list.append("Invoice spares mismatch")
        elif self.invoice and self.invoice.invoice_type == 'SPARES_ONLY' and (self.selling_price_aed or Decimal('0.00')) != self.invoice.amount_excl_vat_aed:
            alerts_list.append("Invoice spares mismatch")

        if self.invoice_id:
            leg_cost = None
            if hasattr(self, '_legacy_purchases_cost'):
                leg_cost = self._legacy_purchases_cost
            elif hasattr(self, '_prefetched_objects_cache') and 'invoice' in self._prefetched_objects_cache and hasattr(self.invoice, '_prefetched_objects_cache') and 'job_costs' in self.invoice._prefetched_objects_cache:
                leg_cost = sum(((c.amount_aed or Decimal('0.00')) for c in self.invoice.job_costs.all() if c.source == 'LEGACY' and c.description == 'Legacy purchases'), Decimal('0.00'))

            if leg_cost and leg_cost > Decimal('0.00'):
                alerts_list.append(f"This invoice also has a Legacy purchases cost of AED {leg_cost:,.2f} — delete it if it is the same spares cost, otherwise it is counted twice.")

        return alerts_list



class ExchangeRate(models.Model):
    currency = models.CharField(
        max_length=3,
        choices=NON_BASE_CURRENCY_CHOICES,
        help_text="Foreign currency code (e.g. USD, INR, EUR)",
    )
    rate_to_aed = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        validators=[MinValueValidator(Decimal("0.000001"))],
        help_text="Exchange rate: 1 unit of foreign currency = X AED",
    )
    effective_from = models.DateField(
        help_text="Date from which this rate applies",
    )
    notes = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional remarks",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive rates are ignored in rate lookups",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exchange_rates_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("currency", "effective_from")
        ordering = ["currency", "-effective_from"]
        verbose_name = "Exchange Rate"
        verbose_name_plural = "Exchange Rates"

    def __str__(self):
        return f"1 {self.currency} = {self.rate_to_aed} AED (from {self.effective_from})"

    def clean(self):
        super().clean()
        if self.rate_to_aed is not None and self.rate_to_aed <= Decimal("0"):
            raise ValidationError({"rate_to_aed": "Rate must be greater than zero."})

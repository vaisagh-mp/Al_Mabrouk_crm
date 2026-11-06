from decimal import Decimal
from django.db import migrations
from django.db.models import F


def populate_aed_fields(apps, schema_editor):
    Invoice = apps.get_model('finance', 'Invoice')
    JobCost = apps.get_model('finance', 'JobCost')
    SpareSale = apps.get_model('finance', 'SpareSale')
    Expense = apps.get_model('finance', 'Expense')

    # Invoice
    Invoice.objects.all().update(
        currency='AED',
        exchange_rate=Decimal('1.000000'),
        amount_excl_vat_aed=F('amount_excl_vat'),
        vat_amount_aed=F('vat_amount'),
        credits_applied_aed=F('credits_applied'),
        amount_received_aed=F('amount_received'),
    )

    # JobCost
    JobCost.objects.all().update(
        currency='AED',
        exchange_rate=Decimal('1.000000'),
        amount_aed=F('amount'),
    )

    # SpareSale
    SpareSale.objects.all().update(
        purchase_currency='AED',
        purchase_rate=Decimal('1.000000'),
        purchase_cost_aed=F('purchase_cost'),
        selling_currency='AED',
        selling_rate=Decimal('1.000000'),
        selling_price_aed=F('selling_price'),
    )

    # Expense
    Expense.objects.all().update(
        currency='AED',
        exchange_rate=Decimal('1.000000'),
        amount_aed=F('amount'),
        vat_amount_aed=F('vat_amount'),
    )


def rollback_aed_fields(apps, schema_editor):
    Invoice = apps.get_model('finance', 'Invoice')
    JobCost = apps.get_model('finance', 'JobCost')
    SpareSale = apps.get_model('finance', 'SpareSale')
    Expense = apps.get_model('finance', 'Expense')

    Invoice.objects.all().update(
        amount_excl_vat_aed=Decimal('0.00'),
        vat_amount_aed=Decimal('0.00'),
        credits_applied_aed=Decimal('0.00'),
        amount_received_aed=Decimal('0.00'),
    )
    JobCost.objects.all().update(amount_aed=Decimal('0.00'))
    SpareSale.objects.all().update(purchase_cost_aed=Decimal('0.00'), selling_price_aed=Decimal('0.00'))
    Expense.objects.all().update(amount_aed=Decimal('0.00'), vat_amount_aed=Decimal('0.00'))


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0008_expense_amount_aed_expense_currency_and_more'),
    ]

    operations = [
        migrations.RunPython(populate_aed_fields, rollback_aed_fields),
    ]


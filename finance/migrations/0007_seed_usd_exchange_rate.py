from decimal import Decimal
import datetime
from django.db import migrations


def seed_usd_rate(apps, schema_editor):
    ExchangeRate = apps.get_model('finance', 'ExchangeRate')
    ExchangeRate.objects.get_or_create(
        currency='USD',
        effective_from=datetime.date(2020, 1, 1),
        defaults={
            'rate_to_aed': Decimal('3.672500'),
            'notes': 'AED is pegged to USD',
            'is_active': True,
        },
    )


def unseed_usd_rate(apps, schema_editor):
    ExchangeRate = apps.get_model('finance', 'ExchangeRate')
    ExchangeRate.objects.filter(
        currency='USD',
        effective_from=datetime.date(2020, 1, 1),
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0006_exchangerate'),
    ]

    operations = [
        migrations.RunPython(seed_usd_rate, unseed_usd_rate),
    ]


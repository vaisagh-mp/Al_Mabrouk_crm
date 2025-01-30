# Generated by Django 5.1.4 on 2025-01-30 04:33

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Admin', '0022_project_deadline_date'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='teammemberstatus',
            name='last_updated',
        ),
        migrations.AddField(
            model_name='teammemberstatus',
            name='created',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='teammemberstatus',
            name='notes',
            field=models.TextField(blank=True, max_length=2000, null=True),
        ),
    ]

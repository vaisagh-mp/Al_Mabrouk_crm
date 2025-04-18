# Generated by Django 5.1.4 on 2025-01-31 07:24

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Admin', '0031_attendance_total_travel_time'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='attendance',
            name='total_travel_time',
        ),
        migrations.RemoveField(
            model_name='attendance',
            name='travel_in_time',
        ),
        migrations.RemoveField(
            model_name='attendance',
            name='travel_out_time',
        ),
        migrations.CreateModel(
            name='TravelTime',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('travel_in_time', models.DateTimeField(blank=True, null=True)),
                ('travel_out_time', models.DateTimeField(blank=True, null=True)),
                ('total_travel_time', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='travel_times', to='Admin.employee')),
            ],
        ),
    ]

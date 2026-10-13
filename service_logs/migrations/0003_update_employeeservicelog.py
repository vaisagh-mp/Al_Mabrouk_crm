# Generated migration for Part 3: Update EmployeeServiceLog
# Adds time_log, is_auto_created; makes end_time nullable; adds defaults for cost/hours/vessel/port

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('service_logs', '0002_alter_employeeservicelog_ot_type'),
        ('Admin', '0007_project_service_ref_workordertime_employee'),
    ]

    operations = [
        # Make end_time optional
        migrations.AlterField(
            model_name='employeeservicelog',
            name='end_time',
            field=models.TimeField(blank=True, null=True),
        ),
        # Add default to total_hours
        migrations.AlterField(
            model_name='employeeservicelog',
            name='total_hours',
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=5),
        ),
        # Add default to normal_cost
        migrations.AlterField(
            model_name='employeeservicelog',
            name='normal_cost',
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=10),
        ),
        # Add default to ot_cost
        migrations.AlterField(
            model_name='employeeservicelog',
            name='ot_cost',
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=10),
        ),
        # Make vessel_name optional with default
        migrations.AlterField(
            model_name='employeeservicelog',
            name='vessel_name',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        # Make port optional with default
        migrations.AlterField(
            model_name='employeeservicelog',
            name='port',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        # Add default to location_type
        migrations.AlterField(
            model_name='employeeservicelog',
            name='location_type',
            field=models.CharField(
                choices=[('ANCHORAGE', 'Anchorage'), ('SAILING', 'Sailing')],
                default='ANCHORAGE',
                max_length=20,
            ),
        ),
        # Add time_log OneToOne
        migrations.AddField(
            model_name='employeeservicelog',
            name='time_log',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='service_log',
                to='Admin.workordertime',
            ),
        ),
        # Add is_auto_created flag
        migrations.AddField(
            model_name='employeeservicelog',
            name='is_auto_created',
            field=models.BooleanField(default=False),
        ),
    ]

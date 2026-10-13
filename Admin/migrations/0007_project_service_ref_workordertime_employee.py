# Generated migration for Part 1 (service_reference on Project) and Part 2 (employee + optional finish_time on WorkOrderTime)

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Admin', '0006_create_spareconsumed'),
    ]

    operations = [
        # Part 1: Add service_reference to Project
        migrations.AddField(
            model_name='project',
            name='service_reference',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        # Part 2a: Make finish_time optional on WorkOrderTime
        migrations.AlterField(
            model_name='workordertime',
            name='finish_time',
            field=models.TimeField(blank=True, null=True),
        ),
        # Part 2b: Add employee FK to WorkOrderTime
        migrations.AddField(
            model_name='workordertime',
            name='employee',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='work_order_times',
                to='Admin.employee',
            ),
        ),
    ]

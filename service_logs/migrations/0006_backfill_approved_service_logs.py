# Generated data migration to set existing EmployeeServiceLog records to APPROVED

from django.db import migrations

def backfill_approved_service_logs(apps, schema_editor):
    EmployeeServiceLog = apps.get_model('service_logs', 'EmployeeServiceLog')
    EmployeeServiceLog.objects.all().update(approval_status='APPROVED')

def reverse_backfill(apps, schema_editor):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('service_logs', '0005_employeeservicelog_approval_status_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_approved_service_logs, reverse_backfill),
    ]


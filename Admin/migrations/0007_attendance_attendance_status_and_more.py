# Generated by Django 5.1.4 on 2025-01-06 05:21

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Admin', '0006_attendance_location_alter_attendance_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='attendance',
            name='attendance_status',
            field=models.CharField(blank=True, choices=[('PRESENT', 'Present'), ('WORK FROM HOME', 'Work from Home'), ('SICK LEAVE', 'Sick Leave'), ('ANNUAL LEAVE', 'Annual Leave'), ('CASUAL LEAVE', 'Casual Leave')], max_length=20, null=True),
        ),
        migrations.AlterField(
            model_name='attendance',
            name='log_out_time',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='attendance',
            name='login_time',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='attendance',
            name='status',
            field=models.CharField(choices=[('PENDING', 'Pending'), ('APPROVED', 'Approved'), ('REJECTED', 'Rejected')], default='PENDING', max_length=10),
        ),
        migrations.AlterField(
            model_name='attendance',
            name='total_hours_of_work',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
    ]

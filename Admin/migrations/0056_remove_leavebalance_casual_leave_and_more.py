# Generated by Django 5.1.4 on 2025-06-19 11:53

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Admin', '0055_holiday'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='leavebalance',
            name='casual_leave',
        ),
        migrations.AlterField(
            model_name='leave',
            name='leave_type',
            field=models.CharField(choices=[('SICK LEAVE', 'Sick Leave'), ('ANNUAL LEAVE', 'Annual Leave')], max_length=50),
        ),
        migrations.AlterField(
            model_name='leavebalance',
            name='annual_leave',
            field=models.IntegerField(default=30),
        ),
    ]

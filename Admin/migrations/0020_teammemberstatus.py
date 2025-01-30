# Generated by Django 5.1.4 on 2025-01-22 05:02

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Admin', '0019_alter_team_employees_alter_team_manager'),
    ]

    operations = [
        migrations.CreateModel(
            name='TeamMemberStatus',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('ONGOING', 'Ongoing'), ('HOLD', 'Hold'), ('CANCELLED', 'Cancelled'), ('COMPLETED', 'Completed')], default='PENDING', max_length=100)),
                ('last_updated', models.DateTimeField(auto_now=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='project_statuses', to='Admin.employee')),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='team_members_status', to='Admin.team')),
            ],
            options={
                'unique_together': {('team', 'employee')},
            },
        ),
    ]

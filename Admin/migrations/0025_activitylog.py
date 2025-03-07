# Generated by Django 5.1.4 on 2025-01-30 04:52

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Admin', '0024_remove_teammemberstatus_created_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ActivityLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('old_status', models.CharField(choices=[('ASSIGN', 'Assign'), ('ONGOING', 'Ongoing'), ('HOLD', 'Hold'), ('CANCELLED', 'Cancelled'), ('COMPLETED', 'Completed')], max_length=100)),
                ('new_status', models.CharField(choices=[('ASSIGN', 'Assign'), ('ONGOING', 'Ongoing'), ('HOLD', 'Hold'), ('CANCELLED', 'Cancelled'), ('COMPLETED', 'Completed')], max_length=100)),
                ('changed_at', models.DateTimeField(auto_now_add=True)),
                ('changed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('team_member_status', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='activity_logs', to='Admin.teammemberstatus')),
            ],
        ),
    ]

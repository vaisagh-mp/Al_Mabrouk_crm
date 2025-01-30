# Generated by Django 5.1.4 on 2025-01-30 08:41

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Admin', '0028_activitylog_notes'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Leave',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('leave_type', models.CharField(choices=[('SICK LEAVE', 'Sick Leave'), ('ANNUAL LEAVE', 'Annual Leave'), ('CASUAL LEAVE', 'Casual Leave'), ('WORK FROM HOME', 'Work From Home')], max_length=50)),
                ('from_date', models.DateField()),
                ('to_date', models.DateField()),
                ('reason', models.TextField()),
                ('no_of_days', models.IntegerField(editable=False)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]

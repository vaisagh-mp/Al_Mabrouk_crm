# Generated by Django 5.1.4 on 2025-02-20 11:50

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Admin', '0046_project_attachment'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='is_administration',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='employee',
            name='is_hr',
            field=models.BooleanField(default=False),
        ),
    ]

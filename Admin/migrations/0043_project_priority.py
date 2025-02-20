# Generated by Django 5.1.4 on 2025-02-18 04:03

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Admin', '0042_notification'),
    ]

    operations = [
        migrations.AddField(
            model_name='project',
            name='priority',
            field=models.CharField(choices=[('HIGH', 'High'), ('MEDIUM', 'Medium'), ('LOW', 'Low')], default='MEDIUM', max_length=10),
        ),
    ]

# Generated by Django 5.1.4 on 2024-12-17 10:10

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Admin', '0003_alter_project_invoice_amount'),
    ]

    operations = [
        migrations.AlterField(
            model_name='project',
            name='status',
            field=models.CharField(choices=[('ACTIVE', 'Active'), ('INACTIVE', 'Inactive')], default='ACTIVE', max_length=100),
        ),
    ]

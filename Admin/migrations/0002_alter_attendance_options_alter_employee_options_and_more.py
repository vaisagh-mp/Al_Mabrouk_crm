# Generated by Django 5.1.4 on 2024-12-17 10:06

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Admin', '0001_initial'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='attendance',
            options={},
        ),
        migrations.AlterModelOptions(
            name='employee',
            options={},
        ),
        migrations.RemoveField(
            model_name='employee',
            name='project',
        ),
        migrations.RemoveField(
            model_name='employee',
            name='project_invoice',
        ),
        migrations.RemoveField(
            model_name='employee',
            name='project_purchase_cost',
        ),
        migrations.AddField(
            model_name='project',
            name='currency_code',
            field=models.CharField(default='USD', max_length=10),
        ),
        migrations.AddField(
            model_name='project',
            name='invoice_amount',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=12),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='project',
            name='purchase_and_expenses',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=12),
        ),
        migrations.AddField(
            model_name='project',
            name='status',
            field=models.CharField(default='In Progress', max_length=100),
        ),
        migrations.AlterField(
            model_name='attendance',
            name='status',
            field=models.CharField(choices=[('DMC', 'DMC Warehouse'), ('FUJ', 'FUJ'), ('KFK', 'KFK'), ('ABU', 'Abu Dhabi'), ('BDO', 'Bur Dubai Office'), ('OVERSEAS', 'Overseas'), ('WORK FROM HOME', 'Work from Home'), ('SICK LEAVE', 'Sick Leave'), ('ANNUAL LEAVE', 'Annual Leave')], max_length=20),
        ),
        migrations.CreateModel(
            name='ProjectAssignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('time_start', models.DateTimeField()),
                ('time_stop', models.DateTimeField()),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='Admin.employee')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='Admin.project')),
            ],
        ),
    ]
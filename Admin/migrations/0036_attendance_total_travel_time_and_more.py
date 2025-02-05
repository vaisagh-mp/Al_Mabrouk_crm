# Generated by Django 5.1.4 on 2025-02-03 07:56

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Admin', '0035_remove_attendance_total_travel_time_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='attendance',
            name='total_travel_time',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='attendance',
            name='travel_in_time',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='attendance',
            name='travel_out_time',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.DeleteModel(
            name='Travel',
        ),
    ]

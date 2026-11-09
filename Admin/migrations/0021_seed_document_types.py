from django.db import migrations


def seed_document_types(apps, schema_editor):
    DocumentType = apps.get_model('Admin', 'DocumentType')

    employee_types = [
        {'name': 'Passport', 'has_expiry': True, 'reminder_days': '90,60,30', 'is_required': True, 'sort_order': 1},
        {'name': 'Residence Visa', 'has_expiry': True, 'reminder_days': '90,60,30', 'is_required': True, 'sort_order': 2},
        {'name': 'Emirates ID', 'has_expiry': True, 'reminder_days': '90,60,30', 'is_required': True, 'sort_order': 3},
        {'name': 'Labour Card / Work Permit', 'has_expiry': True, 'reminder_days': '90,60,30', 'is_required': True, 'sort_order': 4},
        {'name': 'Employment Contract', 'has_expiry': True, 'reminder_days': '90,60,30', 'is_required': True, 'sort_order': 5},
        {'name': 'Medical Insurance', 'has_expiry': True, 'reminder_days': '90,60,30', 'is_required': True, 'sort_order': 6},
        {'name': 'Driving License', 'has_expiry': True, 'reminder_days': '90,60,30', 'is_required': False, 'sort_order': 7},
        {'name': 'Educational Certificates', 'has_expiry': False, 'reminder_days': '', 'is_required': False, 'sort_order': 8},
        {'name': 'Technical / Professional Certificates', 'has_expiry': False, 'reminder_days': '', 'is_required': False, 'sort_order': 9},
        {'name': 'Other', 'has_expiry': False, 'reminder_days': '', 'is_required': False, 'sort_order': 10},
    ]

    company_types = [
        {'name': 'Trade License', 'has_expiry': True, 'reminder_days': '90,60,30', 'is_required': False, 'sort_order': 1},
        {'name': 'Establishment Card', 'has_expiry': True, 'reminder_days': '90,60,30', 'is_required': False, 'sort_order': 2},
        {'name': 'Ejari / Lease Agreement', 'has_expiry': True, 'reminder_days': '90,60,30', 'is_required': False, 'sort_order': 3},
        {'name': 'Trakhees Permits', 'has_expiry': True, 'reminder_days': '90,60,30', 'is_required': False, 'sort_order': 4},
        {'name': 'ISO Certificates', 'has_expiry': True, 'reminder_days': '90,60,30', 'is_required': False, 'sort_order': 5},
        {'name': 'Class Certificates', 'has_expiry': True, 'reminder_days': '90,60,30', 'is_required': False, 'sort_order': 6},
        {'name': 'Insurance Policies', 'has_expiry': True, 'reminder_days': '90,60,30', 'is_required': False, 'sort_order': 7},
        {'name': 'Other', 'has_expiry': False, 'reminder_days': '', 'is_required': False, 'sort_order': 8},
    ]

    for item in employee_types:
        DocumentType.objects.get_or_create(
            name=item['name'],
            category='EMPLOYEE',
            defaults=item,
        )

    for item in company_types:
        DocumentType.objects.get_or_create(
            name=item['name'],
            category='COMPANY',
            defaults=item,
        )


def reverse_seed(apps, schema_editor):
    DocumentType = apps.get_model('Admin', 'DocumentType')
    DocumentType.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('Admin', '0020_employee_last_working_day_employee_notice_start_date_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_document_types, reverse_code=reverse_seed),
    ]


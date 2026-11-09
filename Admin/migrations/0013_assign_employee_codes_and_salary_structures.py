from datetime import date
from decimal import Decimal
from django.db import migrations, models


def migrate_employee_codes_and_salary(apps, schema_editor):
    Employee = apps.get_model('Admin', 'Employee')
    SalaryStructure = apps.get_model('Admin', 'SalaryStructure')

    # Existing staff numbered by date_of_join (oldest first; no joining date -> after the others, by id)
    employees = list(Employee.objects.all().order_by(
        models.F('date_of_join').asc(nulls_last=True),
        'id'
    ).select_related('user'))

    seq = 1
    for emp in employees:
        if not emp.employee_code:
            emp.employee_code = f"AMMS-{seq:04d}"
            seq += 1

        if not emp.personal_email and emp.user and emp.user.email:
            emp.personal_email = emp.user.email

        emp.save(update_fields=['employee_code', 'personal_email'])

        # Create initial SalaryStructure if salary is not None
        if emp.salary is not None:
            eff_from = emp.date_of_join or date(2020, 1, 1)
            if not SalaryStructure.objects.filter(employee=emp).exists():
                SalaryStructure.objects.create(
                    employee=emp,
                    basic=emp.salary,
                    housing_allowance=Decimal('0.00'),
                    transport_allowance=Decimal('0.00'),
                    other_allowances=Decimal('0.00'),
                    total=emp.salary,
                    effective_from=eff_from,
                    notes='Initial migrated salary structure'
                )


def reverse_migration(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('Admin', '0012_branch_department_employee_company_email_and_more'),
    ]

    operations = [
        migrations.RunPython(migrate_employee_codes_and_salary, reverse_migration),
    ]


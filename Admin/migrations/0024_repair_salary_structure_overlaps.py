from datetime import timedelta
from django.db import migrations


def repair_salary_structure_overlaps(apps, schema_editor):
    Employee = apps.get_model('Admin', 'Employee')
    SalaryStructure = apps.get_model('Admin', 'SalaryStructure')
    EmployeeChangeLog = apps.get_model('Admin', 'EmployeeChangeLog')

    for emp in Employee.objects.all():
        structs = list(SalaryStructure.objects.filter(employee=emp).order_by('effective_from', 'id'))
        if len(structs) <= 1:
            continue

        for i, s in enumerate(structs):
            # Check if there is another structure with the exact same effective_from after s (higher id)
            same_day_newer = None
            for j in range(i + 1, len(structs)):
                if structs[j].effective_from == s.effective_from:
                    same_day_newer = structs[j]
                    break

            if same_day_newer:
                max_to = s.effective_from - timedelta(days=1)
                if s.effective_to != max_to:
                    old_to_str = str(s.effective_to) if s.effective_to else 'None'
                    s.effective_to = max_to
                    s.save(update_fields=['effective_to'])
                    EmployeeChangeLog.objects.create(
                        employee=emp,
                        field='salary_structure',
                        old_value=f"Structure {s.id} effective_to: {old_to_str}",
                        new_value=f"Structure {s.id} effective_to: {max_to}",
                        source='Structure repair'
                    )
                continue

            # Find earliest structure starting strictly after s.effective_from
            next_struct = None
            for j in range(i + 1, len(structs)):
                if structs[j].effective_from > s.effective_from:
                    next_struct = structs[j]
                    break

            if next_struct:
                max_to = next_struct.effective_from - timedelta(days=1)
                if s.effective_to is None or s.effective_to > max_to:
                    old_to_str = str(s.effective_to) if s.effective_to else 'None'
                    s.effective_to = max_to
                    s.save(update_fields=['effective_to'])
                    EmployeeChangeLog.objects.create(
                        employee=emp,
                        field='salary_structure',
                        old_value=f"Structure {s.id} effective_to: {old_to_str}",
                        new_value=f"Structure {s.id} effective_to: {max_to}",
                        source='Structure repair'
                    )


def reverse_repair(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('Admin', '0023_alter_documentreminderlog_unique_together_and_more'),
    ]

    operations = [
        migrations.RunPython(repair_salary_structure_overlaps, reverse_repair),
    ]


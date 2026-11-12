from django.contrib.auth.models import User


def can_view_salary(user) -> bool:
    """
    Salary visible ONLY to HR (is_hr), Accountant (is_accountant) and Admin (superuser).
    No new role.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, 'employee_profile', None)
    if profile and (profile.is_hr or profile.is_accountant):
        return True
    return False


def can_manage_hr_settings(user) -> bool:
    """
    HR master data (Departments, Branches) can be managed ONLY by HR (is_hr) and Admin (superuser).
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, 'employee_profile', None)
    if profile and profile.is_hr:
        return True
    return False


def can_view_document(user, doc) -> bool:
    """
    Download/preview permission:
    - Superuser: True
    - HR (profile.is_hr): True
    - Document's own employee (employee documents only): True
    - Company documents: superuser and HR only.
    - Manager, Accountant, Administration, other employees: False (403).
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, 'employee_profile', None)
    if profile and profile.is_hr:
        return True

    employee = getattr(doc, 'employee', None)
    if employee and employee.user_id == user.id:
        return True

    return False



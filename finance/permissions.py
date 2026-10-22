from django.core.exceptions import PermissionDenied


def is_accountant(user):
    """
    Check if the user is an accountant or superuser.
    Raises PermissionDenied if neither so user_passes_test returns 403.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, 'employee_profile', None)
    if profile and profile.is_accountant:
        return True
    raise PermissionDenied


def is_finance_admin(user):
    """
    Check if the user is an authenticated superuser.
    Raises PermissionDenied for authenticated non-superusers so user_passes_test returns 403.
    """
    if not (user and user.is_authenticated):
        return False
    if user.is_superuser:
        return True
    raise PermissionDenied


def can_view_finance(user):
    """
    Check if the user is authorized to view finance data (superusers and accountants).
    Returns boolean without raising PermissionDenied.
    """
    if not (user and user.is_authenticated):
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, 'employee_profile', None)
    if profile and getattr(profile, 'is_accountant', False):
        return True
    return False


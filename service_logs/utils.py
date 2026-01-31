def is_admin(user):
    return user.is_superuser


def is_manager(user):
    return getattr(user.employee_profile, 'is_manager', False)


def is_employee(user):
    return getattr(user.employee_profile, 'is_employee', False)

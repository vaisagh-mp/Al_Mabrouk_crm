from django.core.exceptions import PermissionDenied
from .permissions import is_finance_admin


def finance_role(request):
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {'is_finance_admin': False}
    try:
        is_admin = bool(is_finance_admin(user))
    except PermissionDenied:
        is_admin = False
    return {'is_finance_admin': is_admin}

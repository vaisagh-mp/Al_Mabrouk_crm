"""
Remember which portal (Admin / HR / Finance) a superuser is working in, so pages shared between
portals (payroll & salary, documents, my payslips) keep that portal's sidebar.

- Any /admin-panel/ page            -> admin portal
- Any /hr/ page                     -> HR portal
- The Finance dashboard (/finance/) -> finance portal
  (other /finance/ pages are shared, so they do not change the portal)
"""

PORTAL_BASES = {
    'admin': 'Admin/adminbase.html',
    'hr': 'hr/hrbase.html',
    'finance': 'finance/financebase.html',
}
SESSION_KEY = 'active_portal'


class PortalMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and user.is_superuser and request.method == 'GET':
            path = request.path
            portal = None
            if path.startswith('/admin-panel/'):
                portal = 'admin'
            elif path.startswith('/hr/'):
                portal = 'hr'
            elif path in ('/finance/', '/finance/dashboard/'):
                portal = 'finance'
            if portal and request.session.get(SESSION_KEY) != portal:
                request.session[SESSION_KEY] = portal
        return self.get_response(request)


def superuser_portal_base(request, default):
    """Base template for a superuser on a shared page: the portal they are in, else default."""
    portal = request.session.get(SESSION_KEY) if hasattr(request, 'session') else None
    return PORTAL_BASES.get(portal, default)

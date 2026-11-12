from collections.abc import Iterable
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone
from Admin.models import Notification

VALID_NOTIFICATION_TYPES = {c[0] for c in Notification.TYPE_CHOICES}

NOTIFICATION_TYPE_META = {
    'LEAVE': {
        'label': 'Leave',
        'icon': 'fas fa-calendar-minus',
        'color': '#0ea5e9',
        'bg': '#e0f2fe',
    },
    'ATTENDANCE_CORRECTION': {
        'label': 'Attendance Correction',
        'icon': 'fas fa-clock',
        'color': '#f59e0b',
        'bg': '#fef3c7',
    },
    'TIMESHEET': {
        'label': 'Timesheet',
        'icon': 'fas fa-stopwatch',
        'color': '#6366f1',
        'bg': '#e0e7ff',
    },
    'OT': {
        'label': 'Overtime',
        'icon': 'fas fa-hourglass-half',
        'color': '#ea580c',
        'bg': '#ffedd5',
    },
    'DOCUMENT_EXPIRY': {
        'label': 'Document Expiry',
        'icon': 'fas fa-file-alt',
        'color': '#ef4444',
        'bg': '#fee2e2',
    },
    'PAYROLL': {
        'label': 'Payroll',
        'icon': 'fas fa-money-bill-alt',
        'color': '#10b981',
        'bg': '#d1fae5',
    },
    'PAYSLIP': {
        'label': 'Payslip',
        'icon': 'fas fa-file-alt',
        'color': '#059669',
        'bg': '#d1fae5',
    },
    'ADVANCE': {
        'label': 'Salary Advance',
        'icon': 'fas fa-dollar-sign',
        'color': '#8b5cf6',
        'bg': '#ede9fe',
    },
    'INCREMENT': {
        'label': 'Salary Increment',
        'icon': 'fas fa-chart-line',
        'color': '#14b8a6',
        'bg': '#ccfbf1',
    },
    'PROJECT': {
        'label': 'Project',
        'icon': 'fas fa-ship',
        'color': '#1f3a52',
        'bg': '#e2e8f0',
    },
    'SYSTEM': {
        'label': 'System',
        'icon': 'fas fa-bell',
        'color': '#64748b',
        'bg': '#f1f5f9',
    },
}


def get_type_meta(notif_type):
    return NOTIFICATION_TYPE_META.get(notif_type, NOTIFICATION_TYPE_META['SYSTEM'])


def get_base_template_for_user(user):
    if not user or not user.is_authenticated:
        return 'employee/employeebase.html'
    if user.is_superuser:
        return 'Admin/adminbase.html'
    emp = getattr(user, 'employee_profile', None)
    if not emp:
        return 'Admin/adminbase.html'
    if emp.is_hr:
        return 'hr/hrbase.html'
    if emp.is_accountant:
        return 'finance/financebase.html'
    if emp.is_manager:
        return 'Manager/managerbase.html'
    if emp.is_administration:
        return 'administration/administrationbase.html'
    return 'employee/employeebase.html'


def get_user_role_link(user, area, obj_id=None, query=''):
    """
    Returns an appropriate URL path for the recipient user and target area.
    """
    if not user:
        return ''
    emp = getattr(user, 'employee_profile', None)
    is_su = bool(getattr(user, 'is_superuser', False))
    is_hr = bool(emp and emp.is_hr)
    is_mgr = bool(emp and emp.is_manager)
    is_acc = bool(emp and emp.is_accountant)
    is_adm = bool(emp and emp.is_administration)

    path = ''
    if area == 'leave_manage':
        if is_su:
            path = '/admin-panel/employee-manage-leave/'
        elif is_hr:
            path = '/hr/employee-manage-leave/'
        elif is_mgr:
            path = '/manager/manage-leave/'
        else:
            path = '/hr/employee-manage-leave/'
    elif area == 'leave_status':
        if is_hr:
            path = '/hr/my-leave/'
        elif is_mgr:
            path = '/manager/my-leave/'
        elif is_acc:
            path = '/finance/my-leave/'
        elif is_adm:
            path = '/administration/my-leave/'
        elif is_su:
            path = '/admin-panel/employee-leave-list/'
        else:
            path = '/employee/my-leave/'
    elif area == 'correction_team':
        if is_su:
            path = '/admin-panel/attendance-status/?tab=team_requests'
        elif is_hr:
            path = '/hr/attendance-status/?tab=team_requests'
        elif is_mgr:
            path = '/manager/attendance-status/?tab=team_requests'
        else:
            path = '/hr/attendance-status/?tab=team_requests'
    elif area == 'correction_my':
        if is_hr:
            path = '/hr/attendance-status/?tab=my_requests'
        elif is_mgr:
            path = '/manager/attendance-status/?tab=my_requests'
        elif is_acc:
            path = '/finance/attendance-status/?tab=my_requests'
        elif is_adm:
            path = '/administration/attendance-status/?tab=my_requests'
        else:
            path = '/employee/attendance-list/?tab=my_requests'
    elif area == 'timesheet_approvals':
        if is_su:
            path = '/admin-panel/timesheet-approvals/'
        elif is_hr:
            path = '/hr/timesheet-approvals/'
        elif is_mgr:
            path = '/manager/timesheet-approvals/'
        else:
            path = '/hr/timesheet-approvals/'
    elif area == 'timesheet_my':
        if obj_id:
            if is_su:
                path = f'/admin-panel/work-order/view/{obj_id}/'
            elif is_mgr:
                path = f'/manager/work-order/view/{obj_id}/'
            else:
                path = f'/employee/work-order/view/{obj_id}/'
        else:
            path = '/service-logs/service-logs/'
    elif area == 'ot_approval':
        if is_su:
            path = '/admin-panel/ot-approval/'
        elif is_hr:
            path = '/hr/ot-approval/'
        elif is_mgr:
            path = '/manager/ot-approval/'
        else:
            path = '/hr/ot-approval/'
    elif area == 'documents':
        if is_su:
            path = '/admin-panel/documents/'
        elif is_hr:
            path = '/hr/documents/'
        else:
            path = '/employee/profile/'
    elif area == 'payslips':
        path = '/finance/my-payslips/'
    elif area == 'payroll':
        path = '/finance/payroll/'
    elif area == 'advances':
        path = '/finance/advances/'
    elif area == 'increments':
        path = '/finance/increments/'
    elif area == 'project':
        if is_su:
            path = f'/admin-panel/project/{obj_id}/' if obj_id else '/admin-panel/projects/'
        elif is_mgr:
            path = f'/manager/project/{obj_id}/' if obj_id else '/manager/projects/'
        elif is_adm:
            path = f'/administration/project/{obj_id}/' if obj_id else '/administration/projects/'
        elif is_acc:
            path = f'/finance/projects/{obj_id}/' if obj_id else '/finance/projects/'
        else:
            path = f'/employee/project/{obj_id}/' if obj_id else '/employee/projects/'

    if query and path:
        sep = '&' if '?' in path else '?'
        path = f"{path}{sep}{query.lstrip('?&')}"
    return path


def _extract_related(related):
    if not related:
        return None, None
    if isinstance(related, (tuple, list)) and len(related) == 2:
        kind, rid = related
        try:
            rid = int(rid) if rid is not None else None
        except (TypeError, ValueError):
            rid = None
        return str(kind)[:50] if kind else None, rid
    pk = getattr(related, 'pk', None)
    try:
        pk = int(pk) if pk is not None else None
    except (TypeError, ValueError):
        pk = None
    return related.__class__.__name__[:50], pk


def notify(users, type='SYSTEM', message='', link='', related=None, actor=None):
    """
    Create typed notifications for one or more users.
    - Active users only
    - De-duplicated per recipient
    - Never notifies the actor themselves
    - link may be a string URL path or a callable(user) -> str
    """
    if not users:
        return []

    notif_type = type if type in VALID_NOTIFICATION_TYPES else 'SYSTEM'

    actor_id = None
    if isinstance(actor, User):
        actor_id = actor.pk
    elif isinstance(actor, int):
        actor_id = actor

    if isinstance(users, (User, int)):
        raw_list = [users]
    elif isinstance(users, Iterable):
        raw_list = list(users)
    else:
        raw_list = [users]

    user_ids = []
    seen_ids = set()
    user_objs = {}
    for u in raw_list:
        if u is None:
            continue
        if isinstance(u, int):
            uid = u
        elif isinstance(u, User):
            uid = u.pk
            user_objs[uid] = u
        else:
            continue
        if not uid or uid == actor_id or uid in seen_ids:
            continue
        seen_ids.add(uid)
        user_ids.append(uid)

    if not user_ids:
        return []

    active_users = list(
        User.objects.filter(pk__in=user_ids, is_active=True)
        .select_related('employee_profile')
    )
    if not active_users:
        return []

    active_map = {u.pk: u for u in active_users}
    related_kind, related_id = _extract_related(related)

    created = []
    for uid in user_ids:
        u_obj = active_map.get(uid)
        if not u_obj:
            continue
        resolved_link = link(u_obj) if callable(link) else (link or '')
        if resolved_link and len(resolved_link) > 255:
            resolved_link = resolved_link[:255]
        notif = Notification.objects.create(
            recipient=u_obj,
            message=message,
            type=notif_type,
            link=resolved_link,
            related_kind=related_kind,
            related_id=related_id,
        )
        created.append(notif)
    return created


def notify_role(role, type='SYSTEM', message='', link='', related=None, actor=None):
    """
    Notify all active users holding one or more roles ('HR', 'ADMIN', 'ACCOUNTANT', 'MANAGER', 'ADMINISTRATION').
    De-duplicates users across roles and excludes the actor.
    """
    if not role:
        return []

    roles = [role] if isinstance(role, str) else list(role)
    role_q = Q()
    for r in roles:
        key = (r or '').strip().upper()
        if key == 'HR':
            role_q |= Q(employee_profile__is_hr=True)
        elif key in ('ADMIN', 'SUPERUSER'):
            role_q |= Q(is_superuser=True)
        elif key in ('ACCOUNTANT', 'FINANCE'):
            role_q |= Q(employee_profile__is_accountant=True)
        elif key == 'MANAGER':
            role_q |= Q(employee_profile__is_manager=True)
        elif key == 'ADMINISTRATION':
            role_q |= Q(employee_profile__is_administration=True)

    if not role_q:
        return []

    recipients = list(
        User.objects.filter(is_active=True)
        .filter(role_q)
        .select_related('employee_profile')
        .distinct()
    )
    return notify(recipients, type=type, message=message, link=link, related=related, actor=actor)


def serialize_notifications_for_user(user, limit=20):
    """
    Returns unread notifications for user as JSON-serializable list of dicts,
    preserving id, message, created_at and adding type, type_label, icon, color, link, open_url.
    """
    if not user or not user.is_authenticated:
        return []

    qs = Notification.objects.filter(recipient=user, is_read=False).order_by('-created_at', '-id')
    if limit:
        qs = qs[:limit]

    results = []
    for n in qs:
        meta = get_type_meta(n.type)
        results.append({
            'id': n.id,
            'message': n.message,
            'created_at': n.created_at.isoformat() if n.created_at else '',
            'type': n.type,
            'type_label': meta['label'],
            'icon': meta['icon'],
            'color': meta['color'],
            'bg': meta['bg'],
            'link': n.link or '',
            'open_url': f'/notifications/{n.id}/open/',
        })
    return results


def mark_notifications_read(user, notification_id=None):
    """
    Marks notifications as read and sets read_at timestamp.
    """
    if not user or not user.is_authenticated:
        return 0
    qs = Notification.objects.filter(recipient=user, is_read=False)
    if notification_id is not None:
        qs = qs.filter(pk=notification_id)
    now_ts = timezone.now()
    return qs.update(is_read=True, read_at=now_ts)


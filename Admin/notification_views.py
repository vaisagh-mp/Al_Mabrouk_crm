from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from Admin.models import Notification
from Admin.notifications import (
    NOTIFICATION_TYPE_META,
    get_base_template_for_user,
    get_type_meta,
    mark_notifications_read,
)


@login_required
def notifications_page_view(request):
    """
    Shared notifications page for every role.
    Supports filtering by type, read/unread status, search query; 20 per page.
    """
    qs = Notification.objects.filter(recipient=request.user).order_by('-created_at', '-id')

    notif_type = (request.GET.get('type') or '').strip().upper()
    read_filter = (request.GET.get('read') or 'all').strip().lower()
    q = (request.GET.get('q') or '').strip()

    valid_types = {c[0] for c in Notification.TYPE_CHOICES}
    if notif_type and notif_type in valid_types:
        qs = qs.filter(type=notif_type)
    else:
        notif_type = ''

    if read_filter == 'unread':
        qs = qs.filter(is_read=False)
    elif read_filter == 'read':
        qs = qs.filter(is_read=True)
    else:
        read_filter = 'all'

    if q:
        qs = qs.filter(message__icontains=q)

    unread_count = Notification.objects.filter(recipient=request.user, is_read=False).count()

    paginator = Paginator(qs, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    for n in page_obj:
        n.meta = get_type_meta(n.type)

    return render(request, 'notifications/notifications.html', {
        'base_template': get_base_template_for_user(request.user),
        'page_obj': page_obj,
        'type_choices': Notification.TYPE_CHOICES,
        'type_meta_map': NOTIFICATION_TYPE_META,
        'selected_type': notif_type,
        'selected_read': read_filter,
        'q': q,
        'unread_count': unread_count,
    })


@login_required
def notification_open_view(request, notification_id):
    """
    Marks the notification read and redirects to its target link (or notifications page).
    """
    notification = get_object_or_404(Notification, pk=notification_id, recipient=request.user)
    if not notification.is_read:
        mark_notifications_read(request.user, notification_id=notification.id)

    target = (notification.link or '').strip()
    if target and target.startswith('/') and not target.startswith('//'):
        return redirect(target)
    return redirect(reverse('notifications_page'))


@login_required
@require_POST
def notifications_mark_all_read_view(request):
    """
    Marks all unread notifications for the current user as read.
    """
    mark_notifications_read(request.user)
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and next_url.startswith('/') and not next_url.startswith('//'):
        return redirect(next_url)
    return redirect('notifications_page')


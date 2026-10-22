from datetime import datetime, time, timedelta
from django.db import transaction
from django.utils import timezone


class ClosedSessionsCount(int):
    """
    An integer subclass representing the count of closed stale sessions,
    with an extra .dates attribute containing the local dates of closed sessions.
    """
    dates = []

    def __new__(cls, val, dates=None):
        obj = super().__new__(cls, val)
        obj.dates = dates or []
        return obj


def session_cutoff(login_time):
    """
    Returns the aware Asia/Dubai datetime for 00:00 of the day following login_time.
    """
    tz = timezone.get_current_timezone()
    local = timezone.localtime(login_time, tz)
    cutoff_date = local.date() + timedelta(days=1)
    return timezone.make_aware(datetime.combine(cutoff_date, time.min), tz)


def close_stale_sessions(employee=None):
    """
    Finds Attendance sessions with log_out_time IS NULL and login_time before
    today's local midnight (optionally filtered by employee).
    Closes each with log_out_time = session_cutoff(login_time) and auto_punched_out = True,
    calling .save() so total_hours_of_work, work days and OT signals run.
    Uses transaction.atomic() and select_for_update() to avoid concurrency race conditions.
    Returns the number of sessions closed (as an int with a .dates attribute).
    Costs exactly 1 query when no stale sessions exist.
    """
    from Admin.models import Attendance

    tz = timezone.get_current_timezone()
    today_local = timezone.localdate()
    today_midnight = timezone.make_aware(datetime.combine(today_local, time.min), tz)

    base_qs = Attendance.objects.filter(
        log_out_time__isnull=True,
        login_time__lt=today_midnight,
    )
    if employee is not None:
        base_qs = base_qs.filter(employee=employee)

    # Fast check: costs exactly 1 query when nothing is stale
    if not base_qs.exists():
        return ClosedSessionsCount(0, [])

    with transaction.atomic():
        qs = Attendance.objects.select_for_update().filter(
            log_out_time__isnull=True,
            login_time__lt=today_midnight,
        )
        if employee is not None:
            qs = qs.filter(employee=employee)

        stale_records = list(qs)
        closed_dates = []

        for att in stale_records:
            if att.login_time:
                closed_dates.append(timezone.localtime(att.login_time, tz).date())
                att.log_out_time = session_cutoff(att.login_time)
            else:
                att.log_out_time = today_midnight
            att.auto_punched_out = True
            att.save()

    return ClosedSessionsCount(len(stale_records), closed_dates)


def capped_logout(attendance, at=None):
    """
    Caps the logout datetime at the session cutoff (midnight of next day).
    Returns (logout_dt, was_capped).
    """
    if at is None:
        at = timezone.now()

    if not attendance.login_time:
        return at, False

    cutoff = session_cutoff(attendance.login_time)
    if at > cutoff:
        return cutoff, True
    return at, False

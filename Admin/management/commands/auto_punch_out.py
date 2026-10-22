from datetime import datetime, time
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from Admin.models import Attendance
from Admin.attendance_autoclose import close_stale_sessions, session_cutoff


class Command(BaseCommand):
    help = "Automatically close stale attendance sessions at midnight and optionally fix over-midnight closed sessions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List sessions that would be closed or fixed without making changes.",
        )
        parser.add_argument(
            "--fix-closed",
            action="store_true",
            help="Also find already-closed sessions where log_out_time > session_cutoff(login_time) and cap them.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        fix_closed = options.get("fix_closed", False)

        tz = timezone.get_current_timezone()
        today_local = timezone.localdate()
        today_midnight = timezone.make_aware(datetime.combine(today_local, time.min), tz)

        if dry_run:
            stale_qs = Attendance.objects.filter(
                log_out_time__isnull=True,
                login_time__lt=today_midnight,
            ).order_by("login_time")

            count_open = 0
            for att in stale_qs:
                count_open += 1
                new_logout = session_cutoff(att.login_time)
                hours = (new_logout - att.login_time).total_seconds() / 3600
                self.stdout.write(
                    f"Employee: {att.employee} | Login: {att.login_time} | New Logout: {new_logout} | Hours: {hours:.2f}"
                )

            self.stdout.write(f"[Dry Run] Would close {count_open} open session(s).")
        else:
            closed_count = close_stale_sessions()
            self.stdout.write(f"Closed {int(closed_count)} session(s)")

        if fix_closed:
            closed_qs = Attendance.objects.filter(
                log_out_time__isnull=False,
                login_time__isnull=False,
            ).order_by("login_time")

            to_fix = []
            for att in closed_qs:
                cutoff = session_cutoff(att.login_time)
                if att.log_out_time > cutoff:
                    to_fix.append((att, cutoff))

            for att, cutoff in to_fix:
                before_hours = att.total_hours_of_work
                after_hours = Decimal(str(round((cutoff - att.login_time).total_seconds() / 3600, 2)))
                self.stdout.write(
                    f"Employee: {att.employee} | Login: {att.login_time} | Logout: {att.log_out_time} -> {cutoff} | Hours: {before_hours} -> {after_hours}"
                )
                if not dry_run:
                    att.log_out_time = cutoff
                    att.auto_punched_out = True
                    att.save()

            prefix = "[Dry Run] Would fix" if dry_run else "Fixed"
            self.stdout.write(f"{prefix} {len(to_fix)} already-closed session(s).")

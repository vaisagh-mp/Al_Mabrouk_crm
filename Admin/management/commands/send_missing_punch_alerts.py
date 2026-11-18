import datetime
from collections import defaultdict
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from Admin.attendance_register import build_attendance_register
from Admin.models import AttendanceCorrection, Employee, MissingPunchAlert, Notification
from Admin.notifications import get_user_role_link


class Command(BaseCommand):
    help = (
        "Send automatic missing punch notifications and reminders to employees and approvers, "
        "and resolve alerts when missing punches are corrected or excused."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='Target date to check (YYYY-MM-DD). Default: yesterday (local Dubai date).'
        )
        parser.add_argument(
            '--lookback',
            type=int,
            default=7,
            help='Number of days to look back for missing punches and reminders (default: 7).'
        )
        parser.add_argument(
            '--reminder-days',
            type=int,
            default=2,
            dest='reminder_days',
            help='Days after last alert to send reminder (default: 2).'
        )
        parser.add_argument(
            '--max-reminders',
            type=int,
            default=2,
            dest='max_reminders',
            help='Maximum number of reminders to send per alert (default: 2).'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print what would be sent without modifying database.'
        )

    def handle(self, *args, **options):
        if not getattr(settings, 'HR_ATTENDANCE_ENGINE_V2', False):
            self.stdout.write("HR_ATTENDANCE_ENGINE_V2 is off; doing nothing.")
            return

        date_str = options.get('date')
        lookback = options.get('lookback') or 7
        reminder_days = options.get('reminder_days') or 2
        max_reminders = options.get('max_reminders') or 2
        dry_run = options.get('dry_run', False)

        today = timezone.localdate()
        if date_str:
            target_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        else:
            target_date = today - datetime.timedelta(days=1)

        start_date = target_date - datetime.timedelta(days=lookback - 1)
        end_date = target_date

        self.stdout.write(
            f"Running send_missing_punch_alerts for target_date={target_date} "
            f"(window: {start_date} to {end_date}, lookback={lookback}d, dry_run={dry_run})..."
        )

        # 1. Build register once across the lookback window
        rows = build_attendance_register(start_date, end_date)

        # 2. Bulk load active employees with reporting manager and user
        emp_map = {
            e.id: e
            for e in Employee.objects.select_related('user', 'reporting_manager__user').filter(user__is_active=True)
        }

        # 3. Load existing alerts in the lookback window
        alerts_qs = list(MissingPunchAlert.objects.filter(date__gte=start_date, date__lte=end_date))
        existing_alerts = {(a.employee_id, a.date): a for a in alerts_qs}

        # 4. Load pending corrections in the lookback window
        pending_corrections_set = set(
            AttendanceCorrection.objects.filter(
                date__gte=start_date, date__lte=end_date, status='PENDING'
            ).values_list('employee_id', 'date')
        )

        # 5. Load HR users for employees without reporting manager
        hr_users = list(User.objects.filter(
            Q(employee_profile__is_hr=True) | Q(is_superuser=True),
            is_active=True
        ).distinct())

        now = timezone.now()

        # Trackers
        first_alerts_count = 0
        reminders_count = 0
        skip_counts = {
            'inactive': 0,
            'deactivated': 0,
            'before_join': 0,
            'future': 0,
            'pending_correction': 0,
        }

        notifications_to_create = []
        alerts_to_create = []
        alerts_to_update = []
        alerts_to_resolve = []

        # Approver queue: key = approver User (or None for HR), value = list of (Employee, Date)
        approver_queue = defaultdict(list)

        # Employee messages are grouped: ONE first-alert and ONE reminder notification per employee per run,
        # listing every date, so a first run over the lookback window never floods anyone.
        emp_first_items = defaultdict(list)     # emp_id -> [(date, punch_type, time_str)]
        emp_reminder_dates = defaultdict(list)  # emp_id -> [date]

        # Map register status by (emp_id, date) for resolution check
        row_status_map = {}

        for row in rows:
            emp_obj = row.get('employee')
            d = row.get('date')
            if not emp_obj or not d:
                continue

            status_code = row.get('status_code')
            row_status_map[(emp_obj.id, d)] = status_code

            if status_code != 'MISSING_PUNCH':
                continue

            emp = emp_map.get(emp_obj.id)
            if not emp:
                skip_counts['inactive'] += 1
                continue

            if not emp.user or not emp.user.is_active:
                skip_counts['inactive'] += 1
                continue

            if emp.deactivated_at and emp.deactivated_at.date() <= d:
                skip_counts['deactivated'] += 1
                continue

            if emp.date_of_join and d < emp.date_of_join:
                skip_counts['before_join'] += 1
                continue

            if d > today:
                skip_counts['future'] += 1
                continue

            if (emp.id, d) in pending_corrections_set:
                skip_counts['pending_correction'] += 1
                continue

            alert = existing_alerts.get((emp.id, d))
            if not alert:
                # FIRST alert
                punch_type = row.get('missing_punch_type') or 'NO_PUNCH'
                time_str = ''
                if punch_type == 'INCOMPLETE':
                    # Only show a punch-in time we actually have; never invent one.
                    if row.get('start'):
                        time_str = row['start'].strftime('%H:%M')
                    elif row.get('login_time'):
                        time_str = timezone.localtime(row['login_time']).strftime('%H:%M')
                emp_first_items[emp.id].append((d, punch_type, time_str))

                new_alert = MissingPunchAlert(
                    employee=emp,
                    date=d,
                    punch_type=punch_type,
                    first_alert_at=now,
                    last_alert_at=now,
                    reminder_count=0,
                )
                alerts_to_create.append(new_alert)
                existing_alerts[(emp.id, d)] = new_alert
                first_alerts_count += 1

                approver_user = emp.reporting_manager.user if (emp.reporting_manager and emp.reporting_manager.user and emp.reporting_manager.user.is_active) else None
                approver_queue[approver_user].append((emp, d))

            else:
                # Existing alert: check reminder
                if alert.resolved_at is None:
                    time_diff = now - alert.last_alert_at
                    if time_diff >= datetime.timedelta(days=reminder_days) and alert.reminder_count < max_reminders:
                        emp_reminder_dates[emp.id].append(d)
                        alert.reminder_count += 1
                        alert.last_alert_at = now
                        alerts_to_update.append(alert)
                        reminders_count += 1

                        if alert.reminder_count == max_reminders:
                            approver_user = emp.reporting_manager.user if (emp.reporting_manager and emp.reporting_manager.user and emp.reporting_manager.user.is_active) else None
                            approver_queue[approver_user].append((emp, d))

        # c) Resolution check for existing unresolved alerts
        for alert in alerts_qs:
            if alert.resolved_at is None:
                cur_status = row_status_map.get((alert.employee_id, alert.date))
                if cur_status and cur_status != 'MISSING_PUNCH':
                    alert.resolved_at = now
                    alerts_to_resolve.append(alert)

        # Grouped employee notifications (one first-alert + one reminder message per employee per run)
        def _correct_link(emp_obj, day):
            return get_user_role_link(emp_obj.user, 'correction_my_date', query=f"correct={day.strftime('%Y-%m-%d')}")

        for emp_id, items in emp_first_items.items():
            emp = emp_map[emp_id]
            items = sorted(items)
            if len(items) == 1:
                d, punch_type, time_str = items[0]
                if punch_type == 'INCOMPLETE':
                    at = f" at {time_str}" if time_str else ""
                    msg = f"You punched in{at} on {d.strftime('%d/%m/%Y')} but did not punch out. Please request a correction."
                else:
                    msg = f"No attendance recorded for {d.strftime('%a %d/%m/%Y')} (scheduled working day). Please request a correction or apply for leave."
            else:
                days_txt = ', '.join(
                    f"{d.strftime('%a %d/%m')}{' (no punch-out)' if pt == 'INCOMPLETE' else ''}" for d, pt, _ in items
                )
                msg = (f"Missing attendance on {len(items)} days: {days_txt}. "
                       f"Please request a correction or apply for leave for each day.")
            notifications_to_create.append(Notification(
                recipient=emp.user, message=msg, type='MISSING_PUNCH',
                link=_correct_link(emp, items[0][0]), related_kind='MissingPunchAlert',
            ))

        for emp_id, days in emp_reminder_dates.items():
            emp = emp_map[emp_id]
            days = sorted(days)
            if len(days) == 1:
                msg = f"Reminder: missing punch on {days[0].strftime('%d/%m/%Y')} is still not corrected."
            else:
                msg = (f"Reminder: missing punches on {', '.join(x.strftime('%d/%m/%Y') for x in days)} "
                       f"are still not corrected.")
            notifications_to_create.append(Notification(
                recipient=emp.user, message=msg, type='MISSING_PUNCH',
                link=_correct_link(emp, days[0]), related_kind='MissingPunchAlert',
            ))

        # d) Combined approver notifications
        approver_notifs_count = 0
        for approver_user, items in approver_queue.items():
            emp_names = []
            seen_ids = set()
            dates_set = set()
            for (e, dt) in items:
                dates_set.add(dt)
                if e.id not in seen_ids:
                    seen_ids.add(e.id)
                    name = e.name or e.user.get_full_name() or e.user.username
                    emp_names.append(name)

            count = len(emp_names)
            dates_sorted = sorted(dates_set)
            if len(dates_sorted) == 1:
                date_str = dates_sorted[0].strftime('%d/%m/%Y')
            else:
                date_str = ', '.join(d.strftime('%d/%m/%Y') for d in dates_sorted)

            if count == 1:
                approver_msg = f"1 team member has a missing punch for {date_str}: {emp_names[0]}."
            else:
                approver_msg = f"{count} team members have missing punches for {date_str}: {', '.join(emp_names)}."

            primary_date_iso = dates_sorted[0].strftime('%Y-%m-%d')
            if approver_user:
                recipients = [approver_user]
                link = get_user_role_link(approver_user, 'missing_punch_approver', query=f"start={primary_date_iso}&end={primary_date_iso}&status=MISSING_PUNCH")
            else:
                recipients = hr_users
                link = f"/hr/employee/attendance/?start={primary_date_iso}&end={primary_date_iso}&status=MISSING_PUNCH"

            for rec in recipients:
                notifications_to_create.append(
                    Notification(
                        recipient=rec,
                        message=approver_msg,
                        type='MISSING_PUNCH',
                        link=link,
                        related_kind='MissingPunchAlert',
                    )
                )
            approver_notifs_count += 1

        # Execution or Dry Run
        if dry_run:
            self.stdout.write(f"[DRY-RUN] Would create {len(alerts_to_create)} alert(s).")
            self.stdout.write(f"[DRY-RUN] Would send {len(notifications_to_create)} notification(s).")
            self.stdout.write(f"[DRY-RUN] Would update {len(alerts_to_update)} alert reminder(s).")
            self.stdout.write(f"[DRY-RUN] Would resolve {len(alerts_to_resolve)} alert(s).")
        else:
            with transaction.atomic():
                if alerts_to_create:
                    MissingPunchAlert.objects.bulk_create(alerts_to_create)
                if notifications_to_create:
                    Notification.objects.bulk_create(notifications_to_create)
                if alerts_to_update:
                    MissingPunchAlert.objects.bulk_update(alerts_to_update, ['last_alert_at', 'reminder_count'])
                if alerts_to_resolve:
                    MissingPunchAlert.objects.bulk_update(alerts_to_resolve, ['resolved_at'])

        total_skipped = sum(skip_counts.values())
        self.stdout.write(
            f"Missing punch alerts summary for {target_date} (lookback={lookback}d):\n"
            f"  First alerts sent: {first_alerts_count}\n"
            f"  Reminders sent: {reminders_count}\n"
            f"  Approver notifications: {approver_notifs_count}\n"
            f"  Alerts resolved: {len(alerts_to_resolve)}\n"
            f"  Skipped: {total_skipped} (inactive: {skip_counts['inactive']}, "
            f"deactivated: {skip_counts['deactivated']}, before_join: {skip_counts['before_join']}, "
            f"future: {skip_counts['future']}, pending_correction: {skip_counts['pending_correction']})"
        )

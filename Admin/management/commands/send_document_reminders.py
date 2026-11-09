from datetime import date
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from Admin.models import (
    EmployeeDocument, CompanyDocument,
    DocumentReminderLog, Notification
)
from django.contrib.auth.models import User


class Command(BaseCommand):
    help = "Daily document expiry reminder command. Sends in-app notifications and records reminder logs."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simulate reminder check without creating notifications or log records.',
        )
        parser.add_argument(
            '--as-of',
            type=str,
            help='Override today date for testing (YYYY-MM-DD)',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        as_of_str = options.get('as_of')

        if as_of_str:
            today = timezone.datetime.strptime(as_of_str, '%Y-%m-%d').date()
        else:
            today = timezone.localdate()

        self.stdout.write(f"Running send_document_reminders for date {today} (dry_run={dry_run})...")

        # HR recipients
        hr_users = list(User.objects.filter(
            Q(employee_profile__is_hr=True) | Q(is_superuser=True),
            is_active=True
        ).distinct())

        emp_reminders_sent = 0
        comp_reminders_sent = 0

        # 1. Process Employee Documents
        active_emp_docs = EmployeeDocument.objects.filter(
            is_current=True,
            expiry_date__isnull=False,
            employee__user__is_active=True,  # Skip deactivated employees
            doc_type__has_expiry=True,
        ).select_related('employee__user', 'doc_type')

        for doc in active_emp_docs:
            thresholds = self._parse_thresholds(doc.doc_type.reminder_days)
            days_left = (doc.expiry_date - today).days

            matching_thresholds = [t for t in sorted(thresholds) if days_left <= t]
            if not matching_thresholds:
                continue
            threshold = matching_thresholds[0]

            # Check idempotency
            already_sent = DocumentReminderLog.objects.filter(
                employee_document=doc,
                threshold_days=threshold,
            ).exists()
            if already_sent:
                continue

            recipients = set(hr_users)
            if doc.employee.user and doc.employee.user.is_active:
                recipients.add(doc.employee.user)

            if threshold == 0 or days_left <= 0:
                msg = (
                    f"Document EXPIRED: {doc.doc_type.name} for "
                    f"{doc.employee.user.get_full_name() or doc.employee.user.username} "
                    f"({doc.employee.employee_code or ''}) expired on {doc.expiry_date.strftime('%d/%m/%Y')}."
                )
            else:
                msg = (
                    f"Document Reminder: {doc.doc_type.name} for "
                    f"{doc.employee.user.get_full_name() or doc.employee.user.username} "
                    f"({doc.employee.employee_code or ''}) expires in {days_left} days "
                    f"on {doc.expiry_date.strftime('%d/%m/%Y')}."
                )

            self.stdout.write(
                f"[{'DRY-RUN' if dry_run else 'SEND'}] EMP Doc {doc.id} ({doc.doc_type.name}) "
                f"threshold {threshold}d (days_left={days_left}) -> {len(recipients)} recipients"
            )

            if not dry_run:
                with transaction.atomic():
                    for recipient in recipients:
                        Notification.objects.create(
                            recipient=recipient,
                            message=msg,
                        )
                    DocumentReminderLog.objects.create(
                        document_type_category='EMPLOYEE',
                        employee_document=doc,
                        threshold_days=threshold,
                        recipient_count=len(recipients),
                    )
            emp_reminders_sent += 1

        # 2. Process Company Documents
        active_comp_docs = CompanyDocument.objects.filter(
            is_current=True,
            expiry_date__isnull=False,
            doc_type__has_expiry=True,
        ).select_related('doc_type')

        for doc in active_comp_docs:
            thresholds = self._parse_thresholds(doc.doc_type.reminder_days)
            days_left = (doc.expiry_date - today).days

            matching_thresholds = [t for t in sorted(thresholds) if days_left <= t]
            if not matching_thresholds:
                continue
            threshold = matching_thresholds[0]

            already_sent = DocumentReminderLog.objects.filter(
                company_document=doc,
                threshold_days=threshold,
            ).exists()
            if already_sent:
                continue

            recipients = set(hr_users)
            if threshold == 0 or days_left <= 0:
                msg = (
                    f"Company Document EXPIRED: '{doc.title}' ({doc.doc_type.name}) "
                    f"expired on {doc.expiry_date.strftime('%d/%m/%Y')}."
                )
            else:
                msg = (
                    f"Company Document Reminder: '{doc.title}' ({doc.doc_type.name}) "
                    f"expires in {days_left} days on {doc.expiry_date.strftime('%d/%m/%Y')}."
                )

            self.stdout.write(
                f"[{'DRY-RUN' if dry_run else 'SEND'}] COMP Doc {doc.id} ('{doc.title}') "
                f"threshold {threshold}d (days_left={days_left}) -> {len(recipients)} recipients"
            )

            if not dry_run:
                with transaction.atomic():
                    for recipient in recipients:
                        Notification.objects.create(
                            recipient=recipient,
                            message=msg,
                        )
                    DocumentReminderLog.objects.create(
                        document_type_category='COMPANY',
                        company_document=doc,
                        threshold_days=threshold,
                        recipient_count=len(recipients),
                    )
            comp_reminders_sent += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Finished reminders. Total processed: {emp_reminders_sent} employee, "
                f"{comp_reminders_sent} company (dry_run={dry_run})."
            )
        )

    def _parse_thresholds(self, reminder_days_str):
        thresholds = {0}
        if reminder_days_str:
            for part in str(reminder_days_str).split(','):
                part = part.strip()
                if part.isdigit():
                    thresholds.add(int(part))
        return sorted(thresholds, reverse=True)

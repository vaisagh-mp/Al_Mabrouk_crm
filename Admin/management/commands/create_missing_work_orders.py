from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.contrib.auth.models import User
from Admin.models import Project, WorkOrder
from Admin.work_order_sync import sync_work_order_from_project


class Command(BaseCommand):
    help = "Create work orders for existing projects that have none, and report differences for existing ones."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help="Print what would be created without committing changes."
        )
        parser.add_argument(
            '--include-closed',
            action='store_true',
            default=False,
            help="Include COMPLETED and CANCELLED projects (default skips them)."
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        include_closed = options['include_closed']

        # 1. Report (do not change) existing work orders whose client/vessel differ from their project
        differing = []
        for wo in WorkOrder.objects.filter(project__isnull=False).select_related('project'):
            proj = wo.project
            expected_vessel = proj.vessel_name or "N/A"
            expected_client = proj.client_name or ""
            diffs = []
            if (wo.vessel or "") != expected_vessel:
                diffs.append(f"vessel: '{wo.vessel}' (WO) vs '{expected_vessel}' (Project)")
            if (wo.client or "") != expected_client:
                diffs.append(f"client: '{wo.client}' (WO) vs '{expected_client}' (Project)")
            if diffs:
                differing.append((wo, proj, diffs))

        self.stdout.write("=== Audit: Existing Work Orders vs Projects ===")
        if differing:
            self.stdout.write(self.style.WARNING(
                f"Found {len(differing)} work order(s) whose client/vessel differ from their project:"
            ))
            for wo, proj, diffs in differing:
                self.stdout.write(
                    f"  - WorkOrder {wo.work_order_number} (Project #{proj.id} '{proj.name}'): {'; '.join(diffs)}"
                )
        else:
            self.stdout.write(self.style.SUCCESS("All existing work orders match their project's vessel and client."))

        # 2. Find projects without work orders
        projects_qs = Project.objects.filter(workorder__isnull=True).order_by('id')
        if not include_closed:
            projects_qs = projects_qs.exclude(status__in=['COMPLETED', 'CANCELLED'])

        count = projects_qs.count()
        self.stdout.write(f"\n=== Missing Work Orders ===")
        self.stdout.write(f"Projects without work orders to process: {count}")

        if count == 0:
            self.stdout.write(self.style.SUCCESS("No missing work orders found."))
            return

        superuser = User.objects.filter(is_superuser=True).first()

        created_count = 0
        with transaction.atomic():
            for proj in projects_qs:
                assigned_date = proj.created_at.date() if proj.created_at else timezone.now().date()
                if proj.manager and proj.manager.user:
                    created_by = proj.manager.user
                else:
                    created_by = superuser

                if dry_run:
                    self.stdout.write(
                        f"[Dry Run] Would create WorkOrder for Project #{proj.id} ('{proj.name}') "
                        f"[Status: {proj.status}, Assigned Date: {assigned_date}, Created By: {created_by}]"
                    )
                    created_count += 1
                else:
                    wo, created = sync_work_order_from_project(
                        project=proj,
                        wo_fields={'assigned_date': assigned_date},
                        by_user=created_by
                    )
                    if created:
                        created_count += 1
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"Created WorkOrder {wo.work_order_number} for Project #{proj.id} ('{proj.name}')"
                            )
                        )

            if dry_run:
                transaction.set_rollback(True)
                self.stdout.write(
                    self.style.NOTICE(f"[Dry Run] Finished. Would have created {created_count} work order(s). No changes committed.")
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(f"Successfully created {created_count} work order(s).")
                )


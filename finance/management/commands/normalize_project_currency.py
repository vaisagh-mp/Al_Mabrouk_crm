from collections import defaultdict
from django.core.management.base import BaseCommand
from django.db import transaction
from Admin.models import Project

CURRENCY_NORMALIZE_MAP = {
    # AED
    "aed": "AED",
    "dhs": "AED",
    "dh": "AED",
    "dirham": "AED",
    "dirhams": "AED",
    "uae dirham": "AED",
    "": "AED",
    # USD
    "usd": "USD",
    "us$": "USD",
    "$": "USD",
    "dollar": "USD",
    "us dollar": "USD",
    # INR
    "inr": "INR",
    "rs": "INR",
    "rupee": "INR",
    "rupees": "INR",
    "₹": "INR",
    # EUR
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "€": "EUR",
}


class Command(BaseCommand):
    help = "Normalize Project.currency_code values to supported standard codes (AED, USD, INR, EUR)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run the normalization without committing changes to the database.',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)

        projects = Project.objects.all().order_by('id')
        changes_count = defaultdict(int)
        already_normalized_count = defaultdict(int)
        unknown_projects = []
        projects_to_update = []

        with transaction.atomic():
            for project in projects:
                raw_val = project.currency_code
                cleaned_key = (raw_val or "").strip().lower()

                if cleaned_key in CURRENCY_NORMALIZE_MAP:
                    target_code = CURRENCY_NORMALIZE_MAP[cleaned_key]
                    display_old = repr(raw_val) if raw_val is not None else "None"
                    if raw_val != target_code:
                        changes_count[(display_old, target_code)] += 1
                        project.currency_code = target_code
                        projects_to_update.append(project)
                    else:
                        already_normalized_count[(display_old, target_code)] += 1
                else:
                    unknown_projects.append((project.id, project.name, repr(raw_val)))

            if not dry_run and projects_to_update:
                Project.objects.bulk_update(projects_to_update, ['currency_code'])

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write("\n" + "=" * 65)
        self.stdout.write("Project Currency Normalization Summary")
        self.stdout.write("=" * 65)

        if changes_count:
            self.stdout.write(f"{'Old Value':<24} -> {'New Value':<12} | {'Count':<8}")
            self.stdout.write("-" * 65)
            for (old_val, new_val), cnt in sorted(changes_count.items()):
                self.stdout.write(f"{old_val:<24} -> {new_val:<12} | {cnt:<8}")
        else:
            self.stdout.write("No projects needed currency normalization.")

        if already_normalized_count:
            self.stdout.write("-" * 65)
            self.stdout.write("Already normalized:")
            for (old_val, new_val), cnt in sorted(already_normalized_count.items()):
                self.stdout.write(f"  {old_val} ({cnt} project{'s' if cnt != 1 else ''})")

        self.stdout.write("=" * 65)

        if unknown_projects:
            self.stdout.write(self.style.WARNING("\nNeeds Manual Fix (Unknown currency codes):"))
            for pid, pname, raw_val in unknown_projects:
                self.stdout.write(self.style.WARNING(f"  - Project #{pid} ('{pname}'): current value {raw_val}"))
        else:
            self.stdout.write(self.style.SUCCESS("No unknown currency codes found."))

        if dry_run:
            self.stdout.write(self.style.NOTICE(f"\n[Dry Run] Changes not saved. ({len(projects_to_update)} project(s) would be updated)"))
        else:
            self.stdout.write(self.style.SUCCESS(f"\nSuccessfully updated {len(projects_to_update)} project(s)."))


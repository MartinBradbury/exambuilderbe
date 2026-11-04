from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from accounts.models import CustomUser
from accounts.services.entitlements import (
    ensure_subject_entitlement_rows,
    missing_legacy_subject_codes,
)


class Command(BaseCommand):
    help = (
        'Create missing gcse_science / alevel_biology SubjectEntitlement rows for users '
        'holding the legacy paid flags or lifetime access. Never grants Chemistry or Physics. '
        'Idempotent.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report changes without writing.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        users = CustomUser.objects.filter(
            Q(has_gcse_paid_access=True)
            | Q(has_alevel_paid_access=True)
            | Q(entitlement__lifetime_unlocked=True)
        ).distinct()

        users_affected = 0
        rows_needed = 0
        for user in users.iterator():
            missing = missing_legacy_subject_codes(user)
            if not missing:
                continue
            users_affected += 1
            rows_needed += len(missing)
            if not dry_run:
                with transaction.atomic():
                    ensure_subject_entitlement_rows(user, missing)

        verb = 'would create' if dry_run else 'created'
        prefix = '[dry-run] ' if dry_run else ''
        self.stdout.write(
            f'{prefix}{verb} {rows_needed} subject entitlement row(s) for {users_affected} user(s).'
        )

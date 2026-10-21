from datetime import datetime, timezone

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


MIGRATE_FROM = ('accounts', '0008_seed_initial_subjects')
MIGRATE_TO = ('accounts', '0009_backfill_subject_entitlements')


class SubjectEntitlementBackfillMigrationTests(TransactionTestCase):
    migrate_from = MIGRATE_FROM
    migrate_to = MIGRATE_TO

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])
        self.apps = self.executor.loader.project_state([self.migrate_from]).apps
        self._create_historical_data()

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _create_user(self, email, gcse_access=False, alevel_access=False):
        CustomUser = self.apps.get_model('accounts', 'CustomUser')
        UserEntitlement = self.apps.get_model('accounts', 'UserEntitlement')
        user = CustomUser.objects.create(
            username=email.split('@')[0],
            email=email,
            password='!',
            has_gcse_paid_access=gcse_access,
            has_alevel_paid_access=alevel_access,
        )
        entitlement = UserEntitlement.objects.create(
            user_id=user.pk,
            plan_type='free',
            lifetime_unlocked=False,
            stripe_customer_id=f'cus_{user.pk}',
            stripe_checkout_session_id=f'cs_{user.pk}',
            stripe_subscription_id=f'sub_{user.pk}',
            paid_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        return user, entitlement

    def _create_historical_data(self):
        Subject = self.apps.get_model('accounts', 'Subject')
        SubjectEntitlement = self.apps.get_model('accounts', 'SubjectEntitlement')

        for code, name, qualification in (
            ('gcse_science', 'Science', 'GCSE'),
            ('alevel_biology', 'Biology', 'A_LEVEL'),
            ('alevel_chemistry', 'Chemistry', 'A_LEVEL'),
            ('alevel_physics', 'Physics', 'A_LEVEL'),
        ):
            Subject.objects.get_or_create(
                code=code,
                defaults={
                    'name': name,
                    'qualification': qualification,
                    'is_active': True,
                },
            )

        self.users = {}
        self.entitlements = {}
        self.users['gcse_only'], self.entitlements['gcse_only'] = self._create_user(
            'gcse-only@example.com',
            gcse_access=True,
        )
        self.users['biology_only'], self.entitlements['biology_only'] = self._create_user(
            'biology-only@example.com',
            alevel_access=True,
        )
        self.users['both'], self.entitlements['both'] = self._create_user(
            'both@example.com',
            gcse_access=True,
            alevel_access=True,
        )
        self.users['lifetime_flag'], self.entitlements['lifetime_flag'] = self._create_user(
            'lifetime-flag@example.com',
        )
        self.entitlements['lifetime_flag'].lifetime_unlocked = True
        self.entitlements['lifetime_flag'].save(update_fields=['lifetime_unlocked'])

        self.users['lifetime_plan'], self.entitlements['lifetime_plan'] = self._create_user(
            'lifetime-plan@example.com',
        )
        self.entitlements['lifetime_plan'].plan_type = 'lifetime'
        self.entitlements['lifetime_plan'].save(update_fields=['plan_type'])

        self.users['free'], self.entitlements['free'] = self._create_user(
            'free@example.com',
        )
        self.users['paid_no_flags'], self.entitlements['paid_no_flags'] = self._create_user(
            'paid-no-flags@example.com',
        )
        self.entitlements['paid_no_flags'].plan_type = 'paid'
        self.entitlements['paid_no_flags'].save(update_fields=['plan_type'])

        gcse_subject = Subject.objects.get(code='gcse_science')
        existing = SubjectEntitlement.objects.create(
            user_id=self.users['gcse_only'].pk,
            subject_id=gcse_subject.pk,
        )
        self.existing_entitlement = (existing.pk, existing.granted_at)

        self.user_state_before = {
            user.pk: (user.has_gcse_paid_access, user.has_alevel_paid_access)
            for user in self.users.values()
        }
        billing_fields = (
            'plan_type',
            'lifetime_unlocked',
            'stripe_customer_id',
            'stripe_checkout_session_id',
            'stripe_subscription_id',
            'paid_at',
        )
        self.billing_state_before = {
            key: tuple(getattr(entitlement, field) for field in billing_fields)
            for key, entitlement in self.entitlements.items()
        }

    def _apply_backfill(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        apps = executor.loader.project_state([self.migrate_to]).apps
        SubjectEntitlement = apps.get_model('accounts', 'SubjectEntitlement')
        Subject = apps.get_model('accounts', 'Subject')
        access_by_user = {
            user.pk: set(
                SubjectEntitlement.objects.filter(user_id=user.pk)
                .values_list('subject__code', flat=True)
            )
            for user in self.users.values()
        }
        return apps, SubjectEntitlement, Subject, access_by_user

    def test_backfill_maps_existing_access_without_changing_billing_data(self):
        apps, SubjectEntitlement, Subject, access_by_user = self._apply_backfill()

        expected_access = {
            'gcse_only': {'gcse_science'},
            'biology_only': {'alevel_biology'},
            'both': {'gcse_science', 'alevel_biology'},
            'lifetime_flag': {'gcse_science', 'alevel_biology'},
            'lifetime_plan': {'gcse_science', 'alevel_biology'},
            'free': set(),
            'paid_no_flags': set(),
        }
        for key, subjects in expected_access.items():
            self.assertEqual(access_by_user[self.users[key].pk], subjects, key)

        self.assertFalse(
            SubjectEntitlement.objects.filter(
                subject__code__in=('alevel_chemistry', 'alevel_physics')
            ).exists()
        )
        self.assertEqual(
            set(Subject.objects.values_list('code', flat=True)),
            {'gcse_science', 'alevel_biology', 'alevel_chemistry', 'alevel_physics'},
        )

        CustomUser = apps.get_model('accounts', 'CustomUser')
        UserEntitlement = apps.get_model('accounts', 'UserEntitlement')
        self.assertEqual(
            {
                user_id: (gcse_access, alevel_access)
                for user_id, gcse_access, alevel_access in
                CustomUser.objects.values_list(
                    'pk', 'has_gcse_paid_access', 'has_alevel_paid_access'
                )
            },
            {
                user_id: access_flags
                for user_id, access_flags in self.user_state_before.items()
            },
        )
        for key, user in self.users.items():
            entitlement = UserEntitlement.objects.get(user_id=user.pk)
            self.assertEqual(
                tuple(
                    getattr(entitlement, field)
                    for field in (
                        'plan_type',
                        'lifetime_unlocked',
                        'stripe_customer_id',
                        'stripe_checkout_session_id',
                        'stripe_subscription_id',
                        'paid_at',
                    )
                ),
                self.billing_state_before[key],
            )

        existing = SubjectEntitlement.objects.get(
            user_id=self.users['gcse_only'].pk,
            subject__code='gcse_science',
        )
        self.assertEqual((existing.pk, existing.granted_at), self.existing_entitlement)

    def test_reapplying_backfill_does_not_duplicate_or_replace_existing_rows(self):
        _, SubjectEntitlement, _, _ = self._apply_backfill()
        first_run = {
            user.pk: list(
                SubjectEntitlement.objects.filter(user_id=user.pk)
                .order_by('subject__code')
                .values_list('pk', 'subject__code', 'granted_at')
            )
            for user in self.users.values()
        }

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        apps = executor.loader.project_state([self.migrate_to]).apps
        SubjectEntitlement = apps.get_model('accounts', 'SubjectEntitlement')

        second_run = {
            user.pk: list(
                SubjectEntitlement.objects.filter(user_id=user.pk)
                .order_by('subject__code')
                .values_list('pk', 'subject__code', 'granted_at')
            )
            for user in self.users.values()
        }
        self.assertEqual(second_run, first_run)

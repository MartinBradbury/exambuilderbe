from io import StringIO

from django.contrib.admin.sites import site as admin_site
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import CustomUser, Subject, SubjectEntitlement, UserEntitlement
from accounts.services.entitlements import (
    LifetimeAccessProtectedError,
    grant_subject_access,
    revoke_subject_access,
)
from accounts.services.stripe import process_stripe_webhook_event
from examquestions.views import _has_paid_generation_access


def make_user(name):
    return CustomUser.objects.create_user(
        email=f'{name}@example.com', username=name, password='SubjectPass123'
    )


def codes(user):
    return set(user.subject_entitlements.values_list('subject__code', flat=True))


def payment_event(user, qualification, n=1, event_id=None):
    return {
        'id': event_id or f'evt_sub_{n}',
        'type': 'checkout.session.completed',
        'data': {
            'object': {
                'id': f'cs_sub_{n}',
                'mode': 'payment',
                'payment_status': 'paid',
                'created': 1790000000,
                'amount_total': 1700,
                'currency': 'gbp',
                'payment_intent': f'pi_sub_{n}',
                'client_reference_id': str(user.id),
                'metadata': {'qualification': qualification},
            }
        },
    }


class StripePurchaseSubjectEntitlementTests(TestCase):
    def setUp(self):
        self.user = make_user('buyer')

    def test_gcse_only_purchase_grants_gcse_science(self):
        process_stripe_webhook_event(payment_event(self.user, 'GCSE_SCIENCE'))
        self.user.refresh_from_db()
        self.assertEqual(codes(self.user), {'gcse_science'})
        self.assertTrue(self.user.has_entitlement_for_subject('gcse_science'))
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_biology'))

    def test_biology_only_purchase_grants_biology_only(self):
        process_stripe_webhook_event(payment_event(self.user, 'ALEVEL_BIOLOGY'))
        self.user.refresh_from_db()
        self.assertEqual(codes(self.user), {'alevel_biology'})
        for code in ('alevel_chemistry', 'alevel_physics', 'gcse_science'):
            self.assertFalse(self.user.has_entitlement_for_subject(code))

    def test_combined_purchase_grants_gcse_and_biology_not_new_subjects(self):
        process_stripe_webhook_event(payment_event(self.user, 'BOTH'))
        self.user.refresh_from_db()
        self.assertEqual(codes(self.user), {'gcse_science', 'alevel_biology'})
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_chemistry'))
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_physics'))

    def test_repeated_webhook_events_are_idempotent(self):
        event = payment_event(self.user, 'BOTH')
        process_stripe_webhook_event(event)
        process_stripe_webhook_event(event)
        process_stripe_webhook_event(payment_event(self.user, 'BOTH', event_id='evt_other'))
        self.assertEqual(self.user.subject_entitlements.count(), 2)

    def test_lifetime_purchaser_gets_original_subjects_only(self):
        UserEntitlement.objects.filter(user=self.user).update(lifetime_unlocked=True)
        process_stripe_webhook_event(payment_event(self.user, 'GCSE_SCIENCE'))
        self.user.refresh_from_db()
        self.assertEqual(codes(self.user), {'gcse_science', 'alevel_biology'})


class SubjectPermissionTests(TestCase):
    def setUp(self):
        self.user = make_user('perm')

    def test_legacy_flags_grant_original_subjects_only(self):
        self.user.has_gcse_paid_access = True
        self.user.has_alevel_paid_access = True
        self.user.save()
        self.assertTrue(self.user.has_entitlement_for_subject('gcse_science'))
        self.assertTrue(self.user.has_entitlement_for_subject('alevel_biology'))
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_chemistry'))
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_physics'))

    def test_lifetime_without_flags_or_rows_keeps_original_subjects(self):
        UserEntitlement.objects.filter(user=self.user).update(lifetime_unlocked=True)
        self.assertTrue(self.user.has_entitlement_for_subject('gcse_science'))
        self.assertTrue(self.user.has_entitlement_for_subject('alevel_biology'))
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_chemistry'))

    def test_chemistry_and_physics_need_explicit_rows_and_are_independent(self):
        grant_subject_access(self.user, 'alevel_chemistry')
        self.assertTrue(self.user.has_entitlement_for_subject('alevel_chemistry'))
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_physics'))
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_biology'))
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_alevel_paid_access)

    def test_unknown_subject_is_denied(self):
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_history'))

    def test_biology_access_does_not_unlock_new_subjects(self):
        grant_subject_access(self.user, 'alevel_biology')
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_chemistry'))
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_physics'))

    def test_existing_generation_permissions_unchanged(self):
        self.assertFalse(_has_paid_generation_access(self.user, 'GCSE_SCIENCE'))
        self.assertFalse(_has_paid_generation_access(self.user, 'ALEVEL_BIOLOGY'))
        self.user.has_gcse_paid_access = True
        self.user.save()
        self.assertTrue(_has_paid_generation_access(self.user, 'GCSE_SCIENCE'))
        self.assertFalse(_has_paid_generation_access(self.user, 'ALEVEL_BIOLOGY'))


class SubjectGrantRevokeServiceTests(TestCase):
    def setUp(self):
        self.user = make_user('grants')

    def test_grant_biology_sets_flag_and_row_and_is_idempotent(self):
        self.assertTrue(grant_subject_access(self.user, 'alevel_biology'))
        self.assertFalse(grant_subject_access(self.user, 'alevel_biology'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.has_alevel_paid_access)
        self.assertFalse(self.user.has_gcse_paid_access)
        self.assertEqual(codes(self.user), {'alevel_biology'})
        self.assertEqual(self.user.entitlement.plan_type, UserEntitlement.PlanType.PAID)
        self.assertTrue(_has_paid_generation_access(self.user, 'ALEVEL_BIOLOGY'))

    def test_revoke_removes_row_and_flag_so_no_effective_access_remains(self):
        grant_subject_access(self.user, 'alevel_biology')
        revoke_subject_access(self.user, 'alevel_biology')
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_biology'))
        self.assertFalse(self.user.has_alevel_paid_access)
        self.assertEqual(self.user.entitlement.plan_type, UserEntitlement.PlanType.FREE)

    def test_revoke_flag_only_legacy_access(self):
        self.user.has_gcse_paid_access = True
        self.user.save()
        revoke_subject_access(self.user, 'gcse_science')
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_entitlement_for_subject('gcse_science'))

    def test_revoke_leaves_other_subjects(self):
        grant_subject_access(self.user, 'gcse_science')
        grant_subject_access(self.user, 'alevel_biology')
        revoke_subject_access(self.user, 'gcse_science')
        self.user.refresh_from_db()
        self.assertTrue(self.user.has_entitlement_for_subject('alevel_biology'))
        self.assertTrue(self.user.has_alevel_paid_access)
        self.assertEqual(self.user.entitlement.plan_type, UserEntitlement.PlanType.PAID)

    def test_revoke_chemistry_leaves_biology_flag(self):
        grant_subject_access(self.user, 'alevel_biology')
        grant_subject_access(self.user, 'alevel_chemistry')
        revoke_subject_access(self.user, 'alevel_chemistry')
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_chemistry'))
        self.assertTrue(self.user.has_entitlement_for_subject('alevel_biology'))

    def test_revoke_refused_for_lifetime_legacy_subjects(self):
        UserEntitlement.objects.filter(user=self.user).update(lifetime_unlocked=True)
        grant_subject_access(self.user, 'alevel_biology')
        with self.assertRaises(LifetimeAccessProtectedError):
            revoke_subject_access(self.user, 'alevel_biology')
        self.assertTrue(self.user.has_entitlement_for_subject('alevel_biology'))
        self.assertEqual(codes(self.user), {'alevel_biology'})

    def test_lifetime_user_can_have_chemistry_revoked(self):
        UserEntitlement.objects.filter(user=self.user).update(lifetime_unlocked=True)
        grant_subject_access(self.user, 'alevel_chemistry')
        revoke_subject_access(self.user, 'alevel_chemistry')
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_chemistry'))
        self.assertTrue(self.user.has_entitlement_for_subject('alevel_biology'))

    def test_invalid_subject_code_rejected(self):
        with self.assertRaises(ValueError):
            grant_subject_access(self.user, 'alevel_history')


class SubjectEntitlementAdminTests(TestCase):
    def setUp(self):
        self.admin_user = CustomUser.objects.create_superuser(
            email='admin@example.com', username='admin', password='AdminPass123'
        )
        self.client.force_login(self.admin_user)
        self.user = make_user('managed')
        self.url = reverse('admin:accounts_customuser_changelist')

    def _action(self, action):
        return self.client.post(
            self.url,
            {'action': action, '_selected_action': [self.user.pk]},
            follow=True,
        )

    def test_grant_action_biology_does_not_grant_chemistry_or_physics(self):
        self._action('grant_alevel_biology')
        self.user.refresh_from_db()
        self.assertTrue(self.user.has_entitlement_for_subject('alevel_biology'))
        self.assertTrue(self.user.has_alevel_paid_access)
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_chemistry'))
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_physics'))

    def test_grant_and_revoke_chemistry_and_physics_actions(self):
        self._action('grant_alevel_chemistry')
        self._action('grant_alevel_physics')
        self.assertEqual(codes(self.user), {'alevel_chemistry', 'alevel_physics'})
        self._action('revoke_alevel_chemistry')
        self.assertEqual(codes(self.user), {'alevel_physics'})

    def test_revoke_action_clears_flag_and_row(self):
        grant_subject_access(self.user, 'gcse_science')
        self._action('revoke_gcse_science')
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_gcse_paid_access)
        self.assertFalse(self.user.has_entitlement_for_subject('gcse_science'))

    def test_revoke_action_skips_lifetime_user(self):
        UserEntitlement.objects.filter(user=self.user).update(lifetime_unlocked=True)
        grant_subject_access(self.user, 'alevel_biology')
        response = self._action('revoke_alevel_biology')
        self.assertContains(response, 'lifetime access protects')
        self.assertTrue(self.user.has_entitlement_for_subject('alevel_biology'))

    def test_deleting_subject_entitlement_in_admin_removes_effective_access(self):
        grant_subject_access(self.user, 'alevel_biology')
        row = SubjectEntitlement.objects.get(user=self.user)
        model_admin = admin_site._registry[SubjectEntitlement]
        model_admin.delete_model(self._request(), row)
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_biology'))

    def test_adding_subject_entitlement_in_admin_syncs_flag(self):
        model_admin = admin_site._registry[SubjectEntitlement]
        obj = SubjectEntitlement(user=self.user, subject=Subject.objects.get(code='alevel_biology'))
        model_admin.save_model(self._request(), obj, None, False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.has_alevel_paid_access)

    def _request(self):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        request = RequestFactory().post('/')
        request.user = self.admin_user
        request.session = {}
        request._messages = FallbackStorage(request)
        return request


class BackfillSubjectEntitlementsCommandTests(TestCase):
    def setUp(self):
        self.gcse = make_user('gcse')
        self.gcse.has_gcse_paid_access = True
        self.gcse.save()
        self.both = make_user('both')
        self.both.has_gcse_paid_access = True
        self.both.has_alevel_paid_access = True
        self.both.save()
        self.lifetime = make_user('lifetime')
        UserEntitlement.objects.filter(user=self.lifetime).update(lifetime_unlocked=True)
        self.free = make_user('free')

    def _run(self, *args):
        out = StringIO()
        call_command('backfill_subject_entitlements', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        output = self._run('--dry-run')
        self.assertIn('[dry-run] would create 5 subject entitlement row(s) for 3 user(s)', output)
        self.assertEqual(SubjectEntitlement.objects.count(), 0)

    def test_backfill_creates_original_subjects_only_and_is_idempotent(self):
        self._run()
        self.assertEqual(codes(self.gcse), {'gcse_science'})
        self.assertEqual(codes(self.both), {'gcse_science', 'alevel_biology'})
        self.assertEqual(codes(self.lifetime), {'gcse_science', 'alevel_biology'})
        self.assertEqual(codes(self.free), set())
        self.assertFalse(
            SubjectEntitlement.objects.filter(
                subject__code__in=('alevel_chemistry', 'alevel_physics')
            ).exists()
        )
        total = SubjectEntitlement.objects.count()
        output = self._run()
        self.assertIn('created 0 subject entitlement row(s) for 0 user(s)', output)
        self.assertEqual(SubjectEntitlement.objects.count(), total)

    def test_backfill_does_not_change_legacy_flags(self):
        self._run()
        self.gcse.refresh_from_db()
        self.assertFalse(self.gcse.has_alevel_paid_access)

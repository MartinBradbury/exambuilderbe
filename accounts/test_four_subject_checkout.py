from types import SimpleNamespace
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import CustomUser, Purchase, SubjectEntitlement, UserEntitlement
from accounts.services.entitlements import grant_subject_access
from accounts.services.stripe import (
    CheckoutNotAllowedError,
    create_stripe_checkout_session,
    process_stripe_webhook_event,
)

STRIPE_SETTINGS = dict(
    STRIPE_SECRET_KEY='sk_test_123',
    STRIPE_PUBLISHABLE_KEY='pk_test_123',
    STRIPE_PRICE_ID='price_generic',
    STRIPE_PRICE_ID_GCSE='price_gcse',
    STRIPE_PRICE_ID_ALEVEL='price_bio',
    STRIPE_PRICE_ID_ALEVEL_CHEMISTRY='price_chem',
    STRIPE_PRICE_ID_ALEVEL_PHYSICS='price_phys',
    STRIPE_PRICE_ID_BOTH='price_both',
    STRIPE_WEBHOOK_SECRET='whsec_123',
    STRIPE_CHECKOUT_MODE='payment',
)

PRODUCTS = {
    'GCSE_SCIENCE': ('gcse_science', 'price_gcse'),
    'ALEVEL_BIOLOGY': ('alevel_biology', 'price_bio'),
    'ALEVEL_CHEMISTRY': ('alevel_chemistry', 'price_chem'),
    'ALEVEL_PHYSICS': ('alevel_physics', 'price_phys'),
}


def event(user, qualification, n=1, event_id=None):
    return {
        'id': event_id or f'evt_four_{n}',
        'type': 'checkout.session.completed',
        'data': {
            'object': {
                'id': f'cs_four_{n}',
                'mode': 'payment',
                'payment_status': 'paid',
                'created': 1790000000,
                'amount_total': 299,
                'currency': 'gbp',
                'payment_intent': f'pi_four_{n}',
                'client_reference_id': str(user.id),
                'metadata': {'qualification': qualification},
            }
        },
    }


def owned_codes(user):
    return set(user.subject_entitlements.values_list('subject__code', flat=True))


@override_settings(**STRIPE_SETTINGS)
class FourSubjectCheckoutTests(APITestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email='four@example.com', username='four', password='FourSubjectPass123'
        )

    @patch('accounts.services.stripe.stripe.checkout.Session.create')
    def test_each_subject_uses_its_own_price_in_payment_mode(self, mock_create):
        mock_create.return_value = SimpleNamespace(id='cs_x', url='https://checkout.stripe.com/x')
        for qualification, (code, price) in PRODUCTS.items():
            create_stripe_checkout_session(self.user, qualification)
            kwargs = mock_create.call_args.kwargs
            self.assertEqual(kwargs['line_items'], [{'price': price, 'quantity': 1}])
            self.assertEqual(kwargs['mode'], 'payment')
            self.assertEqual(kwargs['metadata']['qualification'], qualification)
            self.assertTrue(kwargs['allow_promotion_codes'])
            self.assertEqual(kwargs['client_reference_id'], str(self.user.id))

    @override_settings(STRIPE_CHECKOUT_MODE='subscription')
    @patch('accounts.services.stripe.stripe.checkout.Session.create')
    def test_checkout_is_always_payment_mode(self, mock_create):
        mock_create.return_value = SimpleNamespace(id='cs_x', url='https://checkout.stripe.com/x')
        create_stripe_checkout_session(self.user, 'ALEVEL_CHEMISTRY')
        self.assertEqual(mock_create.call_args.kwargs['mode'], 'payment')

    @patch('accounts.services.stripe.stripe.checkout.Session.create')
    def test_existing_customer_is_reused(self, mock_create):
        mock_create.return_value = SimpleNamespace(id='cs_x', url='https://checkout.stripe.com/x')
        UserEntitlement.objects.filter(user=self.user).update(stripe_customer_id='cus_123')
        create_stripe_checkout_session(self.user, 'ALEVEL_PHYSICS')
        kwargs = mock_create.call_args.kwargs
        self.assertEqual(kwargs['customer'], 'cus_123')
        self.assertNotIn('customer_email', kwargs)

    @patch('accounts.services.stripe.stripe.checkout.Session.create')
    def test_aliases_resolve_to_new_subjects(self, mock_create):
        mock_create.return_value = SimpleNamespace(id='cs_x', url='https://checkout.stripe.com/x')
        create_stripe_checkout_session(self.user, 'a-level chemistry')
        self.assertEqual(mock_create.call_args.kwargs['line_items'][0]['price'], 'price_chem')

    @override_settings(STRIPE_PRICE_ID_ALEVEL_CHEMISTRY='')
    @patch('accounts.services.stripe.stripe.checkout.Session.create')
    def test_missing_chemistry_price_is_rejected_without_generic_fallback(self, mock_create):
        with self.assertRaises(ValueError) as ctx:
            create_stripe_checkout_session(self.user, 'ALEVEL_CHEMISTRY')
        self.assertIn('ALEVEL_CHEMISTRY', str(ctx.exception))
        mock_create.assert_not_called()

    @override_settings(STRIPE_PRICE_ID_ALEVEL_PHYSICS='')
    @patch('accounts.services.stripe.stripe.checkout.Session.create')
    def test_missing_physics_price_returns_503_from_endpoint(self, mock_create):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            reverse('stripe-checkout-session'), {'qualification': 'ALEVEL_PHYSICS'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn('price id is missing', response.data['detail'])
        mock_create.assert_not_called()

    @patch('accounts.services.stripe.stripe.checkout.Session.create')
    def test_duplicate_purchase_blocked_for_every_owned_subject(self, mock_create):
        for qualification, (code, _) in PRODUCTS.items():
            grant_subject_access(self.user, code)
            self.user.refresh_from_db()
            with self.assertRaises(CheckoutNotAllowedError):
                create_stripe_checkout_session(self.user, qualification)
        mock_create.assert_not_called()

    @patch('accounts.services.stripe.stripe.checkout.Session.create')
    def test_owning_biology_does_not_block_chemistry_checkout(self, mock_create):
        mock_create.return_value = SimpleNamespace(id='cs_x', url='https://checkout.stripe.com/x')
        grant_subject_access(self.user, 'alevel_biology')
        self.user.refresh_from_db()
        create_stripe_checkout_session(self.user, 'ALEVEL_CHEMISTRY')
        mock_create.assert_called_once()

    @patch('accounts.services.stripe.stripe.checkout.Session.create')
    def test_new_both_checkout_rejected_in_service_and_endpoint(self, mock_create):
        with self.assertRaises(CheckoutNotAllowedError):
            create_stripe_checkout_session(self.user, 'BOTH')
        self.client.force_authenticate(user=self.user)
        for value in ('BOTH', 'all', 'gcse_and_alevel'):
            response = self.client.post(
                reverse('stripe-checkout-session'), {'qualification': value}, format='json'
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_create.assert_not_called()

    def test_endpoint_accepts_new_subjects(self):
        self.client.force_authenticate(user=self.user)
        with patch('accounts.views.create_stripe_checkout_session') as mock_create:
            mock_create.return_value = SimpleNamespace(id='cs_x', url='https://checkout.stripe.com/x')
            for qualification in PRODUCTS:
                response = self.client.post(
                    reverse('stripe-checkout-session'), {'qualification': qualification}, format='json'
                )
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data['mode'], 'payment')


@override_settings(**STRIPE_SETTINGS)
class FourSubjectWebhookTests(APITestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email='hook@example.com', username='hook', password='FourSubjectPass123'
        )

    def test_each_payment_grants_only_its_subject_with_correct_purchase_identifier(self):
        for n, (qualification, (code, _)) in enumerate(PRODUCTS.items(), start=1):
            user = CustomUser.objects.create_user(
                email=f'u{n}@example.com', username=f'u{n}', password='FourSubjectPass123'
            )
            process_stripe_webhook_event(event(user, qualification, n=n))
            self.assertEqual(owned_codes(user), {code})
            purchase = Purchase.objects.get(user=user)
            self.assertEqual(purchase.product_identifier, code)
            self.assertEqual(purchase.payment_status, Purchase.PaymentStatus.PAID)

    def test_chemistry_and_physics_do_not_set_legacy_flags_or_unlock_biology(self):
        process_stripe_webhook_event(event(self.user, 'ALEVEL_CHEMISTRY', n=1))
        process_stripe_webhook_event(event(self.user, 'ALEVEL_PHYSICS', n=2))
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_alevel_paid_access)
        self.assertFalse(self.user.has_gcse_paid_access)
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_biology'))
        self.assertTrue(self.user.has_entitlement_for_subject('alevel_chemistry'))
        self.assertTrue(self.user.has_entitlement_for_subject('alevel_physics'))
        self.assertEqual(self.user.entitlement.plan_type, UserEntitlement.PlanType.FREE)

    def test_biology_payment_does_not_unlock_chemistry_or_physics(self):
        process_stripe_webhook_event(event(self.user, 'ALEVEL_BIOLOGY'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.has_alevel_paid_access)
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_chemistry'))
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_physics'))

    def test_historical_both_payment_grants_gcse_and_biology_only(self):
        process_stripe_webhook_event(event(self.user, 'BOTH'))
        self.user.refresh_from_db()
        self.assertEqual(owned_codes(self.user), {'gcse_science', 'alevel_biology'})
        self.assertTrue(self.user.has_full_paid_access)
        self.assertEqual(Purchase.objects.get(user=self.user).product_identifier, 'both')

    def test_replayed_and_duplicate_events_are_idempotent(self):
        payload = event(self.user, 'ALEVEL_CHEMISTRY')
        process_stripe_webhook_event(payload)
        process_stripe_webhook_event(payload)
        process_stripe_webhook_event(event(self.user, 'ALEVEL_CHEMISTRY', event_id='evt_new_id'))
        self.assertEqual(Purchase.objects.filter(user=self.user).count(), 1)
        self.assertEqual(SubjectEntitlement.objects.filter(user=self.user).count(), 1)

    def test_lifetime_buyer_of_chemistry_gets_chemistry_and_keeps_original_subjects(self):
        UserEntitlement.objects.filter(user=self.user).update(lifetime_unlocked=True)
        process_stripe_webhook_event(event(self.user, 'ALEVEL_CHEMISTRY'))
        self.assertEqual(
            owned_codes(self.user), {'alevel_chemistry', 'gcse_science', 'alevel_biology'}
        )
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_physics'))

    def test_lifetime_without_purchase_never_gets_new_subjects(self):
        UserEntitlement.objects.filter(user=self.user).update(lifetime_unlocked=True)
        self.assertTrue(self.user.has_entitlement_for_subject('gcse_science'))
        self.assertTrue(self.user.has_entitlement_for_subject('alevel_biology'))
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_chemistry'))
        self.assertFalse(self.user.has_entitlement_for_subject('alevel_physics'))

    def test_new_subjects_are_ignored_for_subscription_mode_sessions(self):
        payload = event(self.user, 'ALEVEL_PHYSICS')
        payload['data']['object']['mode'] = 'subscription'
        payload['data']['object'].update(subscription='sub_1', customer='cus_1')
        with patch('accounts.services.stripe.stripe.Subscription.retrieve') as retrieve:
            retrieve.return_value = {'id': 'sub_1', 'customer': 'cus_1', 'status': 'active'}
            process_stripe_webhook_event(payload)
        self.assertEqual(owned_codes(self.user), set())
        self.assertFalse(Purchase.objects.filter(user=self.user).exists())

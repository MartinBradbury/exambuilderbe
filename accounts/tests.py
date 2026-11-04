import re
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
import stripe
from types import SimpleNamespace
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections
from django.test import override_settings
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import ProtectedError
from .models import CustomUser, Purchase, Subject, SubjectEntitlement, StripeWebhookEvent, UserEntitlement
from .services.stripe import create_stripe_checkout_session, process_stripe_webhook_event


class PurchaseModelTests(APITestCase):
	def setUp(self):
		self.user = CustomUser.objects.create_user(
			email='purchase@example.com',
			username='purchase-user',
			password='PurchasePass123',
			email_verified=False,
		)

	def test_purchase_supports_known_and_future_product_identifiers(self):
		product_identifiers = (
			'gcse_science',
			'alevel_biology',
			'alevel_chemistry',
			'alevel_physics',
			'both',
			'alevel_bundle',
			'future_product',
		)

		for product_identifier in product_identifiers:
			with self.subTest(product_identifier=product_identifier):
				purchase = Purchase.objects.create(
					user=self.user,
					product_identifier=product_identifier,
				)
				self.assertEqual(purchase.product_identifier, product_identifier)

	def test_stripe_identifiers_are_unique_when_present(self):
		Purchase.objects.create(
			user=self.user,
			product_identifier='gcse_science',
			stripe_checkout_session_id='cs_unique',
		)
		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				Purchase.objects.create(
					user=self.user,
					product_identifier='gcse_science',
					stripe_checkout_session_id='cs_unique',
				)

		Purchase.objects.create(
			user=self.user,
			product_identifier='alevel_biology',
			stripe_payment_intent_id='pi_unique',
		)
		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				Purchase.objects.create(
					user=self.user,
					product_identifier='alevel_biology',
					stripe_payment_intent_id='pi_unique',
				)

	def test_null_and_empty_stripe_identifiers_do_not_conflict(self):
		first = Purchase.objects.create(
			user=self.user,
			product_identifier='gcse_science',
		)
		second = Purchase.objects.create(
			user=self.user,
			product_identifier='alevel_biology',
			stripe_checkout_session_id='',
			stripe_payment_intent_id='',
		)
		third = Purchase.objects.create(
			user=self.user,
			product_identifier='both',
		)

		self.assertIsNone(first.stripe_checkout_session_id)
		self.assertIsNone(first.stripe_payment_intent_id)
		self.assertEqual(second.stripe_checkout_session_id, '')
		self.assertEqual(second.stripe_payment_intent_id, '')
		self.assertIsNone(third.stripe_checkout_session_id)
		self.assertIsNone(third.stripe_payment_intent_id)

	def test_purchase_status_and_source_choices_validate(self):
		for payment_status in Purchase.PaymentStatus.values:
			purchase = Purchase(
				user=self.user,
				product_identifier='gcse_science',
				payment_status=payment_status,
			)
			purchase.full_clean()

		for source in Purchase.Source.values:
			purchase = Purchase(
				user=self.user,
				product_identifier='gcse_science',
				source=source,
			)
			purchase.full_clean()

	def test_purchase_allows_incomplete_legacy_snapshot(self):
		purchase = Purchase.objects.create(
			user=None,
			product_identifier='both',
			payment_status=Purchase.PaymentStatus.UNKNOWN,
			source=Purchase.Source.LEGACY_SNAPSHOT,
		)

		self.assertIsNone(purchase.user)
		self.assertIsNone(purchase.amount)
		self.assertIsNone(purchase.currency)
		self.assertIsNone(purchase.purchased_at)

	def test_purchase_user_relationship_uses_set_null(self):
		purchase = Purchase.objects.create(
			user=self.user,
			product_identifier='gcse_science',
		)

		self.user.delete()
		purchase.refresh_from_db()
		self.assertIsNone(purchase.user)

	def test_purchase_string_representation(self):
		purchase = Purchase.objects.create(
			user=self.user,
			product_identifier='gcse_science',
			payment_status=Purchase.PaymentStatus.PAID,
			stripe_checkout_session_id='cs_string',
		)

		self.assertEqual(str(purchase), 'gcse_science | paid | cs_string')


class StripeWebhookEventModelTests(APITestCase):
	def test_stripe_event_id_is_unique(self):
		StripeWebhookEvent.objects.create(
			stripe_event_id='evt_unique',
			event_type='checkout.session.completed',
		)

		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				StripeWebhookEvent.objects.create(
					stripe_event_id='evt_unique',
					event_type='checkout.session.completed',
				)

	def test_processing_status_choices_validate(self):
		for processing_status in StripeWebhookEvent.ProcessingStatus.values:
			event = StripeWebhookEvent(
				stripe_event_id=f'evt_{processing_status}',
				event_type='checkout.session.completed',
				processing_status=processing_status,
			)
			event.full_clean()

	def test_event_defaults_and_operational_metadata(self):
		event = StripeWebhookEvent.objects.create(
			stripe_event_id='evt_operational',
			event_type='checkout.session.completed',
			stripe_object_id='cs_123',
			livemode=False,
			api_version='2026-01-01',
			error_message='',
		)

		self.assertEqual(event.processing_status, StripeWebhookEvent.ProcessingStatus.PENDING)
		self.assertEqual(event.attempt_count, 0)
		self.assertIsNone(event.processed_at)
		self.assertEqual(event.stripe_object_id, 'cs_123')
		self.assertFalse(event.livemode)
		self.assertEqual(event.api_version, '2026-01-01')
		self.assertEqual(str(event), 'checkout.session.completed | evt_operational | pending')

	def test_event_error_message_is_bounded(self):
		event = StripeWebhookEvent(
			stripe_event_id='evt_error',
			event_type='checkout.session.completed',
			error_message='x' * 501,
		)

		with self.assertRaises(ValidationError):
			event.full_clean()


@override_settings(
	EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
	EMAIL_VERIFICATION_URL='http://localhost:3000/verify-email',
	PASSWORD_RESET_URL='http://localhost:3000/reset-password',
)
class PasswordResetFlowTests(APITestCase):
	def setUp(self):
		self.user = CustomUser.objects.create_user(
			email='reset@example.com',
			username='reset-user',
			password='OldPassword123',
		)
		self.request_url = reverse('password-reset-request')
		self.confirm_url = reverse('password-reset-confirm')
		self.login_url = reverse('user-login')

	def test_password_reset_request_sends_email(self):
		response = self.client.post(self.request_url, {'email': self.user.email}, format='json')

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(mail.outbox), 1)
		self.assertIn('reset-password?uid=', mail.outbox[0].body)
		self.assertIn('&token=', mail.outbox[0].body)

	def test_password_reset_request_for_unknown_email_is_generic(self):
		response = self.client.post(self.request_url, {'email': 'missing@example.com'}, format='json')

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(mail.outbox), 0)

	def test_password_reset_confirm_updates_password(self):
		self.client.post(self.request_url, {'email': self.user.email}, format='json')
		email_body = mail.outbox[0].body
		match = re.search(r'uid=([^&\s]+)&token=([^\s]+)', email_body)

		self.assertIsNotNone(match)
		uid, token = match.groups()

		response = self.client.post(
			self.confirm_url,
			{
				'uid': uid,
				'token': token,
				'password1': 'NewPassword123',
				'password2': 'NewPassword123',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)

		login_response = self.client.post(
			self.login_url,
			{'email': self.user.email, 'password': 'NewPassword123'},
			format='json',
		)
		self.assertEqual(login_response.status_code, status.HTTP_200_OK)


@override_settings(
	EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
	EMAIL_VERIFICATION_URL='http://localhost:3000/verify-email',
)
class EmailVerificationFlowTests(APITestCase):
	def setUp(self):
		self.register_url = reverse('user-registration')
		self.confirm_url = reverse('email-verification-confirm')
		self.resend_url = reverse('email-verification-resend')

	def test_registration_sends_verification_email_and_user_starts_unverified(self):
		response = self.client.post(
			self.register_url,
			{
				'email': 'verify@example.com',
				'username': 'verify-user',
				'password1': 'VerifyPass123',
				'password2': 'VerifyPass123',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		user = CustomUser.objects.get(email='verify@example.com')
		self.assertFalse(user.email_verified)
		self.assertEqual(len(mail.outbox), 1)
		self.assertIn('verify-email?uid=', mail.outbox[0].body)
		self.assertIn('&token=', mail.outbox[0].body)

	def test_email_verification_confirm_marks_user_verified(self):
		send_response = self.client.post(
			self.register_url,
			{
				'email': 'newconfirm@example.com',
				'username': 'newconfirm-user',
				'password1': 'ConfirmPass123',
				'password2': 'ConfirmPass123',
			},
			format='json',
		)
		self.assertEqual(send_response.status_code, status.HTTP_201_CREATED)
		verification_email = mail.outbox[-1].body
		match = re.search(r'uid=([^&\s]+)&token=([^\s]+)', verification_email)
		self.assertIsNotNone(match)
		uid, token = match.groups()

		response = self.client.post(
			self.confirm_url,
			{'uid': uid, 'token': token},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		verified_user = CustomUser.objects.get(email='newconfirm@example.com')
		self.assertTrue(verified_user.email_verified)
		self.assertIsNotNone(verified_user.email_verified_at)

	def test_resend_verification_email_for_authenticated_unverified_user(self):
		user = CustomUser.objects.create_user(
			email='resend@example.com',
			username='resend-user',
			password='ResendPass123',
		)

		self.client.force_authenticate(user=user)
		response = self.client.post(self.resend_url, {}, format='json')

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['detail'], 'Verification email sent.')
		self.assertEqual(len(mail.outbox), 1)

	def test_resend_verification_email_for_verified_user_returns_already_verified(self):
		user = CustomUser.objects.create_user(
			email='verified@example.com',
			username='verified-user',
			password='VerifiedPass123',
			email_verified=True,
		)

		self.client.force_authenticate(user=user)
		response = self.client.post(self.resend_url, {}, format='json')

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['detail'], 'Email is already verified.')
		self.assertEqual(len(mail.outbox), 0)


@override_settings(
	STRIPE_SECRET_KEY='sk_test_123',
	STRIPE_PUBLISHABLE_KEY='pk_test_123',
	STRIPE_PRICE_ID='price_123',
	STRIPE_PRICE_ID_GCSE='price_gcse_123',
	STRIPE_PRICE_ID_ALEVEL='price_alevel_123',
	STRIPE_PRICE_ID_BOTH='price_both_123',
	STRIPE_WEBHOOK_SECRET='whsec_123',
	STRIPE_SUCCESS_URL='http://localhost:3000/billing?checkout=success',
	STRIPE_CANCEL_URL='http://localhost:3000/billing?checkout=cancelled',
	STRIPE_CHECKOUT_MODE='payment',
)
class StripeBillingTests(APITestCase):
	def setUp(self):
		self.user = CustomUser.objects.create_user(
			email='billing@example.com',
			username='billing-user',
			password='BillingPass123',
			email_verified=True,
		)
		self.checkout_url = reverse('stripe-checkout-session')
		self.webhook_url = reverse('stripe-webhook')
		self.webhook_event_counter = 0

	def _post_stripe_event(self, mock_construct_event, event_type, event_object, event_id=None):
		self.webhook_event_counter += 1
		event_object.setdefault('id', f'cs_test_{self.webhook_event_counter}')
		if event_object.get('mode') == 'payment':
			event_object.setdefault('amount_total', 1200)
			event_object.setdefault('currency', 'gbp')
			event_object.setdefault('created', 1790000000)
			event_object.setdefault('payment_intent', f'pi_test_{self.webhook_event_counter}')
		mock_construct_event.return_value = {
			'id': event_id or f'evt_test_{self.webhook_event_counter}',
			'type': event_type,
			'data': {'object': event_object},
		}
		return self.client.post(
			self.webhook_url,
			data='{}',
			content_type='application/json',
			HTTP_STRIPE_SIGNATURE='sig_test',
		)

	@patch('accounts.services.stripe.stripe.checkout.Session.create')
	def test_create_checkout_session_uses_gcse_price_id(self, mock_session_create):
		mock_session_create.return_value = SimpleNamespace(id='cs_gcse', url='https://checkout.stripe.com/c/pay/cs_gcse')

		create_stripe_checkout_session(self.user, 'GCSE_SCIENCE')

		self.assertEqual(
			mock_session_create.call_args.kwargs['line_items'],
			[{'price': 'price_gcse_123', 'quantity': 1}],
		)

	@patch('accounts.services.stripe.stripe.checkout.Session.create')
	def test_create_checkout_session_uses_alevel_price_id(self, mock_session_create):
		mock_session_create.return_value = SimpleNamespace(id='cs_alevel', url='https://checkout.stripe.com/c/pay/cs_alevel')

		create_stripe_checkout_session(self.user, 'ALEVEL_BIOLOGY')

		self.assertEqual(
			mock_session_create.call_args.kwargs['line_items'],
			[{'price': 'price_alevel_123', 'quantity': 1}],
		)

	@patch('accounts.services.stripe.stripe.checkout.Session.create')
	def test_create_checkout_session_uses_both_price_id(self, mock_session_create):
		mock_session_create.return_value = SimpleNamespace(id='cs_both', url='https://checkout.stripe.com/c/pay/cs_both')

		create_stripe_checkout_session(self.user, 'BOTH')

		self.assertEqual(
			mock_session_create.call_args.kwargs['line_items'],
			[{'price': 'price_both_123', 'quantity': 1}],
		)

	@patch('accounts.views.create_stripe_checkout_session')
	def test_create_checkout_session_returns_hosted_checkout_data(self, mock_create_session):
		mock_create_session.return_value = SimpleNamespace(
			id='cs_test_123',
			url='https://checkout.stripe.com/c/pay/cs_test_123',
		)

		self.client.force_authenticate(user=self.user)
		response = self.client.post(self.checkout_url, {'qualification': 'ALEVEL'}, format='json')

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['session_id'], 'cs_test_123')
		self.assertEqual(response.data['checkout_url'], 'https://checkout.stripe.com/c/pay/cs_test_123')
		self.assertEqual(response.data['publishable_key'], 'pk_test_123')

	@patch('accounts.views.create_stripe_checkout_session')
	def test_create_checkout_session_allows_unverified_user(self, mock_create_session):
		mock_create_session.return_value = SimpleNamespace(
			id='cs_test_unverified',
			url='https://checkout.stripe.com/c/pay/cs_test_unverified',
		)
		self.user.email_verified = False
		self.user.save(update_fields=['email_verified'])

		self.client.force_authenticate(user=self.user)
		response = self.client.post(self.checkout_url, {'qualification': 'ALEVEL_BIOLOGY'}, format='json')

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['session_id'], 'cs_test_unverified')
		self.assertEqual(response.data['checkout_url'], 'https://checkout.stripe.com/c/pay/cs_test_unverified')

	def test_create_checkout_session_rejects_user_with_existing_qualification_access(self):
		self.user.has_gcse_paid_access = True
		self.user.save(update_fields=['has_gcse_paid_access'])

		self.client.force_authenticate(user=self.user)
		response = self.client.post(self.checkout_url, {'qualification': 'GCSE'}, format='json')

		self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
		self.assertEqual(response.data['detail'], 'This account already has paid access for this qualification.')

	@patch('accounts.views.create_stripe_checkout_session')
	def test_create_checkout_session_allows_combined_purchase_when_user_has_only_one_access_type(self, mock_create_session):
		mock_create_session.return_value = SimpleNamespace(
			id='cs_test_both',
			url='https://checkout.stripe.com/c/pay/cs_test_both',
		)
		self.user.has_gcse_paid_access = True
		self.user.save(update_fields=['has_gcse_paid_access'])

		self.client.force_authenticate(user=self.user)
		response = self.client.post(self.checkout_url, {'qualification': 'BOTH'}, format='json')

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['session_id'], 'cs_test_both')

	@patch('accounts.views.construct_stripe_event')
	def test_checkout_completed_webhook_promotes_user_to_both_paid_access(self, mock_construct_event):
		mock_construct_event.return_value = {
			'id': 'evt_both',
			'type': 'checkout.session.completed',
			'data': {
				'object': {
					'id': 'cs_live_both',
					'customer': 'cus_123',
					'mode': 'payment',
					'payment_status': 'paid',
					'client_reference_id': str(self.user.id),
					'metadata': {'plan_type': UserEntitlement.PlanType.PAID, 'qualification': 'BOTH'},
				}
			},
		}

		response = self.client.post(
			self.webhook_url,
			data='{}',
			content_type='application/json',
			HTTP_STRIPE_SIGNATURE='sig_test',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.user.refresh_from_db()
		self.assertTrue(self.user.has_gcse_paid_access)
		self.assertTrue(self.user.has_alevel_paid_access)
		purchase = Purchase.objects.get(stripe_checkout_session_id='cs_live_both')
		self.assertEqual(purchase.product_identifier, 'both')
		self.assertEqual(purchase.payment_status, Purchase.PaymentStatus.PAID)
		self.assertEqual(purchase.user, self.user)

	@patch('accounts.views.construct_stripe_event')
	def test_duplicate_processed_event_does_not_repeat_entitlement_or_purchase_updates(self, mock_construct_event):
		session = {
			'id': 'cs_duplicate',
			'mode': 'payment',
			'payment_status': 'paid',
			'created': 1790000000,
			'amount_total': 1500,
			'currency': 'gbp',
			'payment_intent': 'pi_duplicate',
			'client_reference_id': str(self.user.id),
			'metadata': {'qualification': 'GCSE_SCIENCE'},
		}
		first_response = self._post_stripe_event(
			mock_construct_event,
			'checkout.session.completed',
			session.copy(),
			event_id='evt_duplicate',
		)
		purchase = Purchase.objects.get(stripe_checkout_session_id='cs_duplicate')
		entitlement = self.user.entitlement
		entitlement.refresh_from_db()
		purchase_timestamp = purchase.purchased_at
		paid_at = entitlement.paid_at

		stale_session = {**session, 'created': 1791000000, 'amount_total': 1}
		second_response = self._post_stripe_event(
			mock_construct_event,
			'checkout.session.completed',
			stale_session,
			event_id='evt_duplicate',
		)

		purchase.refresh_from_db()
		entitlement.refresh_from_db()
		webhook_event = StripeWebhookEvent.objects.get(stripe_event_id='evt_duplicate')
		self.assertEqual(first_response.status_code, status.HTTP_200_OK)
		self.assertEqual(second_response.status_code, status.HTTP_200_OK)
		self.assertEqual(Purchase.objects.filter(stripe_checkout_session_id='cs_duplicate').count(), 1)
		self.assertEqual(purchase.purchased_at, purchase_timestamp)
		self.assertEqual(purchase.amount, 1500)
		self.assertEqual(purchase.currency, 'gbp')
		self.assertEqual(purchase.stripe_payment_intent_id, 'pi_duplicate')
		self.assertEqual(entitlement.paid_at, paid_at)
		self.assertEqual(webhook_event.attempt_count, 1)

	@patch('accounts.views.construct_stripe_event')
	def test_distinct_events_for_same_checkout_create_one_paid_purchase(self, mock_construct_event):
		session = {
			'id': 'cs_two_events',
			'mode': 'payment',
			'payment_status': 'paid',
			'created': 1790000000,
			'amount_total': 2400,
			'currency': 'gbp',
			'payment_intent': 'pi_two_events',
			'client_reference_id': str(self.user.id),
			'metadata': {'qualification': 'ALEVEL_BIOLOGY'},
		}
		first = self._post_stripe_event(
			mock_construct_event,
			'checkout.session.completed',
			session.copy(),
			event_id='evt_completed',
		)
		first_purchase = Purchase.objects.get(stripe_checkout_session_id='cs_two_events')
		purchased_at = first_purchase.purchased_at
		second = self._post_stripe_event(
			mock_construct_event,
			'checkout.session.async_payment_succeeded',
			{**session, 'created': 1791000000, 'amount_total': 10},
			event_id='evt_async_succeeded',
		)

		first_purchase.refresh_from_db()
		self.user.refresh_from_db()
		self.assertEqual(first.status_code, status.HTTP_200_OK)
		self.assertEqual(second.status_code, status.HTTP_200_OK)
		self.assertEqual(Purchase.objects.filter(stripe_checkout_session_id='cs_two_events').count(), 1)
		self.assertEqual(first_purchase.purchased_at, purchased_at)
		self.assertEqual(first_purchase.amount, 2400)
		self.assertTrue(self.user.has_alevel_paid_access)
		self.assertFalse(self.user.has_gcse_paid_access)

	@patch('accounts.services.stripe._save_user_access_flags', side_effect=RuntimeError('temporary failure'))
	@patch('accounts.views.construct_stripe_event')
	def test_failed_processing_is_retryable_and_atomic(self, mock_construct_event, mock_save_access):
		session = {
			'id': 'cs_retry',
			'mode': 'payment',
			'payment_status': 'paid',
			'created': 1790000000,
			'amount_total': 900,
			'currency': 'gbp',
			'payment_intent': 'pi_retry',
			'client_reference_id': str(self.user.id),
			'metadata': {'qualification': 'GCSE_SCIENCE'},
		}
		failed_response = self._post_stripe_event(
			mock_construct_event,
			'checkout.session.completed',
			session.copy(),
			event_id='evt_retry',
		)
		webhook_event = StripeWebhookEvent.objects.get(stripe_event_id='evt_retry')
		self.assertEqual(failed_response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
		self.assertEqual(webhook_event.processing_status, StripeWebhookEvent.ProcessingStatus.FAILED)
		self.assertEqual(webhook_event.attempt_count, 1)
		self.assertFalse(Purchase.objects.filter(stripe_checkout_session_id='cs_retry').exists())
		self.user.refresh_from_db()
		self.assertFalse(self.user.has_gcse_paid_access)

		def save_access_flags(user, **access_flags):
			field_names = {
				'gcse_access': 'has_gcse_paid_access',
				'alevel_access': 'has_alevel_paid_access',
			}
			for field, value in access_flags.items():
				setattr(user, field_names[field], value)
			user.save(update_fields=[field_names[field] for field in access_flags])

		mock_save_access.side_effect = save_access_flags
		retry_response = self._post_stripe_event(
			mock_construct_event,
			'checkout.session.completed',
			session.copy(),
			event_id='evt_retry',
		)
		webhook_event.refresh_from_db()
		self.assertEqual(retry_response.status_code, status.HTTP_200_OK)
		self.assertEqual(webhook_event.processing_status, StripeWebhookEvent.ProcessingStatus.PROCESSED)
		self.assertEqual(webhook_event.attempt_count, 2)
		self.assertTrue(Purchase.objects.filter(stripe_checkout_session_id='cs_retry').exists())
		self.user.refresh_from_db()
		self.assertTrue(self.user.has_gcse_paid_access)

	@patch('accounts.views.construct_stripe_event')
	def test_subscription_deleted_webhook_does_not_revoke_both_access_flags(self, mock_construct_event):
		entitlement = self.user.entitlement
		self.user.has_gcse_paid_access = True
		self.user.has_alevel_paid_access = True
		self.user.save(update_fields=['has_gcse_paid_access', 'has_alevel_paid_access'])
		entitlement.plan_type = UserEntitlement.PlanType.PAID
		entitlement.stripe_customer_id = 'cus_123'
		entitlement.stripe_subscription_id = 'sub_123'
		entitlement.save(update_fields=['plan_type', 'stripe_customer_id', 'stripe_subscription_id'])

		mock_construct_event.return_value = {
			'id': 'evt_subscription_deleted_both',
			'type': 'customer.subscription.deleted',
			'data': {
				'object': {
					'id': 'sub_123',
					'customer': 'cus_123',
					'status': 'canceled',
					'metadata': {'qualification': 'BOTH'},
				}
			},
		}

		response = self.client.post(
			self.webhook_url,
			data='{}',
			content_type='application/json',
			HTTP_STRIPE_SIGNATURE='sig_test',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.user.refresh_from_db()
		self.assertTrue(self.user.has_gcse_paid_access)
		self.assertTrue(self.user.has_alevel_paid_access)
		self.assertFalse(Purchase.objects.exists())

	@patch('accounts.services.stripe.stripe.Subscription.retrieve')
	@patch('accounts.views.construct_stripe_event')
	def test_checkout_completed_webhook_promotes_user_to_paid(self, mock_construct_event, mock_subscription_retrieve):
		mock_subscription_retrieve.return_value = {
			'id': 'sub_123',
			'customer': 'cus_123',
			'status': 'active',
		}
		mock_construct_event.return_value = {
			'id': 'evt_subscription_checkout',
			'type': 'checkout.session.completed',
			'data': {
				'object': {
					'id': 'cs_live_123',
					'customer': 'cus_123',
					'subscription': 'sub_123',
					'mode': 'subscription',
					'payment_status': 'paid',
					'client_reference_id': str(self.user.id),
					'metadata': {'plan_type': UserEntitlement.PlanType.PAID, 'qualification': 'ALEVEL_BIOLOGY'},
				}
			},
		}

		response = self.client.post(
			self.webhook_url,
			data='{}',
			content_type='application/json',
			HTTP_STRIPE_SIGNATURE='sig_test',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		entitlement = self.user.entitlement
		entitlement.refresh_from_db()
		self.assertEqual(entitlement.plan_type, UserEntitlement.PlanType.PAID)
		self.assertEqual(entitlement.stripe_customer_id, 'cus_123')
		self.assertEqual(entitlement.stripe_checkout_session_id, 'cs_live_123')
		self.assertEqual(entitlement.stripe_subscription_id, 'sub_123')
		self.user.refresh_from_db()
		self.assertTrue(self.user.has_alevel_paid_access)
		self.assertFalse(self.user.has_gcse_paid_access)
		mock_subscription_retrieve.assert_called_once_with('sub_123')

	@patch('accounts.views.construct_stripe_event')
	def test_checkout_with_missing_or_unknown_mode_does_not_grant_access(self, mock_construct_event):
		for mode in (None, 'unknown'):
			with self.subTest(mode=mode):
				checkout_session = {
					'payment_status': 'paid',
					'client_reference_id': str(self.user.id),
					'metadata': {'qualification': 'GCSE_SCIENCE'},
				}
				if mode is not None:
					checkout_session['mode'] = mode

				response = self._post_stripe_event(
					mock_construct_event,
					'checkout.session.completed',
					checkout_session,
				)

				self.assertEqual(response.status_code, status.HTTP_200_OK)
				self.user.refresh_from_db()
				self.assertFalse(self.user.has_gcse_paid_access)
				self.assertFalse(self.user.has_alevel_paid_access)
				self.assertFalse(Purchase.objects.exists())

	@patch('accounts.services.stripe.stripe.Subscription.retrieve')
	@patch('accounts.views.construct_stripe_event')
	def test_inactive_subscription_checkout_does_not_grant_access(
		self, mock_construct_event, mock_subscription_retrieve
	):
		mock_subscription_retrieve.return_value = {
			'id': 'sub_123',
			'customer': 'cus_123',
			'status': 'incomplete',
		}
		response = self._post_stripe_event(
			mock_construct_event,
			'checkout.session.completed',
			{
				'mode': 'subscription',
				'subscription': 'sub_123',
				'customer': 'cus_123',
				'payment_status': 'paid',
				'client_reference_id': str(self.user.id),
				'metadata': {'qualification': 'ALEVEL_BIOLOGY'},
			},
		)

		self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
		self.user.refresh_from_db()
		self.assertFalse(self.user.has_gcse_paid_access)
		self.assertFalse(self.user.has_alevel_paid_access)
		webhook_event = StripeWebhookEvent.objects.get()
		self.assertEqual(webhook_event.processing_status, StripeWebhookEvent.ProcessingStatus.FAILED)

	@patch('accounts.services.stripe.stripe.Subscription.retrieve')
	@patch('accounts.views.construct_stripe_event')
	def test_subscription_lookup_failure_is_retryable_and_recovers(
		self, mock_construct_event, mock_subscription_retrieve
	):
		session = {
			'mode': 'subscription',
			'subscription': 'sub_retry',
			'customer': 'cus_retry',
			'payment_status': 'paid',
			'client_reference_id': str(self.user.id),
			'metadata': {'qualification': 'ALEVEL_BIOLOGY'},
		}
		mock_subscription_retrieve.side_effect = stripe.error.APIConnectionError('network down')
		first = self._post_stripe_event(
			mock_construct_event, 'checkout.session.completed', session.copy(), event_id='evt_sub_retry'
		)
		webhook_event = StripeWebhookEvent.objects.get(stripe_event_id='evt_sub_retry')
		self.assertEqual(first.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
		self.assertEqual(webhook_event.processing_status, StripeWebhookEvent.ProcessingStatus.FAILED)
		self.assertEqual(webhook_event.attempt_count, 1)
		self.user.refresh_from_db()
		self.assertFalse(self.user.has_alevel_paid_access)

		mock_subscription_retrieve.side_effect = None
		mock_subscription_retrieve.return_value = {
			'id': 'sub_retry',
			'customer': 'cus_retry',
			'status': 'active',
		}
		second = self._post_stripe_event(
			mock_construct_event, 'checkout.session.completed', session.copy(), event_id='evt_sub_retry'
		)
		webhook_event.refresh_from_db()
		self.assertEqual(second.status_code, status.HTTP_200_OK)
		self.assertEqual(webhook_event.processing_status, StripeWebhookEvent.ProcessingStatus.PROCESSED)
		self.assertEqual(webhook_event.attempt_count, 2)
		self.user.refresh_from_db()
		self.assertTrue(self.user.has_alevel_paid_access)

	@patch('accounts.services.stripe.stripe.Subscription.retrieve')
	@patch('accounts.views.construct_stripe_event')
	def test_canceled_or_mismatched_subscription_is_permanently_ignored(
		self, mock_construct_event, mock_subscription_retrieve
	):
		cases = (('canceled', 'cus_123'), ('active', 'cus_other'))
		for index, (sub_status, sub_customer) in enumerate(cases):
			with self.subTest(status=sub_status, customer=sub_customer):
				mock_subscription_retrieve.return_value = {
					'id': 'sub_123',
					'customer': sub_customer,
					'status': sub_status,
				}
				response = self._post_stripe_event(
					mock_construct_event,
					'checkout.session.completed',
					{
						'mode': 'subscription',
						'subscription': 'sub_123',
						'customer': 'cus_123',
						'client_reference_id': str(self.user.id),
						'metadata': {'qualification': 'ALEVEL_BIOLOGY'},
					},
					event_id=f'evt_ignored_{index}',
				)
				event = StripeWebhookEvent.objects.get(stripe_event_id=f'evt_ignored_{index}')
				self.assertEqual(response.status_code, status.HTTP_200_OK)
				self.assertEqual(event.processing_status, StripeWebhookEvent.ProcessingStatus.PROCESSED)
				self.user.refresh_from_db()
				self.assertFalse(self.user.has_alevel_paid_access)

	@patch('accounts.views.construct_stripe_event')
	def test_paid_gcse_checkout_grants_gcse_access_only(self, mock_construct_event):
		response = self._post_stripe_event(
			mock_construct_event,
			'checkout.session.completed',
			{
				'mode': 'payment',
				'payment_status': 'paid',
				'client_reference_id': str(self.user.id),
				'metadata': {'qualification': 'GCSE_SCIENCE'},
			},
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.user.refresh_from_db()
		self.assertTrue(self.user.has_gcse_paid_access)
		self.assertFalse(self.user.has_alevel_paid_access)

	@patch('accounts.views.construct_stripe_event')
	def test_paid_biology_checkout_grants_biology_access_only(self, mock_construct_event):
		response = self._post_stripe_event(
			mock_construct_event,
			'checkout.session.completed',
			{
				'mode': 'payment',
				'payment_status': 'paid',
				'client_reference_id': str(self.user.id),
				'metadata': {'qualification': 'ALEVEL_BIOLOGY'},
			},
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.user.refresh_from_db()
		self.assertFalse(self.user.has_gcse_paid_access)
		self.assertTrue(self.user.has_alevel_paid_access)

	@patch('accounts.views.construct_stripe_event')
	def test_unpaid_checkout_does_not_grant_access(self, mock_construct_event):
		response = self._post_stripe_event(
			mock_construct_event,
			'checkout.session.completed',
			{
				'mode': 'payment',
				'payment_status': 'unpaid',
				'client_reference_id': str(self.user.id),
				'metadata': {'qualification': 'GCSE_SCIENCE'},
			},
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.user.refresh_from_db()
		self.assertFalse(self.user.has_gcse_paid_access)
		self.assertFalse(self.user.has_alevel_paid_access)
		self.assertFalse(Purchase.objects.exists())

	@patch('accounts.views.construct_stripe_event')
	def test_pending_checkout_grants_access_after_confirmed_async_payment(self, mock_construct_event):
		pending_response = self._post_stripe_event(
			mock_construct_event,
			'checkout.session.completed',
			{
				'mode': 'payment',
				'payment_status': 'unpaid',
				'client_reference_id': str(self.user.id),
				'metadata': {'qualification': 'GCSE_SCIENCE'},
			},
		)
		self.user.refresh_from_db()
		self.assertEqual(pending_response.status_code, status.HTTP_200_OK)
		self.assertFalse(self.user.has_gcse_paid_access)
		self.assertFalse(self.user.has_alevel_paid_access)

		async_response = self._post_stripe_event(
			mock_construct_event,
			'checkout.session.async_payment_succeeded',
			{
				'mode': 'payment',
				'payment_status': 'unpaid',
				'client_reference_id': str(self.user.id),
				'metadata': {'qualification': 'GCSE_SCIENCE'},
			},
		)
		self.user.refresh_from_db()
		self.assertEqual(async_response.status_code, status.HTTP_200_OK)
		self.assertFalse(self.user.has_gcse_paid_access)
		self.assertFalse(self.user.has_alevel_paid_access)

		async_response = self._post_stripe_event(
			mock_construct_event,
			'checkout.session.async_payment_succeeded',
			{
				'mode': 'payment',
				'payment_status': 'paid',
				'client_reference_id': str(self.user.id),
				'metadata': {
					'qualification': 'GCSE_SCIENCE',
					'plan_type': UserEntitlement.PlanType.LIFETIME,
				},
			},
		)

		self.assertEqual(async_response.status_code, status.HTTP_200_OK)
		self.user.refresh_from_db()
		self.assertTrue(self.user.has_gcse_paid_access)
		self.assertFalse(self.user.has_alevel_paid_access)

	@patch('accounts.views.construct_stripe_event')
	def test_missing_checkout_qualification_does_not_grant_access(self, mock_construct_event):
		for metadata in ({}, {'qualification': ''}):
			with self.subTest(metadata=metadata):
				response = self._post_stripe_event(
					mock_construct_event,
					'checkout.session.completed',
					{
						'mode': 'payment',
						'payment_status': 'paid',
						'client_reference_id': str(self.user.id),
						'metadata': metadata,
					},
				)

				self.assertEqual(response.status_code, status.HTTP_200_OK)
				self.user.refresh_from_db()
				self.assertFalse(self.user.has_gcse_paid_access)
				self.assertFalse(self.user.has_alevel_paid_access)

	@patch('accounts.views.construct_stripe_event')
	def test_unknown_checkout_qualification_does_not_grant_access(self, mock_construct_event):
		response = self._post_stripe_event(
			mock_construct_event,
			'checkout.session.completed',
			{
				'mode': 'payment',
				'payment_status': 'paid',
				'client_reference_id': str(self.user.id),
				'metadata': {'qualification': 'CHEMISTRY'},
			},
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.user.refresh_from_db()
		self.assertFalse(self.user.has_gcse_paid_access)
		self.assertFalse(self.user.has_alevel_paid_access)

	@patch('accounts.views.construct_stripe_event')
	def test_conflicting_checkout_user_metadata_does_not_guess_account(self, mock_construct_event):
		other_user = CustomUser.objects.create_user(
			email='other-billing@example.com',
			username='other-billing-user',
			password='OtherBillingPass123',
		)
		response = self._post_stripe_event(
			mock_construct_event,
			'checkout.session.completed',
			{
				'id': 'cs_conflicting_user',
				'mode': 'payment',
				'payment_status': 'paid',
				'client_reference_id': str(self.user.id),
				'metadata': {
					'user_id': str(other_user.id),
					'qualification': 'GCSE_SCIENCE',
				},
			},
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.user.refresh_from_db()
		other_user.refresh_from_db()
		self.assertFalse(self.user.has_gcse_paid_access)
		self.assertFalse(other_user.has_gcse_paid_access)
		self.assertFalse(Purchase.objects.exists())
		webhook_event = StripeWebhookEvent.objects.get(stripe_event_id='evt_test_1')
		self.assertEqual(webhook_event.processing_status, StripeWebhookEvent.ProcessingStatus.PROCESSED)
		self.assertIn('conflicting', webhook_event.error_message)

	@patch('accounts.views.construct_stripe_event')
	def test_checkout_preserves_existing_lifetime_access(self, mock_construct_event):
		entitlement = self.user.entitlement
		entitlement.lifetime_unlocked = True
		entitlement.plan_type = UserEntitlement.PlanType.LIFETIME
		entitlement.save(update_fields=['lifetime_unlocked', 'plan_type'])
		self.user.has_gcse_paid_access = True
		self.user.has_alevel_paid_access = True
		self.user.save(update_fields=['has_gcse_paid_access', 'has_alevel_paid_access'])

		response = self._post_stripe_event(
			mock_construct_event,
			'checkout.session.completed',
			{
				'mode': 'payment',
				'payment_status': 'paid',
				'client_reference_id': str(self.user.id),
				'metadata': {
					'qualification': 'GCSE_SCIENCE',
					'plan_type': UserEntitlement.PlanType.LIFETIME,
				},
			},
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		entitlement.refresh_from_db()
		self.user.refresh_from_db()
		self.assertTrue(entitlement.lifetime_unlocked)
		self.assertEqual(entitlement.plan_type, UserEntitlement.PlanType.LIFETIME)
		self.assertTrue(self.user.has_gcse_paid_access)
		self.assertTrue(self.user.has_alevel_paid_access)

	@patch('accounts.views.construct_stripe_event')
	def test_checkout_lifetime_metadata_does_not_create_lifetime_access(self, mock_construct_event):
		response = self._post_stripe_event(
			mock_construct_event,
			'checkout.session.completed',
			{
				'mode': 'payment',
				'payment_status': 'paid',
				'client_reference_id': str(self.user.id),
				'metadata': {
					'qualification': 'GCSE_SCIENCE',
					'plan_type': UserEntitlement.PlanType.LIFETIME,
				},
			},
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		entitlement = self.user.entitlement
		entitlement.refresh_from_db()
		self.user.refresh_from_db()
		self.assertFalse(entitlement.lifetime_unlocked)
		self.assertEqual(entitlement.plan_type, UserEntitlement.PlanType.PAID)
		self.assertTrue(self.user.has_gcse_paid_access)
		self.assertFalse(self.user.has_alevel_paid_access)

	@patch('accounts.views.construct_stripe_event')
	def test_checkout_historical_qualification_aliases_still_work(self, mock_construct_event):
		response = self._post_stripe_event(
			mock_construct_event,
			'checkout.session.completed',
			{
				'mode': 'payment',
				'payment_status': 'paid',
				'client_reference_id': str(self.user.id),
				'metadata': {'qualification': 'A-Level Biology'},
			},
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.user.refresh_from_db()
		self.assertFalse(self.user.has_gcse_paid_access)
		self.assertTrue(self.user.has_alevel_paid_access)

	@patch('accounts.views.construct_stripe_event')
	def test_invalid_subscription_qualification_does_not_change_access(self, mock_construct_event):
		entitlement = self.user.entitlement
		entitlement.stripe_customer_id = 'cus_123'
		entitlement.stripe_subscription_id = 'sub_123'
		entitlement.save(update_fields=['stripe_customer_id', 'stripe_subscription_id'])
		self.user.has_gcse_paid_access = True
		self.user.save(update_fields=['has_gcse_paid_access'])

		for subscription_status, event_type in (
			('active', 'customer.subscription.updated'),
			('canceled', 'customer.subscription.deleted'),
		):
			with self.subTest(subscription_status=subscription_status):
				response = self._post_stripe_event(
					mock_construct_event,
					event_type,
					{
						'id': 'sub_123',
						'customer': 'cus_123',
						'status': subscription_status,
						'metadata': {'qualification': 'UNKNOWN'},
					},
				)

				self.assertEqual(response.status_code, status.HTTP_200_OK)
				self.user.refresh_from_db()
				self.assertTrue(self.user.has_gcse_paid_access)
				self.assertFalse(self.user.has_alevel_paid_access)

	@patch('accounts.views.construct_stripe_event')
	def test_subscription_cancellation_preserves_lifetime_access(self, mock_construct_event):
		entitlement = self.user.entitlement
		entitlement.stripe_customer_id = 'cus_123'
		entitlement.stripe_subscription_id = 'sub_123'
		entitlement.lifetime_unlocked = True
		entitlement.plan_type = UserEntitlement.PlanType.LIFETIME
		entitlement.save(
			update_fields=['stripe_customer_id', 'stripe_subscription_id', 'lifetime_unlocked', 'plan_type']
		)
		self.user.has_gcse_paid_access = True
		self.user.has_alevel_paid_access = True
		self.user.save(update_fields=['has_gcse_paid_access', 'has_alevel_paid_access'])

		response = self._post_stripe_event(
			mock_construct_event,
			'customer.subscription.deleted',
			{
				'id': 'sub_123',
				'customer': 'cus_123',
				'status': 'canceled',
				'metadata': {'qualification': 'BOTH'},
			},
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		entitlement.refresh_from_db()
		self.user.refresh_from_db()
		self.assertTrue(entitlement.lifetime_unlocked)
		self.assertEqual(entitlement.plan_type, UserEntitlement.PlanType.LIFETIME)
		self.assertTrue(self.user.has_gcse_paid_access)
		self.assertTrue(self.user.has_alevel_paid_access)

	@patch('accounts.views.construct_stripe_event')
	def test_subscription_deleted_webhook_does_not_downgrade_one_off_access(self, mock_construct_event):
		entitlement = self.user.entitlement
		self.user.has_gcse_paid_access = True
		self.user.save(update_fields=['has_gcse_paid_access'])
		entitlement.plan_type = UserEntitlement.PlanType.PAID
		entitlement.stripe_customer_id = 'cus_123'
		entitlement.stripe_subscription_id = 'sub_123'
		entitlement.save(update_fields=['plan_type', 'stripe_customer_id', 'stripe_subscription_id'])

		mock_construct_event.return_value = {
			'id': 'evt_subscription_deleted',
			'type': 'customer.subscription.deleted',
			'data': {
				'object': {
					'id': 'sub_123',
					'customer': 'cus_123',
					'status': 'canceled',
					'metadata': {'qualification': 'GCSE_SCIENCE'},
				}
			},
		}

		response = self.client.post(
			self.webhook_url,
			data='{}',
			content_type='application/json',
			HTTP_STRIPE_SIGNATURE='sig_test',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		entitlement.refresh_from_db()
		self.user.refresh_from_db()
		self.assertEqual(entitlement.plan_type, UserEntitlement.PlanType.PAID)
		self.assertTrue(self.user.has_gcse_paid_access)

	def _subscription_event(self, mock_construct_event, event_type, sub_status, subscription_id, qualification):
		return self._post_stripe_event(
			mock_construct_event,
			event_type,
			{
				'id': subscription_id,
				'customer': 'cus_123',
				'status': sub_status,
				'metadata': {'qualification': qualification},
			},
		)

	@patch('accounts.views.construct_stripe_event')
	def test_legacy_purchaser_without_purchase_record_keeps_access_after_cancellation(self, mock_construct_event):
		self.user.has_gcse_paid_access = True
		self.user.save(update_fields=['has_gcse_paid_access'])
		entitlement = self.user.entitlement
		entitlement.stripe_customer_id = 'cus_123'
		entitlement.save(update_fields=['stripe_customer_id'])

		for index, (sub_status, event_type) in enumerate((
			('canceled', 'customer.subscription.deleted'),
			('unpaid', 'customer.subscription.updated'),
		)):
			with self.subTest(sub_status=sub_status):
				response = self._subscription_event(
					mock_construct_event, event_type, sub_status, f'sub_other_{index}', 'GCSE_SCIENCE'
				)
				self.assertEqual(response.status_code, status.HTTP_200_OK)
				self.user.refresh_from_db()
				self.assertTrue(self.user.has_gcse_paid_access)
		self.assertFalse(Purchase.objects.exists())

	@patch('accounts.views.construct_stripe_event')
	def test_paid_purchaser_keeps_access_after_canceled_or_unpaid_matching_subscription(self, mock_construct_event):
		self._post_stripe_event(
			mock_construct_event,
			'checkout.session.completed',
			{
				'mode': 'payment',
				'payment_status': 'paid',
				'customer': 'cus_123',
				'client_reference_id': str(self.user.id),
				'metadata': {'qualification': 'GCSE_SCIENCE'},
			},
		)
		entitlement = self.user.entitlement
		entitlement.stripe_subscription_id = 'sub_123'
		entitlement.save(update_fields=['stripe_subscription_id'])

		for index, (sub_status, event_type) in enumerate((
			('canceled', 'customer.subscription.deleted'),
			('unpaid', 'customer.subscription.updated'),
		)):
			with self.subTest(sub_status=sub_status):
				response = self._subscription_event(mock_construct_event, event_type, sub_status, 'sub_123', 'GCSE_SCIENCE')
				self.assertEqual(response.status_code, status.HTTP_200_OK)
				self.user.refresh_from_db()
				self.assertTrue(self.user.has_gcse_paid_access)
		self.assertEqual(Purchase.objects.count(), 1)

	@patch('accounts.views.construct_stripe_event')
	def test_gcse_customer_buying_biology_keeps_both_flags_after_cancellation(self, mock_construct_event):
		for qualification in ('GCSE_SCIENCE', 'ALEVEL_BIOLOGY'):
			self._post_stripe_event(
				mock_construct_event,
				'checkout.session.completed',
				{
					'mode': 'payment',
					'payment_status': 'paid',
					'customer': 'cus_123',
					'client_reference_id': str(self.user.id),
					'metadata': {'qualification': qualification},
				},
			)
		entitlement = self.user.entitlement
		entitlement.stripe_subscription_id = 'sub_123'
		entitlement.save(update_fields=['stripe_subscription_id'])

		self._subscription_event(mock_construct_event, 'customer.subscription.deleted', 'canceled', 'sub_123', 'GCSE_SCIENCE')

		self.user.refresh_from_db()
		self.assertTrue(self.user.has_gcse_paid_access)
		self.assertTrue(self.user.has_alevel_paid_access)
		self.assertEqual(Purchase.objects.count(), 2)

	@patch('accounts.views.construct_stripe_event')
	def test_unrelated_subscription_id_is_ignored_even_with_matching_customer(self, mock_construct_event):
		entitlement = self.user.entitlement
		entitlement.stripe_customer_id = 'cus_123'
		entitlement.stripe_subscription_id = 'sub_123'
		entitlement.save(update_fields=['stripe_customer_id', 'stripe_subscription_id'])

		response = self._subscription_event(
			mock_construct_event, 'customer.subscription.updated', 'active', 'sub_unrelated', 'ALEVEL_BIOLOGY'
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		entitlement.refresh_from_db()
		self.user.refresh_from_db()
		self.assertEqual(entitlement.stripe_subscription_id, 'sub_123')
		self.assertFalse(self.user.has_alevel_paid_access)
		self.assertFalse(self.user.has_gcse_paid_access)

	@patch('accounts.views.construct_stripe_event')
	def test_subscription_event_without_stored_subscription_id_is_ignored(self, mock_construct_event):
		entitlement = self.user.entitlement
		entitlement.stripe_customer_id = 'cus_123'
		entitlement.save(update_fields=['stripe_customer_id'])

		response = self._subscription_event(
			mock_construct_event, 'customer.subscription.updated', 'active', 'sub_new', 'GCSE_SCIENCE'
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		entitlement.refresh_from_db()
		self.user.refresh_from_db()
		self.assertIsNone(entitlement.stripe_subscription_id)
		self.assertFalse(self.user.has_gcse_paid_access)

	@patch('accounts.views.construct_stripe_event')
	def test_duplicate_checkout_events_do_not_change_existing_access(self, mock_construct_event):
		self.user.has_gcse_paid_access = True
		self.user.save(update_fields=['has_gcse_paid_access'])
		session = {
			'id': 'cs_dup',
			'mode': 'payment',
			'payment_status': 'paid',
			'client_reference_id': str(self.user.id),
			'metadata': {'qualification': 'ALEVEL_BIOLOGY'},
		}
		self._post_stripe_event(mock_construct_event, 'checkout.session.completed', session.copy(), event_id='evt_dup_1')
		purchase = Purchase.objects.get()
		purchased_at = purchase.purchased_at
		for event_id, event_type in (
			('evt_dup_1', 'checkout.session.completed'),
			('evt_dup_2', 'checkout.session.async_payment_succeeded'),
		):
			response = self._post_stripe_event(mock_construct_event, event_type, session.copy(), event_id=event_id)
			self.assertEqual(response.status_code, status.HTTP_200_OK)

		purchase.refresh_from_db()
		self.user.refresh_from_db()
		self.assertEqual(Purchase.objects.count(), 1)
		self.assertEqual(purchase.purchased_at, purchased_at)
		self.assertTrue(self.user.has_gcse_paid_access)
		self.assertTrue(self.user.has_alevel_paid_access)


class ConcurrentStripeWebhookTests(TransactionTestCase):
	reset_sequences = True

	def setUp(self):
		# TransactionTestCase flushes the migration-seeded subjects.
		for code, name, qualification in (
			('gcse_science', 'Science', 'GCSE'),
			('alevel_biology', 'Biology', 'A_LEVEL'),
		):
			Subject.objects.get_or_create(code=code, defaults={'name': name, 'qualification': qualification})

	def test_concurrent_duplicate_delivery_creates_one_purchase(self):
		user = CustomUser.objects.create_user(
			email='concurrent-billing@example.com',
			username='concurrent-billing-user',
			password='ConcurrentBillingPass123',
			email_verified=False,
		)
		event = {
			'id': 'evt_concurrent',
			'type': 'checkout.session.completed',
			'data': {
				'object': {
					'id': 'cs_concurrent',
					'mode': 'payment',
					'payment_status': 'paid',
					'created': 1790000000,
					'amount_total': 1700,
					'currency': 'gbp',
					'payment_intent': 'pi_concurrent',
					'client_reference_id': str(user.id),
					'metadata': {'qualification': 'GCSE_SCIENCE'},
				},
			},
		}
		barrier = Barrier(2)

		def deliver_event():
			close_old_connections()
			try:
				barrier.wait(timeout=5)
				for attempt in range(40):
					try:
						return process_stripe_webhook_event(event)
					except OperationalError:
						# SQLite table locks stand in for PostgreSQL row-lock waits.
						if attempt == 39:
							raise
						time.sleep(0.05)
			finally:
				# close_old_connections() keeps healthy connections open when CONN_MAX_AGE > 0.
				connections.close_all()

		with ThreadPoolExecutor(max_workers=2) as executor:
			results = list(executor.map(lambda _: deliver_event(), range(2)))

		self.assertEqual(sorted(results), [False, True])
		self.assertEqual(Purchase.objects.filter(stripe_checkout_session_id='cs_concurrent').count(), 1)
		webhook_event = StripeWebhookEvent.objects.get(stripe_event_id='evt_concurrent')
		self.assertEqual(webhook_event.processing_status, StripeWebhookEvent.ProcessingStatus.PROCESSED)
		user.refresh_from_db()
		self.assertTrue(user.has_gcse_paid_access)
		self.assertFalse(user.has_alevel_paid_access)


class PerformanceTrackingResetTests(APITestCase):
	def setUp(self):
		self.user = CustomUser.objects.create_user(
			email='tracking@example.com',
			username='tracking-user',
			password='TrackingPass123',
		)
		self.url = reverse('reset-performance-tracking')

	def test_reset_performance_tracking_sets_start_date(self):
		self.client.force_authenticate(user=self.user)

		response = self.client.post(self.url, {}, format='json')

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.user.refresh_from_db()
		self.assertIsNotNone(self.user.performance_tracking_start_date)
		self.assertEqual(response.data['detail'], 'Performance tracking reset successfully.')

	def test_reset_performance_tracking_requires_authentication(self):
		response = self.client.post(self.url, {}, format='json')

		self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class SubjectModelTests(APITestCase):
	def test_initial_subjects_are_seeded(self):
		codes = set(Subject.objects.values_list('code', flat=True))
		self.assertTrue({'gcse_science', 'alevel_biology', 'alevel_chemistry', 'alevel_physics'} <= codes)
		self.assertEqual(Subject.objects.get(code='gcse_science').qualification, Subject.Qualification.GCSE)
		self.assertEqual(Subject.objects.get(code='alevel_physics').qualification, Subject.Qualification.A_LEVEL)

	def test_create_subject(self):
		subject = Subject.objects.create(code='alevel_geography', name='Geography', qualification=Subject.Qualification.A_LEVEL)

		self.assertTrue(subject.is_active)
		self.assertEqual(str(subject), 'A-level Geography')

	def test_subject_code_must_be_unique(self):
		Subject.objects.create(code='gcse_english', name='English', qualification=Subject.Qualification.GCSE)

		with self.assertRaises(IntegrityError), transaction.atomic():
			Subject.objects.create(code='gcse_english', name='English Again', qualification=Subject.Qualification.GCSE)


class SubjectEntitlementTests(APITestCase):
	def setUp(self):
		self.user = CustomUser.objects.create_user(email='subject1@example.com', username='subject-user-1', password='SubjectPass123')
		self.other_user = CustomUser.objects.create_user(email='subject2@example.com', username='subject-user-2', password='SubjectPass123')
		self.biology = Subject.objects.get(code='alevel_biology')
		self.chemistry = Subject.objects.get(code='alevel_chemistry')

	def test_create_subject_entitlement(self):
		entitlement = SubjectEntitlement.objects.create(user=self.user, subject=self.biology)

		self.assertIsNotNone(entitlement.granted_at)
		self.assertEqual(str(entitlement), 'subject1@example.com | alevel_biology')
		self.assertEqual(list(self.user.subject_entitlements.all()), [entitlement])

	def test_duplicate_entitlement_for_same_user_and_subject_is_rejected(self):
		SubjectEntitlement.objects.create(user=self.user, subject=self.biology)

		with self.assertRaises(IntegrityError), transaction.atomic():
			SubjectEntitlement.objects.create(user=self.user, subject=self.biology)

	def test_user_can_have_multiple_subject_entitlements(self):
		SubjectEntitlement.objects.create(user=self.user, subject=self.biology)
		SubjectEntitlement.objects.create(user=self.user, subject=self.chemistry)

		self.assertEqual(self.user.subject_entitlements.count(), 2)

	def test_different_users_can_share_a_subject(self):
		SubjectEntitlement.objects.create(user=self.user, subject=self.biology)
		SubjectEntitlement.objects.create(user=self.other_user, subject=self.biology)

		self.assertEqual(self.biology.entitlements.count(), 2)

	def test_subject_with_entitlements_is_protected_from_deletion(self):
		SubjectEntitlement.objects.create(user=self.user, subject=self.biology)

		with self.assertRaises(ProtectedError):
			self.biology.delete()

	def test_subject_entitlements_do_not_change_legacy_access_flags(self):
		SubjectEntitlement.objects.create(user=self.user, subject=self.biology)
		SubjectEntitlement.objects.create(user=self.user, subject=Subject.objects.get(code='gcse_science'))

		self.user.refresh_from_db()
		self.assertFalse(self.user.has_gcse_paid_access)
		self.assertFalse(self.user.has_alevel_paid_access)
		self.assertFalse(self.user.has_full_paid_access)
		self.assertEqual(self.user.entitlement.plan_type, UserEntitlement.PlanType.FREE)

	def test_gcse_entitlement_grants_only_gcse_subject_access(self):
		SubjectEntitlement.objects.create(user=self.user, subject=Subject.objects.get(code='gcse_science'))

		self.assertTrue(self.user.has_subject_access('gcse_science'))
		self.assertFalse(self.user.has_subject_access('alevel_biology'))

	def test_biology_entitlement_grants_only_biology_subject_access(self):
		SubjectEntitlement.objects.create(user=self.user, subject=self.biology)

		self.assertTrue(self.user.has_subject_access('alevel_biology'))
		self.assertFalse(self.user.has_subject_access('gcse_science'))

	def test_both_legacy_subject_entitlements_grant_access_independently(self):
		SubjectEntitlement.objects.create(user=self.user, subject=Subject.objects.get(code='gcse_science'))
		SubjectEntitlement.objects.create(user=self.user, subject=self.biology)

		self.assertTrue(self.user.has_subject_access('gcse_science'))
		self.assertTrue(self.user.has_subject_access('alevel_biology'))

	def test_chemistry_entitlement_does_not_unlock_other_subjects(self):
		SubjectEntitlement.objects.create(user=self.user, subject=self.chemistry)

		self.assertTrue(self.user.has_subject_access('alevel_chemistry'))
		self.assertFalse(self.user.has_subject_access('alevel_biology'))
		self.assertFalse(self.user.has_subject_access('alevel_physics'))

	def test_physics_entitlement_is_independent(self):
		physics = Subject.objects.get(code='alevel_physics')
		SubjectEntitlement.objects.create(user=self.user, subject=physics)

		self.assertTrue(self.user.has_subject_access('alevel_physics'))
		self.assertFalse(self.user.has_subject_access('alevel_biology'))
		self.assertFalse(self.user.has_subject_access('alevel_chemistry'))

	def test_lifetime_legacy_entitlements_do_not_unlock_new_subjects(self):
		SubjectEntitlement.objects.create(user=self.user, subject=Subject.objects.get(code='gcse_science'))
		SubjectEntitlement.objects.create(user=self.user, subject=self.biology)
		self.user.entitlement.lifetime_unlocked = True
		self.user.entitlement.save(update_fields=['lifetime_unlocked'])

		self.assertTrue(self.user.has_subject_access('gcse_science'))
		self.assertTrue(self.user.has_subject_access('alevel_biology'))
		self.assertFalse(self.user.has_subject_access('alevel_chemistry'))
		self.assertFalse(self.user.has_subject_access('alevel_physics'))

	def test_unknown_and_unentitled_subjects_return_false(self):
		self.assertFalse(self.user.has_subject_access('not_a_subject'))
		self.assertFalse(self.user.has_subject_access('alevel_biology'))

	def test_paid_plan_without_subject_entitlement_does_not_grant_subject_access(self):
		self.user.entitlement.plan_type = UserEntitlement.PlanType.PAID
		self.user.entitlement.save(update_fields=['plan_type'])

		self.assertFalse(self.user.has_subject_access('gcse_science'))
		self.assertFalse(self.user.has_subject_access('alevel_biology'))

	def test_subject_access_does_not_change_legacy_access_methods(self):
		self.user.has_gcse_paid_access = True
		self.user.save(update_fields=['has_gcse_paid_access'])
		SubjectEntitlement.objects.create(user=self.user, subject=self.chemistry)

		self.assertTrue(self.user.has_paid_access_for_qualification('GCSE_SCIENCE'))
		self.assertFalse(self.user.has_paid_access_for_qualification('ALEVEL_BIOLOGY'))
		self.assertFalse(self.user.has_full_paid_access)
		self.assertFalse(self.user.has_subject_access('alevel_biology'))

	def test_user_api_returns_sorted_subject_entitlements_and_legacy_fields(self):
		self.user.has_gcse_paid_access = True
		self.user.save(update_fields=['has_gcse_paid_access'])
		SubjectEntitlement.objects.create(user=self.user, subject=self.chemistry)
		SubjectEntitlement.objects.create(user=self.user, subject=Subject.objects.get(code='gcse_science'))
		SubjectEntitlement.objects.create(user=self.user, subject=self.biology)
		SubjectEntitlement.objects.create(user=self.other_user, subject=Subject.objects.get(code='alevel_physics'))
		self.client.force_authenticate(user=self.user)

		response = self.client.get(reverse('user-info'))

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			response.data['unlocked_subjects'],
			['alevel_biology', 'alevel_chemistry', 'gcse_science'],
		)
		self.assertEqual(
			set(response.data),
			{
				'id',
				'username',
				'email',
				'email_verified',
				'email_verified_at',
				'performance_tracking_start_date',
				'has_gcse_paid_access',
				'has_alevel_paid_access',
				'plan_type',
				'lifetime_unlocked',
				'has_unlimited_access',
				'questions_remaining_today',
				'unlocked_subjects',
			},
		)
		self.assertTrue(response.data['has_gcse_paid_access'])
		self.assertFalse(response.data['has_alevel_paid_access'])
		self.assertEqual(response.data['plan_type'], UserEntitlement.PlanType.PAID)
		self.assertFalse(response.data['lifetime_unlocked'])
		self.assertFalse(response.data['has_unlimited_access'])
		self.assertEqual(response.data['questions_remaining_today'], 2)

	def test_user_api_returns_empty_subject_list_for_free_user(self):
		self.client.force_authenticate(user=self.user)

		response = self.client.get(reverse('user-info'))

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['unlocked_subjects'], [])

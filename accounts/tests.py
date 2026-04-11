import re
from unittest.mock import patch
from types import SimpleNamespace
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from .models import CustomUser, UserEntitlement


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

	def test_create_checkout_session_rejects_unverified_user(self):
		self.user.email_verified = False
		self.user.save(update_fields=['email_verified'])

		self.client.force_authenticate(user=self.user)
		response = self.client.post(self.checkout_url, {'qualification': 'ALEVEL_BIOLOGY'}, format='json')

		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
		self.assertEqual(response.data['detail'], 'Please verify your email before starting checkout.')

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
			'type': 'checkout.session.completed',
			'data': {
				'object': {
					'id': 'cs_live_both',
					'customer': 'cus_123',
					'subscription': 'sub_123',
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

	@patch('accounts.views.construct_stripe_event')
	def test_subscription_deleted_webhook_removes_both_access_flags_for_combined_plan(self, mock_construct_event):
		entitlement = self.user.entitlement
		self.user.has_gcse_paid_access = True
		self.user.has_alevel_paid_access = True
		self.user.save(update_fields=['has_gcse_paid_access', 'has_alevel_paid_access'])
		entitlement.plan_type = UserEntitlement.PlanType.PAID
		entitlement.stripe_customer_id = 'cus_123'
		entitlement.stripe_subscription_id = 'sub_123'
		entitlement.save(update_fields=['plan_type', 'stripe_customer_id', 'stripe_subscription_id'])

		mock_construct_event.return_value = {
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
		self.assertFalse(self.user.has_gcse_paid_access)
		self.assertFalse(self.user.has_alevel_paid_access)

	@patch('accounts.views.construct_stripe_event')
	def test_checkout_completed_webhook_promotes_user_to_paid(self, mock_construct_event):
		mock_construct_event.return_value = {
			'type': 'checkout.session.completed',
			'data': {
				'object': {
					'id': 'cs_live_123',
					'customer': 'cus_123',
					'subscription': 'sub_123',
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

	@patch('accounts.views.construct_stripe_event')
	def test_subscription_deleted_webhook_downgrades_user_to_free(self, mock_construct_event):
		entitlement = self.user.entitlement
		self.user.has_gcse_paid_access = True
		self.user.save(update_fields=['has_gcse_paid_access'])
		entitlement.plan_type = UserEntitlement.PlanType.PAID
		entitlement.stripe_customer_id = 'cus_123'
		entitlement.stripe_subscription_id = 'sub_123'
		entitlement.save(update_fields=['plan_type', 'stripe_customer_id', 'stripe_subscription_id'])

		mock_construct_event.return_value = {
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
		self.assertEqual(entitlement.plan_type, UserEntitlement.PlanType.FREE)
		self.assertFalse(self.user.has_gcse_paid_access)


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

import re
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from .models import CustomUser


@override_settings(
	EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
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

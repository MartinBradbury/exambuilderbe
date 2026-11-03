import json
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import CustomUser, Subject
from examquestions.admin import PENDING_IMPORT_SESSION_KEY
from examquestions.models import ALevelSubCategory, ALevelSubTopic, ALevelTopic


def sample(**overrides):
	data = {
		'subject': 'alevel_chemistry',
		'exam_board': 'AQA',
		'specification': '',
		'source': {'name': 'Fictional specification'},
		'topics': [
			{
				'topic': 'Fictional Topic One',
				'subtopics': [{'title': 'Fictional Subtopic', 'subcategories': ['Fictional Category']}],
			},
			{'topic': 'Fictional Topic Two'},
		],
	}
	data.update(overrides)
	return data


class AdminCurriculumImportTests(TestCase):
	def setUp(self):
		self.url = reverse('admin:examquestions_aleveltopic_import')
		self.changelist = reverse('admin:examquestions_aleveltopic_changelist')
		self.superuser = CustomUser.objects.create_superuser(
			email='super@example.com', username='super-user', password='testpass123'
		)
		self.client.force_login(self.superuser)

	def counts(self):
		return (ALevelTopic.objects.count(), ALevelSubTopic.objects.count(), ALevelSubCategory.objects.count())

	def upload(self, data=None, raw=None, name='curriculum.json', client=None):
		body = raw if raw is not None else json.dumps(data).encode('utf-8')
		return (client or self.client).post(self.url, {
			'action': 'preview', 'file': SimpleUploadedFile(name, body, content_type='application/json'),
		})

	def token(self, client=None):
		return (client or self.client).session[PENDING_IMPORT_SESSION_KEY]['token']

	def confirm(self, token=None, client=None):
		client = client or self.client
		return client.post(self.url, {'action': 'confirm', 'token': token if token is not None else self.token(client)})

	def messages(self, response):
		return [str(m) for m in get_messages(response.wsgi_request)]

	def make_staff(self, with_permission):
		user = CustomUser.objects.create_user(
			email=f'staff{with_permission}@example.com', username=f'staff-{with_permission}', password='testpass123',
			is_staff=True,
		)
		if with_permission:
			user.user_permissions.add(Permission.objects.get(codename='add_aleveltopic'))
		return user

	# permissions
	def test_anonymous_and_non_staff_cannot_access(self):
		self.client.logout()
		self.assertEqual(self.client.get(self.url).status_code, 302)
		self.assertEqual(self.upload(sample()).status_code, 302)
		member = CustomUser.objects.create_user(email='m@example.com', username='member', password='testpass123')
		self.client.force_login(member)
		self.assertEqual(self.client.get(self.url).status_code, 302)
		self.assertEqual(self.upload(sample()).status_code, 302)
		self.assertFalse(ALevelTopic.objects.filter(topic='Fictional Topic One').exists())

	def test_staff_without_add_permission_forbidden(self):
		self.client.force_login(self.make_staff(False))
		self.assertEqual(self.client.get(self.url).status_code, 403)
		self.assertEqual(self.upload(sample()).status_code, 403)
		self.assertEqual(self.client.post(self.url, {'action': 'confirm', 'token': 'x'}).status_code, 403)
		self.assertFalse(ALevelTopic.objects.filter(topic='Fictional Topic One').exists())

	def test_staff_with_add_permission_and_superuser_allowed(self):
		self.assertEqual(self.client.get(self.url).status_code, 200)
		self.client.force_login(self.make_staff(True))
		self.assertEqual(self.client.get(self.url).status_code, 200)
		self.assertEqual(self.upload(sample()).status_code, 200)

	def test_changelist_shows_import_link(self):
		response = self.client.get(self.changelist)
		self.assertContains(response, 'Import curriculum JSON')
		self.assertContains(response, self.url)

	# preview
	def test_valid_upload_previews_without_writing(self):
		before = self.counts()
		response = self.upload(sample())
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'alevel_chemistry')
		self.assertContains(response, 'AQA')
		self.assertContains(response, 'Confirm import')
		rows = {label: (new, reused) for label, new, reused in response.context['preview']['rows']}
		self.assertEqual(rows, {'topics': (2, 0), 'subtopics': (1, 0), 'subcategories': (1, 0)})
		self.assertEqual(self.counts(), before)

	def test_get_never_imports(self):
		self.upload(sample())
		before = self.counts()
		self.client.get(self.url, {'action': 'confirm', 'token': self.token()})
		self.assertEqual(self.counts(), before)

	# confirm
	def test_confirmation_imports_and_redirects(self):
		self.upload(sample())
		response = self.confirm()
		self.assertRedirects(response, self.changelist, fetch_redirect_response=False)
		self.assertIn('2 topics created', ' '.join(self.messages(response)))
		topic = ALevelTopic.objects.get(subject__code='alevel_chemistry', topic='Fictional Topic One', exam_board='AQA', specification='')
		self.assertEqual(topic.subtopics.get().subcategories.get().title, 'Fictional Category')

	def test_repeat_import_is_idempotent(self):
		self.upload(sample())
		self.confirm()
		after_first = self.counts()
		response = self.upload(sample())
		rows = {label: (new, reused) for label, new, reused in response.context['preview']['rows']}
		self.assertEqual(rows['topics'], (0, 2))
		response = self.confirm()
		self.assertEqual(self.counts(), after_first)
		self.assertIn('0 topics created (2 reused)', ' '.join(self.messages(response)))

	def test_existing_biology_curriculum_unchanged(self):
		bio = Subject.objects.get(code='alevel_biology')
		topic = ALevelTopic.objects.create(subject=bio, topic='Existing Bio', exam_board='AQA')
		sub = ALevelSubTopic.objects.create(topic=topic, title='Existing Sub')
		snapshot = list(ALevelTopic.objects.filter(subject=bio).values())
		self.upload(sample())
		self.confirm()
		self.upload(sample(subject='alevel_biology', topics=[{'topic': 'Existing Bio', 'subtopics': [{'title': 'Existing Sub'}, {'title': 'Added'}]}]))
		self.confirm()
		self.assertEqual(list(ALevelTopic.objects.filter(subject=bio).values()), snapshot)
		self.assertTrue(ALevelSubTopic.objects.filter(pk=sub.pk, title='Existing Sub').exists())
		self.assertEqual(ALevelSubTopic.objects.filter(topic=topic).count(), 2)

	# invalid uploads
	def test_invalid_files_rejected_without_writes(self):
		before = self.counts()
		cases = {
			'malformed': {'raw': b'{not json'},
			'not utf8': {'raw': b'\xff\xfe\x00bad'},
			'board': {'data': sample(exam_board='WJEC')},
			'spec': {'data': sample(specification='A')},
			'edexcel spec': {'data': sample(exam_board='EDEXCEL', specification='')},
			'subject': {'data': sample(subject='gcse_science')},
			'duplicate': {'data': sample(topics=[{'topic': 'Same'}, {'topic': 'Same'}])},
			'unknown field': {'data': sample(extra=1)},
			'wrong extension': {'data': sample(), 'name': 'curriculum.txt'},
		}
		for label, kwargs in cases.items():
			with self.subTest(label):
				response = self.upload(kwargs.get('data'), raw=kwargs.get('raw'), name=kwargs.get('name', 'c.json'))
				self.assertEqual(response.status_code, 200)
				self.assertTrue(response.context['form'].errors)
				self.assertIsNone(response.context['preview'])
				self.assertNotContains(response, 'Confirm import')
				self.assertNotIn('Traceback', response.content.decode())
		self.assertEqual(self.counts(), before)

	@override_settings(CURRICULUM_IMPORT_MAX_BYTES=200)
	def test_oversized_upload_rejected(self):
		response = self.upload(sample())
		self.assertContains(response, 'too large')
		self.assertNotIn(PENDING_IMPORT_SESSION_KEY, self.client.session)

	def test_default_limit_is_512_kb(self):
		from examquestions.admin import DEFAULT_IMPORT_MAX_BYTES
		self.assertEqual(DEFAULT_IMPORT_MAX_BYTES, 524288)
		padding = 'x' * (512 * 1024)
		response = self.upload(sample(source={'name': padding}))
		self.assertContains(response, 'too large (maximum 512 KB)')
		self.assertNotIn(PENDING_IMPORT_SESSION_KEY, self.client.session)
		self.assertEqual(self.counts(), (0, 0, 0))

	def test_file_just_under_default_limit_accepted(self):
		body = json.dumps(sample()).encode('utf-8')
		body += b' ' * (512 * 1024 - len(body))
		response = self.upload(raw=body)
		self.assertNotContains(response, 'too large')
		self.assertIn(PENDING_IMPORT_SESSION_KEY, self.client.session)

	def test_validation_errors_truncated_to_fifty(self):
		topics = [{'topic': 'Duplicate Topic'} for _ in range(70)]
		response = self.upload(sample(topics=topics))
		self.assertContains(response, 'Showing the first 50 of')
		self.assertContains(response, 'omitted')
		self.assertEqual(response.content.decode().count('Duplicate Topic'), 50)
		self.assertNotIn(PENDING_IMPORT_SESSION_KEY, self.client.session)

	def test_few_validation_errors_not_truncated(self):
		topics = [{'topic': 'Duplicate Topic'} for _ in range(3)]
		response = self.upload(sample(topics=topics))
		self.assertContains(response, 'Duplicate Topic')
		self.assertNotContains(response, 'omitted')

	# token security
	def test_confirm_without_preview_or_with_forged_token_rejected(self):
		before = self.counts()
		self.assertRedirects(self.confirm(token='forged'), self.url, fetch_redirect_response=False)
		self.upload(sample())
		response = self.confirm(token='forged')
		self.assertRedirects(response, self.url, fetch_redirect_response=False)
		self.assertIn('invalid or has expired', ' '.join(self.messages(response)))
		self.assertEqual(self.counts(), before)
		# the pending preview is consumed by the failed attempt
		self.assertNotIn(PENDING_IMPORT_SESSION_KEY, self.client.session)

	def test_confirmation_cannot_be_replayed(self):
		self.upload(sample())
		token = self.token()
		self.confirm(token=token)
		after = self.counts()
		ALevelTopic.objects.filter(topic='Fictional Topic Two').delete()
		replay = self.confirm(token=token)
		self.assertRedirects(replay, self.url, fetch_redirect_response=False)
		self.assertEqual(self.counts()[0], after[0] - 1)

	def test_other_user_cannot_use_token(self):
		self.upload(sample())
		token = self.token()
		other = CustomUser.objects.create_superuser(email='other@example.com', username='other-user', password='testpass123')
		other_client = self.client_class()
		other_client.force_login(other)
		before = self.counts()
		response = self.confirm(token=token, client=other_client)
		self.assertRedirects(response, self.url, fetch_redirect_response=False)
		self.assertEqual(self.counts(), before)

	def test_token_bound_to_user_even_if_session_shared(self):
		self.upload(sample())
		session = self.client.session
		session[PENDING_IMPORT_SESSION_KEY]['user_id'] = self.superuser.pk + 999
		session.save()
		before = self.counts()
		self.assertRedirects(self.confirm(), self.url, fetch_redirect_response=False)
		self.assertEqual(self.counts(), before)

	def test_expired_token_rejected(self):
		self.upload(sample())
		token = self.token()
		with patch('examquestions.admin.time') as fake_time:
			fake_time.time.return_value = 10 ** 11
			response = self.confirm(token=token)
		self.assertRedirects(response, self.url, fetch_redirect_response=False)
		self.assertFalse(ALevelTopic.objects.filter(topic='Fictional Topic One').exists())

	def test_tampered_pending_data_rejected(self):
		self.upload(sample())
		session = self.client.session
		session[PENDING_IMPORT_SESSION_KEY]['data']['topics'][0]['topic'] = 'Tampered'
		session.save()
		self.confirm()
		self.assertFalse(ALevelTopic.objects.filter(topic__in=['Tampered', 'Fictional Topic One']).exists())

	def test_confirmation_revalidates_database_state(self):
		self.upload(sample())
		chem = Subject.objects.get(code='alevel_chemistry')
		ALevelTopic.objects.create(subject=chem, topic='fictional topic one', exam_board='AQA')
		before = self.counts()
		response = self.confirm()
		self.assertRedirects(response, self.url, fetch_redirect_response=False)
		self.assertIn('differs from', ' '.join(self.messages(response)))
		self.assertEqual(self.counts(), before)

	def test_failed_confirmation_rolls_back(self):
		self.upload(sample())
		before = self.counts()
		calls = {'n': 0}
		original = ALevelSubTopic.full_clean

		def failing(instance, *args, **kwargs):
			from django.core.exceptions import ValidationError
			raise ValidationError('forced')

		with patch.object(ALevelSubTopic, 'full_clean', failing):
			response = self.confirm()
		self.assertRedirects(response, self.url, fetch_redirect_response=False)
		self.assertEqual(self.counts(), before)
		self.assertIn('nothing was written', ' '.join(self.messages(response)))

	def test_staff_with_permission_can_import_but_not_without(self):
		self.client.force_login(self.make_staff(True))
		self.upload(sample())
		self.assertEqual(self.confirm().status_code, 302)
		self.assertTrue(ALevelTopic.objects.filter(topic='Fictional Topic One').exists())

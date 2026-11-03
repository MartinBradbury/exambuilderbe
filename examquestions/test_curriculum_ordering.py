import json

from django.contrib.admin.sites import site
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.urls import reverse

from accounts.models import CustomUser, Subject
from examquestions.curriculum_ordering import SORT_KEY_MAX_LENGTH, natural_sort_key
from examquestions.models import ALevelSubTopic, ALevelTopic
from examquestions.services.curriculum_import import apply_import


def ordered(titles):
	return sorted(titles, key=lambda t: (natural_sort_key(t), t))


class NaturalSortKeyTests(SimpleTestCase):
	def test_topic_numbers_sort_numerically(self):
		self.assertEqual(
			ordered(['3.10 J', '3.2 B', '3.1 A', '3.11 K', '3.3 C']),
			['3.1 A', '3.2 B', '3.3 C', '3.10 J', '3.11 K'],
		)

	def test_nested_numbers_sort_numerically(self):
		self.assertEqual(
			ordered(['3.1.10', '3.1.2', '3.1.1', '3.1', '3.2', '3.10', '3.1.1.1']),
			['3.1', '3.1.1', '3.1.1.1', '3.1.2', '3.1.10', '3.2', '3.10'],
		)

	def test_parent_with_text_precedes_its_children(self):
		self.assertLess(natural_sort_key('3.1 Foo'), natural_sort_key('3.1.1 Bar'))

	def test_unnumbered_after_numbered_and_alphabetical(self):
		self.assertEqual(
			ordered(['Zebra', '10 Ten', 'apple', '2 Two', 'Mango']),
			['2 Two', '10 Ten', 'apple', 'Mango', 'Zebra'],
		)

	def test_whitespace_and_case_normalised(self):
		self.assertEqual(natural_sort_key('  3.2   Atomic  STRUCTURE '), natural_sort_key('3.2 atomic structure'))
		self.assertEqual(natural_sort_key('  Alpha   Beta'), natural_sort_key('alpha beta'))

	def test_leading_zeros_equal_value(self):
		self.assertEqual(natural_sort_key('03.01 X'), natural_sort_key('3.1 X'))

	def test_equal_prefix_orders_by_remainder_then_title(self):
		self.assertEqual(ordered(['3.1 b', '3.1 A']), ['3.1 A', '3.1 b'])
		self.assertEqual(ordered(['3.1 Same', '3.1 same']), ['3.1 Same', '3.1 same'])

	def test_malformed_prefixes_are_unnumbered(self):
		too_long = '1234567.1 Big'
		too_deep = '.'.join(['1'] * 9) + ' Deep'
		self.assertTrue(natural_sort_key(too_long).startswith('1'))
		self.assertTrue(natural_sort_key(too_deep).startswith('1'))
		self.assertTrue(natural_sort_key('3.1 ok').startswith('0'))

	def test_trailing_dot_and_empty(self):
		self.assertEqual(natural_sort_key('3.'), natural_sort_key('3 .'.replace(' ', '', 1)))
		self.assertEqual(natural_sort_key(''), '1')
		self.assertEqual(natural_sort_key(None), '1')

	def test_key_never_exceeds_max_length(self):
		worst = '.'.join(['9'] * 8) + 'x' * 240
		self.assertLessEqual(len(natural_sort_key(worst[:255])), SORT_KEY_MAX_LENGTH)
		self.assertLessEqual(len(natural_sort_key('x' * 255)), SORT_KEY_MAX_LENGTH)

	def test_distinct_numeric_prefixes_do_not_collide(self):
		keys = {natural_sort_key(t) for t in ['1.1', '11', '1.11', '11.1', '1.1.1', '111']}
		self.assertEqual(len(keys), 6)


class ALevelTopicSortKeyModelTests(TestCase):
	def setUp(self):
		self.chem = Subject.objects.get(code='alevel_chemistry')
		self.phys = Subject.objects.get(code='alevel_physics')

	def make(self, topic, subject=None, board='AQA', spec=''):
		return ALevelTopic.objects.create(subject=subject or self.phys, topic=topic, exam_board=board, specification=spec)

	def test_save_populates_sort_key_and_updates_on_rename(self):
		t = self.make('3.2 Thing')
		self.assertEqual(t.sort_key, natural_sort_key('3.2 Thing'))
		t.topic = '3.10 Thing'
		t.save()
		t.refresh_from_db()
		self.assertEqual(t.sort_key, natural_sort_key('3.10 Thing'))
		t.topic = '3.3 Thing'
		t.save(update_fields=['topic'])
		t.refresh_from_db()
		self.assertEqual(t.sort_key, natural_sort_key('3.3 Thing'))

	def test_default_ordering_natural_within_group(self):
		for name in ['3.10 J', '3.2 B', '3.1 A', 'Unnumbered', '3.11 K']:
			self.make(name)
		self.make('1.1 Other board', board='OCR')
		self.make('9.9 Other subject', subject=self.chem)
		physics_aqa = [t.topic for t in ALevelTopic.objects.filter(subject=self.phys, exam_board='AQA')]
		self.assertEqual(physics_aqa, ['3.1 A', '3.2 B', '3.10 J', '3.11 K', 'Unnumbered'])
		grouping = [(t.subject.code, t.exam_board) for t in ALevelTopic.objects.all()]
		self.assertEqual(grouping, sorted(grouping))

	def test_edexcel_specifications_stay_grouped(self):
		self.make('3.10 X', board='EDEXCEL', spec='Spec B')
		self.make('3.2 Y', board='EDEXCEL', spec='Spec B')
		self.make('3.20 Z', board='EDEXCEL', spec='Spec A')
		result = [(t.specification, t.topic) for t in ALevelTopic.objects.filter(exam_board='EDEXCEL')]
		self.assertEqual(result, [('Spec A', '3.20 Z'), ('Spec B', '3.2 Y'), ('Spec B', '3.10 X')])

	def test_subtopic_ordering_unchanged(self):
		self.assertEqual(
			ALevelSubTopic._meta.ordering,
			['topic__subject__code', 'topic__exam_board', 'topic__topic', 'title'],
		)


class ALevelTopicSortKeyAdminAndImportTests(TestCase):
	def setUp(self):
		self.superuser = CustomUser.objects.create_superuser(
			email='sk@example.com', username='sk-user', password='testpass123'
		)
		self.client.force_login(self.superuser)
		self.phys = Subject.objects.get(code='alevel_physics')

	def test_admin_ordering_uses_sort_key(self):
		model_admin = site._registry[ALevelTopic]
		self.assertEqual(
			tuple(model_admin.ordering),
			('subject__code', 'exam_board', 'specification', 'sort_key', 'topic'),
		)

	def test_admin_changelist_natural_order(self):
		for name in ['3.10 J', '3.2 B', '3.1 A']:
			ALevelTopic.objects.create(subject=self.phys, topic=name, exam_board='AQA')
		response = self.client.get(reverse('admin:examquestions_aleveltopic_changelist'))
		self.assertEqual(response.status_code, 200)
		content = response.content.decode()
		positions = [content.index(n) for n in ['3.1 A', '3.2 B', '3.10 J']]
		self.assertEqual(positions, sorted(positions))

	def test_admin_add_populates_sort_key(self):
		response = self.client.post(reverse('admin:examquestions_aleveltopic_add'), {
			'subject': self.phys.pk, 'topic': '3.12 Added', 'exam_board': 'AQA', 'specification': '',
		})
		self.assertEqual(response.status_code, 302)
		topic = ALevelTopic.objects.get(topic='3.12 Added')
		self.assertEqual(topic.sort_key, natural_sort_key('3.12 Added'))

	def test_importer_populates_sort_key_and_is_idempotent(self):
		data = {
			'subject': 'alevel_physics', 'exam_board': 'AQA', 'specification': '',
			'topics': [{'topic': '3.10 Ten'}, {'topic': '3.2 Two'}, {'topic': 'Plain'}],
		}
		apply_import(json.loads(json.dumps(data)))
		first = list(ALevelTopic.objects.values_list('pk', 'topic', 'sort_key'))
		counts = apply_import(json.loads(json.dumps(data)))
		self.assertEqual(list(ALevelTopic.objects.values_list('pk', 'topic', 'sort_key')), first)
		self.assertEqual(counts['topics'], [0, 3])
		self.assertEqual(
			[t.topic for t in ALevelTopic.objects.filter(subject=self.phys)],
			['3.2 Two', '3.10 Ten', 'Plain'],
		)

	def test_admin_upload_import_populates_sort_key(self):
		data = {'subject': 'alevel_chemistry', 'exam_board': 'OCR', 'specification': '',
				'topics': [{'topic': '3.1.10 B'}, {'topic': '3.1.2 A'}]}
		url = reverse('admin:examquestions_aleveltopic_import')
		self.client.post(url, {'action': 'preview', 'file': SimpleUploadedFile('c.json', json.dumps(data).encode())})
		token = self.client.session['alevel_curriculum_import_pending']['token']
		self.client.post(url, {'action': 'confirm', 'token': token})
		topics = list(ALevelTopic.objects.filter(subject__code='alevel_chemistry'))
		self.assertEqual([t.topic for t in topics], ['3.1.2 A', '3.1.10 B'])
		self.assertTrue(all(t.sort_key for t in topics))


class BackfillSortKeyMigrationTests(TransactionTestCase):
	before = [('examquestions', '0017_questionsession_qualification_chemistry_physics')]
	after = [('examquestions', '0018_aleveltopic_sort_key')]

	def test_backfill_preserves_records_and_sets_keys(self):
		executor = MigrationExecutor(connection)
		executor.migrate(self.before)
		apps = executor.loader.project_state(self.before).apps
		Subject_ = apps.get_model('accounts', 'Subject')
		Topic = apps.get_model('examquestions', 'ALevelTopic')
		Sub = apps.get_model('examquestions', 'ALevelSubTopic')
		subject, _ = Subject_.objects.get_or_create(
			code='alevel_physics', defaults={'name': 'Physics', 'qualification': 'A_LEVEL', 'is_active': True},
		)
		t10 = Topic.objects.create(subject=subject, topic='3.10 Ten', exam_board='AQA')
		t2 = Topic.objects.create(subject=subject, topic='3.2 Two', exam_board='AQA')
		plain = Topic.objects.create(subject=subject, topic='Plain', exam_board='EDEXCEL', specification='Spec A')
		sub = Sub.objects.create(topic=t10, title='Child')
		ids = {t.pk: (t.topic, t.exam_board, t.specification) for t in (t10, t2, plain)}

		executor = MigrationExecutor(connection)
		executor.migrate(self.after)
		apps = executor.loader.project_state(self.after).apps
		Topic = apps.get_model('examquestions', 'ALevelTopic')
		Sub = apps.get_model('examquestions', 'ALevelSubTopic')

		self.assertEqual(Topic.objects.count(), 3)
		for pk, (topic, board, spec) in ids.items():
			row = Topic.objects.get(pk=pk)
			self.assertEqual((row.topic, row.exam_board, row.specification), (topic, board, spec))
			self.assertEqual(row.sort_key, natural_sort_key(topic))
		self.assertEqual(Sub.objects.get(pk=sub.pk).topic_id, t10.pk)
		self.assertEqual(
			[t.topic for t in Topic.objects.filter(exam_board='AQA').order_by('sort_key')],
			['3.2 Two', '3.10 Ten'],
		)


class FrozenMigrationAlgorithmParityTests(SimpleTestCase):
	TITLES = [
		'', '   ', 'Zebra', 'apple Mango', '1', '3.1', '3.2', '3.10', '3.11', '3.1.1', '3.1.2', '3.1.10',
		'3.1.1.1', '03.01 Padded', '  3.2   Atomic  STRUCTURE ', '3.1 Foo', '3.1 foo', '1234567.1 Big',
		'.'.join(['1'] * 8) + ' Max depth', '.'.join(['1'] * 9) + ' Too deep', '3. Trailing dot',
		'Éclair 3.1', '3.1 Über', 'x' * 255, '9.' * 7 + '9' + 'y' * 240,
	]

	def test_frozen_migration_function_matches_runtime(self):
		from importlib import import_module
		migration = import_module('examquestions.migrations.0018_aleveltopic_sort_key')
		for title in self.TITLES:
			self.assertEqual(migration.frozen_natural_sort_key(title), natural_sort_key(title), repr(title))

	def test_migration_does_not_import_runtime_module(self):
		from importlib import import_module
		migration = import_module('examquestions.migrations.0018_aleveltopic_sort_key')
		self.assertFalse(hasattr(migration, 'natural_sort_key'))


class ALevelTopicSortKeyPersistenceTests(TestCase):
	def test_admin_edit_title_updates_sort_key(self):
		user = CustomUser.objects.create_superuser(email='ed@example.com', username='ed-user', password='testpass123')
		self.client.force_login(user)
		phys = Subject.objects.get(code='alevel_physics')
		topic = ALevelTopic.objects.create(subject=phys, topic='3.2 Old', exam_board='AQA')
		response = self.client.post(reverse('admin:examquestions_aleveltopic_change', args=[topic.pk]), {
			'subject': phys.pk, 'topic': '3.10 New', 'exam_board': 'AQA', 'specification': '',
		})
		self.assertEqual(response.status_code, 302)
		topic.refresh_from_db()
		self.assertEqual(topic.topic, '3.10 New')
		self.assertEqual(topic.sort_key, natural_sort_key('3.10 New'))

	def test_update_fields_without_topic_keeps_key_consistent(self):
		phys = Subject.objects.get(code='alevel_physics')
		topic = ALevelTopic.objects.create(subject=phys, topic='3.2 Old', exam_board='AQA')
		topic.specification = ''
		topic.save(update_fields=['specification'])
		topic.refresh_from_db()
		self.assertEqual(topic.sort_key, natural_sort_key('3.2 Old'))

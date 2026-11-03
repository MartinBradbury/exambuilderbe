from importlib import import_module
from types import SimpleNamespace

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


MIGRATE_FROM = [('examquestions', '0013_alevel_curriculum_models')]
MIGRATE_TO = [('examquestions', '0014_copy_alevel_biology_curriculum')]


class CopyALevelBiologyCurriculumMigrationTests(TransactionTestCase):
	def setUp(self):
		super().setUp()
		executor = MigrationExecutor(connection)
		executor.migrate(MIGRATE_FROM)
		self.apps = executor.loader.project_state(MIGRATE_FROM).apps
		# TransactionTestCase flushes seeded data, so recreate the subject the migration needs.
		self.apps.get_model('accounts', 'Subject').objects.get_or_create(
			code='alevel_biology',
			defaults={'name': 'Biology', 'qualification': 'A_LEVEL', 'is_active': True},
		)
		self.migration = import_module('examquestions.migrations.0014_copy_alevel_biology_curriculum')
		self._create_source_data()

	def tearDown(self):
		executor = MigrationExecutor(connection)
		executor.migrate(executor.loader.graph.leaf_nodes())
		super().tearDown()

	def _create_source_data(self):
		BiologyTopic = self.apps.get_model('examquestions', 'BiologyTopic')
		BiologySubTopic = self.apps.get_model('examquestions', 'BiologySubTopic')
		BiologySubCategory = self.apps.get_model('examquestions', 'BiologySubCategory')

		self.expected = {}
		for topic_name, board, spec in (
			('Cells', 'OCR', ''),
			('Cells', 'AQA', ''),
			('Cells', 'EDEXCEL', 'Spec A'),
			('Exchange', 'OCR', 'H020'),
		):
			topic = BiologyTopic.objects.create(topic=topic_name, exam_board=board, specification=spec)
			for sub_index in range(2):
				subtopic = BiologySubTopic.objects.create(topic=topic, title=f'{topic_name} sub {sub_index}')
				for cat_index in range(2):
					BiologySubCategory.objects.create(subtopic=subtopic, title=f'cat {cat_index}')

	def _migrate_forward(self):
		executor = MigrationExecutor(connection)
		executor.migrate(MIGRATE_TO)
		return executor.loader.project_state(MIGRATE_TO).apps

	def _hierarchy(self, apps, destination):
		if destination:
			Topic = apps.get_model('examquestions', 'ALevelTopic')
			SubTopic = apps.get_model('examquestions', 'ALevelSubTopic')
			SubCategory = apps.get_model('examquestions', 'ALevelSubCategory')
			topic_filter = {'subject__code': 'alevel_biology'}
		else:
			Topic = apps.get_model('examquestions', 'BiologyTopic')
			SubTopic = apps.get_model('examquestions', 'BiologySubTopic')
			SubCategory = apps.get_model('examquestions', 'BiologySubCategory')
			topic_filter = {}
		return {
			(topic.topic, topic.exam_board, topic.specification): {
				subtopic.title: sorted(
					SubCategory.objects.filter(subtopic=subtopic).values_list('title', flat=True)
				)
				for subtopic in SubTopic.objects.filter(topic=topic)
			}
			for topic in Topic.objects.filter(**topic_filter)
		}

	def test_copy_creates_complete_hierarchy_with_exact_board_and_specification(self):
		apps = self._migrate_forward()

		self.assertEqual(apps.get_model('examquestions', 'ALevelTopic').objects.count(), 4)
		self.assertEqual(apps.get_model('examquestions', 'ALevelSubTopic').objects.count(), 8)
		self.assertEqual(apps.get_model('examquestions', 'ALevelSubCategory').objects.count(), 16)
		self.assertEqual(self._hierarchy(apps, True), self._hierarchy(apps, False))
		self.assertIn(('Cells', 'EDEXCEL', 'Spec A'), self._hierarchy(apps, True))

	def test_copy_is_repeatable_without_duplicates(self):
		apps = self._migrate_forward()
		counts = [
			apps.get_model('examquestions', name).objects.count()
			for name in ('ALevelTopic', 'ALevelSubTopic', 'ALevelSubCategory')
		]

		self.migration.copy_alevel_biology_curriculum(apps, SimpleNamespace(connection=connection))

		self.assertEqual(
			counts,
			[
				apps.get_model('examquestions', name).objects.count()
				for name in ('ALevelTopic', 'ALevelSubTopic', 'ALevelSubCategory')
			],
		)

	def test_existing_matching_destination_records_are_reused(self):
		Subject = self.apps.get_model('accounts', 'Subject')
		ALevelTopic = self.apps.get_model('examquestions', 'ALevelTopic')
		existing = ALevelTopic.objects.create(
			subject=Subject.objects.get(code='alevel_biology'),
			topic='Cells',
			exam_board='OCR',
			specification='',
		)

		apps = self._migrate_forward()

		topics = apps.get_model('examquestions', 'ALevelTopic').objects
		self.assertEqual(topics.count(), 4)
		self.assertTrue(topics.filter(pk=existing.pk, topic='Cells').exists())

	def test_source_records_and_sessions_are_preserved(self):
		CustomUser = self.apps.get_model('accounts', 'CustomUser')
		QuestionSession = self.apps.get_model('examquestions', 'QuestionSession')
		BiologyTopic = self.apps.get_model('examquestions', 'BiologyTopic')
		user = CustomUser.objects.create(username='migration-user', email='migration@example.com', password='!')
		source_topic = BiologyTopic.objects.get(topic='Exchange')
		session = QuestionSession.objects.create(user=user, topic=source_topic, exam_board='OCR', number_of_questions=5)
		before = self._hierarchy(self.apps, False)

		apps = self._migrate_forward()

		self.assertEqual(self._hierarchy(apps, False), before)
		self.assertEqual(apps.get_model('examquestions', 'BiologySubCategory').objects.count(), 16)
		migrated_session = apps.get_model('examquestions', 'QuestionSession').objects.get(pk=session.pk)
		self.assertEqual(migrated_session.topic_id, source_topic.pk)

	def test_missing_biology_subject_fails_clearly(self):
		self.apps.get_model('accounts', 'Subject').objects.filter(code='alevel_biology').delete()

		with self.assertRaisesMessage(self.migration.CurriculumCopyError, "'alevel_biology' does not exist"):
			self.migration.copy_alevel_biology_curriculum(self.apps, SimpleNamespace(connection=connection))

		self.assertFalse(self.apps.get_model('accounts', 'Subject').objects.filter(code='alevel_biology').exists())
		self.apps.get_model('accounts', 'Subject').objects.create(
			code='alevel_biology', name='Biology', qualification='A_LEVEL', is_active=True
		)

	def test_reverse_migration_keeps_copied_data(self):
		apps = self._migrate_forward()
		migration = MigrationExecutor(connection).loader.get_migration(*MIGRATE_TO[0])
		self.assertIs(migration.operations[0].reverse_code, self.migration.migrations.RunPython.noop)

		executor = MigrationExecutor(connection)
		executor.migrate(MIGRATE_FROM)
		apps = executor.loader.project_state(MIGRATE_FROM).apps

		self.assertEqual(apps.get_model('examquestions', 'ALevelTopic').objects.count(), 4)

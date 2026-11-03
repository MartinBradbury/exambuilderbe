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


MIGRATE_0015 = [('examquestions', '0015_questionsession_alevel_curriculum_fks')]
MIGRATE_0016 = [('examquestions', '0016_backfill_questionsession_alevel_links')]


class BackfillQuestionSessionALevelLinksMigrationTests(TransactionTestCase):
	def setUp(self):
		super().setUp()
		executor = MigrationExecutor(connection)
		executor.migrate(MIGRATE_0015)
		self.apps = executor.loader.project_state(MIGRATE_0015).apps
		self.migration = import_module('examquestions.migrations.0016_backfill_questionsession_alevel_links')
		Subject = self.apps.get_model('accounts', 'Subject')
		self.subject, _ = Subject.objects.get_or_create(
			code='alevel_biology',
			defaults={'name': 'Biology', 'qualification': 'A_LEVEL', 'is_active': True},
		)
		self.chemistry, _ = Subject.objects.get_or_create(
			code='alevel_chemistry',
			defaults={'name': 'Chemistry', 'qualification': 'A_LEVEL', 'is_active': True},
		)
		self.user = self.apps.get_model('accounts', 'CustomUser').objects.create(
			username='backfill-user', email='backfill@example.com', password='!'
		)
		self.Session = self.apps.get_model('examquestions', 'QuestionSession')
		self.BioTopic = self.apps.get_model('examquestions', 'BiologyTopic')
		self.BioSub = self.apps.get_model('examquestions', 'BiologySubTopic')
		self.BioCat = self.apps.get_model('examquestions', 'BiologySubCategory')
		self.ATopic = self.apps.get_model('examquestions', 'ALevelTopic')
		self.ASub = self.apps.get_model('examquestions', 'ALevelSubTopic')
		self.ACat = self.apps.get_model('examquestions', 'ALevelSubCategory')

		# Offset destination primary keys so they never coincide with source keys.
		offset_topic = self.ATopic.objects.create(subject=self.chemistry, topic='Offset', exam_board='OCR')
		offset_sub = self.ASub.objects.create(topic=offset_topic, title='Offset')
		self.ACat.objects.create(subtopic=offset_sub, title='Offset')
		self.ACat.objects.create(subtopic=offset_sub, title='Offset 2')

		self.src_topic = self.BioTopic.objects.create(topic='Cells', exam_board='EDEXCEL', specification='Spec A')
		self.src_sub = self.BioSub.objects.create(topic=self.src_topic, title='Membranes')
		self.src_cat = self.BioCat.objects.create(subtopic=self.src_sub, title='Transport')
		self.dst_topic, self.dst_sub, self.dst_cat = self._create_destination(self.src_topic, self.src_sub, self.src_cat)

	def tearDown(self):
		# Remove sessions so the pending 0016 backfill runs cleanly during teardown.
		self.Session.objects.all().delete()
		executor = MigrationExecutor(connection)
		executor.migrate(executor.loader.graph.leaf_nodes())
		super().tearDown()

	def _create_destination(self, topic, sub, cat):
		dst_topic = self.ATopic.objects.create(
			subject=self.subject, topic=topic.topic, exam_board=topic.exam_board, specification=topic.specification
		)
		dst_sub = self.ASub.objects.create(topic=dst_topic, title=sub.title) if sub else None
		dst_cat = self.ACat.objects.create(subtopic=dst_sub, title=cat.title) if cat else None
		return dst_topic, dst_sub, dst_cat

	def _session(self, **kwargs):
		defaults = {'user': self.user, 'exam_board': 'EDEXCEL', 'number_of_questions': 3, 'total_score': 2,
			'total_available': 3, 'feedback': 'ok'}
		defaults.update(kwargs)
		return self.Session.objects.create(**defaults)

	def _run(self, apps=None):
		self.migration.backfill_alevel_session_links(apps or self.apps, SimpleNamespace(connection=connection))

	def _snapshot(self, session):
		row = self.Session.objects.get(pk=session.pk)
		return {
			f.name: getattr(row, f.attname)
			for f in row._meta.concrete_fields
			if not f.name.startswith('alevel_')
		}

	def test_complete_mapping_with_different_primary_keys(self):
		session = self._session(topic=self.src_topic, subtopic=self.src_sub, subcategory=self.src_cat,
			specification='Spec A')
		self.assertNotEqual(self.src_topic.pk, self.dst_topic.pk)
		self.assertNotEqual(self.src_cat.pk, self.dst_cat.pk)
		before = self._snapshot(session)

		self._run()

		row = self.Session.objects.get(pk=session.pk)
		self.assertEqual(
			(row.alevel_topic_id, row.alevel_subtopic_id, row.alevel_subcategory_id),
			(self.dst_topic.pk, self.dst_sub.pk, self.dst_cat.pk),
		)
		self.assertEqual(self._snapshot(session), before)

	def test_null_subtopic_and_subcategory_are_preserved(self):
		topic_only = self._session(topic=self.src_topic)
		sub_only = self._session(topic=self.src_topic, subtopic=self.src_sub)

		self._run()

		row = self.Session.objects.get(pk=topic_only.pk)
		self.assertEqual(row.alevel_topic_id, self.dst_topic.pk)
		self.assertIsNone(row.alevel_subtopic_id)
		self.assertIsNone(row.alevel_subcategory_id)
		row = self.Session.objects.get(pk=sub_only.pk)
		self.assertEqual(row.alevel_subtopic_id, self.dst_sub.pk)
		self.assertIsNone(row.alevel_subcategory_id)

	def test_session_without_any_legacy_topic_is_left_alone(self):
		session = self._session()
		self._run()
		row = self.Session.objects.get(pk=session.pk)
		self.assertIsNone(row.alevel_topic_id)

	def test_backfill_is_repeatable(self):
		session = self._session(topic=self.src_topic, subtopic=self.src_sub, subcategory=self.src_cat)
		self._run()
		first = self.Session.objects.values().get(pk=session.pk)
		self._run()
		self.assertEqual(self.Session.objects.values().get(pk=session.pk), first)

	def test_forward_migration_applies_backfill(self):
		session = self._session(topic=self.src_topic, subtopic=self.src_sub, subcategory=self.src_cat)
		executor = MigrationExecutor(connection)
		executor.migrate(MIGRATE_0016)
		apps = executor.loader.project_state(MIGRATE_0016).apps
		row = apps.get_model('examquestions', 'QuestionSession').objects.get(pk=session.pk)
		self.assertEqual(row.alevel_subcategory_id, self.dst_cat.pk)
		migration = executor.loader.get_migration(*MIGRATE_0016[0])
		self.assertIs(migration.operations[0].reverse_code, self.migration.migrations.RunPython.noop)

	def test_matching_existing_links_are_accepted(self):
		session = self._session(topic=self.src_topic, subtopic=self.src_sub, subcategory=self.src_cat,
			alevel_topic=self.dst_topic)
		self._run()
		row = self.Session.objects.get(pk=session.pk)
		self.assertEqual(row.alevel_topic_id, self.dst_topic.pk)
		self.assertEqual(row.alevel_subcategory_id, self.dst_cat.pk)

	def test_conflicting_existing_link_fails_and_rolls_back(self):
		good = self._session(topic=self.src_topic, subtopic=self.src_sub, subcategory=self.src_cat)
		other = self.ATopic.objects.create(subject=self.subject, topic='Other', exam_board='OCR')
		bad = self._session(topic=self.src_topic, alevel_topic=other)

		with self.assertRaisesMessage(self.migration.SessionBackfillError, 'conflicts with expected'):
			self._run()

		self.assertIsNone(self.Session.objects.get(pk=good.pk).alevel_topic_id)
		self.assertEqual(self.Session.objects.get(pk=bad.pk).alevel_topic_id, other.pk)

	def test_missing_destination_topic_fails(self):
		self.ACat.objects.filter(pk=self.dst_cat.pk).delete()
		self.ASub.objects.filter(pk=self.dst_sub.pk).delete()
		self.ATopic.objects.filter(pk=self.dst_topic.pk).delete()
		self._session(topic=self.src_topic)
		with self.assertRaisesMessage(self.migration.SessionBackfillError, 'Missing destination'):
			self._run()

	def test_missing_destination_subcategory_fails(self):
		self.ACat.objects.filter(pk=self.dst_cat.pk).delete()
		self._session(topic=self.src_topic, subtopic=self.src_sub, subcategory=self.src_cat)
		with self.assertRaisesMessage(self.migration.SessionBackfillError, 'Missing destination'):
			self._run()

	def test_subcategory_without_subtopic_fails_safely(self):
		session = self._session(topic=self.src_topic, subcategory=self.src_cat)
		with self.assertRaisesMessage(self.migration.SessionBackfillError, 'subcategory but no subtopic'):
			self._run()
		row = self.Session.objects.get(pk=session.pk)
		self.assertIsNone(row.alevel_topic_id)
		self.assertIsNone(row.alevel_subcategory_id)

	def test_inconsistent_legacy_hierarchy_fails(self):
		other_topic = self.BioTopic.objects.create(topic='Exchange', exam_board='OCR')
		self._session(topic=other_topic, subtopic=self.src_sub)
		with self.assertRaisesMessage(self.migration.SessionBackfillError, 'does not belong to its topic'):
			self._run()

	def test_missing_biology_subject_fails_when_sessions_exist(self):
		self._session(topic=self.src_topic)
		self.ACat.objects.filter(subtopic__topic__subject=self.subject).delete()
		self.ASub.objects.filter(topic__subject=self.subject).delete()
		self.ATopic.objects.filter(subject=self.subject).delete()
		self.apps.get_model('accounts', 'Subject').objects.filter(code='alevel_biology').delete()
		with self.assertRaisesMessage(self.migration.SessionBackfillError, "'alevel_biology' does not exist"):
			self._run()

	def test_gcse_sessions_and_legacy_data_are_untouched(self):
		GCSETopic = self.apps.get_model('examquestions', 'GCSEScienceTopic')
		gcse_topic = GCSETopic.objects.create(topic='Atomic structure', exam_board='AQA', subject='CHEMISTRY',
			tier='HIGHER')
		gcse = self._session(qualification='GCSE_SCIENCE', gcse_topic=gcse_topic, exam_board='AQA')
		bio = self._session(topic=self.src_topic, subtopic=self.src_sub, subcategory=self.src_cat)
		gcse_before = self.Session.objects.values().get(pk=gcse.pk)
		bio_before = self._snapshot(bio)

		self._run()

		self.assertEqual(self.Session.objects.values().get(pk=gcse.pk), gcse_before)
		self.assertEqual(self._snapshot(bio), bio_before)
		self.assertEqual(self.BioTopic.objects.count(), 1)
		self.assertEqual(self.BioSub.objects.count(), 1)
		self.assertEqual(self.BioCat.objects.count(), 1)

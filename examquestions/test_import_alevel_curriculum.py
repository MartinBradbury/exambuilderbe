import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from examquestions.models import ALevelSubCategory, ALevelSubTopic, ALevelTopic


def sample(**overrides):
	data = {
		'subject': 'alevel_chemistry',
		'exam_board': 'AQA',
		'specification': '',
		'source': {'name': 'Fictional specification', 'version': 'v0'},
		'topics': [
			{
				'topic': 'Fictional Topic One',
				'subtopics': [
					{'title': 'Fictional Subtopic', 'subcategories': ['Fictional Category A', 'Fictional Category B']},
					{'title': 'Another Subtopic'},
				],
			},
			{'topic': 'Fictional Topic Two'},
		],
	}
	data.update(overrides)
	return data


class ImportALevelCurriculumTests(TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self.tmp.cleanup)

	def write(self, data, raw=None):
		path = Path(self.tmp.name) / 'curriculum.json'
		path.write_text(raw if raw is not None else json.dumps(data), encoding='utf-8')
		return str(path)

	def run_import(self, data, *flags, raw=None):
		out = StringIO()
		call_command('import_alevel_curriculum', '--file', self.write(data, raw), *flags, stdout=out)
		return out.getvalue()

	def counts(self):
		return (ALevelTopic.objects.count(), ALevelSubTopic.objects.count(), ALevelSubCategory.objects.count())

	def assertInvalid(self, data, fragment):
		before = self.counts()
		with self.assertRaises(CommandError) as ctx:
			self.run_import(data, '--apply')
		self.assertIn(fragment, str(ctx.exception))
		self.assertEqual(self.counts(), before)

	def test_dry_run_is_default_and_writes_nothing(self):
		before = self.counts()
		output = self.run_import(sample())
		self.assertIn('Dry run', output)
		self.assertIn('topics: 2 to create', output)
		self.assertIn('subcategories: 2 to create', output)
		self.assertEqual(self.counts(), before)
		self.run_import(sample(), '--dry-run')
		self.assertEqual(self.counts(), before)

	def test_apply_creates_hierarchy(self):
		before = self.counts()
		self.run_import(sample(), '--apply')
		self.assertEqual(self.counts(), (before[0] + 2, before[1] + 2, before[2] + 2))
		topic = ALevelTopic.objects.get(subject__code='alevel_chemistry', topic='Fictional Topic One', exam_board='AQA', specification='')
		self.assertEqual(set(topic.subtopics.values_list('title', flat=True)), {'Fictional Subtopic', 'Another Subtopic'})
		sub = topic.subtopics.get(title='Fictional Subtopic')
		self.assertEqual(set(sub.subcategories.values_list('title', flat=True)), {'Fictional Category A', 'Fictional Category B'})

	def test_apply_is_idempotent(self):
		self.run_import(sample(), '--apply')
		after_first = self.counts()
		output = self.run_import(sample(), '--apply')
		self.assertEqual(self.counts(), after_first)
		self.assertIn('topics: 0 created, 2 existing reused', output)
		self.assertIn('0 to create', self.run_import(sample()))

	def test_extending_existing_topic_only_adds_new_records(self):
		self.run_import(sample(), '--apply')
		extended = sample()
		extended['topics'][1]['subtopics'] = [{'title': 'Added Later'}]
		before = self.counts()
		self.run_import(extended, '--apply')
		self.assertEqual(self.counts(), (before[0], before[1] + 1, before[2]))

	def test_edexcel_specifications_are_separate(self):
		for spec in ('Spec A', 'Spec B'):
			self.run_import(sample(exam_board='EDEXCEL', specification=spec), '--apply')
		self.assertEqual(ALevelTopic.objects.filter(topic='Fictional Topic One', exam_board='EDEXCEL').count(), 2)

	def test_subject_and_board_separation(self):
		before = self.counts()
		self.run_import(sample(), '--apply')
		self.run_import(sample(subject='alevel_physics'), '--apply')
		self.run_import(sample(exam_board='OCR'), '--apply')
		self.assertEqual(ALevelTopic.objects.filter(topic='Fictional Topic One').count(), 3)
		self.assertEqual(self.counts()[0], before[0] + 6)
		self.assertEqual(
			ALevelTopic.objects.filter(subject__code='alevel_physics', exam_board='AQA').count(), 2
		)
		self.assertEqual(
			ALevelSubTopic.objects.filter(topic__subject__code='alevel_chemistry', topic__exam_board='OCR').count(), 2
		)

	def test_invalid_subject(self):
		self.assertInvalid(sample(subject='alevel_unknown'), 'subject')
		self.assertInvalid(sample(subject='gcse_science'), 'not an existing A-level subject')

	def test_invalid_board(self):
		self.assertInvalid(sample(exam_board='WJEC'), 'exam_board')
		self.assertInvalid(sample(exam_board='aqa'), 'exam_board')

	def test_invalid_specification(self):
		self.assertInvalid(sample(specification='A'), 'must be an empty string for AQA')
		self.assertInvalid(sample(exam_board='OCR', specification='B'), 'must be an empty string for OCR')
		self.assertInvalid(sample(exam_board='EDEXCEL', specification=''), 'required for EDEXCEL')
		self.assertInvalid(sample(exam_board='EDEXCEL', specification=' '), 'required for EDEXCEL')
		self.assertInvalid(sample(specification=None), 'specification')

	def test_duplicate_titles_rejected(self):
		dup_topic = sample(topics=[{'topic': 'Same'}, {'topic': 'same'}])
		self.assertInvalid(dup_topic, 'duplicate title')
		dup_sub = sample(topics=[{'topic': 'T', 'subtopics': [{'title': 'S'}, {'title': 'S'}]}])
		self.assertInvalid(dup_sub, 'duplicate title')
		dup_cat = sample(topics=[{'topic': 'T', 'subtopics': [{'title': 'S', 'subcategories': ['C', 'C']}]}])
		self.assertInvalid(dup_cat, 'duplicate title')

	def test_same_subtopic_title_under_different_topics_allowed(self):
		data = sample(topics=[
			{'topic': 'T1', 'subtopics': [{'title': 'Shared'}]},
			{'topic': 'T2', 'subtopics': [{'title': 'Shared'}]},
		])
		self.run_import(data, '--apply')
		self.assertEqual(ALevelSubTopic.objects.filter(title='Shared').count(), 2)

	def test_empty_and_malformed_fields_rejected(self):
		self.assertInvalid(sample(topics=[{'topic': '  '}]), 'non-empty string')
		self.assertInvalid(sample(topics=[{'topic': ' padded '}]), 'whitespace')
		self.assertInvalid(sample(topics=[{'topic': 'T', 'subtopics': [{'title': ''}]}]), 'non-empty string')
		self.assertInvalid(sample(topics=[{'topic': 'T', 'subtopics': [{'title': 'S', 'subcategories': [5]}]}]), 'non-empty string')
		self.assertInvalid(sample(topics=[{'topic': 'T', 'subtopics': 'nope'}]), 'must be a list')
		self.assertInvalid(sample(topics=[]), 'non-empty list')
		self.assertInvalid(sample(topics=[{'topic': 'T' * 256}]), 'at most 255')

	def test_unknown_and_missing_fields_rejected(self):
		self.assertInvalid(sample(extra='x'), "unknown field 'extra'")
		self.assertInvalid(sample(topics=[{'topic': 'T', 'notes': 'x'}]), "unknown field 'notes'")
		self.assertInvalid(sample(topics=[{'topic': 'T', 'subtopics': [{'title': 'S', 'code': '1'}]}]), "unknown field 'code'")
		data = sample()
		del data['exam_board']
		self.assertInvalid(data, "missing required field 'exam_board'")

	def test_source_metadata_validated(self):
		self.assertInvalid(sample(source={'url': 'x'}), "missing required field 'name'")
		self.assertInvalid(sample(source={'name': 'x', 'author': 'y'}), "unknown field 'author'")
		self.assertInvalid(sample(source='spec'), 'expected an object')
		no_source = sample()
		del no_source['source']
		self.run_import(no_source, '--apply')

	def test_malformed_json_and_missing_file(self):
		with self.assertRaisesMessage(CommandError, 'Could not parse'):
			self.run_import(None, '--apply', raw='{not json')
		with self.assertRaisesMessage(CommandError, 'Could not parse'):
			self.run_import(None, '--apply', raw='[1, 2')
		with self.assertRaisesMessage(CommandError, 'expected an object'):
			self.run_import(None, '--apply', raw='[]')
		with self.assertRaisesMessage(CommandError, 'File not found'):
			call_command('import_alevel_curriculum', '--file', str(Path(self.tmp.name) / 'missing.json'))

	def test_apply_and_dry_run_flags_are_exclusive(self):
		with self.assertRaises(CommandError):
			self.run_import(sample(), '--apply', '--dry-run')

	def test_case_only_conflict_rejected_and_rolled_back(self):
		self.run_import(sample(topics=[{'topic': 'Existing Topic'}]), '--apply')
		before = self.counts()
		data = sample(topics=[{'topic': 'Brand New'}, {'topic': 'existing topic'}])
		with self.assertRaisesMessage(CommandError, 'differs from'):
			self.run_import(data, '--apply')
		self.assertEqual(self.counts(), before)
		self.assertFalse(ALevelTopic.objects.filter(topic='Brand New').exists())

	def test_rollback_on_model_validation_failure(self):
		before = self.counts()
		original = ALevelSubTopic.full_clean
		calls = {'n': 0}

		def failing(instance, *args, **kwargs):
			calls['n'] += 1
			if calls['n'] == 2:
				from django.core.exceptions import ValidationError
				raise ValidationError('forced failure')
			return original(instance, *args, **kwargs)

		with patch.object(ALevelSubTopic, 'full_clean', failing):
			with self.assertRaisesMessage(CommandError, 'nothing was written'):
				self.run_import(sample(), '--apply')
		self.assertEqual(self.counts(), before)

	def test_existing_records_are_never_modified_or_deleted(self):
		self.run_import(sample(topics=[{'topic': 'Keep', 'subtopics': [{'title': 'Old'}]}]), '--apply')
		keep_ids = set(ALevelSubTopic.objects.filter(title='Old').values_list('id', flat=True))
		self.run_import(sample(topics=[{'topic': 'Keep', 'subtopics': [{'title': 'New'}]}]), '--apply')
		self.assertEqual(set(ALevelSubTopic.objects.filter(title='Old').values_list('id', flat=True)), keep_ids)
		self.assertTrue(ALevelSubTopic.objects.filter(title='New').exists())

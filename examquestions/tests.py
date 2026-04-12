import json
from unittest.mock import Mock, patch
from django.utils import timezone
from django.urls import reverse
from rest_framework.test import APITestCase
from accounts.models import QuestionUsage
from accounts.models import CustomUser
from .models import BiologyTopic, BiologySubTopic, BiologySubCategory, GCSEScienceTopic, GCSEScienceSubTopic, GCSEScienceSubCategory, QuestionSession, QualificationPath, ServedQuestion
from .services import ai, aiGCSE
from .views import GCSE_SUBJECT_ERROR_MESSAGE, is_self_contained_ai_question, resolve_gcse_fallback_bank_path


def _mock_openai_json_response(payload):
	response = Mock()
	message = Mock()
	message.content = json.dumps(payload)
	choice = Mock()
	choice.message = message
	response.choices = [choice]
	return response


class GenerateExamQuestionsLimitTests(APITestCase):
	def setUp(self):
		self.user = CustomUser.objects.create_user(
			email='free@example.com',
			username='free-user',
			password='testpass123',
		)
		self.topic = BiologyTopic.objects.create(topic='Test Topic', exam_board='OCR')
		self.url = reverse('generate-exam-questions')

	@patch('examquestions.views.generate_questions')
	def test_free_user_can_generate_one_question_per_day(self, mock_generate_questions):
		mock_generate_questions.return_value = {
			'questions': [
				{
					'question': 'Explain osmosis. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['Water moves down a water potential gradient (1 mark)'],
				}
			]
		}

		self.client.force_authenticate(user=self.user)
		payload = {
			'qualification': 'ALEVEL',
			'topic_id': self.topic.id,
			'exam_board': 'OCR',
			'number_of_questions': 1,
		}

		first_response = self.client.post(self.url, payload, format='json')

		self.assertEqual(first_response.status_code, 200)
		self.assertEqual(first_response.data['questions_remaining_today'], 0)
		self.assertEqual(QuestionUsage.objects.get(user=self.user).question_count, 1)
		self.assertEqual(QuestionSession.objects.filter(user=self.user).count(), 1)

		second_response = self.client.post(self.url, payload, format='json')

		self.assertEqual(second_response.status_code, 403)
		self.assertEqual(second_response.data['questions_remaining_today'], 0)
		self.assertEqual(QuestionUsage.objects.get(user=self.user).question_count, 1)

	def test_free_user_cannot_request_multiple_questions(self):
		self.client.force_authenticate(user=self.user)

		response = self.client.post(
			self.url,
			{
				'qualification': 'ALEVEL_BIOLOGY',
				'topic_id': self.topic.id,
				'exam_board': 'OCR',
				'number_of_questions': 2,
			},
			format='json',
		)

		self.assertEqual(response.status_code, 403)
		self.assertEqual(response.data['questions_remaining_today'], 1)
		self.assertFalse(QuestionUsage.objects.filter(user=self.user).exists())

	@patch('examquestions.views.generate_questions')
	def test_alevel_paid_user_has_unlimited_alevel_generation(self, mock_generate_questions):
		mock_generate_questions.return_value = {
			'questions': [
				{
					'question': 'State one role of DNA. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['It carries genetic information (1 mark)'],
				},
				{
					'question': 'Define diffusion. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['Net movement from high to low concentration (1 mark)'],
				},
			]
		}

		self.user.has_alevel_paid_access = True
		self.user.save(update_fields=['has_alevel_paid_access'])

		self.client.force_authenticate(user=self.user)
		response = self.client.post(
			self.url,
			{
				'qualification': 'ALEVEL',
				'topic_id': self.topic.id,
				'exam_board': 'OCR',
				'number_of_questions': 2,
			},
			format='json',
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data['plan_type'], 'paid')
		self.assertIsNone(response.data['questions_remaining_today'])
		self.assertFalse(QuestionUsage.objects.filter(user=self.user).exists())

	@patch('examquestions.views.generate_gcse_questions')
	@patch('examquestions.views.generate_questions')
	def test_free_daily_limit_is_shared_across_gcse_and_alevel(self, mock_generate_questions, mock_generate_gcse_questions):
		gcse_topic = GCSEScienceTopic.objects.create(topic='Particles', exam_board='OCR', subject='PHYSICS', tier='FOUNDATION')
		mock_generate_gcse_questions.return_value = {
			'questions': [
				{
					'question': 'State one property of waves. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['Any valid property (1 mark)'],
				}
			]
		}
		mock_generate_questions.return_value = {
			'questions': [
				{
					'question': 'Explain diffusion. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['Particles move down a concentration gradient (1 mark)'],
				}
			]
		}

		self.client.force_authenticate(user=self.user)
		gcse_response = self.client.post(
			self.url,
			{
				'qualification': 'GCSE',
				'topic_id': gcse_topic.id,
				'exam_board': 'OCR',
				'subject': 'PHYSICS',
				'tier': 'FOUNDATION',
				'number_of_questions': 1,
			},
			format='json',
		)
		alevel_response = self.client.post(
			self.url,
			{
				'qualification': 'ALEVEL',
				'topic_id': self.topic.id,
				'exam_board': 'OCR',
				'number_of_questions': 1,
			},
			format='json',
		)

		self.assertEqual(gcse_response.status_code, 200)
		self.assertEqual(alevel_response.status_code, 403)
		self.assertEqual(QuestionUsage.objects.get(user=self.user).question_count, 1)

	@patch('examquestions.views.generate_gcse_questions')
	@patch('examquestions.views.generate_questions')
	def test_gcse_paid_access_does_not_remove_alevel_free_limit(self, mock_generate_questions, mock_generate_gcse_questions):
		gcse_topic = GCSEScienceTopic.objects.create(topic='Energy', exam_board='AQA', subject='BIOLOGY', tier='HIGHER')
		self.user.has_gcse_paid_access = True
		self.user.save(update_fields=['has_gcse_paid_access'])
		mock_generate_gcse_questions.return_value = {
			'questions': [
				{
					'question': 'Describe aerobic respiration. [2 marks]',
					'total_marks': 2,
					'mark_scheme': ['Uses oxygen (1 mark)', 'Releases energy (1 mark)'],
				}
			]
		}
		mock_generate_questions.return_value = {
			'questions': [
				{
					'question': 'Explain osmosis. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['Water moves down a water potential gradient (1 mark)'],
				}
			]
		}

		self.client.force_authenticate(user=self.user)
		gcse_response = self.client.post(
			self.url,
			{
				'qualification': 'GCSE_SCIENCE',
				'topic_id': gcse_topic.id,
				'exam_board': 'AQA',
				'subject': 'BIOLOGY',
				'tier': 'HIGHER',
				'number_of_questions': 1,
			},
			format='json',
		)
		first_alevel_response = self.client.post(
			self.url,
			{
				'qualification': 'ALEVEL_BIOLOGY',
				'topic_id': self.topic.id,
				'exam_board': 'OCR',
				'number_of_questions': 1,
			},
			format='json',
		)
		second_alevel_response = self.client.post(
			self.url,
			{
				'qualification': 'ALEVEL_BIOLOGY',
				'topic_id': self.topic.id,
				'exam_board': 'OCR',
				'number_of_questions': 1,
			},
			format='json',
		)

		self.assertEqual(gcse_response.status_code, 200)
		self.assertEqual(first_alevel_response.status_code, 200)
		self.assertEqual(second_alevel_response.status_code, 403)
		self.assertEqual(QuestionUsage.objects.get(user=self.user).question_count, 1)

	def test_generate_exam_questions_requires_qualification(self):
		self.client.force_authenticate(user=self.user)

		response = self.client.post(
			self.url,
			{
				'topic_id': self.topic.id,
				'exam_board': 'OCR',
				'number_of_questions': 1,
			},
			format='json',
		)

		self.assertEqual(response.status_code, 400)
		self.assertEqual(response.data['error'], "qualification is required. Use 'GCSE_SCIENCE' or 'ALEVEL_BIOLOGY'.")

	@patch('examquestions.views.load_fallback_bank_for_board')
	@patch('examquestions.views.generate_questions')
	def test_duplicate_ai_questions_are_replaced_from_fallback_only(self, mock_generate_questions, mock_load_fallback_bank):
		mock_load_fallback_bank.return_value = {
			'Test Topic': [
				{
					'question': 'Fallback replacement one. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['Fallback mark scheme (1 mark)'],
				},
				{
					'question': 'Fallback replacement two. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['Fallback mark scheme (1 mark)'],
				},
			],
		}
		mock_generate_questions.return_value = {
			'questions': [
				{
					'question': 'Repeated question. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['One point (1 mark)'],
				},
				{
					'question': 'Repeated question. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['One point (1 mark)'],
				},
			],
		}

		ServedQuestion.objects.create(
			user=self.user,
			exam_board='OCR',
			scope_key=f'topic:{self.topic.id}',
			normalized_question='repeated question.',
		)

		self.user.has_alevel_paid_access = True
		self.user.save(update_fields=['has_alevel_paid_access'])

		self.client.force_authenticate(user=self.user)
		response = self.client.post(
			self.url,
			{
				'qualification': 'ALEVEL_BIOLOGY',
				'topic_id': self.topic.id,
				'exam_board': 'OCR',
				'number_of_questions': 2,
			},
			format='json',
		)

		self.assertEqual(response.status_code, 200)
		returned_questions = {question['question'] for question in response.data['questions']}
		self.assertEqual(returned_questions, {
			'Fallback replacement one. [1 mark]',
			'Fallback replacement two. [1 mark]',
		})
		mock_generate_questions.assert_called_once()

	@patch('examquestions.views.load_fallback_bank_for_board')
	@patch('examquestions.views.generate_questions')
	def test_alevel_requests_full_question_count_from_ai_even_when_fallback_pool_exists(self, mock_generate_questions, mock_load_fallback_bank):
		mock_load_fallback_bank.return_value = {
			'Test Topic': [
				{
					'question': 'Fallback question one. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['Fallback point (1 mark)'],
				},
			],
		}
		mock_generate_questions.return_value = {
			'questions': [
				{
					'question': 'AI question one. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['AI point (1 mark)'],
				},
				{
					'question': 'AI question two. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['AI point (1 mark)'],
				},
			],
		}

		self.user.has_alevel_paid_access = True
		self.user.save(update_fields=['has_alevel_paid_access'])

		self.client.force_authenticate(user=self.user)
		response = self.client.post(
			self.url,
			{
				'qualification': 'ALEVEL_BIOLOGY',
				'topic_id': self.topic.id,
				'exam_board': 'OCR',
				'number_of_questions': 2,
			},
			format='json',
		)

		self.assertEqual(response.status_code, 200)
		mock_generate_questions.assert_called_once_with('Test Topic', 'OCR', 2)

	@patch('examquestions.views.load_fallback_bank_for_board')
	@patch('examquestions.views.generate_questions')
	def test_alevel_returns_valid_ai_questions_without_preloading_fallback(self, mock_generate_questions, mock_load_fallback_bank):
		mock_load_fallback_bank.return_value = {
			'Test Topic': [
				{
					'question': 'Fallback question one. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['Fallback point (1 mark)'],
				},
				{
					'question': 'Fallback question two. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['Fallback point (1 mark)'],
				},
			],
		}
		mock_generate_questions.return_value = {
			'questions': [
				{
					'question': 'Explain the role of water in transport. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['Water acts as a solvent for transport (1 mark)'],
				},
				{
					'question': 'State one function of the cell membrane. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['Controls movement of substances into and out of the cell (1 mark)'],
				},
			],
		}

		self.user.has_alevel_paid_access = True
		self.user.save(update_fields=['has_alevel_paid_access'])

		self.client.force_authenticate(user=self.user)
		response = self.client.post(
			self.url,
			{
				'qualification': 'ALEVEL_BIOLOGY',
				'topic_id': self.topic.id,
				'exam_board': 'OCR',
				'number_of_questions': 2,
			},
			format='json',
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(
			{question['question'] for question in response.data['questions']},
			{
				'Explain the role of water in transport. [1 mark]',
				'State one function of the cell membrane. [1 mark]',
			},
		)

	@patch('examquestions.views.load_fallback_bank_for_board')
	@patch('examquestions.views.generate_questions')
	def test_ai_questions_missing_method_context_are_replaced_from_fallback(self, mock_generate_questions, mock_load_fallback_bank):
		mock_load_fallback_bank.return_value = {
			'Test Topic': [
				{
					'question': 'Describe one limitation of using a single pH value when testing enzyme activity. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['Only one pH value means no valid comparison across a range (1 mark)'],
				},
			],
		}
		mock_generate_questions.return_value = {
			'questions': [
				{
					'question': 'A student investigates the effect of pH on enzyme activity. Evaluate the method used and suggest improvements. [6 marks]',
					'total_marks': 6,
					'mark_scheme': ['Repeat the experiment (1 mark)'],
				},
			],
		}

		self.user.has_alevel_paid_access = True
		self.user.save(update_fields=['has_alevel_paid_access'])

		self.client.force_authenticate(user=self.user)
		response = self.client.post(
			self.url,
			{
				'qualification': 'ALEVEL_BIOLOGY',
				'topic_id': self.topic.id,
				'exam_board': 'OCR',
				'number_of_questions': 1,
			},
			format='json',
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(
			response.data['questions'][0]['question'],
			'Describe one limitation of using a single pH value when testing enzyme activity. [1 mark]',
		)

	@patch('examquestions.views.load_fallback_bank_for_board')
	@patch('examquestions.views.generate_questions')
	def test_exhausted_fallback_history_resets_for_user_and_reuses_pool(self, mock_generate_questions, mock_load_fallback_bank):
		mock_load_fallback_bank.return_value = {
			'Test Topic': [
				{
					'question': 'Fallback only question. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['Fallback mark scheme (1 mark)'],
				},
			],
		}
		mock_generate_questions.return_value = {
			'questions': [
				{
					'question': 'Fallback only question. [1 mark]',
					'total_marks': 1,
					'mark_scheme': ['One point (1 mark)'],
				},
			],
		}

		ServedQuestion.objects.create(
			user=self.user,
			exam_board='OCR',
			scope_key=f'topic:{self.topic.id}',
			normalized_question='fallback only question.',
		)

		self.user.has_alevel_paid_access = True
		self.user.save(update_fields=['has_alevel_paid_access'])

		self.client.force_authenticate(user=self.user)
		response = self.client.post(
			self.url,
			{
				'qualification': 'ALEVEL',
				'topic_id': self.topic.id,
				'exam_board': 'OCR',
				'number_of_questions': 1,
			},
			format='json',
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data['questions'][0]['question'], 'Fallback only question. [1 mark]')
		self.assertEqual(
			ServedQuestion.objects.filter(user=self.user, exam_board='OCR', scope_key=f'topic:{self.topic.id}').count(),
			1,
		)


class QuestionPromptContractTests(APITestCase):
	@patch('examquestions.services.ai._create_json_chat_completion')
	def test_alevel_generation_prompt_requires_self_contained_question_context(self, mock_create_completion):
		mock_create_completion.return_value = _mock_openai_json_response({'questions': []})

		ai.generate_questions('Module 1 (SubTopic: Evaluate methods)', 'OCR', 1)

		messages = mock_create_completion.call_args.kwargs['messages']
		prompt = messages[1]['content']

		self.assertIn('Make each question fully answerable from the text you return.', prompt)
		self.assertIn('Do not refer to any unseen method, figure, graph, table, practical setup, results, or source material.', prompt)
		self.assertIn('include a concise stem describing that method or data directly in the `question` text', prompt)


class AIQuestionValidationTests(APITestCase):
	def test_validator_rejects_unseen_graph_reference(self):
		self.assertFalse(
			is_self_contained_ai_question({
				'question': 'Use the information provided in the graph to explain why the reaction rate decreases after 2 minutes. [3 marks]',
			})
		)

	def test_validator_accepts_method_question_with_embedded_context(self):
		self.assertTrue(
			is_self_contained_ai_question({
				'question': 'A student adds amylase to starch solution, samples the mixture every 30 seconds, and uses iodine to check when starch is no longer present. Evaluate the method and suggest one improvement. [4 marks]',
			})
		)

	@patch('examquestions.services.aiGCSE._create_json_chat_completion')
	def test_gcse_generation_prompt_requires_self_contained_question_context(self, mock_create_completion):
		mock_create_completion.return_value = _mock_openai_json_response({'questions': []})

		aiGCSE.generate_questions('Enzymes', 'OCR', 1, 'BIOLOGY', 'FOUNDATION')

		messages = mock_create_completion.call_args.kwargs['messages']
		prompt = messages[1]['content']

		self.assertIn('Make each question fully answerable from the text you return.', prompt)
		self.assertIn('Do not refer to any unseen method, figure, graph, table, practical setup, results, or source material.', prompt)
		self.assertIn('include a concise stem describing that method or data directly in the `question` text', prompt)


class MarkingFlowTests(APITestCase):
	def setUp(self):
		self.user = CustomUser.objects.create_user(
			email='marker@example.com',
			username='marker-user',
			password='testpass123',
		)
		self.topic = BiologyTopic.objects.create(topic='Cells', exam_board='OCR')
		self.session = QuestionSession.objects.create(
			user=self.user,
			topic=self.topic,
			exam_board='OCR',
			number_of_questions=2,
			total_available=4,
		)
		self.mark_url = reverse('mark-user-answer')
		self.submit_url = reverse('submit_question_session')
		self.client.force_authenticate(user=self.user)

	@patch('examquestions.views.evaluate_batch_responses_with_openai')
	def test_mark_user_answer_supports_batch_mode(self, mock_batch_mark):
		mock_batch_mark.return_value = {
			'results': [
				{'index': 1, 'score': 1, 'out_of': 2, 'feedback': 'One valid point credited.'},
				{'index': 2, 'score': 2, 'out_of': 2, 'feedback': 'Full marks.'},
			],
			'strengths': ['Strength 1', 'Strength 2', 'Strength 3'],
			'improvements': ['Improve 1', 'Improve 2', 'Improve 3'],
		}

		response = self.client.post(
			self.mark_url,
			{
				'exam_board': 'OCR',
				'answers': [
					{
						'question': 'Explain osmosis. [2 marks]',
						'mark_scheme': ['Water moves down a water potential gradient'],
						'user_answer': 'Water moves from high to low water potential.',
					},
					{
						'question': 'State one role of DNA. [2 marks]',
						'mark_scheme': ['Carries genetic information', 'Codes for proteins'],
						'user_answer': 'It carries genetic information and codes for proteins.',
					},
				],
			},
			format='json',
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.data['results']), 2)
		self.assertEqual(response.data['strengths'][0], 'Strength 1')
		mock_batch_mark.assert_called_once()

	@patch('examquestions.views.evaluate_batch_responses_with_openai')
	def test_submit_question_session_uses_provided_feedback_without_extra_openai_call(self, mock_batch_mark):
		response = self.client.post(
			self.submit_url,
			{
				'session_id': self.session.id,
				'answers': [
					{
						'question': 'Explain osmosis. [2 marks]',
						'user_answer': 'Water moves from high to low water potential.',
						'score': 1,
						'out_of': 2,
						'feedback': 'You identified the direction but not the membrane context.',
					},
					{
						'question': 'State one role of DNA. [2 marks]',
						'user_answer': 'It carries genetic information.',
						'score': 1,
						'out_of': 2,
						'feedback': 'One valid role credited.',
					},
				],
				'feedback': {
					'strengths': ['Strength 1', 'Strength 2', 'Strength 3'],
					'improvements': ['Improve 1', 'Improve 2', 'Improve 3'],
				},
			},
			format='json',
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data['feedback']['strengths'][0], 'Strength 1')
		self.session.refresh_from_db()
		self.assertIn('Strength 1', self.session.feedback)
		mock_batch_mark.assert_not_called()

	@patch('examquestions.views.evaluate_batch_responses_with_openai')
	def test_submit_question_session_builds_local_feedback_when_none_supplied(self, mock_batch_mark):
		response = self.client.post(
			self.submit_url,
			{
				'session_id': self.session.id,
				'answers': [
					{
						'question': 'Explain osmosis across a partially permeable membrane. [2 marks]',
						'user_answer': 'Water moves across a membrane.',
						'score': 1,
						'out_of': 2,
						'feedback': 'Mention the water potential gradient explicitly.',
					},
					{
						'question': 'State one role of DNA. [2 marks]',
						'user_answer': 'It stores genetic information.',
						'score': 2,
						'out_of': 2,
						'feedback': 'Clear answer.',
					},
				],
			},
			format='json',
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.data['feedback']['strengths']), 3)
		self.assertEqual(len(response.data['feedback']['improvements']), 3)
		mock_batch_mark.assert_not_called()


class GCSEFlowTests(APITestCase):
	def setUp(self):
		self.user = CustomUser.objects.create_user(
			email='gcse@example.com',
			username='gcse-user',
			password='testpass123',
		)
		self.topic = GCSEScienceTopic.objects.create(topic='Atomic structure', exam_board='AQA', subject='CHEMISTRY', tier='HIGHER')
		self.subtopic = GCSEScienceSubTopic.objects.create(topic=self.topic, title='Atomic models')
		self.subcategory = GCSEScienceSubCategory.objects.create(subtopic=self.subtopic, title='Electronic structure')
		self.generate_url = reverse('generate-exam-questions')
		self.mark_url = reverse('mark-user-answer')
		self.client.force_authenticate(user=self.user)

	def _assert_aqa_subject_fallback_response(self, subject, question_text, mark_scheme):
		topic = GCSEScienceTopic.objects.create(
			topic=f'{subject.title()} fallback topic',
			exam_board='AQA',
			subject=subject,
			tier='HIGHER',
		)

		with patch('examquestions.views.load_fallback_bank_for_gcse') as mock_load_fallback_bank_for_gcse, patch('examquestions.views.generate_gcse_questions') as mock_generate_gcse_questions:
			mock_load_fallback_bank_for_gcse.return_value = {
				'Generic GCSE fallback': [
					{
						'question': question_text,
						'mark': 1,
						'mark_scheme': [mark_scheme],
					},
				],
			}
			mock_generate_gcse_questions.return_value = {
				'questions': [
					{
						'question': 'A student investigates the effect of pH on enzyme activity. Evaluate the method used and suggest improvements. [6 marks]',
						'total_marks': 6,
						'mark_scheme': ['Repeat the experiment (1 mark)'],
					},
				],
			}

			self.user.has_gcse_paid_access = True
			self.user.save(update_fields=['has_gcse_paid_access'])

			response = self.client.post(
				self.generate_url,
				{
					'qualification': 'GCSE_SCIENCE',
					'topic_id': topic.id,
					'exam_board': 'AQA',
					'subject': subject,
					'tier': 'HIGHER',
					'number_of_questions': 1,
				},
				format='json',
			)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data['questions'][0]['question'], question_text)

	def _assert_ocr_subject_fallback_response(self, subject, question_text, mark_scheme):
		topic = GCSEScienceTopic.objects.create(
			topic=f'{subject.title()} OCR fallback topic',
			exam_board='OCR',
			subject=subject,
			tier='HIGHER',
		)

		with patch('examquestions.views.load_fallback_bank_for_gcse') as mock_load_fallback_bank_for_gcse, patch('examquestions.views.generate_gcse_questions') as mock_generate_gcse_questions:
			mock_load_fallback_bank_for_gcse.return_value = {
				'Generic GCSE fallback': [
					{
						'question': question_text,
						'mark': 1,
						'mark_scheme': [mark_scheme],
					},
				],
			}
			mock_generate_gcse_questions.return_value = {
				'questions': [
					{
						'question': 'Refer to the graph and evaluate the method used. [6 marks]',
						'total_marks': 6,
						'mark_scheme': ['Repeat the investigation (1 mark)'],
					},
				],
			}

			self.user.has_gcse_paid_access = True
			self.user.save(update_fields=['has_gcse_paid_access'])

			response = self.client.post(
				self.generate_url,
				{
					'qualification': 'GCSE_SCIENCE',
					'topic_id': topic.id,
					'exam_board': 'OCR',
					'subject': subject,
					'tier': 'HIGHER',
					'number_of_questions': 1,
				},
				format='json',
			)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data['questions'][0]['question'], question_text)

	@patch('examquestions.views.generate_gcse_questions')
	def test_generate_exam_questions_routes_to_gcse_service(self, mock_generate_gcse_questions):
		mock_generate_gcse_questions.return_value = {
			'questions': [
				{
					'question': 'Describe the structure of an atom. [2 marks]',
					'total_marks': 2,
					'mark_scheme': ['A nucleus contains protons and neutrons (1 mark)', 'Electrons are in shells around the nucleus (1 mark)'],
				}
			]
		}

		self.user.has_gcse_paid_access = True
		self.user.save(update_fields=['has_gcse_paid_access'])

		response = self.client.post(
			self.generate_url,
			{
				'qualification': 'GCSE_SCIENCE',
				'topic_id': self.topic.id,
				'subtopic_id': self.subtopic.id,
				'subcategory_id': self.subcategory.id,
				'exam_board': 'AQA',
				'subject': 'chemistry',
				'tier': 'higher',
				'number_of_questions': 1,
			},
			format='json',
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data['qualification'], QualificationPath.GCSE_SCIENCE)
		self.assertEqual(response.data['questions'][0]['question'], 'Describe the structure of an atom. [2 marks]')
		mock_generate_gcse_questions.assert_called_once_with('Atomic structure (SubTopic: Atomic models) (SubCategory: Electronic structure)', 'AQA', 1, 'CHEMISTRY', 'HIGHER')
		session = QuestionSession.objects.get(user=self.user)
		self.assertEqual(session.qualification, QualificationPath.GCSE_SCIENCE)
		self.assertEqual(session.gcse_topic, self.topic)
		self.assertEqual(session.gcse_subtopic, self.subtopic)
		self.assertEqual(session.gcse_subcategory, self.subcategory)
		self.assertEqual(session.gcse_subject, 'CHEMISTRY')
		self.assertEqual(session.gcse_tier, 'HIGHER')

	@patch('examquestions.views.load_fallback_bank_for_gcse')
	@patch('examquestions.views.generate_gcse_questions')
	def test_gcse_generation_uses_json_bank_fallback_for_missing_context(self, mock_generate_gcse_questions, mock_load_fallback_bank_for_gcse):
		mock_load_fallback_bank_for_gcse.return_value = {
			'Generic GCSE fallback': [
				{
					'question': 'State the relative charge of a proton. [1 mark]',
					'mark': 1,
					'mark_scheme': ['+1 (1 mark)'],
				},
			],
		}
		mock_generate_gcse_questions.return_value = {
			'questions': [
				{
					'question': 'A student investigates the effect of pH on enzyme activity. Evaluate the method used and suggest improvements. [6 marks]',
					'total_marks': 6,
					'mark_scheme': ['Repeat the experiment (1 mark)'],
				},
			],
		}

		self.user.has_gcse_paid_access = True
		self.user.save(update_fields=['has_gcse_paid_access'])

		response = self.client.post(
			self.generate_url,
			{
				'qualification': 'GCSE_SCIENCE',
				'topic_id': self.topic.id,
				'subtopic_id': self.subtopic.id,
				'subcategory_id': self.subcategory.id,
				'exam_board': 'AQA',
				'subject': 'CHEMISTRY',
				'tier': 'HIGHER',
				'number_of_questions': 1,
			},
			format='json',
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(
			response.data['questions'][0]['question'],
			'State the relative charge of a proton. [1 mark]',
		)

	def test_aqa_biology_generation_uses_fallback_when_ai_output_is_invalid(self):
		self._assert_aqa_subject_fallback_response(
			'BIOLOGY',
			'State one function of the cell membrane. [1 mark]',
			'Controls movement of substances into and out of the cell (1 mark)',
		)

	def test_aqa_chemistry_generation_uses_fallback_when_ai_output_is_invalid(self):
		self._assert_aqa_subject_fallback_response(
			'CHEMISTRY',
			'State the relative charge of an electron. [1 mark]',
			'-1 (1 mark)',
		)

	def test_aqa_physics_generation_uses_fallback_when_ai_output_is_invalid(self):
		self._assert_aqa_subject_fallback_response(
			'PHYSICS',
			'State the unit of force. [1 mark]',
			'newton / N (1 mark)',
		)

	def test_ocr_biology_generation_uses_fallback_when_ai_output_is_invalid(self):
		self._assert_ocr_subject_fallback_response(
			'BIOLOGY',
			'State one function of the nucleus. [1 mark]',
			'Contains genetic material / controls the activities of the cell (1 mark)',
		)

	def test_ocr_chemistry_generation_uses_fallback_when_ai_output_is_invalid(self):
		self._assert_ocr_subject_fallback_response(
			'CHEMISTRY',
			'State the formula for density. [1 mark]',
			'density = mass / volume (1 mark)',
		)

	def test_ocr_physics_generation_uses_fallback_when_ai_output_is_invalid(self):
		self._assert_ocr_subject_fallback_response(
			'PHYSICS',
			'State the unit of power. [1 mark]',
			'watt / W (1 mark)',
		)

	@patch('examquestions.views.evaluate_gcse_batch_responses_with_openai')
	def test_mark_user_answer_routes_to_gcse_marking_service(self, mock_gcse_mark):
		mock_gcse_mark.return_value = {
			'results': [
				{'index': 1, 'score': 2, 'out_of': 2, 'feedback': 'Full marks.'},
			],
			'strengths': ['Strength 1', 'Strength 2', 'Strength 3'],
			'improvements': ['Improve 1', 'Improve 2', 'Improve 3'],
		}
		payload = {
			'qualification': 'GCSE_SCIENCE',
			'exam_board': 'AQA',
			'subject': 'CHEMISTRY',
			'tier': 'FOUNDATION',
			'answers': [
				{
					'question': 'State the relative charge of a proton. [1 mark]',
					'mark_scheme': ['A proton has a charge of +1'],
					'user_answer': '+1',
				},
			],
		}

		response = self.client.post(
			self.mark_url,
			payload,
			format='json',
		)

		self.assertEqual(response.status_code, 200)
		mock_gcse_mark.assert_called_once_with(payload['answers'], 'AQA', 'CHEMISTRY', 'FOUNDATION')


class GCSEFallbackRoutingTests(APITestCase):
	def test_ocr_gcse_biology_routes_to_biology_fallback_bank(self):
		path = resolve_gcse_fallback_bank_path('OCR', 'BIOLOGY')
		self.assertEqual(path.name, 'ocr_gateway_gcse_triple_biology_fallback_questions.json')

	def test_ocr_gcse_chemistry_routes_to_chemistry_fallback_bank(self):
		path = resolve_gcse_fallback_bank_path('OCR', 'CHEMISTRY')
		self.assertEqual(path.name, 'ocr_gateway_gcse_triple_chemistry_fallback_questions.json')

	def test_ocr_gcse_physics_routes_to_physics_fallback_bank(self):
		path = resolve_gcse_fallback_bank_path('OCR', 'PHYSICS')
		self.assertEqual(path.name, 'ocr_gateway_gcse_triple_physics_fallback_questions.json')

	def test_aqa_gcse_biology_routes_to_biology_fallback_bank(self):
		path = resolve_gcse_fallback_bank_path('AQA', 'BIOLOGY')
		self.assertEqual(path.name, 'aqa_triple_biology_compact_exam_style.json')

	def test_aqa_gcse_chemistry_routes_to_chemistry_fallback_bank(self):
		path = resolve_gcse_fallback_bank_path('AQA', 'CHEMISTRY')
		self.assertEqual(path.name, 'aqa_triple_chemistry_compact_exam_style.json')

	def test_aqa_gcse_physics_routes_to_physics_fallback_bank(self):
		path = resolve_gcse_fallback_bank_path('AQA', 'PHYSICS')
		self.assertEqual(path.name, 'aqa_triple_physics_compact_exam_style.json')

	def test_ocr_gcse_combined_has_no_dedicated_fallback_bank(self):
		self.assertIsNone(resolve_gcse_fallback_bank_path('OCR', 'COMBINED'))

	def test_aqa_gcse_combined_has_no_dedicated_fallback_bank(self):
		self.assertIsNone(resolve_gcse_fallback_bank_path('AQA', 'COMBINED'))


class GCSECombinedSubjectValidationTests(APITestCase):
	def setUp(self):
		self.user = CustomUser.objects.create_user(
			email='combined@example.com',
			username='combined-user',
			password='testpass123',
		)
		self.client.force_authenticate(user=self.user)
		self.topics_url = reverse('gcse-topics')

	def test_get_gcse_topics_accepts_combined_subject_filter(self):
		response = self.client.get(
			self.topics_url,
			{
				'exam_board': 'OCR',
				'subject': 'COMBINED',
			},
		)

		self.assertEqual(response.status_code, 200)

	def test_invalid_gcse_subject_message_includes_combined(self):
		response = self.client.get(
			self.topics_url,
			{
				'exam_board': 'OCR',
				'subject': 'NOT_A_SUBJECT',
			},
		)

		self.assertEqual(response.status_code, 400)
		self.assertEqual(response.data['error'], GCSE_SUBJECT_ERROR_MESSAGE)


class UserSessionsSerializerTests(APITestCase):
	def setUp(self):
		self.user = CustomUser.objects.create_user(
			email='sessions@example.com',
			username='sessions-user',
			password='testpass123',
		)
		self.url = reverse('get_user_sessions')
		self.delete_url = reverse('delete_user_results')
		self.user_info_url = reverse('user-info')
		self.reset_tracking_url = reverse('reset-performance-tracking')
		self.client.force_authenticate(user=self.user)

	def test_get_user_sessions_returns_separate_alevel_result_card_fields(self):
		topic = BiologyTopic.objects.create(topic='Cells', exam_board='OCR')
		subtopic = BiologySubTopic.objects.create(topic=topic, title='Cell membrane')
		subcategory = BiologySubCategory.objects.create(subtopic=subtopic, title='Transport across membranes')
		QuestionSession.objects.create(
			user=self.user,
			qualification=QualificationPath.ALEVEL_BIOLOGY,
			topic=topic,
			subtopic=subtopic,
			subcategory=subcategory,
			exam_board='OCR',
			number_of_questions=2,
			total_score=5,
			total_available=6,
		)

		response = self.client.get(self.url)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.data), 1)
		self.assertEqual(response.data[0]['level'], 'A level')
		self.assertEqual(response.data[0]['qualification'], QualificationPath.ALEVEL_BIOLOGY)
		self.assertEqual(response.data[0]['exam_board'], 'OCR')
		self.assertEqual(response.data[0]['topic_name'], 'Cells')
		self.assertEqual(response.data[0]['subtopic_name'], 'Cell membrane')
		self.assertEqual(response.data[0]['subcategory_name'], 'Transport across membranes')

	def test_get_user_sessions_returns_separate_gcse_result_card_fields(self):
		topic = GCSEScienceTopic.objects.create(
			topic='Atomic structure',
			exam_board='AQA',
			subject='CHEMISTRY',
			tier='HIGHER',
		)
		subtopic = GCSEScienceSubTopic.objects.create(topic=topic, title='Atomic models')
		subcategory = GCSEScienceSubCategory.objects.create(subtopic=subtopic, title='Electronic structure')
		QuestionSession.objects.create(
			user=self.user,
			qualification=QualificationPath.GCSE_SCIENCE,
			gcse_topic=topic,
			gcse_subtopic=subtopic,
			gcse_subcategory=subcategory,
			gcse_subject='CHEMISTRY',
			gcse_tier='HIGHER',
			exam_board='AQA',
			number_of_questions=1,
			total_score=2,
			total_available=2,
		)

		response = self.client.get(self.url)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.data), 1)
		self.assertEqual(response.data[0]['level'], 'GCSE')
		self.assertEqual(response.data[0]['qualification'], QualificationPath.GCSE_SCIENCE)
		self.assertEqual(response.data[0]['exam_board'], 'AQA')
		self.assertEqual(response.data[0]['topic_name'], 'Atomic structure')
		self.assertEqual(response.data[0]['subtopic_name'], 'Atomic models')
		self.assertEqual(response.data[0]['subcategory_name'], 'Electronic structure')

	def test_get_user_sessions_still_returns_history_after_soft_reset(self):
		topic = BiologyTopic.objects.create(topic='Inheritance', exam_board='AQA')
		first_session = QuestionSession.objects.create(
			user=self.user,
			qualification=QualificationPath.ALEVEL_BIOLOGY,
			topic=topic,
			exam_board='AQA',
			number_of_questions=1,
			total_score=1,
			total_available=1,
		)
		QuestionSession.objects.create(
			user=self.user,
			qualification=QualificationPath.ALEVEL_BIOLOGY,
			topic=topic,
			exam_board='AQA',
			number_of_questions=1,
			total_score=0,
			total_available=1,
		)

		reset_response = self.client.post(self.reset_tracking_url, {}, format='json')

		response = self.client.get(self.url)

		self.assertEqual(reset_response.status_code, 200)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.data), 2)
		self.assertEqual(response.data[-1]['id'], first_session.id)

	def test_reset_performance_tracking_keeps_sessions_and_updates_user_baseline(self):
		topic = BiologyTopic.objects.create(topic='Ecology', exam_board='OCR')
		QuestionSession.objects.create(
			user=self.user,
			qualification=QualificationPath.ALEVEL_BIOLOGY,
			topic=topic,
			exam_board='OCR',
			number_of_questions=2,
			total_score=3,
			total_available=4,
		)
		QuestionSession.objects.create(
			user=self.user,
			qualification=QualificationPath.ALEVEL_BIOLOGY,
			topic=topic,
			exam_board='OCR',
			number_of_questions=1,
			total_score=1,
			total_available=2,
		)

		response = self.client.post(self.reset_tracking_url, {}, format='json')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data['detail'], 'Performance tracking reset successfully.')
		self.user.refresh_from_db()
		self.assertIsNotNone(self.user.performance_tracking_start_date)
		self.assertLessEqual(self.user.performance_tracking_start_date, timezone.now())
		self.assertEqual(QuestionSession.objects.filter(user=self.user).count(), 2)
		user_info_response = self.client.get(self.user_info_url)
		self.assertEqual(user_info_response.status_code, 200)
		self.assertIsNotNone(user_info_response.data['performance_tracking_start_date'])

	def test_delete_user_results_hard_deletes_all_sessions(self):
		topic = BiologyTopic.objects.create(topic='Homeostasis', exam_board='OCR')
		QuestionSession.objects.create(
			user=self.user,
			qualification=QualificationPath.ALEVEL_BIOLOGY,
			topic=topic,
			exam_board='OCR',
			number_of_questions=1,
			total_score=1,
			total_available=1,
		)

		response = self.client.delete(self.delete_url, {'mode': 'hard'}, format='json')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data['mode'], 'hard')
		self.assertEqual(QuestionSession.objects.filter(user=self.user).count(), 0)

	def test_delete_user_results_rejects_soft_mode(self):
		response = self.client.delete(self.delete_url, {'mode': 'soft'}, format='json')

		self.assertEqual(response.status_code, 400)
		self.assertEqual(response.data['error'], "Soft reset moved to POST /accounts/reset-performance-tracking/. Use mode='hard' for permanent deletion here.")

from unittest.mock import patch
from django.urls import reverse
from rest_framework.test import APITestCase
from accounts.models import QuestionUsage, UserEntitlement
from accounts.models import CustomUser
from .models import BiologyTopic, GCSEScienceTopic, GCSEScienceSubTopic, GCSEScienceSubCategory, QuestionSession, QualificationPath, ServedQuestion


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
	def test_lifetime_user_has_unlimited_generation(self, mock_generate_questions):
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

		entitlement = self.user.entitlement
		entitlement.plan_type = UserEntitlement.PlanType.LIFETIME
		entitlement.lifetime_unlocked = True
		entitlement.save(update_fields=['plan_type', 'lifetime_unlocked'])

		self.client.force_authenticate(user=self.user)
		response = self.client.post(
			self.url,
			{
				'topic_id': self.topic.id,
				'exam_board': 'OCR',
				'number_of_questions': 2,
			},
			format='json',
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data['plan_type'], UserEntitlement.PlanType.LIFETIME)
		self.assertIsNone(response.data['questions_remaining_today'])
		self.assertFalse(QuestionUsage.objects.filter(user=self.user).exists())

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

		entitlement = self.user.entitlement
		entitlement.plan_type = UserEntitlement.PlanType.LIFETIME
		entitlement.lifetime_unlocked = True
		entitlement.save(update_fields=['plan_type', 'lifetime_unlocked'])

		self.client.force_authenticate(user=self.user)
		response = self.client.post(
			self.url,
			{
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

		entitlement = self.user.entitlement
		entitlement.plan_type = UserEntitlement.PlanType.LIFETIME
		entitlement.lifetime_unlocked = True
		entitlement.save(update_fields=['plan_type', 'lifetime_unlocked'])

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

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data['questions'][0]['question'], 'Fallback only question. [1 mark]')
		self.assertEqual(
			ServedQuestion.objects.filter(user=self.user, exam_board='OCR', scope_key=f'topic:{self.topic.id}').count(),
			1,
		)


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
		self.topic = GCSEScienceTopic.objects.create(topic='Atomic structure', exam_board='AQA', subject='CHEMISTRY')
		self.subtopic = GCSEScienceSubTopic.objects.create(topic=self.topic, title='Atomic models')
		self.subcategory = GCSEScienceSubCategory.objects.create(subtopic=self.subtopic, title='Electronic structure')
		self.generate_url = reverse('generate-exam-questions')
		self.mark_url = reverse('mark-user-answer')
		self.client.force_authenticate(user=self.user)

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

		entitlement = self.user.entitlement
		entitlement.plan_type = UserEntitlement.PlanType.LIFETIME
		entitlement.lifetime_unlocked = True
		entitlement.save(update_fields=['plan_type', 'lifetime_unlocked'])

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

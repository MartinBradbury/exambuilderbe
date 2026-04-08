from unittest.mock import patch
from django.urls import reverse
from rest_framework.test import APITestCase
from accounts.models import QuestionUsage, UserEntitlement
from accounts.models import CustomUser
from .models import BiologyTopic, QuestionSession


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

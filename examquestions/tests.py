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

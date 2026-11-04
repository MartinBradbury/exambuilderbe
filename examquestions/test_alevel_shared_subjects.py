from unittest.mock import patch

from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import CustomUser, QuestionUsage, Subject, SubjectEntitlement
from .models import (
    ALevelSubCategory,
    ALevelSubTopic,
    ALevelTopic,
    BiologySubCategory,
    BiologySubTopic,
    BiologyTopic,
    QuestionSession,
    ServedQuestion,
)
from .services import ai_chemistry, ai_physics

SUBJECTS = (
    ("ALEVEL_CHEMISTRY", "alevel_chemistry", "examquestions.views.ai_chemistry"),
    ("ALEVEL_PHYSICS", "alevel_physics", "examquestions.views.ai_physics"),
)


def ai_payload(*texts):
    return {
        "questions": [
            {"question": text, "total_marks": 1, "mark_scheme": ["Valid point (1 mark)"]}
            for text in texts
        ]
    }


class SharedSubjectFixtureMixin:
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="shared@example.com", username="shared-user", password="testpass123"
        )
        self.client.force_authenticate(user=self.user)
        self.generate_url = reverse("generate-exam-questions")
        self.topics = {}
        for _, code, _ in SUBJECTS + (("ALEVEL_BIOLOGY", "alevel_biology", ""),):
            subject = Subject.objects.get(code=code)
            topic = ALevelTopic.objects.create(subject=subject, topic=f"{code} topic", exam_board="OCR")
            subtopic = ALevelSubTopic.objects.create(topic=topic, title=f"{code} subtopic")
            subcategory = ALevelSubCategory.objects.create(subtopic=subtopic, title=f"{code} subcategory")
            self.topics[code] = (topic, subtopic, subcategory)

    def grant(self, code):
        SubjectEntitlement.objects.create(user=self.user, subject=Subject.objects.get(code=code))

    def payload(self, qualification, code, **extra):
        topic, subtopic, subcategory = self.topics[code]
        body = {
            "qualification": qualification,
            "topic_id": topic.id,
            "subtopic_id": subtopic.id,
            "subcategory_id": subcategory.id,
            "exam_board": "OCR",
            "number_of_questions": 2,
        }
        body.update(extra)
        return body

    def assert_no_side_effects(self):
        self.assertEqual(QuestionSession.objects.count(), 0)
        self.assertEqual(ServedQuestion.objects.count(), 0)
        self.assertEqual(QuestionUsage.objects.count(), 0)


class SharedSubjectGenerationTests(SharedSubjectFixtureMixin, APITestCase):
    def test_generation_creates_shared_only_session_with_subject_scope_key(self):
        self.grant("alevel_chemistry")
        self.grant("alevel_physics")
        for qualification, code, module in SUBJECTS:
            with self.subTest(qualification=qualification):
                with patch(f"{module}.generate_questions") as mock_ai:
                    mock_ai.return_value = ai_payload(f"Explain {code} idea one. [1 mark]", f"Explain {code} idea two. [1 mark]")
                    response = self.client.post(self.generate_url, self.payload(qualification, code), format="json")
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data["qualification"], qualification)
                self.assertEqual(len(response.data["questions"]), 2)
                args, kwargs = mock_ai.call_args
                self.assertEqual(args[2], 2)
                self.assertEqual(kwargs["curriculum_context"], {
                    "topic": f"{code} topic",
                    "subtopic": f"{code} subtopic",
                    "subcategory": f"{code} subcategory",
                })
                session = QuestionSession.objects.get(pk=response.data["session_id"])
                topic, subtopic, subcategory = self.topics[code]
                self.assertEqual(session.qualification, qualification)
                self.assertEqual(
                    (session.alevel_topic, session.alevel_subtopic, session.alevel_subcategory),
                    (topic, subtopic, subcategory),
                )
                self.assertIsNone(session.topic)
                self.assertIsNone(session.subtopic)
                self.assertIsNone(session.subcategory)
                session.full_clean(exclude=["user"])
                self.assertTrue(
                    ServedQuestion.objects.filter(
                        user=self.user, scope_key=f"{code}:subcategory:{subcategory.id}"
                    ).count() == 2
                )

    def test_topic_only_selection_uses_topic_scope_key(self):
        self.grant("alevel_physics")
        topic = self.topics["alevel_physics"][0]
        with patch("examquestions.views.ai_physics.generate_questions", return_value=ai_payload("Define speed. [1 mark]")):
            response = self.client.post(self.generate_url, {
                "qualification": "ALEVEL_PHYSICS", "topic_id": topic.id,
                "exam_board": "OCR", "number_of_questions": 1,
            }, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(ServedQuestion.objects.filter(scope_key=f"alevel_physics:topic:{topic.id}").exists())

    def test_aliases_resolve_to_subjects(self):
        self.grant("alevel_chemistry")
        with patch("examquestions.views.ai_chemistry.generate_questions", return_value=ai_payload("Define mole. [1 mark]")):
            response = self.client.post(
                self.generate_url,
                self.payload("a-level chemistry", "alevel_chemistry", number_of_questions=1),
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["qualification"], "ALEVEL_CHEMISTRY")

    def test_topic_from_another_subject_is_rejected(self):
        self.grant("alevel_chemistry")
        with patch("examquestions.views.ai_chemistry.generate_questions") as mock_ai:
            response = self.client.post(
                self.generate_url, self.payload("ALEVEL_CHEMISTRY", "alevel_physics"), format="json"
            )
        self.assertEqual(response.status_code, 400)
        mock_ai.assert_not_called()
        self.assert_no_side_effects()

    def test_wrong_board_is_rejected(self):
        self.grant("alevel_chemistry")
        with patch("examquestions.views.ai_chemistry.generate_questions") as mock_ai:
            response = self.client.post(
                self.generate_url,
                self.payload("ALEVEL_CHEMISTRY", "alevel_chemistry", exam_board="AQA"),
                format="json",
            )
        self.assertEqual(response.status_code, 400)
        mock_ai.assert_not_called()
        self.assert_no_side_effects()

    def test_invalid_board_and_missing_edexcel_specification_rejected(self):
        self.grant("alevel_chemistry")
        for board in ("XYZ", "EDEXCEL"):
            response = self.client.post(
                self.generate_url,
                self.payload("ALEVEL_CHEMISTRY", "alevel_chemistry", exam_board=board),
                format="json",
            )
            self.assertEqual(response.status_code, 400)
        self.assert_no_side_effects()

    def test_subtopic_of_another_topic_and_orphan_subcategory_rejected(self):
        self.grant("alevel_chemistry")
        other_subtopic = self.topics["alevel_physics"][1]
        response = self.client.post(
            self.generate_url,
            self.payload("ALEVEL_CHEMISTRY", "alevel_chemistry", subtopic_id=other_subtopic.id, subcategory_id=None),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            self.generate_url,
            self.payload("ALEVEL_CHEMISTRY", "alevel_chemistry", subtopic_id=None),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assert_no_side_effects()

    def test_essay_not_available_for_chemistry_or_physics(self):
        for qualification, code, _ in SUBJECTS:
            response = self.client.post(
                self.generate_url,
                self.payload(qualification, code, question_type="ESSAY_25_MARK", exam_board="AQA"),
                format="json",
            )
            self.assertEqual(response.status_code, 400)
        self.assert_no_side_effects()

    def test_previously_served_questions_are_filtered_and_duplicates_collapsed(self):
        self.grant("alevel_chemistry")
        topic, subtopic, subcategory = self.topics["alevel_chemistry"]
        ServedQuestion.objects.create(
            user=self.user, exam_board="OCR",
            scope_key=f"alevel_chemistry:subcategory:{subcategory.id}",
            normalized_question="define mole.",
        )
        questions = ai_payload(
            "Define mole. [1 mark]", "Define mole. [1 mark]", "State Avogadro's law. [1 mark]",
            "Explain electronegativity. [1 mark]",
        )
        with patch("examquestions.views.ai_chemistry.generate_questions", return_value=questions):
            response = self.client.post(
                self.generate_url, self.payload("ALEVEL_CHEMISTRY", "alevel_chemistry"), format="json"
            )
        self.assertEqual(response.status_code, 200, response.data)
        texts = [q["question"] for q in response.data["questions"]]
        self.assertEqual(texts, ["State Avogadro's law. [1 mark]", "Explain electronegativity. [1 mark]"])

    def test_shortfall_without_bank_returns_controlled_error_without_side_effects(self):
        self.grant("alevel_chemistry")
        with patch("examquestions.views.ai_chemistry.generate_questions", return_value=ai_payload("Define mole. [1 mark]")), \
                patch("examquestions.views.load_fallback_bank_for_board") as mock_bank, \
                patch("examquestions.views.resolve_gcse_fallback_bank_path") as mock_gcse_bank:
            response = self.client.post(
                self.generate_url, self.payload("ALEVEL_CHEMISTRY", "alevel_chemistry"), format="json"
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(set(response.data), {"error"})
        mock_bank.assert_not_called()
        mock_gcse_bank.assert_not_called()
        self.assert_no_side_effects()

    def test_ai_errors_are_not_exposed_and_have_no_side_effects(self):
        self.grant("alevel_physics")
        secret = "sk-secret-details"
        failures = (RuntimeError(secret), TimeoutError(secret), ValueError(secret))
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch("examquestions.views.ai_physics.generate_questions", side_effect=failure):
                    response = self.client.post(
                        self.generate_url, self.payload("ALEVEL_PHYSICS", "alevel_physics"), format="json"
                    )
                self.assertEqual(response.status_code, 503)
                self.assertNotIn(secret, str(response.data))
                self.assert_no_side_effects()

    def test_malformed_ai_response_is_handled(self):
        self.grant("alevel_physics")
        for bad in ({"questions": "nope"}, {}, {"questions": [None, {"x": 1}]}):
            with patch("examquestions.views.ai_physics.generate_questions", return_value=bad):
                response = self.client.post(
                    self.generate_url, self.payload("ALEVEL_PHYSICS", "alevel_physics"), format="json"
                )
            self.assertEqual(response.status_code, 503)
        self.assert_no_side_effects()

    def test_openai_client_uses_bounded_timeout_without_retries(self):
        from .services import ai
        self.assertLess(ai.ALEVEL_OPENAI_TIMEOUT_SECONDS, 30)
        self.assertIs(ai_chemistry.get_openai_client, ai.get_alevel_openai_client)
        self.assertIs(ai_physics.get_openai_client, ai.get_alevel_openai_client)
        with self.settings(OPEN_AI_KEY="test-key"):
            ai.get_alevel_openai_client.cache_clear()
            client = ai.get_alevel_openai_client()
            ai.get_alevel_openai_client.cache_clear()
        self.assertEqual(client.max_retries, 0)
        self.assertEqual(client.timeout, ai.ALEVEL_OPENAI_TIMEOUT_SECONDS)


class SharedSubjectEntitlementTests(SharedSubjectFixtureMixin, APITestCase):
    def _generate(self, qualification, code, count=2, label=""):
        module = {"alevel_chemistry": "ai_chemistry", "alevel_physics": "ai_physics"}[code]
        texts = [f"Explain {code} {label} point {i}. [1 mark]" for i in range(count)]
        with patch(f"examquestions.views.{module}.generate_questions", return_value=ai_payload(*texts)):
            return self.client.post(
                self.generate_url,
                self.payload(qualification, code, number_of_questions=count),
                format="json",
            )

    def test_owner_has_unlimited_generation_and_no_usage(self):
        self.grant("alevel_chemistry")
        for attempt in range(3):
            response = self._generate("ALEVEL_CHEMISTRY", "alevel_chemistry", label=f"run{attempt}")
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(response.data["questions_remaining_today"])
        self.assertEqual(QuestionUsage.objects.count(), 0)

    def test_free_user_uses_shared_daily_allowance(self):
        first = self._generate("ALEVEL_CHEMISTRY", "alevel_chemistry", count=1)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data["questions_remaining_today"], 1)
        second = self._generate("ALEVEL_PHYSICS", "alevel_physics", count=1)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["questions_remaining_today"], 0)
        third = self._generate("ALEVEL_CHEMISTRY", "alevel_chemistry", count=1)
        self.assertEqual(third.status_code, 403)
        self.assertEqual(QuestionUsage.objects.get(user=self.user).question_count, 2)
        self.assertEqual(QuestionSession.objects.count(), 2)

    def test_chemistry_does_not_unlock_physics_or_biology(self):
        self.grant("alevel_chemistry")
        response = self._generate("ALEVEL_PHYSICS", "alevel_physics", count=3)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(QuestionSession.objects.count(), 0)

    def test_biology_flag_does_not_unlock_chemistry_or_physics(self):
        self.user.has_alevel_paid_access = True
        self.user.save(update_fields=["has_alevel_paid_access"])
        for qualification, code, _ in SUBJECTS:
            self.assertEqual(self._generate(qualification, code, count=3).status_code, 403)
        self.assert_no_side_effects()

    def test_chemistry_owner_does_not_get_unlimited_biology(self):
        self.grant("alevel_chemistry")
        legacy = BiologyTopic.objects.create(topic="Cells", exam_board="OCR")
        with patch("examquestions.views.generate_questions") as mock_bio:
            response = self.client.post(self.generate_url, {
                "qualification": "ALEVEL_BIOLOGY", "topic_id": legacy.id,
                "exam_board": "OCR", "number_of_questions": 3,
            }, format="json")
        self.assertEqual(response.status_code, 403)
        mock_bio.assert_not_called()


class SharedSubjectMarkingTests(SharedSubjectFixtureMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.mark_url = reverse("mark-user-answer")
        self.single = {
            "exam_board": "OCR", "question": "Q [1 mark]",
            "mark_scheme": ["x (1 mark)"], "user_answer": "a",
        }
        self.batch = {
            "exam_board": "OCR",
            "answers": [{"question": "Q [1 mark]", "mark_scheme": ["x (1 mark)"], "user_answer": "a"}],
        }

    def test_single_and_batch_marking_route_to_subject_service(self):
        for qualification, _, module in SUBJECTS:
            with self.subTest(qualification=qualification):
                single_result = {"score": 1, "out_of": 1, "feedback": "ok"}
                batch_result = {"results": [], "strengths": [], "improvements": []}
                with patch(f"{module}.evaluate_response_with_openai", return_value=single_result) as single, \
                        patch(f"{module}.evaluate_batch_responses_with_openai", return_value=batch_result) as batch, \
                        patch("examquestions.views.evaluate_response_with_openai") as bio_single, \
                        patch("examquestions.views.evaluate_batch_responses_with_openai") as bio_batch:
                    one = self.client.post(self.mark_url, {**self.single, "qualification": qualification}, format="json")
                    many = self.client.post(self.mark_url, {**self.batch, "qualification": qualification}, format="json")
                self.assertEqual(one.status_code, 200)
                self.assertEqual(one.data, single_result)
                self.assertEqual(many.status_code, 200)
                self.assertEqual(many.data, batch_result)
                single.assert_called_once()
                batch.assert_called_once()
                self.assertEqual(single.call_args.kwargs["specification"], "")
                bio_single.assert_not_called()
                bio_batch.assert_not_called()

    def test_edexcel_specification_rules_match_generation(self):
        for qualification, _, module in SUBJECTS:
            with patch(f"{module}.evaluate_response_with_openai") as single:
                missing = self.client.post(
                    self.mark_url, {**self.single, "qualification": qualification, "exam_board": "EDEXCEL"}, format="json"
                )
                extra = self.client.post(
                    self.mark_url, {**self.single, "qualification": qualification, "specification": "Spec A"}, format="json"
                )
            self.assertEqual(missing.status_code, 400)
            self.assertEqual(extra.status_code, 400)
            single.assert_not_called()

    def test_marking_failures_do_not_expose_details(self):
        secret = "sk-secret-details"
        for qualification, _, module in SUBJECTS:
            with patch(f"{module}.evaluate_response_with_openai", side_effect=RuntimeError(secret)), \
                    patch(f"{module}.evaluate_batch_responses_with_openai", side_effect=TimeoutError(secret)):
                one = self.client.post(self.mark_url, {**self.single, "qualification": qualification}, format="json")
                many = self.client.post(self.mark_url, {**self.batch, "qualification": qualification}, format="json")
            for response in (one, many):
                self.assertEqual(response.status_code, 503)
                self.assertNotIn(secret, str(response.data))

    def test_essay_marking_rejected_for_chemistry_and_physics(self):
        for qualification, _, _ in SUBJECTS:
            response = self.client.post(self.mark_url, {
                **self.single, "qualification": qualification,
                "question_type": "ESSAY_25_MARK", "exam_board": "AQA",
            }, format="json")
            self.assertEqual(response.status_code, 400)


class SharedSubjectHistoryTests(SharedSubjectFixtureMixin, APITestCase):
    def test_generate_submit_and_history_round_trip(self):
        self.grant("alevel_chemistry")
        with patch("examquestions.views.ai_chemistry.generate_questions", return_value=ai_payload("A. [1 mark]", "B. [1 mark]")):
            created = self.client.post(
                self.generate_url, self.payload("ALEVEL_CHEMISTRY", "alevel_chemistry"), format="json"
            )
        self.assertEqual(created.status_code, 200, created.data)
        submit = self.client.post(reverse("submit_question_session"), {
            "session_id": created.data["session_id"],
            "answers": [{"score": 1}, {"score": 1}],
            "feedback": "Well done",
        }, format="json")
        self.assertEqual(submit.status_code, 200, submit.data)
        self.assertEqual(submit.data["score"], 2)
        self.assertEqual(submit.data["out_of"], 2)

        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        history_url = reverse("get_user_sessions")
        with CaptureQueriesContext(connection) as one_session:
            history = self.client.get(history_url)
        topic, subtopic, subcategory = self.topics["alevel_chemistry"]
        for _ in range(3):
            QuestionSession.objects.create(
                user=self.user, qualification="ALEVEL_CHEMISTRY", alevel_topic=topic,
                alevel_subtopic=subtopic, alevel_subcategory=subcategory,
                exam_board="OCR", number_of_questions=1,
            )
        with CaptureQueriesContext(connection) as four_sessions:
            self.client.get(history_url)
        self.assertEqual(len(one_session), len(four_sessions))
        self.assertEqual(history.status_code, 200)
        item = history.data[0]
        self.assertEqual(item["qualification"], "ALEVEL_CHEMISTRY")
        self.assertEqual(item["subject"], "alevel_chemistry")
        self.assertEqual(item["level"], "A level")
        self.assertIn("alevel_chemistry topic", item["topic"])
        self.assertIn("OCR", item["topic"])
        self.assertEqual(item["topic_name"], "alevel_chemistry topic")
        self.assertEqual(item["subtopic_name"], "alevel_chemistry subtopic")
        self.assertEqual(item["subcategory_name"], "alevel_chemistry subcategory")
        self.assertEqual(item["total_score"], 2)
        self.assertEqual(item["total_available"], 2)
        self.assertEqual(item["exam_board"], "OCR")
        self.assertTrue(item["feedback"])

    def test_physics_history_exposes_subject(self):
        topic, subtopic, subcategory = self.topics["alevel_physics"]
        QuestionSession.objects.create(
            user=self.user, qualification="ALEVEL_PHYSICS", alevel_topic=topic,
            alevel_subtopic=subtopic, alevel_subcategory=subcategory,
            exam_board="OCR", number_of_questions=1,
        )
        item = self.client.get(reverse("get_user_sessions")).data[0]
        self.assertEqual(item["subject"], "alevel_physics")
        self.assertEqual(item["topic_name"], "alevel_physics topic")

    def test_legacy_biology_history_prefers_legacy_links(self):
        legacy_topic = BiologyTopic.objects.create(topic="Legacy cells", exam_board="OCR")
        legacy_subtopic = BiologySubTopic.objects.create(topic=legacy_topic, title="Legacy sub")
        legacy_subcategory = BiologySubCategory.objects.create(subtopic=legacy_subtopic, title="Legacy cat")
        topic, subtopic, subcategory = self.topics["alevel_biology"]
        QuestionSession.objects.create(
            user=self.user, qualification="ALEVEL_BIOLOGY",
            topic=legacy_topic, subtopic=legacy_subtopic, subcategory=legacy_subcategory,
            alevel_topic=topic, alevel_subtopic=subtopic, alevel_subcategory=subcategory,
            exam_board="OCR", number_of_questions=1,
        )
        QuestionSession.objects.create(
            user=self.user, qualification="ALEVEL_BIOLOGY", topic=legacy_topic,
            exam_board="OCR", number_of_questions=1,
        )
        for item in self.client.get(reverse("get_user_sessions")).data:
            self.assertEqual(item["subject"], "alevel_biology")
            self.assertEqual(item["topic_name"], "Legacy cells")
        dual = [i for i in self.client.get(reverse("get_user_sessions")).data if i["subtopic_name"]][0]
        self.assertEqual(dual["subtopic_name"], "Legacy sub")
        self.assertEqual(dual["subcategory_name"], "Legacy cat")

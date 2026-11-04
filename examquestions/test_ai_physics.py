import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from examquestions.services import ai_physics as phys
from examquestions.views import SUPPORTED_QUALIFICATIONS


def fake_response(payload):
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def mock_client(payload=None, side_effect=None):
    client = MagicMock()
    if side_effect is not None:
        client.chat.completions.create.side_effect = side_effect
    else:
        client.chat.completions.create.return_value = fake_response(payload)
    return client


def run_with(client, func, *args, **kwargs):
    with patch.object(phys, "get_openai_client", return_value=client):
        return func(*args, **kwargs)


def prompt_text(client):
    messages = client.chat.completions.create.call_args.kwargs["messages"]
    return "\n".join(m["content"] for m in messages)


GOOD_QUESTION = {
    "question": "A 0.50 kg trolley moves at 4.0 m s-1. Calculate its kinetic energy. [2 marks]",
    "total_marks": 2,
    "mark_scheme": ["Uses E = 1/2 m v^2 (1 mark)", "E = 4.0 J (1 mark)"],
}


class PhysicsGenerationTests(SimpleTestCase):
    def test_prompt_contains_board_topic_and_curriculum_context(self):
        client = mock_client({"questions": [GOOD_QUESTION]})
        context = {
            "topic": "Mechanics",
            "subtopic": "Kinematics",
            "subcategory": "Projectile motion",
            "learning_objectives": ["Resolve velocity into components"],
        }
        run_with(client, phys.generate_questions, "Kinematics", "AQA", 3, curriculum_context=context)
        text = prompt_text(client)
        for expected in ("AQA A-level Physics", "Create 3", "Kinematics", "Projectile motion",
                         "Resolve velocity into components"):
            self.assertIn(expected, text)
        self.assertNotIn("Chemistry", text)
        self.assertNotIn("Biology", text)

    def test_physics_specific_requirements(self):
        client = mock_client({"questions": [GOOD_QUESTION]})
        run_with(client, phys.generate_questions, "Waves", "AQA", 1)
        text = prompt_text(client)
        for expected in ("numerical problems", "practical investigations", "data interpretation",
                         "scalars and vectors", "significant figures", "standard form",
                         "never invent data, physical constants", "add up exactly to total_marks"):
            self.assertIn(expected, text)

    def test_ocr_uses_physics_a_baseline_without_b_claim(self):
        client = mock_client({"questions": [GOOD_QUESTION]})
        run_with(client, phys.generate_questions, "Fields", "OCR", 1)
        text = prompt_text(client)
        self.assertIn("OCR A-level Physics", text)
        self.assertIn("Physics A (H556)", text)
        self.assertIn("do not claim alignment with OCR Physics B", text)

    def test_specification_rules(self):
        client = mock_client({"questions": [GOOD_QUESTION]})
        for board in ("AQA", "OCR"):
            with self.assertRaises(ValueError):
                run_with(client, phys.generate_questions, "t", board, 1, specification="Spec A")
        with self.assertRaises(ValueError):
            run_with(client, phys.generate_questions, "t", "EDEXCEL", 1)
        with self.assertRaises(ValueError):
            run_with(client, phys.generate_questions, "t", "WJEC", 1)
        client.chat.completions.create.assert_not_called()
        run_with(client, phys.generate_questions, "t", "OCR", 1, specification=" ")
        run_with(client, phys.generate_questions, "t", "EDEXCEL", 1, specification="Spec A")
        self.assertIn("Spec A", prompt_text(client))

    def test_response_structure(self):
        client = mock_client({"questions": [GOOD_QUESTION]})
        result = run_with(client, phys.generate_questions, "Energy", "AQA", 1)
        self.assertEqual(result, {"questions": [GOOD_QUESTION]})
        self.assertEqual(
            client.chat.completions.create.call_args.kwargs["response_format"],
            {"type": "json_object"},
        )

    def test_invalid_and_inconsistent_questions_dropped(self):
        mismatched = dict(GOOD_QUESTION, total_marks=5)
        payload = {"questions": [
            GOOD_QUESTION, mismatched,
            {"question": "", "total_marks": 2, "mark_scheme": ["x"]},
            {"question": "q", "total_marks": 0, "mark_scheme": ["x"]},
            {"question": "q", "total_marks": 1, "mark_scheme": []},
            {"question": "q", "total_marks": 1, "mark_scheme": "text"},
            "junk",
        ]}
        result = run_with(mock_client(payload), phys.generate_questions, "Energy", "AQA", 7)
        self.assertEqual(result["questions"], [GOOD_QUESTION])
        with self.assertRaises(ValueError):
            run_with(mock_client({"questions": [mismatched]}), phys.generate_questions, "m", "AQA", 1)
        with self.assertRaises(ValueError):
            run_with(mock_client({"questions": "nope"}), phys.generate_questions, "m", "AQA", 1)

    def test_unlabelled_scheme_points_are_kept(self):
        question = dict(GOOD_QUESTION, mark_scheme=["Uses E = 1/2 m v^2", "E = 4.0 J"])
        result = run_with(mock_client({"questions": [question]}), phys.generate_questions, "m", "AQA", 1)
        self.assertEqual(len(result["questions"]), 1)

    def test_malformed_output_and_api_failure(self):
        with self.assertRaises(json.JSONDecodeError):
            run_with(mock_client("not json"), phys.generate_questions, "m", "AQA", 1)
        with self.assertRaises(ValueError):
            run_with(mock_client(""), phys.generate_questions, "m", "AQA", 1)
        with self.assertRaises(ValueError):
            run_with(mock_client("[1]"), phys.generate_questions, "m", "AQA", 1)
        with self.assertRaises(RuntimeError):
            run_with(mock_client(side_effect=RuntimeError("down")), phys.generate_questions, "m", "AQA", 1)


class PhysicsSingleMarkingTests(SimpleTestCase):
    scheme = ["Uses E = 1/2 m v^2 (1 mark)", "Substitution (1 mark)", "E = 4.0 J with unit"]

    def mark(self, payload, scheme=None, **kwargs):
        client = mock_client(payload)
        result = run_with(
            client, phys.evaluate_response_with_openai, "Q?", scheme or self.scheme, "my answer", "AQA", **kwargs
        )
        return client, result

    def test_contract_and_prompt_guidance(self):
        client, result = self.mark({"score": 1.5, "out_of": 3, "feedback": "Partly right."})
        self.assertEqual(result, {"score": 1.5, "out_of": 3, "feedback": "Partly right."})
        text = prompt_text(client)
        for expected in ("method marks", "valid rearrangements", "error carried forward",
                         "significant figures", "signs and directions", "my answer"):
            self.assertIn(expected, text)

    def test_score_bounds_and_authoritative_out_of(self):
        _, result = self.mark({"score": 99, "out_of": 100, "feedback": "x"})
        self.assertEqual((result["score"], result["out_of"]), (3, 3))
        _, result = self.mark({"score": -2, "out_of": 3, "feedback": "x"})
        self.assertEqual(result["score"], 0)

    def test_multi_mark_and_bracket_suffix_forms(self):
        client, result = self.mark(
            {"score": 9, "out_of": 1, "feedback": "x"}, scheme=["A (3 marks)", "B [2 marks]"]
        )
        self.assertEqual((result["score"], result["out_of"]), (5, 5))
        self.assertNotIn("[2 marks] (1 mark)", prompt_text(client))

    def test_declared_total_marks_is_respected(self):
        _, result = self.mark(
            {"score": 4, "out_of": 4, "feedback": "x"}, scheme=["A", "B"], total_marks=4
        )
        self.assertEqual(result["out_of"], 4)

    def test_empty_mark_scheme_rejected_before_api_call(self):
        client = mock_client({"score": 0, "out_of": 0, "feedback": ""})
        for scheme in ([], [""], None):
            with self.assertRaises(ValueError):
                run_with(client, phys.evaluate_response_with_openai, "Q", scheme, "a", "AQA")
        client.chat.completions.create.assert_not_called()

    def test_non_numeric_score_rejected(self):
        for bad in ("abc", None, True, float("nan")):
            with self.assertRaises(ValueError):
                self.mark({"score": bad, "out_of": 3, "feedback": "x"})

    def test_failures_and_specification_rule(self):
        with self.assertRaises(json.JSONDecodeError):
            run_with(mock_client("{bad"), phys.evaluate_response_with_openai, "Q", ["a"], "b", "AQA")
        with self.assertRaises(RuntimeError):
            run_with(mock_client(side_effect=RuntimeError("x")),
                     phys.evaluate_response_with_openai, "Q", ["a"], "b", "AQA")
        with self.assertRaises(ValueError):
            self.mark({"score": 1, "out_of": 1, "feedback": ""}, specification="Spec B")

    def test_errors_do_not_log_answers_or_prompts(self):
        with self.assertLogs("examquestions.services.ai_physics", level="ERROR") as logs:
            with self.assertRaises(RuntimeError):
                run_with(mock_client(side_effect=RuntimeError("secret-answer")),
                         phys.evaluate_response_with_openai, "Q", ["a"], "secret-answer", "AQA")
        self.assertNotIn("secret-answer", "\n".join(logs.output))


class PhysicsBatchMarkingTests(SimpleTestCase):
    answers = [
        {"question": "Q1", "mark_scheme": ["a (1 mark)", "b (1 mark)"], "user_answer": "x"},
        {"question": "Q2", "mark_scheme": ["c (3 marks)"], "user_answer": "y", "total_marks": 3},
    ]

    def good(self):
        return {
            "results": [
                {"index": 1, "score": 1, "out_of": 2, "feedback": "f1"},
                {"index": 2, "score": 2.5, "out_of": 3, "feedback": "f2"},
            ],
            "strengths": ["s1", "s2", "s3"],
            "improvements": ["i1", "i2", "i3"],
        }

    def run_batch(self, payload, answers=None, **kwargs):
        client = mock_client(payload)
        return client, run_with(
            client, phys.evaluate_batch_responses_with_openai, answers or self.answers, "OCR", **kwargs
        )

    def test_contract_and_prompt(self):
        client, result = self.run_batch(
            self.good(), curriculum_context={"topic": "Fields"}
        )
        self.assertEqual(result, self.good())
        text = prompt_text(client)
        self.assertIn("OCR A-level Physics", text)
        self.assertIn("Fields", text)

    def test_marks_bounded_and_reordered(self):
        payload = self.good()
        payload["results"] = [
            {"index": 2, "score": 10, "out_of": 99, "feedback": "f2"},
            {"index": 1, "score": -1, "out_of": 99, "feedback": "f1"},
        ]
        _, result = self.run_batch(payload)
        self.assertEqual([r["index"] for r in result["results"]], [1, 2])
        self.assertEqual([(r["score"], r["out_of"]) for r in result["results"]], [(0, 2), (3, 3)])

    def test_bad_structure_rejected(self):
        for mutate in (
            lambda p: p["results"].pop(),
            lambda p: p["results"][1].update(index=1),
            lambda p: p["results"][1].update(index=7),
            lambda p: p["results"][0].update(index="1"),
            lambda p: p["results"].__setitem__(0, "junk"),
            lambda p: p["results"][0].update(score="many"),
            lambda p: p.update(results="nope"),
        ):
            payload = self.good()
            mutate(payload)
            with self.assertRaises(ValueError):
                self.run_batch(payload)

    def test_empty_inputs_and_list_cleaning(self):
        with self.assertRaises(ValueError):
            run_with(mock_client({}), phys.evaluate_batch_responses_with_openai, [], "AQA")
        bad_scheme = [{"question": "Q", "mark_scheme": [], "user_answer": "a"}]
        with self.assertRaises(ValueError):
            run_with(mock_client({}), phys.evaluate_batch_responses_with_openai, bad_scheme, "AQA")
        payload = self.good()
        del payload["strengths"]
        payload["improvements"] = ["a", "b", "c", "d"]
        _, result = self.run_batch(payload)
        self.assertEqual(result["strengths"], [])
        self.assertEqual(result["improvements"], ["a", "b", "c"])

    def test_failures(self):
        with self.assertRaises(json.JSONDecodeError):
            run_with(mock_client("oops"), phys.evaluate_batch_responses_with_openai, self.answers, "AQA")
        with self.assertRaises(RuntimeError):
            run_with(mock_client(side_effect=RuntimeError("x")),
                     phys.evaluate_batch_responses_with_openai, self.answers, "AQA")
        with self.assertRaises(ValueError):
            self.run_batch(self.good(), specification="Spec A")


class PhysicsFeedbackTests(SimpleTestCase):
    def test_feedback_contract(self):
        client = mock_client({"strengths": ["a", "b", "c"], "improvements": ["d", "e", "f"], "extra": 1})
        result = run_with(client, phys.get_feedback_from_openai, "summary", "AQA")
        self.assertEqual(result, {"strengths": ["a", "b", "c"], "improvements": ["d", "e", "f"]})
        self.assertIn("A-level Physics", prompt_text(client))

    def test_feedback_without_board_and_failures(self):
        result = run_with(mock_client({"strengths": "bad"}), phys.get_feedback_from_openai, "summary")
        self.assertEqual(result, {"strengths": [], "improvements": []})
        with self.assertRaises(json.JSONDecodeError):
            run_with(mock_client("nope"), phys.get_feedback_from_openai, "s", "OCR")
        with self.assertRaises(ValueError):
            run_with(mock_client({}), phys.get_feedback_from_openai, "s", "AQA", "Spec A")


class PhysicsMarkAllocationTests(SimpleTestCase):
    def test_suffix_formats_and_totals(self):
        cases = [
            (["A (2 marks)"], 2),
            (["A [2 MARKS]"], 2),
            (["A (3 marks)", "B [1 mark]"], 4),
            (["A (1 mark)", "B [1 mark]", "C (1 mark)"], 3),
            (["A", "B (2 marks)"], 3),
        ]
        for scheme, total in cases:
            formatted, out_of = phys._available_marks(scheme)
            self.assertEqual(out_of, total, scheme)
            for original, point in zip(scheme, formatted):
                if phys._MARKS_SUFFIX_RE.search(original):
                    self.assertEqual(point, original)
                else:
                    self.assertTrue(point.endswith("(1 mark)"))

    def test_blank_schemes_rejected_before_api_call(self):
        client = mock_client({"score": 0, "out_of": 0, "feedback": ""})
        for scheme in ([], [""], ["  ", ""], None, "", ["A (0 marks)"]):
            with self.assertRaises(ValueError, msg=repr(scheme)):
                run_with(client, phys.evaluate_response_with_openai, "Q", scheme, "a", "AQA")
            with self.assertRaises(ValueError, msg=repr(scheme)):
                run_with(
                    client, phys.evaluate_batch_responses_with_openai,
                    [{"question": "Q", "mark_scheme": scheme, "user_answer": "a"}], "AQA",
                )
        client.chat.completions.create.assert_not_called()

    def test_declared_total_precedence_rule(self):
        with self.assertRaises(ValueError):
            phys._available_marks(["A (1 mark)", "B (1 mark)"], 5)
        self.assertEqual(phys._available_marks(["A (2 marks)", "B [1 mark]"], "3")[1], 3)
        self.assertEqual(phys._available_marks(["A", "B"], 4)[1], 4)
        self.assertEqual(phys._available_marks(["A (2 marks)"], None)[1], 2)
        self.assertEqual(phys._available_marks(["A (2 marks)"], " ")[1], 2)
        for bad in (0, -1, "abc", True, float("nan")):
            with self.assertRaises(ValueError, msg=repr(bad)):
                phys._available_marks(["A (2 marks)"], bad)

    def test_single_and_batch_consistency(self):
        scheme = ["A (2 marks)", "B [1 mark]"]
        single_client = mock_client({"score": 9, "out_of": 1, "feedback": "f"})
        single = run_with(
            single_client, phys.evaluate_response_with_openai, "Q", scheme, "a", "AQA", total_marks=3
        )
        batch_client = mock_client({
            "results": [{"index": 1, "score": 9, "out_of": 1, "feedback": "f"}],
            "strengths": [], "improvements": [],
        })
        batch = run_with(
            batch_client, phys.evaluate_batch_responses_with_openai,
            [{"question": "Q", "mark_scheme": scheme, "user_answer": "a", "total_marks": 3}], "AQA",
        )
        self.assertEqual((single["score"], single["out_of"]), (3, 3))
        self.assertEqual(batch["results"][0]["out_of"], single["out_of"])
        self.assertEqual(batch["results"][0]["score"], single["score"])
        with self.assertRaises(ValueError):
            run_with(single_client, phys.evaluate_response_with_openai, "Q", scheme, "a", "AQA", total_marks=9)
        with self.assertRaises(ValueError):
            run_with(
                batch_client, phys.evaluate_batch_responses_with_openai,
                [{"question": "Q", "mark_scheme": scheme, "user_answer": "a", "total_marks": 9}], "AQA",
            )

    def test_generation_rejects_mismatched_totals(self):
        for scheme, total, kept in (
            (["A (1 mark)", "B (1 mark)"], 2, True),
            (["A (2 marks)", "B [1 mark]"], 3, True),
            (["A (1 mark)", "B (1 mark)"], 3, False),
            (["A (2 marks)", "B [2 marks]"], 3, False),
            (["A", "B"], 5, True),
        ):
            item = {"question": "Q [x marks]", "total_marks": total, "mark_scheme": scheme}
            self.assertEqual(bool(phys._normalize_generated_question(item)), kept, scheme)

    def test_too_few_valid_questions_reported_not_fabricated(self):
        bad = dict(GOOD_QUESTION, total_marks=9)
        client = mock_client({"questions": [GOOD_QUESTION, bad]})
        with self.assertLogs("examquestions.services.ai_physics", level="WARNING") as logs:
            result = run_with(client, phys.generate_questions, "Energy", "AQA", 3)
        self.assertEqual(result, {"questions": [GOOD_QUESTION]})
        self.assertIn("1 valid question(s) of 3", "\n".join(logs.output))

    def test_error_logs_contain_exception_type_only(self):
        secret = "secret-student-answer"
        calls = (
            (phys.evaluate_response_with_openai, ("Q", ["A (1 mark)"], secret, "AQA")),
            (phys.evaluate_batch_responses_with_openai,
             ([{"question": "Q", "mark_scheme": ["A (1 mark)"], "user_answer": secret}], "AQA")),
        )
        for func, args in calls:
            with self.assertLogs("examquestions.services.ai_physics", level="ERROR") as logs:
                with self.assertRaises(RuntimeError):
                    run_with(mock_client(side_effect=RuntimeError(secret)), func, *args)
            output = "\n".join(logs.output)
            self.assertIn("RuntimeError", output)
            self.assertNotIn(secret, output)
            self.assertNotIn("A (1 mark)", output)

    def test_matches_chemistry_rules(self):
        from examquestions.services import ai_chemistry as chem
        schemes = [["A (2 marks)", "B [1 mark]"], ["A", "B (2 marks)"], ["A (1 mark)"]]
        for scheme in schemes:
            for declared in (None, "", 3, 1, "x", 0, True):
                outcomes = []
                for module in (phys, chem):
                    try:
                        outcomes.append(module._available_marks(scheme, declared))
                    except ValueError:
                        outcomes.append("ValueError")
                self.assertEqual(outcomes[0], outcomes[1], (scheme, declared))
        for item in (
            {"question": "Q", "total_marks": 3, "mark_scheme": ["A (1 mark)", "B (1 mark)"]},
            {"question": "Q", "total_marks": 2, "mark_scheme": ["A (1 mark)", "B [1 mark]"]},
        ):
            self.assertEqual(
                phys._normalize_generated_question(item), chem._normalize_generated_question(item)
            )


class PhysicsIsolationTests(SimpleTestCase):
    def test_not_enabled_in_public_api(self):
        self.assertNotIn("ALEVEL_PHYSICS", SUPPORTED_QUALIFICATIONS)
        import examquestions.views as views
        self.assertFalse(hasattr(views, "ai_physics"))

    def test_public_signatures_match_chemistry(self):
        import inspect
        from examquestions.services import ai_chemistry as chem
        for name in ("generate_questions", "evaluate_batch_responses_with_openai", "get_feedback_from_openai"):
            self.assertEqual(
                list(inspect.signature(getattr(phys, name)).parameters),
                list(inspect.signature(getattr(chem, name)).parameters),
            )
        chem_params = list(inspect.signature(chem.evaluate_response_with_openai).parameters)
        phys_params = list(inspect.signature(phys.evaluate_response_with_openai).parameters)
        self.assertEqual(phys_params[: len(chem_params)], chem_params)

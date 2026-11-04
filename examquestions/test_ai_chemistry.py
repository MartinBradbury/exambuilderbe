import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from examquestions.services import ai_chemistry as chem
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
    with patch.object(chem, "get_openai_client", return_value=client):
        return func(*args, **kwargs)


def prompt_text(client):
    messages = client.chat.completions.create.call_args.kwargs["messages"]
    return "\n".join(m["content"] for m in messages)


GOOD_QUESTION = {
    "question": "Calculate moles of NaOH in 25.0 cm3 of 0.100 mol dm-3 solution. [2 marks]",
    "total_marks": 2,
    "mark_scheme": ["Volume = 0.0250 dm3 (1 mark)", "Moles = 2.50 x 10-3 mol (1 mark)"],
}


class ChemistryGenerationTests(SimpleTestCase):
    def test_prompt_contains_board_topic_and_curriculum_context(self):
        client = mock_client({"questions": [GOOD_QUESTION]})
        context = {
            "topic": "Amount of substance",
            "subtopic": "Titrations",
            "learning_objectives": ["Carry out titration calculations"],
        }
        run_with(client, chem.generate_questions, "Titrations", "AQA", 1, curriculum_context=context)
        text = prompt_text(client)
        self.assertIn("AQA A-level Chemistry", text)
        self.assertIn("Titrations", text)
        self.assertIn("Carry out titration calculations", text)
        self.assertIn("plain text", text)
        self.assertIn("never invent data", text.lower())
        self.assertNotIn("Biology", text)

    def test_ocr_uses_chemistry_a_baseline_without_b_claim(self):
        client = mock_client({"questions": [GOOD_QUESTION]})
        run_with(client, chem.generate_questions, "Acids", "OCR", 1)
        text = prompt_text(client)
        self.assertIn("OCR A-level Chemistry", text)
        self.assertIn("Chemistry A (H432)", text)
        self.assertIn("do not claim alignment with OCR Chemistry B", text)

    def test_specification_rules(self):
        client = mock_client({"questions": [GOOD_QUESTION]})
        for board in ("AQA", "OCR"):
            with self.assertRaises(ValueError):
                run_with(client, chem.generate_questions, "t", board, 1, specification="Spec A")
        with self.assertRaises(ValueError):
            run_with(client, chem.generate_questions, "t", "EDEXCEL", 1)
        with self.assertRaises(ValueError):
            run_with(client, chem.generate_questions, "t", "WJEC", 1)
        client.chat.completions.create.assert_not_called()
        run_with(client, chem.generate_questions, "t", "AQA", 1, specification="  ")
        run_with(client, chem.generate_questions, "t", "EDEXCEL", 1, specification="Spec A")

    def test_response_structure(self):
        client = mock_client({"questions": [GOOD_QUESTION]})
        result = run_with(client, chem.generate_questions, "Moles", "AQA", 1)
        self.assertEqual(result, {"questions": [GOOD_QUESTION]})

    def test_invalid_questions_are_dropped_and_empty_raises(self):
        payload = {"questions": [GOOD_QUESTION, {"question": "", "total_marks": 2, "mark_scheme": ["x"]},
                                 {"question": "q", "total_marks": 0, "mark_scheme": ["x"]},
                                 {"question": "q", "total_marks": 1, "mark_scheme": []}, "junk"]}
        result = run_with(mock_client(payload), chem.generate_questions, "Moles", "AQA", 5)
        self.assertEqual(len(result["questions"]), 1)
        with self.assertRaises(ValueError):
            run_with(mock_client({"questions": [{"question": ""}]}), chem.generate_questions, "m", "AQA", 1)
        with self.assertRaises(ValueError):
            run_with(mock_client({"questions": "nope"}), chem.generate_questions, "m", "AQA", 1)

    def test_malformed_output_and_api_failure(self):
        with self.assertRaises(json.JSONDecodeError):
            run_with(mock_client("not json"), chem.generate_questions, "m", "AQA", 1)
        with self.assertRaises(ValueError):
            run_with(mock_client(""), chem.generate_questions, "m", "AQA", 1)
        with self.assertRaises(ValueError):
            run_with(mock_client("[1, 2]"), chem.generate_questions, "m", "AQA", 1)
        with self.assertRaises(RuntimeError):
            run_with(mock_client(side_effect=RuntimeError("down")), chem.generate_questions, "m", "AQA", 1)


class ChemistrySingleMarkingTests(SimpleTestCase):
    scheme = ["Correct equation 2H2 + O2 -> 2H2O (1 mark)", "Correct units mol dm-3 (1 mark)", "Method shown"]

    def mark(self, payload, **kwargs):
        client = mock_client(payload)
        result = run_with(
            client, chem.evaluate_response_with_openai, "Q?", self.scheme, "my answer", "AQA", **kwargs
        )
        return client, result

    def test_contract_and_partial_credit(self):
        client, result = self.mark({"score": 1.5, "out_of": 3, "feedback": "Partly right."})
        self.assertEqual(result, {"score": 1.5, "out_of": 3, "feedback": "Partly right."})
        text = prompt_text(client)
        self.assertIn("partial credit", text)
        self.assertIn("significant figures", text)
        self.assertIn("chemically equivalent", text)
        self.assertIn("my answer", text)

    def test_score_is_bounded_and_out_of_is_authoritative(self):
        _, result = self.mark({"score": 99, "out_of": 100, "feedback": "x"})
        self.assertEqual((result["score"], result["out_of"]), (3, 3))
        _, result = self.mark({"score": -4, "out_of": 3, "feedback": "x"})
        self.assertEqual(result["score"], 0)

    def test_multi_mark_points_counted(self):
        client = mock_client({"score": 5, "out_of": 4, "feedback": "x"})
        result = run_with(
            client, chem.evaluate_response_with_openai, "Q", ["Point A (3 marks)", "Point B (1 mark)"], "a", "OCR"
        )
        self.assertEqual((result["score"], result["out_of"]), (4, 4))
        self.assertNotIn("(3 marks) (1 mark)", prompt_text(client))

    def test_non_numeric_score_rejected(self):
        for bad in ("abc", None, True, float("nan")):
            with self.assertRaises(ValueError):
                self.mark({"score": bad, "out_of": 3, "feedback": "x"})

    def test_failures_and_specification_rule(self):
        with self.assertRaises(json.JSONDecodeError):
            run_with(mock_client("{bad"), chem.evaluate_response_with_openai, "Q", ["a"], "b", "AQA")
        with self.assertRaises(RuntimeError):
            run_with(mock_client(side_effect=RuntimeError("x")),
                     chem.evaluate_response_with_openai, "Q", ["a"], "b", "AQA")
        with self.assertRaises(ValueError):
            self.mark({"score": 1, "out_of": 1, "feedback": ""}, specification="Spec B")


class ChemistryBatchMarkingTests(SimpleTestCase):
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
            client, chem.evaluate_batch_responses_with_openai, answers or self.answers, "OCR", **kwargs
        )

    def test_contract(self):
        client, result = self.run_batch(self.good())
        self.assertEqual(result, self.good())
        self.assertIn("OCR A-level Chemistry", prompt_text(client))

    def test_marks_are_bounded_and_reindexed_to_input(self):
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
            lambda p: p["results"].__setitem__(0, "junk"),
            lambda p: p["results"][0].update(score="many"),
            lambda p: p.update(results="nope"),
        ):
            payload = self.good()
            mutate(payload)
            with self.assertRaises(ValueError):
                self.run_batch(payload)

    def test_empty_input_and_missing_lists(self):
        with self.assertRaises(ValueError):
            run_with(mock_client({}), chem.evaluate_batch_responses_with_openai, [], "AQA")
        payload = self.good()
        del payload["strengths"]
        payload["improvements"] = ["a", "b", "c", "d"]
        _, result = self.run_batch(payload)
        self.assertEqual(result["strengths"], [])
        self.assertEqual(result["improvements"], ["a", "b", "c"])

    def test_failures(self):
        with self.assertRaises(json.JSONDecodeError):
            run_with(mock_client("oops"), chem.evaluate_batch_responses_with_openai, self.answers, "AQA")
        with self.assertRaises(RuntimeError):
            run_with(mock_client(side_effect=RuntimeError("x")),
                     chem.evaluate_batch_responses_with_openai, self.answers, "AQA")
        with self.assertRaises(ValueError):
            self.run_batch(self.good(), specification="Spec A")


class ChemistryFeedbackTests(SimpleTestCase):
    def test_feedback_contract(self):
        client = mock_client({"strengths": ["a", "b", "c"], "improvements": ["d", "e", "f"], "extra": 1})
        result = run_with(client, chem.get_feedback_from_openai, "summary", "AQA")
        self.assertEqual(result, {"strengths": ["a", "b", "c"], "improvements": ["d", "e", "f"]})
        self.assertIn("A-level Chemistry", prompt_text(client))

    def test_feedback_without_board_and_failures(self):
        client = mock_client({"strengths": "bad"})
        result = run_with(client, chem.get_feedback_from_openai, "summary")
        self.assertEqual(result, {"strengths": [], "improvements": []})
        with self.assertRaises(json.JSONDecodeError):
            run_with(mock_client("nope"), chem.get_feedback_from_openai, "s", "OCR")
        with self.assertRaises(ValueError):
            run_with(mock_client({}), chem.get_feedback_from_openai, "s", "AQA", "Spec A")


class ChemistryMarkAllocationTests(SimpleTestCase):
    def test_suffix_formats_and_totals(self):
        cases = [
            (["A (2 marks)"], 2),
            (["A [2 marks]"], 2),
            (["A (3 marks)", "B [1 mark]"], 4),
            (["A (1 mark)", "B [1 mark]", "C (1 mark)"], 3),
            (["A", "B (2 marks)"], 3),
        ]
        for scheme, total in cases:
            formatted, out_of = chem._available_marks(scheme)
            self.assertEqual(out_of, total, scheme)
            for original, point in zip(scheme, formatted):
                if chem._MARKS_SUFFIX_RE.search(original):
                    self.assertEqual(point, original)
                else:
                    self.assertTrue(point.endswith("(1 mark)"))

    def test_blank_schemes_rejected_before_api_call(self):
        client = mock_client({"score": 0, "out_of": 0, "feedback": ""})
        for scheme in ([], [""], ["  ", ""], None, "", ["A (0 marks)"]):
            with self.assertRaises(ValueError, msg=repr(scheme)):
                run_with(client, chem.evaluate_response_with_openai, "Q", scheme, "a", "AQA")
            with self.assertRaises(ValueError, msg=repr(scheme)):
                run_with(
                    client, chem.evaluate_batch_responses_with_openai,
                    [{"question": "Q", "mark_scheme": scheme, "user_answer": "a"}], "AQA",
                )
        client.chat.completions.create.assert_not_called()

    def test_declared_total_precedence_rule(self):
        # Explicit allocations must agree with a declared total.
        with self.assertRaises(ValueError):
            chem._available_marks(["A (1 mark)", "B (1 mark)"], 5)
        self.assertEqual(chem._available_marks(["A (2 marks)", "B [1 mark]"], "3")[1], 3)
        # Unlabelled points count 1 each, so the declared total is used as given.
        self.assertEqual(chem._available_marks(["A", "B"], 4)[1], 4)
        # No declared total (None/blank): scheme total is used.
        self.assertEqual(chem._available_marks(["A (2 marks)"], None)[1], 2)
        self.assertEqual(chem._available_marks(["A (2 marks)"], " ")[1], 2)
        for bad in (0, -1, "abc", True, float("nan")):
            with self.assertRaises(ValueError, msg=repr(bad)):
                chem._available_marks(["A (2 marks)"], bad)

    def test_single_total_marks_and_single_batch_consistency(self):
        scheme = ["A (2 marks)", "B [1 mark]"]
        single_client = mock_client({"score": 9, "out_of": 1, "feedback": "f"})
        single = run_with(
            single_client, chem.evaluate_response_with_openai, "Q", scheme, "a", "AQA", total_marks=3
        )
        batch_client = mock_client({
            "results": [{"index": 1, "score": 9, "out_of": 1, "feedback": "f"}],
            "strengths": [], "improvements": [],
        })
        batch = run_with(
            batch_client, chem.evaluate_batch_responses_with_openai,
            [{"question": "Q", "mark_scheme": scheme, "user_answer": "a", "total_marks": 3}], "AQA",
        )
        self.assertEqual((single["score"], single["out_of"]), (3, 3))
        self.assertEqual(batch["results"][0]["out_of"], single["out_of"])
        self.assertEqual(batch["results"][0]["score"], single["score"])
        with self.assertRaises(ValueError):
            run_with(single_client, chem.evaluate_response_with_openai, "Q", scheme, "a", "AQA", total_marks=9)

    def test_generation_rejects_mismatched_totals_and_keeps_valid(self):
        for scheme, total, kept in (
            (["A (1 mark)", "B (1 mark)"], 2, True),
            (["A (2 marks)", "B [1 mark]"], 3, True),
            (["A (1 mark)", "B (1 mark)"], 3, False),
            (["A (2 marks)", "B [2 marks]"], 3, False),
            (["A", "B"], 5, True),  # unlabelled points: no explicit allocation to contradict
        ):
            item = {"question": "Q [x marks]", "total_marks": total, "mark_scheme": scheme}
            self.assertEqual(bool(chem._normalize_generated_question(item)), kept, scheme)

    def test_too_few_valid_questions_reported_not_fabricated(self):
        bad = dict(GOOD_QUESTION, total_marks=9)
        client = mock_client({"questions": [GOOD_QUESTION, bad]})
        with self.assertLogs("examquestions.services.ai_chemistry", level="WARNING") as logs:
            result = run_with(client, chem.generate_questions, "Moles", "AQA", 3)
        self.assertEqual(result, {"questions": [GOOD_QUESTION]})
        self.assertIn("1 valid question(s) of 3", "\n".join(logs.output))
        with self.assertRaises(ValueError):
            run_with(mock_client({"questions": [bad]}), chem.generate_questions, "Moles", "AQA", 1)

    def test_error_logs_contain_exception_type_only(self):
        secret = "secret-student-answer"
        calls = (
            (chem.evaluate_response_with_openai, ("Q", ["A (1 mark)"], secret, "AQA")),
            (chem.evaluate_batch_responses_with_openai,
             ([{"question": "Q", "mark_scheme": ["A (1 mark)"], "user_answer": secret}], "AQA")),
        )
        for func, args in calls:
            with self.assertLogs("examquestions.services.ai_chemistry", level="ERROR") as logs:
                with self.assertRaises(RuntimeError):
                    run_with(mock_client(side_effect=RuntimeError(secret)), func, *args)
            output = "\n".join(logs.output)
            self.assertIn("RuntimeError", output)
            self.assertNotIn(secret, output)
            self.assertNotIn("A (1 mark)", output)


class ChemistryIsolationTests(SimpleTestCase):
    def test_enabled_in_public_api(self):
        self.assertIn("ALEVEL_CHEMISTRY", SUPPORTED_QUALIFICATIONS)
        import examquestions.views as views
        self.assertIs(views.ai_chemistry, chem)

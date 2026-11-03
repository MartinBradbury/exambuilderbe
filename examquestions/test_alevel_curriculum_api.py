from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import Subject

from .models import (
    ALevelSubCategory,
    ALevelSubTopic,
    ALevelTopic,
    BiologySubCategory,
    BiologySubTopic,
    BiologyTopic,
    GCSEScienceSubCategory,
    GCSEScienceSubTopic,
    GCSEScienceTopic,
)

User = get_user_model()


class ALevelCurriculumApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="curriculum-user", email="curriculum@example.com", password="pw12345!"
        )
        cls.chem = Subject.objects.get(code="alevel_chemistry")
        cls.phys = Subject.objects.get(code="alevel_physics")
        cls.bio = Subject.objects.get(code="alevel_biology")

        cls.chem_aqa_10 = ALevelTopic.objects.create(
            subject=cls.chem, exam_board="AQA", specification="", topic="3.10 Late topic"
        )
        cls.chem_aqa_2 = ALevelTopic.objects.create(
            subject=cls.chem, exam_board="AQA", specification="", topic="3.2 Early topic"
        )
        cls.chem_ocr = ALevelTopic.objects.create(
            subject=cls.chem, exam_board="OCR", specification="", topic="Module 1"
        )
        cls.phys_aqa = ALevelTopic.objects.create(
            subject=cls.phys, exam_board="AQA", specification="", topic="Measurements"
        )
        cls.phys_ocr = ALevelTopic.objects.create(
            subject=cls.phys, exam_board="OCR", specification="", topic="Module 2"
        )
        cls.bio_aqa = ALevelTopic.objects.create(
            subject=cls.bio, exam_board="AQA", specification="", topic="3.1 Biological molecules"
        )
        cls.chem_edx_a = ALevelTopic.objects.create(
            subject=cls.chem, exam_board="EDEXCEL", specification="Spec A", topic="Topic A"
        )
        cls.chem_edx_b = ALevelTopic.objects.create(
            subject=cls.chem, exam_board="EDEXCEL", specification="Spec B", topic="Topic B"
        )

        cls.sub_10 = ALevelSubTopic.objects.create(topic=cls.chem_aqa_2, title="3.2.10 Ten")
        cls.sub_2 = ALevelSubTopic.objects.create(topic=cls.chem_aqa_2, title="3.2.2 Two")
        cls.sub_phys = ALevelSubTopic.objects.create(topic=cls.phys_aqa, title="1.1 Units")
        cls.sub_edx_a = ALevelSubTopic.objects.create(topic=cls.chem_edx_a, title="A sub")

        cls.cat_10 = ALevelSubCategory.objects.create(subtopic=cls.sub_2, title="3.2.2.10 Ten")
        cls.cat_2 = ALevelSubCategory.objects.create(subtopic=cls.sub_2, title="3.2.2.2 Two")
        cls.cat_phys = ALevelSubCategory.objects.create(subtopic=cls.sub_phys, title="Cat P")

    def setUp(self):
        self.client.force_authenticate(self.user)

    def topics(self, **params):
        return self.client.get(reverse("alevel-topics"), params)

    def subtopics(self, **params):
        return self.client.get(reverse("alevel-subtopics"), params)

    def subcategories(self, **params):
        return self.client.get(reverse("alevel-subcategories"), params)

    # Authentication
    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        for name in ("alevel-topics", "alevel-subtopics", "alevel-subcategories"):
            response = self.client.get(
                reverse(name), {"subject": "alevel_chemistry", "exam_board": "AQA"}
            )
            self.assertEqual(response.status_code, 401, name)

    # Topics
    def test_topics_shape_and_natural_ordering(self):
        response = self.topics(subject="alevel_chemistry", exam_board="AQA")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "id": self.chem_aqa_2.id,
                    "topic": "3.2 Early topic",
                    "subject": "alevel_chemistry",
                    "exam_board": "AQA",
                    "specification": "",
                },
                {
                    "id": self.chem_aqa_10.id,
                    "topic": "3.10 Late topic",
                    "subject": "alevel_chemistry",
                    "exam_board": "AQA",
                    "specification": "",
                },
            ],
        )

    def test_topics_per_subject_and_board(self):
        cases = [
            ("alevel_chemistry", "OCR", {self.chem_ocr.id}),
            ("alevel_physics", "AQA", {self.phys_aqa.id}),
            ("alevel_physics", "OCR", {self.phys_ocr.id}),
            ("alevel_biology", "AQA", {self.bio_aqa.id}),
        ]
        for subject, board, expected in cases:
            response = self.topics(subject=subject, exam_board=board)
            self.assertEqual(response.status_code, 200)
            self.assertEqual({row["id"] for row in response.json()}, expected)

    def test_topics_input_is_normalised(self):
        response = self.topics(subject=" ALEVEL_PHYSICS ", exam_board="aqa", specification="")
        self.assertEqual([r["id"] for r in response.json()], [self.phys_aqa.id])

    def test_topics_query_count_is_constant(self):
        with CaptureQueriesContext(connection) as ctx:
            self.topics(subject="alevel_chemistry", exam_board="AQA")
        self.assertLessEqual(len(ctx), 4)

    def test_required_and_invalid_params(self):
        bad = [
            {},
            {"exam_board": "AQA"},
            {"subject": "alevel_chemistry"},
            {"subject": "nonsense", "exam_board": "AQA"},
            {"subject": "alevel_chemistry", "exam_board": "WJEC"},
        ]
        for params in bad:
            for call in (self.topics, self.subtopics, self.subcategories):
                response = call(**params)
                self.assertEqual(response.status_code, 400, (call.__name__, params))
                self.assertIn("error", response.json())

    def test_aqa_ocr_reject_specification(self):
        for board in ("AQA", "OCR"):
            response = self.topics(
                subject="alevel_chemistry", exam_board=board, specification="Spec A"
            )
            self.assertEqual(response.status_code, 400)
        ok = self.topics(subject="alevel_chemistry", exam_board="AQA", specification="")
        self.assertEqual(ok.status_code, 200)

    def test_edexcel_requires_specification(self):
        response = self.topics(subject="alevel_chemistry", exam_board="EDEXCEL")
        self.assertEqual(response.status_code, 400)

    def test_edexcel_returns_only_matching_specification(self):
        response = self.topics(
            subject="alevel_chemistry", exam_board="EDEXCEL", specification="Spec A"
        )
        self.assertEqual([r["id"] for r in response.json()], [self.chem_edx_a.id])
        self.assertEqual(response.json()[0]["specification"], "Spec A")

    def test_edexcel_absent_specification_returns_empty(self):
        response = self.topics(
            subject="alevel_physics", exam_board="EDEXCEL", specification="Spec A"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_inactive_and_gcse_subjects_rejected(self):
        response = self.topics(subject="gcse_science", exam_board="AQA")
        self.assertEqual(response.status_code, 400)
        Subject.objects.filter(pk=self.phys.pk).update(is_active=False)
        response = self.topics(subject="alevel_physics", exam_board="AQA")
        self.assertEqual(response.status_code, 400)
        response = self.subtopics(
            subject="alevel_physics", exam_board="AQA", topic_id=self.phys_aqa.id
        )
        self.assertEqual(response.status_code, 400)

    # Subtopics
    def test_subtopics_shape_and_natural_ordering(self):
        response = self.subtopics(
            subject="alevel_chemistry", exam_board="AQA", topic_id=self.chem_aqa_2.id
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {"id": self.sub_2.id, "title": "3.2.2 Two", "topic": self.chem_aqa_2.id},
                {"id": self.sub_10.id, "title": "3.2.10 Ten", "topic": self.chem_aqa_2.id},
            ],
        )

    def test_subtopics_param_validation(self):
        base = {"subject": "alevel_chemistry", "exam_board": "AQA"}
        for value in (None, "", "abc", "-1", "1.5", "99999999999999"):
            params = dict(base)
            if value is not None:
                params["topic_id"] = value
            self.assertEqual(self.subtopics(**params).status_code, 400, value)

    def test_subtopics_isolation(self):
        base_id = self.chem_aqa_2.id
        mismatches = [
            {"subject": "alevel_physics", "exam_board": "AQA"},
            {"subject": "alevel_chemistry", "exam_board": "OCR"},
            {"subject": "alevel_chemistry", "exam_board": "EDEXCEL", "specification": "Spec A"},
        ]
        for params in mismatches:
            response = self.subtopics(topic_id=base_id, **params)
            self.assertEqual(response.status_code, 404, params)
        response = self.subtopics(
            subject="alevel_chemistry",
            exam_board="EDEXCEL",
            specification="Spec B",
            topic_id=self.chem_edx_a.id,
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self.subtopics(
                subject="alevel_chemistry", exam_board="AQA", topic_id=9999999
            ).status_code,
            404,
        )

    def test_subtopics_edexcel_matching(self):
        response = self.subtopics(
            subject="alevel_chemistry",
            exam_board="EDEXCEL",
            specification="Spec A",
            topic_id=self.chem_edx_a.id,
        )
        self.assertEqual([r["id"] for r in response.json()], [self.sub_edx_a.id])

    # Subcategories
    def test_subcategories_shape_and_natural_ordering(self):
        response = self.subcategories(
            subject="alevel_chemistry", exam_board="AQA", subtopic_id=self.sub_2.id
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {"id": self.cat_2.id, "title": "3.2.2.2 Two", "subtopic": self.sub_2.id},
                {"id": self.cat_10.id, "title": "3.2.2.10 Ten", "subtopic": self.sub_2.id},
            ],
        )

    def test_subcategories_param_validation(self):
        base = {"subject": "alevel_chemistry", "exam_board": "AQA"}
        self.assertEqual(self.subcategories(**base).status_code, 400)
        self.assertEqual(self.subcategories(subtopic_id="x", **base).status_code, 400)

    def test_subcategories_isolation(self):
        for params in (
            {"subject": "alevel_physics", "exam_board": "AQA"},
            {"subject": "alevel_chemistry", "exam_board": "OCR"},
            {"subject": "alevel_chemistry", "exam_board": "EDEXCEL", "specification": "Spec B"},
        ):
            response = self.subcategories(subtopic_id=self.sub_2.id, **params)
            self.assertEqual(response.status_code, 404, params)
        ok = self.subcategories(
            subject="alevel_physics", exam_board="AQA", subtopic_id=self.sub_phys.id
        )
        self.assertEqual([r["id"] for r in ok.json()], [self.cat_phys.id])

    # Legacy endpoints untouched
    def test_legacy_endpoints_still_work(self):
        self.assertEqual(self.client.get("/api/biology-topics/").status_code, 200)
        self.assertEqual(
            self.client.get("/api/gcse-topics/", {"exam_board": "AQA"}).status_code, 200
        )


class LegacyEndpointsUnchangedTests(APITestCase):
    """Legacy Biology/GCSE contracts must be unaffected by shared A-level data."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="legacy-user", email="legacy@example.com", password="pw12345!"
        )
        # Deterministic fixtures: start from empty legacy tables.
        for model in (BiologyTopic, GCSEScienceTopic):
            model.objects.all().delete()

        cls.bio_a = BiologyTopic.objects.create(
            topic="Cells", exam_board="AQA", specification=""
        )
        cls.bio_b = BiologyTopic.objects.create(
            topic="Ecology", exam_board="AQA", specification=""
        )
        cls.bio_ocr = BiologyTopic.objects.create(
            topic="Cells", exam_board="OCR", specification=""
        )
        cls.bio_sub_a = BiologySubTopic.objects.create(topic=cls.bio_a, title="Cell structure")
        cls.bio_sub_b = BiologySubTopic.objects.create(topic=cls.bio_a, title="Cell division")
        cls.bio_sub_ocr = BiologySubTopic.objects.create(topic=cls.bio_ocr, title="OCR sub")
        cls.bio_cat_a = BiologySubCategory.objects.create(
            subtopic=cls.bio_sub_a, title="Organelles"
        )
        cls.bio_cat_b = BiologySubCategory.objects.create(
            subtopic=cls.bio_sub_a, title="Microscopy"
        )

        cls.gcse_bio_f = GCSEScienceTopic.objects.create(
            topic="Cell biology", exam_board="AQA", specification="",
            subject="BIOLOGY", tier="FOUNDATION",
        )
        cls.gcse_chem_h = GCSEScienceTopic.objects.create(
            topic="Atomic structure", exam_board="AQA", specification="",
            subject="CHEMISTRY", tier="HIGHER",
        )
        cls.gcse_ocr = GCSEScienceTopic.objects.create(
            topic="Particles", exam_board="OCR", specification="",
            subject="PHYSICS", tier="HIGHER",
        )
        cls.gcse_sub_a = GCSEScienceSubTopic.objects.create(
            topic=cls.gcse_bio_f, title="Transport in cells"
        )
        cls.gcse_sub_b = GCSEScienceSubTopic.objects.create(
            topic=cls.gcse_bio_f, title="Cell structure"
        )
        cls.gcse_cat_a = GCSEScienceSubCategory.objects.create(
            subtopic=cls.gcse_sub_a, title="Osmosis"
        )
        cls.gcse_cat_b = GCSEScienceSubCategory.objects.create(
            subtopic=cls.gcse_sub_a, title="Diffusion"
        )

    def setUp(self):
        self.client.force_authenticate(self.user)

    def legacy_snapshot(self):
        calls = [
            ("/api/biology-topics/", {}),
            ("/api/biology-topics/", {"exam_board": "AQA"}),
            ("/api/biology-subtopics/", {"topic_id": self.bio_a.id}),
            ("/api/biology-subcategories/", {"subtopic_id": self.bio_sub_a.id}),
            ("/api/gcse-topics/", {}),
            ("/api/gcse-topics/", {"exam_board": "AQA", "subject": "BIOLOGY"}),
            ("/api/gcse-subtopics/", {"topic_id": self.gcse_bio_f.id}),
            ("/api/gcse-subcategories/", {"subtopic_id": self.gcse_sub_a.id}),
        ]
        snapshot = []
        for url, params in calls:
            response = self.client.get(url, params)
            self.assertEqual(response.status_code, 200, url)
            snapshot.append(response.json())
        return snapshot

    def test_biology_topics_exact_response(self):
        response = self.client.get("/api/biology-topics/", {"exam_board": "AQA"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {"id": self.bio_a.id, "topic": "Cells", "exam_board": "AQA", "specification": ""},
                {"id": self.bio_b.id, "topic": "Ecology", "exam_board": "AQA", "specification": ""},
            ],
        )

    def test_biology_topics_unfiltered_ordered_by_topic(self):
        response = self.client.get("/api/biology-topics/")
        self.assertEqual(
            [(r["topic"], r["exam_board"]) for r in response.json()],
            [("Cells", "AQA"), ("Cells", "OCR"), ("Ecology", "AQA")],
        )
        for row in response.json():
            self.assertEqual(set(row), {"id", "topic", "exam_board", "specification"})

    def test_biology_subtopics_exact_response(self):
        response = self.client.get("/api/biology-subtopics/", {"topic_id": self.bio_a.id})
        self.assertEqual(
            response.json(),
            [
                {"id": self.bio_sub_b.id, "title": "Cell division", "topic": self.bio_a.id},
                {"id": self.bio_sub_a.id, "title": "Cell structure", "topic": self.bio_a.id},
            ],
        )

    def test_biology_subcategories_exact_response(self):
        response = self.client.get(
            "/api/biology-subcategories/", {"subtopic_id": self.bio_sub_a.id}
        )
        self.assertEqual(
            response.json(),
            [
                {"id": self.bio_cat_b.id, "title": "Microscopy", "subtopic": self.bio_sub_a.id},
                {"id": self.bio_cat_a.id, "title": "Organelles", "subtopic": self.bio_sub_a.id},
            ],
        )

    def test_gcse_topics_exact_response(self):
        response = self.client.get("/api/gcse-topics/", {"exam_board": "AQA"})
        self.assertEqual(
            response.json(),
            [
                {
                    "id": self.gcse_chem_h.id, "topic": "Atomic structure",
                    "subject": "CHEMISTRY", "tier": "HIGHER",
                    "exam_board": "AQA", "specification": "",
                },
                {
                    "id": self.gcse_bio_f.id, "topic": "Cell biology",
                    "subject": "BIOLOGY", "tier": "FOUNDATION",
                    "exam_board": "AQA", "specification": "",
                },
            ],
        )

    def test_gcse_topics_subject_tier_filters(self):
        response = self.client.get(
            "/api/gcse-topics/", {"subject": "PHYSICS", "tier": "HIGHER"}
        )
        self.assertEqual(
            response.json(),
            [
                {
                    "id": self.gcse_ocr.id, "topic": "Particles",
                    "subject": "PHYSICS", "tier": "HIGHER",
                    "exam_board": "OCR", "specification": "",
                }
            ],
        )

    def test_gcse_subtopics_exact_response(self):
        response = self.client.get("/api/gcse-subtopics/", {"topic_id": self.gcse_bio_f.id})
        self.assertEqual(
            response.json(),
            [
                {"id": self.gcse_sub_b.id, "title": "Cell structure", "topic": self.gcse_bio_f.id},
                {"id": self.gcse_sub_a.id, "title": "Transport in cells", "topic": self.gcse_bio_f.id},
            ],
        )

    def test_gcse_subcategories_exact_response(self):
        response = self.client.get(
            "/api/gcse-subcategories/", {"subtopic_id": self.gcse_sub_a.id}
        )
        self.assertEqual(
            response.json(),
            [
                {"id": self.gcse_cat_b.id, "title": "Diffusion", "subtopic": self.gcse_sub_a.id},
                {"id": self.gcse_cat_a.id, "title": "Osmosis", "subtopic": self.gcse_sub_a.id},
            ],
        )

    def test_shared_alevel_data_does_not_change_legacy_results(self):
        before = self.legacy_snapshot()

        chem = Subject.objects.get(code="alevel_chemistry")
        bio = Subject.objects.get(code="alevel_biology")
        for subject, name in ((chem, "Cells"), (bio, "Cells"), (chem, "Atomic structure")):
            topic = ALevelTopic.objects.create(
                subject=subject, exam_board="AQA", specification="", topic=name
            )
            sub = ALevelSubTopic.objects.create(topic=topic, title="Cell structure")
            ALevelSubCategory.objects.create(subtopic=sub, title="Organelles")

        self.assertEqual(self.legacy_snapshot(), before)

    def test_legacy_ids_not_resolved_by_alevel_endpoints(self):
        base = {"subject": "alevel_biology", "exam_board": "AQA"}
        self.assertEqual(
            self.client.get(
                "/api/alevel-subtopics/", {**base, "topic_id": self.bio_a.id}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                "/api/alevel-subcategories/", {**base, "subtopic_id": self.bio_sub_a.id}
            ).status_code,
            404,
        )
        topics = self.client.get("/api/alevel-topics/", base).json()
        self.assertEqual(topics, [])

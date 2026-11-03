# models.py
from django.db import models
from django.core.validators import MinValueValidator
from accounts.models import CustomUser, Subject
from .curriculum_ordering import SORT_KEY_MAX_LENGTH, natural_sort_key


class ExamBoard(models.TextChoices):
    OCR = "OCR", "OCR"
    AQA = "AQA", "AQA"
    EDEXCEL = "EDEXCEL", "Edexcel"


class QualificationPath(models.TextChoices):
    ALEVEL_BIOLOGY = "ALEVEL_BIOLOGY", "A-level Biology"
    GCSE_SCIENCE = "GCSE_SCIENCE", "GCSE Science"
    ALEVEL_CHEMISTRY = "ALEVEL_CHEMISTRY", "A-level Chemistry"
    ALEVEL_PHYSICS = "ALEVEL_PHYSICS", "A-level Physics"


class GCSESubject(models.TextChoices):
    BIOLOGY = "BIOLOGY", "Biology"
    CHEMISTRY = "CHEMISTRY", "Chemistry"
    PHYSICS = "PHYSICS", "Physics"
    COMBINED = "COMBINED", "Combined Science"


class GCSEScienceRoute(models.TextChoices):
    COMBINED = "combined", "Combined"
    SEPARATE = "separate", "Separate"


class GCSETier(models.TextChoices):
    FOUNDATION = "FOUNDATION", "Foundation"
    HIGHER = "HIGHER", "Higher"


class BiologyTopic(models.Model):
    topic = models.CharField(max_length=255)  # ⬅ remove unique=True
    exam_board = models.CharField(
        max_length=8,
        choices=ExamBoard.choices,
        default=ExamBoard.OCR,
        db_index=True,
    )
    specification = models.CharField(max_length=100, blank=True, default="", db_index=True)

    class Meta:
        ordering = ["exam_board", "specification", "topic"]
        constraints = [
            models.UniqueConstraint(
                fields=["topic", "exam_board", "specification"],
                name="uniq_topic_per_exam_board",
            )
        ]

    def __str__(self):
        if self.specification:
            return f"{self.topic} ({self.exam_board} - {self.specification})"
        return f"{self.topic} ({self.exam_board})"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.exam_board == ExamBoard.EDEXCEL and not self.specification:
            raise ValidationError("Edexcel topics require a specification.")


class GCSEScienceTopic(models.Model):
    topic = models.CharField(max_length=255)
    exam_board = models.CharField(
        max_length=8,
        choices=ExamBoard.choices,
        default=ExamBoard.OCR,
        db_index=True,
    )
    subject = models.CharField(
        max_length=16,
        choices=GCSESubject.choices,
        db_index=True,
    )
    tier = models.CharField(
        max_length=16,
        choices=GCSETier.choices,
        default=GCSETier.HIGHER,
        db_index=True,
    )
    specification = models.CharField(max_length=100, blank=True, default="", db_index=True)

    class Meta:
        ordering = ["exam_board", "specification", "subject", "tier", "topic"]
        constraints = [
            models.UniqueConstraint(
                fields=["topic", "exam_board", "specification", "subject", "tier"],
                name="uniq_gcse_topic_per_board_subject",
            )
        ]

    def __str__(self):
        if self.specification:
            return (
                f"{self.get_subject_display()} | {self.get_tier_display()} | "
                f"{self.topic} ({self.exam_board} - {self.specification})"
            )
        return f"{self.get_subject_display()} | {self.get_tier_display()} | {self.topic} ({self.exam_board})"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.exam_board == ExamBoard.EDEXCEL and not self.specification:
            raise ValidationError("Edexcel GCSE topics require a specification.")


class GCSEScienceSubTopic(models.Model):
    topic = models.ForeignKey(
        GCSEScienceTopic,
        on_delete=models.CASCADE,
        related_name="subtopics",
    )
    title = models.CharField(max_length=200)

    class Meta:
        ordering = ["topic__exam_board", "topic__subject", "topic__tier", "topic__topic", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["topic", "title"],
                name="uniq_gcse_subtopic_per_topic",
            ),
        ]

    def __str__(self):
        return f"{self.topic} – {self.title}"


class GCSEScienceSubCategory(models.Model):
    subtopic = models.ForeignKey(
        GCSEScienceSubTopic,
        on_delete=models.CASCADE,
        related_name="subcategories",
    )
    title = models.CharField(max_length=200)

    class Meta:
        ordering = ["subtopic__topic__exam_board", "subtopic__topic__subject", "subtopic__topic__tier", "subtopic__topic__topic", "subtopic__title", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["subtopic", "title"],
                name="uniq_gcse_subcategory_per_subtopic",
            ),
        ]

    def __str__(self):
        return f"{self.subtopic} – {self.title}"


class BiologySubTopic(models.Model):
    topic = models.ForeignKey(
        BiologyTopic,
        on_delete=models.CASCADE,
        related_name="subtopics",
    )
    title = models.CharField(max_length=200)

    class Meta:
        ordering = ["topic__exam_board", "topic__topic", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["topic", "title"],
                name="uniq_subtopic_per_topic",
            ),
        ]

    def __str__(self):
        return f"{self.topic} – {self.title}"


class BiologySubCategory(models.Model):
    subtopic = models.ForeignKey(
        BiologySubTopic,
        on_delete=models.CASCADE,
        related_name="subcategories",
    )
    title = models.CharField(max_length=200)

    class Meta:
        ordering = ["subtopic__topic__exam_board", "subtopic__topic__topic", "subtopic__title", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["subtopic", "title"],
                name="uniq_subcategory_per_subtopic",
            ),
        ]

    def __str__(self):
        return f"{self.subtopic} – {self.title}"


class ALevelTopic(models.Model):
    subject = models.ForeignKey(
        Subject,
        on_delete=models.PROTECT,
        related_name="alevel_topics",
    )
    topic = models.CharField(max_length=255)
    exam_board = models.CharField(
        max_length=8,
        choices=ExamBoard.choices,
        db_index=True,
    )
    specification = models.CharField(max_length=100, blank=True, default="", db_index=True)
    sort_key = models.CharField(
        max_length=SORT_KEY_MAX_LENGTH, blank=True, default="", editable=False, db_index=True,
    )

    class Meta:
        ordering = ["subject__code", "exam_board", "specification", "sort_key", "topic"]
        constraints = [
            models.UniqueConstraint(
                fields=["subject", "topic", "exam_board", "specification"],
                name="uniq_alevel_topic_per_subject_board_spec",
            )
        ]

    def __str__(self):
        if self.specification:
            return f"{self.subject.name}: {self.topic} ({self.exam_board} - {self.specification})"
        return f"{self.subject.name}: {self.topic} ({self.exam_board})"

    def save(self, *args, **kwargs):
        self.sort_key = natural_sort_key(self.topic)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "topic" in update_fields:
            kwargs["update_fields"] = set(update_fields) | {"sort_key"}
        super().save(*args, **kwargs)

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.subject_id and self.subject.qualification != Subject.Qualification.A_LEVEL:
            raise ValidationError({"subject": "A-level topics require an A-level subject."})
        if self.exam_board == ExamBoard.EDEXCEL and not self.specification:
            raise ValidationError("Edexcel topics require a specification.")


class ALevelSubTopic(models.Model):
    topic = models.ForeignKey(
        ALevelTopic,
        on_delete=models.PROTECT,
        related_name="subtopics",
    )
    title = models.CharField(max_length=200)

    class Meta:
        ordering = ["topic__subject__code", "topic__exam_board", "topic__topic", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["topic", "title"],
                name="uniq_alevel_subtopic_per_topic",
            ),
        ]

    def __str__(self):
        return f"{self.topic} – {self.title}"


class ALevelSubCategory(models.Model):
    subtopic = models.ForeignKey(
        ALevelSubTopic,
        on_delete=models.PROTECT,
        related_name="subcategories",
    )
    title = models.CharField(max_length=200)

    class Meta:
        ordering = [
            "subtopic__topic__subject__code",
            "subtopic__topic__exam_board",
            "subtopic__topic__topic",
            "subtopic__title",
            "title",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["subtopic", "title"],
                name="uniq_alevel_subcategory_per_subtopic",
            ),
        ]

    def __str__(self):
        return f"{self.subtopic} – {self.title}"


class QuestionSession(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    topic = models.ForeignKey(BiologyTopic, on_delete=models.CASCADE, null=True, blank=True)
    subtopic = models.ForeignKey(BiologySubTopic, on_delete=models.SET_NULL, null=True, blank=True)
    subcategory = models.ForeignKey(BiologySubCategory, on_delete=models.SET_NULL, null=True, blank=True)
    gcse_topic = models.ForeignKey("GCSEScienceTopic", on_delete=models.SET_NULL, null=True, blank=True)
    gcse_subtopic = models.ForeignKey("GCSEScienceSubTopic", on_delete=models.SET_NULL, null=True, blank=True)
    gcse_subcategory = models.ForeignKey("GCSEScienceSubCategory", on_delete=models.SET_NULL, null=True, blank=True)
    alevel_topic = models.ForeignKey("ALevelTopic", on_delete=models.PROTECT, null=True, blank=True, related_name="sessions")
    alevel_subtopic = models.ForeignKey("ALevelSubTopic", on_delete=models.PROTECT, null=True, blank=True, related_name="sessions")
    alevel_subcategory = models.ForeignKey("ALevelSubCategory", on_delete=models.PROTECT, null=True, blank=True, related_name="sessions")

    qualification = models.CharField(
        max_length=32,
        choices=QualificationPath.choices,
        default=QualificationPath.ALEVEL_BIOLOGY,
    )
    exam_board = models.CharField(max_length=50)
    specification = models.CharField(max_length=100, blank=True, default="")
    gcse_subject = models.CharField(max_length=16, choices=GCSESubject.choices, blank=True)
    science_route = models.CharField(max_length=16, choices=GCSEScienceRoute.choices, blank=True)
    gcse_tier = models.CharField(max_length=16, choices=GCSETier.choices, blank=True)
    number_of_questions = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    total_score = models.PositiveIntegerField(default=0)
    total_available = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    feedback = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        topic_label = self.topic or self.gcse_topic or "No topic"
        return (
            f"Session by {self.user.username} | "
            f"Topic: {topic_label} | "
            f"Board: {self.exam_board} | "
            f"Score: {self.total_score}/{self.total_available} | "
            f"{self.created_at.strftime('%d %b %Y')}"
        )

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.total_score > self.total_available:
            raise ValidationError("Total score cannot exceed total available.")
        if self.exam_board == ExamBoard.EDEXCEL and not self.specification:
            raise ValidationError("Edexcel sessions require a specification.")
        if self.qualification == QualificationPath.ALEVEL_BIOLOGY:
            if not self.topic_id:
                raise ValidationError("A-level Biology sessions require a topic.")
            if self.gcse_topic_id or self.gcse_subject or self.gcse_tier or self.science_route:
                raise ValidationError("A-level Biology sessions cannot include GCSE fields.")
            if self.subtopic and self.subtopic.topic_id != self.topic_id:
                raise ValidationError("Selected subtopic doesn’t belong to the chosen topic.")
            if self.subcategory and self.subcategory.subtopic.topic_id != self.topic_id:
                raise ValidationError("Selected subcategory doesn’t belong to the chosen topic.")
            self._clean_alevel_curriculum_links()
        elif self.qualification == QualificationPath.GCSE_SCIENCE:
            if self.alevel_topic_id or self.alevel_subtopic_id or self.alevel_subcategory_id:
                raise ValidationError("GCSE Science sessions cannot include A-level curriculum fields.")
            if not self.gcse_topic_id:
                raise ValidationError("GCSE Science sessions require a GCSE topic.")
            if not self.gcse_subject or not self.gcse_tier or not self.science_route:
                raise ValidationError("GCSE Science sessions require subject, tier, and science route.")
            if self.topic_id or self.subtopic_id or self.subcategory_id:
                raise ValidationError("GCSE Science sessions cannot include A-level Biology topic fields.")
            if self.gcse_topic and self.gcse_topic.subject != self.gcse_subject:
                raise ValidationError("Selected GCSE topic does not match the GCSE subject.")
            if self.gcse_topic and self.gcse_topic.tier != self.gcse_tier:
                raise ValidationError("Selected GCSE topic does not match the GCSE tier.")
            expected_route = GCSEScienceRoute.COMBINED if self.gcse_subject == GCSESubject.COMBINED else GCSEScienceRoute.SEPARATE
            if self.science_route != expected_route:
                raise ValidationError("GCSE science route does not match the selected GCSE subject.")
            if self.gcse_subtopic and self.gcse_subtopic.topic_id != self.gcse_topic_id:
                raise ValidationError("Selected GCSE subtopic does not belong to the chosen GCSE topic.")
            if self.gcse_subcategory and self.gcse_subcategory.subtopic.topic_id != self.gcse_topic_id:
                raise ValidationError("Selected GCSE subcategory does not belong to the chosen GCSE topic.")
            if self.gcse_subcategory and self.gcse_subtopic and self.gcse_subcategory.subtopic_id != self.gcse_subtopic_id:
                raise ValidationError("Selected GCSE subcategory does not belong to the chosen GCSE subtopic.")
        elif self.qualification in self.SHARED_ALEVEL_SUBJECT_CODES:
            label = QualificationPath(self.qualification).label
            if self.topic_id or self.subtopic_id or self.subcategory_id:
                raise ValidationError(f"{label} sessions cannot include legacy Biology topic fields.")
            if self.gcse_topic_id or self.gcse_subtopic_id or self.gcse_subcategory_id or self.gcse_subject or self.gcse_tier or self.science_route:
                raise ValidationError(f"{label} sessions cannot include GCSE fields.")
            self._clean_shared_alevel_links(self.SHARED_ALEVEL_SUBJECT_CODES[self.qualification], label)

    SHARED_ALEVEL_SUBJECT_CODES = {
        QualificationPath.ALEVEL_CHEMISTRY: "alevel_chemistry",
        QualificationPath.ALEVEL_PHYSICS: "alevel_physics",
    }

    def _clean_shared_alevel_links(self, subject_code, label):
        from django.core.exceptions import ValidationError
        if not self.alevel_topic_id:
            raise ValidationError(f"{label} sessions require an A-level topic.")
        if self.alevel_topic.subject.code != subject_code:
            raise ValidationError(f"{label} sessions require a {label} curriculum topic.")
        if self.exam_board != self.alevel_topic.exam_board:
            raise ValidationError("Session exam board does not match the selected A-level topic.")
        if self.specification != self.alevel_topic.specification:
            raise ValidationError("Session specification does not match the selected A-level topic.")
        if self.alevel_subcategory_id and not self.alevel_subtopic_id:
            raise ValidationError("An A-level subcategory requires an A-level subtopic.")
        if self.alevel_subtopic_id and self.alevel_subtopic.topic_id != self.alevel_topic_id:
            raise ValidationError("Selected A-level subtopic does not belong to the chosen A-level topic.")
        if self.alevel_subcategory_id and self.alevel_subcategory.subtopic_id != self.alevel_subtopic_id:
            raise ValidationError("Selected A-level subcategory does not belong to the chosen A-level subtopic.")

    def _clean_alevel_curriculum_links(self):
        from django.core.exceptions import ValidationError
        if self.alevel_subtopic_id and not self.alevel_topic_id:
            raise ValidationError("An A-level subtopic requires an A-level topic.")
        if self.alevel_subcategory_id and not self.alevel_subtopic_id:
            raise ValidationError("An A-level subcategory requires an A-level subtopic.")
        if self.alevel_topic_id:
            alevel_topic = self.alevel_topic
            if alevel_topic.subject.code != "alevel_biology":
                raise ValidationError("A-level Biology sessions require an A-level Biology curriculum topic.")
            legacy = self.topic
            if (
                alevel_topic.topic != legacy.topic
                or alevel_topic.exam_board != legacy.exam_board
                or alevel_topic.specification != legacy.specification
            ):
                raise ValidationError("Selected A-level topic does not match the session's Biology topic.")
        if self.alevel_subtopic_id and self.alevel_subtopic.topic_id != self.alevel_topic_id:
            raise ValidationError("Selected A-level subtopic does not belong to the chosen A-level topic.")
        if self.alevel_subcategory_id and self.alevel_subcategory.subtopic_id != self.alevel_subtopic_id:
            raise ValidationError("Selected A-level subcategory does not belong to the chosen A-level subtopic.")
        if self.alevel_subtopic_id:
            legacy_subtopic = self.subtopic
            if legacy_subtopic is None:
                raise ValidationError("An A-level subtopic requires the matching legacy Biology subtopic.")
            if legacy_subtopic.topic_id != self.topic_id or legacy_subtopic.title != self.alevel_subtopic.title:
                raise ValidationError("Selected A-level subtopic does not match the session's Biology subtopic.")
        if self.alevel_subcategory_id:
            legacy_subcategory = self.subcategory
            if legacy_subcategory is None:
                raise ValidationError("An A-level subcategory requires the matching legacy Biology subcategory.")
            if (
                legacy_subcategory.subtopic_id != self.subtopic_id
                or legacy_subcategory.title != self.alevel_subcategory.title
            ):
                raise ValidationError("Selected A-level subcategory does not match the session's Biology subcategory.")


class ServedQuestion(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="served_questions")
    exam_board = models.CharField(max_length=8, choices=ExamBoard.choices, db_index=True)
    scope_key = models.CharField(max_length=255, db_index=True)
    normalized_question = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "exam_board", "scope_key", "normalized_question"],
                name="uniq_served_question_per_user_scope",
            ),
        ]

    def __str__(self):
        return f"{self.user_id} | {self.exam_board} | {self.scope_key}"

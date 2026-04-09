# models.py
from django.db import models
from django.core.validators import MinValueValidator
from accounts.models import CustomUser


class ExamBoard(models.TextChoices):
    OCR = "OCR", "OCR"
    AQA = "AQA", "AQA"


class QualificationPath(models.TextChoices):
    ALEVEL_BIOLOGY = "ALEVEL_BIOLOGY", "A-level Biology"
    GCSE_SCIENCE = "GCSE_SCIENCE", "GCSE Science"


class GCSESubject(models.TextChoices):
    BIOLOGY = "BIOLOGY", "Biology"
    CHEMISTRY = "CHEMISTRY", "Chemistry"
    PHYSICS = "PHYSICS", "Physics"


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

    class Meta:
        ordering = ["exam_board", "topic"]
        constraints = [
            models.UniqueConstraint(
                fields=["topic", "exam_board"],
                name="uniq_topic_per_exam_board",
            )
        ]

    def __str__(self):
        return f"{self.topic} ({self.exam_board})"


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

    class Meta:
        ordering = ["exam_board", "subject", "tier", "topic"]
        constraints = [
            models.UniqueConstraint(
                fields=["topic", "exam_board", "subject", "tier"],
                name="uniq_gcse_topic_per_board_subject",
            )
        ]

    def __str__(self):
        return f"{self.get_subject_display()} | {self.get_tier_display()} | {self.topic} ({self.exam_board})"


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


class QuestionSession(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    topic = models.ForeignKey(BiologyTopic, on_delete=models.CASCADE, null=True, blank=True)
    subtopic = models.ForeignKey(BiologySubTopic, on_delete=models.SET_NULL, null=True, blank=True)
    subcategory = models.ForeignKey(BiologySubCategory, on_delete=models.SET_NULL, null=True, blank=True)
    gcse_topic = models.ForeignKey("GCSEScienceTopic", on_delete=models.SET_NULL, null=True, blank=True)
    gcse_subtopic = models.ForeignKey("GCSEScienceSubTopic", on_delete=models.SET_NULL, null=True, blank=True)
    gcse_subcategory = models.ForeignKey("GCSEScienceSubCategory", on_delete=models.SET_NULL, null=True, blank=True)

    qualification = models.CharField(
        max_length=32,
        choices=QualificationPath.choices,
        default=QualificationPath.ALEVEL_BIOLOGY,
    )
    exam_board = models.CharField(max_length=50)
    gcse_subject = models.CharField(max_length=16, choices=GCSESubject.choices, blank=True)
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
        if self.qualification == QualificationPath.ALEVEL_BIOLOGY:
            if not self.topic_id:
                raise ValidationError("A-level Biology sessions require a topic.")
            if self.gcse_topic_id or self.gcse_subject or self.gcse_tier:
                raise ValidationError("A-level Biology sessions cannot include GCSE fields.")
            if self.subtopic and self.subtopic.topic_id != self.topic_id:
                raise ValidationError("Selected subtopic doesn’t belong to the chosen topic.")
            if self.subcategory and self.subcategory.subtopic.topic_id != self.topic_id:
                raise ValidationError("Selected subcategory doesn’t belong to the chosen topic.")
        elif self.qualification == QualificationPath.GCSE_SCIENCE:
            if not self.gcse_topic_id:
                raise ValidationError("GCSE Science sessions require a GCSE topic.")
            if not self.gcse_subject or not self.gcse_tier:
                raise ValidationError("GCSE Science sessions require subject and tier.")
            if self.topic_id or self.subtopic_id or self.subcategory_id:
                raise ValidationError("GCSE Science sessions cannot include A-level Biology topic fields.")
            if self.gcse_topic and self.gcse_topic.subject != self.gcse_subject:
                raise ValidationError("Selected GCSE topic does not match the GCSE subject.")
            if self.gcse_topic and self.gcse_topic.tier != self.gcse_tier:
                raise ValidationError("Selected GCSE topic does not match the GCSE tier.")
            if self.gcse_subtopic and self.gcse_subtopic.topic_id != self.gcse_topic_id:
                raise ValidationError("Selected GCSE subtopic does not belong to the chosen GCSE topic.")
            if self.gcse_subcategory and self.gcse_subcategory.subtopic.topic_id != self.gcse_topic_id:
                raise ValidationError("Selected GCSE subcategory does not belong to the chosen GCSE topic.")
            if self.gcse_subcategory and self.gcse_subtopic and self.gcse_subcategory.subtopic_id != self.gcse_subtopic_id:
                raise ValidationError("Selected GCSE subcategory does not belong to the chosen GCSE subtopic.")


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

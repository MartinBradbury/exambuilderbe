# models.py
from django.db import models
from django.core.validators import MinValueValidator
from accounts.models import CustomUser


class ExamBoard(models.TextChoices):
    OCR = "OCR", "OCR"
    AQA = "AQA", "AQA"


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
    topic = models.ForeignKey(BiologyTopic, on_delete=models.CASCADE)
    subtopic = models.ForeignKey(BiologySubTopic, on_delete=models.SET_NULL, null=True, blank=True)
    subcategory = models.ForeignKey(BiologySubCategory, on_delete=models.SET_NULL, null=True, blank=True)

    exam_board = models.CharField(max_length=50)
    number_of_questions = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    total_score = models.PositiveIntegerField(default=0)
    total_available = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    feedback = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"Session by {self.user.username} | "
            f"Topic: {self.topic} | "
            f"Board: {self.exam_board} | "
            f"Score: {self.total_score}/{self.total_available} | "
            f"{self.created_at.strftime('%d %b %Y')}"
        )

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.total_score > self.total_available:
            raise ValidationError("Total score cannot exceed total available.")
        if self.subtopic and self.subtopic.topic_id != self.topic_id:
            raise ValidationError("Selected subtopic doesn’t belong to the chosen topic.")
        if self.subcategory and self.subcategory.subtopic.topic_id != self.topic_id:
            raise ValidationError("Selected subcategory doesn’t belong to the chosen topic.")

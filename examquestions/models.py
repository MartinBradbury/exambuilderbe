from django.db import models
from accounts.models import CustomUser


class BiologyTopic(models.Model):
    topic = models.CharField(max_length = 255, blank=True, null=True)

    def __str__(self):
        return self.topic

    
class QuestionSession(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    topic = models.ForeignKey(BiologyTopic, on_delete=models.CASCADE)
    exam_board = models.CharField(max_length=50)
    number_of_questions = models.IntegerField()
    total_score = models.IntegerField(default=0)
    total_available = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    feedback = models.TextField(blank=True)

    def __str__(self):
        return (
            f"Session by {self.user.username} | "
            f"Topic: {self.topic} | "
            f"Board: {self.exam_board} | "
            f"Score: {self.total_score}/{self.total_available} | "
            f"{self.created_at.strftime('%d %b %Y')}"
        )
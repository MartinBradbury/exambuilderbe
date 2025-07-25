from django.contrib import admin
from .models import QuestionSession, BiologyTopic


@admin.register(QuestionSession)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("user", "topic", "exam_board", "number_of_questions", "total_score", "total_available", "created_at")
    search_fields = ("user__username", "topic", "exam_board")
    list_filter = ("exam_board", "created_at")
    ordering = ("-created_at",)


@admin.register(BiologyTopic)
class BiologyTopicAdmin(admin.ModelAdmin):
    list_display = ("topic",)
    search_filter = ("topic",)
    list_filter = ("topic",)
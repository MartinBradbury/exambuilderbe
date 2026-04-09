from django.contrib import admin
from .models import (
    QuestionSession,
    BiologyTopic,
    BiologySubTopic,
    BiologySubCategory,
    GCSEScienceTopic,
    GCSEScienceSubTopic,
    GCSEScienceSubCategory,
)


@admin.register(QuestionSession)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("user", "qualification", "topic", "gcse_topic", "gcse_subtopic", "gcse_subcategory", "exam_board", "number_of_questions", "total_score", "total_available", "created_at")
    search_fields = ("user__username", "topic__topic", "gcse_topic__topic", "gcse_subtopic__title", "gcse_subcategory__title", "exam_board")
    list_filter = ("qualification", "exam_board", "created_at")
    ordering = ("-created_at",)


@admin.register(BiologyTopic)
class BiologyTopicAdmin(admin.ModelAdmin):
    list_display = ("topic",)
    search_filter = ("topic",)
    list_filter = ("topic",)

@admin.register(BiologySubTopic)
class BiologySubTopicAdmin(admin.ModelAdmin):  # added colon
    list_display = ("title", "topic")
    search_fields = ("title",)
    list_filter = ("title",)

@admin.register(BiologySubCategory)
class BiologySubCategoryAdmin(admin.ModelAdmin):  # fixed class name + colon
    list_display = ("title", "subtopic")
    search_fields = ("title",)
    list_filter = ("subtopic",)


@admin.register(GCSEScienceTopic)
class GCSEScienceTopicAdmin(admin.ModelAdmin):
    list_display = ("topic", "subject", "exam_board")
    search_fields = ("topic",)
    list_filter = ("subject", "exam_board")


@admin.register(GCSEScienceSubTopic)
class GCSEScienceSubTopicAdmin(admin.ModelAdmin):
    list_display = ("title", "topic")
    search_fields = ("title",)
    list_filter = ("topic__subject", "topic__exam_board")


@admin.register(GCSEScienceSubCategory)
class GCSEScienceSubCategoryAdmin(admin.ModelAdmin):
    list_display = ("title", "subtopic")
    search_fields = ("title",)
    list_filter = ("subtopic__topic__subject", "subtopic__topic__exam_board")
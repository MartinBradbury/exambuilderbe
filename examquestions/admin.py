from django.contrib import admin
from .models import (
    ALevelSubCategory,
    ALevelSubTopic,
    ALevelTopic,
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
    list_display = ("topic", "subject", "tier", "exam_board")
    search_fields = ("topic",)
    list_filter = ("subject", "tier", "exam_board")


@admin.register(GCSEScienceSubTopic)
class GCSEScienceSubTopicAdmin(admin.ModelAdmin):
    list_display = ("title", "topic")
    search_fields = ("title",)
    list_filter = ("topic__subject", "topic__tier", "topic__exam_board")


@admin.register(GCSEScienceSubCategory)
class GCSEScienceSubCategoryAdmin(admin.ModelAdmin):
    list_display = ("title", "subtopic")
    search_fields = ("title",)
    list_filter = ("subtopic__topic__subject", "subtopic__topic__tier", "subtopic__topic__exam_board")

@admin.register(ALevelTopic)
class ALevelTopicAdmin(admin.ModelAdmin):
    list_display = ("topic", "subject", "exam_board", "specification")
    list_filter = ("subject", "exam_board", "specification")
    search_fields = ("topic", "subject__name", "subject__code")
    list_select_related = ("subject",)
    autocomplete_fields = ("subject",)
    ordering = ("subject__code", "exam_board", "specification", "topic")


@admin.register(ALevelSubTopic)
class ALevelSubTopicAdmin(admin.ModelAdmin):
    list_display = ("title", "topic", "subject_name", "exam_board")
    list_filter = ("topic__subject", "topic__exam_board", "topic__specification")
    search_fields = ("title", "topic__topic", "topic__subject__name", "topic__subject__code")
    list_select_related = ("topic", "topic__subject")
    autocomplete_fields = ("topic",)
    ordering = ("topic__subject__code", "topic__exam_board", "topic__specification", "topic__topic", "title")

    @admin.display(description="Subject", ordering="topic__subject__name")
    def subject_name(self, obj):
        return obj.topic.subject.name

    @admin.display(description="Exam board", ordering="topic__exam_board")
    def exam_board(self, obj):
        return obj.topic.exam_board


@admin.register(ALevelSubCategory)
class ALevelSubCategoryAdmin(admin.ModelAdmin):
    list_display = ("title", "subtopic", "parent_topic", "subject_name", "exam_board")
    list_filter = ("subtopic__topic__subject", "subtopic__topic__exam_board", "subtopic__topic__specification")
    search_fields = (
        "title",
        "subtopic__title",
        "subtopic__topic__topic",
        "subtopic__topic__subject__name",
        "subtopic__topic__subject__code",
    )
    list_select_related = ("subtopic", "subtopic__topic", "subtopic__topic__subject")
    autocomplete_fields = ("subtopic",)
    ordering = (
        "subtopic__topic__subject__code",
        "subtopic__topic__exam_board",
        "subtopic__topic__specification",
        "subtopic__topic__topic",
        "subtopic__title",
        "title",
    )

    @admin.display(description="Topic", ordering="subtopic__topic__topic")
    def parent_topic(self, obj):
        return obj.subtopic.topic.topic

    @admin.display(description="Subject", ordering="subtopic__topic__subject__name")
    def subject_name(self, obj):
        return obj.subtopic.topic.subject.name

    @admin.display(description="Exam board", ordering="subtopic__topic__exam_board")
    def exam_board(self, obj):
        return obj.subtopic.topic.exam_board

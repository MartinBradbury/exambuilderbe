from rest_framework import serializers
from .models import (
    QuestionSession,
    BiologyTopic,
    BiologySubTopic,
    BiologySubCategory,
    GCSEScienceTopic,
    GCSEScienceSubTopic,
    GCSEScienceSubCategory,
    ALevelTopic,
    ALevelSubTopic,
    ALevelSubCategory,
)

class BiologyTopicListSerializer(serializers.ModelSerializer):
    class Meta:
        model = BiologyTopic
        fields = ["id", "topic", "exam_board", "specification"]


class BiologySubTopicListSerializer(serializers.ModelSerializer):
    class Meta:
        model = BiologySubTopic
        fields = ["id", "title", "topic"]  # topic is a FK id by default


class BiologySubCategoryListSerializer(serializers.ModelSerializer):
    class Meta:
        model = BiologySubCategory
        fields = ["id", "title", "subtopic"]


class ALevelTopicListSerializer(serializers.ModelSerializer):
    subject = serializers.CharField(source="subject.code", read_only=True)

    class Meta:
        model = ALevelTopic
        fields = ["id", "topic", "subject", "exam_board", "specification"]


class ALevelSubTopicListSerializer(serializers.ModelSerializer):
    class Meta:
        model = ALevelSubTopic
        fields = ["id", "title", "topic"]


class ALevelSubCategoryListSerializer(serializers.ModelSerializer):
    class Meta:
        model = ALevelSubCategory
        fields = ["id", "title", "subtopic"]


class GCSETopicListSerializer(serializers.ModelSerializer):
    class Meta:
        model = GCSEScienceTopic
        fields = ["id", "topic", "subject", "tier", "exam_board", "specification"]


class GCSESubTopicListSerializer(serializers.ModelSerializer):
    class Meta:
        model = GCSEScienceSubTopic
        fields = ["id", "title", "topic"]


class GCSESubCategoryListSerializer(serializers.ModelSerializer):
    class Meta:
        model = GCSEScienceSubCategory
        fields = ["id", "title", "subtopic"]


class QuestionSessionSerializer(serializers.ModelSerializer):
    level = serializers.SerializerMethodField()
    subject = serializers.SerializerMethodField()
    science_route = serializers.SerializerMethodField()
    topic = serializers.SerializerMethodField()
    subtopic = serializers.SerializerMethodField()
    subcategory = serializers.SerializerMethodField()
    gcse_topic = serializers.StringRelatedField(read_only=True)
    gcse_subtopic = serializers.StringRelatedField(read_only=True)
    gcse_subcategory = serializers.StringRelatedField(read_only=True)
    topic_name = serializers.SerializerMethodField()
    subtopic_name = serializers.SerializerMethodField()
    subcategory_name = serializers.SerializerMethodField()

    SUBJECT_BY_QUALIFICATION = {
        "GCSE_SCIENCE": "gcse_science",
        "ALEVEL_BIOLOGY": "alevel_biology",
        "ALEVEL_CHEMISTRY": "alevel_chemistry",
        "ALEVEL_PHYSICS": "alevel_physics",
    }

    def get_level(self, obj):
        if obj.qualification == "GCSE_SCIENCE":
            return "GCSE"
        return "A level"

    def get_subject(self, obj):
        return self.SUBJECT_BY_QUALIFICATION.get(obj.qualification)

    # Legacy Biology and GCSE links win so historical output is unchanged; the shared
    # A-level links only supply values for sessions that have no legacy equivalent.
    def get_topic(self, obj):
        if obj.topic:
            return str(obj.topic)
        if obj.gcse_topic:
            return str(obj.gcse_topic)
        if obj.alevel_topic:
            return str(obj.alevel_topic)
        return None

    def get_subtopic(self, obj):
        if obj.subtopic:
            return str(obj.subtopic)
        if obj.alevel_subtopic:
            return str(obj.alevel_subtopic)
        return None

    def get_subcategory(self, obj):
        if obj.subcategory:
            return str(obj.subcategory)
        if obj.alevel_subcategory:
            return str(obj.alevel_subcategory)
        return None

    def get_science_route(self, obj):
        return obj.science_route or None

    def get_topic_name(self, obj):
        if obj.topic:
            return obj.topic.topic
        if obj.gcse_topic:
            return obj.gcse_topic.topic
        if obj.alevel_topic:
            return obj.alevel_topic.topic
        return None

    def get_subtopic_name(self, obj):
        if obj.subtopic:
            return obj.subtopic.title
        if obj.gcse_subtopic:
            return obj.gcse_subtopic.title
        if obj.alevel_subtopic:
            return obj.alevel_subtopic.title
        return None

    def get_subcategory_name(self, obj):
        if obj.subcategory:
            return obj.subcategory.title
        if obj.gcse_subcategory:
            return obj.gcse_subcategory.title
        if obj.alevel_subcategory:
            return obj.alevel_subcategory.title
        return None

    class Meta:
        model = QuestionSession
        fields = [
            "id",
            "level",
            "subject",
            "science_route",
            "qualification",
            "topic",
            "subtopic",
            "subcategory",
            "gcse_topic",
            "gcse_subtopic",
            "gcse_subcategory",
            "topic_name",
            "subtopic_name",
            "subcategory_name",
            "exam_board",
            "specification",
            "gcse_subject",
            "gcse_tier",
            "number_of_questions",
            "total_score",
            "total_available",
            "created_at",
            "feedback",
        ]
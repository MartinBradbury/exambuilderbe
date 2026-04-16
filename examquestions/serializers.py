from rest_framework import serializers
from .models import (
    QuestionSession,
    BiologyTopic,
    BiologySubTopic,
    BiologySubCategory,
    GCSEScienceTopic,
    GCSEScienceSubTopic,
    GCSEScienceSubCategory,
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
    science_route = serializers.SerializerMethodField()
    topic = serializers.SerializerMethodField()
    subtopic = serializers.StringRelatedField(read_only=True)
    subcategory = serializers.StringRelatedField(read_only=True)
    gcse_topic = serializers.StringRelatedField(read_only=True)
    gcse_subtopic = serializers.StringRelatedField(read_only=True)
    gcse_subcategory = serializers.StringRelatedField(read_only=True)
    topic_name = serializers.SerializerMethodField()
    subtopic_name = serializers.SerializerMethodField()
    subcategory_name = serializers.SerializerMethodField()

    def get_level(self, obj):
        if obj.qualification == "GCSE_SCIENCE":
            return "GCSE"
        return "A level"

    def get_topic(self, obj):
        if obj.topic:
            return str(obj.topic)
        if obj.gcse_topic:
            return str(obj.gcse_topic)
        return None

    def get_science_route(self, obj):
        return obj.science_route or None

    def get_topic_name(self, obj):
        if obj.topic:
            return obj.topic.topic
        if obj.gcse_topic:
            return obj.gcse_topic.topic
        return None

    def get_subtopic_name(self, obj):
        if obj.subtopic:
            return obj.subtopic.title
        if obj.gcse_subtopic:
            return obj.gcse_subtopic.title
        return None

    def get_subcategory_name(self, obj):
        if obj.subcategory:
            return obj.subcategory.title
        if obj.gcse_subcategory:
            return obj.gcse_subcategory.title
        return None

    class Meta:
        model = QuestionSession
        fields = [
            "id",
            "level",
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
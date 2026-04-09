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
        fields = ["id", "topic"]


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
        fields = ["id", "topic", "subject", "exam_board"]


class GCSESubTopicListSerializer(serializers.ModelSerializer):
    class Meta:
        model = GCSEScienceSubTopic
        fields = ["id", "title", "topic"]


class GCSESubCategoryListSerializer(serializers.ModelSerializer):
    class Meta:
        model = GCSEScienceSubCategory
        fields = ["id", "title", "subtopic"]


class QuestionSessionSerializer(serializers.ModelSerializer):
    topic = serializers.SerializerMethodField()
    subtopic = serializers.StringRelatedField(read_only=True)
    subcategory = serializers.StringRelatedField(read_only=True)
    gcse_topic = serializers.StringRelatedField(read_only=True)
    gcse_subtopic = serializers.StringRelatedField(read_only=True)
    gcse_subcategory = serializers.StringRelatedField(read_only=True)

    def get_topic(self, obj):
        if obj.topic:
            return str(obj.topic)
        if obj.gcse_topic:
            return str(obj.gcse_topic)
        return None

    class Meta:
        model = QuestionSession
        fields = [
            "id",
            "qualification",
            "topic",
            "subtopic",
            "subcategory",
            "gcse_topic",
            "gcse_subtopic",
            "gcse_subcategory",
            "exam_board",
            "gcse_subject",
            "gcse_tier",
            "number_of_questions",
            "total_score",
            "total_available",
            "created_at",
            "feedback",
        ]
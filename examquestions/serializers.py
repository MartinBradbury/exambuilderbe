from rest_framework import serializers
from .models import QuestionSession, BiologyTopic, BiologySubTopic, BiologySubCategory

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


class QuestionSessionSerializer(serializers.ModelSerializer):
    topic = serializers.StringRelatedField()
    # Optional: expose these if you added FKs on QuestionSession
    subtopic = serializers.StringRelatedField(read_only=True)
    subcategory = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = QuestionSession
        fields = [
            "id",
            "topic",
            "subtopic",        # optional if present on model
            "subcategory",     # optional if present on model
            "exam_board",
            "number_of_questions",
            "total_score",
            "total_available",
            "created_at",
            "feedback",
        ]
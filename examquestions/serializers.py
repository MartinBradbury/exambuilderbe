from rest_framework import serializers
from .models import QuestionSession



class QuestionSessionSerializer(serializers.ModelSerializer):
    topic = serializers.StringRelatedField() 
    class Meta:
        model = QuestionSession
        fields = ['id', 'topic', 'exam_board', 'number_of_questions', 'total_score', 'total_available', 'created_at', 'feedback']

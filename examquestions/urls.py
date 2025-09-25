from django.urls import path
from .views import generate_exam_questions, mark_user_answer, submit_question_session, get_user_sessions, get_biology_topics, get_biology_subtopics, get_biology_subcategories

urlpatterns = [
    path("generate-questions/", generate_exam_questions, name="generate-exam-questions"),
    path("mark-answer/", mark_user_answer, name="mark-user-answer"),
    path('submit-question-session/', submit_question_session, name='submit_question_session'),
    path('user-sessions/', get_user_sessions, name='get_user_sessions'),
    path('biology-topics/', get_biology_topics, name='biology-topics'),
    path("biology-subtopics/", get_biology_subtopics),           # add
    path("biology-subcategories/", get_biology_subcategories),

    
]
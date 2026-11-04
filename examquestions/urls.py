from django.urls import path
from .views import generate_exam_questions, get_biology_curriculum_mode_view, mark_user_answer, submit_question_session, get_user_sessions, delete_user_results, get_biology_topics, get_biology_subtopics, get_biology_subcategories, get_gcse_topics, get_gcse_subtopics, get_gcse_subcategories, get_alevel_topics, get_alevel_subtopics, get_alevel_subcategories

urlpatterns = [
    path("biology-curriculum-mode/", get_biology_curriculum_mode_view, name="biology-curriculum-mode"),
    path("generate-questions/", generate_exam_questions, name="generate-exam-questions"),
    path("mark-answer/", mark_user_answer, name="mark-user-answer"),
    path('submit-question-session/', submit_question_session, name='submit_question_session'),
    path('user-sessions/', get_user_sessions, name='get_user_sessions'),
    path('user-sessions/delete-all/', delete_user_results, name='delete_user_results'),
    path('biology-topics/', get_biology_topics, name='biology-topics'),
    path("biology-subtopics/", get_biology_subtopics),           # add
    path("biology-subcategories/", get_biology_subcategories),
    path("alevel-topics/", get_alevel_topics, name="alevel-topics"),
    path("alevel-subtopics/", get_alevel_subtopics, name="alevel-subtopics"),
    path("alevel-subcategories/", get_alevel_subcategories, name="alevel-subcategories"),
    path("gcse-topics/", get_gcse_topics, name="gcse-topics"),
    path("gcse-subtopics/", get_gcse_subtopics, name="gcse-subtopics"),
    path("gcse-subcategories/", get_gcse_subcategories, name="gcse-subcategories"),

    
]
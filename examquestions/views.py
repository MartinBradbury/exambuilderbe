from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .services.ai import generate_questions, evaluate_response_with_openai, get_feedback_from_openai
from .models import QuestionSession, BiologyTopic, BiologySubCategory, BiologySubTopic
import json
from .serializers import (
    QuestionSessionSerializer,
    BiologySubCategoryListSerializer,
    BiologySubTopicListSerializer,
    BiologyTopicListSerializer,
)
import logging

logger = logging.getLogger(__name__)

from pathlib import Path
import random


# ------------------------------------------------------------
# Exam board → local fallback question banks (same JSON schema)
# ------------------------------------------------------------
FALLBACK_QUESTION_PATHS = {
    "OCR": Path(__file__).resolve().parent.parent / "examquestions/ocr_questions.json",
    "AQA": Path(__file__).resolve().parent.parent / "examquestions/aqa_questions.json",
}
ALLOWED_BOARDS = {"OCR", "AQA"}


def load_fallback_bank_for_board(exam_board: str) -> dict:
    board_key = (exam_board or "").strip().upper()
    path = FALLBACK_QUESTION_PATHS.get(board_key, FALLBACK_QUESTION_PATHS["OCR"])

    logger.info("Exam board: %s | Using fallback file: %s | Exists: %s",
                board_key, path, path.exists())

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("Fallback file NOT FOUND for %s at %s", board_key, path)
        return {}
    # Let JSONDecodeError bubble up so the caller's except block handles it.


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def generate_exam_questions(request):
    topic_id = request.data.get("topic_id")
    subtopic_id = request.data.get("subtopic_id")         # optional
    subcategory_id = request.data.get("subcategory_id")   # optional
    exam_board = request.data.get("exam_board")
    try:
        number = int(request.data.get("number_of_questions"))
    except (TypeError, ValueError):
        number = 0

    if not all([topic_id, exam_board, number]):
        return Response({"error": "Missing required fields"}, status=400)

    board_key = (exam_board or "").strip().upper()
    if board_key not in ALLOWED_BOARDS:
        return Response({"error": "Invalid exam_board. Use 'OCR' or 'AQA'."}, status=400)

    try:
        # 1) Fetch the topic for THIS board (critical change)
        topic = BiologyTopic.objects.get(id=topic_id, exam_board=board_key)

        # 2) Validate optional relationships strictly under this topic
        subtopic = None
        if subtopic_id:
            subtopic = BiologySubTopic.objects.get(id=subtopic_id, topic_id=topic.id)

        subcategory = None
        if subcategory_id:
            if not subtopic_id:
                return Response({"error": "subcategory_id provided without subtopic_id"}, status=400)
            subcategory = BiologySubCategory.objects.get(id=subcategory_id, subtopic_id=subtopic.id)

        # Load fallback bank for the selected exam board
        all_fallback_questions = load_fallback_bank_for_board(board_key)

        # Prefer most specific key present in your JSON: subcategory -> subtopic -> topic
        fallback_pool = []
        if subcategory and subcategory.title in all_fallback_questions:
            fallback_pool = all_fallback_questions[subcategory.title]
        elif subtopic and subtopic.title in all_fallback_questions:
            fallback_pool = all_fallback_questions[subtopic.title]
        elif topic.topic in all_fallback_questions:
            fallback_pool = all_fallback_questions[topic.topic]

        fallback_count = number // 2
        ai_count = number - fallback_count

        fallback_selected = []
        if isinstance(fallback_pool, list) and fallback_pool:
            fallback_selected = random.sample(fallback_pool, min(fallback_count, len(fallback_pool)))
        else:
            # no fallback available -> all AI
            ai_count = number
            fallback_selected = []

        scope = topic.topic
        if subtopic:
            scope += f' (SubTopic: {subtopic.title})'
        if subcategory:
            scope += f' (SubCategory: {subcategory.title})'

        ai_response = generate_questions(scope, board_key, ai_count)
        ai_questions = ai_response.get("questions", [])

        combined_questions = ai_questions + fallback_selected
        random.shuffle(combined_questions)

        total_available = sum(q.get("total_marks", q.get("mark", 0)) for q in combined_questions)

        session = QuestionSession.objects.create(
            user=request.user,
            topic=topic,
            subtopic=subtopic,
            subcategory=subcategory,
            exam_board=board_key,
            number_of_questions=number,
            total_available=total_available
        )

        return Response({
            "questions": combined_questions,
            "session_id": session.id
        }, status=200)

    except BiologyTopic.DoesNotExist:
        return Response({"error": "Invalid topic selected for this exam board"}, status=400)
    except BiologySubTopic.DoesNotExist:
        return Response({"error": "Invalid subtopic for the selected topic"}, status=400)
    except BiologySubCategory.DoesNotExist:
        return Response({"error": "Invalid subcategory for the selected subtopic"}, status=400)
    except json.JSONDecodeError:
        return Response({"error": "Invalid JSON format in fallback question bank"}, status=500)
    except Exception as e:
        logger.exception("generate_exam_questions failed")
        return Response({"error": str(e)}, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_user_answer(request):
    question = request.data.get("question")
    mark_scheme = request.data.get("mark_scheme")
    user_answer = request.data.get("user_answer")
    exam_board = request.data.get("exam_board", "AQA")

    logger.info("Incoming marking request:")
    logger.info("Question: %s", question)
    logger.info("User Answer: %s", user_answer)
    logger.info("Mark Scheme: %s", mark_scheme)
    logger.info("Exam Board: %s", exam_board)

    if not all([question, mark_scheme, user_answer]):
        return Response({"error": "Missing one or more fields."}, status=400)

    try:
        result = evaluate_response_with_openai(question, mark_scheme, user_answer, exam_board)
        return Response(result, status=200)
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON from OpenAI: %s", e)
        return Response({"error": "Invalid JSON returned by OpenAI"}, status=500)
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        return Response({"error": str(e)}, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def submit_question_session(request):
    session_id = request.data.get("session_id")
    answers = request.data.get("answers")
    feedback_text = request.data.get("feedback")

    if not session_id or not answers:
        return Response({"error": "Missing session_id or answers"}, status=400)

    try:
        session = QuestionSession.objects.get(id=session_id, user=request.user)
        total_score = sum([a.get("score", 0) for a in answers])

        prompt = "Give strengths and weaknesses based on these answers:\n"
        for a in answers:
            prompt += f"\nQuestion: {a['question']}\nAnswer: {a['user_answer']}\nScore: {a['score']}/{session.total_available}\n"

        feedback = get_feedback_from_openai(prompt)
        session.total_score = total_score
        session.feedback = json.dumps(feedback)
        session.save()

        return Response({
            "message": "Session submitted",
            "score": total_score,
            "out_of": session.total_available,
            "feedback": feedback
        })

    except QuestionSession.DoesNotExist:
        return Response({"error": "Session not found"}, status=404)
    except Exception as e:
        return Response({"error": str(e)}, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_user_sessions(request):
    sessions = QuestionSession.objects.filter(user=request.user).order_by('-created_at')
    serializer = QuestionSessionSerializer(sessions, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_biology_topics(request):
    board = (request.query_params.get("exam_board") or "").strip().upper()
    qs = BiologyTopic.objects.all().order_by("topic")
    if board:
        if board not in ALLOWED_BOARDS:
            return Response({"error": "Invalid exam_board. Use 'OCR' or 'AQA'."}, status=400)
        qs = qs.filter(exam_board=board)
    return Response(BiologyTopicListSerializer(qs, many=True).data)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_biology_subtopics(request):
    board = (request.query_params.get("exam_board") or "").strip().upper()
    qs = BiologySubTopic.objects.select_related("topic").all().order_by("title")
    topic_id = request.query_params.get("topic_id")
    if topic_id:
        qs = qs.filter(topic_id=topic_id)
    if board:
        if board not in ALLOWED_BOARDS:
            return Response({"error": "Invalid exam_board. Use 'OCR' or 'AQA'."}, status=400)
        qs = qs.filter(topic__exam_board=board)
    return Response(BiologySubTopicListSerializer(qs, many=True).data)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_biology_subcategories(request):
    board = (request.query_params.get("exam_board") or "").strip().upper()
    qs = BiologySubCategory.objects.select_related("subtopic", "subtopic__topic").all().order_by("title")
    subtopic_id = request.query_params.get("subtopic_id")
    if subtopic_id:
        qs = qs.filter(subtopic_id=subtopic_id)
    if board:
        if board not in ALLOWED_BOARDS:
            return Response({"error": "Invalid exam_board. Use 'OCR' or 'AQA'."}, status=400)
        qs = qs.filter(subtopic__topic__exam_board=board)
    return Response(BiologySubCategoryListSerializer(qs, many=True).data)

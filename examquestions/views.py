from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .services.ai import generate_questions, evaluate_batch_responses_with_openai, evaluate_response_with_openai
from .services.aiGCSE import (
    generate_questions as generate_gcse_questions,
    evaluate_batch_responses_with_openai as evaluate_gcse_batch_responses_with_openai,
    evaluate_response_with_openai as evaluate_gcse_response_with_openai,
)
from .models import (
    QuestionSession,
    BiologyTopic,
    BiologySubCategory,
    BiologySubTopic,
    GCSEScienceTopic,
    GCSEScienceSubTopic,
    GCSEScienceSubCategory,
    ServedQuestion,
    QualificationPath,
    GCSESubject,
    GCSETier,
)
from accounts.models import QuestionUsage, UserEntitlement
from django.db import transaction
from django.utils import timezone
from functools import lru_cache
import json
from .serializers import (
    QuestionSessionSerializer,
    BiologySubCategoryListSerializer,
    BiologySubTopicListSerializer,
    BiologyTopicListSerializer,
    GCSETopicListSerializer,
    GCSESubTopicListSerializer,
    GCSESubCategoryListSerializer,
)
import logging

logger = logging.getLogger(__name__)

from pathlib import Path
import random
import re


# ------------------------------------------------------------
# Exam board → local fallback question banks (same JSON schema)
# ------------------------------------------------------------
FALLBACK_QUESTION_PATHS = {
    "OCR": Path(__file__).resolve().parent.parent / "examquestions/ocr_questions.json",
    "AQA": Path(__file__).resolve().parent.parent / "examquestions/aqa_questions.json",
}
ALLOWED_BOARDS = {"OCR", "AQA"}
ALLOWED_QUALIFICATIONS = {choice for choice, _ in QualificationPath.choices}
ALLOWED_GCSE_SUBJECTS = {choice for choice, _ in GCSESubject.choices}
ALLOWED_GCSE_TIERS = {choice for choice, _ in GCSETier.choices}


class DailyQuestionLimitExceeded(Exception):
    pass


def _normalize_choice(raw_value):
    return str(raw_value or "").strip().replace("-", "_").replace(" ", "_").upper()


def _normalize_qualification(raw_value):
    normalized = _normalize_choice(raw_value)
    if not normalized:
        return QualificationPath.ALEVEL_BIOLOGY
    return normalized


def _normalize_gcse_subject(raw_value):
    return _normalize_choice(raw_value)


def _normalize_gcse_tier(raw_value):
    return _normalize_choice(raw_value)


def _coerce_numeric_score(value):
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return 0
    if numeric_value.is_integer():
        return int(numeric_value)
    return numeric_value


def _answer_out_of(answer):
    explicit_out_of = answer.get("out_of")
    if explicit_out_of is not None:
        return _coerce_numeric_score(explicit_out_of)

    explicit_total_marks = answer.get("total_marks")
    if explicit_total_marks is not None:
        return _coerce_numeric_score(explicit_total_marks)

    mark_scheme = answer.get("mark_scheme") or []
    return len(mark_scheme)


def _question_label(answer):
    question_text = str(answer.get("question", "")).strip()
    if not question_text:
        return "this question"
    shortened = question_text.replace("\n", " ")
    if len(shortened) > 72:
        shortened = shortened[:69].rstrip() + "..."
    return shortened


def _unique_preserve_order(items):
    seen = set()
    unique_items = []
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_items.append(normalized)
    return unique_items


def _ensure_three_items(items, fallbacks):
    combined = _unique_preserve_order(items + fallbacks)
    return combined[:3]


def build_session_feedback_from_answers(answers):
    strengths = []
    improvements = []

    for answer in answers:
        score = _coerce_numeric_score(answer.get("score", 0))
        out_of = _answer_out_of(answer)
        label = _question_label(answer)
        feedback_text = str(answer.get("feedback", "")).strip()

        if score > 0:
            strengths.append(f"You picked up marks on {label} ({score}/{out_of}).")
        if score < out_of:
            if feedback_text:
                improvements.append(f"Review {label}: {feedback_text}")
            else:
                improvements.append(f"Review {label} to recover missed marks ({score}/{out_of}).")

    if not strengths:
        strengths.append("You attempted the full set of questions, which gives a clear baseline for revision.")

    if not improvements:
        improvements.append("Keep answers precise and aligned to the mark scheme to maintain full marks.")

    strengths = _ensure_three_items(
        strengths,
        [
            "You are building a consistent picture of which topics are strongest.",
            "There is enough detail in these answers to guide targeted revision.",
            "Your completed session gives a useful benchmark for future practice.",
        ],
    )
    improvements = _ensure_three_items(
        improvements,
        [
            "Use the exact biological terms expected by the mark scheme where possible.",
            "Aim to include every marking point rather than one or two partial ideas.",
            "Check each answer against the command word and mark allocation before submitting.",
        ],
    )

    return {
        "strengths": strengths,
        "improvements": improvements,
    }


def normalize_feedback_payload(feedback_value, answers):
    if isinstance(feedback_value, dict):
        strengths = feedback_value.get("strengths") or []
        improvements = feedback_value.get("improvements") or []
        if isinstance(strengths, list) and isinstance(improvements, list):
            return {
                "strengths": strengths,
                "improvements": improvements,
            }

    if isinstance(feedback_value, str) and feedback_value.strip():
        try:
            parsed_feedback = json.loads(feedback_value)
        except json.JSONDecodeError:
            parsed_feedback = None
        if isinstance(parsed_feedback, dict):
            strengths = parsed_feedback.get("strengths") or []
            improvements = parsed_feedback.get("improvements") or []
            if isinstance(strengths, list) and isinstance(improvements, list):
                return {
                    "strengths": strengths,
                    "improvements": improvements,
                }

    return build_session_feedback_from_answers(answers)


def normalize_question_text(question_text):
    normalized = str(question_text or "").strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\s*\[\d+\s+marks?\]\s*$", "", normalized)
    return normalized.strip()


def question_text_from_item(question_item):
    return str(question_item.get("question", "")).strip()


def build_gcse_scope_metadata(topic, subtopic=None, subcategory=None, tier=None):
    if subcategory:
        return subcategory.title, f"gcse-subcategory:{subcategory.id}:{tier}"
    if subtopic:
        return subtopic.title, f"gcse-subtopic:{subtopic.id}:{tier}"
    return topic.topic, f"gcse-topic:{topic.id}:{tier}"


@lru_cache(maxsize=None)
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


def get_or_create_entitlement(user):
    entitlement, _ = UserEntitlement.objects.get_or_create(user=user)
    return entitlement


def build_scope_metadata(topic, subtopic=None, subcategory=None):
    if subcategory:
        return subcategory.title, f"subcategory:{subcategory.id}"
    if subtopic:
        return subtopic.title, f"subtopic:{subtopic.id}"
    return topic.topic, f"topic:{topic.id}"


def get_user_served_question_set(user, exam_board, scope_key):
    return set(
        ServedQuestion.objects.filter(user=user, exam_board=exam_board, scope_key=scope_key)
        .values_list("normalized_question", flat=True)
    )


def reset_user_served_questions(user, exam_board, scope_key):
    ServedQuestion.objects.filter(user=user, exam_board=exam_board, scope_key=scope_key).delete()


def select_fallback_questions(fallback_pool, count, excluded_questions):
    if count <= 0:
        return []

    unique_candidates = []
    seen_in_pool = set()
    for candidate in fallback_pool:
        question_text = question_text_from_item(candidate)
        normalized = normalize_question_text(question_text)
        if not normalized or normalized in excluded_questions or normalized in seen_in_pool:
            continue
        seen_in_pool.add(normalized)
        unique_candidates.append(candidate)

    return random.sample(unique_candidates, min(count, len(unique_candidates)))


def replace_duplicate_questions_from_fallback(
    user,
    exam_board,
    scope_key,
    fallback_pool,
    accepted_questions,
    requested_count,
    served_questions,
):
    current_questions = list(accepted_questions)
    excluded_questions = {normalize_question_text(question_text_from_item(item)) for item in current_questions}
    excluded_questions.update(served_questions)
    missing_count = max(requested_count - len(current_questions), 0)
    replacements = select_fallback_questions(fallback_pool, missing_count, excluded_questions)

    if len(replacements) < missing_count:
        reset_user_served_questions(user, exam_board, scope_key)
        served_questions.clear()
        excluded_questions = {normalize_question_text(question_text_from_item(item)) for item in current_questions}
        replacements = select_fallback_questions(fallback_pool, missing_count, excluded_questions)

    if len(replacements) < missing_count:
        raise ValueError(
            "Not enough stored fallback questions are available to replace duplicate questions for this selection."
        )

    current_questions.extend(replacements)
    return current_questions, served_questions


def record_served_questions(user, exam_board, scope_key, questions):
    records = []
    for question_item in questions:
        normalized = normalize_question_text(question_text_from_item(question_item))
        if not normalized:
            continue
        records.append(
            ServedQuestion(
                user=user,
                exam_board=exam_board,
                scope_key=scope_key,
                normalized_question=normalized,
            )
        )

    if records:
        ServedQuestion.objects.bulk_create(records, ignore_conflicts=True)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def generate_exam_questions(request):
    topic_id = request.data.get("topic_id")
    subtopic_id = request.data.get("subtopic_id")         # optional
    subcategory_id = request.data.get("subcategory_id")   # optional
    exam_board = request.data.get("exam_board")
    qualification = _normalize_qualification(request.data.get("qualification"))
    try:
        number = int(request.data.get("number_of_questions"))
    except (TypeError, ValueError):
        number = 0

    if not all([topic_id, exam_board, number]):
        return Response({"error": "Missing required fields"}, status=400)

    board_key = (exam_board or "").strip().upper()
    if board_key not in ALLOWED_BOARDS:
        return Response({"error": "Invalid exam_board. Use 'OCR' or 'AQA'."}, status=400)
    if qualification not in ALLOWED_QUALIFICATIONS:
        return Response({"error": "Invalid qualification. Use 'ALEVEL_BIOLOGY' or 'GCSE_SCIENCE'."}, status=400)

    entitlement = get_or_create_entitlement(request.user)
    today = timezone.localdate()
    questions_remaining_today = None

    if not entitlement.has_unlimited_access:
        current_usage = (
            QuestionUsage.objects.filter(user=request.user, date=today)
            .values_list("question_count", flat=True)
            .first()
            or 0
        )
        questions_remaining_today = max(
            UserEntitlement.FREE_DAILY_QUESTION_LIMIT - current_usage,
            0,
        )
        if number > questions_remaining_today:
            return Response(
                {
                    "error": "Free users can only generate 1 question per day. Upgrade for unlimited access.",
                    "plan_type": entitlement.plan_type,
                    "questions_remaining_today": questions_remaining_today,
                },
                status=403,
            )

    try:
        if qualification == QualificationPath.ALEVEL_BIOLOGY:
            topic = BiologyTopic.objects.get(id=topic_id, exam_board=board_key)

            subtopic = None
            if subtopic_id:
                subtopic = BiologySubTopic.objects.get(id=subtopic_id, topic_id=topic.id)

            subcategory = None
            if subcategory_id:
                if not subtopic_id:
                    return Response({"error": "subcategory_id provided without subtopic_id"}, status=400)
                subcategory = BiologySubCategory.objects.get(id=subcategory_id, subtopic_id=subtopic.id)

            all_fallback_questions = load_fallback_bank_for_board(board_key)
            scope_title, scope_key = build_scope_metadata(topic, subtopic, subcategory)
            served_questions = get_user_served_question_set(request.user, board_key, scope_key)

            fallback_pool = []
            if scope_title in all_fallback_questions:
                fallback_pool = all_fallback_questions[scope_title]
            elif topic.topic in all_fallback_questions:
                fallback_pool = all_fallback_questions[topic.topic]

            fallback_count = number // 2
            ai_count = number - fallback_count

            fallback_selected = []
            if isinstance(fallback_pool, list) and fallback_pool:
                fallback_selected = select_fallback_questions(fallback_pool, fallback_count, served_questions)
            else:
                ai_count = number

            scope = topic.topic
            if subtopic:
                scope += f' (SubTopic: {subtopic.title})'
            if subcategory:
                scope += f' (SubCategory: {subcategory.title})'

            ai_response = generate_questions(scope, board_key, ai_count)
            ai_questions = ai_response.get("questions", [])

            combined_questions = list(fallback_selected)
            current_batch_questions = {
                normalize_question_text(question_text_from_item(question_item))
                for question_item in combined_questions
            }

            for ai_question in ai_questions:
                normalized = normalize_question_text(question_text_from_item(ai_question))
                if not normalized or normalized in served_questions or normalized in current_batch_questions:
                    continue
                current_batch_questions.add(normalized)
                combined_questions.append(ai_question)

            if len(combined_questions) < number:
                combined_questions, served_questions = replace_duplicate_questions_from_fallback(
                    user=request.user,
                    exam_board=board_key,
                    scope_key=scope_key,
                    fallback_pool=fallback_pool if isinstance(fallback_pool, list) else [],
                    accepted_questions=combined_questions,
                    requested_count=number,
                    served_questions=served_questions,
                )

            random.shuffle(combined_questions)

            total_available = sum(q.get("total_marks", q.get("mark", 0)) for q in combined_questions)
            session_kwargs = {
                "topic": topic,
                "subtopic": subtopic,
                "subcategory": subcategory,
                "qualification": qualification,
                "exam_board": board_key,
                "number_of_questions": number,
                "total_available": total_available,
            }
        else:
            gcse_subject = _normalize_gcse_subject(request.data.get("subject"))
            gcse_tier = _normalize_gcse_tier(request.data.get("tier"))
            if gcse_subject not in ALLOWED_GCSE_SUBJECTS:
                return Response({"error": "Invalid GCSE subject. Use 'BIOLOGY', 'CHEMISTRY', or 'PHYSICS'."}, status=400)
            if gcse_tier not in ALLOWED_GCSE_TIERS:
                return Response({"error": "Invalid GCSE tier. Use 'FOUNDATION' or 'HIGHER'."}, status=400)

            gcse_topic = GCSEScienceTopic.objects.get(id=topic_id, exam_board=board_key, subject=gcse_subject)
            gcse_subtopic = None
            if subtopic_id:
                gcse_subtopic = GCSEScienceSubTopic.objects.get(id=subtopic_id, topic_id=gcse_topic.id)

            gcse_subcategory = None
            if subcategory_id:
                if not subtopic_id:
                    return Response({"error": "subcategory_id provided without subtopic_id"}, status=400)
                gcse_subcategory = GCSEScienceSubCategory.objects.get(id=subcategory_id, subtopic_id=gcse_subtopic.id)

            scope_title, scope_key = build_gcse_scope_metadata(gcse_topic, gcse_subtopic, gcse_subcategory, gcse_tier)
            served_questions = get_user_served_question_set(request.user, board_key, scope_key)

            scope = gcse_topic.topic
            if gcse_subtopic:
                scope += f' (SubTopic: {gcse_subtopic.title})'
            if gcse_subcategory:
                scope += f' (SubCategory: {gcse_subcategory.title})'

            ai_response = generate_gcse_questions(scope, board_key, number, gcse_subject, gcse_tier)
            ai_questions = ai_response.get("questions", [])

            combined_questions = []
            current_batch_questions = set()
            for ai_question in ai_questions:
                normalized = normalize_question_text(question_text_from_item(ai_question))
                if not normalized or normalized in served_questions or normalized in current_batch_questions:
                    continue
                current_batch_questions.add(normalized)
                combined_questions.append(ai_question)

            if len(combined_questions) < number:
                raise ValueError("The AI did not return enough unique GCSE questions for this topic.")

            total_available = sum(q.get("total_marks", q.get("mark", 0)) for q in combined_questions)
            session_kwargs = {
                "qualification": qualification,
                "gcse_topic": gcse_topic,
                "gcse_subtopic": gcse_subtopic,
                "gcse_subcategory": gcse_subcategory,
                "gcse_subject": gcse_subject,
                "gcse_tier": gcse_tier,
                "exam_board": board_key,
                "number_of_questions": number,
                "total_available": total_available,
            }

        with transaction.atomic():
            if not entitlement.has_unlimited_access:
                usage, _ = QuestionUsage.objects.select_for_update().get_or_create(
                    user=request.user,
                    date=today,
                    defaults={"question_count": 0},
                )
                questions_remaining_today = max(
                    UserEntitlement.FREE_DAILY_QUESTION_LIMIT - usage.question_count,
                    0,
                )
                if number > questions_remaining_today:
                    raise DailyQuestionLimitExceeded()

            session = QuestionSession.objects.create(
                user=request.user,
                **session_kwargs,
            )
            record_served_questions(request.user, board_key, scope_key, combined_questions)

            if not entitlement.has_unlimited_access:
                usage.question_count += number
                usage.save(update_fields=["question_count"])
                questions_remaining_today = max(
                    UserEntitlement.FREE_DAILY_QUESTION_LIMIT - usage.question_count,
                    0,
                )

        return Response({
            "questions": combined_questions,
            "session_id": session.id,
            "qualification": qualification,
            "questions_remaining_today": questions_remaining_today,
            "plan_type": entitlement.plan_type,
        }, status=200)

    except DailyQuestionLimitExceeded:
        return Response(
            {
                "error": "Free users can only generate 1 question per day. Upgrade for unlimited access.",
                "plan_type": entitlement.plan_type,
                "questions_remaining_today": 0,
            },
            status=403,
        )
    except BiologyTopic.DoesNotExist:
        return Response({"error": "Invalid topic selected for this exam board"}, status=400)
    except BiologySubTopic.DoesNotExist:
        return Response({"error": "Invalid subtopic for the selected topic"}, status=400)
    except BiologySubCategory.DoesNotExist:
        return Response({"error": "Invalid subcategory for the selected subtopic"}, status=400)
    except GCSEScienceTopic.DoesNotExist:
        return Response({"error": "Invalid GCSE topic selected for this exam board and subject"}, status=400)
    except GCSEScienceSubTopic.DoesNotExist:
        return Response({"error": "Invalid GCSE subtopic for the selected GCSE topic"}, status=400)
    except GCSEScienceSubCategory.DoesNotExist:
        return Response({"error": "Invalid GCSE subcategory for the selected GCSE subtopic"}, status=400)
    except json.JSONDecodeError:
        return Response({"error": "Invalid JSON format in fallback question bank"}, status=500)
    except Exception as e:
        logger.exception("generate_exam_questions failed")
        return Response({"error": str(e)}, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_user_answer(request):
    qualification = _normalize_qualification(request.data.get("qualification"))
    answers = request.data.get("answers")
    exam_board = (request.data.get("exam_board", "AQA") or "AQA").strip().upper()
    if exam_board not in ALLOWED_BOARDS:
        return Response({"error": "Invalid exam_board. Use 'OCR' or 'AQA'."}, status=400)
    if qualification not in ALLOWED_QUALIFICATIONS:
        return Response({"error": "Invalid qualification. Use 'ALEVEL_BIOLOGY' or 'GCSE_SCIENCE'."}, status=400)

    gcse_subject = _normalize_gcse_subject(request.data.get("subject"))
    gcse_tier = _normalize_gcse_tier(request.data.get("tier"))
    if qualification == QualificationPath.GCSE_SCIENCE:
        if gcse_subject not in ALLOWED_GCSE_SUBJECTS:
            return Response({"error": "Invalid GCSE subject. Use 'BIOLOGY', 'CHEMISTRY', or 'PHYSICS'."}, status=400)
        if gcse_tier not in ALLOWED_GCSE_TIERS:
            return Response({"error": "Invalid GCSE tier. Use 'FOUNDATION' or 'HIGHER'."}, status=400)

    if answers is not None:
        if not isinstance(answers, list) or not answers:
            return Response({"error": "answers must be a non-empty list."}, status=400)

        for answer in answers:
            if not answer.get("question") or not answer.get("mark_scheme"):
                return Response({"error": "Each answer must include question and mark_scheme."}, status=400)

        try:
            if qualification == QualificationPath.GCSE_SCIENCE:
                result = evaluate_gcse_batch_responses_with_openai(answers, exam_board, gcse_subject, gcse_tier)
            else:
                result = evaluate_batch_responses_with_openai(answers, exam_board)
            return Response(result, status=200)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON from OpenAI batch marking: %s", e)
            return Response({"error": "Invalid JSON returned by OpenAI"}, status=500)
        except Exception as e:
            logger.error("Unexpected batch marking error: %s", e)
            return Response({"error": str(e)}, status=500)

    question = request.data.get("question")
    mark_scheme = request.data.get("mark_scheme")
    user_answer = request.data.get("user_answer")

    logger.info("Incoming marking request:")
    logger.info("Question: %s", question)
    logger.info("User Answer: %s", user_answer)
    logger.info("Mark Scheme: %s", mark_scheme)
    logger.info("Exam Board: %s", exam_board)
    logger.info("Qualification: %s", qualification)

    if not all([question, mark_scheme, user_answer]):
        return Response({"error": "Missing one or more fields."}, status=400)

    try:
        if qualification == QualificationPath.GCSE_SCIENCE:
            result = evaluate_gcse_response_with_openai(question, mark_scheme, user_answer, exam_board, gcse_subject, gcse_tier)
        else:
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
        total_score = sum(_coerce_numeric_score(a.get("score", 0)) for a in answers)
        feedback = normalize_feedback_payload(feedback_text, answers)
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


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_gcse_topics(request):
    board = (request.query_params.get("exam_board") or "").strip().upper()
    subject = _normalize_gcse_subject(request.query_params.get("subject"))
    tier = _normalize_gcse_tier(request.query_params.get("tier"))
    qs = GCSEScienceTopic.objects.all().order_by("topic")
    if board:
        if board not in ALLOWED_BOARDS:
            return Response({"error": "Invalid exam_board. Use 'OCR' or 'AQA'."}, status=400)
        qs = qs.filter(exam_board=board)
    if subject:
        if subject not in ALLOWED_GCSE_SUBJECTS:
            return Response({"error": "Invalid GCSE subject. Use 'BIOLOGY', 'CHEMISTRY', or 'PHYSICS'."}, status=400)
        qs = qs.filter(subject=subject)
    if tier:
        if tier not in ALLOWED_GCSE_TIERS:
            return Response({"error": "Invalid GCSE tier. Use 'FOUNDATION' or 'HIGHER'."}, status=400)
        qs = qs.filter(tier=tier)
    return Response(GCSETopicListSerializer(qs, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_gcse_subtopics(request):
    board = (request.query_params.get("exam_board") or "").strip().upper()
    subject = _normalize_gcse_subject(request.query_params.get("subject"))
    tier = _normalize_gcse_tier(request.query_params.get("tier"))
    topic_id = request.query_params.get("topic_id")
    qs = GCSEScienceSubTopic.objects.select_related("topic").all().order_by("title")
    if topic_id:
        qs = qs.filter(topic_id=topic_id)
    if board:
        if board not in ALLOWED_BOARDS:
            return Response({"error": "Invalid exam_board. Use 'OCR' or 'AQA'."}, status=400)
        qs = qs.filter(topic__exam_board=board)
    if subject:
        if subject not in ALLOWED_GCSE_SUBJECTS:
            return Response({"error": "Invalid GCSE subject. Use 'BIOLOGY', 'CHEMISTRY', or 'PHYSICS'."}, status=400)
        qs = qs.filter(topic__subject=subject)
    if tier:
        if tier not in ALLOWED_GCSE_TIERS:
            return Response({"error": "Invalid GCSE tier. Use 'FOUNDATION' or 'HIGHER'."}, status=400)
        qs = qs.filter(topic__tier=tier)
    return Response(GCSESubTopicListSerializer(qs, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_gcse_subcategories(request):
    board = (request.query_params.get("exam_board") or "").strip().upper()
    subject = _normalize_gcse_subject(request.query_params.get("subject"))
    tier = _normalize_gcse_tier(request.query_params.get("tier"))
    subtopic_id = request.query_params.get("subtopic_id")
    qs = GCSEScienceSubCategory.objects.select_related("subtopic", "subtopic__topic").all().order_by("title")
    if subtopic_id:
        qs = qs.filter(subtopic_id=subtopic_id)
    if board:
        if board not in ALLOWED_BOARDS:
            return Response({"error": "Invalid exam_board. Use 'OCR' or 'AQA'."}, status=400)
        qs = qs.filter(subtopic__topic__exam_board=board)
    if subject:
        if subject not in ALLOWED_GCSE_SUBJECTS:
            return Response({"error": "Invalid GCSE subject. Use 'BIOLOGY', 'CHEMISTRY', or 'PHYSICS'."}, status=400)
        qs = qs.filter(subtopic__topic__subject=subject)
    if tier:
        if tier not in ALLOWED_GCSE_TIERS:
            return Response({"error": "Invalid GCSE tier. Use 'FOUNDATION' or 'HIGHER'."}, status=400)
        qs = qs.filter(subtopic__topic__tier=tier)
    return Response(GCSESubCategoryListSerializer(qs, many=True).data)

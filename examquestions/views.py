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
    GCSEScienceRoute,
    GCSETier,
)
from accounts.models import CustomUser, QuestionUsage, UserEntitlement
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


EXAMQUESTIONS_DIR = Path(__file__).resolve().parent.parent / "examquestions"
FALLBACK_QUESTIONS_DIR = EXAMQUESTIONS_DIR / "fallbackQuestions"

# ------------------------------------------------------------
# Exam board → local fallback question banks (same JSON schema)
# ------------------------------------------------------------
FALLBACK_QUESTION_PATHS = {
    "OCR": FALLBACK_QUESTIONS_DIR / "ocr_questions.json",
    "AQA": FALLBACK_QUESTIONS_DIR / "aqa_questions.json",
}
OCR_GCSE_SEPARATE_FALLBACK_PATHS = {
    GCSESubject.BIOLOGY: FALLBACK_QUESTIONS_DIR / "ocr_gateway_gcse_triple_biology_fallback_questions.json",
    GCSESubject.CHEMISTRY: FALLBACK_QUESTIONS_DIR / "ocr_gateway_gcse_triple_chemistry_fallback_questions.json",
    GCSESubject.PHYSICS: FALLBACK_QUESTIONS_DIR / "ocr_gateway_gcse_triple_physics_fallback_questions.json",
    GCSESubject.COMBINED: FALLBACK_QUESTIONS_DIR / "ocr_gateway_gcse_combined.json",
}
AQA_GCSE_SEPARATE_FALLBACK_PATHS = {
    GCSESubject.BIOLOGY: FALLBACK_QUESTIONS_DIR / "aqa_triple_biology_compact_exam_style.json",
    GCSESubject.CHEMISTRY: FALLBACK_QUESTIONS_DIR / "aqa_triple_chemistry_compact_exam_style.json",
    GCSESubject.PHYSICS: FALLBACK_QUESTIONS_DIR / "aqa_triple_physics_compact_exam_style.json",
}
GCSE_FALLBACK_PATHS_BY_BOARD = {
    "OCR": OCR_GCSE_SEPARATE_FALLBACK_PATHS,
    "AQA": AQA_GCSE_SEPARATE_FALLBACK_PATHS,
}
ALLOWED_BOARDS = {"OCR", "AQA"}
ALLOWED_QUALIFICATIONS = {choice for choice, _ in QualificationPath.choices}
ALLOWED_GCSE_SUBJECTS = {choice for choice, _ in GCSESubject.choices}
ALLOWED_GCSE_TIERS = {choice for choice, _ in GCSETier.choices}
GCSE_SUBJECT_ERROR_MESSAGE = "Invalid GCSE subject. Use 'BIOLOGY', 'CHEMISTRY', 'PHYSICS', or 'COMBINED'."


class DailyQuestionLimitExceeded(Exception):
    pass


def _normalize_choice(raw_value):
    return str(raw_value or "").strip().replace("-", "_").replace(" ", "_").upper()


def _normalize_qualification(raw_value, default=QualificationPath.ALEVEL_BIOLOGY):
    return CustomUser.normalize_paid_access_qualification(raw_value) or default


def _has_paid_generation_access(user, qualification):
    # Paid access is qualification-specific, but the free quota remains shared per user per day.
    return user.has_paid_access_for_qualification(qualification)


def _current_plan_type(user, entitlement):
    if entitlement.lifetime_unlocked:
        return UserEntitlement.PlanType.LIFETIME
    if user.has_gcse_paid_access or user.has_alevel_paid_access:
        return UserEntitlement.PlanType.PAID
    return UserEntitlement.PlanType.FREE


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


UNSEEN_RESOURCE_PATTERNS = [
    re.compile(r"\b(?:figure|fig\.?|graph|table|chart|diagram|image)\b"),
    re.compile(r"\b(?:data|results?|information)\s+(?:above|below|provided|shown|in)\b"),
    re.compile(r"\bshown in\b"),
    re.compile(r"\b(?:use|using|refer(?:ring)? to|from|based on)\s+(?:the\s+)?(?:information|data|results?|figure|fig\.?|graph|table|chart|diagram|image)\b"),
    re.compile(r"\b(?:in|on)\s+the\s+(?:figure|fig\.?|graph|table|chart|diagram|image)\b"),
    re.compile(r"\b(?:information|data|results?)\s+(?:provided|given|displayed)\b"),
]

METHOD_EVALUATION_PATTERNS = [
    re.compile(r"\bevaluate the method(?: used)?\b"),
    re.compile(r"\bevaluate (?:this|the|a student'?s) (?:method|investigation|experiment)\b"),
    re.compile(r"\bsuggest improvements? (?:to|for) the method\b"),
    re.compile(r"\bsuggest improvements? (?:to|for) (?:this|the|a student'?s) (?:investigation|experiment)\b"),
    re.compile(r"\bhow could the method be improved\b"),
    re.compile(r"\bhow could (?:this|the) (?:investigation|experiment) be improved\b"),
]

PROCEDURAL_DETAIL_PATTERNS = [
    re.compile(r"\busing\b"),
    re.compile(r"\bmeasure\w*\b"),
    re.compile(r"\brecord\w*\b"),
    re.compile(r"\bcount\w*\b"),
    re.compile(r"\btim\w*\b"),
    re.compile(r"\bcalculate\w*\b"),
    re.compile(r"\bmix\w*\b"),
    re.compile(r"\badd\w*\b"),
    re.compile(r"\bplace\w*\b"),
    re.compile(r"\bheat\w*\b"),
    re.compile(r"\bcool\w*\b"),
    re.compile(r"\biodine\b"),
    re.compile(r"\bcolorimeter\b"),
    re.compile(r"\bwater bath\b"),
    re.compile(r"\btest tube\b"),
    re.compile(r"\bbalance\b"),
    re.compile(r"\bthermometer\b"),
    re.compile(r"\bpipette\b"),
    re.compile(r"\bburette\b"),
    re.compile(r"\b\d+(?:\.\d+)?\s?(?:cm3|cm\^3|ml|dm3|g|mg|kg|mm|cm|m|s|seconds?|minutes?|hours?|°c|degrees c)\b"),
]


def is_self_contained_ai_question(question_item):
    normalized_question = normalize_question_text(question_text_from_item(question_item))
    if not normalized_question:
        return False

    if any(pattern.search(normalized_question) for pattern in UNSEEN_RESOURCE_PATTERNS):
        return False

    if any(pattern.search(normalized_question) for pattern in METHOD_EVALUATION_PATTERNS):
        detail_matches = sum(bool(pattern.search(normalized_question)) for pattern in PROCEDURAL_DETAIL_PATTERNS)
        if detail_matches < 2:
            return False

    return True


def filter_self_contained_ai_questions(ai_questions):
    valid_questions = []
    for question_item in ai_questions or []:
        if is_self_contained_ai_question(question_item):
            valid_questions.append(question_item)
            continue
        logger.warning("Discarded AI-generated question without enough context: %s", question_text_from_item(question_item))
    return valid_questions


def get_fallback_pool(all_fallback_questions, scope_title, topic_title, allow_generic=False):
    if not isinstance(all_fallback_questions, dict):
        return []

    scope_pool = all_fallback_questions.get(scope_title)
    if isinstance(scope_pool, list) and scope_pool:
        return scope_pool

    topic_pool = all_fallback_questions.get(topic_title)
    if isinstance(topic_pool, list) and topic_pool:
        return topic_pool

    if not allow_generic:
        return []

    lower_mark_questions = []
    all_self_contained_questions = []
    for question_group in all_fallback_questions.values():
        if not isinstance(question_group, list):
            continue
        for question_item in question_group:
            if not is_self_contained_ai_question(question_item):
                continue
            all_self_contained_questions.append(question_item)
            total_marks = question_item.get("total_marks", question_item.get("mark", 0)) or 0
            if total_marks <= 3:
                lower_mark_questions.append(question_item)

    if lower_mark_questions:
        return lower_mark_questions
    return all_self_contained_questions


def build_gcse_scope_metadata(topic, subtopic=None, subcategory=None, tier=None):
    if subcategory:
        return subcategory.title, f"gcse-subcategory:{subcategory.id}:{tier}"
    if subtopic:
        return subtopic.title, f"gcse-subtopic:{subtopic.id}:{tier}"
    return topic.topic, f"gcse-topic:{topic.id}:{tier}"


@lru_cache(maxsize=None)
def load_fallback_bank_from_path(path_value: str) -> dict:
    path = Path(path_value)

    logger.info("Using fallback file: %s | Exists: %s", path, path.exists())

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("Fallback file NOT FOUND at %s", path)
        return {}
    # Let JSONDecodeError bubble up so the caller's except block handles it.


def load_fallback_bank_for_board(exam_board: str) -> dict:
    board_key = (exam_board or "").strip().upper()
    path = FALLBACK_QUESTION_PATHS.get(board_key, FALLBACK_QUESTION_PATHS["OCR"])

    logger.info("Exam board: %s | Using fallback file: %s", board_key, path)
    return load_fallback_bank_from_path(str(path))


def resolve_gcse_fallback_bank_path(exam_board: str, gcse_subject: str) -> Path | None:
    board_key = (exam_board or "").strip().upper()
    normalized_subject = _normalize_gcse_subject(gcse_subject)
    fallback_paths = GCSE_FALLBACK_PATHS_BY_BOARD.get(board_key)
    if fallback_paths is None:
        return None
    return fallback_paths.get(normalized_subject)


def load_fallback_bank_for_gcse(exam_board: str, gcse_subject: str) -> dict:
    path = resolve_gcse_fallback_bank_path(exam_board, gcse_subject)
    if path is None:
        logger.info(
            "GCSE fallback routing | board=%s | subject=%s | file=<none configured>",
            (exam_board or "").strip().upper(),
            gcse_subject,
        )
        return {}
    logger.info(
        "GCSE fallback routing | board=%s | subject=%s | file=%s",
        (exam_board or "").strip().upper(),
        gcse_subject,
        path,
    )
    return load_fallback_bank_from_path(str(path))


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


def build_question_scope(topic_title, subtopic=None, subcategory=None):
    scope = topic_title
    if subtopic:
        scope += f' (SubTopic: {subtopic.title})'
    if subcategory:
        scope += f' (SubCategory: {subcategory.title})'
    return scope


def collect_valid_ai_questions(ai_questions, served_questions):
    accepted_questions = []
    current_batch_questions = set()

    for ai_question in ai_questions:
        normalized = normalize_question_text(question_text_from_item(ai_question))
        if not normalized or normalized in served_questions or normalized in current_batch_questions:
            continue
        current_batch_questions.add(normalized)
        accepted_questions.append(ai_question)

    return accepted_questions


def prepare_alevel_generation(user, board_key, topic_id, subtopic_id, subcategory_id, number):
    topic = BiologyTopic.objects.get(id=topic_id, exam_board=board_key)

    subtopic = None
    if subtopic_id:
        subtopic = BiologySubTopic.objects.get(id=subtopic_id, topic_id=topic.id)

    subcategory = None
    if subcategory_id:
        if not subtopic_id:
            raise ValueError("subcategory_id provided without subtopic_id")
        subcategory = BiologySubCategory.objects.get(id=subcategory_id, subtopic_id=subtopic.id)

    all_fallback_questions = load_fallback_bank_for_board(board_key)
    scope_title, scope_key = build_scope_metadata(topic, subtopic, subcategory)
    served_questions = get_user_served_question_set(user, board_key, scope_key)
    fallback_pool = get_fallback_pool(all_fallback_questions, scope_title, topic.topic)

    scope = build_question_scope(topic.topic, subtopic, subcategory)
    ai_response = generate_questions(scope, board_key, number)
    ai_questions = filter_self_contained_ai_questions(ai_response.get("questions", []))
    combined_questions = collect_valid_ai_questions(ai_questions, served_questions)

    if len(combined_questions) < number:
        combined_questions, served_questions = replace_duplicate_questions_from_fallback(
            user=user,
            exam_board=board_key,
            scope_key=scope_key,
            fallback_pool=fallback_pool if isinstance(fallback_pool, list) else [],
            accepted_questions=combined_questions,
            requested_count=number,
            served_questions=served_questions,
        )

    total_available = sum(q.get("total_marks", q.get("mark", 0)) for q in combined_questions)
    return {
        "scope_key": scope_key,
        "combined_questions": combined_questions,
        "session_kwargs": {
            "topic": topic,
            "subtopic": subtopic,
            "subcategory": subcategory,
            "qualification": QualificationPath.ALEVEL_BIOLOGY,
            "exam_board": board_key,
            "number_of_questions": number,
            "total_available": total_available,
        },
    }


def prepare_gcse_generation(user, board_key, topic_id, subtopic_id, subcategory_id, gcse_subject, gcse_tier, number):
    gcse_topic = GCSEScienceTopic.objects.get(id=topic_id, exam_board=board_key, subject=gcse_subject)
    science_route = (
        GCSEScienceRoute.COMBINED
        if gcse_subject == GCSESubject.COMBINED
        else GCSEScienceRoute.SEPARATE
    )

    gcse_subtopic = None
    if subtopic_id:
        gcse_subtopic = GCSEScienceSubTopic.objects.get(id=subtopic_id, topic_id=gcse_topic.id)

    gcse_subcategory = None
    if subcategory_id:
        if not subtopic_id:
            raise ValueError("subcategory_id provided without subtopic_id")
        gcse_subcategory = GCSEScienceSubCategory.objects.get(id=subcategory_id, subtopic_id=gcse_subtopic.id)

    all_fallback_questions = load_fallback_bank_for_gcse(board_key, gcse_subject)
    scope_title, scope_key = build_gcse_scope_metadata(gcse_topic, gcse_subtopic, gcse_subcategory, gcse_tier)
    served_questions = get_user_served_question_set(user, board_key, scope_key)
    fallback_pool = get_fallback_pool(all_fallback_questions, scope_title, gcse_topic.topic, allow_generic=True)

    scope = build_question_scope(gcse_topic.topic, gcse_subtopic, gcse_subcategory)
    ai_response = generate_gcse_questions(scope, board_key, number, gcse_subject, gcse_tier)
    ai_questions = filter_self_contained_ai_questions(ai_response.get("questions", []))
    combined_questions = collect_valid_ai_questions(ai_questions, served_questions)

    if len(combined_questions) < number:
        if not fallback_pool:
            raise ValueError(f"No GCSE fallback question bank configured for {board_key} {gcse_subject}.")
        combined_questions, served_questions = replace_duplicate_questions_from_fallback(
            user=user,
            exam_board=board_key,
            scope_key=scope_key,
            fallback_pool=fallback_pool if isinstance(fallback_pool, list) else [],
            accepted_questions=combined_questions,
            requested_count=number,
            served_questions=served_questions,
        )

    total_available = sum(q.get("total_marks", q.get("mark", 0)) for q in combined_questions)
    return {
        "scope_key": scope_key,
        "combined_questions": combined_questions,
        "session_kwargs": {
            "qualification": QualificationPath.GCSE_SCIENCE,
            "gcse_topic": gcse_topic,
            "gcse_subtopic": gcse_subtopic,
            "gcse_subcategory": gcse_subcategory,
            "gcse_subject": gcse_subject,
            "science_route": science_route,
            "gcse_tier": gcse_tier,
            "exam_board": board_key,
            "number_of_questions": number,
            "total_available": total_available,
        },
    }


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def generate_exam_questions(request):
    topic_id = request.data.get("topic_id")
    subtopic_id = request.data.get("subtopic_id")         # optional
    subcategory_id = request.data.get("subcategory_id")   # optional
    exam_board = request.data.get("exam_board")
    qualification = _normalize_qualification(request.data.get("qualification"), default='')
    try:
        number = int(request.data.get("number_of_questions"))
    except (TypeError, ValueError):
        number = 0

    if not all([topic_id, exam_board, number]):
        return Response({"error": "Missing required fields"}, status=400)
    if request.data.get('qualification') in {None, ''}:
        return Response({"error": "qualification is required. Use 'GCSE_SCIENCE' or 'ALEVEL_BIOLOGY'."}, status=400)

    board_key = (exam_board or "").strip().upper()
    if board_key not in ALLOWED_BOARDS:
        return Response({"error": "Invalid exam_board. Use 'OCR' or 'AQA'."}, status=400)
    if qualification not in ALLOWED_QUALIFICATIONS:
        return Response({"error": "Invalid qualification. Use 'ALEVEL_BIOLOGY' or 'GCSE_SCIENCE'."}, status=400)

    entitlement = get_or_create_entitlement(request.user)
    current_plan_type = _current_plan_type(request.user, entitlement)
    today = timezone.localdate()
    questions_remaining_today = None
    has_paid_access_for_request = _has_paid_generation_access(request.user, qualification)

    if not has_paid_access_for_request:
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
                    "error": "Free users can only generate 1 question per day total. Upgrade this qualification for unlimited access.",
                    "plan_type": current_plan_type,
                    "questions_remaining_today": questions_remaining_today,
                },
                status=403,
            )

    try:
        if qualification == QualificationPath.ALEVEL_BIOLOGY:
            generation_result = prepare_alevel_generation(
                user=request.user,
                board_key=board_key,
                topic_id=topic_id,
                subtopic_id=subtopic_id,
                subcategory_id=subcategory_id,
                number=number,
            )
        else:
            gcse_subject = _normalize_gcse_subject(request.data.get("subject"))
            gcse_tier = _normalize_gcse_tier(request.data.get("tier"))
            if gcse_subject not in ALLOWED_GCSE_SUBJECTS:
                return Response({"error": GCSE_SUBJECT_ERROR_MESSAGE}, status=400)
            if gcse_tier not in ALLOWED_GCSE_TIERS:
                return Response({"error": "Invalid GCSE tier. Use 'FOUNDATION' or 'HIGHER'."}, status=400)

            generation_result = prepare_gcse_generation(
                user=request.user,
                board_key=board_key,
                topic_id=topic_id,
                subtopic_id=subtopic_id,
                subcategory_id=subcategory_id,
                gcse_subject=gcse_subject,
                gcse_tier=gcse_tier,
                number=number,
            )

        scope_key = generation_result["scope_key"]
        combined_questions = generation_result["combined_questions"]
        session_kwargs = generation_result["session_kwargs"]

        with transaction.atomic():
            if not has_paid_access_for_request:
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

            if not has_paid_access_for_request:
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
            "plan_type": current_plan_type,
        }, status=200)

    except DailyQuestionLimitExceeded:
        return Response(
            {
                "error": "Free users can only generate 1 question per day total. Upgrade this qualification for unlimited access.",
                "plan_type": current_plan_type,
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
    except ValueError as exc:
        if str(exc) == "subcategory_id provided without subtopic_id":
            return Response({"error": str(exc)}, status=400)
        raise
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
            return Response({"error": GCSE_SUBJECT_ERROR_MESSAGE}, status=400)
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
    sessions = QuestionSession.objects.select_related(
        'topic',
        'subtopic',
        'subcategory',
        'gcse_topic',
        'gcse_subtopic',
        'gcse_subcategory',
    ).filter(user=request.user).order_by('-created_at')
    serializer = QuestionSessionSerializer(sessions, many=True)
    return Response(serializer.data)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_user_results(request):
    delete_mode = str(request.data.get('mode') or request.query_params.get('mode') or 'hard').strip().lower()
    if delete_mode != 'hard':
        return Response(
            {'error': "Soft reset moved to POST /accounts/reset-performance-tracking/. Use mode='hard' for permanent deletion here."},
            status=400,
        )

    sessions = QuestionSession.objects.filter(user=request.user)
    deleted_count = sessions.count()
    sessions.delete()
    return Response({
        'message': 'All user results permanently deleted.',
        'mode': 'hard',
        'deleted_count': deleted_count,
        'performance_tracking_start_date': request.user.performance_tracking_start_date,
    }, status=200)


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
            return Response({"error": GCSE_SUBJECT_ERROR_MESSAGE}, status=400)
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
            return Response({"error": GCSE_SUBJECT_ERROR_MESSAGE}, status=400)
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
            return Response({"error": GCSE_SUBJECT_ERROR_MESSAGE}, status=400)
        qs = qs.filter(subtopic__topic__subject=subject)
    if tier:
        if tier not in ALLOWED_GCSE_TIERS:
            return Response({"error": "Invalid GCSE tier. Use 'FOUNDATION' or 'HIGHER'."}, status=400)
        qs = qs.filter(subtopic__topic__tier=tier)
    return Response(GCSESubCategoryListSerializer(qs, many=True).data)

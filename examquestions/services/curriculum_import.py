"""Shared A-level curriculum import logic used by the management command and Django Admin."""
import json

from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import Subject
from examquestions.models import (
    ALevelSubCategory,
    ALevelSubTopic,
    ALevelTopic,
    ExamBoard,
)

FILE_KEYS = {"subject", "exam_board", "specification", "source", "topics"}
REQUIRED_FILE_KEYS = {"subject", "exam_board", "specification", "topics"}
SOURCE_KEYS = {"name", "url", "version"}
TOPIC_KEYS = {"topic", "subtopics"}
SUBTOPIC_KEYS = {"title", "subcategories"}
NO_SPECIFICATION_BOARDS = {ExamBoard.AQA.value, ExamBoard.OCR.value}
COUNT_LABELS = ("topics", "subtopics", "subcategories")


class CurriculumImportError(Exception):
    """A user-presentable import failure that never exposes internals."""

    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = list(errors or [])


def parse_curriculum_bytes(raw):
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CurriculumImportError(f"Could not parse the file as UTF-8 JSON: {exc}")


def load_and_validate(raw):
    """Parse raw bytes and validate them; return the data or raise CurriculumImportError."""
    data = parse_curriculum_bytes(raw)
    errors = validate_curriculum_data(data)
    if errors:
        raise CurriculumImportError("Curriculum file is invalid.", errors)
    return data


def _check_keys(obj, allowed, required, where, errors):
    if not isinstance(obj, dict):
        errors.append(f"{where}: expected an object.")
        return False
    for key in sorted(set(obj) - allowed):
        errors.append(f"{where}: unknown field '{key}'.")
    for key in sorted(required - set(obj)):
        errors.append(f"{where}: missing required field '{key}'.")
    return True


def _check_title(value, max_length, where, errors):
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{where}: must be a non-empty string.")
        return False
    if value != value.strip():
        errors.append(f"{where}: must not have leading or trailing whitespace.")
        return False
    if len(value) > max_length:
        errors.append(f"{where}: must be at most {max_length} characters.")
        return False
    return True


def _check_unique(titles, where, errors):
    seen = set()
    for title in titles:
        if not isinstance(title, str):
            continue
        key = title.casefold()
        if key in seen:
            errors.append(f"{where}: duplicate title '{title}'.")
        seen.add(key)


def validate_curriculum_data(data):
    """Return a list of validation errors; an empty list means the data is valid."""
    errors = []
    if not _check_keys(data, FILE_KEYS, REQUIRED_FILE_KEYS, "file", errors):
        return errors

    subject_code = data.get("subject")
    if "subject" in data:
        if not isinstance(subject_code, str) or not subject_code:
            errors.append("subject: must be a non-empty string.")
        elif not Subject.objects.filter(code=subject_code, qualification=Subject.Qualification.A_LEVEL).exists():
            errors.append(f"subject: '{subject_code}' is not an existing A-level subject.")

    board = data.get("exam_board")
    if "exam_board" in data and board not in ExamBoard.values:
        errors.append(f"exam_board: must be one of {', '.join(ExamBoard.values)}.")

    if "specification" in data:
        spec = data["specification"]
        if not isinstance(spec, str):
            errors.append("specification: must be a string.")
        elif board in NO_SPECIFICATION_BOARDS and spec != "":
            errors.append(f"specification: must be an empty string for {board}.")
        elif board == ExamBoard.EDEXCEL:
            if not spec.strip():
                errors.append("specification: is required for EDEXCEL.")
            elif spec != spec.strip() or len(spec) > 100:
                errors.append("specification: must be trimmed and at most 100 characters.")

    if "source" in data:
        source = data["source"]
        if _check_keys(source, SOURCE_KEYS, {"name"}, "source", errors):
            for key in SOURCE_KEYS & set(source):
                if not isinstance(source[key], str) or not source[key].strip():
                    errors.append(f"source.{key}: must be a non-empty string.")

    topics = data.get("topics")
    if "topics" in data:
        if not isinstance(topics, list) or not topics:
            errors.append("topics: must be a non-empty list.")
            return errors
        _validate_topics(topics, errors)
    return errors


def _validate_topics(topics, errors):
    _check_unique([t.get("topic") for t in topics if isinstance(t, dict)], "topics", errors)
    for ti, topic in enumerate(topics):
        where = f"topics[{ti}]"
        if not _check_keys(topic, TOPIC_KEYS, {"topic"}, where, errors):
            continue
        if "topic" in topic:
            _check_title(topic["topic"], 255, f"{where}.topic", errors)
        subtopics = topic.get("subtopics", [])
        if not isinstance(subtopics, list):
            errors.append(f"{where}.subtopics: must be a list.")
            continue
        _check_unique([s.get("title") for s in subtopics if isinstance(s, dict)], f"{where}.subtopics", errors)
        for si, subtopic in enumerate(subtopics):
            swhere = f"{where}.subtopics[{si}]"
            if not _check_keys(subtopic, SUBTOPIC_KEYS, {"title"}, swhere, errors):
                continue
            if "title" in subtopic:
                _check_title(subtopic["title"], 200, f"{swhere}.title", errors)
            subcategories = subtopic.get("subcategories", [])
            if not isinstance(subcategories, list):
                errors.append(f"{swhere}.subcategories: must be a list of strings.")
                continue
            _check_unique(subcategories, f"{swhere}.subcategories", errors)
            for ci, title in enumerate(subcategories):
                _check_title(title, 200, f"{swhere}.subcategories[{ci}]", errors)


def preview_import(data):
    """Database-aware dry run. Returns {label: [to_create, existing]} and writes nothing."""
    return _run(data, apply=False)


def apply_import(data):
    """Validate against current database state and write atomically. Returns {label: [created, reused]}."""
    errors = validate_curriculum_data(data)
    if errors:
        raise CurriculumImportError("Curriculum file is invalid.", errors)
    return _run(data, apply=True)


def _run(data, apply):
    try:
        with transaction.atomic():
            return _import(data, apply)
    except ValidationError as exc:
        raise CurriculumImportError(f"Model validation failed; nothing was written: {'; '.join(exc.messages)}")


def _import(data, apply):
    subject_qs = Subject.objects.all()
    if apply:
        # Serialises concurrent imports for the same subject on databases that support row locks.
        subject_qs = subject_qs.select_for_update()
    subject = subject_qs.get(code=data["subject"])
    counts = {label: [0, 0] for label in COUNT_LABELS}
    model_label = {ALevelTopic: "topics", ALevelSubTopic: "subtopics", ALevelSubCategory: "subcategories"}

    def resolve(model, queryset, field, name, **create_fields):
        exact = list(queryset.filter(**{field: name}))
        if len(exact) > 1:
            raise CurriculumImportError(f"Ambiguous existing {model.__name__} records for '{name}'.")
        if exact:
            counts[model_label[model]][1] += 1
            return exact[0]
        if queryset.filter(**{f"{field}__iexact": name}).exists():
            raise CurriculumImportError(
                f"Conflict: an existing {model.__name__} differs from '{name}' only by case; refusing to import."
            )
        counts[model_label[model]][0] += 1
        if not apply:
            return None
        obj = model(**create_fields, **{field: name})
        obj.full_clean()
        obj.save()
        return obj

    topic_scope = ALevelTopic.objects.filter(
        subject=subject, exam_board=data["exam_board"], specification=data["specification"]
    )
    for topic_data in data["topics"]:
        topic = resolve(
            ALevelTopic, topic_scope, "topic", topic_data["topic"],
            subject=subject, exam_board=data["exam_board"], specification=data["specification"],
        )
        for sub_data in topic_data.get("subtopics", []):
            sub_scope = ALevelSubTopic.objects.filter(topic=topic) if topic else ALevelSubTopic.objects.none()
            subtopic = resolve(ALevelSubTopic, sub_scope, "title", sub_data["title"], topic=topic)
            for title in sub_data.get("subcategories", []):
                cat_scope = (
                    ALevelSubCategory.objects.filter(subtopic=subtopic)
                    if subtopic else ALevelSubCategory.objects.none()
                )
                resolve(ALevelSubCategory, cat_scope, "title", title, subtopic=subtopic)
    return counts

import hashlib
import hmac
import json
import logging
import secrets
import time

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from .services.curriculum_import import (
    CurriculumImportError,
    apply_import,
    load_and_validate,
    preview_import,
)
from .models import (
    ALevelSubCategory,
    ALevelSubTopic,
    ALevelTopic,
    QuestionSession,
    BiologyTopic,
    BiologySubTopic,
    BiologySubCategory,
    GCSEScienceTopic,
    GCSEScienceSubTopic,
    GCSEScienceSubCategory,
)


@admin.register(QuestionSession)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("user", "qualification", "topic", "gcse_topic", "gcse_subtopic", "gcse_subcategory", "exam_board", "number_of_questions", "total_score", "total_available", "created_at")
    search_fields = ("user__username", "topic__topic", "gcse_topic__topic", "gcse_subtopic__title", "gcse_subcategory__title", "exam_board")
    list_filter = ("qualification", "exam_board", "created_at")
    ordering = ("-created_at",)


@admin.register(BiologyTopic)
class BiologyTopicAdmin(admin.ModelAdmin):
    list_display = ("topic",)
    search_filter = ("topic",)
    list_filter = ("topic",)

@admin.register(BiologySubTopic)
class BiologySubTopicAdmin(admin.ModelAdmin):  # added colon
    list_display = ("title", "topic")
    search_fields = ("title",)
    list_filter = ("title",)

@admin.register(BiologySubCategory)
class BiologySubCategoryAdmin(admin.ModelAdmin):  # fixed class name + colon
    list_display = ("title", "subtopic")
    search_fields = ("title",)
    list_filter = ("subtopic",)


@admin.register(GCSEScienceTopic)
class GCSEScienceTopicAdmin(admin.ModelAdmin):
    list_display = ("topic", "subject", "tier", "exam_board")
    search_fields = ("topic",)
    list_filter = ("subject", "tier", "exam_board")


@admin.register(GCSEScienceSubTopic)
class GCSEScienceSubTopicAdmin(admin.ModelAdmin):
    list_display = ("title", "topic")
    search_fields = ("title",)
    list_filter = ("topic__subject", "topic__tier", "topic__exam_board")


@admin.register(GCSEScienceSubCategory)
class GCSEScienceSubCategoryAdmin(admin.ModelAdmin):
    list_display = ("title", "subtopic")
    search_fields = ("title",)
    list_filter = ("subtopic__topic__subject", "subtopic__topic__tier", "subtopic__topic__exam_board")

logger = logging.getLogger(__name__)

PENDING_IMPORT_SESSION_KEY = "alevel_curriculum_import_pending"
DEFAULT_IMPORT_MAX_BYTES = 512 * 1024
DEFAULT_IMPORT_TOKEN_TTL_SECONDS = 15 * 60
MAX_DISPLAYED_ERRORS = 50


def _limit_errors(errors):
    errors = list(errors)
    if len(errors) <= MAX_DISPLAYED_ERRORS:
        return errors
    omitted = len(errors) - MAX_DISPLAYED_ERRORS
    return errors[:MAX_DISPLAYED_ERRORS] + [
        f"Showing the first {MAX_DISPLAYED_ERRORS} of {len(errors)} errors; {omitted} more omitted. "
        "Fix these and upload again."
    ]


class CurriculumUploadForm(forms.Form):
    file = forms.FileField(label="Curriculum JSON file", help_text="UTF-8 JSON file in the importer schema.")

    def clean_file(self):
        upload = self.cleaned_data["file"]
        max_bytes = getattr(settings, "CURRICULUM_IMPORT_MAX_BYTES", DEFAULT_IMPORT_MAX_BYTES)
        if not upload.name.lower().endswith(".json"):
            raise forms.ValidationError("Upload a .json file.")
        if upload.size > max_bytes:
            raise forms.ValidationError(f"File is too large (maximum {max_bytes // 1024} KB).")
        return upload


def _data_digest(data):
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@admin.register(ALevelTopic)
class ALevelTopicAdmin(admin.ModelAdmin):
    change_list_template = "admin/examquestions/aleveltopic/change_list.html"
    import_template = "admin/examquestions/aleveltopic/import_curriculum.html"
    list_display = ("topic", "subject", "exam_board", "specification")
    list_filter = ("subject", "exam_board", "specification")
    search_fields = ("topic", "subject__name", "subject__code")
    list_select_related = ("subject",)
    autocomplete_fields = ("subject",)
    ordering = ("subject__code", "exam_board", "specification", "topic")

    def get_urls(self):
        custom = [
            path(
                "import-curriculum/",
                self.admin_site.admin_view(self.import_curriculum_view),
                name="examquestions_aleveltopic_import",
            ),
        ]
        return custom + super().get_urls()

    def import_curriculum_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        changelist_url = reverse("admin:examquestions_aleveltopic_changelist")
        form = CurriculumUploadForm()
        preview = None

        if request.method == "POST":
            if request.POST.get("action") == "confirm":
                return self._confirm_import(request, changelist_url)
            form = CurriculumUploadForm(request.POST, request.FILES)
            if form.is_valid():
                preview, errors = self._build_preview(request, form.cleaned_data["file"])
                if errors:
                    for error in errors:
                        form.add_error(None, error)

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Import curriculum JSON",
            "form": form,
            "preview": preview,
            "changelist_url": changelist_url,
        }
        return TemplateResponse(request, self.import_template, context)

    def _build_preview(self, request, upload):
        max_bytes = getattr(settings, "CURRICULUM_IMPORT_MAX_BYTES", DEFAULT_IMPORT_MAX_BYTES)
        request.session.pop(PENDING_IMPORT_SESSION_KEY, None)
        try:
            data = load_and_validate(upload.read(max_bytes + 1)[:max_bytes + 1])
            counts = preview_import(data)
        except CurriculumImportError as exc:
            logger.info(
                "Curriculum import preview rejected for user %s (%d validation errors).",
                request.user.pk, len(exc.errors),
            )
            return None, _limit_errors(exc.errors or [str(exc)])
        except Exception:
            logger.exception("Unexpected curriculum import preview failure for user %s.", request.user.pk)
            return None, ["The file could not be processed."]

        token = secrets.token_urlsafe(32)
        request.session[PENDING_IMPORT_SESSION_KEY] = {
            "token": token,
            "user_id": request.user.pk,
            "created": time.time(),
            "digest": _data_digest(data),
            "data": data,
        }
        source = data.get("source") or {}
        return {
            "token": token,
            "subject": data["subject"],
            "exam_board": data["exam_board"],
            "specification": data["specification"],
            "source_name": source.get("name", ""),
            "rows": [(label, counts[label][0], counts[label][1]) for label in ("topics", "subtopics", "subcategories")],
        }, []

    def _confirm_import(self, request, changelist_url):
        upload_url = reverse("admin:examquestions_aleveltopic_import")
        # Popped first so a confirmation token can never be replayed.
        pending = request.session.pop(PENDING_IMPORT_SESSION_KEY, None)
        submitted = request.POST.get("token", "")
        ttl = getattr(settings, "CURRICULUM_IMPORT_TOKEN_TTL_SECONDS", DEFAULT_IMPORT_TOKEN_TTL_SECONDS)

        valid = (
            isinstance(pending, dict)
            and hmac.compare_digest(str(pending.get("token", "")), str(submitted))
            and pending.get("user_id") == request.user.pk
            and time.time() - pending.get("created", 0) <= ttl
            and pending.get("digest") == _data_digest(pending.get("data"))
        )
        if not valid:
            logger.warning("Rejected curriculum import confirmation for user %s.", request.user.pk)
            self.message_user(
                request, "The import confirmation is invalid or has expired. Upload the file again.", messages.ERROR
            )
            return redirect(upload_url)

        try:
            counts = apply_import(pending["data"])
        except CurriculumImportError as exc:
            logger.info("Curriculum import failed for user %s (%d validation errors).", request.user.pk, len(exc.errors))
            self.message_user(request, "; ".join(_limit_errors(exc.errors)) if exc.errors else str(exc), messages.ERROR)
            return redirect(upload_url)
        except IntegrityError:
            logger.warning("Curriculum import hit an integrity conflict for user %s.", request.user.pk)
            self.message_user(
                request, "The import conflicted with a concurrent change. Nothing was written; upload the file again.",
                messages.ERROR,
            )
            return redirect(upload_url)
        except Exception:
            logger.exception("Unexpected curriculum import failure for user %s.", request.user.pk)
            self.message_user(request, "The import failed and nothing was written.", messages.ERROR)
            return redirect(upload_url)

        summary = ", ".join(f"{counts[l][0]} {l} created ({counts[l][1]} reused)" for l in ("topics", "subtopics", "subcategories"))
        logger.info("Curriculum import applied by user %s: %s", request.user.pk, summary)
        self.message_user(request, f"Curriculum imported: {summary}.", messages.SUCCESS)
        return redirect(changelist_url)


@admin.register(ALevelSubTopic)
class ALevelSubTopicAdmin(admin.ModelAdmin):
    list_display = ("title", "topic", "subject_name", "exam_board")
    list_filter = ("topic__subject", "topic__exam_board", "topic__specification")
    search_fields = ("title", "topic__topic", "topic__subject__name", "topic__subject__code")
    list_select_related = ("topic", "topic__subject")
    autocomplete_fields = ("topic",)
    ordering = ("topic__subject__code", "topic__exam_board", "topic__specification", "topic__topic", "title")

    @admin.display(description="Subject", ordering="topic__subject__name")
    def subject_name(self, obj):
        return obj.topic.subject.name

    @admin.display(description="Exam board", ordering="topic__exam_board")
    def exam_board(self, obj):
        return obj.topic.exam_board


@admin.register(ALevelSubCategory)
class ALevelSubCategoryAdmin(admin.ModelAdmin):
    list_display = ("title", "subtopic", "parent_topic", "subject_name", "exam_board")
    list_filter = ("subtopic__topic__subject", "subtopic__topic__exam_board", "subtopic__topic__specification")
    search_fields = (
        "title",
        "subtopic__title",
        "subtopic__topic__topic",
        "subtopic__topic__subject__name",
        "subtopic__topic__subject__code",
    )
    list_select_related = ("subtopic", "subtopic__topic", "subtopic__topic__subject")
    autocomplete_fields = ("subtopic",)
    ordering = (
        "subtopic__topic__subject__code",
        "subtopic__topic__exam_board",
        "subtopic__topic__specification",
        "subtopic__topic__topic",
        "subtopic__title",
        "title",
    )

    @admin.display(description="Topic", ordering="subtopic__topic__topic")
    def parent_topic(self, obj):
        return obj.subtopic.topic.topic

    @admin.display(description="Subject", ordering="subtopic__topic__subject__name")
    def subject_name(self, obj):
        return obj.subtopic.topic.subject.name

    @admin.display(description="Exam board", ordering="subtopic__topic__exam_board")
    def exam_board(self, obj):
        return obj.subtopic.topic.exam_board

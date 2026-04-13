from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError

from examquestions.models import (
    BiologySubCategory,
    BiologySubTopic,
    BiologyTopic,
    GCSEScienceSubCategory,
    GCSEScienceSubTopic,
    GCSEScienceTopic,
)


CURRICULUM_MODELS = (
    BiologyTopic,
    BiologySubTopic,
    BiologySubCategory,
    GCSEScienceTopic,
    GCSEScienceSubTopic,
    GCSEScienceSubCategory,
)


class Command(BaseCommand):
    help = "Export curriculum hierarchy only as a Django fixture JSON file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            help="Optional path for the exported JSON fixture.",
        )
        parser.add_argument(
            "--indent",
            type=int,
            default=2,
            help="JSON indentation level. Defaults to 2.",
        )
        parser.add_argument(
            "--to-stdout",
            dest="to_stdout",
            action="store_true",
            help="Write the exported JSON to stdout instead of a file.",
        )

    def handle(self, *args, **options):
        indent = options["indent"]
        write_to_stdout = options["to_stdout"]

        if write_to_stdout and options.get("output"):
            raise CommandError("Use either --to-stdout or --output, not both.")

        output_path = None if write_to_stdout else self._resolve_output_path(options.get("output"))

        objects = []
        for model in CURRICULUM_MODELS:
            objects.extend(model.objects.order_by("pk"))

        serialized = serializers.serialize("json", objects, indent=indent)

        if write_to_stdout:
            self.stdout.write(serialized)
            return

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(serialized, encoding="utf-8")

        self.stdout.write(
            self.style.SUCCESS(
                f"Exported {len(objects)} curriculum records to {output_path}"
            )
        )

    def _resolve_output_path(self, raw_output):
        if raw_output:
            return Path(raw_output).expanduser().resolve()

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return settings.BASE_DIR / "backups" / f"curriculum-backup-{timestamp}.json"

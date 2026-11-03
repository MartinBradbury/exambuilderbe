from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from examquestions.services.curriculum_import import (
    CurriculumImportError,
    apply_import,
    load_and_validate,
    preview_import,
)


class Command(BaseCommand):
    help = (
        "Validate and import an A-level curriculum JSON file into the shared "
        "ALevelTopic/ALevelSubTopic/ALevelSubCategory models. Dry-run by default."
    )

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="Path to a UTF-8 JSON curriculum file.")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", help="Validate and report only (default).")
        mode.add_argument("--apply", action="store_true", help="Write the curriculum to the database.")

    def handle(self, *args, **options):
        path = Path(options["file"]).expanduser()
        if not path.is_file():
            raise CommandError(f"File not found: {path}")

        apply = options["apply"]
        try:
            data = load_and_validate(path.read_bytes())
            counts = apply_import(data) if apply else preview_import(data)
        except CurriculumImportError as exc:
            if exc.errors:
                raise CommandError(f"{exc}\n" + "\n".join(f"  - {e}" for e in exc.errors))
            raise CommandError(str(exc))

        mode = "Applied" if apply else "Dry run (no changes written)"
        self.stdout.write(self.style.SUCCESS(
            f"{mode}: {data['subject']} {data['exam_board']} spec={data['specification']!r}"
        ))
        for label in ("topics", "subtopics", "subcategories"):
            created, reused = counts[label]
            verb = "created" if apply else "to create"
            self.stdout.write(f"  {label}: {created} {verb}, {reused} existing reused")

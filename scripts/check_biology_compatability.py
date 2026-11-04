
from collections import Counter

from examquestions.models import (
    ALevelTopic,
    ALevelSubTopic,
    ALevelSubCategory,
    BiologyTopic,
    BiologySubTopic,
    BiologySubCategory,
)

SUBJECT_ID = 2
BOARDS = ("AQA", "OCR")


def normalise(value):
    return " ".join(str(value or "").strip().casefold().split())


def run():
    problems = []
    counts = Counter()

    for board in BOARDS:
        topics = ALevelTopic.objects.filter(
            subject_id=SUBJECT_ID,
            exam_board=board,
        ).prefetch_related("subtopics__subcategories")

        for topic in topics:
            counts["topics"] += 1

            legacy_topics = [
                item for item in BiologyTopic.objects.filter(
                    exam_board=board
                )
                if normalise(item.topic) == normalise(topic.topic)
            ]

            if len(legacy_topics) != 1:
                problems.append(
                    f"{board} topic {topic.id}: "
                    f"{topic.topic!r} -> {len(legacy_topics)} matches"
                )
                continue

            legacy_topic = legacy_topics[0]

            for subtopic in topic.subtopics.all():
                counts["subtopics"] += 1

                legacy_subtopics = [
                    item for item in BiologySubTopic.objects.filter(
                        topic=legacy_topic
                    )
                    if normalise(item.title) == normalise(subtopic.title)
                ]

                if len(legacy_subtopics) != 1:
                    problems.append(
                        f"{board} subtopic {subtopic.id}: "
                        f"{subtopic.title!r} -> "
                        f"{len(legacy_subtopics)} matches"
                    )
                    continue

                legacy_subtopic = legacy_subtopics[0]

                for subcategory in subtopic.subcategories.all():
                    counts["subcategories"] += 1

                    legacy_subcategories = [
                        item for item in BiologySubCategory.objects.filter(
                            subtopic=legacy_subtopic
                        )
                        if normalise(item.title)
                        == normalise(subcategory.title)
                    ]

                    if len(legacy_subcategories) != 1:
                        problems.append(
                            f"{board} subcategory {subcategory.id}: "
                            f"{subcategory.title!r} -> "
                            f"{len(legacy_subcategories)} matches"
                        )

    print("Topics checked:", counts["topics"])
    print("Subtopics checked:", counts["subtopics"])
    print("Subcategories checked:", counts["subcategories"])
    print("Problems:", len(problems))

    for problem in problems:
        print("PROBLEM:", problem)

    expected = (14, 65, 173)
    actual = (
        counts["topics"],
        counts["subtopics"],
        counts["subcategories"],
    )

    if actual != expected:
        print("FAIL: Unexpected curriculum counts.")
        print("Expected:", expected)
        print("Actual:", actual)
    elif problems:
        print("FAIL: Curriculum compatibility problems found.")
    else:
        print("PASS: All 252 shared selections matched legacy records.")


run()

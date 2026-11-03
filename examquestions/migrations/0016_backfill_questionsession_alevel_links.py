from django.db import migrations, transaction


SUBJECT_CODE = 'alevel_biology'
QUALIFICATION = 'ALEVEL_BIOLOGY'


class SessionBackfillError(RuntimeError):
    pass


def _single_destination(queryset, description):
    matches = list(queryset[:2])
    if not matches:
        raise SessionBackfillError(f'Missing destination curriculum record for {description}.')
    if len(matches) > 1:
        raise SessionBackfillError(f'Ambiguous destination curriculum records for {description}.')
    return matches[0]


def backfill_alevel_session_links(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    Subject = apps.get_model('accounts', 'Subject')
    QuestionSession = apps.get_model('examquestions', 'QuestionSession')
    ALevelTopic = apps.get_model('examquestions', 'ALevelTopic')
    ALevelSubTopic = apps.get_model('examquestions', 'ALevelSubTopic')
    ALevelSubCategory = apps.get_model('examquestions', 'ALevelSubCategory')

    sessions = (
        QuestionSession.objects.using(db_alias)
        .filter(qualification=QUALIFICATION)
        .select_related('topic', 'subtopic', 'subcategory', 'subcategory__subtopic')
        .order_by('pk')
    )
    if not sessions.exists():
        return

    subject = Subject.objects.using(db_alias).filter(code=SUBJECT_CODE).first()
    if subject is None:
        raise SessionBackfillError(
            f"Subject '{SUBJECT_CODE}' does not exist; refusing to create or guess a replacement."
        )

    topic_cache = {}
    subtopic_cache = {}
    subcategory_cache = {}

    with transaction.atomic(using=db_alias):
        for session in sessions.iterator():
            label = f'session {session.pk}'
            source_topic = session.topic
            source_subtopic = session.subtopic
            source_subcategory = session.subcategory

            if source_topic is None:
                if source_subtopic is not None or source_subcategory is not None:
                    raise SessionBackfillError(f'{label} has a legacy subtopic/subcategory but no topic.')
                if session.alevel_topic_id or session.alevel_subtopic_id or session.alevel_subcategory_id:
                    raise SessionBackfillError(f'{label} has A-level links but no legacy topic.')
                continue
            if source_subtopic is None and source_subcategory is not None:
                raise SessionBackfillError(f'{label} has a legacy subcategory but no subtopic.')
            if source_subtopic is not None and source_subtopic.topic_id != source_topic.pk:
                raise SessionBackfillError(f'{label} has a legacy subtopic that does not belong to its topic.')
            if source_subcategory is not None and source_subcategory.subtopic_id != source_subtopic.pk:
                raise SessionBackfillError(f'{label} has a legacy subcategory that does not belong to its subtopic.')

            if source_topic.pk not in topic_cache:
                topic_cache[source_topic.pk] = _single_destination(
                    ALevelTopic.objects.using(db_alias).filter(
                        subject=subject,
                        topic=source_topic.topic,
                        exam_board=source_topic.exam_board,
                        specification=source_topic.specification,
                    ),
                    f'topic {source_topic.pk}',
                ).pk
            expected_topic_id = topic_cache[source_topic.pk]

            expected_subtopic_id = None
            if source_subtopic is not None:
                if source_subtopic.pk not in subtopic_cache:
                    subtopic_cache[source_subtopic.pk] = _single_destination(
                        ALevelSubTopic.objects.using(db_alias).filter(
                            topic_id=expected_topic_id, title=source_subtopic.title
                        ),
                        f'subtopic {source_subtopic.pk}',
                    ).pk
                expected_subtopic_id = subtopic_cache[source_subtopic.pk]

            expected_subcategory_id = None
            if source_subcategory is not None:
                if source_subcategory.pk not in subcategory_cache:
                    subcategory_cache[source_subcategory.pk] = _single_destination(
                        ALevelSubCategory.objects.using(db_alias).filter(
                            subtopic_id=expected_subtopic_id, title=source_subcategory.title
                        ),
                        f'subcategory {source_subcategory.pk}',
                    ).pk
                expected_subcategory_id = subcategory_cache[source_subcategory.pk]

            expected = {
                'alevel_topic_id': expected_topic_id,
                'alevel_subtopic_id': expected_subtopic_id,
                'alevel_subcategory_id': expected_subcategory_id,
            }
            updates = {}
            for field, expected_id in expected.items():
                current_id = getattr(session, field)
                if current_id is None:
                    if expected_id is not None:
                        updates[field] = expected_id
                elif current_id != expected_id:
                    raise SessionBackfillError(
                        f'{label} has {field}={current_id} which conflicts with expected {expected_id}.'
                    )

            if updates:
                QuestionSession.objects.using(db_alias).filter(pk=session.pk).update(**updates)


class Migration(migrations.Migration):

    dependencies = [
        ('examquestions', '0015_questionsession_alevel_curriculum_fks'),
    ]

    operations = [
        # Reverse is a no-op: the links are additive and the legacy fields are never modified.
        migrations.RunPython(backfill_alevel_session_links, migrations.RunPython.noop),
    ]

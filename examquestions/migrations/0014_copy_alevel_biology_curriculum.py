from django.db import migrations


SUBJECT_CODE = 'alevel_biology'


class CurriculumCopyError(RuntimeError):
    pass


def _get_single(queryset, description):
    matches = list(queryset[:2])
    if len(matches) > 1:
        raise CurriculumCopyError(f'Ambiguous destination records for {description}.')
    return matches[0] if matches else None


def copy_alevel_biology_curriculum(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    Subject = apps.get_model('accounts', 'Subject')
    BiologyTopic = apps.get_model('examquestions', 'BiologyTopic')
    BiologySubTopic = apps.get_model('examquestions', 'BiologySubTopic')
    BiologySubCategory = apps.get_model('examquestions', 'BiologySubCategory')
    ALevelTopic = apps.get_model('examquestions', 'ALevelTopic')
    ALevelSubTopic = apps.get_model('examquestions', 'ALevelSubTopic')
    ALevelSubCategory = apps.get_model('examquestions', 'ALevelSubCategory')

    subject = Subject.objects.using(db_alias).filter(code=SUBJECT_CODE).first()
    if subject is None:
        raise CurriculumCopyError(
            f"Subject '{SUBJECT_CODE}' does not exist; refusing to create or guess a replacement."
        )
    if subject.qualification != 'A_LEVEL':
        raise CurriculumCopyError(f"Subject '{SUBJECT_CODE}' is not an A-level subject.")

    topic_map = {}
    for source in BiologyTopic.objects.using(db_alias).order_by('pk').iterator():
        destination = _get_single(
            ALevelTopic.objects.using(db_alias).filter(
                subject=subject,
                topic=source.topic,
                exam_board=source.exam_board,
                specification=source.specification,
            ),
            f'topic {source.pk}',
        )
        if destination is None:
            destination = ALevelTopic.objects.using(db_alias).create(
                subject=subject,
                topic=source.topic,
                exam_board=source.exam_board,
                specification=source.specification,
            )
        topic_map[source.pk] = destination.pk

    subtopic_map = {}
    for source in BiologySubTopic.objects.using(db_alias).order_by('pk').iterator():
        destination_topic_id = topic_map.get(source.topic_id)
        if destination_topic_id is None:
            raise CurriculumCopyError(f'Subtopic {source.pk} references an unmapped topic {source.topic_id}.')
        destination = _get_single(
            ALevelSubTopic.objects.using(db_alias).filter(topic_id=destination_topic_id, title=source.title),
            f'subtopic {source.pk}',
        )
        if destination is None:
            destination = ALevelSubTopic.objects.using(db_alias).create(topic_id=destination_topic_id, title=source.title)
        subtopic_map[source.pk] = destination.pk

    for source in BiologySubCategory.objects.using(db_alias).order_by('pk').iterator():
        destination_subtopic_id = subtopic_map.get(source.subtopic_id)
        if destination_subtopic_id is None:
            raise CurriculumCopyError(
                f'Subcategory {source.pk} references an unmapped subtopic {source.subtopic_id}.'
            )
        destination = _get_single(
            ALevelSubCategory.objects.using(db_alias).filter(subtopic_id=destination_subtopic_id, title=source.title),
            f'subcategory {source.pk}',
        )
        if destination is None:
            ALevelSubCategory.objects.using(db_alias).create(subtopic_id=destination_subtopic_id, title=source.title)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_seed_initial_subjects'),
        ('examquestions', '0013_alevel_curriculum_models'),
    ]

    operations = [
        # Reverse is a no-op so rolling back never deletes curriculum rows that may be referenced later.
        migrations.RunPython(copy_alevel_biology_curriculum, migrations.RunPython.noop),
    ]

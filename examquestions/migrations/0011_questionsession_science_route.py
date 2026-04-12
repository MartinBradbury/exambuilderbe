from django.db import migrations, models


def populate_science_route(apps, schema_editor):
    QuestionSession = apps.get_model('examquestions', 'QuestionSession')

    QuestionSession.objects.filter(
        qualification='GCSE_SCIENCE',
        gcse_subject='COMBINED',
    ).update(science_route='combined')

    QuestionSession.objects.filter(
        qualification='GCSE_SCIENCE',
    ).exclude(gcse_subject='COMBINED').update(science_route='separate')


def clear_science_route(apps, schema_editor):
    QuestionSession = apps.get_model('examquestions', 'QuestionSession')
    QuestionSession.objects.update(science_route='')


class Migration(migrations.Migration):

    dependencies = [
        ('examquestions', '0010_alter_gcsesciencesubcategory_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='questionsession',
            name='science_route',
            field=models.CharField(
                blank=True,
                choices=[('combined', 'Combined'), ('separate', 'Separate')],
                max_length=16,
            ),
        ),
        migrations.RunPython(populate_science_route, clear_science_route),
    ]

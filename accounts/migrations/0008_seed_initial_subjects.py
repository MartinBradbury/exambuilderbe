from django.db import migrations


INITIAL_SUBJECTS = [
    ('gcse_science', 'Science', 'GCSE'),
    ('alevel_biology', 'Biology', 'A_LEVEL'),
    ('alevel_chemistry', 'Chemistry', 'A_LEVEL'),
    ('alevel_physics', 'Physics', 'A_LEVEL'),
]


def seed_subjects(apps, schema_editor):
    Subject = apps.get_model('accounts', 'Subject')
    for code, name, qualification in INITIAL_SUBJECTS:
        Subject.objects.get_or_create(
            code=code,
            defaults={'name': name, 'qualification': qualification, 'is_active': True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_subject_and_subjectentitlement'),
    ]

    operations = [
        # Reverse is a no-op so rolling back never deletes subjects that may already be referenced.
        migrations.RunPython(seed_subjects, migrations.RunPython.noop),
    ]

from django.db import migrations
from django.db.models import Exists, OuterRef, Q


def backfill_subject_entitlements(apps, schema_editor):
    database = schema_editor.connection.alias
    CustomUser = apps.get_model('accounts', 'CustomUser')
    UserEntitlement = apps.get_model('accounts', 'UserEntitlement')
    Subject = apps.get_model('accounts', 'Subject')
    SubjectEntitlement = apps.get_model('accounts', 'SubjectEntitlement')

    lifetime_entitlements = UserEntitlement.objects.using(database).filter(
        user_id=OuterRef('pk'),
    ).filter(
        Q(lifetime_unlocked=True) | Q(plan_type='lifetime')
    )

    subjects = dict(
        Subject.objects.using(database)
        .filter(code__in=('gcse_science', 'alevel_biology'))
        .values_list('code', 'pk')
    )

    entitlements = []
    users = (
        CustomUser.objects.using(database)
        .annotate(is_lifetime=Exists(lifetime_entitlements))
        .filter(
            Q(has_gcse_paid_access=True)
            | Q(has_alevel_paid_access=True)
            | Q(is_lifetime=True)
        )
        .values_list('pk', 'has_gcse_paid_access', 'has_alevel_paid_access', 'is_lifetime')
        .iterator(chunk_size=1000)
    )

    for user_id, has_gcse_access, has_alevel_access, is_lifetime in users:
        if has_gcse_access or is_lifetime:
            entitlements.append(
                SubjectEntitlement(
                    user_id=user_id,
                    subject_id=subjects['gcse_science'],
                )
            )
        if has_alevel_access or is_lifetime:
            entitlements.append(
                SubjectEntitlement(
                    user_id=user_id,
                    subject_id=subjects['alevel_biology'],
                )
            )

        if len(entitlements) >= 1000:
            SubjectEntitlement.objects.using(database).bulk_create(
                entitlements,
                ignore_conflicts=True,
                batch_size=1000,
            )
            entitlements.clear()

    if entitlements:
        SubjectEntitlement.objects.using(database).bulk_create(
            entitlements,
            ignore_conflicts=True,
            batch_size=1000,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_seed_initial_subjects'),
    ]

    operations = [
        migrations.RunPython(
            backfill_subject_entitlements,
            migrations.RunPython.noop,
        ),
    ]

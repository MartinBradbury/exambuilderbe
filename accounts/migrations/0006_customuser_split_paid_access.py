from django.db import migrations, models
from django.db.models import Q


def backfill_split_paid_access(apps, schema_editor):
    CustomUser = apps.get_model('accounts', 'CustomUser')
    UserEntitlement = apps.get_model('accounts', 'UserEntitlement')

    previously_paid_user_ids = UserEntitlement.objects.filter(
        Q(plan_type__in=['paid', 'lifetime']) | Q(lifetime_unlocked=True)
    ).values_list('user_id', flat=True)

    CustomUser.objects.filter(id__in=previously_paid_user_ids).update(
        has_gcse_paid_access=True,
        has_alevel_paid_access=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_customuser_performance_tracking_start_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='customuser',
            name='has_alevel_paid_access',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='customuser',
            name='has_gcse_paid_access',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(backfill_split_paid_access, migrations.RunPython.noop),
    ]
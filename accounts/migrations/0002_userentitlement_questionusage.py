from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def create_default_entitlements(apps, schema_editor):
    CustomUser = apps.get_model('accounts', 'CustomUser')
    UserEntitlement = apps.get_model('accounts', 'UserEntitlement')

    entitled_user_ids = set(
        UserEntitlement.objects.values_list('user_id', flat=True)
    )
    UserEntitlement.objects.bulk_create(
        [
            UserEntitlement(user_id=user_id)
            for user_id in CustomUser.objects.exclude(id__in=entitled_user_ids).values_list('id', flat=True)
        ],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserEntitlement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('plan_type', models.CharField(choices=[('free', 'Free'), ('lifetime', 'Lifetime')], default='free', max_length=20)),
                ('lifetime_unlocked', models.BooleanField(default=False)),
                ('stripe_customer_id', models.CharField(blank=True, max_length=255, null=True)),
                ('stripe_checkout_session_id', models.CharField(blank=True, max_length=255, null=True)),
                ('paid_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='entitlement', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='QuestionUsage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(default=django.utils.timezone.localdate)),
                ('question_count', models.PositiveIntegerField(default=0)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='question_usages', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-date'],
            },
        ),
        migrations.AddConstraint(
            model_name='questionusage',
            constraint=models.UniqueConstraint(fields=('user', 'date'), name='uniq_question_usage_per_user_per_day'),
        ),
        migrations.RunPython(
            code=create_default_entitlements,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
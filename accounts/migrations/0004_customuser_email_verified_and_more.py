from django.db import migrations, models
from django.utils import timezone


def mark_existing_users_verified(apps, schema_editor):
    CustomUser = apps.get_model('accounts', 'CustomUser')
    CustomUser.objects.filter(email_verified=False).update(
        email_verified=True,
        email_verified_at=timezone.now(),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_userentitlement_paid_and_subscription'),
    ]

    operations = [
        migrations.AddField(
            model_name='customuser',
            name='email_verified',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='customuser',
            name='email_verified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(mark_existing_users_verified, migrations.RunPython.noop),
    ]
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_userentitlement_questionusage'),
    ]

    operations = [
        migrations.AddField(
            model_name='userentitlement',
            name='stripe_subscription_id',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AlterField(
            model_name='userentitlement',
            name='plan_type',
            field=models.CharField(choices=[('free', 'Free'), ('paid', 'Paid'), ('lifetime', 'Lifetime')], default='free', max_length=20),
        ),
    ]
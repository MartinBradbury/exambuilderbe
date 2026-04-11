from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_customuser_email_verified_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='customuser',
            name='performance_tracking_start_date',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
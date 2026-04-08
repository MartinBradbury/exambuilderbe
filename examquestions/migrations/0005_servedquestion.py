from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_customuser_email_verified_and_more"),
        ("examquestions", "0004_alter_biologysubcategory_options_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ServedQuestion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("exam_board", models.CharField(choices=[("OCR", "OCR"), ("AQA", "AQA")], db_index=True, max_length=8)),
                ("scope_key", models.CharField(db_index=True, max_length=255)),
                ("normalized_question", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="served_questions", to="accounts.customuser")),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="servedquestion",
            constraint=models.UniqueConstraint(fields=("user", "exam_board", "scope_key", "normalized_question"), name="uniq_served_question_per_user_scope"),
        ),
    ]
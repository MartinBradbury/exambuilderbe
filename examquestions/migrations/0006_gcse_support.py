from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("examquestions", "0005_servedquestion"),
    ]

    operations = [
        migrations.CreateModel(
            name="GCSEScienceTopic",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("topic", models.CharField(max_length=255)),
                ("exam_board", models.CharField(choices=[("OCR", "OCR"), ("AQA", "AQA")], db_index=True, default="OCR", max_length=8)),
                ("subject", models.CharField(choices=[("BIOLOGY", "Biology"), ("CHEMISTRY", "Chemistry"), ("PHYSICS", "Physics")], db_index=True, max_length=16)),
            ],
            options={
                "ordering": ["exam_board", "subject", "topic"],
            },
        ),
        migrations.AddField(
            model_name="questionsession",
            name="gcse_subject",
            field=models.CharField(blank=True, choices=[("BIOLOGY", "Biology"), ("CHEMISTRY", "Chemistry"), ("PHYSICS", "Physics")], max_length=16),
        ),
        migrations.AddField(
            model_name="questionsession",
            name="gcse_tier",
            field=models.CharField(blank=True, choices=[("FOUNDATION", "Foundation"), ("HIGHER", "Higher")], max_length=16),
        ),
        migrations.AddField(
            model_name="questionsession",
            name="gcse_topic",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="examquestions.gcsesciencetopic"),
        ),
        migrations.AddField(
            model_name="questionsession",
            name="qualification",
            field=models.CharField(choices=[("ALEVEL_BIOLOGY", "A-level Biology"), ("GCSE_SCIENCE", "GCSE Science")], default="ALEVEL_BIOLOGY", max_length=32),
        ),
        migrations.AlterField(
            model_name="questionsession",
            name="topic",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to="examquestions.biologytopic"),
        ),
        migrations.AddConstraint(
            model_name="gcsesciencetopic",
            constraint=models.UniqueConstraint(fields=("topic", "exam_board", "subject"), name="uniq_gcse_topic_per_board_subject"),
        ),
    ]
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("examquestions", "0006_gcse_support"),
    ]

    operations = [
        migrations.CreateModel(
            name="GCSEScienceSubTopic",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("topic", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="subtopics", to="examquestions.gcsesciencetopic")),
            ],
            options={
                "ordering": ["topic__exam_board", "topic__subject", "topic__topic", "title"],
            },
        ),
        migrations.CreateModel(
            name="GCSEScienceSubCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("subtopic", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="subcategories", to="examquestions.gcsesciencesubtopic")),
            ],
            options={
                "ordering": ["subtopic__topic__exam_board", "subtopic__topic__subject", "subtopic__topic__topic", "subtopic__title", "title"],
            },
        ),
        migrations.AddField(
            model_name="questionsession",
            name="gcse_subcategory",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="examquestions.gcsesciencesubcategory"),
        ),
        migrations.AddField(
            model_name="questionsession",
            name="gcse_subtopic",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="examquestions.gcsesciencesubtopic"),
        ),
        migrations.AddConstraint(
            model_name="gcsesciencesubtopic",
            constraint=models.UniqueConstraint(fields=("topic", "title"), name="uniq_gcse_subtopic_per_topic"),
        ),
        migrations.AddConstraint(
            model_name="gcsesciencesubcategory",
            constraint=models.UniqueConstraint(fields=("subtopic", "title"), name="uniq_gcse_subcategory_per_subtopic"),
        ),
    ]
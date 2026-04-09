from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("examquestions", "0007_gcse_subtopic_subcategory"),
    ]

    operations = [
        migrations.AddField(
            model_name="gcsesciencetopic",
            name="tier",
            field=models.CharField(choices=[("FOUNDATION", "Foundation"), ("HIGHER", "Higher")], db_index=True, default="HIGHER", max_length=16),
        ),
        migrations.RemoveConstraint(
            model_name="gcsesciencetopic",
            name="uniq_gcse_topic_per_board_subject",
        ),
        migrations.AddConstraint(
            model_name="gcsesciencetopic",
            constraint=models.UniqueConstraint(fields=("topic", "exam_board", "subject", "tier"), name="uniq_gcse_topic_per_board_subject"),
        ),
    ]
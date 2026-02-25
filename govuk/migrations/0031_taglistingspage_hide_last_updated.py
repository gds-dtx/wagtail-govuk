from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("govuk", "0030_eddsakeypair_algorithm"),
    ]

    operations = [
        migrations.AddField(
            model_name="taglistingspage",
            name="hide_last_updated",
            field=models.BooleanField(
                default=False,
                help_text="Hide the last updated date below each listing.",
                verbose_name="Hide last updated",
            ),
        ),
    ]

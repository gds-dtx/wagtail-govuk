import django.core.validators
from django.db import migrations, models

# GOV.UK's palette, so cards tell existing types apart before anyone chooses.
PALETTE = ["#1d70b8", "#0f7a52", "#54319f", "#f47738", "#158187", "#ca357c", "#ca3535", "#ffdd00", "#858686"]


def spread_colours(apps, schema_editor):
    CatalogueType = apps.get_model("govuk", "CatalogueType")
    types = list(CatalogueType.objects.order_by("sort_order", "label"))
    for number, entry_type in enumerate(types):
        entry_type.colour = PALETTE[number % len(PALETTE)]
    CatalogueType.objects.bulk_update(types, ["colour"])


class Migration(migrations.Migration):

    dependencies = [
        ("govuk", "0102_search_scope"),
    ]

    operations = [
        migrations.AddField(
            model_name="cataloguetype",
            name="colour",
            field=models.CharField(
                default="#1d70b8",
                help_text="Of the bar along the top of its cards. Its tag is a darker and a lighter shade of it.",
                max_length=7,
                validators=[
                    django.core.validators.RegexValidator(
                        "^#[0-9a-fA-F]{6}$", "Enter a colour as # and six hex digits, such as #1d70b8."
                    )
                ],
            ),
        ),
        migrations.RunPython(spread_colours, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='contententryexternalpage',
            name='primary',
            field=models.BooleanField(default=False, help_text='Listed first in its section, with a starred corner on its card.', verbose_name='Primary'),
        ),
        migrations.AlterField(
            model_name='contententrygenericpage',
            name='primary',
            field=models.BooleanField(default=False, help_text='Listed first in its section, with a starred corner on its card.', verbose_name='Primary'),
        ),
        migrations.AlterField(
            model_name='contententryguidepage',
            name='primary',
            field=models.BooleanField(default=False, help_text='Listed first in its section, with a starred corner on its card.', verbose_name='Primary'),
        ),
        migrations.AlterField(
            model_name='contententrypolicypage',
            name='primary',
            field=models.BooleanField(default=False, help_text='Listed first in its section, with a starred corner on its card.', verbose_name='Primary'),
        ),
        migrations.AlterField(
            model_name='contententryrequirementspage',
            name='primary',
            field=models.BooleanField(default=False, help_text='Listed first in its section, with a starred corner on its card.', verbose_name='Primary'),
        ),
        migrations.AlterField(
            model_name='contententrystepspage',
            name='primary',
            field=models.BooleanField(default=False, help_text='Listed first in its section, with a starred corner on its card.', verbose_name='Primary'),
        ),
        migrations.AlterField(
            model_name='contententrytoolpage',
            name='primary',
            field=models.BooleanField(default=False, help_text='Listed first in its section, with a starred corner on its card.', verbose_name='Primary'),
        ),
        migrations.AddField(
            model_name='contentmainpage',
            name='all_content_location',
            field=models.CharField(choices=[('all_page', 'On a page of its own: .../this-page/all/'), ('this_page', 'At the bottom of this page')], default='all_page', help_text='Every entry, with its filters.', max_length=10, verbose_name='Where all content is listed'),
        ),
    ]

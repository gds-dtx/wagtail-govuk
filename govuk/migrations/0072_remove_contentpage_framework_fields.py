"""Drop the framework fields from ContentPage.

Plain content pages no longer carry the framework switches or the welcome
content -- those moved to the two framework page types. This runs after
``0069``, which has already copied every framework page's values onto its new
type, so nothing is lost by dropping the columns here.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("govuk", "0071_delete_rolepages"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="contentpage",
            name="framework_welcome_body",
        ),
        migrations.RemoveField(
            model_name="contentpage",
            name="show_framework_updates",
        ),
        migrations.RemoveField(
            model_name="contentpage",
            name="show_framework_welcome",
        ),
        migrations.RemoveField(
            model_name="contentpage",
            name="show_role_navigation",
        ),
    ]

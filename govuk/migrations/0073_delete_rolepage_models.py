"""Remove the RolePage and RolePageTag models.

The pages are gone (``0070``), so the tables can go too. The tag model's
foreign keys are dropped before the models, the order Django's own autodetector
produced, so nothing references a table as it is deleted.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("govuk", "0072_remove_contentpage_framework_fields"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="rolepagetag",
            name="content_object",
        ),
        migrations.RemoveField(
            model_name="rolepagetag",
            name="tag",
        ),
        migrations.DeleteModel(
            name="RolePage",
        ),
        migrations.DeleteModel(
            name="RolePageTag",
        ),
    ]

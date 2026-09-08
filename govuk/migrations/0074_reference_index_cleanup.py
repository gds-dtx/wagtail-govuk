"""Clear the reference-index rows the deleted role pages left behind.

A role page's role chooser recorded a reference to its ``GovukRole`` in the
reference index. Deleting the pages (``0070``) removed the pages but not those
rows, because the reference index has no foreign key back to the page. They are
keyed by the specific content type, so the role-page rows can be removed on
their own without touching any other page's references.

Guarded so a replay on a database that never had the reference index, or an
older schema, is a no-op rather than an error -- the same care ``0061`` takes.

After this migration, run ``manage.py rebuild_references_index`` once so the
converted framework pages' own references are re-recorded under their new
content types. Nothing depends on that being done -- the index only drives the
admin's "usage" view -- so it is left as an operational step rather than run
here against models this migration would have to import.
"""

from django.db import migrations


def clear_rolepage_references(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    db_alias = schema_editor.connection.alias

    try:
        ReferenceIndex = apps.get_model("wagtailcore", "ReferenceIndex")
    except LookupError:
        return

    rolepage_ct = (
        ContentType.objects.using(db_alias)
        .filter(app_label="govuk", model="rolepage")
        .first()
    )
    if rolepage_ct is None:
        return

    ReferenceIndex.objects.using(db_alias).filter(
        content_type_id=rolepage_ct.id
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("govuk", "0073_delete_rolepage_models"),
        ("wagtailcore", "0078_referenceindex"),
    ]

    operations = [
        migrations.RunPython(clear_rolepage_references, migrations.RunPython.noop),
    ]

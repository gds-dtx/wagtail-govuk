"""Delete the role pages.

Roles are served as routes on the framework main page now, from the
``GovukRole`` snippets, so the hand-made role pages have no part left to play.
Their content was only ever the snippet's, so nothing authored is lost; any
bespoke body or hero typed onto a role page is discarded with it.

Deleting a multi-table page cleanly takes two steps. The tag rows and the
``govuk_rolepage`` child rows go first -- deleting the base page rows on their
own would leave those child rows pointing at a page that is gone. The base page
rows then go through the real page queryset, whose treebeard delete removes each
page's subtree and mends the parents' child counts; it works at the base
``Page`` level, so it does not need the ``RolePage`` model this branch has
already removed from the code. The reference-index rows the role choosers left
behind are cleared in ``0073``.
"""

from django.db import migrations


def delete_rolepages(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    RolePage = apps.get_model("govuk", "RolePage")
    RolePageTag = apps.get_model("govuk", "RolePageTag")
    db_alias = schema_editor.connection.alias

    rolepage_ct = (
        ContentType.objects.using(db_alias)
        .filter(app_label="govuk", model="rolepage")
        .first()
    )
    if rolepage_ct is None:
        return

    # The real page model, not the historical one: its queryset delete is
    # treebeard-aware, and the historical model's is not.
    from wagtail.models import Page as WagtailPage

    role_ids = list(
        WagtailPage.objects.using(db_alias)
        .filter(content_type_id=rolepage_ct.id)
        .values_list("id", flat=True)
    )
    if not role_ids:
        return

    RolePageTag.objects.using(db_alias).filter(
        content_object_id__in=role_ids
    ).delete()

    qn = schema_editor.connection.ops.quote_name
    child_table = RolePage._meta.db_table
    placeholders = ", ".join(["%s"] * len(role_ids))
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM {qn(child_table)} WHERE {qn('page_ptr_id')} IN ({placeholders})",
            role_ids,
        )

    WagtailPage.objects.using(db_alias).filter(id__in=role_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("govuk", "0070_convert_framework_contentpages"),
    ]

    operations = [
        migrations.RunPython(delete_rolepages, migrations.RunPython.noop),
    ]

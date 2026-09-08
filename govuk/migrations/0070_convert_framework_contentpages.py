"""Convert the framework's content pages to the new page types.

Every ``ContentPage`` that switched on any framework option becomes a framework
page: the shallowest one the single ``FrameworkMainPage`` (the framework root
that serves the role routes), the rest ``FrameworkContentPage`` children of it.
Plain content pages -- those with none of the switches on -- are left as
``ContentPage``.

Follows the project's page-type conversion pattern (see
``0005_convert_homepage_to_sectionpage``): swap the page's content type and copy
its type-specific columns into the new child table, the ``wagtailcore_page`` row
itself unchanged. The framework columns still exist on ``ContentPage`` here --
``0071`` drops them, after this has copied their values across.
"""

from django.db import migrations


def _framework_columns(ContentPage):
    """The child-table columns shared by ContentPage and the framework types.

    Both the source (ContentPage, before 0071 drops the framework fields) and
    the targets carry the identical set of concrete columns, so one list drives
    both the read and the write.
    """
    return [field.column for field in ContentPage._meta.local_fields]


def _convert_page(cursor, qn, *, page_id, source_table, target_table, columns):
    """Move one page's type-specific row from ContentPage to a framework table.

    The ``wagtailcore_page`` row keeps its id, path and slug; only its content
    type and its child-table row change, which is what a page-type change is.
    """
    col_sql = ", ".join(qn(column) for column in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    cursor.execute(
        f"SELECT {col_sql} FROM {qn(source_table)} WHERE {qn('page_ptr_id')} = %s",
        [page_id],
    )
    row = cursor.fetchone()
    if row is None:
        return
    cursor.execute(
        f"INSERT INTO {qn(target_table)} ({col_sql}) VALUES ({placeholders})",
        list(row),
    )
    cursor.execute(
        f"DELETE FROM {qn(source_table)} WHERE {qn('page_ptr_id')} = %s",
        [page_id],
    )


def _migrate_tags(*, page_id, SourceTag, TargetTag):
    """Carry a converted page's tags onto its new tag model."""
    tag_ids = list(
        SourceTag.objects.filter(content_object_id=page_id).values_list(
            "tag_id", flat=True
        )
    )
    for tag_id in tag_ids:
        TargetTag.objects.create(content_object_id=page_id, tag_id=tag_id)
    SourceTag.objects.filter(content_object_id=page_id).delete()


def convert_framework_contentpages(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Page = apps.get_model("wagtailcore", "Page")
    ContentPage = apps.get_model("govuk", "ContentPage")
    FrameworkMainPage = apps.get_model("govuk", "FrameworkMainPage")
    FrameworkContentPage = apps.get_model("govuk", "FrameworkContentPage")
    ContentPageTag = apps.get_model("govuk", "ContentPageTag")
    FrameworkMainPageTag = apps.get_model("govuk", "FrameworkMainPageTag")
    FrameworkContentPageTag = apps.get_model("govuk", "FrameworkContentPageTag")

    db_alias = schema_editor.connection.alias

    framework_page_ids = list(
        ContentPage.objects.using(db_alias)
        .filter(
            models_show_any_framework_switch()
        )
        .values_list("page_ptr_id", flat=True)
    )
    if not framework_page_ids:
        return

    # Shallowest first, ties broken by tree position, so the pick is stable.
    ordered = list(
        Page.objects.using(db_alias)
        .filter(id__in=framework_page_ids)
        .order_by("depth", "path")
        .values_list("id", "path")
    )
    main_page_id = ordered[0][0]
    content_page_ids = [page_id for page_id, _path in ordered[1:]]

    contentpage_ct = ContentType.objects.using(db_alias).get(
        app_label="govuk", model="contentpage"
    )
    main_ct, _ = ContentType.objects.using(db_alias).get_or_create(
        app_label="govuk", model="frameworkmainpage"
    )
    content_ct, _ = ContentType.objects.using(db_alias).get_or_create(
        app_label="govuk", model="frameworkcontentpage"
    )

    columns = _framework_columns(ContentPage)
    source_table = ContentPage._meta.db_table
    main_table = FrameworkMainPage._meta.db_table
    content_table = FrameworkContentPage._meta.db_table
    qn = schema_editor.connection.ops.quote_name

    with schema_editor.connection.cursor() as cursor:
        _convert_page(
            cursor,
            qn,
            page_id=main_page_id,
            source_table=source_table,
            target_table=main_table,
            columns=columns,
        )
        for page_id in content_page_ids:
            _convert_page(
                cursor,
                qn,
                page_id=page_id,
                source_table=source_table,
                target_table=content_table,
                columns=columns,
            )

    Page.objects.using(db_alias).filter(id=main_page_id).update(
        content_type_id=main_ct.id
    )
    Page.objects.using(db_alias).filter(id__in=content_page_ids).update(
        content_type_id=content_ct.id
    )

    _migrate_tags(
        page_id=main_page_id,
        SourceTag=ContentPageTag,
        TargetTag=FrameworkMainPageTag,
    )
    for page_id in content_page_ids:
        _migrate_tags(
            page_id=page_id,
            SourceTag=ContentPageTag,
            TargetTag=FrameworkContentPageTag,
        )

    _reparent_under_main(db_alias, main_page_id, content_page_ids)


def models_show_any_framework_switch():
    from django.db.models import Q

    return (
        Q(show_role_navigation=True)
        | Q(show_framework_updates=True)
        | Q(show_framework_welcome=True)
    )


def _reparent_under_main(db_alias, main_page_id, content_page_ids):
    """Move any framework content page not already under the main page beneath it.

    The framework's pages normally sit under the page that becomes the main
    page, so this usually moves nothing. A stray one elsewhere in the tree is
    moved with the real page model's treebeard ``move`` so the paths stay
    consistent; the child-table swap above does not touch the tree.
    """
    from wagtail.models import Page as WagtailPage

    main_page = WagtailPage.objects.using(db_alias).filter(id=main_page_id).first()
    if main_page is None:
        return
    main_path = main_page.path
    for page_id in content_page_ids:
        page = WagtailPage.objects.using(db_alias).filter(id=page_id).first()
        if page is None or page.path.startswith(main_path):
            continue
        page.move(main_page, pos="last-child")


class Migration(migrations.Migration):

    dependencies = [
        ("govuk", "0069_frameworkcontentpage_frameworkmainpage_and_more"),
    ]

    operations = [
        migrations.RunPython(
            convert_framework_contentpages, migrations.RunPython.noop
        ),
    ]

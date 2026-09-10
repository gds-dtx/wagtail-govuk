"""Second half of the page-type reshaping: remove what 0071 replaced.

Split from 0071 because Postgres refuses to ALTER or DROP a table that has
pending trigger events in the same transaction. 0071 deletes rows from
govuk_contentpage and govuk_rolepage (each referenced by a tag table through a
deferrable foreign key, so the deletes queue deferred constraint checks), and
the RemoveField / DeleteModel operations below alter and drop exactly those
tables. In one migration -- one transaction -- Postgres raises "cannot ALTER
TABLE ... because it has pending trigger events" at the first RemoveField, and
the container does not start. SQLite has no such check, which is why the
tests passed. Two migrations are two transactions: 0071 commits its data
changes, then this one changes the schema.
"""

from django.db import migrations, models


def clear_stale_contentpage_references(apps, schema_editor):
    """Drop reference-index rows that point at ContentPage.framework_welcome_body.

    This migration removes that field from ContentPage.  Any reference-index row
    still typed as ContentPage with a framework_welcome_body path — from a page
    that carried welcome-body content but was not converted, or one re-indexing
    did not cover — raises FieldDoesNotExist when Wagtail resolves it (e.g. on
    the page-delete confirmation).  Correct rows for the converted pages are now
    typed FrameworkMainPage/FrameworkContentPage and are left untouched;
    ``rebuild_references_index`` restores anything dropped here.
    """
    ContentType = apps.get_model("contenttypes", "ContentType")
    db_alias = schema_editor.connection.alias

    try:
        ReferenceIndex = apps.get_model("wagtailcore", "ReferenceIndex")
    except LookupError:
        return

    contentpage_ct = (
        ContentType.objects.using(db_alias)
        .filter(app_label="govuk", model="contentpage")
        .first()
    )
    if contentpage_ct is None:
        return

    ReferenceIndex.objects.using(db_alias).filter(
        content_type_id=contentpage_ct.id,
        model_path__startswith="framework_welcome_body",
    ).delete()


def carry_header_settings_forward(apps, schema_editor):
    """Set each site's three header dropdowns from the two tick boxes they replace.

    Before this migration a site's header layout was two booleans on
    CustomiseSettings: ``show_service_name_in_navigation`` moved the service
    name *and* the search box from the header bar into the service navigation
    bar, and ``hide_sign_in_link`` hid the sign-in link. Now it is three
    choices. Adding the choices with their defaults and dropping the booleans
    would give every existing site the default layout -- name and search in
    the header bar, sign-in link showing -- whatever it showed the day before,
    and the Capability Framework's development site shows the live service's
    layout (everything in the navigation bar, no sign-in link) precisely
    because an editor set the booleans. So the booleans are read first.

    A site whose row was created after the booleans existed but never saved
    has both False, and lands on header/header/navigation, which is what the
    template drew for that row before. A database without the booleans'
    values -- one created on a codebase that never had them -- has no rows to
    carry and keeps the defaults, which reproduce that codebase's header.

    The two hero colour fields are dropped by this migration as well. They
    were not inoperable: ``render_custom_css`` wrote them into ``/gen/custom.css``
    as ``.masthead { background: ... }`` and ``.masthead { color: ... }`` (with
    ``.hero__description``), and a test held it to that. A site that set them
    keeps exactly that CSS, prepended to its ``extra_css``, which the same view
    still serves.
    """
    CustomiseSettings = apps.get_model("govuk", "CustomiseSettings")
    db_alias = schema_editor.connection.alias
    for row in CustomiseSettings.objects.using(db_alias).all():
        in_navigation = bool(row.show_service_name_in_navigation)
        row.service_name_location = "navigation" if in_navigation else "header"
        row.search_location = "navigation" if in_navigation else "header"
        row.sign_in_location = "hidden" if row.hide_sign_in_link else "navigation"
        update_fields = ["service_name_location", "search_location", "sign_in_location"]

        hero_css = []
        background = (row.hero_background_color or "").strip()
        text = (row.hero_text_color or "").strip()
        if background:
            hero_css.append(f".masthead {{ background: {background}; }}")
        if text:
            hero_css.append(f".masthead {{ color: {text}; }}")
            hero_css.append(f".hero__description {{ color: {text}; }}")
        if hero_css:
            extra_css = (row.extra_css or "").strip()
            row.extra_css = "\n".join(hero_css + ([extra_css] if extra_css else []))
            update_fields.append("extra_css")

        row.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ('govuk', '0071_pagetypes_setting_reorg_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='contentpage',
            name='framework_welcome_body',
        ),
        migrations.RemoveField(
            model_name='contentpage',
            name='show_framework_updates',
        ),
        migrations.RemoveField(
            model_name='contentpage',
            name='show_framework_welcome',
        ),
        migrations.RemoveField(
            model_name='contentpage',
            name='show_in_role_navigation',
        ),
        migrations.RemoveField(
            model_name='contentpage',
            name='show_role_navigation',
        ),
        # The three dropdowns replace two tick boxes, and the two hero colour
        # fields go. Add the dropdowns, carry each site's choices across (the
        # colours into extra_css), then drop the old fields -- in that order,
        # so an existing site looks the same after the deploy as before it.
        migrations.AddField(
            model_name='customisesettings',
            name='search_location',
            field=models.CharField(choices=[('header', 'Header bar'), ('navigation', 'Service navigation'), ('hidden', 'Hidden')], default='header', help_text='Where the search box appears.', max_length=20),
        ),
        migrations.AddField(
            model_name='customisesettings',
            name='service_name_location',
            field=models.CharField(choices=[('header', 'Header bar'), ('navigation', 'Service navigation')], default='header', help_text='Where the service name appears.', max_length=20),
        ),
        migrations.AddField(
            model_name='customisesettings',
            name='sign_in_location',
            field=models.CharField(choices=[('header', 'Header bar'), ('navigation', 'Service navigation'), ('hidden', 'Hidden')], default='navigation', help_text='Where the sign in and sign out links appear. Choose Hidden for sites where visitors never sign in -- the sign in link is hidden, but a signed-in user can still sign out.', max_length=20),
        ),
        migrations.RunPython(
            carry_header_settings_forward,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name='customisesettings',
            name='hide_sign_in_link',
        ),
        migrations.RemoveField(
            model_name='customisesettings',
            name='show_service_name_in_navigation',
        ),
        migrations.RemoveField(
            model_name='customisesettings',
            name='hero_background_color',
        ),
        migrations.RemoveField(
            model_name='customisesettings',
            name='hero_text_color',
        ),
        # RolePageTag fields removed after delete_rolepages has used them.
        migrations.RemoveField(
            model_name='rolepagetag',
            name='content_object',
        ),
        migrations.RemoveField(
            model_name='rolepagetag',
            name='tag',
        ),
        migrations.DeleteModel(
            name='RolePage',
        ),
        migrations.DeleteModel(
            name='RolePageTag',
        ),
        # Field is gone now; drop any reference-index rows that still point at
        # ContentPage.framework_welcome_body so the admin can resolve references.
        migrations.RunPython(
            clear_stale_contentpage_references,
            migrations.RunPython.noop,
        ),
    ]

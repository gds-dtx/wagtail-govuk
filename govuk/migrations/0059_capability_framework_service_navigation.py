from django.db import migrations


def enable_service_navigation(apps, schema_editor):
    """Move the site name and search into the service navigation bar.

    The Design System puts a service's name in the service navigation, leaving
    the header for the GOV.UK logo. Only sites that publish framework role
    pages are switched over, so the other sites this code base serves keep the
    header they were set up with.
    """
    Site = apps.get_model("wagtailcore", "Site")
    Page = apps.get_model("wagtailcore", "Page")
    ContentType = apps.get_model("contenttypes", "ContentType")
    CustomiseSettings = apps.get_model("govuk", "CustomiseSettings")

    role_page_type = ContentType.objects.filter(
        app_label="govuk", model="rolepage"
    ).first()
    if role_page_type is None:
        return

    for site in Site.objects.all():
        root = Page.objects.filter(pk=site.root_page_id).first()
        if root is None:
            continue
        has_role_pages = (
            Page.objects.filter(
                content_type_id=role_page_type.pk,
                path__startswith=root.path,
                live=True,
            )
            .exclude(pk=root.pk)
            .exists()
        )
        if not has_role_pages:
            continue

        settings, _ = CustomiseSettings.objects.get_or_create(site_id=site.pk)
        if not settings.show_service_name_in_navigation:
            settings.show_service_name_in_navigation = True
            settings.save(update_fields=["show_service_name_in_navigation"])


class Migration(migrations.Migration):

    dependencies = [
        ("govuk", "0058_repair_changelog_notes"),
        ("wagtailcore", "0094_alter_page_locale"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(enable_service_navigation, migrations.RunPython.noop),
    ]

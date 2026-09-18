from django.db import migrations

from govuk.capability_framework import repair_changelog_html


def repair_notes(apps, schema_editor):
    """Rewrite change notes imported before the exports' bullets were read.

    The published exports mark bullets with a leading hyphen. Notes imported
    before that was understood hold a paragraph per hyphen line, and a few
    carry a literal "[](/skills)" where the export had a link with no text.
    """
    GovukChangelogEntry = apps.get_model("govuk", "GovukChangelogEntry")
    for entry in GovukChangelogEntry.objects.all().iterator():
        repaired = repair_changelog_html(entry.note)
        if repaired != entry.note:
            entry.note = repaired
            entry.save(update_fields=["note"])


class Migration(migrations.Migration):

    dependencies = [
        ("govuk", "0057_contentpage_framework_welcome_body"),
    ]

    # The repair reads the stored HTML and rewrites the same field, so there is
    # nothing to undo beyond leaving the corrected markup in place.
    operations = [
        migrations.RunPython(repair_notes, migrations.RunPython.noop),
    ]

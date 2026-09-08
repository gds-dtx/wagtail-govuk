"""Export Capability Framework content as the published CSV downloads.

Produces the three CSVs the framework publishes for reuse by departments
(roles, skills and change notes), generated from the content held in
Wagtail so the download stays in step with what is published.

Also useful as a migration check: exporting and diffing against the source
files shows exactly what survived an import.

The rows themselves live in ``govuk.framework_csv``, shared with the
download views so the file on disk and the file a reader downloads cannot
disagree.

Usage:
    python manage.py export_capability_framework /path/to/output-dir
"""

from pathlib import Path

from django.core.management.base import BaseCommand

from govuk.framework_csv import write_changelog_csv, write_roles_csv, write_skills_csv


class Command(BaseCommand):
    help = "Export Capability Framework content to the published CSV formats"

    def add_arguments(self, parser):
        parser.add_argument("output_dir", help="Directory to write the CSV files to")

    def handle(self, *args, **options):
        output_dir = Path(options["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        for name, write in (
            ("roles.csv", write_roles_csv),
            ("skills.csv", write_skills_csv),
            ("changelog.csv", write_changelog_csv),
        ):
            path = output_dir / name
            with open(path, "w", newline="") as f:
                rows = write(f)
            self.stdout.write(f"{name}: {rows} rows -> {path}")

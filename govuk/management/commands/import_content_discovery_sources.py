from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from govuk.content_discovery.csv_import import (
    ContentDiscoverySourceImportError,
    import_content_discovery_sources_from_csv,
)


class Command(BaseCommand):
    help = (
        "Import Content discovery sources from CSV and create or update rows by "
        "(site_id, url)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_path",
            help="Path to a CSV file. Required columns: url and either site_id column or --site-id.",
        )
        parser.add_argument(
            "--site-id",
            type=int,
            default=None,
            help="Default Wagtail site ID for rows that do not provide site_id.",
        )
        parser.add_argument(
            "--delimiter",
            default=",",
            help="CSV delimiter (default: ',').",
        )

    def handle(self, *args, **options):
        csv_path = Path(options["csv_path"])
        if not csv_path.exists() or not csv_path.is_file():
            raise CommandError(f"CSV file does not exist: {csv_path}")

        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                result = import_content_discovery_sources_from_csv(
                    csv_file,
                    default_site_id=options["site_id"],
                    delimiter=options["delimiter"],
                )
        except ContentDiscoverySourceImportError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Imported content discovery sources: "
                f"processed {result.processed} row(s), "
                f"created {result.created}, "
                f"updated {result.updated}, "
                f"unchanged {result.unchanged}, "
                f"skipped empty {result.skipped_empty}."
            )
        )

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from govuk.catalogue.bundle import GuidanceSyncError
from govuk.catalogue.import_catalogue import (
    files_from_directory,
    files_from_github,
    import_catalogue,
)
from govuk.features import catalogue_enabled
from govuk.models import ContentMainPage


class Command(BaseCommand):
    """Re-runnable; ``--update`` overwrites pages from the source, losing editors' changes."""

    help = "Import a content catalogue (a folder or owner/name on GitHub) beneath a content main page."

    def add_arguments(self, parser):
        parser.add_argument("main_page_id", type=int, help="The content main page's id.")
        parser.add_argument("source", help="A catalogue folder, or a GitHub repo as owner/name.")
        parser.add_argument("--branch", default="", help="The GitHub repo's branch (default main).")
        parser.add_argument(
            "--draft",
            action="store_true",
            help="Save what is imported as drafts, to publish from the admin.",
        )
        parser.add_argument(
            "--update",
            action="store_true",
            help="Rewrite pages the catalogue already has from the source.",
        )

    def handle(self, *args, **options):
        if not catalogue_enabled():
            raise CommandError("The content catalogue is off (FEATURE_CATALOGUE).")
        main_page = ContentMainPage.objects.filter(pk=options["main_page_id"]).first()
        if main_page is None:
            raise CommandError(f"No content main page has id {options['main_page_id']}.")

        source = options["source"]
        try:
            if Path(source).is_dir():
                files, repository = files_from_directory(source), None
            else:
                files, repository = files_from_github(source, branch=options["branch"])
            result = import_catalogue(
                main_page,
                files,
                repository=repository,
                publish=not options["draft"],
                update_existing=options["update"],
            )
        except GuidanceSyncError as exc:
            raise CommandError(str(exc)) from exc

        for warning in result.warnings:
            self.stdout.write(self.style.WARNING(f"- {warning}"))
        self.stdout.write(self.style.SUCCESS(result.summary()))

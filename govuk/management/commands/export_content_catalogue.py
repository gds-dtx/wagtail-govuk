from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from govuk.catalogue.export_catalogue import export_catalogue
from govuk.models import ContentMainPage


class Command(BaseCommand):
    """Write a content main page's catalogue out in the catalogue repo's format.

    The folder it writes can be checked with the catalogue repo's
    ``scripts/validate.py`` and imported again with ``import_content_catalogue``.
    What does not survive the trip is listed in ``govuk/catalogue/export_catalogue.py``.
    """

    help = "Export a content main page's catalogue to a folder, in the catalogue repo's format."

    def add_arguments(self, parser):
        parser.add_argument("main_page_id", type=int, help="The content main page's id.")
        parser.add_argument("out_dir", help="The folder to write; made if it does not exist.")

    def handle(self, *args, **options):
        if not settings.FEATURE_FLAGS.get("GUIDANCE"):
            raise CommandError("The content catalogue is off (FEATURE_GUIDANCE).")
        main_page = ContentMainPage.objects.filter(pk=options["main_page_id"]).first()
        if main_page is None:
            raise CommandError(f"No content main page has id {options['main_page_id']}.")
        out_dir = Path(options["out_dir"])
        if out_dir.exists() and any(out_dir.iterdir()):
            raise CommandError(f"{out_dir} is not empty; export into a new folder.")

        result = export_catalogue(main_page)
        result.write(out_dir)
        for warning in result.warnings:
            self.stdout.write(self.style.WARNING(f"- {warning}"))
        self.stdout.write(
            self.style.SUCCESS(f"Exported {result.entries} entries ({len(result.files)} files) to {out_dir}.")
        )

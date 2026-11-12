"""Fetch the source page of every external catalogue entry into the content index.

Run on a timer, beside ``rebuild_content_index``. A source that has not
changed answers 304 and costs next to nothing; one that refuses or cannot be
reached keeps the text last fetched, and is reported without failing the run,
since a few always will. Only an unexpected error fails it.
"""

from django.core.management.base import BaseCommand, CommandError

from govuk.content_index import content_index_enabled
from govuk.content_index.harvest import harvest_all
from govuk.models import HarvestedSource, catalogue_enabled


class Command(BaseCommand):
    help = "Fetch external catalogue entries' source pages into the content index."

    def add_arguments(self, parser):
        parser.add_argument("--url", action="append", help="Fetch only this source. Repeatable.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Fetch every page in full, not only those that say they have changed.",
        )

    def handle(self, *args, **options):
        if not content_index_enabled():
            raise CommandError("The content index is off (FEATURE_MCP).")
        if not catalogue_enabled():
            raise CommandError("The content catalogue is off (FEATURE_GUIDANCE).")
        result = harvest_all(force=options["force"], urls=options["url"])
        for line in result.lines:
            self.stdout.write(line)
        outcomes = [*HarvestedSource.Status.values, "removed", "error"]
        self.stdout.write(", ".join(f"{result.counts[outcome]} {outcome}" for outcome in outcomes) + ".")
        if result.counts["error"]:
            raise CommandError(f"{result.counts['error']} sources failed unexpectedly.")

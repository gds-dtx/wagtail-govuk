"""Fetch every external catalogue entry's source page into the content index.

Refused or unreachable sources are reported but do not fail the run, since a few always will.
"""

from django.core.management.base import BaseCommand, CommandError

from govuk.content_index import INDEX_OFF
from govuk.content_index.harvest import harvest_all
from govuk.features import content_index_enabled
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
            raise CommandError(INDEX_OFF)
        if not catalogue_enabled():
            raise CommandError("The content catalogue is off (FEATURE_CATALOGUE).")
        result = harvest_all(force=options["force"], urls=options["url"])
        for line in result.lines:
            self.stdout.write(line)
        outcomes = [*HarvestedSource.Status.values, "removed", "error"]
        self.stdout.write(", ".join(f"{result.counts[outcome]} {outcome}" for outcome in outcomes) + ".")
        if result.counts["error"]:
            raise CommandError(f"{result.counts['error']} sources failed unexpectedly.")

"""Rebuild the content index from every page, role and skill, then embed new text.

Run on a timer to catch changes no publish signals (renamed tags, flag changes, imports).
"""

from django.core.management.base import BaseCommand, CommandError

from govuk.content_index import INDEX_OFF, embeddings
from govuk.content_index.indexer import OUTCOMES, REBUILD_SCOPES, rebuild
from govuk.features import content_index_enabled

from .embed_content_index import report as report_embeddings


class Command(BaseCommand):
    help = "Rebuild the content index."

    def add_arguments(self, parser):
        parser.add_argument(
            "--only",
            choices=REBUILD_SCOPES,
            help="Rebuild only pages, roles or skills.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Rewrite every row, including those whose source has not changed.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change, and write nothing.",
        )
        parser.add_argument(
            "--no-embed",
            action="store_true",
            help="Leave new and changed text without a vector (see embed_content_index).",
        )

    def handle(self, *args, **options):
        if not content_index_enabled():
            raise CommandError(INDEX_OFF)
        result = rebuild(only=options["only"], force=options["force"], dry_run=options["dry_run"])
        for failure in result.failures:
            self.stderr.write(failure)
        summary = ", ".join(f"{result.counts[outcome]} {outcome}" for outcome in OUTCOMES)
        prefix = "Dry run, nothing written: " if options["dry_run"] else ""
        self.stdout.write(f"{prefix}{summary}.")
        embedded = None
        if not options["no_embed"] and not options["dry_run"] and embeddings.get_embedder() is not None:
            embedded = embeddings.embed_missing()
            report_embeddings(self, embedded)
        if result.failures:
            raise CommandError(f"{len(result.failures)} items could not be indexed.")
        if embedded and embedded.counts["failed"]:
            raise CommandError(f"{embedded.counts['failed']} texts could not be embedded.")

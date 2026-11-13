"""Embed what the content index has no vector for yet, and remove unused vectors.

Run at the end of ``rebuild_content_index`` (nightly, deployed), so needs
running alone only after a change of embedding model, or to catch up
without a rebuild. Cheap when nothing has changed: one read of the index.
"""

from django.core.management.base import BaseCommand, CommandError

from govuk.content_index import FLAGS_OFF, content_index_enabled, embeddings


def report(command, result, *, dry_run: bool = False) -> None:
    for failure in dict.fromkeys(result.failures):
        command.stderr.write(failure)
    names = {"embedded": "to embed" if dry_run else "embedded", "removed": "to remove" if dry_run else "removed"}
    summary = ", ".join(f"{result.counts[outcome]} {names.get(outcome, outcome)}" for outcome in embeddings.EMBED_OUTCOMES)
    command.stdout.write(f"{'Dry run, nothing written: ' if dry_run else ''}Embeddings: {summary}.")
    if result.counts["stale"]:
        command.stderr.write(
            f"{result.counts['stale']} rows record the hash of other text: run rebuild_content_index --force."
        )


class Command(BaseCommand):
    help = "Embed the content index's new and changed text, so search can match it by meaning."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Count what would change, and write nothing.")

    def handle(self, *args, **options):
        if not content_index_enabled():
            raise CommandError(FLAGS_OFF)
        if embeddings.get_embedder() is None:
            raise CommandError("Embeddings are off (CONTENT_INDEX_EMBEDDINGS).")
        result = embeddings.embed_missing(dry_run=options["dry_run"])
        report(self, result, dry_run=options["dry_run"])
        if result.counts["failed"]:
            raise CommandError(f"{result.counts['failed']} texts could not be embedded.")

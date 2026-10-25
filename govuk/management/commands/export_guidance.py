from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from govuk.guidance.export import export_snapshot
from govuk.models import GuidanceCollectionPage


class Command(BaseCommand):
    help = (
        "Write a synced guidance collection page out as a guidance-as-code "
        "manifest repo (guidance.yml plus Markdown), whatever format it was "
        "synced from."
    )

    def add_arguments(self, parser):
        parser.add_argument("page_id", type=int, help="The guidance collection page ID.")
        parser.add_argument("out_dir", help="Directory to write to. Must be empty or new.")

    def handle(self, *args, **options):
        page = GuidanceCollectionPage.objects.filter(pk=options["page_id"]).first()
        if page is None:
            raise CommandError(f"No guidance collection page with ID {options['page_id']}.")
        snapshot = page.snapshot
        if snapshot is None or snapshot.synced_at is None:
            raise CommandError(f"'{page.title}' has not been synced yet.")

        out_dir = Path(options["out_dir"])
        if out_dir.exists() and any(out_dir.iterdir()):
            raise CommandError(f"{out_dir} is not empty.")
        out_dir.mkdir(parents=True, exist_ok=True)

        count = export_snapshot(snapshot, out_dir)
        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {count} pages and guidance.yml to {out_dir} "
                f"(from {page.repository_url} at {snapshot.commit_sha[:7]})."
            )
        )

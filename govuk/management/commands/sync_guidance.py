from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from govuk.guidance.bundle import GuidanceSyncError
from govuk.guidance.sync import sync_guidance_page
from govuk.models import GuidanceCollectionPage


class Command(BaseCommand):
    help = (
        "Sync guidance collection pages from their GitHub repositories. Pages "
        "whose branch has not moved since the last sync are left as they are."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--page-id",
            action="append",
            type=int,
            dest="page_ids",
            help="Limit the sync to one or more guidance collection page IDs.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-render even where the repository has not changed.",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=30.0,
            help="HTTP timeout in seconds for each request to GitHub (default: 30).",
        )

    def handle(self, *args, **options):
        if not settings.FEATURE_FLAGS.get("GUIDANCE"):
            raise CommandError("Guidance is not enabled here (FEATURE_GUIDANCE).")

        pages = GuidanceCollectionPage.objects.order_by("path")
        page_ids = options.get("page_ids") or []
        # Every live page by default; a named page is synced even as a draft,
        # so it can be checked before it is published.
        pages = pages.filter(id__in=page_ids) if page_ids else pages.live()
        page_list = list(pages)
        if not page_list:
            raise CommandError("No guidance collection pages matched.")

        failures: list[str] = []
        for page in page_list:
            self.stdout.write(f"Syncing page {page.pk}: {page.title} ({page.repository_url})")
            try:
                result = sync_guidance_page(
                    page, force=options["force"], timeout=options["timeout"]
                )
            except GuidanceSyncError as exc:
                failures.append(f"{page.pk} ({page.title}): {exc}")
                self.stderr.write(self.style.ERROR(f"  Failed: {exc}"))
                continue
            self.stdout.write(self.style.SUCCESS(f"  {result.summary()}"))
            for warning in result.warnings:
                self.stdout.write(self.style.WARNING(f"  warning: {warning}"))

        summary = f"Synced {len(page_list) - len(failures)} of {len(page_list)} pages."
        if failures:
            self.stderr.write(self.style.ERROR(summary))
            raise CommandError(f"{len(failures)} page(s) failed to sync.")
        self.stdout.write(self.style.SUCCESS(summary))

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from govuk.models import PageUsefulnessVote


class Command(BaseCommand):
    """Delete old "Is this page useful?" votes so the table cannot grow forever.

    Each answer is one ``PageUsefulnessVote`` row. The votes are individually
    low-value once counted, so they need not be kept indefinitely. This deletes
    rows older than ``--days`` (default 365); the "Page usefulness" report then
    reflects only the retained window. Run it on a schedule.
    """

    help = "Delete PageUsefulnessVote rows older than the given number of days."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=365,
            help="Delete votes older than this many days (default 365).",
        )

    def handle(self, *args, **options):
        days = options["days"]
        cutoff = timezone.now() - timedelta(days=days)
        deleted, _ = PageUsefulnessVote.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {deleted} page usefulness vote(s) older than {days} day(s)."
            )
        )

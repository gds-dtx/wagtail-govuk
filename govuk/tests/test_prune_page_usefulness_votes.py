"""The prune_page_usefulness_votes command keeps the votes table bounded."""

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from wagtail.models import Site

from govuk.models import PageUsefulnessVote


class PrunePageUsefulnessVotesTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)

    def _vote(self, days_ago):
        vote = PageUsefulnessVote.objects.create(
            answer="yes", path="/job-grades/", site=self.site
        )
        PageUsefulnessVote.objects.filter(pk=vote.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago)
        )
        return vote

    def test_old_votes_are_deleted_and_recent_ones_kept(self):
        old = self._vote(days_ago=400)
        recent = self._vote(days_ago=10)

        call_command("prune_page_usefulness_votes", "--days", "365", stdout=StringIO())

        self.assertFalse(PageUsefulnessVote.objects.filter(pk=old.pk).exists())
        self.assertTrue(PageUsefulnessVote.objects.filter(pk=recent.pk).exists())

    def test_days_zero_clears_everything(self):
        self._vote(days_ago=1)

        call_command("prune_page_usefulness_votes", "--days", "0", stdout=StringIO())

        self.assertEqual(PageUsefulnessVote.objects.count(), 0)

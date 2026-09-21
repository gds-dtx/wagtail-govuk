"""The admin "Page usefulness" report over stored PageUsefulnessVote rows.

Checks that it aggregates yes/no per path (pooled across sites), that the date
filter narrows the counts, that it exports, that its menu item sits at the top
of the Reports menu, and that it needs admin access.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from wagtail import hooks
from wagtail.models import Site

from govuk.models import PageUsefulnessVote


class PageUsefulnessReportTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.other_site = Site.objects.create(
            hostname="other.example.gov.uk",
            port=443,
            root_page=self.site.root_page,
        )
        self.url = reverse("govuk_page_usefulness_report")
        self.admin_user = get_user_model().objects.create_superuser(
            username="admin-user",
            email="admin@example.gov.uk",
            password="unused-password",
        )
        self.client.force_login(self.admin_user)

    def _vote(self, answer, path, site=None, created_at=None):
        vote = PageUsefulnessVote.objects.create(
            answer=answer, path=path, site=site or self.site
        )
        if created_at is not None:
            PageUsefulnessVote.objects.filter(pk=vote.pk).update(created_at=created_at)
        return vote

    def test_counts_are_grouped_by_path_and_pooled_across_sites(self):
        self._vote("yes", "/job-grades/", site=self.site)
        self._vote("yes", "/job-grades/", site=self.other_site)
        self._vote("no", "/job-grades/", site=self.site)
        self._vote("no", "/pay/", site=self.site)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        rows = {row["path"]: row for row in response.context["object_list"]}
        self.assertEqual(rows["/job-grades/"]["yes_count"], 2)
        self.assertEqual(rows["/job-grades/"]["no_count"], 1)
        self.assertEqual(rows["/job-grades/"]["total"], 3)
        self.assertEqual(rows["/pay/"]["yes_count"], 0)
        self.assertEqual(rows["/pay/"]["no_count"], 1)

    def test_the_date_filter_narrows_the_counts(self):
        now = timezone.now()
        self._vote("yes", "/job-grades/", created_at=now - timedelta(days=10))
        self._vote("yes", "/job-grades/", created_at=now - timedelta(days=1))

        since = (now - timedelta(days=3)).date().isoformat()
        response = self.client.get(self.url, {"created_at_from": since})

        rows = {row["path"]: row for row in response.context["object_list"]}
        self.assertEqual(rows["/job-grades/"]["total"], 1)

    def test_it_exports_to_csv(self):
        self._vote("yes", "/job-grades/")

        response = self.client.get(self.url, {"export": "csv"})

        self.assertEqual(response.status_code, 200)
        body = b"".join(response.streaming_content).decode()
        self.assertIn("/job-grades/", body)

    def test_the_menu_item_is_at_the_top_of_the_reports_menu(self):
        items = []
        for fn in hooks.get_hooks("register_reports_menu_item"):
            items.append(fn())
        ours = next(item for item in items if item.name == "page-usefulness")
        self.assertEqual(ours.url, self.url)
        # Built-in reports start at order 700, so ours must sort before them.
        self.assertLess(ours.order, 700)

    def test_it_requires_admin_access(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertNotEqual(response.status_code, 200)

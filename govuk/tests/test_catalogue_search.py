"""Finding the content catalogue in the site's search: its type as a badge, after the site's own pages."""

from django.test import RequestFactory, TestCase, override_settings
from wagtail.models import Site

from govuk.models import ContentPage, ContentRequirementPage, catalogue_type_labels
from govuk.search_backend import search_backend
from govuk.tests.catalogue_helpers import (
    guidance_flags,
    import_fixture,
    make_content_main_page,
)


@override_settings(FEATURE_FLAGS=guidance_flags())
class CatalogueSearchTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        root = self.site.root_page.specific
        import_fixture(make_content_main_page(root))
        # One of the site's own pages, matching "standard" less well than the
        # catalogue's entries do: only in its summary, not its title.
        self.page = root.add_child(
            instance=ContentPage(
                title="Writing for the site",
                slug="writing",
                search_description="How to meet our standard for plain English.",
            )
        )
        self.page.save_revision().publish()

    def _suggest(self, q):
        response = self.client.get("/search/suggest/", {"q": q})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_entries_and_their_pages_are_labelled_with_their_type(self):
        point = ContentRequirementPage.objects.get(slug="first-point")
        labels = catalogue_type_labels([point.get_parent().specific, point, self.page])
        self.assertEqual(labels, {point.get_parent().pk: "Standard", point.pk: "Standard"})

        items = self._suggest("standard")
        self.assertIn({"text": "Another standard", "link": "/content/standards-and-policies/external-standard/", "type": "Standard"}, items)
        items = self._suggest("first point")
        self.assertEqual(items[0]["text"], "The first point")
        self.assertEqual(items[0]["type"], "Standard")

    def test_the_sites_own_pages_come_before_the_catalogues(self):
        request = RequestFactory().get("/search/")
        ranked = [
            item.title
            for item in search_backend.search(
                "standard", {"request": request, "site": self.site, "live": True, "public": True}
            ).paginator.object_list
        ]
        # On relevance alone an entry outranks the page...
        self.assertLess(ranked.index("Another standard"), ranked.index("Writing for the site"))

        items = self._suggest("standard")
        kinds = [item["type"] for item in items]
        # ...but the box lists the site's own pages first, the group page
        # among them, then the catalogue's entries in their relevance order.
        self.assertEqual(kinds[: kinds.count("Page")], ["Page"] * kinds.count("Page"))
        texts = [item["text"] for item in items]
        self.assertIn("Writing for the site", texts[: kinds.count("Page")])
        self.assertIn("Standards and policies", texts[: kinds.count("Page")])
        entries = [title for title in ranked if title in texts[kinds.count("Page") :]]
        self.assertEqual(texts[kinds.count("Page") :], entries)

    def test_the_results_page_badges_entries_with_their_type(self):
        response = self.client.get("/search/", {"query": "another standard"})
        self.assertContains(
            response, '<strong class="govuk-tag govuk-tag--purple">Standard</strong>', html=False
        )

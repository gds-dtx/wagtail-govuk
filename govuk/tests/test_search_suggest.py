"""The header search box suggests as the reader types -- CS32-3458.

The live service's box is an autocomplete fed by a JSON list of roles and
skills; its specification (17 August 2026) asks for the same here, with the
supporting pages added, ranked roles, then skills, then pages, each linking
where the results page would send the reader. These tests are the endpoint
that feeds it, and that the box in every page's header knows where it is.
"""

from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import (
    ContentDiscoverySettings,
    ContentDiscoverySource,
    ContentPage,
    ExternalContentItem,
    FrameworkSkillsPage,
    GovukRole,
    GovukSkill,
)
from govuk.tests.framework_helpers import make_framework_main_page, role_url


def _feature_flags(*, skills_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
class SearchSuggestTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.main_page = make_framework_main_page(self.root_page)

        self.role = GovukRole.objects.create(
            title="Data analyst",
            family="Data",
            body="<p>A data analyst collects, manages and shares data.</p>",
        )
        self.skill = GovukSkill.objects.create(
            title="Data visualisation",
            body="<p>Presenting data so it can be understood.</p>",
        )
        self.skills_page = self.main_page.add_child(
            instance=FrameworkSkillsPage(title="Skills A to Z", slug="skills")
        )
        self.skills_page.save_revision().publish()
        self.page = self.root_page.add_child(
            instance=ContentPage(
                title="Data protection guidance",
                slug="data-protection",
                body="<p>How the framework handles personal data.</p>",
            )
        )
        self.page.save_revision().publish()

    def _suggest(self, **params):
        response = self.client.get("/search/suggest/", params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        return response, response.json()

    def test_roles_then_skills_then_pages(self):
        _response, items = self._suggest(q="data")

        kinds = [item["type"] for item in items]
        self.assertIn("Role", kinds)
        self.assertIn("Skill", kinds)
        self.assertIn("Page", kinds)
        order = {"Role": 0, "Skill": 1, "Page": 2}
        self.assertEqual(kinds, sorted(kinds, key=order.__getitem__))

    def test_each_kind_links_where_the_results_page_would(self):
        _response, items = self._suggest(q="data")
        by_text = {item["text"]: item for item in items}

        self.assertEqual(by_text["Data analyst"]["link"], role_url(self.main_page, self.role))
        self.assertEqual(
            by_text["Data visualisation"]["link"], f"{self.skills_page.url}#{self.skill.slug}"
        )
        self.assertEqual(by_text["Data protection guidance"]["link"], self.page.url)
        self.assertEqual(by_text["Data protection guidance"]["type"], "Page")

    def test_the_shape_is_the_one_the_autocomplete_consumes(self):
        _response, items = self._suggest(q="data")

        for item in items:
            self.assertEqual(set(item), {"text", "link", "type"})

    def test_fewer_than_two_characters_suggests_nothing(self):
        for params in ({"q": "d"}, {"q": " "}, {}):
            _response, items = self._suggest(**params)
            self.assertEqual(items, [], params)

    def test_the_results_pages_parameter_name_works_too(self):
        _response, items = self._suggest(query="data analyst")

        self.assertEqual(items[0]["text"], "Data analyst")

    def test_no_more_than_ten(self):
        for index in range(12):
            GovukRole.objects.create(title=f"Data role {index}", family="Data")

        _response, items = self._suggest(q="data")

        self.assertEqual(len(items), 10)
        # All ten are roles: the cap falls on the lowest-ranked kind first.
        self.assertEqual({item["type"] for item in items}, {"Role"})

    def test_external_content_is_not_suggested(self):
        source = ContentDiscoverySource.objects.create(
            settings=ContentDiscoverySettings.for_site(self.site),
            sort_order=0,
            name="Elsewhere",
            url="https://example.gov.uk/feed.xml",
        )
        ExternalContentItem.objects.create(
            source=source,
            url="https://example.gov.uk/data-elsewhere",
            title="Data guidance elsewhere",
            hidden=False,
        )

        _response, items = self._suggest(q="data")

        self.assertNotIn("Data guidance elsewhere", [item["text"] for item in items])

    def test_nothing_on_the_path_may_keep_a_copy(self):
        response, _items = self._suggest(q="data")

        self.assertEqual(response["Cache-Control"], "no-store")

    def test_only_get_and_head(self):
        self.assertEqual(self.client.post("/search/suggest/", {"q": "data"}).status_code, 405)
        self.assertEqual(self.client.head("/search/suggest/?q=data").status_code, 200)

    def test_the_header_box_says_where_the_suggestions_are(self):
        response = self.client.get(self.main_page.url)

        self.assertContains(response, 'data-suggest-url="/search/suggest/"')
        self.assertContains(response, 'data-min-length="2"')
        self.assertContains(response, "accessible-autocomplete-3.0.1.min.js")

    def test_the_library_is_served_from_this_site(self):
        """The content security policy allows scripts from here only."""
        self.assertIsNotNone(finders.find("accessible-autocomplete-3.0.1.min.js"))


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
class SearchSuggestWithoutTheFrameworkTests(TestCase):
    def test_only_pages_are_suggested(self):
        site = Site.objects.get(is_default_site=True)
        page = site.root_page.add_child(
            instance=ContentPage(title="Data guidance", slug="data-guidance", body="<p>Data.</p>")
        )
        page.save_revision().publish()
        GovukRole.objects.create(title="Data analyst", family="Data")
        GovukSkill.objects.create(title="Data visualisation")

        items = self.client.get("/search/suggest/", {"q": "data"}).json()

        self.assertEqual([item["type"] for item in items], ["Page"])
        self.assertEqual(items[0]["text"], "Data guidance")

"""Searching the content index, and finding a document by its address."""

from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.content_index import indexer
from govuk.content_index.search import (
    MAX_QUERY_LENGTH,
    SNIPPET_CHARS,
    SearchError,
    find_document,
    search_documents,
)
from govuk.models import (
    ContentEntryExternalPage,
    ContentEntryPolicyPage,
    ContentPage,
    FrameworkSkillsPage,
    GovukSkill,
    TagListingsPage,
)
from govuk.tests.catalogue_helpers import import_fixture, make_content_main_page
from govuk.tests.framework_helpers import make_framework_main_page
from govuk.tests.page_helpers import text_body

FLAGS = {"SKILLS": True, "FEEDBACK": False, "GUIDANCE": True, "MCP": True}


@override_settings(FEATURE_FLAGS=FLAGS)
class SearchTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        home = self.site.root_page
        self.accessibility = self._page(
            home,
            "Accessibility",
            "<p>Everyone should be able to use a service.</p>"
            "<h2>Testing</h2><p>Test with assistive technology users.</p>"
            "<h2>Audits</h2><p>Book an audit before testing in public.</p>",
            search_description="Making services work for everyone.",
        )
        self.hosting = self._page(home, "Hosting", "<p>Use the cloud first. Testing environments too.</p>")
        self.listing = home.add_child(instance=TagListingsPage(title="Procurement frameworks", slug="procurement"))
        self.listing.save_revision().publish()
        indexer.rebuild()

    def _page(self, parent, title, html, **fields):
        page = parent.add_child(
            instance=ContentPage(title=title, slug=title.lower(), body=text_body(html), **fields)
        )
        page.save_revision().publish()
        return page

    def test_a_section_is_found_by_its_text(self):
        hits = search_documents("assistive technology")
        self.assertEqual([hit.document.page_id for hit in hits], [self.accessibility.pk])
        self.assertEqual(hits[0].url, self.site.root_url + "/accessibility/")
        self.assertEqual(hits[0].section.heading_path, "Testing")
        self.assertIn("assistive technology", hits[0].snippet)

    def test_words_split_between_the_title_and_a_section_still_match(self):
        hits = search_documents("hosting cloud")
        self.assertEqual([hit.document.page_id for hit in hits], [self.hosting.pk])

    def test_one_result_per_document_with_its_other_matching_sections(self):
        hits = search_documents("testing")
        pages = [hit.document.page_id for hit in hits]
        self.assertEqual(len(pages), len(set(pages)))
        accessibility = next(hit for hit in hits if hit.document.page_id == self.accessibility.pk)
        headings = {accessibility.section.heading_path, *(s.heading_path for s in accessibility.other_sections)}
        self.assertEqual(headings, {"Testing", "Audits"})

    def test_a_page_with_no_text_is_found_by_its_title(self):
        hits = search_documents("procurement")
        self.assertEqual([hit.document.page_id for hit in hits], [self.listing.pk])
        self.assertIsNone(hits[0].section)

    def test_an_exact_title_comes_first(self):
        self.assertEqual(search_documents("accessibility")[0].document.page_id, self.accessibility.pk)

    def test_the_filters_and_the_limit(self):
        self.assertEqual(search_documents("testing", kind="role"), [])
        self.assertEqual(search_documents("testing", origin="external"), [])
        self.assertEqual(len(search_documents("testing", limit=1)), 1)
        self.assertEqual(len(search_documents("testing", limit=500)), 2)  # capped, not refused

    def test_a_long_section_is_shown_around_the_match(self):
        filler = " ".join(["Background."] * 80)
        page = self._page(self.site.root_page, "Records", f"<p>{filler} Retention schedules apply. {filler}</p>")
        indexer.index_page(page)
        snippet = search_documents("retention schedules")[0].snippet
        self.assertIn("Retention schedules apply.", snippet)
        self.assertTrue(snippet.startswith("… ") and snippet.endswith(" …"))
        self.assertLessEqual(len(snippet), SNIPPET_CHARS + 4)

    def test_queries_that_cannot_be_run(self):
        for query in ("", "   ", "x" * (MAX_QUERY_LENGTH + 1)):
            with self.subTest(query=query[:10]), self.assertRaises(SearchError) as raised:
                search_documents(query)
            self.assertNotIn(query.strip() or "never", str(raised.exception))


@override_settings(FEATURE_FLAGS=FLAGS)
class FindDocumentTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.main = make_content_main_page(self.site.root_page)
        import_fixture(self.main)
        self.framework = make_framework_main_page(self.site.root_page)
        self.skills_page = self.framework.add_child(instance=FrameworkSkillsPage(title="Skills", slug="skills"))
        self.skills_page.save_revision().publish()
        self.skill = GovukSkill.objects.create(title="Forensics", body="<p>Evidence.</p>")
        indexer.rebuild()
        self.policy = ContentEntryPolicyPage.objects.get(slug="hosted-policy")
        self.policy_path = self.policy.get_url_parts()[2]

    def test_a_full_address_a_path_and_a_path_without_its_slash(self):
        for url in (
            self.site.root_url + self.policy_path,
            self.policy_path,
            self.policy_path.rstrip("/"),
            self.policy_path.lstrip("/"),
        ):
            with self.subTest(url=url):
                document, fragment = find_document(url)
                self.assertEqual(document.page_id, self.policy.pk)
                self.assertEqual(fragment, "")

    def test_another_port_on_the_same_host_matches(self):
        url = f"http://{self.site.hostname}:8000{self.policy_path}"
        self.assertEqual(find_document(url)[0].page_id, self.policy.pk)

    def test_a_fragment_names_a_section_of_the_page(self):
        document, fragment = find_document(self.policy_path + "#policy-statement")
        self.assertEqual(document.page_id, self.policy.pk)
        self.assertEqual(fragment, "policy-statement")

    def test_a_skill_is_found_by_its_place_in_the_a_to_z(self):
        document, fragment = find_document(self.skills_page.get_url_parts()[2] + "#" + self.skill.slug)
        self.assertEqual(document.skill_id, self.skill.pk)
        self.assertEqual(fragment, "")

    def test_an_external_entry_is_found_by_its_source(self):
        external = ContentEntryExternalPage.objects.get(slug="external-policy")
        document, _ = find_document("https://www.gov.uk/guidance/external-policy/")
        self.assertEqual(document.page_id, external.pk)

    def test_nothing_is_found_elsewhere(self):
        self.assertIsNone(find_document("https://elsewhere.example" + self.policy_path))
        self.assertIsNone(find_document("/no/such/page/"))
        self.assertIsNone(find_document(""))

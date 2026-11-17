"""Ask: hybrid search over the content index, the /ask/ page and the question box."""

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils.text import slugify
from wagtail.models import Site

from govuk.content_index import embeddings, evaluation as ev, indexer
from govuk.content_index.embeddings import FakeEmbedder, embed_missing
from govuk.content_index.search import (
    HYBRID,
    KEYWORD,
    SEMANTIC_OFF,
    SEMANTIC_OK,
    SEMANTIC_UNAVAILABLE,
    SearchHit,
    is_close,
    search,
)
from govuk.mcp import tools
from govuk.models import ContentPage
from govuk.tests.catalogue_helpers import import_fixture, make_content_main_page
from govuk.tests.page_helpers import text_body

FLAGS = {"SKILLS": False, "FEEDBACK": False, "GUIDANCE": True, "MCP": True, "ASK": True}
CONCEPTS = {"money": "pay", "payments": "pay", "payment": "pay", "card": "pay", "public": "people", "people": "people"}


def publish(title: str, html: str, parent=None) -> ContentPage:
    parent = parent or Site.objects.get(is_default_site=True).root_page
    page = parent.add_child(
        instance=ContentPage(title=title, slug=slugify(title), body=text_body(html))
    )
    page.save_revision().publish()
    return page


class WithFakeModel(TestCase):
    """The index of a few pages, embedded by a fake model that knows ``CONCEPTS``."""

    def setUp(self):
        cache.clear()
        self.fake = FakeEmbedder(concepts=CONCEPTS)
        patcher = mock.patch.object(embeddings, "_fake", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pay = publish("GOV.UK Pay", "<h2>Taking payments</h2><p>Take card payments online.</p>")
        self.testing = publish("Accessibility", "<h2>Testing</h2><p>Test with assistive technology users.</p>")
        indexer.rebuild()
        embed_missing(embedder=self.fake)


@override_settings(FEATURE_FLAGS=FLAGS, CONTENT_INDEX_EMBEDDINGS="fake", CONTENT_INDEX_MIN_SIMILARITY=0.3)
class HybridSearchTests(WithFakeModel):
    def test_a_question_in_other_words_is_found_by_meaning(self):
        question = "How do I take money from the public?"
        self.assertEqual(search(question, mode=KEYWORD).hits, [])
        outcome = search(question, mode=HYBRID)
        self.assertEqual(outcome.semantic, SEMANTIC_OK)
        self.assertEqual(outcome.hits[0].document.title, "GOV.UK Pay")
        self.assertGreater(outcome.hits[0].similarity, 0.3)

    def test_how_each_hit_matched(self):
        hits = {hit.document.title: hit for hit in search("assistive testing", mode=HYBRID).hits}
        self.assertEqual(hits["Accessibility"].matched_words, "every")

    @override_settings(CONTENT_INDEX_EMBEDDINGS="")
    def test_without_embeddings_any_of_the_words_will_do(self):
        outcome = search("I'm trying to find accessibility guidance", mode=HYBRID)
        self.assertEqual(outcome.semantic, SEMANTIC_OFF)
        self.assertEqual(outcome.hits[0].document.title, "Accessibility")
        self.assertEqual(outcome.hits[0].matched_words, "some")
        self.assertIsNone(outcome.hits[0].similarity)

    def test_a_model_that_fails_leaves_the_words(self):
        self.fake.fail = True
        with self.assertLogs("govuk.content_index.search", level="WARNING") as logs:
            outcome = search("assistive technology please", mode=HYBRID)
        self.assertEqual(outcome.semantic, SEMANTIC_UNAVAILABLE)
        self.assertEqual(outcome.hits[0].document.title, "Accessibility")
        self.assertNotIn("assistive", "\n".join(logs.output))

    def test_a_poor_match_by_meaning_alone_is_dropped(self):
        everything = search("poem about cats", mode=HYBRID).hits
        kept = search("poem about cats", mode=HYBRID, min_similarity=0.3).hits
        self.assertTrue(everything)
        self.assertEqual(kept, [])

    def test_the_mcp_server_searches_by_meaning_too(self):
        results = tools.search(query="take money from the public").results
        self.assertEqual(results[0].title, "GOV.UK Pay")


class CloseTests(SimpleTestCase):
    def _hit(self, matched_words, similarity):
        return SearchHit(document=None, url="", score=0, snippet="", matched_words=matched_words, similarity=similarity)

    def test_every_word_or_alike_enough(self):
        self.assertTrue(is_close(self._hit("every", None), 0.4))
        self.assertTrue(is_close(self._hit(None, 0.5), 0.4))
        self.assertFalse(is_close(self._hit(None, 0.3), 0.4))
        self.assertFalse(is_close(self._hit("some", 0.3), 0.4))

    def test_some_of_the_words_when_there_is_no_meaning_to_go_on(self):
        self.assertTrue(is_close(self._hit("some", None), 0.4, semantic=SEMANTIC_OFF))
        self.assertTrue(is_close(self._hit("some", None), 0.4, semantic=SEMANTIC_UNAVAILABLE))


@override_settings(
    FEATURE_FLAGS=FLAGS,
    CONTENT_INDEX_EMBEDDINGS="fake",
    CONTENT_INDEX_MIN_SIMILARITY=0.3,
    ASK_RATE_LIMIT_PER_MINUTE=0,
)
class AskPageTests(WithFakeModel):
    def setUp(self):
        super().setUp()
        self.url = reverse("ask")

    def test_opened_directly_it_is_the_search_area_alone(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        area = html[html.index('id="app-ask-search"'):html.index("</form>", html.index('id="app-ask-search"'))]
        self.assertIn("app-ask-search--page", area)
        self.assertNotIn("hidden", area.split(">", 1)[0])
        self.assertIn(
            '<label class="govuk-label govuk-label--l app-question-box__lead" for="app-ask-search-question">'
            "What would you like to find out about?</label>",
            area,
        )
        self.assertIn('<h1 class="govuk-label-wrapper">', area)
        self.assertIn('placeholder="Search"', area)
        self.assertIn("Do not include personal or sensitive information.", area)
        self.assertNotIn("app-catalogue-card", html)
        self.assertNotIn("data-ask-matches", html)
        self.assertIn('class="app-site-search', html)  # the site search box is still the header's
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["X-Robots-Tag"], "noindex")

    def test_without_a_combined_hero_on_the_home_page_the_area_heads_the_page(self):
        html = self.client.post(self.url, {"question": "payments"}).content.decode()
        main = html[html.index('<main'):]
        self.assertIn('id="app-ask-search"', main)
        self.assertIn('value="payments"', main)
        self.assertNotIn("<textarea", html)
        self.assertNotIn("masthead--combined", html)

    def test_the_closest_matches_as_cards(self):
        response = self.client.post(self.url, {"question": "How do I take money from the public?"})
        self.assertContains(response, "<title>Query results")
        self.assertContains(response, '<h3 class="govuk-heading-m">Close matches')
        self.assertContains(response, 'class="app-catalogue-card')
        self.assertContains(response, self.pay.url)
        self.assertContains(response, 'value="How do I take money from the public?"')  # in the box again
        self.assertIn("no-store", response["Cache-Control"])

    def test_nothing_close(self):
        response = self.client.post(self.url, {"question": "Write me a poem about cats"})
        self.assertContains(response, "Nothing close to your question")
        self.assertNotContains(response, 'class="app-catalogue-card')

    def test_an_empty_or_long_question_is_an_error(self):
        empty = self.client.post(self.url, {"question": "   "})
        self.assertContains(empty, "Please enter a question or search query")
        self.assertContains(empty, "govuk-error-summary")
        long = self.client.post(self.url, {"question": "word " * 70})
        self.assertContains(long, "300 characters or fewer")

    def test_only_get_and_post(self):
        self.assertEqual(self.client.put(self.url).status_code, 405)

    def test_it_needs_no_csrf_token(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(self.url, {"question": "payments"}).status_code, 200)

    def test_the_question_is_never_logged(self):
        with self.assertLogs("govuk.ask", level="INFO") as logs:
            self.client.post(self.url, {"question": "take money from my private client"})
        (record,) = logs.records
        self.assertEqual(record.semantic, SEMANTIC_OK)
        self.assertGreaterEqual(record.results, 1)
        output = "\n".join(logs.output) + str(vars(record))
        self.assertNotIn("private", output)

    @override_settings(FEATURE_FLAGS={**FLAGS, "ASK": False})
    def test_off_with_the_flag(self):
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {"question": "payments"}).status_code, 404)

    def test_with_javascript_it_comes_back_before_searching(self):
        with mock.patch("govuk.ask.views._matches") as matches:
            response = self.client.post(self.url, {"question": "payments <now>", "js": "1"})
        matches.assert_not_called()
        self.assertContains(response, 'data-ask-matches data-url="/ask/matches/"')
        self.assertContains(response, 'data-question="payments &lt;now&gt;"')
        self.assertContains(response, "Thinking…")  # no summary, so in the matches' place
        self.assertNotContains(response, "data-ask-summary")
        self.assertNotContains(response, 'class="app-catalogue-card')
        self.assertIn("no-store", response["Cache-Control"])

    def test_with_javascript_an_empty_question_is_still_an_error(self):
        response = self.client.post(self.url, {"question": " ", "js": "1"})
        self.assertContains(response, "Please enter a question or search query")
        self.assertNotContains(response, "data-ask-matches")

    @override_settings(ASK_RATE_LIMIT_PER_MINUTE=2)
    def test_a_client_asking_too_often_is_told_to_wait(self):
        statuses = [self.client.post(self.url, {"question": "payments"}).status_code for _ in range(3)]
        self.assertEqual(statuses, [200, 200, 429])
        response = self.client.post(self.url, {"question": "payments"})
        self.assertContains(response, "Wait a minute", status_code=429)
        self.assertTrue(1 <= int(response["Retry-After"]) <= 60)

    @override_settings(ASK_RATE_LIMIT_PER_MINUTE=1, CLIENT_IP_HEADER="true-client-ip")
    def test_behind_cloudfront_each_viewer_is_counted_apart(self):
        first = self.client.post(self.url, {"question": "payments"}, HTTP_TRUE_CLIENT_IP="203.0.113.1")
        second = self.client.post(self.url, {"question": "payments"}, HTTP_TRUE_CLIENT_IP="203.0.113.2")
        third = self.client.post(self.url, {"question": "payments"}, HTTP_TRUE_CLIENT_IP="203.0.113.1")
        self.assertEqual([r.status_code for r in (first, second, third)], [200, 200, 429])


class AskMatchesTests(WithFakeModel):
    """/ask/matches/: the matches for main.js to put in the page it went straight to."""

    def setUp(self):
        super().setUp()
        self.url = reverse("ask_matches")

    def _post(self, question, **headers):
        return self.client.post(self.url, {"question": question}, **headers)

    def test_the_matches_section_and_how_many(self):
        response = self._post("How do I take money from the public?")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertGreaterEqual(body["count"], 1)
        self.assertIn('class="app-catalogue-card', body["html"])
        self.assertIn(self.pay.url, body["html"])
        self.assertIn(">Close matches<", body["html"])
        self.assertEqual(body["ticket"], "")  # no model, so no summary
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["X-Robots-Tag"], "noindex")

    def test_nothing_close(self):
        body = self._post("Write me a poem about cats").json()
        self.assertEqual(body["count"], 0)
        self.assertIn("Nothing close to your question", body["html"])

    def test_an_empty_or_long_question_is_refused(self):
        for question in ("  ", "word " * 70):
            response = self._post(question)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["status"], "invalid")

    def test_only_post(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_it_needs_no_csrf_token(self):
        from django.test import Client

        self.assertEqual(Client(enforce_csrf_checks=True).post(self.url, {"question": "payments"}).status_code, 200)

    @override_settings(FEATURE_FLAGS={**FLAGS, "ASK": False})
    def test_off_with_the_flag(self):
        self.assertEqual(self._post("payments").status_code, 404)

    @override_settings(ASK_RATE_LIMIT_PER_MINUTE=1)
    def test_a_client_asking_too_often_is_told_to_wait(self):
        self.assertEqual(self._post("payments").status_code, 200)
        response = self._post("payments")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["status"], "error")
        self.assertIn("Wait a minute", response.json()["html"])
        self.assertTrue(1 <= int(response["Retry-After"]) <= 60)

    def test_the_question_is_never_logged(self):
        with self.assertLogs("govuk.ask", level="INFO") as logs:
            self._post("take money from my private client")
        output = "\n".join(logs.output) + "".join(str(vars(record)) for record in logs.records)
        self.assertNotIn("private", output)


class AISearchTests(WithFakeModel):
    """AI search: the magnifier button in place of the site search box, and its search area."""

    def setUp(self):
        from govuk.models import CustomiseSettings
        from govuk.models.pages import HeroStyle

        site = Site.objects.get(is_default_site=True)
        home = site.root_page.specific
        home.title = "Technology in government"
        home.hero_title = ""
        home.hero_intro = "<p>Find what you need to build services.</p>"
        home.hero_style = HeroStyle.COMBINED
        home.save_revision().publish()
        self.home = home
        self.customise = CustomiseSettings.for_site(site)
        self.customise.search_type = "ask"
        self.customise.ask_search_on_home_page = True
        self.customise.search_placeholder = "For example, cloud"
        self.customise.save()
        super().setUp()
        self.url = reverse("ask")

    @staticmethod
    def _area(html: str) -> str:
        start = html.index('id="app-ask-search"')
        return html[html.rindex("<div", 0, start):html.index("</form>", start)]

    @staticmethod
    def _header(html: str) -> str:
        return html[html.index('<header class="govuk-template__header">'):html.index("</header>")]

    @staticmethod
    def _band(html: str) -> str:
        start = html.index("masthead--combined")
        return html[start:html.index("</form>", start)]

    @staticmethod
    def _below(html: str) -> str:
        return html[html.index("</form>", html.index('id="app-ask-search"')):]

    def _get(self, url: str) -> str:
        return self.client.get(url).content.decode()

    def _page(self, title: str, **fields):
        page = publish(title, "<p>Body.</p>", parent=self.home)
        for name, value in fields.items():
            setattr(page, name, value)
        page.save_revision().publish()
        return page

    def test_always_open_at_the_foot_of_the_home_pages_band(self):
        html = self._get("/")
        band = self._band(html)
        self.assertIn("app-ask-search app-ask-search--inverse", band)
        self.assertNotIn("hidden", self._area(html).split(">", 1)[0])
        self.assertIn(
            '<label class="govuk-label govuk-label--m app-question-box__lead" for="app-ask-search-question">'
            "What would you like to find out about?</label>",
            band,
        )
        self.assertIn('placeholder="For example, cloud"', band)
        self.assertIn('class="govuk-body-s app-question-box__privacy"', band)
        self.assertIn('aria-describedby="app-ask-search-question-privacy"', band)
        header = self._header(html)
        self.assertNotIn("app-search-toggle", header)  # open here, so no button
        self.assertNotIn("app-site-search", header)

    def test_elsewhere_the_button_opens_it_below_the_navigation(self):
        html = self._get(self._page("Guidance").url)
        header = self._header(html)
        self.assertNotIn("app-site-search", header)
        self.assertIn('<a class="app-search-toggle js-ask-search-link" href="/ask/">', header)
        self.assertRegex(
            header,
            r'<button type="button"\s+class="app-search-toggle js-ask-search-toggle"\s+'
            r'aria-controls="app-ask-search"\s+aria-expanded="false"\s+hidden>',
        )
        area = self._area(html)
        self.assertIn('class="app-ask-search"', area)  # below the navigation, not inverse
        self.assertIn("hidden>", area.split("<form", 1)[0])
        self.assertLess(header.index("govuk-service-navigation"), header.index('id="app-ask-search"'))

    def test_on_another_combined_hero_it_opens_at_the_foot_of_the_band(self):
        from govuk.models.pages import HeroStyle

        html = self._get(self._page("Standards", hero_style=HeroStyle.COMBINED).url)
        self.assertNotIn('id="app-ask-search"', self._header(html))
        band = self._band(html)
        self.assertIn("app-ask-search app-ask-search--inverse", band)
        self.assertIn("hidden>", self._area(html).split("<form", 1)[0])
        self.assertIn("js-ask-search-toggle", self._header(html))

    def test_not_always_open_the_home_page_has_the_button(self):
        self.customise.ask_search_on_home_page = False
        self.customise.save()
        html = self._get("/")
        self.assertIn("js-ask-search-toggle", self._header(html))
        self.assertIn("hidden>", self._area(html).split("<form", 1)[0])

    def test_its_lead_text_is_a_setting(self):
        from govuk.models import AskSettings

        ask = AskSettings.for_site(Site.objects.get(is_default_site=True))
        ask.search_lead_text = "Ask us anything"
        ask.save()
        self.assertIn(">Ask us anything</label>", self._get("/"))

    def test_with_search_hidden_there_is_no_button(self):
        self.customise.search_location = "hidden"
        self.customise.save()
        html = self._get(self._page("Guidance").url)
        self.assertNotIn("app-search-toggle", html)
        self.assertNotIn('id="app-ask-search"', html)
        self.assertNotIn("app-site-search", html)
        self.assertIn('id="app-ask-search"', self._get("/"))  # still always open on the home page

    def test_in_the_navigation_bar(self):
        self.customise.search_location = "navigation"
        self.customise.save()
        header = self._header(self._get(self._page("Guidance").url))
        navigation = header[header.index("govuk-service-navigation"):]
        self.assertIn("js-ask-search-toggle", navigation)

    @override_settings(FEATURE_FLAGS={**FLAGS, "ASK": False})
    def test_without_ask_it_is_the_site_search_box(self):
        html = self._get("/")
        self.assertIn("app-site-search", self._header(html))
        self.assertNotIn("app-search-toggle", html)
        self.assertNotIn('id="app-ask-search"', html)

    def test_the_site_search_type_keeps_the_box(self):
        self.customise.search_type = "site"
        self.customise.save()
        html = self._get("/")
        self.assertIn("app-site-search", self._header(html))
        self.assertNotIn('id="app-ask-search"', html)

    def test_ask_opened_directly_is_the_home_pages_band_with_the_area_open(self):
        html = self._get(self.url)
        self.assertIn('<h1 class="hero__title">Technology in government</h1>', html)
        self.assertIn("app-ask-search--inverse", self._band(html))
        self.assertNotIn("hidden", self._area(html).split(">", 1)[0])
        self.assertNotIn("app-search-toggle", self._header(html))
        self.assertNotIn("app-catalogue-card", html)

    def test_the_matches_under_the_home_pages_band(self):
        html = self.client.post(self.url, {"question": "How do I take money from the public?"}).content.decode()
        band = self._band(html)
        self.assertIn('<h1 class="hero__title">Technology in government</h1>', html)
        self.assertIn("Find what you need to build services.", band)
        self.assertIn('value="How do I take money from the public?"', band)
        self.assertIn("govuk-service-navigation--inverse", html)
        self.assertIn('class="app-catalogue-card', self._below(html))
        self.assertIn("<title>Query results", html)
        self.assertNotIn("Edit this page", html)

    def test_an_empty_question_is_an_error_under_the_box(self):
        html = self.client.post(self.url, {"question": "  "}).content.decode()
        band = self._band(html)
        self.assertIn(
            '<p id="app-ask-search-question-error" class="govuk-error-message app-question-box__error">', band
        )
        self.assertIn("Please enter a question or search query", band)
        self.assertIn('aria-invalid="true"', band)
        self.assertIn('aria-describedby="app-ask-search-question-error app-ask-search-question-privacy"', band)
        self.assertNotIn("govuk-input--error", band)  # no red border on the band
        below = self._below(html)
        self.assertIn('<a href="#app-ask-search-question">Please enter a question or search query</a>', below)
        self.assertNotIn("app-catalogue-card", below)

    def test_a_long_question_is_kept_in_the_box(self):
        band = self._band(self.client.post(self.url, {"question": "word " * 70}).content.decode())
        self.assertIn("300 characters or fewer", band)
        self.assertIn('value="word word', band)


@override_settings(FEATURE_FLAGS=FLAGS, CONTENT_INDEX_EMBEDDINGS="", ASK_RATE_LIMIT_PER_MINUTE=0)
class CatalogueCardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.main = make_content_main_page(Site.objects.get(is_default_site=True).root_page)
        import_fixture(self.main)
        indexer.rebuild()

    def _ask(self, question):
        return self.client.post(reverse("ask"), {"question": question}).content.decode()

    def test_a_part_of_an_entry_is_shown_on_its_entrys_card(self):
        html = self._ask("Do the first thing")
        self.assertIn(">Hosted standard</a>", html)
        self.assertIn("Most relevant part:", html)
        self.assertIn(">The first point</a>", html)
        self.assertEqual(html.count(">Hosted standard</a>"), 1)

    def test_an_external_entry_goes_to_its_source(self):
        html = self._ask("external policy")
        self.assertIn('href="https://www.gov.uk/guidance/external-policy"', html)

    def test_each_card_has_its_type(self):
        self.assertIn("app-catalogue-badge--grey\">Policy<", self._ask("external policy"))

    def test_at_most_six_cards(self):
        with mock.patch("govuk.ask.views.MAX_CARDS", 2):
            html = self._ask("hosted")
        self.assertEqual(html.count('<li class="app-catalogue-card'), 2)

    def test_nothing_close_offers_the_whole_catalogue(self):
        self.assertIn('href="/content/all/"', self._ask("zzzz qqqq"))


@override_settings(FEATURE_FLAGS=FLAGS)
class QuestionBoxTests(TestCase):
    def setUp(self):
        self.page = publish("Home", "<p>Welcome.</p>")
        self.page.body = [
            {"type": "text", "value": "<p>Welcome.</p>"},
            {"type": "question_box", "value": {"label": "What do you need?", "hint": "For example, cloud", "button_text": "Go"}},
        ]
        self.page.save_revision().publish()

    def test_it_posts_the_question_to_ask(self):
        html = self.client.get(self.page.url).content.decode()
        self.assertRegex(html, rf'<form class="app-question-box"\s+action="{reverse("ask")}"\s+method="post"')
        box = html.split('class="app-question-box"', 1)[1].split("</form>", 1)[0]
        self.assertIn('<label class="govuk-visually-hidden" for="ask-question-', box)
        self.assertIn("What do you need?", box)
        self.assertRegex(box, r'<input class="govuk-input app-question-box__input"[^>]*type="text"')
        self.assertIn('placeholder="For example, cloud"', box)
        self.assertIn('name="question"', box)
        self.assertIn('<span class="govuk-visually-hidden">Go</span>', box)
        self.assertIn("app-question-box__icon", box)
        for gone in ("govuk-hint", "<textarea", "personal or sensitive", "govuk-error-message", "aria-invalid"):
            self.assertNotIn(gone, box)
        self.assertIn('data-empty-error="Please enter a question or search query"', box)

    @override_settings(FEATURE_FLAGS={**FLAGS, "ASK": False})
    def test_nothing_while_ask_is_off(self):
        html = self.client.get(self.page.url).content.decode()
        self.assertNotIn("What do you need?", html)
        self.assertIn("Welcome.", html)


class QuestionsFileTests(SimpleTestCase):
    def test_the_bundled_questions_are_well_formed(self):
        questions = ev.load_questions()
        self.assertGreaterEqual(len(questions), 50)
        self.assertEqual({question.category for question in questions}, set(ev.CATEGORIES))

    def test_a_badly_formed_question_is_refused(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "q.yaml"
            for text in (
                "- {question: x, category: nonsense, expect: [a/]}",
                "- {question: x, category: out_of_scope, expect: [a/]}",
                "- {question: x, category: find, expect: []}",
                "- {question: x, category: find, expect: [a/], match: most}",
            ):
                path.write_text(text)
                with self.subTest(text=text), self.assertRaises(ev.QuestionsError):
                    ev.load_questions(path)

    def test_a_part_of_an_entry_counts_for_it(self):
        from govuk.models import IndexedDocument

        point = IndexedDocument(url_path="/content/standards/code/use-cloud/", source_url="")
        self.assertTrue(ev.answers(point, "standards/code/"))
        self.assertTrue(ev.answers(point, "code/use-cloud/"))
        self.assertFalse(ev.answers(IndexedDocument(url_path="/content/standards/code/", source_url=""), "code/use-cloud/"))


@override_settings(FEATURE_FLAGS=FLAGS, CONTENT_INDEX_EMBEDDINGS="fake", CONTENT_INDEX_MIN_SIMILARITY=0.3)
class EvaluationTests(WithFakeModel):
    def _questions(self, folder):
        path = Path(folder) / "q.yaml"
        path.write_text(
            "- {question: 'How do I take money from the public?', category: paraphrase, expect: [govuk-pay/]}\n"
            "- {question: 'assistive technology', category: find, expect: [accessibility/, no-such-page/], match: all}\n"
            "- {question: 'Write me a poem about cats', category: out_of_scope, expect: []}\n"
        )
        return path

    def test_the_numbers(self):
        with TemporaryDirectory() as folder:
            questions = ev.load_questions(self._questions(folder))
        keyword = ev.summarise(ev.evaluate(questions, mode=KEYWORD, min_similarity=0.3))
        hybrid = ev.summarise(ev.evaluate(questions, mode=HYBRID, min_similarity=0.3))
        self.assertEqual((keyword.hit_at_1, keyword.recall, keyword.none_close), (0.0, 0.5, 1.0))
        self.assertEqual((hybrid.hit_at_1, hybrid.recall, hybrid.none_close), (1.0, 0.5, 1.0))
        self.assertEqual(ev.unknown_answers(questions), ["no-such-page/"])

    def test_the_command(self):
        out, err = StringIO(), StringIO()
        with TemporaryDirectory() as folder:
            path = self._questions(folder)
            report = Path(folder) / "out.json"
            call_command(
                "evaluate_content_search", str(path), "--mode", "all", "--show-misses", "--json", str(report),
                stdout=out, stderr=err,
            )
            written = json.loads(report.read_text())
        output = out.getvalue()
        for mode in ("keyword", "semantic", "hybrid"):
            self.assertIn(f"Mode {mode}: 3 questions", output)
        self.assertIn("x [paraphrase] How do I take money from the public?", output)
        self.assertIn("no-such-page/", err.getvalue())
        self.assertEqual([mode["mode"] for mode in written["modes"]], ["keyword", "semantic", "hybrid"])

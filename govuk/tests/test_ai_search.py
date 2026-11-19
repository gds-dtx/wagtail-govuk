"""AI search: /search/, the matches endpoint, and the button and search area."""

from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.text import slugify
from wagtail.models import Site

from govuk.ai_search import MATCHES_PATH
from govuk.content_index import embeddings, indexer
from govuk.content_index.embeddings import FakeEmbedder, embed_missing
from govuk.content_index.search import SEMANTIC_OK
from govuk.models import ContentPage, SearchSettings
from govuk.models.pages import HeroStyle
from govuk.tests.catalogue_helpers import import_fixture, make_content_main_page
from govuk.tests.page_helpers import text_body

FLAGS = {"SKILLS": False, "FEEDBACK": False, "CATALOGUE": True, "AI": True}
CONCEPTS = {"money": "pay", "payments": "pay", "payment": "pay", "card": "pay", "public": "people", "people": "people"}
EMPTY = "Please enter a question or search query"


def publish(title: str, html: str, parent=None) -> ContentPage:
    parent = parent or Site.objects.get(is_default_site=True).root_page
    page = parent.add_child(
        instance=ContentPage(title=title, slug=slugify(title), body=text_body(html))
    )
    page.save_revision().publish()
    return page


def choose_ai_search(**fields) -> SearchSettings:
    search = SearchSettings.for_site(Site.objects.get(is_default_site=True))
    search.search_type = SearchSettings.SearchType.AI
    for name, value in fields.items():
        setattr(search, name, value)
    search.save()
    return search


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


@override_settings(
    FEATURE_FLAGS=FLAGS,
    CONTENT_INDEX_EMBEDDINGS="fake",
    CONTENT_INDEX_MIN_SIMILARITY=0.3,
    AI_SEARCH_RATE_LIMIT_PER_MINUTE=0,
)
class SearchPageTests(WithFakeModel):
    """/search/ with AI search, on a site whose home page has no combined hero."""

    def setUp(self):
        super().setUp()
        self.search = choose_ai_search()
        self.url = reverse("search")

    @staticmethod
    def _area(html: str) -> str:
        start = html.index('id="app-ai-search"')
        return html[html.rindex("<div", 0, start):html.index("</form>", start)]

    def test_opened_directly_it_is_the_search_area_alone(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        area = self._area(html)
        self.assertIn("app-ai-search-area--page", area)
        self.assertNotIn("hidden", area.split(">", 1)[0])
        self.assertIn(
            '<label class="govuk-label govuk-label--l app-ai-search-box__lead" for="app-ai-search-question">'
            "What would you like to find out about?</label>",
            area,
        )
        self.assertIn('<h1 class="govuk-label-wrapper">', area)
        self.assertIn('placeholder="Search"', area)
        self.assertIn('action="/search/"', area)
        self.assertIn("Do not include personal or sensitive information.", area)
        self.assertNotIn("app-catalogue-card", html)
        self.assertNotIn("data-ai-search-matches", html)
        self.assertNotIn("app-site-search", html)  # the magnifier button instead
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["X-Robots-Tag"], "noindex")

    def test_a_query_in_the_address_is_not_searched(self):
        html = self.client.get(self.url, {"query": "payments"}).content.decode()
        self.assertNotIn("app-catalogue-card", html)
        self.assertNotIn('value="payments"', html)

    def test_without_javascript_the_matches_are_searched_without_claude(self):
        with mock.patch("govuk.ai_search.views.get_claude") as get_claude:
            response = self.client.post(self.url, {"question": "How do I take money from the public?"})
        get_claude.assert_not_called()
        self.assertContains(response, "<title>Query results")
        self.assertContains(response, '<h3 class="govuk-heading-m">Close matches</h3>')
        self.assertContains(response, 'class="app-catalogue-card')
        self.assertContains(response, self.pay.url)
        self.assertContains(response, 'value="How do I take money from the public?"')  # in the box again
        self.assertNotContains(response, "data-ai-search-summary")
        main = response.content.decode().split("<main", 1)[1]
        self.assertIn('id="app-ai-search"', main)
        self.assertIn("no-store", response["Cache-Control"])

    def test_nothing_close(self):
        response = self.client.post(self.url, {"question": "Write me a poem about cats"})
        self.assertContains(response, "Nothing close to your question")
        self.assertNotContains(response, 'class="app-catalogue-card')

    def test_with_javascript_it_comes_back_before_searching(self):
        with mock.patch("govuk.ai_search.views.find_matches") as find_matches:
            response = self.client.post(self.url, {"question": "payments <now>", "js": "1"})
        find_matches.assert_not_called()
        self.assertContains(response, f'data-ai-search-matches data-url="{MATCHES_PATH}"')
        self.assertContains(response, 'data-question="payments &lt;now&gt;"')
        self.assertContains(response, "Thinking…")  # no summary, so in the matches' place
        self.assertNotContains(response, "data-ai-search-summary")
        self.assertNotContains(response, 'class="app-catalogue-card')

    def test_an_empty_or_long_question_is_an_error(self):
        for question, message in (("   ", EMPTY), ("word " * 70, "300 characters or fewer")):
            for js in ("", "1"):
                with self.subTest(question=question[:5], js=js):
                    response = self.client.post(self.url, {"question": question, "js": js})
                    self.assertContains(response, message)
                    self.assertContains(response, "govuk-error-summary")
                    self.assertContains(response, "<title>Error:")
                    self.assertNotContains(response, "data-ai-search-matches")

    def test_it_needs_no_csrf_token(self):
        response = Client(enforce_csrf_checks=True).post(self.url, {"question": "payments"})
        self.assertEqual(response.status_code, 200)

    def test_the_question_is_never_logged(self):
        with self.assertLogs("govuk.ai_search", level="INFO") as logs:
            self.client.post(self.url, {"question": "take money from my private client"})
        (record,) = logs.records
        self.assertEqual(record.semantic, SEMANTIC_OK)
        self.assertGreaterEqual(record.results, 1)
        output = "\n".join(logs.output) + str(vars(record))
        self.assertNotIn("private", output)

    @override_settings(AI_SEARCH_RATE_LIMIT_PER_MINUTE=2)
    def test_a_client_asking_too_often_is_told_to_wait(self):
        statuses = [self.client.post(self.url, {"question": "payments"}).status_code for _ in range(3)]
        self.assertEqual(statuses, [200, 200, 429])
        response = self.client.post(self.url, {"question": "payments"})
        self.assertContains(response, "Wait a minute", status_code=429)
        self.assertTrue(1 <= int(response["Retry-After"]) <= 60)

    @override_settings(AI_SEARCH_RATE_LIMIT_PER_MINUTE=1, CLIENT_IP_HEADER="true-client-ip")
    def test_behind_cloudfront_each_viewer_is_counted_apart(self):
        first = self.client.post(self.url, {"question": "payments"}, HTTP_TRUE_CLIENT_IP="203.0.113.1")
        second = self.client.post(self.url, {"question": "payments"}, HTTP_TRUE_CLIENT_IP="203.0.113.2")
        third = self.client.post(self.url, {"question": "payments"}, HTTP_TRUE_CLIENT_IP="203.0.113.1")
        self.assertEqual([r.status_code for r in (first, second, third)], [200, 200, 429])

    def test_normal_search_answers_no_post(self):
        self.search.search_type = SearchSettings.SearchType.NORMAL
        self.search.save()
        self.assertEqual(self.client.post(self.url, {"question": "payments"}).status_code, 405)
        self.assertEqual(self.client.get(self.url, {"query": "payments"}).status_code, 200)

    @override_settings(FEATURE_FLAGS={**FLAGS, "AI": False})
    def test_without_the_ai_features_it_is_normal_search(self):
        response = self.client.get(self.url, {"query": "payments"})
        self.assertNotContains(response, 'id="app-ai-search"')
        self.assertEqual(self.client.post(self.url, {"question": "payments"}).status_code, 405)


@override_settings(
    FEATURE_FLAGS=FLAGS,
    CONTENT_INDEX_EMBEDDINGS="fake",
    CONTENT_INDEX_MIN_SIMILARITY=0.3,
    AI_SEARCH_RATE_LIMIT_PER_MINUTE=0,
)
class MatchesTests(WithFakeModel):
    """The matches endpoint, served by Django in development (AI_SEARCH_IN_DJANGO)."""

    def setUp(self):
        super().setUp()
        choose_ai_search()
        self.url = reverse("ai_search_matches")

    def _post(self, question, **headers):
        return self.client.post(self.url, {"question": question}, **headers)

    def test_its_path_is_the_services(self):
        self.assertEqual(self.url, MATCHES_PATH)

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
        self.assertEqual(Client(enforce_csrf_checks=True).post(self.url, {"question": "payments"}).status_code, 200)

    def test_not_while_normal_search_is_chosen(self):
        choose_ai_search(search_type=SearchSettings.SearchType.NORMAL)
        self.assertEqual(self._post("payments").status_code, 404)

    @override_settings(AI_SEARCH_RATE_LIMIT_PER_MINUTE=1)
    def test_a_client_asking_too_often_is_told_to_wait(self):
        self.assertEqual(self._post("payments").status_code, 200)
        response = self._post("payments")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["status"], "error")
        self.assertIn("Wait a minute", response.json()["html"])
        self.assertTrue(1 <= int(response["Retry-After"]) <= 60)

    def test_the_question_is_never_logged(self):
        with self.assertLogs("govuk.ai_search", level="INFO") as logs:
            self._post("take money from my private client")
        output = "\n".join(logs.output) + "".join(str(vars(record)) for record in logs.records)
        self.assertNotIn("private", output)


@override_settings(
    FEATURE_FLAGS=FLAGS,
    CONTENT_INDEX_EMBEDDINGS="fake",
    CONTENT_INDEX_MIN_SIMILARITY=0.3,
    AI_SEARCH_RATE_LIMIT_PER_MINUTE=0,
)
class ButtonAndAreaTests(WithFakeModel):
    """The magnifier button in place of normal search's box, and its search area."""

    def setUp(self):
        site = Site.objects.get(is_default_site=True)
        home = site.root_page.specific
        home.title = "Technology in government"
        home.hero_title = ""
        home.hero_intro = "<p>Find what you need to build services.</p>"
        home.hero_style = HeroStyle.COMBINED
        home.save_revision().publish()
        self.home = home
        self.search = choose_ai_search(ai_search_on_home_page=True, search_placeholder="For example, cloud")
        super().setUp()
        self.url = reverse("search")

    @staticmethod
    def _area(html: str) -> str:
        start = html.index('id="app-ai-search"')
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
        return html[html.index("</form>", html.index('id="app-ai-search"')):]

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
        self.assertIn("app-ai-search-area app-ai-search-area--inverse", band)
        self.assertNotIn("hidden", self._area(html).split(">", 1)[0])
        self.assertIn(
            '<label class="govuk-label govuk-label--m app-ai-search-box__lead" for="app-ai-search-question">'
            "What would you like to find out about?</label>",
            band,
        )
        self.assertIn('placeholder="For example, cloud"', band)
        self.assertIn('class="govuk-body-s app-ai-search-box__privacy"', band)
        self.assertIn('aria-describedby="app-ai-search-question-privacy"', band)
        header = self._header(html)
        self.assertNotIn("app-ai-search-toggle", header)  # open here, so no button
        self.assertNotIn("app-site-search", header)

    def test_its_style_sheet_and_script_are_loaded(self):
        html = self._get("/")
        self.assertIn("/static/ai-search.css", html)
        self.assertIn("/static/ai-search.js", html)
        self.assertIn("/static/catalogue.css", html)  # for the matches' cards

    def test_elsewhere_the_button_opens_it_below_the_navigation(self):
        html = self._get(self._page("Guidance").url)
        header = self._header(html)
        self.assertNotIn("app-site-search", header)
        self.assertIn('<a class="app-ai-search-toggle js-ai-search-link" href="/search/">', header)
        self.assertRegex(
            header,
            r'<button type="button"\s+class="app-ai-search-toggle js-ai-search-toggle"\s+'
            r'aria-controls="app-ai-search"\s+aria-expanded="false"\s+hidden>',
        )
        area = self._area(html)
        self.assertIn('class="app-ai-search-area"', area)  # below the navigation, not inverse
        self.assertIn("hidden>", area.split("<form", 1)[0])
        self.assertLess(header.index("govuk-service-navigation"), header.index('id="app-ai-search"'))

    def test_on_another_combined_hero_it_opens_at_the_foot_of_the_band(self):
        html = self._get(self._page("Standards", hero_style=HeroStyle.COMBINED).url)
        self.assertNotIn('id="app-ai-search"', self._header(html))
        band = self._band(html)
        self.assertIn("app-ai-search-area app-ai-search-area--inverse", band)
        self.assertIn("hidden>", self._area(html).split("<form", 1)[0])
        self.assertIn("js-ai-search-toggle", self._header(html))

    def test_not_always_open_the_home_page_has_the_button(self):
        self.search.ai_search_on_home_page = False
        self.search.save()
        html = self._get("/")
        self.assertIn("js-ai-search-toggle", self._header(html))
        self.assertIn("hidden>", self._area(html).split("<form", 1)[0])

    def test_its_lead_text_is_a_setting(self):
        self.search.ai_search_lead_text = "Ask us anything"
        self.search.save()
        self.assertIn(">Ask us anything</label>", self._get("/"))

    def test_with_search_hidden_there_is_no_button(self):
        self.search.search_location = "hidden"
        self.search.save()
        html = self._get(self._page("Guidance").url)
        self.assertNotIn("app-ai-search-toggle", html)
        self.assertNotIn('id="app-ai-search"', html)
        self.assertNotIn("app-site-search", html)
        self.assertIn('id="app-ai-search"', self._get("/"))  # still always open on the home page

    def test_in_the_navigation_bar(self):
        self.search.search_location = "navigation"
        self.search.save()
        header = self._header(self._get(self._page("Guidance").url))
        navigation = header[header.index("govuk-service-navigation"):]
        self.assertIn("js-ai-search-toggle", navigation)

    @override_settings(FEATURE_FLAGS={**FLAGS, "AI": False})
    def test_without_the_ai_features_it_is_normal_searchs_box(self):
        html = self._get("/")
        self.assertIn("app-site-search", self._header(html))
        self.assertNotIn("app-ai-search-toggle", html)
        self.assertNotIn('id="app-ai-search"', html)
        self.assertNotIn("ai-search.css", html)

    def test_normal_search_keeps_the_box(self):
        self.search.search_type = SearchSettings.SearchType.NORMAL
        self.search.save()
        html = self._get("/")
        self.assertIn("app-site-search", self._header(html))
        self.assertNotIn('id="app-ai-search"', html)

    def test_search_opened_directly_is_the_home_pages_band_with_the_area_open(self):
        html = self._get(self.url)
        self.assertIn('<h1 class="hero__title">Technology in government</h1>', html)
        self.assertIn("app-ai-search-area--inverse", self._band(html))
        self.assertNotIn("hidden", self._area(html).split(">", 1)[0])
        self.assertNotIn("app-ai-search-toggle", self._header(html))
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
            '<p id="app-ai-search-question-error" class="govuk-error-message app-ai-search-box__error">', band
        )
        self.assertIn(EMPTY, band)
        self.assertIn('aria-invalid="true"', band)
        self.assertIn('aria-describedby="app-ai-search-question-error app-ai-search-question-privacy"', band)
        self.assertNotIn("govuk-input--error", band)  # no red border on the band
        below = self._below(html)
        self.assertIn(f'<a href="#app-ai-search-question">{EMPTY}</a>', below)
        self.assertNotIn("app-catalogue-card", below)

    def test_a_long_question_is_kept_in_the_box(self):
        band = self._band(self.client.post(self.url, {"question": "word " * 70}).content.decode())
        self.assertIn("300 characters or fewer", band)
        self.assertIn('value="word word', band)


@override_settings(FEATURE_FLAGS=FLAGS, CONTENT_INDEX_EMBEDDINGS="", AI_SEARCH_RATE_LIMIT_PER_MINUTE=0)
class CatalogueCardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.main = make_content_main_page(Site.objects.get(is_default_site=True).root_page)
        import_fixture(self.main)
        indexer.rebuild()
        choose_ai_search()

    def _ask(self, question):
        return self.client.post(reverse("search"), {"question": question}).content.decode()

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
        with mock.patch("govuk.ai_search.views.MAX_CARDS", 2):
            html = self._ask("hosted")
        self.assertEqual(html.count('<li class="app-catalogue-card'), 2)

    def test_nothing_close_offers_the_whole_catalogue(self):
        self.assertIn('href="/content/all/"', self._ask("zzzz qqqq"))

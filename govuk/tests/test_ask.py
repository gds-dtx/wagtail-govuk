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

    def test_the_form_on_its_own(self):
        response = self.client.get(self.url)
        self.assertContains(response, "What are you wanting to do with technology?")
        self.assertContains(response, "Do not include personal or sensitive information.")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["X-Robots-Tag"], "noindex")

    def test_the_closest_matches_as_cards(self):
        response = self.client.post(self.url, {"question": "How do I take money from the public?"})
        self.assertContains(response, "Results for your question")
        self.assertContains(response, "close match")
        self.assertContains(response, 'class="app-catalogue-card')
        self.assertContains(response, self.pay.url)
        self.assertContains(response, "How do I take money from the public?")  # in the box again
        self.assertIn("no-store", response["Cache-Control"])

    def test_nothing_close(self):
        response = self.client.post(self.url, {"question": "Write me a poem about cats"})
        self.assertContains(response, "Nothing close to your question")
        self.assertNotContains(response, 'class="app-catalogue-card')

    def test_an_empty_or_long_question_is_an_error(self):
        empty = self.client.post(self.url, {"question": "   "})
        self.assertContains(empty, "Enter your question")
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
        self.assertIn(f'action="{reverse("ask")}" method="post"', html)
        self.assertIn("What do you need?", html)
        self.assertIn('name="question"', html)
        self.assertIn("Do not include personal or sensitive information.", html)
        self.assertIn(">Go</button>", html)

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

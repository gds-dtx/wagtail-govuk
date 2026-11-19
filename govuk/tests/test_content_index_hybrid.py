"""Hybrid search of the content index: by words and by meaning (embeddings)."""

from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils.text import slugify
from wagtail.models import Site

from govuk.content_index import embeddings, indexer
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
from govuk.models import ContentPage
from govuk.tests.page_helpers import text_body

FLAGS = {"SKILLS": False, "FEEDBACK": False, "CATALOGUE": True, "AI": True}
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

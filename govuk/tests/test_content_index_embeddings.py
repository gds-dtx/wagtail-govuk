"""The content index's embeddings: what is embedded, filling the store, the model."""

import io
import json
from io import StringIO
from unittest import mock

from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils.text import slugify
from wagtail.models import Site

from govuk.content_index import embeddings, indexer
from govuk.content_index.embeddings import (
    BedrockEmbedder,
    EmbeddingError,
    FakeEmbedder,
    document_text,
    embed_missing,
    embed_query,
    section_text,
    text_hash,
    wanted_texts,
)
from govuk.models import ContentEmbedding, ContentPage, IndexedChunk, IndexedDocument
from govuk.tests.page_helpers import text_body

FLAGS = {"SKILLS": False, "FEEDBACK": False, "GUIDANCE": True, "MCP": True, "ASK": True}


def publish(title: str, html: str, parent=None) -> ContentPage:
    parent = parent or Site.objects.get(is_default_site=True).root_page
    page = parent.add_child(instance=ContentPage(title=title, slug=slugify(title), body=text_body(html)))
    page.save_revision().publish()
    return page


class FakeEmbedderTests(SimpleTestCase):
    def test_alike_texts_are_closer_than_unlike_ones(self):
        fake = FakeEmbedder(concepts={"money": "pay", "payments": "pay"})
        question, answer, other = fake.embed(["take money", "Take payments online", "Write a poem"])
        cosine = embeddings.cosine_similarity
        self.assertGreater(cosine(question, answer), 0.75)
        self.assertLess(cosine(question, other), 0.1)

    def test_vectors_have_length_one(self):
        (vector,) = FakeEmbedder().embed(["some words here"])
        self.assertAlmostEqual(sum(value * value for value in vector), 1.0)


class TextTests(SimpleTestCase):
    def test_a_section_is_embedded_under_its_document_and_heading(self):
        self.assertEqual(
            section_text("Cloud First", "Policy", "Exceptions", " Use the cloud. "),
            "Cloud First › Exceptions\nPolicy\n\nUse the cloud.",
        )
        self.assertEqual(section_text("Page", "", "", "Text"), "Page\n\nText")

    def test_a_document_is_embedded_as_its_card_reads(self):
        self.assertEqual(document_text("GOV.UK Pay", "Product", "Take payments."), "GOV.UK Pay\nProduct\nTake payments.")
        self.assertEqual(document_text("Page", "", ""), "Page")


class BedrockEmbedderTests(SimpleTestCase):
    def _embedder(self, client):
        embedder = BedrockEmbedder(embeddings.TITAN_V2, 256, "eu-west-2")
        embedder._clients = {False: client, True: client}
        return embedder

    def test_each_text_is_one_request_to_titan(self):
        client = mock.Mock()
        client.invoke_model.side_effect = lambda **kwargs: {
            "body": io.BytesIO(json.dumps({"embedding": [len(json.loads(kwargs["body"])["inputText"])]}).encode())
        }
        vectors = self._embedder(client).embed(["one", "three"])
        self.assertEqual(vectors, [[3], [5]])
        sent = json.loads(client.invoke_model.call_args_list[0].kwargs["body"])
        self.assertEqual((sent["dimensions"], sent["normalize"]), (256, True))
        self.assertEqual(client.invoke_model.call_args.kwargs["modelId"], embeddings.TITAN_V2)

    def test_a_refusal_is_an_embedding_error_without_the_text(self):
        from botocore.exceptions import ClientError

        client = mock.Mock()
        client.invoke_model.side_effect = ClientError({"Error": {"Code": "ThrottlingException"}}, "InvokeModel")
        with self.assertRaises(EmbeddingError) as raised:
            self._embedder(client).embed(["a private question"], query=True)
        self.assertIn("ThrottlingException", str(raised.exception))
        self.assertNotIn("private", str(raised.exception))

    def test_only_titan_v2_at_its_own_lengths(self):
        with self.assertRaises(ImproperlyConfigured):
            BedrockEmbedder("cohere.embed-english-v3", 1024, "eu-west-2")
        with self.assertRaises(ImproperlyConfigured):
            BedrockEmbedder(embeddings.TITAN_V2, 300, "eu-west-2")
        self.assertEqual(BedrockEmbedder(embeddings.TITAN_V2, 1024, "eu-west-2").key, f"{embeddings.TITAN_V2}@1024")

    @override_settings(CONTENT_INDEX_EMBEDDINGS="")
    def test_off_unless_configured(self):
        self.assertIsNone(embeddings.get_embedder())

    @override_settings(CONTENT_INDEX_EMBEDDINGS="something")
    def test_an_unknown_backend_is_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            embeddings.get_embedder()


class QueryCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_a_query_is_embedded_once_and_kept_under_its_hash(self):
        fake = FakeEmbedder()
        with mock.patch.object(cache, "set", wraps=cache.set) as cache_set:
            first = embed_query("a private question", fake)
            second = embed_query("a private question", fake)
        self.assertEqual(first, second)
        self.assertEqual(fake.calls, [(1, True)])
        key = cache_set.call_args.args[0]
        self.assertNotIn("private", key)


@override_settings(FEATURE_FLAGS=FLAGS, CONTENT_INDEX_EMBEDDINGS="fake")
class StoreTests(TestCase):
    def setUp(self):
        self.page = publish("Payments", "<h2>Taking payments</h2><p>Take card payments online.</p><h2>Refunds</h2><p>Give money back.</p>")
        indexer.rebuild()

    def test_the_indexer_records_the_hash_of_what_is_embedded(self):
        document = IndexedDocument.objects.get(page_id=self.page.pk)
        self.assertEqual(document.embedding_hash, text_hash(document_text(document.title, "", document.description)))
        for chunk in document.chunks.all():
            self.assertEqual(chunk.embedding_hash, text_hash(section_text("Payments", "", chunk.heading_path, chunk.text)))
        wanted, stale = wanted_texts()
        self.assertEqual(stale, 0)
        self.assertIn(document.embedding_hash, wanted)

    def test_only_new_and_changed_text_is_embedded(self):
        fake = FakeEmbedder()
        first = embed_missing(embedder=fake)
        stored = ContentEmbedding.objects.filter(model=fake.key).count()
        self.assertEqual(first.counts["embedded"], stored)
        self.assertGreater(stored, 0)
        self.assertEqual(embed_missing(embedder=fake).counts["embedded"], 0)

        self.page.body = text_body("<h2>Taking payments</h2><p>Take card payments online.</p><h2>Refunds</h2><p>Changed.</p>")
        self.page.save_revision().publish()
        indexer.rebuild()
        again = embed_missing(embedder=fake)
        # The changed section, and nothing else: the document's card is the same.
        self.assertEqual(again.counts["embedded"], 1)
        self.assertEqual(again.counts["removed"], 1)

    def test_a_forced_rebuild_embeds_nothing_again(self):
        fake = FakeEmbedder()
        embed_missing(embedder=fake)
        indexer.rebuild(force=True)
        self.assertEqual(embed_missing(embedder=fake).counts["embedded"], 0)

    def test_a_dry_run_writes_nothing(self):
        result = embed_missing(embedder=FakeEmbedder(), dry_run=True)
        self.assertGreater(result.counts["embedded"], 0)
        self.assertFalse(ContentEmbedding.objects.exists())

    def test_a_failing_model_is_given_up_on(self):
        fake = FakeEmbedder(fail=True)
        with mock.patch.object(embeddings, "BATCH_SIZE", 1):
            result = embed_missing(embedder=fake)
        self.assertEqual(len(fake.calls), embeddings.MAX_FAILED_BATCHES)
        self.assertEqual(result.counts["failed"], len(wanted_texts()[0]))
        self.assertFalse(ContentEmbedding.objects.exists())

    def test_a_row_whose_hash_is_of_other_text_is_stale(self):
        IndexedChunk.objects.filter(document__page_id=self.page.pk).update(embedding_hash="0" * 64)
        result = embed_missing(embedder=FakeEmbedder())
        self.assertGreater(result.counts["stale"], 0)


@override_settings(FEATURE_FLAGS=FLAGS, CONTENT_INDEX_EMBEDDINGS="fake")
class CommandTests(TestCase):
    def setUp(self):
        publish("Payments", "<p>Take card payments online.</p>")
        self.fake = FakeEmbedder()
        patcher = mock.patch.object(embeddings, "_fake", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_rebuild_embeds_at_the_end(self):
        out = StringIO()
        call_command("rebuild_content_index", stdout=out)
        self.assertIn("Embeddings: ", out.getvalue())
        self.assertTrue(ContentEmbedding.objects.filter(model=self.fake.key).exists())

    def test_the_rebuild_can_leave_it(self):
        call_command("rebuild_content_index", "--no-embed", stdout=StringIO())
        self.assertFalse(ContentEmbedding.objects.exists())

    def test_the_embed_command_reports(self):
        indexer.rebuild()
        out = StringIO()
        call_command("embed_content_index", "--dry-run", stdout=out)
        self.assertIn("Dry run, nothing written: Embeddings: ", out.getvalue())
        self.assertIn("to embed", out.getvalue())
        call_command("embed_content_index", stdout=out)
        self.assertTrue(ContentEmbedding.objects.exists())

    def test_a_failure_fails_the_command(self):
        indexer.rebuild()
        self.fake.fail = True
        with self.assertRaisesMessage(CommandError, "could not be embedded"):
            call_command("embed_content_index", stdout=StringIO(), stderr=StringIO())

    @override_settings(CONTENT_INDEX_EMBEDDINGS="")
    def test_it_refuses_with_embeddings_off(self):
        with self.assertRaisesMessage(CommandError, "CONTENT_INDEX_EMBEDDINGS"):
            call_command("embed_content_index")

    @override_settings(FEATURE_FLAGS={**FLAGS, "MCP": False, "ASK": False})
    def test_it_refuses_with_the_index_off(self):
        with self.assertRaisesMessage(CommandError, "FEATURE_MCP and FEATURE_ASK"):
            call_command("embed_content_index")

    @override_settings(FEATURE_FLAGS={**FLAGS, "MCP": False})
    def test_ask_alone_keeps_the_index(self):
        call_command("rebuild_content_index", stdout=StringIO())
        self.assertTrue(IndexedDocument.objects.exists())

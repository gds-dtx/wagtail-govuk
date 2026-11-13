"""Embeddings: a vector of what each section and document of the index means.

A search by words finds a page that uses the words asked; a vector lets it
find one that means what was asked, in other words ("take card payments"
finds GOV.UK Pay). Each section is embedded with its document's title and
type above it, and each document as its card reads: title, type and
description.

The vectors live in ``ContentEmbedding``, found by the hash of the text they
were made from (``text_hash``), which ``indexer._store`` records on each row
as it writes it. Storing calls nothing: publishing stays as quick as before.
``embed_missing`` (``manage.py embed_content_index``, also run at the end of
``rebuild_content_index``) embeds the text that has no vector yet and removes
vectors nothing uses. Until then a new section is found by its words alone.

Which model, if any, is ``settings.CONTENT_INDEX_EMBEDDINGS``: "bedrock" for
Amazon Titan Text Embeddings V2 on Amazon Bedrock, unset for none.

Nothing here logs or raises with the text it embeds: a query's text is what
someone asked.
"""

import hashlib
import json
import logging
import math
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

# Titan Text Embeddings V2 reads up to 8,192 tokens, roughly 50,000
# characters of English; a section is cut at a safe margin below that.
MAX_EMBED_CHARS = 20_000
# A query's vector is kept this long, under the hash of the query.
QUERY_CACHE_SECONDS = 24 * 60 * 60
# Texts sent, and rows written, at a time while filling the store.
BATCH_SIZE = 32
# Requests to the model at once while filling the store.
WORKERS = 4
# Batches in a row that may fail before a run gives up, rather than keep
# asking a model that is refusing or unreachable.
MAX_FAILED_BATCHES = 3

TITAN_V2 = "amazon.titan-embed-text-v2:0"
TITAN_V2_DIMENSIONS = (256, 512, 1024)

# Words that say nothing about what a question is about, which the fake
# embedder below and the any-word search (``search``) leave out.
STOPWORDS = frozenset(
    """a about am an and any are as at be been being but by can could did do does doing for
    from had has have having how i i'd i'm if in into is it its just m me my of on or our out
    please s should so some t than that the their them then there these they this those to
    trying up us was we were what when where which who why will with would you your""".split()
)


class EmbeddingError(Exception):
    """The model could not be reached or refused. The message never holds the text."""


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def section_text(document_title: str, type_label: str, heading_path: str, text: str) -> str:
    """What is embedded for one section: where it is, then what it says."""
    heading = f"{document_title} › {heading_path}" if heading_path else document_title
    head = f"{heading}\n{type_label}" if type_label else heading
    return f"{head}\n\n{(text or '').strip()}"[:MAX_EMBED_CHARS]


def document_text(title: str, type_label: str, description: str) -> str:
    """What is embedded for a document as a whole: what its card says."""
    parts = [title, type_label, (description or "").strip()]
    return "\n".join(part for part in parts if part)[:MAX_EMBED_CHARS]


# -- models --------------------------------------------------------------------------


class BedrockEmbedder:
    """Amazon Titan Text Embeddings V2, through the Bedrock runtime API.

    Credentials are boto3's: the ECS task's role, deployed; locally, whatever
    profile or environment boto3 finds. A query has a short timeout and one
    retry, since a reader is waiting and search falls back to words; filling
    the store has longer, with boto3's adaptive retries for throttling.
    """

    def __init__(self, model: str, dimensions: int, region: str):
        if model != TITAN_V2:
            raise ImproperlyConfigured(
                f"CONTENT_INDEX_EMBEDDING_MODEL {model!r} is not supported: only {TITAN_V2} is."
            )
        if dimensions not in TITAN_V2_DIMENSIONS:
            raise ImproperlyConfigured(
                f"CONTENT_INDEX_EMBEDDING_DIMENSIONS must be one of {TITAN_V2_DIMENSIONS}."
            )
        self.model = model
        self.dimensions = dimensions
        self.region = region
        self._clients = {}

    @property
    def key(self) -> str:
        """What ``ContentEmbedding.model`` records: the model and its vectors' length."""
        return f"{self.model}@{self.dimensions}"

    def _client(self, query: bool):
        if query not in self._clients:
            import boto3
            from botocore.config import Config

            config = (
                Config(connect_timeout=1, read_timeout=2, retries={"max_attempts": 2, "mode": "standard"})
                if query
                else Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 6, "mode": "adaptive"})
            )
            self._clients[query] = boto3.client("bedrock-runtime", region_name=self.region, config=config)
        return self._clients[query]

    def _embed_one(self, client, text: str) -> list[float]:
        from botocore.exceptions import BotoCoreError, ClientError

        body = {"inputText": text[:MAX_EMBED_CHARS], "dimensions": self.dimensions, "normalize": True}
        try:
            response = client.invoke_model(
                modelId=self.model,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
            return json.loads(response["body"].read())["embedding"]
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "ClientError")
            raise EmbeddingError(f"Bedrock refused the request ({code}).") from exc
        except (BotoCoreError, KeyError, ValueError) as exc:
            raise EmbeddingError(f"Bedrock could not be reached ({type(exc).__name__}).") from exc

    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        client = self._client(query)
        if len(texts) == 1:
            return [self._embed_one(client, texts[0])]
        # Titan takes one text a request.
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            return list(pool.map(lambda text: self._embed_one(client, text), texts))


class FakeEmbedder:
    """Words into a vector, for tests and for trying the search without a model.

    Each word, or the concept ``concepts`` maps it to, is a dimension, so two
    texts are alike as far as they share words or concepts: enough to show a
    paraphrase being found ({"money": "pay", "payments": "pay"}).
    """

    model = "fake"

    def __init__(self, concepts: dict[str, str] | None = None, dimensions: int = 256, fail: bool = False):
        self.concepts = concepts or {}
        self.dimensions = dimensions
        self.fail = fail
        self.calls: list[tuple[int, bool]] = []

    @property
    def key(self) -> str:
        return f"{self.model}@{self.dimensions}"

    def _vector(self, text: str) -> list[float]:
        counts = [0.0] * self.dimensions
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            if word in STOPWORDS:
                continue
            concept = self.concepts.get(word, word)
            index = 1 + int(hashlib.sha256(concept.encode()).hexdigest(), 16) % (self.dimensions - 1)
            counts[index] += 1.0
        if not any(counts):
            counts[0] = 1.0  # a text of no words: alike only to others of none
        norm = math.sqrt(sum(value * value for value in counts))
        return [value / norm for value in counts]

    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        self.calls.append((len(texts), query))
        if self.fail:
            raise EmbeddingError("The fake model was told to fail.")
        return [self._vector(text) for text in texts]


@lru_cache(maxsize=4)
def _bedrock(model: str, dimensions: int, region: str) -> BedrockEmbedder:
    return BedrockEmbedder(model, dimensions, region)


_fake = FakeEmbedder()


def get_embedder():
    """The configured model, or None when embeddings are off."""
    backend = settings.CONTENT_INDEX_EMBEDDINGS
    if not backend or backend == "none":
        return None
    if backend == "bedrock":
        return _bedrock(
            settings.CONTENT_INDEX_EMBEDDING_MODEL,
            settings.CONTENT_INDEX_EMBEDDING_DIMENSIONS,
            settings.CONTENT_INDEX_EMBEDDING_REGION,
        )
    if backend == "fake":
        return _fake
    raise ImproperlyConfigured(f"CONTENT_INDEX_EMBEDDINGS {backend!r} is not one of bedrock, fake or none.")


def embed_query(query: str, embedder) -> list[float]:
    """The query's vector, from the cache when it has been asked lately.

    The cache key is a hash of the query, and the value its vector: the text
    is kept nowhere. Raises ``EmbeddingError``.
    """
    key = "govuk-embed-query:" + text_hash(f"{embedder.key}\n{query}")
    vector = cache.get(key)
    if vector is None:
        vector = embedder.embed([query], query=True)[0]
        cache.set(key, vector, QUERY_CACHE_SECONDS)
    return vector


def cosine_similarity(a, b) -> float:
    """For vectors already normalised to length one, as the store's are."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


# -- filling the store ---------------------------------------------------------------


@dataclass
class EmbedResult:
    counts: Counter = field(default_factory=Counter)
    failures: list[str] = field(default_factory=list)


EMBED_OUTCOMES = ("embedded", "unchanged", "removed", "stale", "failed")


def wanted_texts() -> tuple[dict[str, str], int]:
    """Every text the index wants a vector for, by its hash; and how many rows
    are stale (their recorded hash is not of the text they would embed now)."""
    from govuk.models import IndexedChunk, IndexedDocument

    wanted: dict[str, str] = {}
    stale = 0
    documents = IndexedDocument.objects.exclude(embedding_hash="").values_list(
        "embedding_hash", "title", "type_label", "description"
    )
    for recorded, title, type_label, description in documents.iterator():
        text = document_text(title, type_label, description)
        if text_hash(text) != recorded:
            stale += 1
            continue
        wanted[recorded] = text
    chunks = IndexedChunk.objects.exclude(embedding_hash="").values_list(
        "embedding_hash", "document__title", "document__type_label", "heading_path", "text"
    )
    for recorded, title, type_label, heading_path, text in chunks.iterator():
        embedded = section_text(title, type_label, heading_path, text)
        if text_hash(embedded) != recorded:
            stale += 1
            continue
        wanted[recorded] = embedded
    return wanted, stale


def embed_missing(*, embedder=None, dry_run: bool = False) -> EmbedResult:
    """Embed every text the index wants that has no vector from this model,
    and remove the vectors no row wants any more.

    Stale rows (see ``wanted_texts``) mean the recipe for what is embedded
    changed without ``INDEX_VERSION`` being bumped; a forced rebuild mends them.
    """
    from govuk.models import ContentEmbedding

    embedder = embedder or get_embedder()
    if embedder is None:
        raise ImproperlyConfigured("Embeddings are off (CONTENT_INDEX_EMBEDDINGS).")
    result = EmbedResult()
    wanted, result.counts["stale"] = wanted_texts()
    have = set(ContentEmbedding.objects.filter(model=embedder.key).values_list("text_hash", flat=True))
    missing = [digest for digest in wanted if digest not in have]
    result.counts["unchanged"] = len(wanted) - len(missing)
    unused = [
        pk
        for pk, digest in ContentEmbedding.objects.values_list("pk", "text_hash").iterator()
        if digest not in wanted
    ]
    if dry_run:
        result.counts["embedded"] = len(missing)
        result.counts["removed"] = len(unused)
        return result

    failed_in_a_row = 0
    for start in range(0, len(missing), BATCH_SIZE):
        batch = missing[start : start + BATCH_SIZE]
        try:
            vectors = embedder.embed([wanted[digest] for digest in batch])
        except EmbeddingError as exc:
            result.counts["failed"] += len(batch)
            result.failures.append(str(exc))
            failed_in_a_row += 1
            if failed_in_a_row >= MAX_FAILED_BATCHES:
                left = len(missing) - start - len(batch)
                result.counts["failed"] += left
                result.failures.append(f"Stopped after {failed_in_a_row} failed batches in a row; {left} not tried.")
                break
            continue
        failed_in_a_row = 0
        ContentEmbedding.objects.bulk_create(
            [
                ContentEmbedding(model=embedder.key, text_hash=digest, vector=vector)
                for digest, vector in zip(batch, vectors)
            ],
            ignore_conflicts=True,
        )
        result.counts["embedded"] += len(batch)

    for start in range(0, len(unused), 500):
        ContentEmbedding.objects.filter(pk__in=unused[start : start + 500]).delete()
    result.counts["removed"] = len(unused)
    logger.info("Embedded the content index", extra={"model": embedder.key, **dict(result.counts)})
    return result

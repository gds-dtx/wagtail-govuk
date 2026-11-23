"""Vectors for the index's sections and documents, so search can match meaning.

Publishing never embeds; ``embed_missing`` fills the store. Never log or raise with the text embedded.
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
from django.db import DatabaseError
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)

# Well below Titan V2's 8,192-token limit.
MAX_EMBED_CHARS = 20_000
QUERY_CACHE_SECONDS = 24 * 60 * 60
BATCH_SIZE = 32
WORKERS = 4
# Give up after this many failed batches in a row.
MAX_FAILED_BATCHES = 3

TITAN_V2 = "amazon.titan-embed-text-v2:0"
TITAN_V2_DIMENSIONS = (256, 512, 1024)

# Also used by the any-word search in ``search``.
STOPWORDS = frozenset(
    """a about am an and any are as at be been being but by can could did do does doing for
    from had has have having how i i'd i'm if in into is it its just m me my of on or our out
    please s should so some t than that the their them then there these they this those to
    trying up us was we were what when where which who why will with would you your""".split()
)


class EmbeddingError(Exception):
    """The message never holds the text."""


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def section_text(document_title: str, type_label: str, heading_path: str, text: str) -> str:
    heading = f"{document_title} › {heading_path}" if heading_path else document_title
    head = f"{heading}\n{type_label}" if type_label else heading
    return f"{head}\n\n{(text or '').strip()}"[:MAX_EMBED_CHARS]


def document_text(title: str, type_label: str, description: str) -> str:
    parts = [title, type_label, (description or "").strip()]
    return "\n".join(part for part in parts if part)[:MAX_EMBED_CHARS]


# -- models --------------------------------------------------------------------------


class BedrockEmbedder:
    """Titan Text Embeddings V2 on Bedrock.

    Queries get a short timeout and one retry (a reader waits and search falls
    back to words); filling the store gets adaptive retries for throttling.
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

    def _embed_one(self, client, text: str) -> tuple[list[float], int]:
        """The vector, and the tokens Titan counted for it."""
        from botocore.exceptions import BotoCoreError, ClientError

        body = {"inputText": text[:MAX_EMBED_CHARS], "dimensions": self.dimensions, "normalize": True}
        try:
            response = client.invoke_model(
                modelId=self.model,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
            payload = json.loads(response["body"].read())
            return payload["embedding"], int(payload.get("inputTextTokenCount") or 0)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "ClientError")
            raise EmbeddingError(f"Bedrock refused the request ({code}).") from exc
        except (BotoCoreError, KeyError, ValueError) as exc:
            raise EmbeddingError(f"Bedrock could not be reached ({type(exc).__name__}).") from exc

    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        client = self._client(query)
        if len(texts) == 1:
            results = [self._embed_one(client, texts[0])]
        else:
            # Titan takes one text a request.
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                results = list(pool.map(lambda text: self._embed_one(client, text), texts))
        record_usage(len(results), sum(tokens for _, tokens in results))
        return [vector for vector, _ in results]


def record_usage(calls: int, tokens: int) -> None:
    """Titan's use, beside Claude's on the day's ``AISearchUsage`` row. Never stops an embedding."""
    from govuk.models import AISearchUsage

    day = timezone.now().date()
    try:
        AISearchUsage.ensure(day)
        AISearchUsage.objects.filter(day=day).update(
            embedding_calls=F("embedding_calls") + calls, embedding_tokens=F("embedding_tokens") + tokens
        )
    except DatabaseError:
        logger.warning("Titan's use could not be recorded")


class FakeEmbedder:
    """Bag-of-words vectors for tests; ``concepts`` maps words to shared dimensions."""

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
            counts[0] = 1.0  # avoid a zero vector
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
    """Cached under a hash of the query, so the text is stored nowhere. Raises ``EmbeddingError``."""
    key = "govuk-embed-query:" + text_hash(f"{embedder.key}\n{query}")
    vector = cache.get(key)
    if vector is None:
        vector = embedder.embed([query], query=True)[0]
        cache.set(key, vector, QUERY_CACHE_SECONDS)
    return vector


def cosine_similarity(a, b) -> float:
    """Assumes unit-length vectors, as the store's are."""
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
    """Texts wanting a vector, by hash, and the count of rows whose recorded hash is stale."""
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
    """Embed texts with no vector from this model and remove unused vectors.

    Stale rows mean the embedded text changed without an ``INDEX_VERSION`` bump.
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

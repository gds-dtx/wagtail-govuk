"""Fetching external catalogue entries' source pages into the content index.

``harvest_source`` fetches one address, stores how it went on its
``HarvestedSource`` row, and re-indexes the entries that use it.
``harvest_all`` does that for every live external entry, one host at a time,
and removes the rows no entry uses any more. Run by
``manage.py harvest_external_sources`` on a timer, by the task queued when an
external entry is published with a new source, and by the admin's Fetch now.

A failed or refused attempt keeps the last text fetched: a source that is
briefly down should not empty the index of it. A page that is read and found
to have no text, or that is no longer a page the index reads, does empty it.
"""

import hashlib
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone
from wagtail.models import Page

from govuk.models import ContentEntryExternalPage, HarvestedSource

from . import indexer
from .extract import eligible_pages
from .fetch import FetchError, Robots
from .sources import ROBOTS_REFUSAL, NoText, NotModified, SourceSkipped, read_source

logger = logging.getLogger(__name__)

# Seconds between two requests to the same host in one run.
HOST_DELAY_SECONDS = 1.0
REFUSAL_STATUSES = {401, 403, 429, ROBOTS_REFUSAL}


@dataclass
class HarvestResult:
    counts: Counter = field(default_factory=Counter)
    lines: list[str] = field(default_factory=list)


def source_urls() -> list[str]:
    """Every address a live external entry readers can reach points at."""
    entries = ContentEntryExternalPage.objects.filter(pk__in=eligible_pages().values("pk"))
    return sorted({url for url in entries.values_list("source_url", flat=True) if url})


def harvest_source(url: str, *, fetcher=None, robots=None, force: bool = False) -> HarvestedSource:
    source, _ = HarvestedSource.objects.get_or_create(url=url)
    previous_hash = source.content_hash
    now = timezone.now()
    source.fetched_at = now
    source.http_status = None
    try:
        text = read_source(
            url,
            fetcher=fetcher,
            robots=robots if robots is not None else Robots(fetcher),
            etag="" if force else source.etag,
            last_modified="" if force else source.last_modified,
        )
    except NotModified:
        source.status, source.reason = HarvestedSource.Status.OK, ""
        source.succeeded_at, source.failures, source.http_status = now, 0, 304
    except FetchError as exc:
        refused = exc.status in REFUSAL_STATUSES
        source.status = HarvestedSource.Status.BLOCKED if refused else HarvestedSource.Status.FAILED
        source.reason = str(exc)[:300]
        source.http_status = exc.status if exc.status != ROBOTS_REFUSAL else None
        source.failures += 1
    except (SourceSkipped, NoText) as exc:
        source.status = (
            HarvestedSource.Status.SKIPPED if isinstance(exc, SourceSkipped) else HarvestedSource.Status.NO_TEXT
        )
        source.reason = str(exc)[:300]
        source.title = source.markdown = source.content_hash = source.etag = source.last_modified = ""
        source.source_updated_at = None
        source.succeeded_at, source.failures = now, 0
    else:
        source.status, source.reason = HarvestedSource.Status.OK, ""
        source.http_status = 200
        source.fetched_url = text.fetched_url[:500]
        source.title = text.title[:255]
        source.markdown = text.markdown
        source.content_hash = hashlib.sha256(text.markdown.encode("utf-8")).hexdigest()
        source.etag, source.last_modified = text.etag[:200], text.last_modified[:100]
        source.source_updated_at = text.updated_at
        source.succeeded_at, source.failures = now, 0
    with transaction.atomic():
        source.save()
    if source.content_hash != previous_hash or source.status != HarvestedSource.Status.OK:
        # The reason a source could not be fetched is shown with its entries.
        source_changed(url)
    logger.info(
        "Harvested an external source",
        extra={"host": urlsplit(url).hostname, "status": source.status, "http_status": source.http_status},
    )
    return source


def source_changed(url: str) -> None:
    """Re-index the entries whose source is ``url``."""
    indexer.index_pages(
        Page.objects.filter(pk__in=ContentEntryExternalPage.objects.filter(source_url=url).values("pk"))
    )


def harvest_all(*, fetcher=None, force: bool = False, urls=None, delay: float | None = None) -> HarvestResult:
    delay = HOST_DELAY_SECONDS if delay is None else delay
    result = HarvestResult()
    robots = Robots(fetcher)
    last_request: dict[str, float] = {}
    for url in urls if urls is not None else source_urls():
        host = urlsplit(url).hostname or ""
        wait = last_request.get(host, 0) + delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            source = harvest_source(url, fetcher=fetcher, robots=robots, force=force)
        except Exception as exc:
            logger.exception("Could not harvest an external source", extra={"host": host})
            result.counts["error"] += 1
            result.lines.append(f"error    {url}: {exc}")
            continue
        finally:
            last_request[host] = time.monotonic()
        result.counts[source.status] += 1
        detail = f"{len(source.markdown):,} characters" if source.markdown else source.reason
        result.lines.append(f"{source.status:<8} {url}" + (f": {detail}" if detail else ""))
    if urls is None:
        # Sources no live entry points at any more.
        removed, _ = HarvestedSource.objects.exclude(url__in=source_urls()).delete()
        result.counts["removed"] += removed
    return result

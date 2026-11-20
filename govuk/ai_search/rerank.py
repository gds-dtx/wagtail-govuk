"""Claude re-ranks search's matches; on any failure the page ranks by search alone."""

import hashlib
import logging
import time
from dataclasses import dataclass, field

from django.conf import settings
from django.core.cache import cache

from . import prompts, usage
from .claude import ClaudeError, ClaudeRefused
from .scope import scopes_for

logger = logging.getLogger(__name__)

MAX_CHOSEN = 6
MAX_TOKENS = 4000

OK, REFUSED, FAILED = "ok", "refused", "failed"


@dataclass
class Reranked:
    status: str
    hits: list = field(default_factory=list)
    in_scope: bool = True
    # By document id, for the evaluation.
    reasons: dict = field(default_factory=dict)
    tokens: int = 0


def rerank(question: str, hits: list, claude, *, site=None) -> Reranked:
    instructions = prompts.instructions("rerank", site)
    candidates = [(f"d{number}", hit) for number, hit in enumerate(hits[: settings.AI_SEARCH_RERANK_CANDIDATES], start=1)]
    by_key = dict(candidates)
    prompt = prompts.rerank_prompt(question, candidates, scopes_for(hit.document for _, hit in candidates))
    # Keyed on everything Claude is sent, so a repeat costs nothing and a change is never served stale.
    key = "govuk-ai-search-rerank:" + hashlib.sha256(
        "\n".join((str(claude.model), instructions.text, prompt)).encode("utf-8")
    ).hexdigest()
    started = time.monotonic()
    data, tokens = cache.get(key), 0
    cached = data is not None
    if not cached:
        try:
            reply = claude.complete(
                purpose="rerank",
                system=instructions.text,
                prompt=prompt,
                schema=prompts.RERANK_SCHEMA,
                max_tokens=MAX_TOKENS,
                timeout=settings.AI_SEARCH_RERANK_TIMEOUT_SECONDS,
                context=list(by_key),
            )
        except ClaudeRefused as exc:
            usage.record(exc.usage, outcome="refused")
            logger.info("AI search re-rank refused: %s", exc)
            return Reranked(REFUSED, tokens=exc.usage.total if exc.usage else 0)
        except ClaudeError as exc:
            usage.record(exc.usage, outcome="failed")
            logger.warning("AI search re-rank failed: %s", exc)
            return Reranked(FAILED, tokens=exc.usage.total if exc.usage else 0)
        usage.record(reply.usage)
        data, tokens = reply.data, reply.usage.total
        cache.set(key, data, settings.AI_SEARCH_RERANK_CACHE_SECONDS)

    in_scope = bool(data.get("in_scope"))
    chosen, reasons = [], {}
    for item in data.get("relevant") or []:
        if not isinstance(item, dict):
            continue
        hit = by_key.get(str(item.get("id", "")).strip())
        if hit is None or hit in chosen:
            continue
        chosen.append(hit)
        reasons[hit.document.pk] = str(item.get("why") or "")[:300]
        if len(chosen) >= MAX_CHOSEN:
            break
    if not in_scope:
        chosen = []
    logger.info(
        "AI search re-rank",
        extra={
            "candidates": len(candidates),
            "chosen": len(chosen),
            "in_scope": in_scope,
            "tokens": tokens,
            "cached": cached,
            "prompt_version": instructions.tag,
            "duration_ms": round((time.monotonic() - started) * 1000),
        },
    )
    return Reranked(OK, chosen, in_scope, reasons, tokens)

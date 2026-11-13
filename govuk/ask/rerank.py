"""Claude re-ranks search's matches: which answer the question, best first.

Search finds up to ``ASK_RERANK_CANDIDATES`` matches; Claude is shown each
under a marker (d1, d2...) and answers with the markers of those that help,
in order, and whether the question is one the site is for at all. Markers it
was not given are dropped. Anything else, from a timeout to a refusal, and
``rerank`` says so, and the page ranks by search alone.
"""

import logging
import time
from dataclasses import dataclass, field

from django.conf import settings

from . import prompts, usage
from .claude import ClaudeError, ClaudeRefused

logger = logging.getLogger(__name__)

MAX_CHOSEN = 6
MAX_TOKENS = 4000

OK, REFUSED, FAILED = "ok", "refused", "failed"


@dataclass
class Reranked:
    status: str
    hits: list = field(default_factory=list)
    in_scope: bool = True
    # Why each chosen match helps, by document id: for the evaluation.
    reasons: dict = field(default_factory=dict)
    tokens: int = 0


def rerank(question: str, hits: list, claude) -> Reranked:
    candidates = [(f"d{number}", hit) for number, hit in enumerate(hits[: settings.ASK_RERANK_CANDIDATES], start=1)]
    by_key = dict(candidates)
    started = time.monotonic()
    try:
        reply = claude.complete(
            purpose="rerank",
            system=prompts.RERANK_SYSTEM,
            prompt=prompts.rerank_prompt(question, candidates),
            schema=prompts.RERANK_SCHEMA,
            max_tokens=MAX_TOKENS,
            timeout=settings.ASK_RERANK_TIMEOUT_SECONDS,
            context=list(by_key),
        )
    except ClaudeRefused as exc:
        usage.record(exc.usage, outcome="refused")
        logger.info("Ask re-rank refused: %s", exc)
        return Reranked(REFUSED, tokens=exc.usage.total if exc.usage else 0)
    except ClaudeError as exc:
        usage.record(exc.usage, outcome="failed")
        logger.warning("Ask re-rank failed: %s", exc)
        return Reranked(FAILED, tokens=exc.usage.total if exc.usage else 0)
    usage.record(reply.usage)

    in_scope = bool(reply.data.get("in_scope"))
    chosen, reasons = [], {}
    for item in reply.data.get("relevant") or []:
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
        "Ask re-rank",
        extra={
            "candidates": len(candidates),
            "chosen": len(chosen),
            "in_scope": in_scope,
            "tokens": reply.usage.total,
            "prompt_version": prompts.PROMPT_VERSION,
            "duration_ms": round((time.monotonic() - started) * 1000),
        },
    )
    return Reranked(OK, chosen, in_scope, reasons, reply.usage.total)

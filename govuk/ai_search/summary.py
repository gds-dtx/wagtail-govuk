"""Claude's summary of the matched cards, from a signed ticket.

The ticket is signed so the endpoint only summarises what search found on this
site; page text is read from the index, never the request. Claude's markers
([d1], [d1.2]) become links and unknown ones are dropped. Neither the question
nor the summary is logged.
"""

import hashlib
import logging
import re
import secrets
import time
from dataclasses import dataclass
from html import escape

import nh3
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from markdown_it import MarkdownIt

from govuk.models import IndexedChunk

from . import claims, prompts, usage
from .claude import ClaudeError, ClaudeRefused, get_claude

logger = logging.getLogger(__name__)

TICKET_SALT = "govuk.ai_search.summary"
TICKET_MAX_AGE = 10 * 60
MAX_TOKENS = 6000
MARKER = re.compile(r"\[(d\d{1,2}(?:\.\d{1,2})?)\]")
ALLOWED_TAGS = {"p", "ul", "ol", "li", "strong", "em"}

OK, NONE, UNAVAILABLE = "ok", "none", "unavailable"


class TicketError(ValueError):
    pass


@dataclass
class Summary:
    status: str  # OK, NONE (nothing to say) or UNAVAILABLE
    html: str = ""


def make_ticket(question: str, cards: list[dict]) -> str:
    """Each ticket has its own id, so it is good once."""
    return signing.dumps(
        {
            "v": prompts.PROMPT_VERSION,
            "j": secrets.token_urlsafe(16),
            "q": question,
            "c": [
                {
                    "t": card["title"],
                    "u": card["url"],
                    "y": card.get("type") or "",
                    "x": (card.get("description") or "")[:500],
                    "a": card.get("applies_to") or "",
                    "s": list(dict.fromkeys(card.get("source_sections") or []))[:6],
                }
                for card in cards
            ],
        },
        salt=TICKET_SALT,
        compress=True,
    )


def read_ticket(ticket: str) -> dict:
    try:
        data = signing.loads(ticket or "", salt=TICKET_SALT, max_age=TICKET_MAX_AGE)
    except signing.SignatureExpired as exc:
        raise TicketError("The ticket has expired.") from exc
    except signing.BadSignature as exc:
        raise TicketError("The ticket is not one this site made.") from exc
    if not isinstance(data, dict) or not data.get("q") or not data.get("j") or not isinstance(data.get("c"), list):
        raise TicketError("The ticket is not one this site made.")
    return data


def _path(document) -> str:
    if document.origin == document.Origin.EXTERNAL:
        return document.source_url
    return document.url_path


def _pages(cards: list[dict]) -> list[dict]:
    """Each card with its matched sections' text; sections from other pages become parts."""
    wanted = [section for card in cards for section in card.get("s", [])]
    chunks = {chunk.pk: chunk for chunk in IndexedChunk.objects.filter(pk__in=wanted).select_related("document")}
    pages = []
    for number, card in enumerate(cards, start=1):
        key = f"d{number}"
        own, parts = [], {}
        for section_id in card.get("s", []):
            chunk = chunks.get(section_id)
            if chunk is None:  # unpublished, or rebuilt, since the search
                continue
            document = chunk.document
            if document.title == card["t"]:
                own.append({"heading": chunk.heading_path or document.title, "text": chunk.text})
                continue
            part = parts.get(document.pk)
            if part is None:
                part = parts[document.pk] = {
                    "key": f"{key}.{len(parts) + 1}",
                    "title": document.title,
                    "url": _path(document),
                    "sections": [],
                }
            part["sections"].append({"heading": chunk.heading_path or document.title, "text": chunk.text})
        pages.append(
            {
                "key": key,
                "title": card["t"],
                "url": card.get("u"),
                "type": card.get("y"),
                "applies_to": card.get("a"),
                "description": card.get("x"),
                "sections": own,
                "parts": list(parts.values()),
            }
        )
    return pages


def links_for(pages: list[dict]) -> dict[str, tuple[str, str]]:
    links = {}
    for page in pages:
        links[page["key"]] = (page["title"], page.get("url") or "")
        for part in page.get("parts", []):
            links[part["key"]] = (part["title"], part["url"])
    return links


def card_links(cards: list[dict]) -> dict[str, tuple[str, str]]:
    return {f"d{number}": (card["t"], card.get("u") or "") for number, card in enumerate(cards, start=1)}


def _link(title: str, url: str) -> str:
    title = escape(title)
    if not (url.startswith("/") or url.startswith("https://") or url.startswith("http://")):
        return title
    return f'<a class="govuk-link" href="{escape(url, quote=True)}">{title}</a>'


def _without_repeats(html: str, title: str) -> str:
    """Drop a title Claude repeats after its own link, despite being told not to."""
    name = r"(?:the\s+)?" + re.escape(escape(title))
    link_end = re.escape(f">{escape(title)}</a>")
    # ", which describes GOV.UK Pay." / ", the GOV.UK Pay." / ", GOV.UK Pay."
    html = re.sub(
        rf"({link_end}),\s*(?:(?:which|that)\s+(?:describes|covers|explains|is)\s+)?{name}(?=[.,;:])",
        r"\1",
        html,
        flags=re.IGNORECASE,
    )
    # "<a>GOV.UK Design System</a> GOV.UK Design System" / "... on Internal Access"
    return re.sub(rf"({link_end})\s+(?:on\s+)?{name}\b", r"\1", html, flags=re.IGNORECASE)


def render(text: str, links: dict[str, tuple[str, str]]) -> str:
    """Claude's text as safe HTML, each valid marker a link."""
    html = MarkdownIt("commonmark", {"html": False}).render(str(text or ""))
    html = nh3.clean(html, tags=ALLOWED_TAGS, attributes={})

    def link(match) -> str:
        target = links.get(match.group(1))
        return _link(*target) if target else ""

    html = MARKER.sub(link, html)
    for title, _ in links.values():
        html = _without_repeats(html, title)
    html = re.sub(r"[ \t]+([.,;:)])", r"\1", html)
    html = re.sub(r"[ \t]{2,}", " ", html).strip()
    text_only = re.sub(r"<[^>]+>", "", html).strip()
    return html if text_only else ""


def _cache_key(data: dict, model: str, instructions: str) -> str:
    sources = repr([(card["t"], card.get("u"), card.get("a"), card.get("s")) for card in data["c"]])
    digest = hashlib.sha256(
        f"{model}\n{prompts.PROMPT_VERSION}\n{instructions}\n{data['q']}\n{sources}".encode()
    ).hexdigest()
    return f"govuk-ask-summary:{digest}"


def summarise(ticket: str, site=None, *, check_budget: bool = True) -> Summary:
    """Raises ``TicketError`` for a bad or used ticket. ``check_budget`` off only for evaluation runs."""
    data = read_ticket(ticket)
    if not claims.claim(claims.SUMMARY, data["j"]):
        raise TicketError("The ticket has been used.")
    claude = get_claude()
    if claude is None or not data["c"]:
        return Summary(UNAVAILABLE)
    instructions = prompts.instructions("summary", site)
    key = _cache_key(data, claude.model, instructions.text)
    cached = cache.get(key)
    if cached is not None:
        return Summary(*cached)
    if check_budget and usage.over_budget():
        return Summary(UNAVAILABLE)
    pages = _pages(data["c"])
    started = time.monotonic()
    try:
        reply = claude.complete(
            purpose="summary",
            system=instructions.text,
            prompt=prompts.summary_prompt(data["q"], pages),
            schema=prompts.SUMMARY_SCHEMA,
            max_tokens=MAX_TOKENS,
            timeout=settings.AI_SEARCH_SUMMARY_TIMEOUT_SECONDS,
            context=[page["key"] for page in pages],
        )
    except ClaudeRefused as exc:
        usage.record(exc.usage, outcome="refused")
        logger.info("AI search summary refused: %s", exc)
        return Summary(UNAVAILABLE)
    except ClaudeError as exc:
        usage.record(exc.usage, outcome="failed")
        logger.warning("AI search summary failed: %s", exc)
        return Summary(UNAVAILABLE)
    usage.record(reply.usage)
    html = render(reply.data.get("summary", ""), links_for(pages))
    result = Summary(OK if html else NONE, html)
    cache.set(key, (result.status, result.html), settings.AI_SEARCH_SUMMARY_CACHE_SECONDS)
    logger.info(
        "AI search summary",
        extra={
            "status": result.status,
            "cards": len(data["c"]),
            "tokens": reply.usage.total,
            "prompt_version": instructions.tag,
            "duration_ms": round((time.monotonic() - started) * 1000),
        },
    )
    return result


def summary_enabled(site) -> bool:
    from govuk.features import ai_enabled
    from govuk.models import SearchSettings

    return bool(ai_enabled() and get_claude() is not None and SearchSettings.for_site(site).show_ai_summary)

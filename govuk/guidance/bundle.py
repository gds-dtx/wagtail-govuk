"""The shape every source format is read into, and the path rules they share.

A source repo is either a ``guidance.yml`` manifest repo or a tech-docs
(Middleman) repo such as The GDS Way. Each adapter reads its own layout into a
``GuidanceBundle``; from there on rendering, storing and serving are the same
whichever format the guidance came from.
"""

import calendar
import posixpath
import re
from dataclasses import dataclass, field
from datetime import date, timedelta


class GuidanceSyncError(RuntimeError):
    """Raised when a source repo cannot be fetched, read or rendered."""


@dataclass
class SourceDocument:
    """One page of guidance as the source repo has it, before rendering.

    ``path`` is the route under the collection page (``standards/alerting``;
    empty for the index). ``url_path`` is where the source's own site put it
    (``/standards/alerting.html``), which is what relative links in its body
    are relative to.
    """

    path: str
    source_path: str
    url_path: str
    title: str
    markdown: str
    metadata: dict = field(default_factory=dict)


@dataclass
class GuidanceBundle:
    """A whole source repo, read into the shape the renderer expects.

    Navigation and redirect targets are left as the hrefs the source wrote,
    each with the ``url_path`` it is relative to, because only the renderer
    knows every document and so can tell a page from a file from a broken link.
    """

    format: str
    site: dict
    navigation: list[dict]
    sections: list[dict]
    documents: list[SourceDocument]
    redirects: dict[str, str]
    content_root: str
    files: set[str]
    anchor_style: str = "github"
    warnings: list[str] = field(default_factory=list)


# Longest first, so ``x.html.md.erb`` loses its whole extension.
_TEMPLATE_SUFFIXES = (".html.md.erb", ".html.erb", ".html.md", ".md.erb", ".md", ".html")
_INDEX_NAMES = ("index", "README")


def clean_path(url_path: str) -> str:
    """The route a source URL or file path is served at.

    ``/standards/alerting.html``, ``standards/alerting.md`` and
    ``standards/alerting/`` are all ``standards/alerting``; an ``index`` or
    ``README`` is its directory, so ``nodejs/index.html`` is ``nodejs`` and the
    site's own index is the empty string.
    """
    value = url_path.split("#", 1)[0].split("?", 1)[0].strip()
    value = posixpath.normpath("/" + value).lstrip("/")
    if value in ("", "."):
        return ""
    for suffix in _TEMPLATE_SUFFIXES:
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    head, _, tail = value.rpartition("/")
    if tail in _INDEX_NAMES:
        value = head
    return value.strip("/")


def split_frontmatter(text: str) -> tuple[str, str]:
    """``(frontmatter_yaml, body)``; the frontmatter is empty when there is none."""
    match = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", text, re.S)
    if not match:
        return "", text
    return match.group(1), text[match.end() :]


def iso_date(value) -> str:
    """A frontmatter date as ``YYYY-MM-DD``, or empty if it is not one."""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError:
            return ""
    return ""


_REVIEW_IN = re.compile(r"^\s*(\d+)\s*(day|week|month|year)s?\s*$", re.I)


def next_review_date(last_reviewed_on: str, review_in: str) -> str:
    """``last_reviewed_on`` plus a ``review_in`` such as ``6 months``.

    The tech-docs page-expiry rule, so a page here is due for review on the
    same day the source's own site says it is.
    """
    if not last_reviewed_on or not review_in:
        return ""
    match = _REVIEW_IN.match(str(review_in))
    if not match:
        return ""
    reviewed = date.fromisoformat(last_reviewed_on)
    amount, unit = int(match.group(1)), match.group(2).lower()
    if unit == "day":
        return (reviewed + timedelta(days=amount)).isoformat()
    if unit == "week":
        return (reviewed + timedelta(weeks=amount)).isoformat()
    months = amount * 12 if unit == "year" else amount
    month_index = reviewed.month - 1 + months
    year, month = reviewed.year + month_index // 12, month_index % 12 + 1
    day = min(reviewed.day, calendar.monthrange(year, month)[1])
    return date(year, month, day).isoformat()


def document_metadata(
    frontmatter: dict, *, default_owner: str = "", status: str = "current"
) -> dict:
    """The review, ownership and status metadata both formats record."""
    last_reviewed_on = iso_date(frontmatter.get("last_reviewed_on"))
    review_in = str(frontmatter.get("review_in") or "").strip()
    return {
        "description": str(frontmatter.get("description") or "").strip(),
        "last_reviewed_on": last_reviewed_on,
        "review_in": review_in,
        "next_review_on": next_review_date(last_reviewed_on, review_in),
        "owner": str(frontmatter.get("owner") or default_owner or "").strip(),
        "status": status,
        "hidden": bool(frontmatter.get("hidden")),
    }

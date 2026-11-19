"""The ``GuidanceBundle`` every source format is read into, and their shared path rules."""

import calendar
import posixpath
import re
from dataclasses import dataclass, field
from datetime import date, timedelta


class GuidanceSyncError(RuntimeError):
    """Raised when a source repo cannot be fetched, read or rendered."""


@dataclass
class SourceDocument:
    """One source page before rendering; relative links in it resolve against ``url_path``."""

    path: str
    source_path: str
    url_path: str
    title: str
    markdown: str
    metadata: dict = field(default_factory=dict)


@dataclass
class GuidanceBundle:
    """A whole source repo, read for the renderer.

    Nav and redirect hrefs are left as written: only the renderer knows every
    document, so only it can tell a page from a file from a broken link.
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
    # A manifest's ``requirements:`` or ``steps:``, as hrefs relative to the content root.
    sequence: list[str] = field(default_factory=list)


# Longest first, so ``x.html.md.erb`` loses its whole extension.
_TEMPLATE_SUFFIXES = (".html.md.erb", ".html.erb", ".html.md", ".md.erb", ".md", ".html")
_INDEX_NAMES = ("index", "README")


def clean_path(url_path: str) -> str:
    """The route a source URL or file path is served at; ``index``/``README`` map to their directory."""
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
    """``last_reviewed_on`` plus ``review_in`` (e.g. ``6 months``), by tech-docs' page-expiry rule."""
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
        "summary": str(frontmatter.get("summary") or "").strip(),
        "version": str(frontmatter.get("version") or "").strip(),
        "effective_from": iso_date(frontmatter.get("effective_from")),
        "last_reviewed_on": last_reviewed_on,
        "review_in": review_in,
        "next_review_on": next_review_date(last_reviewed_on, review_in),
        "owner": str(frontmatter.get("owner") or default_owner or "").strip(),
        "status": status,
        "hidden": bool(frontmatter.get("hidden")),
    }

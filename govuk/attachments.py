"""The GOV.UK attachment component, for the framework's CSV downloads.

CS32-3313 asks that "attachments follow GOV.UK's attachment component". That
is how every publication on GOV.UK signposts a file: a thumbnail showing the
kind of file it is, the title as the link, and the format and size beside it,
so somebody knows what they are about to open before they open it. The live
service does not do this -- its download page is three bare links to S3 -- so
this is the acceptance criterion rather than a copy of what is there now.

The links themselves stay ordinary links in the download page's rich text.
An editor writes ``Role content (CSV)`` pointing at ``/download/roles.csv``
and this turns it into the component when the page renders, which is the same
shape as the changelog fix in ``govuk.live_service_links``: the content stays
something an editor can write and read, and the component is a property of
how a CSV link is shown rather than markup they have to hand-build and keep
right.

Size is the awkward part. These CSVs have no stored size, because they are
generated at the moment of asking (``govuk.views.framework_csv_view``), and
that is exactly what keeps them in sync with the published content -- the
live service's copies are rebuilt on a schedule and were 16 days stale when
this was written. So the size is measured by generating the file and counting
the bytes, and cached, because generating the roles CSV costs about 160ms and
the download page should not pay that on every view.
"""

from __future__ import annotations

import re

from django.core.cache import cache
from django.template.loader import render_to_string
from django.urls import NoReverseMatch, reverse
from django.utils.safestring import mark_safe

from govuk.framework_csv import FRAMEWORK_CSV_DOWNLOADS

# Long enough that the download page is not regenerating three CSVs for every
# reader, short enough that an afternoon's editing is reflected the same day.
# The figure shown is rounded to three significant digits, so an edit has to
# move the file by roughly a kilobyte before the page could read differently
# at all.
SIZE_CACHE_SECONDS = 15 * 60
_SIZE_CACHE_KEY = "govuk:csv-download-size:%s"

_KIB = 1024
_MIB = 1024 * 1024
_GIB = 1024 * 1024 * 1024


class _ByteCounter:
    """A file-like object that keeps the length and throws the bytes away.

    The CSV writers take anything with ``write``. Counting encoded bytes
    rather than characters because that is what a reader downloads, and the
    role descriptions carry curly quotes and en dashes that cost more than one
    byte each.
    """

    def __init__(self) -> None:
        self.length = 0

    def write(self, data) -> int:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.length += len(data)
        return len(data)


def measure_csv_download(name: str) -> int | None:
    """Bytes the named CSV would come to, or None if there is no such CSV."""
    try:
        _label, write = FRAMEWORK_CSV_DOWNLOADS[name]
    except KeyError:
        return None

    counter = _ByteCounter()
    write(counter)
    return counter.length


def csv_download_size(name: str) -> int | None:
    """``measure_csv_download``, kept out of the page render where possible."""
    key = _SIZE_CACHE_KEY % name
    size = cache.get(key)
    if size is None:
        size = measure_csv_download(name)
        if size is None:
            return None
        cache.set(key, size, SIZE_CACHE_SECONDS)
    return size


def format_file_size(size: int | None) -> str:
    """A byte count the way GOV.UK writes one: "2.38 MB", "208 KB", "969 Bytes".

    Three significant digits and binary units, matching the attachment
    component elsewhere on GOV.UK so a reader who has seen one of these before
    reads this one the same way.
    """
    if size is None:
        return ""
    if size < _KIB:
        return "1 Byte" if size == 1 else f"{size} Bytes"

    for divisor, unit in ((_KIB, "KB"), (_MIB, "MB"), (_GIB, "GB")):
        value = size / divisor
        if value < _KIB or unit == "GB":
            # Three significant digits: 208 KB, 20.4 MB, 2.38 MB.
            if value >= 100:
                return f"{value:.0f} {unit}"
            if value >= 10:
                return f"{value:.1f} {unit}"
            return f"{value:.2f} {unit}"
    return ""


def attachment_html(*, name: str, title: str, href: str) -> str:
    """The component's markup for one CSV download."""
    return render_to_string(
        "includes/attachment.html",
        {
            "href": href,
            "title": title,
            "file_extension": "CSV",
            "file_extension_title": "Comma-separated Values",
            "file_size": format_file_size(csv_download_size(name)),
        },
    )


def _download_url(name: str) -> str | None:
    try:
        return reverse("govuk_framework_csv", kwargs={"name": name})
    except NoReverseMatch:
        return None


# A whole paragraph, because the component is a <section> and a <section>
# cannot sit inside a <p>: leaving the paragraph around it produces markup a
# browser silently reshuffles, and the reshuffled version is not what was
# tested. The link has to be the only thing in the paragraph -- a CSV link
# mentioned mid-sentence stays a link, which is right, because a sentence with
# a file card wedged into it reads as neither.
_CSV_PARAGRAPH = re.compile(
    r"<p>\s*<a\b[^>]*\bhref=\"(?P<href>/download/(?P<name>[\w-]+)\.csv)\"[^>]*>"
    r"(?P<title>.*?)</a>\s*</p>",
    re.IGNORECASE | re.DOTALL,
)

# Editors write "(CSV)" on the end to say what the file is, which is what the
# component's own metadata line now says. Left in it reads twice.
_TRAILING_FORMAT = re.compile(r"\s*\(\s*CSV\s*\)\s*$", re.IGNORECASE)


def rewrite_csv_download_links(html: str) -> str:
    """Turn each paragraph holding only a CSV download link into the component."""
    if "/download/" not in html:
        return html

    def replace(match: re.Match) -> str:
        name = match.group("name")
        if name not in FRAMEWORK_CSV_DOWNLOADS:
            return match.group(0)
        href = _download_url(name)
        if href is None or href != match.group("href"):
            return match.group(0)

        # The title is the link's own contents, already through the rich text
        # pipeline that produced the html being rewritten, so it is marked
        # safe for the same reason the surrounding body is: escaping it again
        # would show an editor's ampersand as "&amp;amp;".
        title = mark_safe(  # noqa: S308
            _TRAILING_FORMAT.sub("", match.group("title").strip())
        )
        return attachment_html(name=name, title=title, href=href)

    return _CSV_PARAGRAPH.sub(replace, html)

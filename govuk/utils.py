import html
import re

from django.utils.html import strip_tags

# Rich text carries no whitespace between one block and the next, so stripping
# the tags on their own runs the end of one into the start of the next: a "You
# can:" heading followed by a list came out as "You can:guide the organisation".
#
# A block begins as well as ends, and the two are not always both there to be
# matched. Indenting a list item in the editor nests the sublist inside the
# item that precedes it -- "<li>Lead a team<ul><li>and its budget" -- so the
# only thing standing between the two is the opening tag. Opening and closing
# alike separate what is either side of them; the collapse below means the
# spare spaces this adds in the ordinary case cost nothing.
_BLOCK_BOUNDARY = re.compile(
    r"</?(?:p|li|ul|ol|h[1-6]|div|section|article|table|tr|td|th|blockquote|dl|dd|dt)"
    r"\b[^>]*>"
    r"|<br\s*/?>",
    re.IGNORECASE,
)


def normalised_text(value) -> str:
    """Rich text as one line of plain text, for search summaries and metadata.

    The result is plain text, not HTML: unescaping turns "&lt;script&gt;" back
    into a tag, so every use of it has to be somewhere the value is escaped
    again. Template output is, being autoescaped, and so is JSON from the API.
    Anything reaching it through ``|safe`` or ``innerHTML`` would not be.
    """
    text = _BLOCK_BOUNDARY.sub(" ", str(value or ""))
    # strip_tags leaves character references as they were written, so an
    # apostrophe stored as "&#x27;" would reach the template and be escaped a
    # second time, showing the reference itself rather than the apostrophe.
    return " ".join(html.unescape(strip_tags(text)).split())

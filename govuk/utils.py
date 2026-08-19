from django.utils.html import strip_tags

# The largest row id the database can be asked about. Past it SQLite raises
# rather than returning nothing, and no row is named by it either way.
LARGEST_ROW_ID = 2**63 - 1


def normalised_text(value) -> str:
    return " ".join(strip_tags(str(value or "")).split())


def row_id_from_text(value) -> int | None:
    """The row id a piece of text names, or None if it names no row at all.

    ``isdigit`` is the wrong question to ask before ``int``, at both ends.
    "²" and "₂" are isdigit-True and ``int`` refuses them; a run of more than
    4,300 digits is decimal all the way down and ``int`` refuses that too.
    Past those, a number larger than the database holds is an error rather
    than an empty result. Each of the three arrived straight from a query
    string or an uploaded file, so each was a 500 where a filter matching
    nothing was wanted.
    """
    text = str(value or "").strip()
    if not text.isdecimal():
        return None
    try:
        row_id = int(text)
    except ValueError:  # More digits than int() will read.
        return None
    return row_id if row_id <= LARGEST_ROW_ID else None

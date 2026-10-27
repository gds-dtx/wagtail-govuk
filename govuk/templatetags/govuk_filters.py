from django import template
from django.utils.safestring import mark_safe
from wagtail.rich_text import expand_db_html

from govuk.capability_framework.attachments import rewrite_csv_download_links
from govuk.models.blocks import body_layout
from govuk.wagtail_hooks.rich_text import _wrap_table_in_scroll_region

register = template.Library()


ORDINAL_WORDS = {
    1: "first",
    2: "second",
    3: "third",
    4: "fourth",
    5: "fifth",
    6: "sixth",
    7: "seventh",
    8: "eighth",
    9: "ninth",
    10: "tenth",
}


@register.filter
def ordinal_word(value):
    """Spell a small position out, for screen reader wording like "the first of 4"."""
    try:
        return ORDINAL_WORDS[int(value)]
    except (KeyError, TypeError, ValueError):
        return value


@register.filter
def comma_number(value):
    if value in (None, ""):
        return "0"

    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return value


# The grid's horizontal alignment classes, and what each renders as. Left is the
# Design System's default and justify is not offered on GOV.UK, so neither adds
# anything; the grid's vertical alignment classes are not rendered.
TABLE_CELL_ALIGNMENT_CLASSES = {
    "htCenter": "app-table-cell--align-centre",
    "htRight": "app-table-cell--align-right",
}


@register.simple_tag(takes_context=True)
def cell_alignment_class(context, row_index, col_index, table_header=None):
    """The class a table cell's horizontal alignment adds, with a leading space.

    Wagtail keeps the grid's per-cell classes in the ``classnames`` context its
    table block renders with, counting the header as row 0; ``table_header`` is
    passed for body rows, as Wagtail's own ``cell_span`` takes it. Its own
    ``cell_classname`` writes the grid's class names out as they are, which
    style nothing on a GOV.UK page.
    """
    classnames = context.get("classnames") or {}
    if table_header is not None:
        row_index += 1
    for name in (classnames.get((row_index, col_index)) or "").split():
        if name in TABLE_CELL_ALIGNMENT_CLASSES:
            return " " + TABLE_CELL_ALIGNMENT_CLASSES[name]
    return ""


@register.simple_tag(takes_context=True)
def content_body_layout(context, page):
    """The page's body blocks, grouped into the segments the template lays out.

    Worked out at render time rather than in ``get_context`` because whether
    the page has a sidebar is only settled once every ``get_context`` in the
    chain has run: a framework content page forces the role navigation on after
    the shared framework context is built.
    """
    has_sidebar = bool(context.get("heading_navigation") or context.get("role_navigation"))
    card_filter = context.get("card_filter")
    return body_layout(
        getattr(page, "body", None),
        allow_full_width=not has_sidebar,
        visible_cards=card_filter.visible_cards if card_filter else None,
    )


@register.simple_tag
def changelog_note(entry):
    """A changelog note as rich text.

    This replaces ``{{ entry.note|richtext }}``.
    ``expand_db_html`` does what ``|richtext`` does: resolve Wagtail's own page
    and document links, which an editor adding a note through the admin writes.
    """
    note = getattr(entry, "note", entry)
    return mark_safe(expand_db_html(str(note or "")))


@register.filter
def raw_html_block(value):
    """A Raw HTML block's markup, written out as it is.

    A fragment that is a table is given the scrollable region every table on
    the site has.
    """
    return mark_safe(_wrap_table_in_scroll_region(str(value or "")))


@register.filter
def page_body(value):
    """Rich text in a page body, with CSV download links shown as attachments.

    This replaces ``|richtext`` for a content page's Text blocks. Everything
    ``|richtext`` does still happens -- Wagtail's own page, document and embed
    links are resolved -- and then a paragraph holding nothing but a link to
    one of the framework's CSVs becomes the GOV.UK attachment component. See
    ``govuk.capability_framework.attachments`` for why that is done here rather
    than asking an editor to write the markup.
    """
    return mark_safe(rewrite_csv_download_links(expand_db_html(str(value or ""))))

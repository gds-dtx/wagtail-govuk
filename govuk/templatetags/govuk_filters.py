from django import template
from django.utils.safestring import mark_safe
from wagtail.rich_text import expand_db_html

from govuk.capability_framework.attachments import rewrite_csv_download_links
from govuk.models.blocks import body_layout

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

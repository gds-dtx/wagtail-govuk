from django import template
from django.utils.safestring import mark_safe
from wagtail.models import Site
from wagtail.rich_text import expand_db_html

from govuk.live_service_links import (
    live_service_link_map,
    rewrite_live_service_links,
)

register = template.Library()

# render_context is Django's per-render scratch space, so the map is built
# once for a page rather than once for each of the 262 notes on the home page
# or each of the 185 skills on the A to Z.
_LINK_MAP_KEY = "govuk_live_service_link_map"


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
def changelog_note(context, entry):
    """A changelog note, with the live service's links pointed back here.

    This replaces ``{{ entry.note|richtext }}``. The notes were imported from
    the framework's published CSV, whose links are written the live service's
    way -- ``/role/data-engineer`` rather than ``/data-engineer/`` -- and
    ``changelog_note_to_html`` stored them as it found them. They resolve only
    while the seeded redirects are in place, so this makes the content right
    on its own terms; see ``govuk.live_service_links``.

    A tag rather than a filter because the map has to be built once per render
    and only a tag can reach ``render_context`` to keep it there.
    """
    note = getattr(entry, "note", entry)

    render_context = context.render_context
    if _LINK_MAP_KEY not in render_context:
        request = context.get("request")
        site = Site.find_for_request(request) if request else None
        if site is None:
            site = Site.objects.filter(is_default_site=True).first()
        render_context[_LINK_MAP_KEY] = (
            live_service_link_map(site) if site is not None else {}
        )

    rewritten = rewrite_live_service_links(
        str(note or ""), render_context[_LINK_MAP_KEY]
    )
    # What |richtext does: resolve Wagtail's own page and document links, which
    # an editor adding a note through the admin writes.
    return mark_safe(expand_db_html(rewritten))

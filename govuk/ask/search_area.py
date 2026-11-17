"""AI search: the magnifier button in place of the site search box, and the
search area it opens, to ask a question in (Ask).

Chosen in Settings > Customise > Search box ("Search type": AI search), where
the search box would be (header bar or service navigation). The area is the
lead text (Settings > Ask), the question box with the search placeholder
greyed in it, and the note not to include personal information. It opens at
the foot of a combined hero's band, and below the navigation otherwise
(``base.html``, ``includes/ask_search.html``).

Without JavaScript the button is a link to /ask/, where the area is always
open. It is open on the home page too, with "Always show AI search on the home
page", and the button is not shown where it is open.
"""

from govuk.models.blocks import ASK_DEFAULT_BUTTON_TEXT, ASK_EMPTY_ERROR

FIELD_ID = "app-ask-search-question"
PANEL_ID = "app-ask-search"


def search_area(
    request,
    customise,
    *,
    is_home: bool = False,
    ask_page: bool = False,
    question: str = "",
    error: str = "",
    in_main: bool = False,
) -> dict | None:
    """What ``base.html`` needs to draw AI search on a page, or None for none.

    ``ask_page`` is /ask/'s own: the area open whatever the search type, with
    ``question`` in its box and ``error`` under it; ``in_main`` when it has
    no combined hero to open in, so it is drawn at the top of the page's
    content with its lead text as the page's heading.
    """
    from govuk.content_index import ask_enabled

    if not ask_enabled():
        return None
    replaces = bool(customise) and customise.search_type == "ask"
    if not (replaces or ask_page):
        return None
    is_open = ask_page or (replaces and customise.ask_search_on_home_page and is_home)
    toggle = replaces and customise.search_location != "hidden" and not is_open
    from govuk.models import AskSettings
    from govuk.models.ask import DEFAULT_SEARCH_LEAD_TEXT

    lead = AskSettings.for_request(request).search_lead_text if (is_open or toggle) else ""
    return {
        # The header and navigation show the button (or nothing) in place of
        # the site search box.
        "replaces_site_search": replaces,
        "toggle": toggle,
        "open": is_open,
        "panel": is_open or toggle,
        "in_main": in_main,
        "panel_id": PANEL_ID,
        "field_id": FIELD_ID,
        "label": lead or DEFAULT_SEARCH_LEAD_TEXT,
        "hint": (customise.search_placeholder if customise else "") or "Search",
        "button_text": ASK_DEFAULT_BUTTON_TEXT,
        "question": question,
        "error": error,
        "empty_error": ASK_EMPTY_ERROR,
    }

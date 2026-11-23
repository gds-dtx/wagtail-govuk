"""AI search's magnifier button and the search area it opens."""

from govuk.models.settings import DEFAULT_AI_SEARCH_LEAD_TEXT

from . import preference
from .access import claude_offered, may_use_claude

FIELD_ID = "app-ai-search-question"
PANEL_ID = "app-ai-search"
BUTTON_TEXT = "Search"
EMPTY_ERROR = "Please enter a question or search query"
QUESTION_MAX_LENGTH = 300


def search_area(
    search_settings,
    *,
    is_home: bool = False,
    search_page: bool = False,
    question: str = "",
    error: str = "",
    in_main: bool = False,
    user=None,
    use_ai: bool | None = None,
) -> dict | None:
    """Template context for AI search, or None when it is off.

    ``in_main``: no combined hero, so the area tops the content with its lead
    text as the page heading. ``use_ai``: the reader's choice for the question
    just asked; otherwise their remembered choice.
    """
    if search_settings is None or not search_settings.ai_search_on:
        return None
    is_open = search_page or (search_settings.ai_search_on_home_page and is_home)
    toggle = search_settings.search_location != "hidden" and not is_open
    return {
        "toggle": toggle,
        "open": is_open,
        "panel": is_open or toggle,
        "in_main": in_main,
        "panel_id": PANEL_ID,
        "field_id": FIELD_ID,
        "label": search_settings.ai_search_lead_text or DEFAULT_AI_SEARCH_LEAD_TEXT,
        "hint": search_settings.search_placeholder or "Search",
        "button_text": BUTTON_TEXT,
        "max_length": QUESTION_MAX_LENGTH,
        "question": question,
        "error": error,
        "empty_error": EMPTY_ERROR,
        "ai_choice": ai_choice(search_settings, user, use_ai),
    }


def ai_choice(search_settings, user, use_ai: bool | None) -> dict | None:
    """The "Use AI search" checkbox, or None when Claude has nothing to offer."""
    if not claude_offered(search_settings):
        return None
    allowed = may_use_claude(search_settings, user)
    return {
        "id": f"{FIELD_ID}-ai",
        "enabled": allowed,
        "checked": allowed and (preference.remembered(user) if use_ai is None else use_ai),
    }

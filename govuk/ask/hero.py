"""The home page's hero, for /ask/ to draw its band as the home page does.

``base.html`` draws the hero from the page it is given (``self``): its
style, title and intro. /ask/ is no page, so it is given this in its place,
a copy of the home page's, with the question asked in the box and any error
about it under the box. Only for the combined hero with a question box: with
another style on the home page, /ask/ keeps its own form (``ask_page.html``).
"""

from govuk.models.pages import HeroStyle, HeroStyleMixin

ASK_FIELD_ID = "ask-question"


class HomeHero:
    """What ``base.html`` reads of a page for its hero, copied from the home page's."""

    # No page, so no "Edit this page" link in the footer.
    id = None

    hero_is_large_text = HeroStyleMixin.hero_is_large_text
    hero_is_styled = HeroStyleMixin.hero_is_styled
    hero_is_combined = HeroStyleMixin.hero_is_combined
    hero_uses_masthead = HeroStyleMixin.hero_uses_masthead
    hero_is_combined_ask = HeroStyleMixin.hero_is_combined_ask

    def __init__(self, home, box: dict, *, question: str = "", error: str = ""):
        self.hero_style = home.hero_style
        self.title = home.title
        self.hero_title = getattr(home, "hero_title", "")
        self.hero_intro = getattr(home, "hero_intro", "")
        self.search_description = home.search_description
        self.hero_question_box = {**box, "field_id": ASK_FIELD_ID, "question": question, "error": error}


def home_hero(site, *, question: str = "", error: str = "") -> HomeHero | None:
    """The home page's hero with ``question`` in its box, or None when the
    home page has no question box in its hero (or Ask is off)."""
    home = site.root_page.specific if site else None
    if home is None or getattr(home, "hero_style", None) != HeroStyle.COMBINED_ASK:
        return None
    box = home.hero_question_box
    if box is None:
        return None
    return HomeHero(home, box, question=question, error=error)

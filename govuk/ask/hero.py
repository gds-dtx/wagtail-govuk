"""The home page's hero, for /ask/ to draw its band as the home page does.

``base.html`` draws the hero from the page it is given (``self``): its
style, title and intro. /ask/ is no page, so it is given this in its place,
a copy of the home page's, when the home page has a combined hero: the search
area is then open at the foot of the band, with the question in its box. With
another style on the home page, /ask/ has no hero, and the search area is at
the top of its content (``govuk/ask/search_area.py``).
"""

from govuk.models.pages import HeroStyleMixin


class HomeHero:
    """What ``base.html`` reads of a page for its hero, copied from the home page's."""

    # No page, so no "Edit this page" link in the footer.
    id = None

    hero_is_large_text = HeroStyleMixin.hero_is_large_text
    hero_is_styled = HeroStyleMixin.hero_is_styled
    hero_is_combined = HeroStyleMixin.hero_is_combined
    hero_uses_masthead = HeroStyleMixin.hero_uses_masthead

    def __init__(self, home):
        self.hero_style = home.hero_style
        self.title = home.title
        self.hero_title = getattr(home, "hero_title", "")
        self.hero_intro = getattr(home, "hero_intro", "")
        self.search_description = home.search_description


def home_hero(site) -> HomeHero | None:
    """The home page's hero, or None when it has no combined hero."""
    home = site.root_page.specific if site else None
    if home is None or not getattr(home, "hero_is_combined", False):
        return None
    return HomeHero(home)

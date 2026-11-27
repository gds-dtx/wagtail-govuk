"""Card and tag colours, from each catalogue type's one colour.

A tag is drawn as GOV.UK Frontend draws its own: the colour at 50% shade on its 80% tint.
"""

DEFAULT_COLOUR = "#1d70b8"
# Cards for this site's own pages, roles and skills, which have no catalogue type.
SITE_PAGE_COLOUR = "#19609f"
WHITE = "#ffffff"
BLACK = "#0b0c0c"


def normalised(value) -> str:
    """``value`` as lower-case ``#rrggbb``, or "" if it is not a hex colour."""
    from govuk.models.blocks import _hex_colour

    return _hex_colour(value)


def _mix(colour: str, other: str, weight: float) -> str:
    """``weight`` of ``other`` mixed into ``colour``, rounding as Sass's mix() does."""
    channels = (
        int(int(colour[i : i + 2], 16) * (1 - weight) + int(other[i : i + 2], 16) * weight + 0.5)
        for i in (1, 3, 5)
    )
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def contrast(first: str, second: str) -> float:
    from govuk.models.blocks import _relative_luminance

    lighter, darker = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def tag_colours(colour: str) -> tuple[str, str]:
    """``(text, background)``: darker than half shade where a light colour needs it for 4.5:1."""
    background = _mix(colour, WHITE, 0.8)
    for weight in (0.5, 0.6, 0.7, 0.8, 0.9):
        text = _mix(colour, "#000000", weight)
        if contrast(text, background) >= 4.5:
            return text, background
    return BLACK, background


def star_colour(colour: str) -> str:
    """White, unless the corner is too light for it to reach 3:1 (WCAG's for graphics)."""
    return WHITE if contrast(WHITE, colour) >= 3 else BLACK


def colour_class(colour: str) -> str:
    """Keyed on the colour, so types of one colour share a rule."""
    return f"app-colour-{(normalised(colour) or DEFAULT_COLOUR)[1:]}"


def style_sheet(colours) -> str:
    rules = []
    for colour in sorted({normalised(colour) or DEFAULT_COLOUR for colour in colours}):
        text, background = tag_colours(colour)
        rules.append(
            # li: to outrank the card's own defaults wherever this sheet comes.
            f"li.{colour_class(colour)}{{--app-card-colour:{colour};"
            f"--app-card-star:{star_colour(colour)};--app-tag-text:{text};--app-tag-background:{background}}}"
        )
    return "".join(rules)


def catalogue_style_sheet() -> str:
    """Every type's colour, and this site's pages', for the cards wherever they are drawn."""
    from govuk.models import CatalogueType

    return style_sheet([*CatalogueType.objects.values_list("colour", flat=True), SITE_PAGE_COLOUR])

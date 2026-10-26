
from dataclasses import dataclass

from django.utils.text import slugify

from .blocks import CARD_ROW_BLOCK_TYPE


@dataclass
class CardFilter:
    """What the "Filter cards" control offers, and the cards it leaves showing.

    ``visible_cards`` is keyed by each card row's position in its stream, with
    the cards in that row the selected tag matches -- or every card in it when
    no tag is selected. A row whose cards are all filtered out maps to an empty
    list.
    """

    available_tags: list[dict[str, str]]
    selected_tag: dict[str, str] | None
    visible_cards: dict[int, list]


def card_tag_items(card) -> list[dict[str, str]]:
    """A card's tags as ``{"key", "value"}`` pairs, each tag once."""
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for tag in card.get("tags", []):
        if not tag:
            continue

        key = (getattr(tag, "slug", "") or getattr(tag, "key", "")).strip().lower()
        value = (getattr(tag, "name", "") or getattr(tag, "value", "")).strip()

        if not key and isinstance(value, str):
            key = slugify(value).strip().lower()
        if not value and key:
            value = key
        if not key or not value or key in seen:
            continue

        seen.add(key)
        items.append({"key": key, "value": value})
    return items


def filter_cards(stream, request, *, enabled: bool) -> CardFilter:
    """Narrow the card rows in ``stream`` to the tag in ``?tag=``.

    A content page body can hold card rows between its other blocks. The
    filter applies across every row on the page, and blocks that are not
    card rows are left alone. The available tags are worked out either way;
    only the selection waits on ``enabled``, so a tag in the query string does
    nothing on a page that does not offer the control.
    """
    rows: list[tuple[int, list[tuple[object, set[str]]]]] = []
    available_tags_by_key: dict[str, str] = {}
    for index, block in enumerate(stream or []):
        if block.block_type != CARD_ROW_BLOCK_TYPE:
            continue

        prepared_cards: list[tuple[object, set[str]]] = []
        for card in block.value.get("cards", []):
            tag_items = card_tag_items(card)
            for item in tag_items:
                available_tags_by_key[item["key"]] = item["value"]
            prepared_cards.append((card, {item["key"] for item in tag_items}))
        rows.append((index, prepared_cards))

    selected_tag = None
    selected_tag_key = ""
    if enabled:
        selected_tag_key = (request.GET.get("tag") or "").strip().lower()
        if selected_tag_key not in available_tags_by_key:
            selected_tag_key = ""
        if selected_tag_key:
            selected_tag = {
                "key": selected_tag_key,
                "value": available_tags_by_key[selected_tag_key],
            }

    visible_cards = {
        index: [
            card
            for card, tag_keys in prepared_cards
            if not selected_tag_key or selected_tag_key in tag_keys
        ]
        for index, prepared_cards in rows
    }
    available_tags = [
        {"key": key, "value": value}
        for key, value in sorted(
            available_tags_by_key.items(),
            key=lambda row: row[1].lower(),
        )
    ]
    return CardFilter(
        available_tags=available_tags,
        selected_tag=selected_tag,
        visible_cards=visible_cards,
    )

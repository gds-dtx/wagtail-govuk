"""Filter catalogue entries by ``?q=``, ``?tag=``, ``?profession=``, ``?type=``, ``?scope=``.

Done in Python: a catalogue is a few hundred entries, and SQLite cannot index JSON lists.
"""

from dataclasses import dataclass, field

FILTER_FIELDS = ("tag", "profession", "type", "scope")
_ATTRIBUTES = {"tag": "tags", "profession": "professions", "type": "type", "scope": "scope"}
_VOCABULARIES = {"tag": "tags", "profession": "professions", "type": "types", "scope": "scope"}
PRIMARY_BOOST = 1.5


@dataclass
class EntryFilter:
    query: str = ""
    selected: dict[str, list[str]] = field(default_factory=dict)
    # Per field: each value with the count it would show given the other filters.
    options: dict[str, list[dict]] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return bool(self.query or any(self.selected.values()))


def _values(entry, name: str) -> list[str]:
    value = getattr(entry, _ATTRIBUTES[name])
    return list(value) if isinstance(value, list) else [value]


def filter_entries(
    entries, params, vocabularies: dict, *, keep_empty: bool = False
) -> tuple[list, EntryFilter]:
    """``(visible entries, filter state)``. With a query, best matches first.

    ``keep_empty`` keeps zero-count values, for the live counts to bring back.
    """
    entries = list(entries)
    query = " ".join((params.get("q") or "").split())[:200]
    if query:
        # Before counting, so the counts are of what the search would show.
        scored = [(score(entry, query, vocabularies), entry) for entry in entries]
        ranked = sorted((pair for pair in scored if pair[0] > 0), key=lambda pair: -pair[0])
        entries = [entry for _, entry in ranked]
    selected = {
        name: [value for value in params.getlist(name) if value][:20] for name in FILTER_FIELDS
    }

    def passes(entry, *, ignoring: str = "") -> bool:
        return all(
            not chosen or set(chosen) & set(_values(entry, name))
            for name, chosen in selected.items()
            if name != ignoring
        )

    options = {}
    for name in FILTER_FIELDS:
        labels = vocabularies.get(_VOCABULARIES[name], {})
        counts: dict[str, int] = {}
        for entry in entries:
            if not passes(entry, ignoring=name):
                continue
            for value in _values(entry, name):
                counts[value] = counts.get(value, 0) + 1
        options[name] = [
            {
                "value": value,
                "label": labels.get(value, {}).get("label", value),
                "count": counts.get(value, 0),
                "selected": value in selected[name],
            }
            for value in labels
            # Keep a chosen value even at zero, so the form still shows it applied.
            if keep_empty or value in counts or value in selected[name]
        ]

    visible = [entry for entry in entries if passes(entry)]
    return visible, EntryFilter(query=query, selected=selected, options=options)


def score(entry, query: str, vocabularies: dict) -> float:
    """``entry``'s relevance to ``query``, by the site search's scoring."""
    from govuk.search_backend import search_backend

    tags = vocabularies.get("tags", {})
    professions = vocabularies.get("professions", {})
    relevance = search_backend._text_relevance(
        query,
        (
            (entry.title, 3.0),
            (entry.short_description, 1.5),
            (entry.long_description, 1.0),
            (" ".join(tags.get(t, {}).get("label", t) for t in entry.tags), 1.0),
            (" ".join(professions.get(p, {}).get("label", p) for p in entry.professions), 0.75),
            (entry.owner.get("organisation", ""), 0.5),
        ),
    )
    return relevance * PRIMARY_BOOST if relevance and entry.primary else relevance

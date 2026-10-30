"""Filter and search catalogue entries, from the request's query string.

``?q=`` searches; ``?tag=``, ``?profession=``, ``?type=`` and ``?scope=``
narrow, each repeatable (an entry matching any of a field's chosen values
passes that field). It runs over the entries in Python: a catalogue is a few
hundred entries, and their tags and professions are JSON lists, which SQLite
cannot index.

The form on the group and "all" pages uses these parameters, and so can any
other form: a search box on the content main page only needs to send ``q`` to
its ``all/`` page.
"""

from dataclasses import dataclass, field

FILTER_FIELDS = ("tag", "profession", "type", "scope")
# The entry attribute each filter reads, and the vocabulary its labels come from.
_ATTRIBUTES = {"tag": "tags", "profession": "professions", "type": "type", "scope": "scope"}
_VOCABULARIES = {"tag": "tags", "profession": "professions", "type": "types", "scope": "scope"}
# A primary entry is the one to read first, so it ranks above an equal match.
PRIMARY_BOOST = 1.5


@dataclass
class EntryFilter:
    query: str = ""
    selected: dict[str, list[str]] = field(default_factory=dict)
    # For each field, ``[{"value", "label", "count", "selected"}]``: each value
    # with how many entries choosing it would show, given the other filters
    # already chosen, so the form offers only what would match.
    options: dict[str, list[dict]] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return bool(self.query or any(self.selected.values()))


def _values(entry, name: str) -> list[str]:
    value = getattr(entry, _ATTRIBUTES[name])
    return list(value) if isinstance(value, list) else [value]


def filter_entries(entries, params, vocabularies: dict) -> tuple[list, EntryFilter]:
    """``(visible entries, filter state)``. With a query, best matches first."""
    entries = list(entries)
    query = " ".join((params.get("q") or "").split())[:200]
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
            # A chosen value stays listed even if the other filters leave it
            # nothing, so the form still shows what is applied.
            if value in counts or value in selected[name]
        ]

    visible = [entry for entry in entries if passes(entry)]
    if query:
        scored = [(score(entry, query, vocabularies), entry) for entry in visible]
        visible = [
            entry
            for _, entry in sorted(
                (pair for pair in scored if pair[0] > 0), key=lambda pair: -pair[0]
            )
        ]
    return visible, EntryFilter(query=query, selected=selected, options=options)


def score(entry, query: str, vocabularies: dict) -> float:
    """How well ``entry`` matches ``query``: the site search's own scoring."""
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

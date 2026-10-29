"""The content catalogue: sync a GitHub catalogue repo into a content main page.

``fetch`` downloads a repo, ``catalogue`` reads the catalogue's vocabularies and
entries, ``adapters`` and ``render`` turn an entry's hosted or repo content into
stored HTML, ``layouts`` shapes it for the entry's layout, and ``sync`` swaps
it all in. ``filtering`` and ``context`` serve it. The page and its stored copy
are in ``govuk/models/catalogue.py``; see ``docs/content-catalogue.md``.
"""

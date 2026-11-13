"""Ask: a question in a person's own words, and the site's closest matches to it.

An editor places the question box (``QuestionBoxBlock``) on a page, often the
home page. It posts the question to ``/ask/`` (``views.ask_view``), which
searches the content index by words and by meaning (``content_index.search``,
hybrid mode) and shows the closest matches as the cards the catalogue's
section pages use (``cards``). Nothing generates text: every word shown is
the site's own.

Gated by ``FEATURE_FLAGS["ASK"]``. The question is never logged or stored,
and is posted rather than put in the address so no access log on the way
holds it either.
"""

"""Guidance-as-code: sync a GitHub repo of guidance into a collection page.

``fetch`` downloads the repo, ``adapters`` read its layout (a ``guidance.yml``
manifest or a tech-docs repo) into a ``bundle.GuidanceBundle``, ``render``
turns that into the stored HTML, and ``sync`` swaps it in. ``export`` writes a
synced collection back out in the manifest format. The page and its stored
copy are in ``govuk/models/guidance.py``; see ``docs/guidance-as-code.md``.
"""

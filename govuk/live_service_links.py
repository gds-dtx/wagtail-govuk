"""The live service's URL shapes, mapped onto the pages this site serves.

The live framework publishes a role at ``/role/<slug>`` and a skill at
``/skill/<slug>``. This site serves a role page at ``/<slug>`` and every skill
as a section of the skills A to Z, so both shapes have to be translated. That
happens in two places, and this module is the single rule both use:

* ``seed_live_service_redirects`` turns them into redirects, for bookmarks,
  search engines and links from outside the service;
* the ``changelog_note`` template tag rewrites them where they appear inside
  the content itself.

The second is not a nicety. The 262 imported changelog notes hold their links
in the live service's shape -- the framework home page alone renders 58 of
them -- because the CSV they came from wrote them that way and
``changelog_note_to_html`` stored what it was given. Left alone they resolve
only for as long as the redirects are seeded, so a fresh instance imported
from the admin export, with no runbook step run, publishes a home page whose
every changelog link is a 404. Rewriting at render time makes the content
right in itself rather than right by arrangement.

The mapping is a rule, not content: it points at whatever page carries the
role or skill now, so it follows the content when pages move.
"""

from __future__ import annotations

import re

from django.utils.html import escape

from govuk.models import GovukRole, GovukSkill, RolePage, SkillsAZPage

# Only a bare path is rewritten. An href carrying a fragment or a query is
# left for the redirects: a skill target already ends in its own "#slug", and
# appending a second fragment would break the link this is meant to fix.
_LIVE_SERVICE_HREF = re.compile(r'(href=")(/(?:role|skill)/[^"#?]*)(")')


def role_page_targets(site) -> list[tuple[str, RolePage]]:
    """(role slug, page) for every role a live page on this site renders.

    The first page in tree order keeps a role that several pages carry,
    matching the order the listings use.
    """
    slugs_by_id = dict(GovukRole.objects.values_list("pk", "slug"))
    seen: set[int] = set()
    targets: list[tuple[str, RolePage]] = []
    pages = (
        RolePage.objects.live()
        .descendant_of(site.root_page, inclusive=True)
        .order_by("path")
    )
    for page in pages:
        for role_id in page.get_selected_role_ids():
            slug = slugs_by_id.get(role_id)
            if not slug or role_id in seen:
                continue
            seen.add(role_id)
            targets.append((slug, page))
    return targets


def skill_targets(site) -> tuple[list[tuple[str, str]], SkillsAZPage | None]:
    """(skill slug, link) for every skill, into its section of the A to Z.

    A skill is a snippet with no page of its own, so the link carries the
    fragment the search results already use. The page itself is returned
    alongside, because its absence is the only reason for an empty list and
    a caller with somewhere to report it should be able to say so.
    """
    skills_page = (
        SkillsAZPage.objects.live()
        .descendant_of(site.root_page, inclusive=True)
        .order_by("path")
        .first()
    )
    if skills_page is None:
        return [], None
    page_url = skills_page.url or ""
    return [
        (slug, f"{page_url}#{slug}")
        for slug in GovukSkill.objects.values_list("slug", flat=True)
        if slug
    ], skills_page


def live_service_link_map(site) -> dict[str, str]:
    """Every live-service path this site can answer, and what answers it.

    Built in one go and meant to be built once per render: the skills A to Z
    asks all 185 of its skills for their changelog, and the home page renders
    262 entries, so a map per note would be a fresh N+1 on the two heaviest
    pages in the service.
    """
    link_map: dict[str, str] = {}

    for slug, page in role_page_targets(site):
        url = page.url or ""
        if url:
            link_map[f"/role/{slug}".lower()] = url

    targets, _skills_page = skill_targets(site)
    for slug, link in targets:
        if link:
            link_map[f"/skill/{slug}".lower()] = link

    return link_map


def rewrite_live_service_links(html: str, link_map: dict[str, str]) -> str:
    """Point a note's live-service links at the pages that serve them here.

    A path the map does not know is left exactly as it was: it may be a role
    this instance does not carry, and a redirect is still a better answer for
    it than a guess.
    """
    if not html or not link_map:
        return html or ""

    def replace(match: re.Match) -> str:
        target = link_map.get(match.group(2).rstrip("/").lower())
        if not target:
            return match.group(0)
        return f"{match.group(1)}{escape(target)}{match.group(3)}"

    return _LIVE_SERVICE_HREF.sub(replace, html)

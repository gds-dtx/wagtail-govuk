"""The live service's URL shapes, mapped onto the pages this site serves.

The live framework publishes a role at ``/role/<slug>`` and a skill at
``/skill/<slug>``. This site serves a role page at ``/<slug>`` and every skill
as a section of the skills A to Z, so both shapes have to be translated. That
happens in two places, and this module is the single rule both use:

* ``seed_live_service_redirects`` turns them into redirects, for bookmarks,
  search engines and links from outside the service. It runs at the end of an
  import, so a fresh instance answers them from the moment it has content, and
  from the management command of the same name whenever content moves;
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
from dataclasses import dataclass

from django.utils.html import escape
from wagtail.contrib.redirects.models import Redirect

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


@dataclass(frozen=True)
class RedirectSeedResult:
    """What seeding did, in the terms the runbook and the import report use."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    # The one whole category that can go missing in silence: without a live
    # skills A to Z there is nowhere for any of the 185 skill URLs to point,
    # and the roles seed perfectly well beside them. False on a site with no
    # skills, where there is no A to Z because there is nothing to list.
    skills_have_nowhere_to_go: bool = False

    @property
    def written(self) -> int:
        return self.created + self.updated


def live_service_redirect_targets(site) -> tuple[dict, SkillsAZPage | None]:
    """{old path: (page, link)} for every live-service URL this site can answer.

    Paths are normalised the way ``Redirect`` stores them, so they can be
    compared against existing rows without a second pass.
    """
    targets: dict[str, tuple[RolePage | None, str]] = {}
    for slug, page in role_page_targets(site):
        targets[Redirect.normalise_path(f"/role/{slug}")] = (page, "")

    skills, skills_page = skill_targets(site)
    for slug, link in skills:
        targets[Redirect.normalise_path(f"/skill/{slug}")] = (None, link)
    return targets, skills_page


def _redirect_is_current(redirect: Redirect, *, page, link: str) -> bool:
    return (
        redirect.redirect_page_id == (page.pk if page is not None else None)
        and redirect.redirect_link == link
        and redirect.is_permanent
    )


def _existing_redirects(site, paths) -> dict[str, Redirect]:
    """The site's rows for these paths, in one query rather than one each.

    There are around 250 of them -- 67 roles and 185 skills -- and seeding runs
    at the end of an import that has already written the whole site, so asking
    per path would add a round trip apiece to the slowest operation the admin
    offers.
    """
    return {
        redirect.old_path: redirect
        for redirect in Redirect.objects.filter(site=site, old_path__in=list(paths))
    }


def seed_live_service_redirects(site) -> RedirectSeedResult:
    """Point the live service's URLs at the pages this site serves them on.

    Creates what is missing, corrects what points somewhere else, and leaves
    the rest untouched -- so it is safe to run again, and does, every time
    content moves. Nothing is ever deleted: a redirect this rule no longer
    produces may still be the only answer an old bookmark has.
    """
    targets, skills_page = live_service_redirect_targets(site)
    existing = _existing_redirects(site, targets)

    created = updated = unchanged = 0
    for old_path, (page, link) in targets.items():
        redirect = existing.get(old_path)
        if redirect is None:
            Redirect.objects.create(
                old_path=old_path,
                site=site,
                redirect_page=page,
                redirect_link=link,
                is_permanent=True,
            )
            created += 1
        elif _redirect_is_current(redirect, page=page, link=link):
            unchanged += 1
        else:
            redirect.redirect_page = page
            redirect.redirect_link = link
            redirect.is_permanent = True
            redirect.save(
                update_fields=["redirect_page", "redirect_link", "is_permanent"]
            )
            updated += 1

    return RedirectSeedResult(
        created=created,
        updated=updated,
        unchanged=unchanged,
        skills_have_nowhere_to_go=skills_page is None and _skills_exist(),
    )


def _skills_exist() -> bool:
    return GovukSkill.objects.exclude(slug="").exists()


def unseeded_live_service_redirects(site) -> list[str]:
    """Live-service paths with no redirect, or one pointing somewhere else.

    Empty means a reader arriving from a bookmark, a search result or a
    GovSearch link list reaches the right page for every role and skill this
    site carries. Anything in it is a 404 waiting for the first person to
    follow an old link, which is what makes it worth failing a cutover check
    over rather than noting.
    """
    targets, _skills_page = live_service_redirect_targets(site)
    existing = _existing_redirects(site, targets)
    return sorted(
        old_path
        for old_path, (page, link) in targets.items()
        if old_path not in existing
        or not _redirect_is_current(existing[old_path], page=page, link=link)
    )


def unanswerable_live_service_urls(site) -> list[str]:
    """Live-service URLs no redirect can be built for, so nothing can answer.

    A role no live page renders and a skill with no A to Z to sit in are both
    invisible to seeding -- the rule produces no target, so there is nothing to
    create and nothing to report as missing. They are still URLs the live
    service publishes today, and after cutover they 404.
    """
    targets, skills_page = live_service_redirect_targets(site)

    unanswerable = [
        f"/role/{slug}"
        for slug in GovukRole.objects.values_list("slug", flat=True)
        if slug and Redirect.normalise_path(f"/role/{slug}") not in targets
    ]
    if skills_page is None:
        unanswerable += [
            f"/skill/{slug}"
            for slug in GovukSkill.objects.values_list("slug", flat=True)
            if slug
        ]
    return sorted(unanswerable)

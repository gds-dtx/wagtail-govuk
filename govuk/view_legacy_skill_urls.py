"""Answer the live service's /skill/<slug> addresses.

The old service published every skill at ``/skill/<slug>``. The framework has
no page per skill: each one is a section of the skills A to Z, so those
addresses have to be answered with a redirect to the skill's anchor there.
The old domain now redirects path for path onto this site, so every link,
bookmark and search result for a skill arrives here as ``/skill/<slug>``.

Until 24 September 2026 the content import seeded a redirect row per skill.
That machinery was removed together with the role redirects, which had become
unnecessary once roles were served at their live address directly; the skill
half went with it, and all 185 skill addresses answered 404 on production.
This view puts the behaviour back without any rows: look the slug up, send the
reader to the A to Z. Anything that is not a live skill, or a site without the
framework, falls through to Wagtail's own page serving, so a site that happens
to have a page under ``/skill/`` is unaffected.
"""

from django.conf import settings
from django.http import HttpResponsePermanentRedirect
from django.views.decorators.http import require_http_methods
from wagtail.models import Site
from wagtail.views import serve as wagtail_serve

from govuk.models import FrameworkSkillsPage, GovukSkill


def legacy_skill_target(request, slug: str) -> str | None:
    """The A to Z anchor for a live skill on this site, or None."""
    if not settings.FEATURE_FLAGS.get("SKILLS"):
        return None
    skill = GovukSkill.objects.filter(slug__iexact=slug, live=True).first()
    if skill is None:
        return None
    site = Site.find_for_request(request)
    if site is None:
        return None
    skills_page = FrameworkSkillsPage.objects.live().in_site(site).first()
    if skills_page is None:
        return None
    page_url = skills_page.get_url(request) or ""
    if not page_url:
        return None
    return f"{page_url}#{skill.slug}"


@require_http_methods(["GET", "HEAD"])
def legacy_skill_url_view(request, slug: str):
    target = legacy_skill_target(request, slug)
    if target is None:
        path = request.path.lstrip("/")
        return wagtail_serve(request, path)
    return HttpResponsePermanentRedirect(target)

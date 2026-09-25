
from django.conf import settings
from django.utils.text import slugify
from wagtail.models import Site

from ..constants import FRAMEWORK_HOME_LABEL
from .settings import CapabilityFrameworkWordingSettings, SidebarSettings
from .snippets import GovukRole


def _default_site_wording() -> "CapabilityFrameworkWordingSettings":
    """The framework wording for the default site, or the model's defaults.

    The navigation helpers have no request to hang a lookup on, and an unsaved
    instance carries every default, so a site that has never edited the
    wording reads the same either way.
    """
    site = Site.objects.filter(is_default_site=True).first()
    saved = (
        CapabilityFrameworkWordingSettings.objects.filter(site=site).first()
        if site
        else None
    )
    return saved or CapabilityFrameworkWordingSettings()


def framework_main_page():
    """The single live Framework Main Page, or ``None``.

    Roles are served as routes under this one page, so the navigation and the
    related-role links resolve their URLs through it. ``max_count`` keeps it
    unique; the first live one wins if a stray second ever exists.
    """
    from .pages import FrameworkMainPage

    return FrameworkMainPage.objects.live().first()


def role_route_url(main_page, role_slug: str) -> str:
    """The URL of a role's page: the Framework Main Page route for its slug.

    ``reverse_subpage`` gives the sub-path with its trailing slash, and the
    page's own URL its prefix, so the two join to the address a reader lands on.
    Empty when there is no framework main page or it has no URL yet (no site,
    no request), which the templates already fall back on as plain text.
    """
    if main_page is None:
        return ""
    base = main_page.url
    if not base:
        return ""
    return base + main_page.reverse_subpage("serve_role", args=[role_slug])


def without_framework_pages(queryset):
    """``queryset`` minus the framework's page types, on a site without it.

    ``FrameworkSkillsPage`` 404s rather than serves without the flag, but it can still
    be in the tree: the page import creates pages for any model it can resolve,
    and it is generic on purpose. A listing that goes on naming it offers a
    reader a link that 404s, which reads as a broken site rather than a site
    that does not have the framework. (Roles are no longer pages -- they are
    served as routes under the framework main page -- so there is nothing role
    shaped to exclude here.)

    The public surfaces that list pages generically go through here: the pages
    API, front-end search and the navigation menu. Tag listings make the same
    exclusion a queryset at a time in ``_page_listing_querysets``. It is a no-op
    with the flag on, so the framework's own site is unaffected.

    Deliberately not applied to the Wagtail admin. An editor who has ended up
    with these pages needs to be able to see them to delete them, and hiding
    them there would leave the site with pages nobody can find or remove.
    """
    from .pages import FrameworkContentPage, FrameworkMainPage, FrameworkSkillsPage

    if settings.FEATURE_FLAGS.get("SKILLS"):
        return queryset
    return queryset.not_type(FrameworkMainPage, FrameworkContentPage, FrameworkSkillsPage)


def role_page_urls_by_role_id() -> dict[int, str]:
    """Map each role id to the URL of its page under the framework main page.

    Every role now has a page of its own -- a route on the framework main page
    -- so the mapping covers every role, not only the ones an editor chose to
    surface. Used for the related-role and progression links between roles.
    """
    main_page = framework_main_page()
    if main_page is None:
        return {}
    base = main_page.url
    if not base:
        return {}
    return {
        role.pk: base + main_page.reverse_subpage("serve_role", args=[role.slug])
        for role in GovukRole.objects.filter(live=True)
    }


def further_resources_group(
    *, current_page_id: int | None = None, wording=None, request=None
) -> dict | None:
    """The pages about the framework itself, for the side navigation.

    The live service closes its navigation with these: the skills index and the
    handful of pages that are about the framework rather than one role. They are
    the framework main page's framework children -- its framework content pages
    and the skills index. Its plain content pages are not about the framework
    and stay out: the live service keeps its privacy notice, cookie statement
    and accessibility statement off the menu, and an editor chooses which a
    page is by choosing its type.

    Sidebar settings then decides the menu. Listing any page at all makes the
    list the menu: the pages listed and ticked are shown, in the order given,
    and a framework child left off it is left out. An empty list means the
    editor has not chosen, so every framework child is shown in tree order,
    which is what a newly built site gets.

    It used to append the unlisted children to the configured ones instead, so
    that a new page could never silently vanish. Antony found on 10 Sep 2026
    that this makes the setting unable to express live's menu at all: picking
    the six pages live shows still left the other eight on the page, and the
    only way to get live's menu was to list all fourteen and untick eight.
    Choosing pages has to mean choosing pages.
    """
    from .pages import FrameworkContentPage, FrameworkSkillsPage

    main_page = framework_main_page()
    if main_page is None:
        return None

    children = list(
        main_page.get_children()
        .live()
        .type(FrameworkContentPage, FrameworkSkillsPage)
        .order_by("path")
    )
    child_by_id = {page.pk: page for page in children}

    # The configured order/visibility, from Sidebar settings for the site being
    # served when there is a request to say which, so that two sites on one
    # instance each get their own; otherwise the default site's, which is the
    # one _default_site_wording reads.
    site = Site.find_for_request(request) if request is not None else None
    if site is None:
        site = Site.objects.filter(is_default_site=True).first()
    setting = SidebarSettings.objects.filter(site=site).first() if site else None
    configured = list(setting.items.all()) if setting else []

    ordered: list = []
    seen: set = set()
    for item in configured:
        if item.page_id in child_by_id:
            seen.add(item.page_id)
            if item.visible:
                ordered.append(child_by_id[item.page_id])
    # Only when nothing at all is configured does the tree stand in for a
    # choice; once an editor has listed a page, the list is the menu.
    if not configured:
        ordered.extend(page for page in children if page.pk not in seen)

    items = [
        {
            "title": page.title,
            "url": page.url,
            "is_current": page.pk == current_page_id,
        }
        for page in ordered
        if page.url
    ]

    if not items:
        return None
    if wording is None:
        wording = _default_site_wording()
    return {"title": wording.further_resources_heading, "items": items}


def role_navigation_groups(
    *,
    current_role_slug: str | None = None,
    current_page_id: int | None = None,
    wording=None,
    request=None,
) -> list[dict]:
    """The side navigation: every live role grouped by family, then the rest.
    Closes with the pages about the framework itself.
    The roles come straight from the role snippets, so the
    navigation populates itself; a role's own page marks itself current through
    ``current_role_slug``, and a framework content page through
    ``current_page_id``.
    """
    main_page = framework_main_page()
    base = main_page.url if main_page else ""

    groups: dict[str, list[dict]] = {}
    for role in GovukRole.objects.filter(live=True):
        family = (role.family or "").strip()
        if not family:
            continue
        url = (
            base + main_page.reverse_subpage("serve_role", args=[role.slug])
            if base
            else ""
        )
        groups.setdefault(family, []).append(
            {
                "title": role.title,
                "url": url,
                "is_current": role.slug == current_role_slug,
            }
        )

    if wording is None:
        wording = _default_site_wording()
    navigation = [
        {
            "title": wording.family_group_title(family),
            "items": sorted(items, key=lambda item: (item["title"] or "").lower()),
        }
        for family, items in sorted(groups.items())
    ]

    resources = further_resources_group(
        current_page_id=current_page_id, wording=wording, request=request
    )
    if resources:
        navigation.append(resources)
    return navigation




def framework_home_link(*, is_current: bool = False) -> dict | None:
    """The role navigation's top link: the framework main page itself.

    Reads "Capability Framework" (a fixed label -- the main page's own title is
    the full service name), points at the main page, and is marked current on
    the main page's own page the way each role is on its route. ``None`` when
    there is no framework main page, or it has no URL yet, so the template can
    leave the link out.
    """
    main_page = framework_main_page()
    if main_page is None:
        return None
    url = main_page.url
    if not url:
        return None
    return {"title": FRAMEWORK_HOME_LABEL, "url": url, "is_current": is_current}


def framework_breadcrumbs(
    request, page, *, family: str = "", title: str | None = None
) -> list[dict]:
    """Home, an optional role family, then the current thing itself.

    Stands in for the side navigation on a narrow screen, which hides it, so
    every page carrying that navigation carries the same trail. The family
    entry points at its heading on the home page, which is where the role
    lists appear once the navigation is hidden.

    ``title`` names the final crumb where it is not the page's own -- a role
    served on the framework main page's route reads as the role, not the
    framework, so it passes the role title here.
    """
    site = Site.find_for_request(request)
    home_url = site.root_page.get_url(request) if site else None
    if not home_url:
        return []

    wording = CapabilityFrameworkWordingSettings.for_request(request)
    trail = [
        {"title": wording.breadcrumb_home_label, "url": home_url, "is_current": False}
    ]

    if family:
        group_title = wording.family_group_title(family)
        trail.append(
            {
                "title": group_title,
                "url": f"{home_url}#{slugify(group_title)}",
                "is_current": False,
            }
        )

    trail.append(
        {"title": title or page.title, "url": None, "is_current": True}
    )
    return trail





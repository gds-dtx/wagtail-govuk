import os

from django.conf import settings
from django.http import Http404
from wagtail.models import Page, Site

from govuk.models import (
    CustomiseSettings,
    ErrorPagesSettings,
    FooterSettings,
    PhaseBannerSettings,
    without_framework_pages,
)


def navigation_and_breadcrumbs(request):
    additional_css = getattr(settings, "ADDITIONAL_CSS", None)
    if isinstance(additional_css, str):
        additional_css = [additional_css]
    elif additional_css:
        additional_css = [path for path in additional_css if path]
    else:
        additional_css = []

    # At present, main.js gives an editor's rich text the Design System's type
    # scale, which is not the style of every service using this repo.
    # The flag rides on the body element so the script can tell which kind of
    # site it is on.
    framework_enabled = bool(settings.FEATURE_FLAGS.get("SKILLS"))

    template_context = {
        "framework_enabled": framework_enabled,
        "app_debug": settings.DEBUG,
        "app_version": getattr(settings, "VERSION", ""),
        "additional_css": additional_css,
        "noindex": bool(getattr(settings, "NOINDEX", False)),
        "django_settings_module": os.getenv("DJANGO_SETTINGS_MODULE")
        or getattr(settings, "SETTINGS_MODULE", ""),
    }

    site = Site.find_for_request(request)
    if site is None:
        return {
            **template_context,
            "service_navigation_items": [],
            "breadcrumbs": [],
            "phase_banner_settings": None,
            "footer_settings": None,
            "customise_settings": None,
            "error_pages_settings": None,
        }

    site_root = site.root_page.specific

    try:
        current_page = Page.find_for_request(request, request.path_info)
    except Http404:
        current_page = None

    service_navigation_items = []
    menu_pages = (
        without_framework_pages(site_root.get_children().live().in_menu())
        .specific()
        .order_by("path")
    )
    for menu_page in menu_pages:
        service_navigation_items.append(
            {
                "title": menu_page.title,
                "url": menu_page.get_url(request),
                "is_active": bool(
                    current_page and current_page.path.startswith(menu_page.path)
                ),
            }
        )

    breadcrumbs = []
    if (
        current_page
        and current_page.pk != site_root.pk
        and current_page.path.startswith(site_root.path)
    ):
        for ancestor in current_page.get_ancestors(inclusive=True).specific():
            if not ancestor.path.startswith(site_root.path):
                continue

            is_current = ancestor.pk == current_page.pk
            breadcrumbs.append(
                {
                    "title": ancestor.title,
                    "url": None if is_current else ancestor.get_url(request),
                    "is_current": is_current,
                }
            )

    customise_settings = CustomiseSettings.for_site(site)

    return {
        **template_context,
        "service_navigation_items": service_navigation_items,
        "breadcrumbs": breadcrumbs,
        "phase_banner_settings": PhaseBannerSettings.for_site(site),
        "footer_settings": FooterSettings.for_site(site),
        "customise_settings": customise_settings,
        "error_pages_settings": ErrorPagesSettings.for_site(site),
    }

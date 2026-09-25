
from django.conf import settings
from wagtail.admin.panels import FieldPanel, InlinePanel
from wagtail.models import Page


def page_settings_panels() -> list:
    """``Page.settings_panels``, minus scheduling while nothing can schedule.

    Wagtail's go-live and expiry dates do nothing by themselves: they need the
    ``publish_scheduled`` command run on a timer, and nothing in this service's
    deployment runs it -- no cron, no worker, no scheduled task in any of the
    three repos. An editor who sets a go-live date therefore gets a page that
    quietly never publishes, with no error to tell them so. Not offering the
    field is the honest version of that, and it leaves publishing as a thing
    someone does deliberately rather than a thing that was supposed to happen.

    ``SCHEDULED_PUBLISHING=true`` puts the panel back, and with it the "Edit
    schedule" toggle in the status side panel -- Wagtail shows that only when
    the form has a ``go_live_at`` field, which is this panel's doing.

    Read once at import, as panel definitions are. Changing the env var means a
    new container either way.
    """
    if getattr(settings, "SCHEDULED_PUBLISHING", False):
        return list(Page.settings_panels)
    return [
        panel
        for panel in Page.settings_panels
        if getattr(panel, "path", None) != "wagtail.admin.panels.PublishingPanel"
    ]


def base_content_panels() -> list:
    """The editing panels shared by the content-style page types.

    ``ContentPage`` and the two framework page types all carry the same hero and
    body fields; this builds the common part so the three do not each write it
    out. The framework welcome content is added on top only by the framework
    types (see ``framework_content_panels``).
    """
    return list(Page.content_panels) + [
        FieldPanel("hero_title"),
        FieldPanel("hero_intro"),
        FieldPanel("author"),
        FieldPanel("body"),
        FieldPanel("body_blocks"),
    ]


def framework_content_panels() -> list:
    """``base_content_panels`` plus the framework welcome content.

    The welcome content is the Capability Framework's own -- role families,
    skill level definitions -- so it is only offered on the framework page
    types, and only where the framework is switched on. The field stays on the
    model either way: this decides what the form shows, not what the database
    holds, so switching the flag needs no migration.
    """
    panels = base_content_panels()
    if settings.FEATURE_FLAGS.get("SKILLS"):
        panels.append(FieldPanel("framework_welcome_body"))
    return panels


def base_settings_panels() -> list:
    """The settings panels shared by every content-style page type.

    Only the settings every type offers: the last-updated date and the page
    metadata. The hero-styling toggles are plain ``ContentPage``'s alone (the
    framework pages set their own header treatment), as are the
    sidebar-heading-navigation toggle and the tags panel; each type adds what it
    offers on top.
    """
    return page_settings_panels() + [
        FieldPanel("show_last_updated_date"),
        FieldPanel("show_page_content_metadata"),
    ]


def content_settings_panels() -> list:
    """Plain ``ContentPage`` settings: hero styling, the shared panels, heading
    navigation, tags. The hero-styling toggles are offered here only."""
    return page_settings_panels() + [
        FieldPanel("enable_hero_styling"),
        FieldPanel("enable_combined_service_navigation_and_hero_styling"),
        FieldPanel("show_last_updated_date"),
        FieldPanel("show_page_content_metadata"),
        FieldPanel("enable_free_text_heading_navigation"),
        InlinePanel("tagged_items", heading="Tags", label="Tag"),
    ]


def framework_main_settings_panels() -> list:
    """``FrameworkMainPage`` settings: shared panels, the framework switches, tags.

    The three framework switches each turn on a block of Capability Framework
    furniture -- the role side navigation, the site-wide changelog, the welcome
    layout. On a site without the framework they have nothing to show, and the
    wording that labels them comes from settings the admin does not register
    without the flag, so an editor there would be reading captions nobody on
    that site can change. There is no heading-navigation toggle: the role
    navigation already occupies that side column.
    """
    panels = base_settings_panels()
    if settings.FEATURE_FLAGS.get("SKILLS"):
        panels += [
            FieldPanel("show_role_navigation"),
            FieldPanel("show_framework_updates"),
            FieldPanel("show_framework_welcome"),
        ]
    panels.append(InlinePanel("tagged_items", heading="Tags", label="Tag"))
    return panels


def framework_content_settings_panels() -> list:
    """``FrameworkContentPage`` settings: the shared panels and tags only.

    A framework content page always carries the role navigation, so there is no
    switch for it; it offers neither the framework updates, the welcome layout,
    the sidebar heading navigation, nor the hero-styling toggles. The framework
    behaviours are forced in ``FrameworkContentPage.get_context`` regardless of
    what is stored. Whether the page is listed in the sidebar, and in what order,
    is managed centrally in Sidebar settings rather than per page.
    """
    return base_settings_panels() + [
        InlinePanel("tagged_items", heading="Tags", label="Tag"),
    ]




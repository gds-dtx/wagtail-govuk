"""Helpers for tests of the framework's roles.

Roles no longer have a page each; the single ``FrameworkMainPage`` serves one
per ``GovukRole`` snippet at ``role/<role-slug>/``. These build that main page and
the URL a role is served on, so the role tests can set up in one line where they
used to create a ``RolePage``.
"""

from __future__ import annotations

from govuk.models import FrameworkMainPage, GovukRole


def make_framework_main_page(
    parent,
    *,
    title: str = "Capability Framework",
    slug: str = "capability-framework",
    **kwargs,
) -> FrameworkMainPage:
    """Create and publish the framework main page under ``parent``."""
    page = parent.add_child(
        instance=FrameworkMainPage(title=title, slug=slug, **kwargs)
    )
    page.save_revision().publish()
    return FrameworkMainPage.objects.get(pk=page.pk)


def role_url(main_page: FrameworkMainPage, role: GovukRole | str) -> str:
    """The URL a role is served on, under the framework main page's route."""
    slug = role if isinstance(role, str) else role.slug
    return main_page.url + main_page.reverse_subpage("serve_role", args=[slug])

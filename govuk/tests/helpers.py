"""Shared test helpers."""

from __future__ import annotations

from django.utils.text import slugify
from wagtail.models import Site

from govuk.models import ContentPage, FrameworkMainPage, GovukRole, SearchSettings


def find_all_pages(site=None) -> SearchSettings:
    """Settings > Search's "All pages", for tests of search on ordinary pages, roles and skills."""
    search = SearchSettings.for_site(site or Site.objects.get(is_default_site=True))
    search.search_scope = SearchSettings.SearchScope.ALL
    search.save()
    return search


def feature_flags(*, skills: bool = False, feedback: bool = False) -> dict[str, bool]:
    """A ``FEATURE_FLAGS`` value; flags not named are off."""
    return {"SKILLS": skills, "FEEDBACK": feedback}


def text_body(html: str) -> list[dict]:
    """A content page body holding one Text block."""
    return [{"type": "text", "value": html}]


def body_html(page) -> str:
    """The HTML of every Text block in a page's body."""
    return "".join(block.value.source for block in page.body if block.block_type == "text")


def publish_page(title: str, html: str, parent=None) -> ContentPage:
    """Publish a content page with one Text block, under the default site's root by default."""
    parent = parent or Site.objects.get(is_default_site=True).root_page
    page = parent.add_child(instance=ContentPage(title=title, slug=slugify(title), body=text_body(html)))
    page.save_revision().publish()
    return page


def make_framework_main_page(
    parent,
    *,
    title: str = "Capability Framework",
    slug: str = "capability-framework",
    **kwargs,
) -> FrameworkMainPage:
    page = parent.add_child(instance=FrameworkMainPage(title=title, slug=slug, **kwargs))
    page.save_revision().publish()
    return FrameworkMainPage.objects.get(pk=page.pk)


def role_url(main_page: FrameworkMainPage, role: GovukRole | str) -> str:
    slug = role if isinstance(role, str) else role.slug
    return main_page.url + main_page.reverse_subpage("serve_role", args=[slug])


def mcp_token(user=None, *, client_id: str = "test-client", lifetime=None) -> str:
    """A working MCP access token for ``user`` (a new reader by default), as the OAuth flow would issue."""
    import secrets
    from datetime import timedelta

    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from govuk.mcp.auth import SCOPE, digest, resource_url
    from govuk.models import MCPAccessToken, MCPClient

    user = user or get_user_model().objects.create_user(username=f"mcp-{secrets.token_hex(4)}", password="unused")
    client, _ = MCPClient.objects.get_or_create(
        client_id=client_id,
        defaults={
            "name": "Test app",
            "information": {"client_id": client_id, "redirect_uris": ["http://127.0.0.1:33418/callback"]},
        },
    )
    token = secrets.token_urlsafe(32)
    MCPAccessToken.objects.create(
        token_hash=digest(token),
        client=client,
        user=user,
        scopes=[SCOPE],
        resource=resource_url(),
        expires_at=timezone.now() + (lifetime or timedelta(hours=1)),
    )
    return token

"""The MCP sign-in pages on the site, and the root /.well-known/ discovery documents."""

from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.csp import CSP
from django.views.decorators.http import require_http_methods
from mcp.server.auth.provider import construct_redirect_uri

from govuk.features import ai_enabled
from govuk.models import MCPAuthorisationRequest, MCPClient, MCPConsent

from . import metadata
from .auth import issue_code, revoke_for


def _ai_on():
    if not ai_enabled():
        raise Http404


@require_http_methods(["GET", "HEAD"])
def protected_resource_view(request):
    _ai_on()
    return JsonResponse(metadata.protected_resource())


@require_http_methods(["GET", "HEAD"])
def authorization_server_view(request):
    _ai_on()
    return JsonResponse(metadata.authorization_server())


def _back_to_client(pending: MCPAuthorisationRequest, **params) -> HttpResponseRedirect:
    return HttpResponseRedirect(construct_redirect_uri(pending.redirect_uri, state=pending.state or None, **params))


@login_required
@require_http_methods(["GET", "POST"])
def authorise_view(request, request_id):
    """Asks once per app; after that the reader is sent straight back with a code."""
    _ai_on()
    pending = (
        MCPAuthorisationRequest.objects.select_related("client")
        .filter(request_id=request_id, expires_at__gt=timezone.now())
        .first()
    )
    if pending is None:
        return render(request, "mcp_auth/expired.html", status=404)
    agreed = MCPConsent.objects.filter(user=request.user, client=pending.client).exists()
    if request.method == "POST" and request.POST.get("decision") != "allow":
        response = _back_to_client(pending, error="access_denied")
        pending.delete()
        return response
    if request.method == "POST" or agreed:
        MCPConsent.objects.get_or_create(user=request.user, client=pending.client)
        response = _back_to_client(pending, code=issue_code(pending, request.user))
        return response
    redirect = urlsplit(pending.redirect_uri)
    response = render(
        request,
        "mcp_auth/authorise.html",
        {"client_name": pending.client.name or "An app", "redirect_host": redirect.netloc},
    )
    # The form's answer redirects to the app, which form-action 'self' would block.
    response._csp_config = {
        **settings.SECURE_CSP,
        "form-action": [CSP.SELF, f"{redirect.scheme}://{redirect.netloc}"],
    }
    return response


@login_required
@require_http_methods(["GET", "POST"])
def connected_apps_view(request):
    _ai_on()
    if request.method == "POST":
        client = MCPClient.objects.filter(pk=request.POST.get("client")).first()
        if client is not None:
            MCPConsent.objects.filter(user=request.user, client=client).delete()
            revoke_for(request.user, client)
        return HttpResponseRedirect(request.path)
    consents = MCPConsent.objects.filter(user=request.user).select_related("client").order_by("-created_at")
    return render(request, "mcp_auth/apps.html", {"consents": consents})

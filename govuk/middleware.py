from __future__ import annotations

import logging

from django.conf import settings
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from wagtail.models import Site

from govuk.oidc import ADMIN_OIDC_NEXT_URL_KEY, build_oidc_login_url
from govuk.models import AuthenticatedRedirectSettings

logger = logging.getLogger(__name__)


class IncomingRequestDebugLoggingMiddleware:
    """Log inbound requests and headers when explicitly enabled via settings."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(settings, "INCOMING_REQUEST_INFO_LOGGING", False):
            request_headers = dict(request.headers.items())
            logger.info(
                "Incoming request: method=%s path=%s headers=%s",
                request.method,
                request.get_full_path(),
                request_headers,
            )
        return self.get_response(request)


class WellKnownCorsMiddleware:
    """Allow cross-origin reads for well-known endpoints."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.path_prefix = "/.well-known/"

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith(self.path_prefix):
            response.headers.setdefault("Access-Control-Allow-Origin", "*")
        return response


class AdminOIDCLoginMiddleware:
    """Force OIDC login for admin routes by redirecting to OIDC."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.admin_prefixes = ("/admin/", "/django-admin/")

    def __call__(self, request):
        if request.path.startswith(self.admin_prefixes) and not request.user.is_authenticated:
            next_url = request.get_full_path()
            request.session[ADMIN_OIDC_NEXT_URL_KEY] = next_url
            return redirect(build_oidc_login_url(next_url))
        return self.get_response(request)


class AuthenticatedUserRedirectMiddleware:
    """Redirect authenticated users using per-site Wagtail settings."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.safe_methods = {"GET", "HEAD"}
        self.admin_prefixes = ("/admin/", "/django-admin/")

    def __call__(self, request):
        redirect_url = self._get_redirect_url(request)
        if redirect_url:
            return redirect(redirect_url)
        return self.get_response(request)

    def _get_redirect_url(self, request) -> str | None:
        if request.method not in self.safe_methods:
            return None
        if not request.user.is_authenticated:
            return None
        if request.path.startswith(self.admin_prefixes):
            return None

        site = Site.find_for_request(request)
        if site is None:
            return None

        redirect_settings = AuthenticatedRedirectSettings.objects.filter(
            site=site
        ).first()
        if redirect_settings is None:
            return None

        redirect_rule = redirect_settings.redirect_rules.filter(
            source_path=request.path
        ).first()
        if redirect_rule is None:
            return None

        destination_path = redirect_rule.destination_path
        if destination_path == request.path:
            return None
        if not url_has_allowed_host_and_scheme(
            destination_path,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return None
        return destination_path

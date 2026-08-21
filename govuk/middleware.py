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


class SecurityHeadersMiddleware:
    """
    Add security headers not covered by Django's built-in SecurityMiddleware:

    - Permissions-Policy: restrict browser features this application does not use.
    - Cache-Control: no-store on authentication-related paths so browsers and
      shared proxies do not cache pages that may contain tokens or form state.
    """

    _PERMISSIONS_POLICY = (
        "accelerometer=(), camera=(), display-capture=(),"
        " geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
    )

    _NO_CACHE_PATHS = ("/login/", "/logout/", "/accounts/", "/oidc/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault("Permissions-Policy", self._PERMISSIONS_POLICY)
        if any(request.path.startswith(p) for p in self._NO_CACHE_PATHS):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


class AdminCSPMiddleware:
    """
    Override the CSP for /admin pages to allow specific CSP exceptions,
    which are used by the Wagtail admin user interface.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/admin/"):
            response._csp_config = settings.SECURE_CSP_ADMIN
        return response


class CorsMiddleware:
    """Allow cross-origin reads for well-known and API endpoints."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.path_prefixes = ["/.well-known/", "/api/"]

    def __call__(self, request):
        response = self.get_response(request)
        if any(request.path.startswith(p) for p in self.path_prefixes):
            response.headers.setdefault("Access-Control-Allow-Origin", "*")
            response.headers.setdefault("Access-Control-Allow-Credentials", "false")
        return response


class AdminOIDCLoginMiddleware:
    """Force OIDC login for admin routes by redirecting to OIDC."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.admin_prefixes = ("/admin/", "/django-admin/")

    def __call__(self, request):
        if (
            request.path.startswith(self.admin_prefixes)
            and not request.user.is_authenticated
        ):
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


class MaintenanceModeMiddleware:
    """Answer everything but the essentials with the service-unavailable page.

    Switched by the MAINTENANCE_MODE environment variable, so closing the
    service for a cutover is a configuration change, not a deployment. The
    health check stays open or the orchestrator would replace the instance,
    and the admin stays open so the people doing the work can see it.
    """

    EXEMPT_PREFIXES = (
        "/api/health/",
        "/admin",
        "/django-admin",
        "/accounts",
        "/_util",
        "/static",
        # The unavailable page's own dressing: the GOV.UK fonts and crest are
        # served through the /assets alias and the customised styles through
        # /gen, and a page explaining the closure should not arrive undressed.
        "/assets",
        "/gen",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        from django.shortcuts import render

        if not getattr(settings, "MAINTENANCE_MODE", False):
            return self.get_response(request)
        if request.path.startswith(self.EXEMPT_PREFIXES):
            return self.get_response(request)
        return render(
            request,
            "503.html",
            {"maintenance_resume_text": getattr(settings, "MAINTENANCE_RESUME_TEXT", "")},
            status=503,
        )

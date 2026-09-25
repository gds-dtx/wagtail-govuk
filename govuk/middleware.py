from __future__ import annotations

import logging

from django.conf import settings
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from wagtail.models import Site

from govuk.models import AuthenticatedRedirectSettings
from govuk.oidc import ADMIN_OIDC_NEXT_URL_KEY, build_oidc_login_url

logger = logging.getLogger(__name__)


class IncomingRequestDebugLoggingMiddleware:
    """Log inbound requests and headers when explicitly enabled via settings.

    A debugging aid for working out what CloudFront and the load balancer
    actually send, switched on by INCOMING_REQUEST_INFO_LOGGING and off in
    every deployed environment. Header names are always logged in full. Values
    are logged only for the headers named below; every other value is replaced
    by its length, because these lines go to CloudWatch and stay there for the
    log group's retention.

    Which headers arrived, and whether one was truncated, is what anyone is
    reading these for, and the name and the length answer that. So the list is
    of what to log rather than of what to hide: a header nobody here has
    thought of -- a new single sign-on scheme's, a future AWS one -- is then
    redacted by default rather than logged by default, and adding a credential
    to the request is not also a change to what this writes down.

    Two of the headers it does log are personal data: X-Forwarded-For and
    CloudFront-Viewer-Address carry the reader's IP address. Tracing a request
    through the proxy chain is the reason this switch exists, so they are
    logged -- but leave the switch on only as long as the debugging needs it.
    """

    #: Header names, lower-cased, whose values may be written to CloudWatch.
    LOGGED_HEADERS = frozenset(
        {
            # What the proxy chain did with the request, which is what this
            # switch was added to answer.
            "host",
            "via",
            "x-amzn-trace-id",
            "x-forwarded-for",
            "x-forwarded-host",
            "x-forwarded-port",
            "x-forwarded-proto",
            "x-request-id",
            # What CloudFront tells the origin about the viewer: device and
            # geography hints, named one by one rather than by prefix so that
            # a header AWS adds later is redacted until someone looks at it.
            "cloudfront-forwarded-proto",
            "cloudfront-is-android-viewer",
            "cloudfront-is-desktop-viewer",
            "cloudfront-is-ios-viewer",
            "cloudfront-is-mobile-viewer",
            "cloudfront-is-smarttv-viewer",
            "cloudfront-is-tablet-viewer",
            "cloudfront-viewer-address",
            "cloudfront-viewer-country",
            # What the browser asked for.
            "accept",
            "accept-encoding",
            "accept-language",
            "cache-control",
            "connection",
            "content-length",
            "content-type",
            "origin",
            "pragma",
            "referer",
            "upgrade-insecure-requests",
            "user-agent",
        }
    )

    def __init__(self, get_response):
        self.get_response = get_response

    @classmethod
    def _safe_headers(cls, headers) -> dict[str, str]:
        safe = {}
        for name, value in headers.items():
            if value and name.lower() not in cls.LOGGED_HEADERS:
                value = f"[redacted, {len(value)} chars]"
            safe[name] = value
        return safe

    def __call__(self, request):
        if getattr(settings, "INCOMING_REQUEST_INFO_LOGGING", False):
            logger.info(
                "Incoming request: method=%s path=%s headers=%s",
                request.method,
                request.get_full_path(),
                self._safe_headers(request.headers),
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

    Two switches close the service. The MaintenanceModeSettings.enabled toggle
    in the admin is planned maintenance: signed-in staff are let through so they
    can keep working, while everyone else meets the service-unavailable page.
    The MAINTENANCE_MODE environment variable is the emergency override: a
    code-free hard close for everyone but the exempt paths, for when the admin
    or database cannot be relied on. Either one closes the site.

    The health check stays open or the orchestrator would replace the instance,
    and the admin stays open so the people doing the work can sign in. This
    middleware runs after the auth middleware so request.user is populated.
    """

    # Every prefix ends in its separator. Without the trailing slash "/admin"
    # is also a prefix of "/admin-guidance", so a content page whose slug
    # happens to start with one of these words would stay open.
    EXEMPT_PREFIXES = (
        "/api/health/",
        "/admin/",
        "/django-admin/",
        "/accounts/",
        "/_util/",
        "/static/",
        # The unavailable page's own dressing: the GOV.UK fonts and crest are
        # served through the /assets alias and the customised styles through
        # /gen, and a page explaining the closure should not arrive undressed.
        "/assets/",
        "/gen/",
    )

    # The roots themselves, which carry no trailing slash. Asking for /admin
    # should reach Django's APPEND_SLASH redirect to /admin/ rather than the
    # unavailable page; matching these exactly keeps /admin-guidance closed.
    EXEMPT_PATHS = (
        "/api/health",
        "/admin",
        "/django-admin",
        "/accounts",
        "/_util",
        "/static",
        "/assets",
        "/gen",
    )

    # RFC 9110 section 10.2.3: how long a client should wait before asking
    # again. Without it a crawler is free to retry immediately and a browser
    # has nothing to go on, so a planned hour's cutover reads as a permanent
    # failure. An hour is the default because that is the order of a cutover;
    # MAINTENANCE_RETRY_AFTER overrides it.
    DEFAULT_RETRY_AFTER_SECONDS = 3600

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        from django.shortcuts import render
        from wagtail.models import Site

        from govuk.models import MaintenanceModeSettings

        env_on = getattr(settings, "MAINTENANCE_MODE", False)
        site = Site.find_for_request(request)
        maintenance = MaintenanceModeSettings.for_site(site) if site else None
        toggle_on = bool(maintenance and maintenance.enabled)

        if not (env_on or toggle_on):
            return self.get_response(request)

        # Planned maintenance (the admin toggle) lets signed-in staff carry on;
        # the emergency env override is a hard close for everyone but the
        # exempt paths.
        if toggle_on and not env_on and request.user.is_authenticated:
            return self.get_response(request)

        if request.path in self.EXEMPT_PATHS or request.path.startswith(
            self.EXEMPT_PREFIXES
        ):
            return self.get_response(request)

        response = render(
            request,
            "503.html",
            {
                "maintenance_settings": maintenance,
                "maintenance_resume_text": getattr(
                    settings, "MAINTENANCE_RESUME_TEXT", ""
                ),
            },
            status=503,
        )
        response["Retry-After"] = str(
            getattr(
                settings, "MAINTENANCE_RETRY_AFTER", self.DEFAULT_RETRY_AFTER_SECONDS
            )
        )
        return response

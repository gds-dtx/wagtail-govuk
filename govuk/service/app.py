"""The service's Starlette app: contributed routes plus maintenance and rate limits.

Planned maintenance closes the service too, as there are no signed-in staff to
let through. The health check is exempt, or the orchestrator would replace a
closed or busy container.
"""

import logging
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route
from wagtail.models import Site

from govuk import rate_limit
from govuk.api import _check_database_health
from govuk.models import MaintenanceModeSettings

from . import HEALTH_PATH
from .db import release_connections

logger = logging.getLogger(__name__)

TOO_MANY = {"error": "Too many requests. Try again in a minute."}


@dataclass(frozen=True)
class RateLimit:
    """Requests a minute per client to paths under ``prefix``; 0 is no limit."""

    prefix: str
    namespace: str
    limit: int
    body: dict = field(default_factory=lambda: dict(TOO_MANY))

    def applies_to(self, path: str) -> bool:
        return path == self.prefix.rstrip("/") or path.startswith(self.prefix.rstrip("/") + "/")


@dataclass
class Contribution:
    name: str
    routes: list[BaseRoute] = field(default_factory=list)
    lifespan: Callable[[], AbstractAsyncContextManager] | None = None
    rate_limits: list[RateLimit] = field(default_factory=list)
    # Starlette ``Middleware``, applied inside maintenance and the rate limit.
    middleware: list = field(default_factory=list)


def contributions() -> list[Contribution]:
    found = []
    for path in settings.SERVICE_CONTRIBUTIONS:
        contribution = import_string(path)()
        if contribution is not None:
            found.append(contribution)
    return found


def _planned_maintenance() -> bool:
    try:
        site = Site.objects.filter(is_default_site=True).first()
        return bool(site and MaintenanceModeSettings.for_site(site).enabled)
    finally:
        release_connections()


def _database_healthy() -> bool:
    try:
        return _check_database_health()
    finally:
        release_connections()


class MaintenanceMiddleware:
    """The admin's switch is read at most every ``SERVICE_MAINTENANCE_CACHE_SECONDS``, not per request;
    the ``MAINTENANCE_MODE`` override applies at once."""

    def __init__(self, app):
        self.app = app
        self._checked_until = 0.0
        self._planned = False

    async def _closed(self) -> bool:
        if settings.MAINTENANCE_MODE:
            return True
        now = time.monotonic()
        if now >= self._checked_until:
            # One request refreshes it; the rest keep the value held meanwhile, so few reach the shared thread.
            self._checked_until = now + settings.SERVICE_MAINTENANCE_CACHE_SECONDS
            self._planned = await sync_to_async(_planned_maintenance)()
        return self._planned

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != HEALTH_PATH:
            if await self._closed():
                response = JSONResponse(
                    {"error": "This service is closed for maintenance. Try again later."},
                    status_code=503,
                    headers={"Retry-After": str(settings.MAINTENANCE_RETRY_AFTER)},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def scope_client(scope, client_ip_header: str) -> str:
    if client_ip_header:
        wanted = client_ip_header.lower().encode("latin-1")
        for name, value in scope.get("headers") or []:
            if name == wanted:
                address = rate_limit.first_address(value.decode("latin-1"))
                if address:
                    return address
    return (scope.get("client") or ("unknown", 0))[0]


class RateLimitMiddleware:
    def __init__(self, app, *, limits: list[RateLimit], client_ip_header: str = ""):
        self.app = app
        self.limits = [limit for limit in limits if limit.limit > 0]
        self.client_ip_header = client_ip_header

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        rule = next((limit for limit in self.limits if path != HEALTH_PATH and limit.applies_to(path)), None)
        if rule is None:
            await self.app(scope, receive, send)
            return
        client = scope_client(scope, self.client_ip_header)
        count, retry_after = await rate_limit.ahit(rule.namespace, client)
        if count > rule.limit:
            if count == rule.limit + 1:
                logger.warning("Service rate limit reached", extra={"namespace": rule.namespace, "limit": rule.limit})
            response = JSONResponse(
                rule.body,
                status_code=429,
                headers={"Retry-After": str(retry_after), "Cache-Control": "no-store, private"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


async def health(request: Request) -> JSONResponse:
    database_ok = await sync_to_async(_database_healthy)()
    return JSONResponse(
        {"health": {"database": "ok" if database_ok else "error"}},
        status_code=200 if database_ok else 503,
    )


def create_app() -> Starlette:
    found = contributions()
    if not found:
        raise ImproperlyConfigured("The service has nothing to serve: the AI features are off (FEATURE_AI).")

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as stack:
            for contribution in found:
                if contribution.lifespan is not None:
                    await stack.enter_async_context(contribution.lifespan())
            yield

    routes: list[BaseRoute] = [Route(HEALTH_PATH, health, methods=["GET"])]
    for contribution in found:
        routes += contribution.routes
    app = Starlette(routes=routes, lifespan=lifespan)
    # The last added runs first: the rate limit, then maintenance, then the contributions' own.
    for contribution in found:
        for middleware in reversed(contribution.middleware):
            app.add_middleware(middleware.cls, *middleware.args, **middleware.kwargs)
    app.add_middleware(MaintenanceMiddleware)
    app.add_middleware(
        RateLimitMiddleware,
        limits=[limit for contribution in found for limit in contribution.rate_limits],
        client_ip_header=settings.CLIENT_IP_HEADER,
    )
    return app

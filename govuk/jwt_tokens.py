from datetime import timedelta
from typing import Any

from wagtail.models import Site

from govuk.models import DEFAULT_JWT_LIFETIME, EdDSAKeySettings


def generate_site_jwt(
    *,
    site: Site,
    htu: str | None = None,
    htm: str | None = None,
    lifetime: timedelta = DEFAULT_JWT_LIFETIME,
    add_jti: bool = False,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    key_settings = EdDSAKeySettings.for_site(site)
    return key_settings.generate_jwt(
        htu=htu,
        htm=htm,
        lifetime=lifetime,
        add_jti=add_jti,
        extra_claims=extra_claims,
    )

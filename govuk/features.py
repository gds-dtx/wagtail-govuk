"""Feature flag checks, so callers need not import a feature's own package."""

from django.conf import settings


def catalogue_enabled() -> bool:
    return bool(settings.FEATURE_FLAGS.get("CATALOGUE"))

"""Feature flag checks, so callers need not import a feature's own package."""

from django.conf import settings


def catalogue_enabled() -> bool:
    return bool(settings.FEATURE_FLAGS.get("CATALOGUE"))


def ai_enabled() -> bool:
    """One flag for the content index, MCP server and AI search, which depend on one another."""
    return bool(settings.FEATURE_FLAGS.get("AI"))


def content_index_enabled() -> bool:
    return ai_enabled()

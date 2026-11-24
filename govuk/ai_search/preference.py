"""A signed-in reader's "Use AI search" choice, remembered with their account."""

from govuk.models import AISearchPreference

_CACHED = "_govuk_ai_search_use_ai"


def remembered(user) -> bool:
    """The reader's last choice; ticked until they make one. Looked up once per request."""
    if user is None or not user.is_authenticated:
        return True
    if not hasattr(user, _CACHED):
        stored = AISearchPreference.objects.filter(user=user).values_list("use_ai", flat=True).first()
        setattr(user, _CACHED, True if stored is None else stored)
    return getattr(user, _CACHED)


def remember(user, use_ai: bool) -> None:
    """Saved only when it changes."""
    if not user.is_authenticated or remembered(user) == use_ai:
        return
    AISearchPreference.objects.update_or_create(user=user, defaults={"use_ai": use_ai})
    setattr(user, _CACHED, use_ai)

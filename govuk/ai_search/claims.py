"""Single-use tokens and per-account limits, in the database so every task shares them."""

import time
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from govuk.models import AISearchClaim

MATCHES, SUMMARY = AISearchClaim.Kind.MATCHES, AISearchClaim.Kind.SUMMARY
KEEP = timedelta(days=2)
PRUNE_EVERY_SECONDS = 10 * 60
MINUTE, DAY = "minute", "day"

_next_prune = 0.0


def account_for(user_id) -> str:
    """A keyed hash, so the table never names a user."""
    if user_id is None:
        return ""
    return salted_hmac("govuk.ai_search.account", str(user_id)).hexdigest()


def claim(kind: str, token_id: str, account: str = "") -> bool:
    """False if the token was already used."""
    if not token_id:
        return False
    _prune()
    try:
        with transaction.atomic():
            AISearchClaim.objects.create(kind=kind, token_id=token_id, account=account)
    except IntegrityError:
        return False
    return True


def account_limit_reached(account: str) -> str | None:
    """MINUTE or DAY when the account has used its Claude-backed questions, else None."""
    if not account:
        return None
    asked = AISearchClaim.objects.filter(kind=MATCHES, account=account)
    now = timezone.now()
    per_minute = settings.AI_SEARCH_ACCOUNT_RATE_LIMIT_PER_MINUTE
    if per_minute > 0 and asked.filter(created_at__gt=now - timedelta(minutes=1)).count() >= per_minute:
        return MINUTE
    per_day = settings.AI_SEARCH_ACCOUNT_DAILY_LIMIT
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if per_day > 0 and asked.filter(created_at__gte=midnight).count() >= per_day:
        return DAY
    return None


def _prune() -> None:
    global _next_prune
    if time.monotonic() < _next_prune:
        return
    _next_prune = time.monotonic() + PRUNE_EVERY_SECONDS
    AISearchClaim.objects.filter(created_at__lt=timezone.now() - KEEP).delete()

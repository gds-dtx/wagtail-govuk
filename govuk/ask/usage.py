"""What Claude used for Ask today, against the daily token budget.

One ``AskUsage`` row a day (UTC), added to with ``F()`` expressions so every
task's calls land in the same totals. The budget is checked before each
call: a call already under way finishes, so a day can end a little over it.
"""

import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from govuk.models import AskUsage

logger = logging.getLogger(__name__)

_warned_days: set = set()


def _today():
    return timezone.now().date()


def tokens_used_today() -> int:
    row = AskUsage.objects.filter(day=_today()).first()
    return row.total_tokens if row else 0


def over_budget() -> bool:
    """Whether today's budget is spent. A budget of 0 allows no calls."""
    day = _today()
    over = tokens_used_today() >= settings.ASK_DAILY_TOKEN_BUDGET
    if over and day not in _warned_days:
        _warned_days.add(day)
        logger.warning("Ask's daily token budget is spent", extra={"budget": settings.ASK_DAILY_TOKEN_BUDGET})
    return over


def record(usage, *, outcome: str = "ok") -> None:
    """Add a call to today's row. ``outcome`` is "ok", "refused" or "failed"."""
    day = _today()
    try:
        with transaction.atomic():
            AskUsage.objects.get_or_create(day=day)
    except IntegrityError:  # another task made the row first
        pass
    changes = {"calls": F("calls") + 1}
    if usage is not None:
        changes.update(
            input_tokens=F("input_tokens") + usage.input_tokens,
            output_tokens=F("output_tokens") + usage.output_tokens,
            cache_read_tokens=F("cache_read_tokens") + usage.cache_read_tokens,
            cache_write_tokens=F("cache_write_tokens") + usage.cache_write_tokens,
        )
    if outcome == "refused":
        changes["refusals"] = F("refusals") + 1
    elif outcome == "failed":
        changes["failures"] = F("failures") + 1
    AskUsage.objects.filter(day=day).update(**changes)

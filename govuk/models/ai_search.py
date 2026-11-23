"""Daily Claude usage for AI search, shared across tasks so
``AI_DAILY_TOKEN_BUDGET`` holds. Stores no questions or answers."""

from django.conf import settings
from django.db import models
from django.utils import timezone


class AISearchUsage(models.Model):
    """One UTC day's Claude usage."""

    day = models.DateField(unique=True)
    calls = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveBigIntegerField(default=0)
    output_tokens = models.PositiveBigIntegerField(default=0)
    cache_read_tokens = models.PositiveBigIntegerField(default=0)
    cache_write_tokens = models.PositiveBigIntegerField(default=0)
    refusals = models.PositiveIntegerField(default=0)
    failures = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "AI search usage"
        verbose_name_plural = "AI search usage"
        ordering = ["-day"]

    def __str__(self) -> str:
        return f"{self.day}: {self.total_tokens:,} tokens"

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens


class AISearchClaim(models.Model):
    """One use of a token, so each is good once, and per-account counts shared across tasks.

    ``account`` is a keyed hash of the user id, blank when signed out. Rows are
    kept two days. No question is stored.
    """

    class Kind(models.TextChoices):
        MATCHES = "matches", "Matches"
        SUMMARY = "summary", "Summary"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    token_id = models.CharField(max_length=32)
    account = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["kind", "token_id"], name="ai_search_claim_once")]
        indexes = [models.Index(fields=["kind", "account", "created_at"], name="ai_search_claim_account")]

    def __str__(self) -> str:
        return f"{self.kind} {self.created_at:%Y-%m-%d %H:%M}"


class AISearchPreference(models.Model):
    """A signed-in reader's "Use AI search" choice, kept with the account. No row means ticked."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_search_preference")
    use_ai = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"AI search {'on' if self.use_ai else 'off'}"

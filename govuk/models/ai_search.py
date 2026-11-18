"""Daily Claude usage for AI search, shared across tasks so
``AI_DAILY_TOKEN_BUDGET`` holds. Stores no questions or answers."""

from django.db import models


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

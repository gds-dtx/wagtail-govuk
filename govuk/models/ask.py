"""Ask's switches in the admin, and its use of Claude day by day.

``AskSettings`` turns each use of Claude on or off without a deploy;
``AskUsage`` counts what Claude used, one row a day, shared by every task,
so the daily token budget (``ASK_DAILY_TOKEN_BUDGET``) holds across them.
Neither holds a question or an answer.
"""

from django.db import models
from wagtail.admin.panels import FieldPanel, HelpPanel
from wagtail.contrib.settings.models import BaseSiteSetting


class AskSettings(BaseSiteSetting):
    """Registered only while Ask is on (``wagtail_hooks``)."""

    use_ai_ranking = models.BooleanField(
        default=False,
        verbose_name="Re-rank matches with AI",
        help_text=(
            "Claude reads the closest matches to a question and chooses which "
            "answer it, best first. Off, the matches are ranked by search alone."
        ),
    )
    show_ai_summary = models.BooleanField(
        default=False,
        verbose_name="Show an AI summary",
        help_text=(
            "Claude writes a short summary above the matches, from the matches "
            "alone, marked as written by AI."
        ),
    )

    panels = [
        HelpPanel(
            content=(
                "<p>Both need Claude to be set up for this instance (ASK_CLAUDE). "
                "Questions and summaries are never stored. If Claude is "
                "unavailable, refuses, or the day's token budget is spent, the "
                "page shows the matches without it.</p>"
            )
        ),
        FieldPanel("use_ai_ranking"),
        FieldPanel("show_ai_summary"),
    ]

    class Meta:
        verbose_name = "Ask"
        verbose_name_plural = "Ask"


class AskUsage(models.Model):
    """What Claude used for Ask on one day (UTC)."""

    day = models.DateField(unique=True)
    calls = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveBigIntegerField(default=0)
    output_tokens = models.PositiveBigIntegerField(default=0)
    cache_read_tokens = models.PositiveBigIntegerField(default=0)
    cache_write_tokens = models.PositiveBigIntegerField(default=0)
    refusals = models.PositiveIntegerField(default=0)
    failures = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Ask usage"
        verbose_name_plural = "Ask usage"
        ordering = ["-day"]

    def __str__(self) -> str:
        return f"{self.day}: {self.total_tokens:,} tokens"

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens

"""Ask's switches in the admin, and its use of Claude day by day.

``AskSettings`` turns each use of Claude on or off without a deploy;
``AskUsage`` counts what Claude used, one row a day, shared by every task,
so the daily token budget (``ASK_DAILY_TOKEN_BUDGET``) holds across them.
Neither holds a question or an answer.
"""

from html import escape

from django import forms
from django.core.validators import MaxLengthValidator
from django.db import models
from wagtail.admin.panels import FieldPanel, HelpPanel, MultiFieldPanel
from wagtail.contrib.settings.models import BaseSiteSetting

from govuk.ask import prompts


def _built_in(kind: str, label: str) -> HelpPanel:
    """The built-in instructions, shown beside the field that can replace them."""
    return HelpPanel(
        heading=f"Built-in {label} instructions",
        content=(
            "<p>Used while the field below is blank. Copy them into it to start "
            "from them. <code>{site_name}</code> is replaced with the site's name.</p>"
            f'<pre style="white-space: pre-wrap">{escape(prompts.DEFAULTS[kind])}</pre>'
        ),
    )


def _instructions_field(label: str) -> models.TextField:
    return models.TextField(
        blank=True,
        verbose_name=f"{label} instructions",
        validators=[MaxLengthValidator(prompts.MAX_INSTRUCTIONS_CHARS)],
        help_text=(
            "Leave blank to use the built-in instructions. Write {site_name} for "
            "the site's name. The site always adds a line after these telling "
            "Claude to ignore any instructions in the question or the pages. The "
            "shape of Claude's reply is fixed whatever these say."
        ),
    )


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

    rerank_instructions = _instructions_field("Re-ranking")
    summary_instructions = _instructions_field("Summary")

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
        MultiFieldPanel(
            [
                _built_in("rerank", "re-ranking"),
                FieldPanel("rerank_instructions", widget=forms.Textarea(attrs={"rows": 20})),
            ],
            heading="Re-ranking instructions",
            classname="collapsed",
        ),
        MultiFieldPanel(
            [
                _built_in("summary", "summary"),
                FieldPanel("summary_instructions", widget=forms.Textarea(attrs={"rows": 20})),
            ],
            heading="Summary instructions",
            classname="collapsed",
        ),
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

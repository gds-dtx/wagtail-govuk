
from django.conf import settings
from django.db import models
from django.utils.text import Truncator


class Feedback(models.Model):
    class FeedbackType(models.TextChoices):
        CORRECTION = "correction", "Correction"
        FEATURE_SUGGESTION = "feature_suggestion", "Feature suggestion"
        BUG_REPORT = "bug_report", "Bug report"
        CONTENT_REQUEST = "content_request", "Content request"
        GENERAL = "general", "General feedback"
        OTHER = "other", "Other"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feedback_entries",
    )
    name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    feedback_type = models.CharField(
        max_length=40,
        choices=FeedbackType.choices,
        default=FeedbackType.GENERAL,
    )
    comments = models.TextField()
    referrer = models.CharField(max_length=500, blank=True)
    browser = models.CharField(max_length=255, blank=True)
    is_mobile = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        label = self.name or "Unknown user"
        return f"{label} - {self.get_feedback_type_display()}"

    def feedback_type_label(self) -> str:
        return self.get_feedback_type_display()

    feedback_type_label.short_description = "Type"

    def comments_preview(self) -> str:
        return Truncator(self.comments).chars(50)

    comments_preview.short_description = "Feedback"


class PageUsefulnessVote(models.Model):
    """One reader's yes/no answer to "Is this page useful?".

    Stores no PII: just the answer, the page path it was given on, the site,
    and when. The answer is also written to the ``govuk.page_feedback`` log by
    ``page_feedback_view``; this row is the durable record the admin
    "Page usefulness" report reads. Rows are pruned by
    ``prune_page_usefulness_votes``.
    """

    ANSWERS = [("yes", "Yes"), ("no", "No")]

    answer = models.CharField(max_length=3, choices=ANSWERS)
    path = models.CharField(max_length=500, db_index=True)
    site = models.ForeignKey(
        "wagtailcore.Site",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.path} - {self.get_answer_display()}"





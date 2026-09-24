"""The admin "Page usefulness" report.

Reads the ``PageUsefulnessVote`` rows written by ``page_feedback_view`` and
shows, per page path, how many readers answered "Yes" and how many "No". Counts
are pooled across sites (the report groups by ``path`` alone). The report lives
under the admin Reports menu; ``wagtail_hooks`` registers its URLs and menu item.

The on-screen table's queryset is a grouped ``.values("path").annotate(...)`` —
Wagtail resolves column values with ``multigetattr``, which does dictionary
lookups first, so the annotated dict rows render without wrapper objects. The
date filter references the plain ``created_at`` column, so it is applied as a
WHERE clause before the GROUP BY and the counts reflect the chosen range.

The export is different: rather than the aggregated counts, it returns one row
per vote (page, answer, time), so the raw votes for the selected period can be
analysed elsewhere. ``get_queryset`` branches on ``self.is_export`` (set by
Wagtail's spreadsheet mixin in ``setup()``, before ``get_queryset`` runs), and
``list_export``/``export_headings`` name the per-vote fields — those only affect
the export, so the displayed columns are unchanged. The same date filter narrows
the exported rows to the chosen range.
"""

import django_filters
from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _
from wagtail.admin.filters import DateRangePickerWidget, WagtailFilterSet
from wagtail.admin.ui.tables import Column
from wagtail.admin.views.reports import ReportView

from govuk.models import PageUsefulnessVote


class PageUsefulnessReportFilterSet(WagtailFilterSet):
    created_at = django_filters.DateFromToRangeFilter(
        label=_("Date"),
        widget=DateRangePickerWidget,
    )

    class Meta:
        model = PageUsefulnessVote
        fields = ["created_at"]


class PageUsefulnessReportView(ReportView):
    page_title = _("Page usefulness")
    header_icon = "help"
    model = PageUsefulnessVote
    filterset_class = PageUsefulnessReportFilterSet
    index_url_name = "govuk_page_usefulness_report"
    index_results_url_name = "govuk_page_usefulness_report_results"
    columns = [
        Column("path", label=_("Page")),
        Column("yes_count", label=_("Yes")),
        Column("no_count", label=_("No")),
        Column("total", label=_("Total")),
    ]
    # The export is one row per vote, not the aggregated counts shown on screen.
    list_export = ["path", "get_answer_display", "created_at"]
    export_headings = {
        "path": _("Page"),
        "get_answer_display": _("Answer"),
        "created_at": _("Time"),
    }
    export_filename = "page-usefulness-report"

    def get_queryset(self):
        if self.is_export:
            # One PageUsefulnessVote per row: page, answer, and time. The date
            # filter still applies, narrowing the export to the chosen range.
            self.queryset = PageUsefulnessVote.objects.order_by("-created_at", "-id")
        else:
            self.queryset = (
                PageUsefulnessVote.objects.values("path")
                .annotate(
                    yes_count=Count("pk", filter=Q(answer="yes")),
                    no_count=Count("pk", filter=Q(answer="no")),
                    total=Count("pk"),
                )
                .order_by("-total", "path")
            )
        return super().get_queryset()

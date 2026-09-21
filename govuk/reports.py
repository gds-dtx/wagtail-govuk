"""The admin "Page usefulness" report.

Reads the ``PageUsefulnessVote`` rows written by ``page_feedback_view`` and
shows, per page path, how many readers answered "Yes" and how many "No". Counts
are pooled across sites (the report groups by ``path`` alone). The report lives
under the admin Reports menu; ``wagtail_hooks`` registers its URLs and menu item.

The queryset is a grouped ``.values("path").annotate(...)`` — Wagtail resolves
column and export values with ``multigetattr``, which does dictionary lookups
first, so the annotated dict rows render and export without wrapper objects. The
date filter references the plain ``created_at`` column, so it is applied as a
WHERE clause before the GROUP BY and the counts reflect the chosen range.
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
    list_export = ["path", "yes_count", "no_count", "total"]
    export_headings = {
        "path": _("Page"),
        "yes_count": _("Yes"),
        "no_count": _("No"),
        "total": _("Total"),
    }
    export_filename = "page-usefulness-report"

    def get_queryset(self):
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

"""Dashboard widgets for netbox_facts.

Pending facts changes are otherwise discoverable only by opening reports
one at a time, so the plugin offers a widget that puts the backlog on the
NetBox home page.

Registration happens at import time, so this module has to be imported for
the widget to appear in the widget picker. The plugin's ready() does that.
"""

from django.http import QueryDict
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from extras.dashboard.utils import register_widget
from extras.dashboard.widgets import DashboardWidget

from .choices import ReportStatusChoices
from .models import FactsReport, FactsReportEntry

#: Report statuses that still hold entries nobody has decided on. A report
#: leaves this set only once every entry has been applied, skipped or has
#: failed, which is exactly what _update_report_status() records.
REVIEW_REPORT_STATUSES = (
    ReportStatusChoices.STATUS_PENDING,
    ReportStatusChoices.STATUS_PARTIAL,
)


def review_list_url():
    """Return the facts report list URL filtered to the reports awaiting review."""
    params = QueryDict(mutable=True)
    params.setlist("status", list(REVIEW_REPORT_STATUSES))
    return f"{reverse('plugins:netbox_facts:factsreport_list')}?{params.urlencode()}"


def pending_facts_counts(user):
    """Return the pending-entry and awaiting-review report counts for a user.

    The report count is deliberately taken from report status rather than
    from which reports happen to hold a pending entry, so the number always
    matches the list that review_list_url() opens. The two can differ for a
    report whose run produced no entries at all; that report is still
    unresolved, and counting it keeps the figure and its link honest.
    """
    entries = FactsReportEntry.objects.restrict(user, "view").pending()
    reports = FactsReport.objects.restrict(user, "view").filter(status__in=REVIEW_REPORT_STATUSES)
    return {
        "pending_entries": entries.count(),
        "pending_reports": reports.count(),
    }


@register_widget
class PendingFactsChangesWidget(DashboardWidget):
    """Counts of the facts changes waiting for someone to accept or skip them."""

    default_title = _("Pending Facts Changes")
    description = _("Facts report entries awaiting review, and how many reports hold them.")
    template_name = "netbox_facts/dashboard/pending_facts_changes.html"
    width = 4
    height = 2

    def render(self, request):
        counts = pending_facts_counts(request.user)
        return render_to_string(
            self.template_name,
            {
                "counts": counts,
                "review_url": review_list_url(),
            },
        )

"""Shared plumbing for the views that list report entries under a parent.

Entries are listed in two places -- one tab per status on a report, and the
Facts tab on a device -- and both do it as an ObjectChildrenView over the
same child model, table and filterset. That block lives here so a change to
how entries are listed lands once; each subclass supplies only its own tab,
its own queryset, and whatever extra context its template reads.
"""

from netbox.views import generic

from . import filtersets, tables
from .entry_actions import entry_actions_for_status
from .models import FactsReportEntry

#: Permission gating every entry tab. Listing a report's entries is reading
#: the report, so whoever may view reports sees them wherever they appear.
ENTRY_TAB_PERMISSION = "netbox_facts.view_factsreport"


class FactsReportEntryChildrenView(generic.ObjectChildrenView):
    """Base for the tabs that list FactsReportEntry rows under a parent."""

    child_model = FactsReportEntry
    table = tables.FactsReportEntryTable
    filterset = filtersets.FactsReportEntryFilterSet
    actions = ()

    #: The entry status this tab lists, or None for a tab that does not list
    #: by status. It both rides along in the POST, so a select-all can be
    #: resolved back to this tab's entries server side, and selects the
    #: lifecycle buttons the template offers -- so a tab and its bulk
    #: controls cannot come to disagree about which transitions apply.
    entry_status = None

    def get_extra_context(self, request, instance):
        return {
            "entry_status": self.entry_status,
            "entry_actions": entry_actions_for_status(self.entry_status),
        }

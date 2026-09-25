"""The lifecycle transitions a report entry offers, keyed by its status.

An entry in a given status can be moved in exactly one set of ways, and the
review UI offers those moves twice over: as a per-row button group in the
entry table, and as a bulk button group on the matching report tab. Both
need the same facts about each transition -- which view performs it, what
it looks like, and what to call it -- so the table is written down once here
and both button groups are rendered from it.
"""

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from netbox_facts.choices import EntryStatusChoices

__all__ = (
    "ENTRY_ACTIONS_BY_STATUS",
    "EntryAction",
    "entry_actions_for_status",
)


@dataclass(frozen=True)
class EntryAction:
    """One lifecycle transition, as both button groups render it.

    ``name`` is the POST field a bulk button submits under; ``url_name`` is
    the report-scoped view that performs the transition. ``label`` names the
    button that acts on a single row and ``bulk_label`` the one that acts on
    a selection, kept as two strings rather than one plus a suffix so each
    reads naturally on its own when translated.
    """

    name: str
    url_name: str
    icon: str
    css_class: str
    label: str
    bulk_label: str


ENTRY_ACTIONS_BY_STATUS = {
    EntryStatusChoices.STATUS_PENDING: (
        EntryAction(
            name="apply",
            url_name="plugins:netbox_facts:factsreport_apply",
            icon="mdi-check",
            css_class="btn-green",
            label=_("Apply"),
            bulk_label=_("Apply Selected"),
        ),
        EntryAction(
            name="skip",
            url_name="plugins:netbox_facts:factsreport_skip",
            icon="mdi-close",
            css_class="btn-secondary",
            label=_("Skip"),
            bulk_label=_("Skip Selected"),
        ),
    ),
    EntryStatusChoices.STATUS_FAILED: (
        EntryAction(
            name="retry",
            url_name="plugins:netbox_facts:factsreport_retry",
            icon="mdi-refresh",
            css_class="btn-warning",
            label=_("Retry"),
            bulk_label=_("Retry Selected"),
        ),
    ),
    EntryStatusChoices.STATUS_SKIPPED: (
        EntryAction(
            name="unskip",
            url_name="plugins:netbox_facts:factsreport_unskip",
            icon="mdi-undo-variant",
            css_class="btn-secondary",
            label=_("Un-skip"),
            bulk_label=_("Un-skip Selected"),
        ),
    ),
}


def entry_actions_for_status(status):
    """Return the transitions an entry in this status offers.

    A status with nothing left to decide -- applied, or mid-apply -- offers
    none, which is what makes a tab render no lifecycle controls at all.
    """
    return ENTRY_ACTIONS_BY_STATUS.get(status, ())

"""Device page integration: the Facts tab attached to dcim.Device.

Operators work from the Device page, so the plugin answers three questions
there: what has this device reported that nobody has decided on yet, when
was it last seen by each kind of collector, and which plans would collect
it on their next run.

Registration happens at import time, so this module has to be imported
before any URLConf is loaded. The plugin's ready() does that.
"""

from dcim.models import Device
from django.db.models import Max
from django.db.models.functions import Coalesce
from django.utils.translation import gettext_lazy as _
from netbox.views import generic
from utilities.views import ViewTab, register_model_view

from . import filtersets, tables
from .choices import CollectionTypeChoices
from .models import CollectionPlan, FactsReportEntry

#: How many enabled plans the Facts tab is willing to resolve for one page
#: view. Plan scope is a set of m2m dimensions rather than a stored device
#: list, so membership can only be answered by running each plan's queryset:
#: the cost is one existence query per candidate. Capping the candidates
#: keeps that bounded no matter how many plans a deployment accumulates.
MAX_COVERING_PLANS = 20


def pending_entries(device):
    """Return the device's report entries that are still awaiting a decision."""
    return device.facts_entries.pending()


def pending_entry_count(device):
    """Return how many of the device's entries are awaiting a decision."""
    return pending_entries(device).count()


def has_facts_data(device):
    """Return True when the plugin has recorded any entry for this device."""
    return device.facts_entries.exists()


def last_collected_by_type(device):
    """Return the device's most recent collection timestamp per collector type.

    Freshness is read off the reports that produced this device's own
    entries, so it answers "when was this device last seen by a collector
    of this type" rather than the weaker "when did some plan of this type
    last run". A report that has not finished yet has no completion time,
    so its creation time stands in.

    One grouped aggregate covers every type, and the rows come back in the
    declared collector-type order so the panel does not reshuffle between
    page loads.
    """
    latest = dict(
        device.facts_entries.values_list("collector_type").annotate(
            last_collected=Max(Coalesce("report__completed_at", "report__created"))
        )
    )
    return [
        {"collector_type": value, "label": label, "last_collected": latest[value]}
        for value, label in CollectionTypeChoices
        if value in latest
    ]


def plans_covering_device(device, limit=MAX_COVERING_PLANS):
    """Return the enabled plans whose resolved scope includes this device.

    Returns a ``(plans, truncated)`` pair, where ``truncated`` reports that
    more enabled plans exist than were examined -- the caller should say so
    rather than present the list as exhaustive. Candidates are taken in
    name order so the cap falls the same way on every page load.
    """
    candidates = list(CollectionPlan.objects.filter(enabled=True).order_by("name")[: limit + 1])
    truncated = len(candidates) > limit
    return [plan for plan in candidates[:limit] if plan.get_devices_queryset().filter(pk=device.pk).exists()], truncated


@register_model_view(Device, "facts")
class DeviceFactsView(generic.ObjectChildrenView):
    """Facts tab on the Device detail page."""

    queryset = Device.objects.all()
    child_model = FactsReportEntry
    table = tables.FactsReportEntryTable
    filterset = filtersets.FactsReportEntryFilterSet
    actions = ()
    template_name = "netbox_facts/device_facts.html"
    tab = ViewTab(
        label=_("Facts"),
        # Visibility is answered by "has this device any entry at all", which is
        # deliberately not hide_if_empty: that flag tests the badge, and the badge
        # is the *pending* count, so it would also hide a device that has been
        # collected and is simply clean -- the device whose freshness and plan
        # coverage panels are most worth reading. ViewTab evaluates visible()
        # before the badge, so the badge keeps its pending-count meaning.
        visible=has_facts_data,
        badge=pending_entry_count,
        permission="netbox_facts.view_factsreport",
        weight=5000,
    )

    def get_children(self, request, parent):
        # The badge cannot be restricted -- ViewTab hands its callable only the
        # instance -- so the tab is gated on view_factsreport and the count may
        # exceed what an object-permission-constrained user sees listed here.
        return pending_entries(parent).restrict(request.user, "view")

    def get_extra_context(self, request, instance):
        covering_plans, plans_truncated = plans_covering_device(instance)
        return {
            "last_collected": last_collected_by_type(instance),
            "covering_plans": covering_plans,
            "plans_truncated": plans_truncated,
            "covering_plan_limit": MAX_COVERING_PLANS,
        }

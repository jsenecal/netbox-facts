"""Custom NetBox event types raised by this plugin."""

from core.models import ObjectType
from django.utils.translation import gettext as _
from extras.events import EventContext, enqueue_event, flush_events, get_snapshots
from netbox.context import current_request, events_queue
from netbox.events import EVENT_TYPE_KIND_SUCCESS, EventType, get_event_type
from netbox.models.features import has_feature

__all__ = (
    "REPORT_READY",
    "enqueue_report_ready",
    "register_event_types",
)

# Namespaced with the plugin module name so it cannot collide with a core
# event type or one registered by another plugin.
REPORT_READY = "netbox_facts.report_ready"


def register_event_types():
    """Register this plugin's event types with NetBox's event registry.

    Called from the plugin's ready(). The guard is deliberate: register()
    rejects a name that is already known, and an AppConfig's ready() can run
    more than once in a single process (autoreloader, test harnesses), which
    would otherwise turn a second pass into a startup failure.

    The text is translated eagerly, as NetBox core does: EventType.__str__
    returns it verbatim, and Python rejects a __str__ that hands back a lazy
    proxy instead of a string.
    """
    if get_event_type(REPORT_READY) is None:
        EventType(
            name=REPORT_READY,
            text=_("Facts report ready for review"),
            kind=EVENT_TYPE_KIND_SUCCESS,
        ).register()


def enqueue_report_ready(report):
    """Raise the report-ready event for a finalized FactsReport.

    Returns True if the event was raised, False if the report's model does
    not support event rules.

    Within a request context the event joins that request's queue and is
    flushed with it, so a failure later in the request suppresses it. A run
    started by the scheduler has no request to attach to, and its report is
    already saved by the time this is called, so there the event is handed
    to the pipeline directly rather than dropped -- an unattended nightly
    run is exactly the case a reviewer wants to hear about.
    """
    if not has_feature(report, "event_rules"):
        return False

    if request := current_request.get():
        queue = events_queue.get()
        enqueue_event(queue, report, request, REPORT_READY)
        events_queue.set(queue)
        return True

    # No 'request' key at all: consumers treat its presence as a promise that
    # the value is a usable request (NetBox's own job events do the same).
    flush_events(
        [
            EventContext(
                object_type=ObjectType.objects.get_for_model(report),
                object_id=report.pk,
                object=report,
                event_type=REPORT_READY,
                snapshots=get_snapshots(report, REPORT_READY),
                user=report.collection_plan.run_as,
            )
        ]
    )
    return True

"""Tests for the event fired when a Facts Report completes.

Regression coverage for issue #143: a finished report must raise a NetBox
event so reviewers can be notified by an event rule instead of polling the
report list.
"""

import uuid
from unittest.mock import MagicMock, patch

from core.models import ObjectType
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from netbox.context import events_queue
from netbox.context_managers import event_tracking
from netbox.events import EVENT_TYPE_KIND_SUCCESS, get_event_type
from netbox.models.features import get_model_features

from netbox_facts.choices import (
    CollectionTypeChoices,
    EntryActionChoices,
    ReportStatusChoices,
)
from netbox_facts.events import REPORT_READY, enqueue_report_ready, register_event_types
from netbox_facts.helpers.collector import NapalmCollector
from netbox_facts.models import FactsReport, FactsReportEntry
from netbox_facts.tests.test_helpers import CollectorTestMixin


def _collect_two_entries(self, driver):  # pylint: disable=unused-argument
    """Stand in for a real collector body, recording two pending entries."""
    for action in (EntryActionChoices.ACTION_NEW, EntryActionChoices.ACTION_CHANGED):
        FactsReportEntry.objects.create(
            report=self._report,
            action=action,
            collector_type=CollectionTypeChoices.TYPE_ARP,
            device=self._current_device,
            object_repr=f"MACAddress for {action}",
        )


def _collect_and_fail(self, driver):  # pylint: disable=unused-argument
    """Stand in for a collector body that dies mid-run."""
    raise RuntimeError("collector exploded")


class ReportReadyEventTypeTest(TestCase):
    """Tests for the plugin's custom event type registration."""

    def test_report_ready_event_type_is_registered(self):
        """The plugin registers its report-ready event type with NetBox."""
        event_type = get_event_type(REPORT_READY)

        self.assertIsNotNone(event_type)
        self.assertEqual(event_type.name, REPORT_READY)
        self.assertEqual(event_type.kind, EVENT_TYPE_KIND_SUCCESS)

    def test_report_ready_event_text_is_not_lazy(self):
        """EventType.__str__ hands back text verbatim, and a lazy proxy raises there."""
        self.assertIsInstance(get_event_type(REPORT_READY).text, str)

    def test_register_event_types_is_idempotent(self):
        """Re-registering is a no-op, so a second ready() cannot break startup."""
        register_event_types()
        register_event_types()

        self.assertIsNotNone(get_event_type(REPORT_READY))

    def test_facts_report_supports_event_rules(self):
        """FactsReport advertises the event_rules feature to NetBox."""
        self.assertIn("event_rules", get_model_features(FactsReport))


class ReportCompletionEventTest(CollectorTestMixin, TestCase):
    """Tests for the event queued when a collection run finalizes its report."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = get_user_model().objects.create_user(username="facts-events")

    def setUp(self):
        # post_migrate stamps the model's feature list onto its ObjectType row,
        # and the test database is reused across runs, so re-sync it here the
        # same way a migration would.
        object_type = ObjectType.objects.get_for_model(FactsReport)
        object_type.features = get_model_features(FactsReport)
        object_type.save()

        self.device = self._create_device("event-dev")
        self.plan = self._create_plan(CollectionTypeChoices.TYPE_ARP, detect_only=True)

    def _make_request(self):
        request = RequestFactory().get("/")
        request.id = uuid.uuid4()
        request.user = self.user
        return request

    def _make_collector(self):
        """Build a collector through __init__, unlike the mixin's variant.

        These tests drive the real execute(), which touches attributes the
        mixin's hand-assembled collector leaves unset.
        """
        collector = NapalmCollector(self.plan)
        collector._devices = [self.device]
        collector._napalm_driver = MagicMock()
        return collector

    def _run(self, collect, raises=None):
        """Run a collection with the collector body replaced, returning queued events."""
        request = self._make_request()
        collector = self._make_collector()
        with (
            patch.object(NapalmCollector, "arp", collect),
            patch(
                "netbox_facts.helpers.collector.get_connection_ips",
                return_value=[("10.0.0.1", "primary")],
            ),
        ):
            with event_tracking(request):
                if raises is not None:
                    with self.assertRaises(raises):
                        collector.execute()
                else:
                    collector.execute()
                queued = list(events_queue.get().values())
        return queued

    def test_finalization_queues_one_report_ready_event(self):
        """A completed run queues exactly one report-ready event for its report."""
        queued = self._run(_collect_two_entries)

        events = [event for event in queued if event["event_type"] == REPORT_READY]
        self.assertEqual(len(events), 1)
        report = FactsReport.objects.get()
        self.assertEqual(events[0]["object"], report)
        self.assertEqual(events[0]["object_id"], report.pk)
        self.assertEqual(events[0]["user"], self.user)

    def test_report_ready_event_carries_summary_and_status(self):
        """The queued event exposes the finalized counts and status to event rules."""
        queued = self._run(_collect_two_entries)

        event = next(event for event in queued if event["event_type"] == REPORT_READY)
        expected_summary = {
            EntryActionChoices.ACTION_NEW: 1,
            EntryActionChoices.ACTION_CHANGED: 1,
            EntryActionChoices.ACTION_CONFIRMED: 0,
            EntryActionChoices.ACTION_STALE: 0,
        }
        self.assertEqual(event["data"]["summary"], expected_summary)
        self.assertEqual(event["data"]["status"], ReportStatusChoices.STATUS_PENDING)
        self.assertEqual(event["snapshots"]["postchange"]["summary"], expected_summary)

    def test_failed_run_queues_no_report_ready_event(self):
        """A run that dies before finalization queues no report-ready event."""
        queued = self._run(_collect_and_fail, raises=RuntimeError)

        self.assertEqual([event for event in queued if event["event_type"] == REPORT_READY], [])
        self.assertEqual(FactsReport.objects.get().status, ReportStatusChoices.STATUS_FAILED)

    def test_report_ready_is_dispatched_without_a_request_context(self):
        """A scheduled run has no request, so its event goes straight to the pipeline."""
        report = FactsReport.objects.create(collection_plan=self.plan)

        with patch("netbox_facts.events.flush_events") as flush_events:
            self.assertTrue(enqueue_report_ready(report))

        flush_events.assert_called_once()
        (events,) = flush_events.call_args.args
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], REPORT_READY)
        self.assertEqual(events[0]["object"], report)
        # A 'request' key is a promise to consumers that the value is usable;
        # the webhook action copies it unconditionally once it is present.
        self.assertNotIn("request", events[0])

"""Tests for the per-device outcomes a collection run records.

Covers issue #144: a run writes one FactsReportDeviceOutcome per attempted
device -- with the reason it was passed over, how long it was dialed for and
how many entries it produced -- and a run that collected from no device at
all finishes FAILED with the distribution in its error message instead of
sitting Pending forever.
"""

import itertools
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from dcim.models import Platform
from django.test import TestCase
from napalm.base.exceptions import ConnectAuthError, ConnectionException
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from netbox_facts.api.serializers import (
    FactsReportDeviceOutcomeSerializer,
    FactsReportSerializer,
)
from netbox_facts.api.views import FactsReportDeviceOutcomeViewSet
from netbox_facts.choices import (
    FAILED_DEVICE_OUTCOMES,
    SKIPPED_DEVICE_OUTCOMES,
    CollectionTypeChoices,
    DeviceOutcomeChoices,
    EntryActionChoices,
    ReportStatusChoices,
)
from netbox_facts.filtersets import FactsReportDeviceOutcomeFilterSet
from netbox_facts.helpers.collector import DeviceSkipReasons, NapalmCollector
from netbox_facts.models import FactsReport, FactsReportDeviceOutcome, FactsReportEntry
from netbox_facts.models.outcomes import DEVICE_OUTCOME_COUNT_ANNOTATIONS
from netbox_facts.tests.test_helpers import CollectorTestMixin
from netbox_facts.views import FactsReportDevicesView


class DeviceOutcomeTaxonomyTest(TestCase):
    """The outcome choices must stay aligned with the collector's skip reasons."""

    def test_every_skip_reason_maps_to_an_outcome(self):
        """A reason the run can tally is a reason an outcome row can state."""
        self.assertEqual(
            set(DeviceSkipReasons.OUTCOMES),
            set(DeviceSkipReasons.LABELS),
        )
        for outcome in DeviceSkipReasons.OUTCOMES.values():
            self.assertIn(outcome, DeviceOutcomeChoices.values())

    def test_outcome_groups_partition_the_choices(self):
        """Every outcome counts as exactly one of collected, failed or skipped."""
        grouped = (DeviceOutcomeChoices.OUTCOME_OK, *FAILED_DEVICE_OUTCOMES, *SKIPPED_DEVICE_OUTCOMES)

        self.assertEqual(len(grouped), len(set(grouped)))
        self.assertEqual(set(grouped), set(DeviceOutcomeChoices.values()))


def _collect_two_entries(self, driver):
    """Stand in for a collector body, recording two entries for the device."""
    for action in (EntryActionChoices.ACTION_NEW, EntryActionChoices.ACTION_CHANGED):
        FactsReportEntry.objects.create(
            report=self._report,
            action=action,
            collector_type=CollectionTypeChoices.TYPE_ARP,
            device=self._current_device,
            object_repr=f"MACAddress for {action}",
        )


class DeviceOutcomeRunMixin(CollectorTestMixin):
    """Drives execute() with the dial path stubbed out.

    The platforms name real NAPALM drivers so a blank-driver plan resolves
    one per device, which is how a single run can reach every outcome.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.junos_platform = Platform.objects.create(name="Juniper Junos", slug="junos")
        cls.ios_platform = Platform.objects.create(name="Cisco IOS", slug="ios")
        cls.unknown_platform = Platform.objects.create(name="Mystery OS", slug="mystery-os")

    def _run(self, plan, devices, collect=None, open_session=None, get_ips=None, clock=None):
        """Run execute() against the given devices, returning its report."""
        collector = self._make_collector(plan)
        collector._napalm_driver = plan.get_napalm_driver()
        collector._devices = devices
        patches = [
            patch.object(NapalmCollector, plan.collector_type, collect or MagicMock()),
            patch(
                "netbox_facts.helpers.collector.get_connection_ips",
                side_effect=get_ips or (lambda *args, **kwargs: [("10.0.0.1", "primary")]),
            ),
            patch.object(
                NapalmCollector,
                "_open_napalm_session",
                side_effect=open_session or (lambda *args, **kwargs: MagicMock()),
            ),
        ]
        if clock is not None:
            patches.append(patch("netbox_facts.helpers.collector.monotonic", side_effect=clock))
        with ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            collector.execute()
        return collector._report

    @staticmethod
    def _ip_per_device(device, _target):
        """Give each device its own address, so a stub can refuse one of them."""
        return [(f"10.0.0.{device.pk}", "primary")]

    @classmethod
    def _ips_missing_for(cls, undialable):
        """Address every device but one, which has no usable IP at all."""

        def ips(device, target):
            if device.pk == undialable.pk:
                raise ValueError("no usable IP")
            return cls._ip_per_device(device, target)

        return ips

    def _arp_plan(self, name):
        return self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_ARP,
            name=name,
            napalm_driver="",
            detect_only=True,
        )

    @staticmethod
    def _outcomes(report):
        """Return the report's outcome rows keyed by device name."""
        return {row.device.name: row for row in report.device_outcomes.all()}


class DeviceOutcomeRecordingTest(DeviceOutcomeRunMixin, TestCase):
    """One outcome row per attempted device, carrying what the run learned."""

    def test_collected_device_records_ok_with_timing_and_entry_count(self):
        plan = self._arp_plan("out-ok")
        device = self._create_device("out-ok-dev", platform=self.junos_platform)

        report = self._run(
            plan,
            [device],
            collect=_collect_two_entries,
            clock=itertools.count(0, 0.5),
        )

        outcome = report.device_outcomes.get()
        self.assertEqual(outcome.device, device)
        self.assertEqual(outcome.outcome, DeviceOutcomeChoices.OUTCOME_OK)
        self.assertEqual(outcome.entry_count, 2)
        self.assertEqual(outcome.duration, 0.5)
        self.assertEqual(outcome.message, "")

    def test_unreachable_device_records_the_connection_failure(self):
        plan = self._arp_plan("out-unreachable")
        device = self._create_device("out-unreachable-dev", platform=self.junos_platform)

        def refuse(*args, **kwargs):
            raise ConnectionException("connection refused")

        report = self._run(plan, [device], open_session=refuse)

        outcome = report.device_outcomes.get()
        self.assertEqual(outcome.outcome, DeviceOutcomeChoices.OUTCOME_UNREACHABLE)
        self.assertEqual(outcome.entry_count, 0)
        # The device was dialed, so the time spent waiting on it is known.
        self.assertIsNotNone(outcome.duration)
        self.assertIn("connection refused", outcome.message)

    def test_auth_failure_is_recorded_apart_from_unreachable(self):
        """An authentication failure is a credential problem, not a dead host."""
        plan = self._arp_plan("out-auth")
        device = self._create_device("out-auth-dev", platform=self.junos_platform)

        def reject(*args, **kwargs):
            raise ConnectAuthError("bad password")

        report = self._run(plan, [device], open_session=reject)

        outcome = report.device_outcomes.get()
        self.assertEqual(outcome.outcome, DeviceOutcomeChoices.OUTCOME_AUTH_FAILED)
        self.assertIn("bad password", outcome.message)

    def test_device_without_an_ip_records_a_skip_and_no_duration(self):
        plan = self._arp_plan("out-no-ip")
        device = self._create_device("out-no-ip-dev", platform=self.junos_platform)

        def no_ips(*args, **kwargs):
            raise ValueError("no usable IP")

        report = self._run(plan, [device], get_ips=no_ips)

        outcome = report.device_outcomes.get()
        self.assertEqual(outcome.outcome, DeviceOutcomeChoices.OUTCOME_SKIPPED_NO_IP)
        # Never dialed, so there is no connection time to report.
        self.assertIsNone(outcome.duration)
        self.assertEqual(outcome.message, DeviceSkipReasons.LABELS[DeviceSkipReasons.NO_IP])

    def test_platformless_device_records_a_no_driver_skip(self):
        plan = self._arp_plan("out-no-driver")
        device = self._create_device("out-no-driver-dev")

        report = self._run(plan, [device])

        outcome = report.device_outcomes.get()
        self.assertEqual(outcome.outcome, DeviceOutcomeChoices.OUTCOME_SKIPPED_NO_DRIVER)
        self.assertIsNone(outcome.duration)

    def test_uninstalled_driver_records_a_driver_error(self):
        plan = self._arp_plan("out-unknown-driver")
        device = self._create_device("out-unknown-dev", platform=self.unknown_platform)

        report = self._run(plan, [device])

        outcome = report.device_outcomes.get()
        self.assertEqual(outcome.outcome, DeviceOutcomeChoices.OUTCOME_DRIVER_ERROR)

    def test_incompatible_driver_records_an_incompatible_skip(self):
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_EVPN,
            name="out-incompatible",
            napalm_driver="",
            detect_only=True,
        )
        device = self._create_device("out-incompatible-dev", platform=self.ios_platform)

        report = self._run(plan, [device])

        outcome = report.device_outcomes.get()
        self.assertEqual(outcome.outcome, DeviceOutcomeChoices.OUTCOME_SKIPPED_INCOMPATIBLE)

    def test_every_attempted_device_gets_exactly_one_row(self):
        """A mixed run accounts for each device once, whatever became of it."""
        plan = self._arp_plan("out-mixed")
        collected = self._create_device("out-mixed-ok", platform=self.junos_platform)
        no_ip = self._create_device("out-mixed-no-ip", platform=self.junos_platform)
        no_driver = self._create_device("out-mixed-no-driver")

        report = self._run(
            plan,
            [collected, no_ip, no_driver],
            get_ips=self._ips_missing_for(no_ip),
        )

        outcomes = self._outcomes(report)
        self.assertEqual(
            {name: row.outcome for name, row in outcomes.items()},
            {
                "out-mixed-ok": DeviceOutcomeChoices.OUTCOME_OK,
                "out-mixed-no-ip": DeviceOutcomeChoices.OUTCOME_SKIPPED_NO_IP,
                "out-mixed-no-driver": DeviceOutcomeChoices.OUTCOME_SKIPPED_NO_DRIVER,
            },
        )


class ReportStatusOnDeviceFailureTest(DeviceOutcomeRunMixin, TestCase):
    """What a run's device outcomes make of the report's final status."""

    def test_a_run_that_collected_nothing_fails_with_the_distribution(self):
        plan = self._arp_plan("status-all-failed")
        unreachable = self._create_device("status-unreachable", platform=self.junos_platform)
        no_ip = self._create_device("status-no-ip", platform=self.junos_platform)

        def refuse(*args, **kwargs):
            raise ConnectionException("refused")

        report = self._run(
            plan,
            [unreachable, no_ip],
            open_session=refuse,
            get_ips=self._ips_missing_for(no_ip),
        )
        report.refresh_from_db()

        self.assertEqual(report.status, ReportStatusChoices.STATUS_FAILED)
        self.assertIsNotNone(report.completed_at)
        self.assertIn("0 of 2 devices collected", report.error_message)
        self.assertIn(f"1 {DeviceSkipReasons.LABELS[DeviceSkipReasons.UNREACHABLE]}", report.error_message)
        self.assertIn(f"1 {DeviceSkipReasons.LABELS[DeviceSkipReasons.NO_IP]}", report.error_message)

    def test_partial_failure_keeps_the_status_it_had(self):
        """One device collected is a reviewable report, not a failed run."""
        plan = self._arp_plan("status-partial")
        collected = self._create_device("status-partial-ok", platform=self.junos_platform)
        unreachable = self._create_device("status-partial-dead", platform=self.ios_platform)

        def open_session(_driver_class, hostname, *args, **kwargs):
            if hostname == f"10.0.0.{unreachable.pk}":
                raise ConnectionException("refused")
            return MagicMock()

        report = self._run(
            plan,
            [collected, unreachable],
            collect=_collect_two_entries,
            open_session=open_session,
            get_ips=self._ip_per_device,
        )
        report.refresh_from_db()

        self.assertEqual(report.status, ReportStatusChoices.STATUS_PENDING)
        self.assertEqual(report.error_message, "")
        self.assertEqual(
            report.device_outcome_counts,
            {
                "device_count": 2,
                "device_ok_count": 1,
                "device_failed_count": 1,
                "device_skipped_count": 0,
            },
        )

    def test_a_run_with_no_devices_in_scope_does_not_fail(self):
        """Nothing was attempted, so there is no failure to report."""
        plan = self._arp_plan("status-empty")

        report = self._run(plan, [])
        report.refresh_from_db()

        self.assertEqual(report.status, ReportStatusChoices.STATUS_PENDING)
        self.assertEqual(report.error_message, "")
        self.assertEqual(report.device_outcomes.count(), 0)


class ReportDeviceCountsTest(CollectorTestMixin, TestCase):
    """The counts the report page and the API read off the outcome rows."""

    def setUp(self):
        self.plan = self._create_plan(CollectionTypeChoices.TYPE_ARP, name="counts-plan")
        self.report = FactsReport.objects.create(collection_plan=self.plan)
        self.devices = {}
        for index, outcome in enumerate(DeviceOutcomeChoices.values()):
            device = self._create_device(f"counts-dev-{index}")
            self.devices[outcome] = device
            FactsReportDeviceOutcome.objects.create(
                report=self.report,
                device=device,
                outcome=outcome,
            )

    def test_counts_group_the_outcomes_as_the_page_reads_them(self):
        """A failure and a pre-dial skip are counted apart from each other."""
        self.assertEqual(
            self.report.device_outcome_counts,
            {
                "device_count": len(DeviceOutcomeChoices.values()),
                "device_ok_count": 1,
                "device_failed_count": len(FAILED_DEVICE_OUTCOMES),
                "device_skipped_count": len(SKIPPED_DEVICE_OUTCOMES),
            },
        )

    def test_counts_are_scoped_to_one_report(self):
        other = FactsReport.objects.create(collection_plan=self.plan)
        FactsReportDeviceOutcome.objects.create(
            report=other,
            device=self.devices[DeviceOutcomeChoices.OUTCOME_OK],
            outcome=DeviceOutcomeChoices.OUTCOME_OK,
        )

        self.assertEqual(other.device_outcome_counts["device_count"], 1)
        self.assertEqual(self.report.device_outcome_counts["device_count"], len(DeviceOutcomeChoices.values()))

    def test_devices_tab_lists_only_this_reports_outcomes(self):
        other = FactsReport.objects.create(collection_plan=self.plan)
        FactsReportDeviceOutcome.objects.create(
            report=other,
            device=self.devices[DeviceOutcomeChoices.OUTCOME_OK],
            outcome=DeviceOutcomeChoices.OUTCOME_UNREACHABLE,
        )
        view = FactsReportDevicesView()

        children = view.get_children(None, self.report)

        self.assertEqual(children.count(), len(DeviceOutcomeChoices.values()))
        self.assertEqual({row.report_id for row in children}, {self.report.pk})

    def test_devices_tab_badge_counts_the_attempted_devices(self):
        self.assertEqual(
            FactsReportDevicesView.tab.render(self.report)["badge"],
            len(DeviceOutcomeChoices.values()),
        )

    def test_filterset_narrows_by_report_and_by_outcome(self):
        other = FactsReport.objects.create(collection_plan=self.plan)
        FactsReportDeviceOutcome.objects.create(
            report=other,
            device=self.devices[DeviceOutcomeChoices.OUTCOME_OK],
            outcome=DeviceOutcomeChoices.OUTCOME_OK,
        )

        filtered = FactsReportDeviceOutcomeFilterSet(
            {
                "report": str(self.report.pk),
                "outcome": [DeviceOutcomeChoices.OUTCOME_UNREACHABLE],
            },
            queryset=FactsReportDeviceOutcome.objects.all(),
        ).qs

        self.assertEqual(
            [row.device_id for row in filtered],
            [self.devices[DeviceOutcomeChoices.OUTCOME_UNREACHABLE].pk],
        )

    def test_report_serializer_exposes_the_device_counts_read_only(self):
        annotated = FactsReport.objects.filter(pk=self.report.pk).annotate(**DEVICE_OUTCOME_COUNT_ANNOTATIONS).get()
        context = {"request": Request(APIRequestFactory().get("/"))}

        data = FactsReportSerializer(annotated, context=context).data

        self.assertEqual(data["device_count"], len(DeviceOutcomeChoices.values()))
        self.assertEqual(data["device_ok_count"], 1)
        self.assertEqual(data["device_failed_count"], len(FAILED_DEVICE_OUTCOMES))
        self.assertEqual(data["device_skipped_count"], len(SKIPPED_DEVICE_OUTCOMES))
        for field_name in DEVICE_OUTCOME_COUNT_ANNOTATIONS:
            self.assertTrue(FactsReportSerializer().fields[field_name].read_only, msg=field_name)


class DeviceOutcomeAPIWiringTest(TestCase):
    """The outcome endpoint is read-only and reuses the existing plumbing."""

    def test_viewset_is_bound_to_the_outcome_model_serializer_and_filterset(self):
        self.assertIs(FactsReportDeviceOutcomeViewSet.queryset.model, FactsReportDeviceOutcome)
        self.assertIs(FactsReportDeviceOutcomeViewSet.serializer_class, FactsReportDeviceOutcomeSerializer)
        self.assertIs(FactsReportDeviceOutcomeViewSet.filterset_class, FactsReportDeviceOutcomeFilterSet)

    def test_viewset_exposes_no_write_handlers(self):
        """Outcomes are written by a collection run and never edited."""
        for handler in ("create", "update", "partial_update", "destroy", "bulk_update", "bulk_destroy"):
            self.assertFalse(hasattr(FactsReportDeviceOutcomeViewSet, handler), msg=handler)

    def test_queryset_is_restrictable(self):
        """Object-level permission enforcement requires a restrictable queryset."""
        self.assertTrue(hasattr(FactsReportDeviceOutcomeViewSet.queryset, "restrict"))

    def test_serializer_is_read_only(self):
        for field in FactsReportDeviceOutcomeSerializer().fields.values():
            self.assertTrue(field.read_only, msg=field.field_name)

"""Regression tests for BGP collector/applier apply-semantics bugs."""

import unittest
from unittest.mock import MagicMock

from django.test import TestCase

from netbox_facts.choices import CollectionTypeChoices, EntryActionChoices, EntryStatusChoices
from netbox_facts.helpers.applier import apply_entries
from netbox_facts.helpers.collector import HAS_NETBOX_ROUTING
from netbox_facts.models import FactsReport, FactsReportEntry
from netbox_facts.tests.test_applier import ApplierTestMixin
from netbox_facts.tests.test_helpers import CollectorTestMixin


def _bgp_neighbors_payload(remote_address="10.0.0.1", local_as=65000, remote_as=65001):
    """Build a minimal get_bgp_neighbors_detail payload with one global peer."""
    return {
        "global": {
            str(remote_as): [
                {
                    "up": True,
                    "local_as": local_as,
                    "remote_as": remote_as,
                    "remote_address": remote_address,
                    "local_address": "10.0.0.2",
                }
            ]
        }
    }


@unittest.skipUnless(HAS_NETBOX_ROUTING, "netbox-routing not installed")
class BGPRoutingDetectOnlyRegressionTest(CollectorTestMixin, TestCase):
    """Detect-only BGP runs must not write to NetBox (issue #48)."""

    def test_detect_only_does_not_create_local_asn(self):
        """A detect-only bgp() run must not create the local ASN (issue #48)."""
        from ipam.models import ASN, RIR
        from netbox_routing.models import BGPRouter

        RIR.objects.create(name="Detect-RIR", slug="detect-rir")

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_BGP,
            name="Plan-bgp-detect-only",
            detect_only=True,
        )
        device = self._create_device("bgp-detect-dev")
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = FactsReport.objects.create(collection_plan=plan)

        driver = MagicMock()
        driver.get_bgp_neighbors_detail.return_value = _bgp_neighbors_payload()

        collector.bgp(driver)

        self.assertFalse(ASN.objects.exists())
        self.assertFalse(BGPRouter.objects.exists())
        self.assertTrue(
            FactsReportEntry.objects.filter(
                report=collector._report,
                object_repr__startswith="BGPRouter",
            ).exists()
        )


class BGPCollectorNoRIRRegressionTest(CollectorTestMixin, TestCase):
    """bgp() must survive a database with zero RIRs (issue #54)."""

    def test_bgp_completes_with_zero_rirs(self):
        """With no RIR, bgp() must warn and finish instead of raising (issue #54)."""
        from ipam.models import ASN, RIR
        from ipam.models.ip import IPAddress

        self.assertFalse(RIR.objects.exists())

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_BGP,
            name="Plan-bgp-no-rir",
            detect_only=False,
        )
        device = self._create_device("bgp-no-rir-dev")
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._log_warning = MagicMock()

        driver = MagicMock()
        driver.get_bgp_neighbors_detail.return_value = _bgp_neighbors_payload(remote_address="10.54.0.1")

        collector.bgp(driver)

        self.assertTrue(IPAddress.objects.filter(address="10.54.0.1/32").exists())
        self.assertFalse(ASN.objects.exists())
        warning_text = " ".join(str(call.args[0]) for call in collector._log_warning.call_args_list)
        self.assertIn("No RIR", warning_text)


class ApplierNoRIRRegressionTest(ApplierTestMixin, TestCase):
    """Applier BGP handlers must not raise IntegrityError with zero RIRs (issue #54)."""

    def _make_entry(self, report, object_repr, detected_values):
        return FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_BGP,
            device=self.device,
            object_repr=object_repr,
            detected_values=detected_values,
        )

    def setUp(self):
        from ipam.models import RIR

        self.assertFalse(RIR.objects.exists())
        self.report = FactsReport.objects.create(collection_plan=self.plan)

    def test_bgp_peer_entry_applies_without_asn_when_no_rir(self):
        """A plain BGP peer entry must apply with zero RIRs, skipping the ASN (issue #54)."""
        from ipam.models import ASN
        from ipam.models.ip import IPAddress

        entry = self._make_entry(
            self.report,
            "BGP peer 10.54.1.1 AS65001",
            {"remote_address": "10.54.1.1", "remote_as": 65001, "vrf": None},
        )

        applied, failed = apply_entries(self.report, [entry.pk])
        self.assertEqual(applied, 1)
        self.assertEqual(failed, 0)
        self.assertTrue(IPAddress.objects.filter(address="10.54.1.1/32").exists())
        self.assertFalse(ASN.objects.exists())

    @unittest.skipUnless(HAS_NETBOX_ROUTING, "netbox-routing not installed")
    def test_bgp_router_entry_fails_with_clear_message_when_no_rir(self):
        """A BGPRouter entry with zero RIRs must fail with a clear message (issue #54)."""
        entry = self._make_entry(self.report, f"BGPRouter {self.device}", {"local_as": 65000})

        applied, failed = apply_entries(self.report, [entry.pk])
        self.assertEqual(applied, 0)
        self.assertEqual(failed, 1)
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_FAILED)
        self.assertIn("No RIR", entry.error_message)

    @unittest.skipUnless(HAS_NETBOX_ROUTING, "netbox-routing not installed")
    def test_bgp_scope_entry_fails_with_clear_message_when_no_rir(self):
        """A BGPScope entry with zero RIRs must fail with a clear message (issue #54)."""
        entry = self._make_entry(
            self.report,
            f"BGPScope {self.device} global",
            {"local_as": 65000, "vrf": None},
        )

        applied, failed = apply_entries(self.report, [entry.pk])
        self.assertEqual(applied, 0)
        self.assertEqual(failed, 1)
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_FAILED)
        self.assertIn("No RIR", entry.error_message)

    @unittest.skipUnless(HAS_NETBOX_ROUTING, "netbox-routing not installed")
    def test_bgp_peer_routing_entry_fails_with_clear_message_when_no_rir(self):
        """A BGPPeer routing entry with zero RIRs must fail with a clear message (issue #54)."""
        entry = self._make_entry(
            self.report,
            "BGPPeer 10.54.2.1 AS65001",
            {"remote_address": "10.54.2.1", "remote_as": 65001, "local_as": 65000, "vrf": None},
        )

        applied, failed = apply_entries(self.report, [entry.pk])
        self.assertEqual(applied, 0)
        self.assertEqual(failed, 1)
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_FAILED)
        self.assertIn("No RIR", entry.error_message)


class ApplierVRFResolutionRegressionTest(ApplierTestMixin, TestCase):
    """A detected VRF that no longer resolves must fail the entry (issue #53)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from ipam.models import RIR

        RIR.objects.create(name="VRF-Test-RIR", slug="vrf-test-rir")

    def setUp(self):
        self.report = FactsReport.objects.create(collection_plan=self.plan)

    def _make_entry(self, collector_type, object_repr, detected_values):
        return FactsReportEntry.objects.create(
            report=self.report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=collector_type,
            device=self.device,
            object_repr=object_repr,
            detected_values=detected_values,
        )

    def _assert_failed_no_ip(self, entry, address):
        from ipam.models.ip import IPAddress

        applied, failed = apply_entries(self.report, [entry.pk])
        self.assertEqual(applied, 0)
        self.assertEqual(failed, 1)
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_FAILED)
        self.assertIn("VRF GONE_VRF not found", entry.error_message)
        self.assertFalse(IPAddress.objects.filter(address=address).exists())

    def test_arp_entry_with_unresolvable_vrf_fails(self):
        """An ARP IP entry naming a missing VRF must fail, not go global (issue #53)."""
        entry = self._make_entry(
            CollectionTypeChoices.TYPE_ARP,
            "IPAddress 10.53.0.1/32",
            {"ip": "10.53.0.1/32", "mac": "AA:BB:CC:DD:EE:53", "interface": "", "vrf": "GONE_VRF"},
        )
        self._assert_failed_no_ip(entry, "10.53.0.1/32")

    def test_interfaces_ip_entry_with_unresolvable_vrf_fails(self):
        """An interfaces IP entry naming a missing VRF must fail, not go global (issue #53)."""
        from ipam.models.ip import Prefix

        entry = self._make_entry(
            CollectionTypeChoices.TYPE_INTERFACES,
            "IPAddress 10.53.1.5/24 on ge-0/0/0.0",
            {
                "ip_address": "10.53.1.5/24",
                "logical_interface": "ge-0/0/0.0",
                "vrf": "GONE_VRF",
                "prefix": "10.53.1.0/24",
            },
        )
        self._assert_failed_no_ip(entry, "10.53.1.5/24")
        self.assertFalse(Prefix.objects.filter(prefix="10.53.1.0/24").exists())

    def test_bgp_entry_with_unresolvable_vrf_fails(self):
        """A BGP peer entry naming a missing VRF must fail, not go global (issue #53)."""
        entry = self._make_entry(
            CollectionTypeChoices.TYPE_BGP,
            "BGP peer 10.53.2.1 AS65001",
            {"remote_address": "10.53.2.1", "remote_as": 65001, "vrf": "GONE_VRF"},
        )
        self._assert_failed_no_ip(entry, "10.53.2.1/32")

    @unittest.skipUnless(HAS_NETBOX_ROUTING, "netbox-routing not installed")
    def test_bgp_scope_entry_with_unresolvable_vrf_fails_with_clear_message(self):
        """A BGPScope entry naming a missing VRF must fail with the canonical message (issue #53)."""
        entry = self._make_entry(
            CollectionTypeChoices.TYPE_BGP,
            f"BGPScope {self.device} GONE_VRF",
            {"local_as": 65000, "vrf": "GONE_VRF"},
        )

        applied, failed = apply_entries(self.report, [entry.pk])
        self.assertEqual(applied, 0)
        self.assertEqual(failed, 1)
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_FAILED)
        self.assertIn("VRF GONE_VRF not found", entry.error_message)

    def test_arp_entry_without_vrf_still_applies_globally(self):
        """An ARP IP entry with no VRF must keep applying into the global table."""
        from ipam.models.ip import IPAddress

        entry = self._make_entry(
            CollectionTypeChoices.TYPE_ARP,
            "IPAddress 10.53.3.1/32",
            {"ip": "10.53.3.1/32", "mac": "", "interface": "", "vrf": None},
        )

        applied, failed = apply_entries(self.report, [entry.pk])
        self.assertEqual(applied, 1)
        self.assertEqual(failed, 0)
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_APPLIED)
        nb_ip = IPAddress.objects.get(address="10.53.3.1/32")
        self.assertIsNone(nb_ip.vrf)

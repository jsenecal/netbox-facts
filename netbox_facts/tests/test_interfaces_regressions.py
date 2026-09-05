"""Regression tests for the interfaces collector and its helpers."""

import re

from dcim.models.device_components import Interface
from django.test import TestCase
from ipam.models.ip import IPAddress, Prefix
from napalm.base.exceptions import ConnectionException
from unittest.mock import MagicMock

from netbox_facts.constants import AUTO_D_TAG

from netbox_facts.choices import CollectionTypeChoices, EntryActionChoices
from netbox_facts.helpers.applier import apply_entries
from netbox_facts.models import FactsReport, FactsReportEntry
from netbox_facts.models.mac import MACAddress
from netbox_facts.tests.test_helpers import CollectorTestMixin


class InterfacesRegressionTestMixin(CollectorTestMixin):
    """CollectorTestMixin with a real catch-all interface regex."""

    def _make_collector(self, plan):
        collector = super()._make_collector(plan)
        collector._interfaces_re = re.compile(r".*")
        return collector


class JunosAbbreviatedDestinationTest(InterfacesRegressionTestMixin, TestCase):
    """Junos trims trailing zero octets from inet destinations."""

    def test_abbreviated_inet_destinations_keep_real_prefix_length(self):
        """Regression test for issue #56: '10/8' and '172.16/16' destinations
        must produce /8 and /16 addresses, never fall back to /32."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-dest-pad",
        )
        device = self._create_device("dest-pad-dev")
        Interface.objects.create(device=device, name="xe-0/0/0", type="other")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "xe-0/0/0": {
                "is_up": True,
                "is_enabled": True,
                "description": "",
                "last_flapped": -1.0,
                "speed": 10000.0,
                "mtu": 1500,
                "mac_address": "",
                "logical_interfaces": {
                    "xe-0/0/0.0": {
                        "vrf": "",
                        "families": {
                            "inet": {
                                "addresses": {
                                    "10/8": {"local": "10.0.0.1", "preferred": True},
                                    "172.16/16": {"local": "172.16.5.1", "preferred": True},
                                    "10.0.1/24": {"local": "10.0.1.5", "preferred": True},
                                },
                            },
                        },
                    },
                },
            },
        }

        collector.interfaces(driver)

        self.assertTrue(IPAddress.objects.filter(address="10.0.0.1/8").exists())
        self.assertTrue(IPAddress.objects.filter(address="172.16.5.1/16").exists())
        self.assertTrue(IPAddress.objects.filter(address="10.0.1.5/24").exists())
        self.assertFalse(IPAddress.objects.filter(address="10.0.0.1/32").exists())
        self.assertFalse(IPAddress.objects.filter(address="172.16.5.1/32").exists())
        self.assertTrue(Prefix.objects.filter(prefix="10.0.0.0/8").exists())
        self.assertTrue(Prefix.objects.filter(prefix="172.16.0.0/16").exists())
        self.assertTrue(Prefix.objects.filter(prefix="10.0.1.0/24").exists())


class InterfaceMacReassignmentTest(InterfacesRegressionTestMixin, TestCase):
    """A changed hardware MAC must not trip the device_interface OneToOne."""

    def test_collector_releases_old_mac_row(self):
        """Regression test for issue #55: when an interface's MAC changes,
        the collector must release the old MACAddress row instead of
        raising IntegrityError and aborting the run."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-mac-swap",
        )
        device = self._create_device("mac-swap-dev")
        iface = Interface.objects.create(device=device, name="Ethernet1", type="1000base-t")
        old_mac = MACAddress.objects.create(mac_address="AA:BB:CC:DD:EE:01", device_interface=iface)
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "Ethernet1": {
                "is_up": True,
                "is_enabled": True,
                "description": "",
                "last_flapped": -1.0,
                "speed": 1000.0,
                "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:02",
            },
        }
        driver.get_interfaces_ip.return_value = {}
        driver.get_network_instances.return_value = {}

        collector.interfaces(driver)

        old_mac.refresh_from_db()
        self.assertIsNone(old_mac.device_interface)
        new_mac = MACAddress.objects.get(mac_address="AA:BB:CC:DD:EE:02")
        self.assertEqual(new_mac.device_interface, iface)

    def test_applier_releases_old_mac_row(self):
        """Regression test for issue #55: applying a MAC entry for an
        interface already held by another MACAddress row must release the
        old row instead of failing the entry."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-mac-swap-apply",
            detect_only=True,
        )
        device = self._create_device("mac-swap-apply-dev")
        iface = Interface.objects.create(device=device, name="Ethernet1", type="1000base-t")
        old_mac = MACAddress.objects.create(mac_address="AA:BB:CC:DD:EE:11", device_interface=iface)
        report = FactsReport.objects.create(collection_plan=plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            device=device,
            object_repr="MACAddress AA:BB:CC:DD:EE:12",
            detected_values={"interface": "Ethernet1", "mac_address": "AA:BB:CC:DD:EE:12"},
            current_values={},
        )

        applied, failed = apply_entries(report, [entry.pk])

        self.assertEqual(applied, 1)
        self.assertEqual(failed, 0)
        old_mac.refresh_from_db()
        self.assertIsNone(old_mac.device_interface)
        new_mac = MACAddress.objects.get(mac_address="AA:BB:CC:DD:EE:12")
        self.assertEqual(new_mac.device_interface, iface)


class DetectOnlyInterfaceCreationTest(InterfacesRegressionTestMixin, TestCase):
    """detect_only=True must never create Interface objects."""

    def _detect_only_setup(self, name):
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name=f"DetectOnly-{name}",
            detect_only=True,
        )
        device = self._create_device(name)
        report = FactsReport.objects.create(collection_plan=plan)
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = report
        return device, report, collector

    def test_detect_only_generic_path_creates_no_interfaces(self):
        """Regression test for issue #47: a detect-only run against a device
        with no pre-created interfaces must leave Interface.objects untouched
        and still record a pending entry for the missing interface."""
        device, report, collector = self._detect_only_setup("detect-noiface-dev")

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "Ethernet1": {
                "is_up": True,
                "is_enabled": True,
                "description": "",
                "last_flapped": -1.0,
                "speed": 1000.0,
                "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:41",
            },
        }
        driver.get_interfaces_ip.return_value = {
            "Ethernet1": {"ipv4": {"10.41.0.1": {"prefix_length": 24}}},
        }
        driver.get_network_instances.return_value = {}

        collector.interfaces(driver)

        self.assertEqual(Interface.objects.filter(device=device).count(), 0)
        self.assertFalse(MACAddress.objects.filter(mac_address="AA:BB:CC:DD:EE:41").exists())
        self.assertFalse(IPAddress.objects.filter(address="10.41.0.1/24").exists())
        entries = report.entries.filter(object_repr="Interface Ethernet1")
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries[0].action, EntryActionChoices.ACTION_NEW)
        self.assertEqual(entries[0].status, "pending")

    def test_detect_only_logical_path_creates_no_interfaces(self):
        """Regression test for issue #47: the Junos logical path must not
        create the physical interface, and must record it exactly once."""
        device, report, collector = self._detect_only_setup("detect-nolog-dev")

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "ge-0/0/1": {
                "is_up": True,
                "is_enabled": True,
                "description": "",
                "last_flapped": -1.0,
                "speed": 1000.0,
                "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:42",
                "logical_interfaces": {
                    "ge-0/0/1.0": {
                        "vrf": "",
                        "families": {
                            "inet": {
                                "addresses": {
                                    "10.42.0.0/24": {"local": "10.42.0.1", "preferred": True},
                                },
                            },
                        },
                    },
                },
            },
        }

        collector.interfaces(driver)

        self.assertEqual(Interface.objects.filter(device=device).count(), 0)
        self.assertFalse(IPAddress.objects.filter(address="10.42.0.1/24").exists())
        entries = report.entries.filter(object_repr="Interface ge-0/0/1")
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries[0].action, EntryActionChoices.ACTION_NEW)

    def test_detect_only_missing_logical_unit_not_created(self):
        """Regression test for issue #47: a missing logical unit under an
        existing physical interface must be recorded, not created."""
        device, report, collector = self._detect_only_setup("detect-nounit-dev")
        Interface.objects.create(device=device, name="ge-0/0/2", type="1000base-t")

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "ge-0/0/2": {
                "is_up": True,
                "is_enabled": True,
                "description": "",
                "last_flapped": -1.0,
                "speed": 1000.0,
                "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:43",
                "logical_interfaces": {
                    "ge-0/0/2.0": {
                        "vrf": "",
                        "families": {
                            "inet": {
                                "addresses": {
                                    "10.43.0.0/24": {"local": "10.43.0.1", "preferred": True},
                                },
                            },
                        },
                    },
                },
            },
        }

        collector.interfaces(driver)

        self.assertEqual(Interface.objects.filter(device=device).count(), 1)
        self.assertFalse(IPAddress.objects.filter(address="10.43.0.1/24").exists())
        entries = report.entries.filter(object_repr="Interface ge-0/0/2.0")
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries[0].action, EntryActionChoices.ACTION_NEW)

    def test_applier_creates_interface_from_pending_entry(self):
        """Regression test for issue #47: the applier must create the
        interface recorded by a detect-only run, even without a MAC."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="DetectOnly-apply-iface",
            detect_only=True,
        )
        device = self._create_device("detect-apply-iface-dev")
        report = FactsReport.objects.create(collection_plan=plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            device=device,
            object_repr="Interface lo0",
            detected_values={"interface": "lo0", "mac_address": ""},
            current_values={},
        )

        applied, failed = apply_entries(report, [entry.pk])

        self.assertEqual(applied, 1)
        self.assertEqual(failed, 0)
        self.assertTrue(Interface.objects.filter(device=device, name="lo0").exists())


class StaleIpScopeTest(InterfacesRegressionTestMixin, TestCase):
    """The stale-IP sweep must only cover what this run could have seen."""

    def test_failed_ip_collection_skips_stale_sweep(self):
        """Regression test for issue #49: when get_interfaces_ip fails, the
        stale sweep must not run -- a transient RPC failure must not
        unassign every previously discovered IP on the device."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-stale-rpcfail",
        )
        device = self._create_device("stale-rpcfail-dev")
        iface = Interface.objects.create(device=device, name="Ethernet5", type="1000base-t")
        auto_ip = IPAddress.objects.create(address="10.51.0.1/24", assigned_object=iface)
        auto_ip.tags.add(AUTO_D_TAG)

        report = FactsReport.objects.create(collection_plan=plan)
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = report

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "Ethernet5": {
                "is_up": True,
                "is_enabled": True,
                "description": "",
                "last_flapped": -1.0,
                "speed": 1000.0,
                "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:51",
            },
        }
        driver.get_interfaces_ip.side_effect = ConnectionException("timeout")

        collector.interfaces(driver)

        auto_ip.refresh_from_db()
        self.assertEqual(auto_ip.assigned_object, iface)
        self.assertEqual(report.entries.filter(action=EntryActionChoices.ACTION_STALE).count(), 0)

    def test_regex_excluded_interface_not_swept(self):
        """Regression test for issue #49: an AUTO_D IP on an interface
        excluded by the configured regex must not be flagged stale."""
        import re as _re

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-stale-regex",
        )
        device = self._create_device("stale-regex-dev")
        mgmt = Interface.objects.create(device=device, name="Management1", type="1000base-t")
        auto_ip = IPAddress.objects.create(address="10.52.0.1/24", assigned_object=mgmt)
        auto_ip.tags.add(AUTO_D_TAG)

        report = FactsReport.objects.create(collection_plan=plan)
        collector = self._make_collector(plan)
        collector._interfaces_re = _re.compile(r"ge-.*")
        collector._current_device = device
        collector._report = report

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "ge-0/0/1": {
                "is_up": True,
                "is_enabled": True,
                "description": "",
                "last_flapped": -1.0,
                "speed": 1000.0,
                "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:52",
            },
        }
        driver.get_interfaces_ip.return_value = {}
        driver.get_network_instances.return_value = {}

        collector.interfaces(driver)

        auto_ip.refresh_from_db()
        self.assertEqual(auto_ip.assigned_object, mgmt)
        self.assertEqual(report.entries.filter(action=EntryActionChoices.ACTION_STALE).count(), 0)

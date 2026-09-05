"""Regression tests for the interfaces collector and its helpers."""

import re

from dcim.models.device_components import Interface
from django.test import TestCase
from ipam.models.ip import IPAddress, Prefix
from unittest.mock import MagicMock

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

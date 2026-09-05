"""Regression tests for ARP/NDP collector portability and VRF round-trip bugs."""

import re

from unittest.mock import MagicMock

from dcim.models.device_components import Interface
from django.test import TestCase
from ipam.models.ip import IPAddress, Prefix

from netbox_facts.choices import CollectionTypeChoices
from netbox_facts.models.mac import MACAddress
from netbox_facts.tests.test_helpers import CollectorTestMixin


class ArpNdpCollectorTestMixin(CollectorTestMixin):
    """Collector mixin with a permissive interface regex for ARP/NDP tests."""

    def _make_collector(self, plan):
        collector = super()._make_collector(plan)
        collector._interfaces_re = re.compile(r".*")
        return collector

    @staticmethod
    def _default_instance_for(interface_name):
        """Standard-driver get_network_instances payload for the global table."""
        return {
            "default": {
                "name": "default",
                "type": "DEFAULT_INSTANCE",
                "state": {"route_distinguisher": ""},
                "interfaces": {"interface": {interface_name: {}}},
            },
        }


class ArpStrIpPortabilityTest(ArpNdpCollectorTestMixin, TestCase):
    """The ARP/NDP pipeline must accept the str ip values standard drivers return."""

    def test_arp_processes_standard_driver_str_ip_table(self):
        """Str-ip ARP rows from standard NAPALM drivers are processed, not fatal.

        Regression test for issue #43: 'ip' values are plain strings on every
        standard NAPALM driver, and the str-in-network membership test raised
        AttributeError, aborting the whole run.
        """
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_ARP,
            name="Plan-arp-str-ip",
        )
        device = self._create_device("arp-strip-dev1")
        Interface.objects.create(device=device, name="Ethernet1", type="1000base-t")
        Prefix.objects.create(prefix="10.1.0.0/24")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_arp_table.return_value = [
            {
                "interface": "Ethernet1",
                "mac": "AA:BB:CC:DD:EE:30",
                "ip": "10.1.0.50",
                "age": 12.0,
            },
        ]
        driver.get_interfaces_ip.return_value = {
            "Ethernet1": {"ipv4": {"10.1.0.1": {"prefix_length": 24}}},
        }
        driver.get_network_instances.return_value = self._default_instance_for("Ethernet1")

        collector.arp(driver)

        self.assertTrue(MACAddress.objects.filter(mac_address="AA:BB:CC:DD:EE:30").exists())
        self.assertTrue(IPAddress.objects.filter(address="10.1.0.50/24").exists())


class ExecuteDispatchTest(ArpNdpCollectorTestMixin, TestCase):
    """execute() collector dispatch must not disguise collector-body errors."""

    def test_unknown_collector_type_raises_not_implemented(self):
        """An unknown collector type raises NotImplementedError (issue #43)."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_ARP,
            name="Plan-exec-unknown",
        )
        collector = self._make_collector(plan)
        collector._collector_type = "no_such_collector"
        collector._napalm_driver = MagicMock()
        collector._devices = []

        with self.assertRaises(NotImplementedError):
            collector.execute()

    def test_attribute_error_in_collector_body_propagates(self):
        """AttributeError inside a collector body stays an AttributeError.

        Regression test for issue #43: execute() converted every
        AttributeError raised while a collector ran into a misleading
        NotImplementedError.
        """
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_ARP,
            name="Plan-exec-attr-error",
        )
        device = self._create_device("exec-attr-dev1")
        iface = Interface.objects.create(device=device, name="mgmt0", type="1000base-t")
        ip = IPAddress.objects.create(address="192.0.2.10/24", assigned_object=iface)
        device.primary_ip4 = ip
        device.save()
        device.refresh_from_db()

        collector = self._make_collector(plan)
        collector._devices = [device]
        collector._napalm_driver = MagicMock()
        collector.arp = MagicMock(side_effect=AttributeError("real attribute bug"))

        with self.assertRaises(AttributeError):
            collector.execute()

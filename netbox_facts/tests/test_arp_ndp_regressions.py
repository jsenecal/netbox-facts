"""Regression tests for ARP/NDP collector portability and VRF round-trip bugs."""

import re

from unittest.mock import MagicMock, patch

from dcim.models.device_components import Interface
from django.test import TestCase
from ipam.models.ip import IPAddress, Prefix
from jnpr.junos.exception import ConnectError, RpcError
from napalm.base.exceptions import CommandErrorException, ConnectionException

from netbox_facts.choices import CollectionTypeChoices
from netbox_facts.models.mac import MACAddress
from netbox_facts.napalm.junos import EnhancedJunOSDriver
from netbox_facts.napalm.utils import junos_views
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


class GeneratorRpcErrorHandlingTest(ArpNdpCollectorTestMixin, TestCase):
    """RPC errors surfacing when a generator getter is iterated must not escape."""

    @staticmethod
    def _failing_table(exc):
        raise exc
        yield  # unreachable; makes this function a generator function

    def _driver_with_failing_table(self, getter_name):
        driver = MagicMock()
        table = self._failing_table(CommandErrorException("RPC failed mid-stream"))
        getattr(driver, getter_name).return_value = table
        driver.get_network_instances.return_value = {}
        driver.get_interfaces_ip.return_value = {}
        return driver

    def test_arp_generator_rpc_error_is_logged_and_skipped(self):
        """An RPC error at first iteration of the ARP table skips the device.

        Regression test for issue #44: generator getters defer the RPC past
        _napalm_rpc, so iteration-time errors escaped arp() and aborted the
        entire multi-device run.
        """
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_ARP,
            name="Plan-arp-gen-error",
        )
        collector = self._make_collector(plan)
        collector._current_device = self._create_device("arp-gen-dev1")
        driver = self._driver_with_failing_table("get_arp_table")

        with patch.object(collector, "_log_failure") as log_failure:
            collector.arp(driver)

        log_failure.assert_called()
        self.assertEqual(MACAddress.objects.count(), 0)

    def test_ndp_generator_rpc_error_is_logged_and_skipped(self):
        """An RPC error at first iteration of the NDP table skips the device (issue #44)."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_NDP,
            name="Plan-ndp-gen-error",
        )
        collector = self._make_collector(plan)
        collector._current_device = self._create_device("ndp-gen-dev1")
        driver = self._driver_with_failing_table("get_ipv6_neighbors_table")

        with patch.object(collector, "_log_failure") as log_failure:
            collector.ndp(driver)

        log_failure.assert_called()
        self.assertEqual(MACAddress.objects.count(), 0)


class EnhancedJunosRpcTranslationTest(TestCase):
    """EnhancedJunOSDriver getters translate PyEZ errors to NAPALM exceptions."""

    @staticmethod
    def _make_driver():
        return EnhancedJunOSDriver("192.0.2.1", "user", "secret")

    def test_get_arp_table_translates_rpc_error(self):
        """A PyEZ RpcError during the ARP RPC becomes CommandErrorException (issue #44)."""
        driver = self._make_driver()
        with patch.object(junos_views, "junos_arp_table") as table_cls:
            table_cls.return_value.get.side_effect = RpcError()
            with self.assertRaises(CommandErrorException):
                list(driver.get_arp_table())

    def test_get_ipv6_neighbors_table_translates_connect_error(self):
        """A PyEZ ConnectError during the NDP RPC becomes ConnectionException (issue #44)."""
        driver = self._make_driver()
        with patch.object(junos_views, "junos_ipv6_neighbors_table") as table_cls:
            table_cls.return_value.get.side_effect = ConnectError(MagicMock())
            with self.assertRaises(ConnectionException):
                list(driver.get_ipv6_neighbors_table())


class NetworkInstancesUnsupportedTest(ArpNdpCollectorTestMixin, TestCase):
    """Drivers without get_network_instances must not abort ARP collection."""

    def test_arp_proceeds_when_get_network_instances_unsupported(self):
        """arp() completes without VRF context when get_network_instances raises.

        Regression test for issue #45: NAPALM's iosxr drivers do not
        implement get_network_instances, and the unguarded call let the
        base class NotImplementedError abort the whole run even though the
        getters ARP actually needs are implemented.
        """
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_ARP,
            name="Plan-arp-no-netinst",
        )
        device = self._create_device("arp-noinst-dev1")
        Interface.objects.create(device=device, name="GigabitEthernet0/0/0/1", type="1000base-t")
        Prefix.objects.create(prefix="10.3.0.0/24")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_network_instances.side_effect = NotImplementedError
        driver.get_arp_table.return_value = [
            {
                "interface": "GigabitEthernet0/0/0/1",
                "mac": "AA:BB:CC:DD:EE:40",
                "ip": "10.3.0.50",
                "age": 5.0,
            },
        ]
        driver.get_interfaces_ip.return_value = {
            "GigabitEthernet0/0/0/1": {"ipv4": {"10.3.0.1": {"prefix_length": 24}}},
        }

        collector.arp(driver)

        self.assertTrue(MACAddress.objects.filter(mac_address="AA:BB:CC:DD:EE:40").exists())
        self.assertTrue(IPAddress.objects.filter(address="10.3.0.50/24").exists())


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

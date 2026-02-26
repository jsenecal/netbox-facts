from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from dcim.choices import DeviceStatusChoices
from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Manufacturer,
    Site,
)
from dcim.models.device_components import Interface
from extras.models.models import JournalEntry

from netbox_facts.choices import CollectionTypeChoices
from netbox_facts.helpers.collector import NapalmCollector
from netbox_facts.helpers.napalm import (
    get_network_instances_by_interface,
    parse_network_instances,
)
from netbox_facts.helpers.netbox import get_absolute_url_markdown, get_primary_ip
from netbox_facts.models import CollectionPlan
from netbox_facts.models.mac import MACAddress


class ParseNetworkInstancesTest(TestCase):
    """Tests for parse_network_instances."""

    def test_parse_l3vrf(self):
        raw = {
            "default": {
                "name": "default",
                "type": "DEFAULT_INSTANCE",
                "state": {"route_distinguisher": ""},
                "interfaces": {"interface": {"ge-0/0/0.0": {}, "lo0.0": {}}},
            },
            "VRF_A": {
                "name": "VRF_A",
                "type": "L3VRF",
                "state": {"route_distinguisher": "65000:100"},
                "interfaces": {"interface": {"ge-0/0/1.100": {}}},
            },
        }
        result = parse_network_instances(raw)
        self.assertIn("default", result)
        self.assertIn("VRF_A", result)
        self.assertEqual(result["default"]["instance_type"], "DEFAULT_INSTANCE")
        self.assertIsNone(result["default"]["route_distinguisher"])
        self.assertEqual(result["VRF_A"]["instance_type"], "L3VRF")
        self.assertEqual(result["VRF_A"]["route_distinguisher"], "65000:100")
        self.assertIn("ge-0/0/1.100", result["VRF_A"]["interfaces"])

    def test_parse_empty(self):
        result = parse_network_instances({})
        self.assertEqual(result, {})


class GetNetworkInstancesByInterfaceTest(TestCase):
    """Tests for get_network_instances_by_interface."""

    def test_yields_per_interface(self):
        instances = [
            (
                "default",
                {
                    "instance_type": "DEFAULT_INSTANCE",
                    "route_distinguisher": None,
                    "interfaces": ["ge-0/0/0.0", "lo0.0"],
                },
            ),
        ]
        result = dict(get_network_instances_by_interface(instances))
        self.assertIn("ge-0/0/0.0", result)
        self.assertIn("lo0.0", result)
        self.assertEqual(result["ge-0/0/0.0"]["name"], "default")
        self.assertNotIn("interfaces", result["ge-0/0/0.0"])


class GetAbsoluteUrlMarkdownTest(TestCase):
    """Tests for get_absolute_url_markdown."""

    def test_basic_link(self):
        obj = MagicMock()
        obj.__str__ = MagicMock(return_value="TestObj")
        obj.get_absolute_url.return_value = "/test/1/"
        result = get_absolute_url_markdown(obj)
        self.assertEqual(result, "[TestObj](/test/1/)")

    def test_bold_link(self):
        obj = MagicMock()
        obj.__str__ = MagicMock(return_value="TestObj")
        obj.get_absolute_url.return_value = "/test/1/"
        result = get_absolute_url_markdown(obj, bold=True)
        self.assertEqual(result, "**[TestObj](/test/1/)**")

    def test_code_link(self):
        obj = MagicMock()
        obj.__str__ = MagicMock(return_value="TestObj")
        obj.get_absolute_url.return_value = "/test/1/"
        result = get_absolute_url_markdown(obj, code=True)
        self.assertEqual(result, "[`TestObj`](/test/1/)")

    def test_raises_on_no_url(self):
        obj = MagicMock(spec=[])
        with self.assertRaises(ValueError):
            get_absolute_url_markdown(obj)


class GetPrimaryIpTest(TestCase):
    """Tests for get_primary_ip."""

    def test_returns_ip_string(self):
        device = MagicMock()
        ip_mock = MagicMock()
        ip_mock.address.ip = "10.0.0.1"
        device.primary_ip = ip_mock
        result = get_primary_ip(device)
        self.assertEqual(result, "10.0.0.1")

    def test_raises_when_no_primary_ip(self):
        device = MagicMock()
        device.primary_ip = None
        device.__str__ = MagicMock(return_value="device1")
        with self.assertRaises(ValueError):
            get_primary_ip(device)


class CollectorTestMixin:
    """Mixin providing shared setup for collector tests."""

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="Collector Site", slug="collector-site")
        cls.manufacturer = Manufacturer.objects.create(
            name="CollectorMfg", slug="collectormfg"
        )
        cls.device_type = DeviceType.objects.create(
            manufacturer=cls.manufacturer, model="CollModel", slug="collmodel"
        )
        cls.role = DeviceRole.objects.create(name="CollRole", slug="collrole")

    def _create_plan(self, collector_type=CollectionTypeChoices.TYPE_INVENTORY, **kwargs):
        defaults = {
            "name": f"Plan-{collector_type}-{id(self)}",
            "collector_type": collector_type,
            "napalm_driver": "junos",
            "device_status": [DeviceStatusChoices.STATUS_ACTIVE],
        }
        defaults.update(kwargs)
        return CollectionPlan.objects.create(**defaults)

    def _create_device(self, name, site=None, **kwargs):
        return Device.objects.create(
            name=name,
            site=site or self.site,
            device_type=self.device_type,
            role=self.role,
            status=DeviceStatusChoices.STATUS_ACTIVE,
            **kwargs,
        )

    def _make_collector(self, plan):
        with patch.object(NapalmCollector, "__init__", lambda self, p: None):
            collector = NapalmCollector.__new__(NapalmCollector)
        collector.plan = plan
        collector._collector_type = plan.collector_type
        collector._napalm_args = {}
        collector._napalm_driver = None
        collector._napalm_username = "test"
        collector._napalm_password = "test"
        collector._interfaces_re = MagicMock()
        collector._interfaces_re.match.return_value = True
        collector._devices = []
        collector._current_device = None
        collector._log_prefix = ""
        collector._now = timezone.now()
        return collector


class InventoryCollectorTest(CollectorTestMixin, TestCase):
    """Tests for the inventory() collector method."""

    def test_inventory_updates_serial(self):
        """Device serial should be updated from get_facts data."""
        plan = self._create_plan()
        device = self._create_device("inv-dev1", serial="OLD_SERIAL")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_facts.return_value = {
            "uptime": 12345.0,
            "vendor": "Juniper",
            "os_version": "21.2R3",
            "serial_number": "NEW_SERIAL",
            "model": "QFX5100",
            "hostname": "switch1",
            "fqdn": "switch1.example.com",
            "interface_list": ["ge-0/0/0", "ge-0/0/1"],
        }

        collector.inventory(driver)

        device.refresh_from_db()
        self.assertEqual(device.serial, "NEW_SERIAL")

    def test_inventory_creates_journal_entry_on_change(self):
        """A JournalEntry should be created when serial changes."""
        plan = self._create_plan(name="Plan-inv-journal")
        device = self._create_device("inv-dev2", serial="OLD")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_facts.return_value = {
            "serial_number": "NEW",
            "os_version": "21.2R3",
            "hostname": "switch2",
            "fqdn": "",
        }

        collector.inventory(driver)

        entries = JournalEntry.objects.filter(
            assigned_object_id=device.pk,
        )
        self.assertTrue(entries.exists())
        self.assertIn("Serial", entries.first().comments)

    def test_inventory_no_change_no_journal(self):
        """No JournalEntry should be created when serial stays the same."""
        plan = self._create_plan(name="Plan-inv-nochange")
        device = self._create_device("inv-dev3", serial="SAME")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_facts.return_value = {
            "serial_number": "SAME",
            "os_version": "21.2R3",
            "hostname": "switch3",
            "fqdn": "",
        }

        collector.inventory(driver)

        entries = JournalEntry.objects.filter(
            assigned_object_id=device.pk,
        )
        self.assertFalse(entries.exists())


class InterfacesCollectorTest(CollectorTestMixin, TestCase):
    """Tests for the interfaces() collector method."""

    def _make_collector(self, plan):
        import re as _re
        collector = super()._make_collector(plan)
        collector._interfaces_re = _re.compile(r".*")
        return collector

    def test_interfaces_creates_mac_for_interface(self):
        """MACAddress should be created and linked to the interface."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-ifaces-create",
        )
        device = self._create_device("iface-dev1")
        iface = Interface.objects.create(device=device, name="Ethernet1", type="1000base-t")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "Ethernet1": {
                "is_up": True,
                "is_enabled": True,
                "description": "uplink",
                "last_flapped": -1.0,
                "speed": 1000.0,
                "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:01",
            }
        }

        collector.interfaces(driver)

        mac = MACAddress.objects.get(mac_address="AA:BB:CC:DD:EE:01")
        self.assertEqual(mac.device_interface, iface)
        self.assertEqual(mac.discovery_method, CollectionTypeChoices.TYPE_INTERFACES)
        self.assertIsNotNone(mac.last_seen)

    def test_interfaces_skips_non_matching_interface(self):
        """Interfaces not matching the regex should be skipped."""
        import re as _re
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-ifaces-regex",
        )
        device = self._create_device("iface-dev2")
        Interface.objects.create(device=device, name="Management1", type="1000base-t")
        collector = self._make_collector(plan)
        collector._interfaces_re = _re.compile(r"ge-.*")
        collector._current_device = device

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "Management1": {
                "is_up": True,
                "is_enabled": True,
                "description": "",
                "last_flapped": -1.0,
                "speed": 1000.0,
                "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:02",
            }
        }

        collector.interfaces(driver)

        self.assertFalse(MACAddress.objects.filter(mac_address="AA:BB:CC:DD:EE:02").exists())

    def test_interfaces_skips_empty_mac(self):
        """Interfaces with empty mac_address should be skipped."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-ifaces-empty",
        )
        device = self._create_device("iface-dev3")
        Interface.objects.create(device=device, name="Loopback0", type="virtual")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "Loopback0": {
                "is_up": True,
                "is_enabled": True,
                "description": "",
                "last_flapped": -1.0,
                "speed": 0.0,
                "mtu": 65535,
                "mac_address": "",
            }
        }

        collector.interfaces(driver)

        self.assertEqual(MACAddress.objects.count(), 0)


class EthernetSwitchingCollectorTest(CollectorTestMixin, TestCase):
    """Tests for the ethernet_switching() collector method."""

    def test_creates_mac_from_table(self):
        """MACAddress should be created and linked via M2M interfaces."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_L2,
            name="Plan-ethsw-create",
        )
        device = self._create_device("ethsw-dev1")
        iface = Interface.objects.create(device=device, name="Ethernet1", type="1000base-t")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_mac_address_table.return_value = [
            {
                "mac": "AA:BB:CC:DD:EE:10",
                "interface": "Ethernet1",
                "vlan": 100,
                "static": False,
                "active": True,
                "moves": 0,
                "last_move": 0.0,
            }
        ]

        collector.ethernet_switching(driver)

        from netbox_facts.models.mac import MACAddressInterfaceRelation
        mac = MACAddress.objects.get(mac_address="AA:BB:CC:DD:EE:10")
        self.assertTrue(
            MACAddressInterfaceRelation.objects.filter(
                mac_address=mac, interface=iface
            ).exists()
        )
        self.assertEqual(mac.discovery_method, CollectionTypeChoices.TYPE_L2)

    def test_skips_empty_mac(self):
        """Entries with empty MAC should be skipped."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_L2,
            name="Plan-ethsw-empty",
        )
        device = self._create_device("ethsw-dev2")
        Interface.objects.create(device=device, name="Ethernet1", type="1000base-t")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_mac_address_table.return_value = [
            {
                "mac": "",
                "interface": "Ethernet1",
                "vlan": 100,
                "static": False,
                "active": True,
                "moves": 0,
                "last_move": 0.0,
            }
        ]

        collector.ethernet_switching(driver)

        self.assertEqual(MACAddress.objects.count(), 0)

    def test_skips_interface_not_in_netbox(self):
        """Entries referencing non-existent interfaces should be skipped without error."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_L2,
            name="Plan-ethsw-noif",
        )
        device = self._create_device("ethsw-dev3")
        # Do NOT create an interface named "Ethernet99"
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_mac_address_table.return_value = [
            {
                "mac": "AA:BB:CC:DD:EE:11",
                "interface": "Ethernet99",
                "vlan": 100,
                "static": False,
                "active": True,
                "moves": 0,
                "last_move": 0.0,
            }
        ]

        # Should not raise
        collector.ethernet_switching(driver)

        self.assertEqual(MACAddress.objects.count(), 0)

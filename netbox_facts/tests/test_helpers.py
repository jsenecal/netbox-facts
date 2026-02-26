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


class LLDPCollectorTest(CollectorTestMixin, TestCase):
    """Tests for the lldp() collector method."""

    def test_creates_cable_same_site(self):
        """A cable should be created between two devices in the same site."""
        from dcim.models.cables import Cable as CableModel

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_LLDP,
            name="Plan-lldp-cable",
        )
        device_a = self._create_device("lldp-dev-a")
        device_b = self._create_device("lldp-dev-b")
        iface_a = Interface.objects.create(device=device_a, name="Ethernet1", type="1000base-t")
        iface_b = Interface.objects.create(device=device_b, name="Ethernet1", type="1000base-t")

        collector = self._make_collector(plan)
        collector._current_device = device_a

        driver = MagicMock()
        driver.get_lldp_neighbors_detail.return_value = {
            "Ethernet1": [
                {
                    "parent_interface": "Ethernet1",
                    "remote_chassis_id": "AA:BB:CC:DD:EE:FF",
                    "remote_system_name": "lldp-dev-b",
                    "remote_port": "Ethernet1",
                    "remote_port_description": "",
                }
            ]
        }

        collector.lldp(driver)

        # Verify a cable was created
        self.assertEqual(CableModel.objects.count(), 1)
        cable = CableModel.objects.first()
        a_terms, b_terms = cable.get_terminations()
        a_ifaces = list(a_terms.keys())
        b_ifaces = list(b_terms.keys())
        self.assertIn(iface_a, a_ifaces)
        self.assertIn(iface_b, b_ifaces)

    def test_no_cable_cross_site(self):
        """No cable should be created when devices are in different sites."""
        from dcim.models.cables import Cable as CableModel

        site_b = Site.objects.create(name="Other LLDP Site", slug="other-lldp-site")
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_LLDP,
            name="Plan-lldp-xsite",
        )
        device_a = self._create_device("lldp-xsite-a")
        device_b = self._create_device("lldp-xsite-b", site=site_b)
        Interface.objects.create(device=device_a, name="Ethernet1", type="1000base-t")
        Interface.objects.create(device=device_b, name="Ethernet1", type="1000base-t")

        collector = self._make_collector(plan)
        collector._current_device = device_a

        driver = MagicMock()
        driver.get_lldp_neighbors_detail.return_value = {
            "Ethernet1": [
                {
                    "parent_interface": "Ethernet1",
                    "remote_chassis_id": "AA:BB:CC:DD:EE:FF",
                    "remote_system_name": "lldp-xsite-b",
                    "remote_port": "Ethernet1",
                    "remote_port_description": "",
                }
            ]
        }

        collector.lldp(driver)

        self.assertEqual(CableModel.objects.count(), 0)

    def test_no_cable_unknown_remote_device(self):
        """No cable should be created when remote device is not in NetBox."""
        from dcim.models.cables import Cable as CableModel

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_LLDP,
            name="Plan-lldp-unknown",
        )
        device_a = self._create_device("lldp-unknown-a")
        Interface.objects.create(device=device_a, name="Ethernet1", type="1000base-t")

        collector = self._make_collector(plan)
        collector._current_device = device_a

        driver = MagicMock()
        driver.get_lldp_neighbors_detail.return_value = {
            "Ethernet1": [
                {
                    "parent_interface": "Ethernet1",
                    "remote_chassis_id": "AA:BB:CC:DD:EE:FF",
                    "remote_system_name": "nonexistent-device",
                    "remote_port": "Ethernet1",
                    "remote_port_description": "",
                }
            ]
        }

        collector.lldp(driver)

        self.assertEqual(CableModel.objects.count(), 0)

    def test_no_cable_already_cabled(self):
        """No duplicate cable should be created when interface already has one."""
        from dcim.choices import LinkStatusChoices
        from dcim.models.cables import Cable as CableModel

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_LLDP,
            name="Plan-lldp-dupcable",
        )
        device_a = self._create_device("lldp-dup-a")
        device_b = self._create_device("lldp-dup-b")
        device_c = self._create_device("lldp-dup-c")
        iface_a = Interface.objects.create(device=device_a, name="Ethernet1", type="1000base-t")
        iface_b = Interface.objects.create(device=device_b, name="Ethernet1", type="1000base-t")
        iface_c = Interface.objects.create(device=device_c, name="Ethernet1", type="1000base-t")

        # Pre-create a cable between device_a and device_c
        existing_cable = CableModel(
            a_terminations=[iface_a],
            b_terminations=[iface_c],
            status=LinkStatusChoices.STATUS_CONNECTED,
        )
        existing_cable.full_clean()
        existing_cable.save()

        collector = self._make_collector(plan)
        collector._current_device = device_a

        driver = MagicMock()
        driver.get_lldp_neighbors_detail.return_value = {
            "Ethernet1": [
                {
                    "parent_interface": "Ethernet1",
                    "remote_chassis_id": "AA:BB:CC:DD:EE:FF",
                    "remote_system_name": "lldp-dup-b",
                    "remote_port": "Ethernet1",
                    "remote_port_description": "",
                }
            ]
        }

        # Need to refresh iface_a from DB to pick up cable_id
        iface_a.refresh_from_db()
        collector.lldp(driver)

        # Should still be just the one original cable
        self.assertEqual(CableModel.objects.count(), 1)


class BGPCollectorTest(CollectorTestMixin, TestCase):
    """Tests for the bgp() collector method."""

    def test_creates_peer_ip(self):
        """IPAddress should be created for peer remote_address as /32."""
        from ipam.models import RIR

        rir = RIR.objects.create(name="BGP-RIR", slug="bgp-rir")

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_BGP,
            name="Plan-bgp-ip",
        )
        device = self._create_device("bgp-dev1")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_bgp_neighbors_detail.return_value = {
            "global": {
                "65001": [
                    {
                        "up": True,
                        "local_as": 65000,
                        "remote_as": 65001,
                        "remote_address": "10.0.0.1",
                        "local_address": "10.0.0.2",
                    }
                ]
            }
        }

        collector.bgp(driver)

        from ipam.models.ip import IPAddress as IP
        self.assertTrue(IP.objects.filter(address="10.0.0.1/32").exists())

    def test_creates_asn_when_rir_exists(self):
        """ASN object should be created when an RIR exists."""
        from ipam.models import ASN, RIR

        rir = RIR.objects.create(name="BGP-RIR-ASN", slug="bgp-rir-asn")

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_BGP,
            name="Plan-bgp-asn",
        )
        device = self._create_device("bgp-dev2")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_bgp_neighbors_detail.return_value = {
            "global": {
                "65001": [
                    {
                        "up": True,
                        "local_as": 65000,
                        "remote_as": 65001,
                        "remote_address": "10.0.1.1",
                        "local_address": "10.0.1.2",
                    }
                ]
            }
        }

        collector.bgp(driver)

        self.assertTrue(ASN.objects.filter(asn=65001).exists())

    def test_vrf_awareness(self):
        """Peer IP should be associated with the correct VRF."""
        from ipam.models import RIR
        from ipam.models.vrfs import VRF

        rir = RIR.objects.create(name="BGP-RIR-VRF", slug="bgp-rir-vrf")
        vrf = VRF.objects.create(name="VRF_BGP", rd="65000:200")

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_BGP,
            name="Plan-bgp-vrf",
        )
        device = self._create_device("bgp-dev3")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_bgp_neighbors_detail.return_value = {
            "VRF_BGP": {
                "65002": [
                    {
                        "up": True,
                        "local_as": 65000,
                        "remote_as": 65002,
                        "remote_address": "172.16.0.1",
                        "local_address": "172.16.0.2",
                    }
                ]
            }
        }

        collector.bgp(driver)

        from ipam.models.ip import IPAddress as IP
        peer_ip = IP.objects.get(address="172.16.0.1/32")
        self.assertEqual(peer_ip.vrf, vrf)


class VendorDispatchTest(TestCase):
    """Tests for the vendor-specific dispatch mechanism."""

    def _make_collector(self, driver_name="junos"):
        plan = MagicMock()
        plan.napalm_driver = driver_name
        plan.collector_type = "l2_circuits"

        with patch.object(NapalmCollector, "__init__", lambda self, p: None):
            collector = NapalmCollector.__new__(NapalmCollector)
        collector.plan = plan
        collector._collector_type = "l2_circuits"
        collector._now = timezone.now()
        collector._current_device = None
        collector._log_prefix = ""
        return collector

    def test_dispatch_junos(self):
        """_get_vendor_method() should return Junos implementation for junos driver."""
        collector = self._make_collector("junos")
        method = collector._get_vendor_method("l2_circuits")
        self.assertTrue(callable(method))

    def test_dispatch_enhanced_junos(self):
        """_get_vendor_method() should work for netbox_facts.napalm.junos driver."""
        collector = self._make_collector("netbox_facts.napalm.junos")
        method = collector._get_vendor_method("l2_circuits")
        self.assertTrue(callable(method))

    def test_dispatch_unsupported_driver(self):
        """_get_vendor_method() should raise NotImplementedError for unsupported drivers."""
        collector = self._make_collector("eos")
        with self.assertRaises(NotImplementedError):
            collector._get_vendor_method("l2_circuits")


class L2CircuitsCollectorTest(CollectorTestMixin, TestCase):
    """Tests for the l2_circuits() Junos collector."""

    def test_l2_circuits_creates_journal_entry(self):
        """l2_circuits() should create journal entries for discovered circuits."""
        device = self._create_device("l2c-dev1")
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_L2CIRCTUITS,
            name="L2C Plan",
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.cli.return_value = {
            "show l2circuit connections": (
                "Legend for connection status (active = Up)\n"
                "Neighbor: 10.0.0.1\n"
                "  Interface: ge-0/0/0.100\n"
                "  Type: rmt  Status: Up  Circuit-ID: 100\n"
            )
        }

        collector.l2_circuits(mock_driver)

        entries = JournalEntry.objects.filter(assigned_object_id=device.pk)
        self.assertTrue(entries.exists())


class EVPNCollectorTest(CollectorTestMixin, TestCase):
    """Tests for the evpn() Junos collector."""

    def test_evpn_creates_mac_with_evpn_discovery(self):
        """evpn() should create MACAddress objects with discovery_method='evpn'."""
        device = self._create_device("evpn-dev1")
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_EVPN,
            name="EVPN Plan",
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.cli.return_value = {
            "show evpn mac-table": (
                "MAC address      Logical interface   NH Index  Flags\n"
                "AA:BB:CC:DD:11:22  vtep.32769         0         D\n"
                "AA:BB:CC:DD:33:44  vtep.32769         0         D\n"
            )
        }

        collector.evpn(mock_driver)

        self.assertTrue(MACAddress.objects.filter(mac_address="AA:BB:CC:DD:11:22").exists())
        mac = MACAddress.objects.get(mac_address="AA:BB:CC:DD:11:22")
        self.assertEqual(mac.discovery_method, CollectionTypeChoices.TYPE_EVPN)

    def test_evpn_unsupported_driver(self):
        """evpn() should raise NotImplementedError for non-Junos drivers."""
        plan = MagicMock()
        plan.napalm_driver = "eos"
        plan.collector_type = CollectionTypeChoices.TYPE_EVPN

        with patch.object(NapalmCollector, "__init__", lambda self, p: None):
            collector = NapalmCollector.__new__(NapalmCollector)
        collector.plan = plan
        collector._collector_type = CollectionTypeChoices.TYPE_EVPN
        collector._now = timezone.now()
        collector._current_device = None
        collector._log_prefix = ""

        with self.assertRaises(NotImplementedError):
            collector.evpn(MagicMock())

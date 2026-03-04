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
from ipam.models.ip import IPAddress, Prefix
from ipam.models.vrfs import VRF

from netbox_facts.choices import CollectionTypeChoices, EntryActionChoices
from netbox_facts.constants import AUTO_D_TAG
from netbox_facts.helpers.collector import NapalmCollector
from netbox_facts.napalm.junos import EnhancedJunOSDriver
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
        collector._report = None
        collector._detect_only = getattr(plan, "detect_only", False)
        collector._seen_ips = set()
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


class InterfacesAutoCreateTest(CollectorTestMixin, TestCase):
    """Tests for auto-creation of missing interfaces during collection."""

    def _make_collector(self, plan):
        import re as _re
        collector = super()._make_collector(plan)
        collector._interfaces_re = _re.compile(r".*")
        return collector

    def test_interfaces_creates_missing_physical(self):
        """Missing physical interface should be auto-created with correct type and tag."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-autocreate-phys",
        )
        device = self._create_device("autocreate-dev1")
        # No interfaces pre-created in NetBox
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "ge-0/0/0": {
                "is_up": True,
                "is_enabled": True,
                "description": "uplink",
                "last_flapped": -1.0,
                "speed": 1000.0,
                "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:A1",
            }
        }

        collector.interfaces(driver)

        # Interface should have been auto-created with type 'other'
        nb_iface = Interface.objects.get(device=device, name="ge-0/0/0")
        self.assertEqual(nb_iface.type, "other")
        self.assertTrue(nb_iface.tags.filter(name=AUTO_D_TAG).exists())

        # MAC should have been created and linked
        mac = MACAddress.objects.get(mac_address="AA:BB:CC:DD:EE:A1")
        self.assertEqual(mac.device_interface, nb_iface)

    def test_interfaces_creates_missing_logical_unit(self):
        """Missing logical unit (.0) should be auto-created as virtual and process IPs."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-autocreate-unit",
        )
        device = self._create_device("autocreate-dev2")
        # Physical exists, but logical unit does not
        Interface.objects.create(device=device, name="ge-0/0/0", type="1000base-t")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "ge-0/0/0": {
                "is_up": True,
                "is_enabled": True,
                "description": "",
                "last_flapped": -1.0,
                "speed": 1000.0,
                "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:A2",
                "logical_interfaces": {
                    "ge-0/0/0.0": {
                        "families": {
                            "inet": {
                                "mtu": 1500,
                                "ae_bundle": "",
                                "addresses": {
                                    "10.99.0.0/24": {
                                        "local": "10.99.0.1",
                                        "broadcast": "",
                                        "preferred": True,
                                        "primary": True,
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }

        collector.interfaces(driver)

        # Logical unit should have been auto-created as virtual
        nb_li = Interface.objects.get(device=device, name="ge-0/0/0.0")
        self.assertEqual(nb_li.type, "virtual")
        self.assertTrue(nb_li.tags.filter(name=AUTO_D_TAG).exists())

        # IP should have been created and assigned
        ip = IPAddress.objects.get(address="10.99.0.1/24")
        self.assertEqual(ip.assigned_object, nb_li)

    def test_interfaces_creates_missing_lag_parent(self):
        """Missing ae0 should be auto-created as LAG when processing LAG membership."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-autocreate-lag",
        )
        device = self._create_device("autocreate-dev3")
        Interface.objects.create(device=device, name="ge-0/0/0", type="1000base-t")
        # ae0 does NOT exist
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "ge-0/0/0": {
                "is_up": True,
                "is_enabled": True,
                "description": "",
                "last_flapped": -1.0,
                "speed": 1000.0,
                "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:A3",
                "logical_interfaces": {
                    "ge-0/0/0.0": {
                        "families": {
                            "aenet": {"ae_bundle": "ae0.0", "mtu": None},
                        },
                    },
                },
            },
        }

        collector.interfaces(driver)

        # ae0 should have been auto-created as LAG type
        ae_iface = Interface.objects.get(device=device, name="ae0")
        self.assertEqual(ae_iface.type, "lag")
        self.assertTrue(ae_iface.tags.filter(name=AUTO_D_TAG).exists())

        # ge-0/0/0 should now have lag set to ae0
        ge_iface = Interface.objects.get(device=device, name="ge-0/0/0")
        self.assertEqual(ge_iface.lag, ae_iface)


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
        collector._report = None
        collector._detect_only = False
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
        collector._report = None
        collector._detect_only = False

        with self.assertRaises(NotImplementedError):
            collector.evpn(MagicMock())


class NetboxRoutingIntegrationTest(TestCase):
    """Tests for conditional netbox-routing integration."""

    def test_has_netbox_routing_detection(self):
        """HAS_NETBOX_ROUTING should be a boolean."""
        from netbox_facts.helpers.collector import HAS_NETBOX_ROUTING
        self.assertIsInstance(HAS_NETBOX_ROUTING, bool)

    def test_bgp_integration_without_routing(self):
        """BGP collector should work without netbox-routing installed."""
        from netbox_facts.helpers.collector import HAS_NETBOX_ROUTING
        self.assertIsInstance(HAS_NETBOX_ROUTING, bool)


class OSPFCollectorTest(CollectorTestMixin, TestCase):
    """Tests for the ospf() Junos collector."""

    def test_ospf_creates_journal_entry(self):
        """ospf() should create journal entries for discovered neighbors."""
        device = self._create_device("ospf-dev1")
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_OSPF,
            name="OSPF Plan",
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.cli.return_value = {
            "show ospf neighbor": (
                "Address          Interface              State     ID               Pri  Dead\n"
                "10.0.0.1         ge-0/0/0.0             Full      10.0.0.1         128    35\n"
                "10.0.0.2         ge-0/0/1.0             Full      10.0.0.2         128    33\n"
            )
        }

        collector.ospf(mock_driver)

        entries = JournalEntry.objects.filter(assigned_object_id=device.pk)
        self.assertTrue(entries.exists())

    def test_ospf_creates_peer_ips(self):
        """ospf() should create IPAddress objects for OSPF neighbors."""
        from ipam.models import IPAddress

        device = self._create_device("ospf-dev2")
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_OSPF,
            name="OSPF Plan 2",
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.cli.return_value = {
            "show ospf neighbor": (
                "Address          Interface              State     ID               Pri  Dead\n"
                "10.0.0.1         ge-0/0/0.0             Full      10.0.0.1         128    35\n"
            )
        }

        collector.ospf(mock_driver)

        self.assertTrue(IPAddress.objects.filter(address="10.0.0.1/32").exists())

    def test_ospf_unsupported_driver(self):
        """ospf() should raise NotImplementedError for non-Junos drivers."""
        plan = MagicMock()
        plan.napalm_driver = "eos"
        plan.collector_type = CollectionTypeChoices.TYPE_OSPF

        with patch.object(NapalmCollector, "__init__", lambda self, p: None):
            collector = NapalmCollector.__new__(NapalmCollector)
        collector.plan = plan
        collector._collector_type = CollectionTypeChoices.TYPE_OSPF
        collector._now = timezone.now()
        collector._current_device = None
        collector._log_prefix = ""
        collector._report = None
        collector._detect_only = False

        with self.assertRaises(NotImplementedError):
            collector.ospf(MagicMock())


class DetectOnlyInventoryTest(CollectorTestMixin, TestCase):
    """Tests that detect_only=True prevents mutations in inventory()."""

    def test_detect_only_inventory_no_serial_update(self):
        """With detect_only=True, device serial should NOT be updated."""
        from netbox_facts.models.facts_report import FactsReport, FactsReportEntry

        plan = self._create_plan(name="DetectOnly-inv", detect_only=True)
        device = self._create_device("detect-inv-dev", serial="OLD_SERIAL")
        report = FactsReport.objects.create(collection_plan=plan)
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = report

        driver = MagicMock()
        driver.get_facts.return_value = {
            "serial_number": "NEW_SERIAL",
            "os_version": "21.2R3",
            "hostname": "switch1",
            "fqdn": "",
        }

        collector.inventory(driver)

        device.refresh_from_db()
        self.assertEqual(device.serial, "OLD_SERIAL")

        # Verify entry was created
        entries = report.entries.all()
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries[0].action, "changed")
        self.assertEqual(entries[0].status, "pending")
        self.assertEqual(entries[0].detected_values["serial_number"], "NEW_SERIAL")


class DetectOnlyInterfacesTest(CollectorTestMixin, TestCase):
    """Tests that detect_only=True prevents mutations in interfaces()."""

    def _make_collector(self, plan):
        import re as _re
        collector = super()._make_collector(plan)
        collector._interfaces_re = _re.compile(r".*")
        return collector

    def test_detect_only_interfaces_no_mac_created(self):
        """With detect_only=True, no MACAddress objects should be created."""
        from netbox_facts.models.facts_report import FactsReport

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="DetectOnly-ifaces",
            detect_only=True,
        )
        device = self._create_device("detect-iface-dev")
        Interface.objects.create(device=device, name="Ethernet1", type="1000base-t")
        report = FactsReport.objects.create(collection_plan=plan)
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = report

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "Ethernet1": {
                "is_up": True,
                "is_enabled": True,
                "description": "uplink",
                "last_flapped": -1.0,
                "speed": 1000.0,
                "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:99",
            }
        }

        collector.interfaces(driver)

        self.assertFalse(MACAddress.objects.filter(mac_address="AA:BB:CC:DD:EE:99").exists())
        self.assertEqual(report.entries.count(), 1)
        self.assertEqual(report.entries.first().status, "pending")


class DetectOnlyEthernetSwitchingTest(CollectorTestMixin, TestCase):
    """Tests that detect_only=True prevents mutations in ethernet_switching()."""

    def test_detect_only_no_mac_created(self):
        """With detect_only=True, no MACAddress objects should be created."""
        from netbox_facts.models.facts_report import FactsReport

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_L2,
            name="DetectOnly-ethsw",
            detect_only=True,
        )
        device = self._create_device("detect-ethsw-dev")
        Interface.objects.create(device=device, name="Ethernet1", type="1000base-t")
        report = FactsReport.objects.create(collection_plan=plan)
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = report

        driver = MagicMock()
        driver.get_mac_address_table.return_value = [
            {
                "mac": "AA:BB:CC:DD:EE:98",
                "interface": "Ethernet1",
                "vlan": 100,
                "static": False,
                "active": True,
                "moves": 0,
                "last_move": 0.0,
            }
        ]

        collector.ethernet_switching(driver)

        self.assertFalse(MACAddress.objects.filter(mac_address="AA:BB:CC:DD:EE:98").exists())
        self.assertEqual(report.entries.count(), 1)
        self.assertEqual(report.entries.first().action, "new")
        self.assertEqual(report.entries.first().status, "pending")


class DetectOnlyLLDPTest(CollectorTestMixin, TestCase):
    """Tests that detect_only=True prevents mutations in lldp()."""

    def test_detect_only_no_cable_created(self):
        """With detect_only=True, no Cable objects should be created."""
        from dcim.models.cables import Cable as CableModel
        from netbox_facts.models.facts_report import FactsReport

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_LLDP,
            name="DetectOnly-lldp",
            detect_only=True,
        )
        device_a = self._create_device("detect-lldp-a")
        device_b = self._create_device("detect-lldp-b")
        Interface.objects.create(device=device_a, name="Ethernet1", type="1000base-t")
        Interface.objects.create(device=device_b, name="Ethernet1", type="1000base-t")
        report = FactsReport.objects.create(collection_plan=plan)
        collector = self._make_collector(plan)
        collector._current_device = device_a
        collector._report = report

        driver = MagicMock()
        driver.get_lldp_neighbors_detail.return_value = {
            "Ethernet1": [
                {
                    "parent_interface": "Ethernet1",
                    "remote_chassis_id": "AA:BB:CC:DD:EE:FF",
                    "remote_system_name": "detect-lldp-b",
                    "remote_port": "Ethernet1",
                    "remote_port_description": "",
                }
            ]
        }

        collector.lldp(driver)

        self.assertEqual(CableModel.objects.count(), 0)
        self.assertEqual(report.entries.count(), 1)
        self.assertEqual(report.entries.first().action, "new")
        self.assertEqual(report.entries.first().status, "pending")


# ---------------------------------------------------------------------------
# Enhanced-driver logical interface tests (LAG, IPs, VRFs)
# ---------------------------------------------------------------------------


class InterfacesLAGTest(CollectorTestMixin, TestCase):
    """Tests for LAG membership detection via enhanced driver logical_interfaces."""

    def _make_collector(self, plan):
        import re as _re
        collector = super()._make_collector(plan)
        collector._interfaces_re = _re.compile(r".*")
        return collector

    def test_lag_membership_sets_lag_parent(self):
        """Physical interface with aenet family should get lag set to AE parent."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-lag-set",
        )
        device = self._create_device("lag-dev1")
        ge_iface = Interface.objects.create(device=device, name="ge-0/0/0", type="1000base-t")
        ae_iface = Interface.objects.create(device=device, name="ae0", type="lag")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "ge-0/0/0": {
                "is_up": True, "is_enabled": True, "description": "",
                "last_flapped": -1.0, "speed": 1000.0, "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:01",
                "logical_interfaces": {
                    "ge-0/0/0.0": {
                        "families": {
                            "aenet": {"ae_bundle": "ae0.0", "mtu": None},
                        },
                    },
                },
            },
        }

        collector.interfaces(driver)

        ge_iface.refresh_from_db()
        self.assertEqual(ge_iface.lag, ae_iface)

    def test_lag_member_skips_ip_processing(self):
        """LAG member interfaces should not generate IP entries."""
        from netbox_facts.models.facts_report import FactsReport

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-lag-skip-ip",
        )
        device = self._create_device("lag-dev2")
        Interface.objects.create(device=device, name="ge-0/0/1", type="1000base-t")
        Interface.objects.create(device=device, name="ae1", type="lag")
        Interface.objects.create(device=device, name="ge-0/0/1.0", type="virtual")
        report = FactsReport.objects.create(collection_plan=plan)
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = report

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "ge-0/0/1": {
                "is_up": True, "is_enabled": True, "description": "",
                "last_flapped": -1.0, "speed": 1000.0, "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:02",
                "logical_interfaces": {
                    "ge-0/0/1.0": {
                        "families": {
                            "aenet": {"ae_bundle": "ae1.0", "mtu": None},
                        },
                    },
                },
            },
        }

        collector.interfaces(driver)

        # Should have MAC entry + LAG entry, but no IP entries
        ip_entries = [e for e in report.entries.all() if e.object_repr.startswith("IP ")]
        self.assertEqual(len(ip_entries), 0)


class InterfacesIPTest(CollectorTestMixin, TestCase):
    """Tests for IP address and prefix creation via enhanced driver logical_interfaces."""

    def _make_collector(self, plan):
        import re as _re
        collector = super()._make_collector(plan)
        collector._interfaces_re = _re.compile(r".*")
        return collector

    def _make_driver(self, ifaces):
        driver = MagicMock()
        driver.get_interfaces.return_value = ifaces
        return driver

    def test_creates_ip_and_prefix(self):
        """IP address and prefix should be created from logical interface data."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-ip-create",
        )
        device = self._create_device("ip-dev1")
        Interface.objects.create(device=device, name="ge-0/0/2", type="1000base-t")
        li = Interface.objects.create(device=device, name="ge-0/0/2.0", type="virtual")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = self._make_driver({
            "ge-0/0/2": {
                "is_up": True, "is_enabled": True, "description": "",
                "last_flapped": -1.0, "speed": 1000.0, "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:10",
                "logical_interfaces": {
                    "ge-0/0/2.0": {
                        "families": {
                            "inet": {
                                "mtu": 1500, "ae_bundle": "",
                                "addresses": {
                                    "10.0.1.0/24": {
                                        "local": "10.0.1.1",
                                        "broadcast": "",
                                        "preferred": True,
                                        "primary": True,
                                    },
                                },
                            },
                        },
                    },
                },
            },
        })

        collector.interfaces(driver)

        ip = IPAddress.objects.get(address="10.0.1.1/24")
        self.assertEqual(ip.assigned_object, li)
        self.assertTrue(Prefix.objects.filter(prefix="10.0.1.0/24").exists())
        nb_prefix = Prefix.objects.get(prefix="10.0.1.0/24")
        self.assertTrue(nb_prefix.tags.filter(name=AUTO_D_TAG).exists())

    def test_loopback_creates_host_route_no_prefix(self):
        """Loopback (no destination) should create /32 IP without a prefix."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-ip-lo",
        )
        device = self._create_device("ip-dev-lo")
        Interface.objects.create(device=device, name="lo0", type="virtual")
        li = Interface.objects.create(device=device, name="lo0.0", type="virtual")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = self._make_driver({
            "lo0": {
                "is_up": True, "is_enabled": True, "description": "",
                "last_flapped": -1.0, "speed": 0, "mtu": 65535,
                "mac_address": "",
                "logical_interfaces": {
                    "lo0.0": {
                        "families": {
                            "inet": {
                                "mtu": 65535, "ae_bundle": "",
                                "addresses": {
                                    # destination is the dict key; empty string = no destination
                                    "": {
                                        "local": "192.0.2.1",
                                        "broadcast": "",
                                        "preferred": True,
                                        "primary": True,
                                    },
                                },
                            },
                        },
                    },
                },
            },
        })

        collector.interfaces(driver)

        ip = IPAddress.objects.get(address="192.0.2.1/32")
        self.assertEqual(ip.assigned_object, li)
        # Host route: no prefix should be created
        self.assertFalse(Prefix.objects.filter(prefix="192.0.2.1/32").exists())

    def test_vrrp_non_preferred_skipped(self):
        """Non-preferred addresses should be skipped when multiple exist on same interface."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-ip-vrrp",
        )
        device = self._create_device("ip-dev-vrrp")
        Interface.objects.create(device=device, name="ge-0/0/3", type="1000base-t")
        Interface.objects.create(device=device, name="ge-0/0/3.0", type="virtual")
        collector = self._make_collector(plan)
        collector._current_device = device

        # Post-driver data: two different subnets on same interface,
        # one preferred, one not. The non-preferred should be skipped.
        driver = self._make_driver({
            "ge-0/0/3": {
                "is_up": True, "is_enabled": True, "description": "",
                "last_flapped": -1.0, "speed": 1000.0, "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:11",
                "logical_interfaces": {
                    "ge-0/0/3.0": {
                        "families": {
                            "inet": {
                                "mtu": 1500, "ae_bundle": "",
                                "addresses": {
                                    "10.0.2.0/24": {
                                        "local": "10.0.2.1",
                                        "broadcast": "",
                                        "preferred": True,
                                        "primary": True,
                                    },
                                    "10.0.3.0/24": {
                                        "local": "10.0.3.1",
                                        "broadcast": "",
                                        "preferred": False,
                                        "primary": False,
                                    },
                                },
                            },
                        },
                    },
                },
            },
        })

        collector.interfaces(driver)

        # Only the preferred address should be created
        self.assertTrue(IPAddress.objects.filter(address="10.0.2.1/24").exists())
        self.assertFalse(IPAddress.objects.filter(address="10.0.3.1/24").exists())

    def test_incomplete_inet_destination_three_octets(self):
        """Incomplete 3-octet inet destination should be handled."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-ip-3oct",
        )
        device = self._create_device("ip-dev-3oct")
        Interface.objects.create(device=device, name="ge-0/0/4", type="1000base-t")
        li = Interface.objects.create(device=device, name="ge-0/0/4.0", type="virtual")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = self._make_driver({
            "ge-0/0/4": {
                "is_up": True, "is_enabled": True, "description": "",
                "last_flapped": -1.0, "speed": 1000.0, "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:12",
                "logical_interfaces": {
                    "ge-0/0/4.0": {
                        "families": {
                            "inet": {
                                "mtu": 1500, "ae_bundle": "",
                                "addresses": {
                                    "10.0.3/24": {
                                        "local": "10.0.3.1",
                                        "broadcast": "",
                                        "preferred": True,
                                        "primary": True,
                                    },
                                },
                            },
                        },
                    },
                },
            },
        })

        collector.interfaces(driver)

        ip = IPAddress.objects.get(address="10.0.3.1/24")
        self.assertEqual(ip.assigned_object, li)
        self.assertTrue(Prefix.objects.filter(prefix="10.0.3.0/24").exists())

    def test_ip_with_vrf(self):
        """IP should be associated with VRF when logical interface has one."""
        vrf = VRF.objects.create(name="CUST_A")
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-ip-vrf",
        )
        device = self._create_device("ip-dev-vrf")
        Interface.objects.create(device=device, name="ge-0/0/5", type="1000base-t")
        li = Interface.objects.create(device=device, name="ge-0/0/5.100", type="virtual")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = self._make_driver({
            "ge-0/0/5": {
                "is_up": True, "is_enabled": True, "description": "",
                "last_flapped": -1.0, "speed": 1000.0, "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:13",
                "logical_interfaces": {
                    "ge-0/0/5.100": {
                        "vrf": "CUST_A",
                        "families": {
                            "inet": {
                                "mtu": 1500, "ae_bundle": "",
                                "addresses": {
                                    "172.16.0.0/30": {
                                        "local": "172.16.0.1",
                                        "broadcast": "",
                                        "preferred": True,
                                        "primary": True,
                                    },
                                },
                            },
                        },
                    },
                },
            },
        })

        collector.interfaces(driver)

        ip = IPAddress.objects.get(address="172.16.0.1/30")
        self.assertEqual(ip.vrf, vrf)
        self.assertEqual(ip.assigned_object, li)
        prefix = Prefix.objects.get(prefix="172.16.0.0/30")
        self.assertEqual(prefix.vrf, vrf)

    def test_ipv6_address(self):
        """IPv6 addresses from inet6 family should be created."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-ip6",
        )
        device = self._create_device("ip-dev-v6")
        Interface.objects.create(device=device, name="ge-0/0/6", type="1000base-t")
        li = Interface.objects.create(device=device, name="ge-0/0/6.0", type="virtual")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = self._make_driver({
            "ge-0/0/6": {
                "is_up": True, "is_enabled": True, "description": "",
                "last_flapped": -1.0, "speed": 1000.0, "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:14",
                "logical_interfaces": {
                    "ge-0/0/6.0": {
                        "families": {
                            "inet6": {
                                "mtu": 1500, "ae_bundle": "",
                                "addresses": {
                                    "2001:db8::/64": {
                                        "local": "2001:db8::1",
                                        "broadcast": "",
                                        "preferred": True,
                                        "primary": True,
                                    },
                                },
                            },
                        },
                    },
                },
            },
        })

        collector.interfaces(driver)

        ip = IPAddress.objects.get(address="2001:db8::1/64")
        self.assertEqual(ip.assigned_object, li)
        self.assertTrue(Prefix.objects.filter(prefix="2001:db8::/64").exists())


class InterfacesIPGenericTest(CollectorTestMixin, TestCase):
    """Tests for the generic IP collection path using standard NAPALM APIs."""

    def _make_collector(self, plan):
        import re as _re
        collector = super()._make_collector(plan)
        collector._interfaces_re = _re.compile(r".*")
        return collector

    def test_generic_path_creates_ip_from_get_interfaces_ip(self):
        """Standard NAPALM get_interfaces_ip() should create IPs and prefixes."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-generic-ip",
        )
        device = self._create_device("generic-dev1")
        li = Interface.objects.create(device=device, name="Ethernet1", type="1000base-t")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        # No logical_interfaces → triggers generic path
        driver.get_interfaces.return_value = {
            "Ethernet1": {
                "is_up": True, "is_enabled": True, "description": "",
                "last_flapped": -1.0, "speed": 1000.0, "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:20",
            },
        }
        # Standard NAPALM get_interfaces_ip() return format
        driver.get_interfaces_ip.return_value = {
            "Ethernet1": {
                "ipv4": {
                    "10.1.0.1": {"prefix_length": 24},
                },
            },
        }
        # No VRFs
        driver.get_network_instances.return_value = {
            "default": {
                "name": "default",
                "type": "DEFAULT_INSTANCE",
                "state": {"route_distinguisher": ""},
                "interfaces": {"interface": {"Ethernet1": {}}},
            },
        }

        collector.interfaces(driver)

        ip = IPAddress.objects.get(address="10.1.0.1/24")
        self.assertEqual(ip.assigned_object, li)
        self.assertTrue(Prefix.objects.filter(prefix="10.1.0.0/24").exists())

    def test_generic_path_with_vrf(self):
        """Generic path should associate IPs with VRFs from network instances."""
        vrf = VRF.objects.create(name="VRF_B")
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-generic-vrf",
        )
        device = self._create_device("generic-dev2")
        li = Interface.objects.create(device=device, name="Ethernet2", type="1000base-t")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "Ethernet2": {
                "is_up": True, "is_enabled": True, "description": "",
                "last_flapped": -1.0, "speed": 1000.0, "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:21",
            },
        }
        driver.get_interfaces_ip.return_value = {
            "Ethernet2": {
                "ipv4": {
                    "172.16.1.1": {"prefix_length": 30},
                },
            },
        }
        driver.get_network_instances.return_value = {
            "VRF_B": {
                "name": "VRF_B",
                "type": "L3VRF",
                "state": {"route_distinguisher": "65000:200"},
                "interfaces": {"interface": {"Ethernet2": {}}},
            },
        }

        collector.interfaces(driver)

        ip = IPAddress.objects.get(address="172.16.1.1/30")
        self.assertEqual(ip.vrf, vrf)
        self.assertEqual(ip.assigned_object, li)

    def test_generic_path_ipv6(self):
        """Generic path should handle IPv6 addresses."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-generic-v6",
        )
        device = self._create_device("generic-dev3")
        li = Interface.objects.create(device=device, name="Ethernet3", type="1000base-t")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "Ethernet3": {
                "is_up": True, "is_enabled": True, "description": "",
                "last_flapped": -1.0, "speed": 1000.0, "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:22",
            },
        }
        driver.get_interfaces_ip.return_value = {
            "Ethernet3": {
                "ipv6": {
                    "2001:db8:1::1": {"prefix_length": 64},
                },
            },
        }
        driver.get_network_instances.return_value = {
            "default": {
                "name": "default",
                "type": "DEFAULT_INSTANCE",
                "state": {"route_distinguisher": ""},
                "interfaces": {"interface": {"Ethernet3": {}}},
            },
        }

        collector.interfaces(driver)

        ip = IPAddress.objects.get(address="2001:db8:1::1/64")
        self.assertEqual(ip.assigned_object, li)

    def test_generic_path_not_called_when_logical_interfaces_present(self):
        """When logical_interfaces data exists, generic path should NOT be used."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-no-generic",
        )
        device = self._create_device("generic-dev4")
        Interface.objects.create(device=device, name="ge-0/0/7", type="1000base-t")
        Interface.objects.create(device=device, name="ge-0/0/7.0", type="virtual")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "ge-0/0/7": {
                "is_up": True, "is_enabled": True, "description": "",
                "last_flapped": -1.0, "speed": 1000.0, "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:23",
                "logical_interfaces": {
                    "ge-0/0/7.0": {
                        "families": {
                            "inet": {
                                "mtu": 1500, "ae_bundle": "",
                                "addresses": {
                                    "10.0.7.0/24": {
                                        "local": "10.0.7.1",
                                        "broadcast": "",
                                        "preferred": True,
                                        "primary": True,
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }

        collector.interfaces(driver)

        # get_interfaces_ip should NOT have been called
        driver.get_interfaces_ip.assert_not_called()
        # But the IP should still be created via the enhanced path
        self.assertTrue(IPAddress.objects.filter(address="10.0.7.1/24").exists())


class DetectOnlyInterfacesLogicalTest(CollectorTestMixin, TestCase):
    """Tests that detect_only=True prevents mutations for LAG/IP entries."""

    def _make_collector(self, plan):
        import re as _re
        collector = super()._make_collector(plan)
        collector._interfaces_re = _re.compile(r".*")
        return collector

    def test_detect_only_lag_no_mutation(self):
        """With detect_only=True, LAG parent should NOT be set."""
        from netbox_facts.models.facts_report import FactsReport

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="DetectOnly-lag",
            detect_only=True,
        )
        device = self._create_device("detect-lag-dev")
        ge_iface = Interface.objects.create(device=device, name="ge-0/0/8", type="1000base-t")
        Interface.objects.create(device=device, name="ae2", type="lag")
        report = FactsReport.objects.create(collection_plan=plan)
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = report

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "ge-0/0/8": {
                "is_up": True, "is_enabled": True, "description": "",
                "last_flapped": -1.0, "speed": 1000.0, "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:30",
                "logical_interfaces": {
                    "ge-0/0/8.0": {
                        "families": {
                            "aenet": {"ae_bundle": "ae2.0", "mtu": None},
                        },
                    },
                },
            },
        }

        collector.interfaces(driver)

        ge_iface.refresh_from_db()
        self.assertIsNone(ge_iface.lag)
        lag_entries = [e for e in report.entries.all() if e.object_repr.startswith("LAG ")]
        self.assertEqual(len(lag_entries), 1)
        self.assertEqual(lag_entries[0].status, "pending")

    def test_detect_only_ip_no_creation(self):
        """With detect_only=True, no IPAddress should be created."""
        from netbox_facts.models.facts_report import FactsReport

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="DetectOnly-ip",
            detect_only=True,
        )
        device = self._create_device("detect-ip-dev")
        Interface.objects.create(device=device, name="ge-0/0/9", type="1000base-t")
        Interface.objects.create(device=device, name="ge-0/0/9.0", type="virtual")
        report = FactsReport.objects.create(collection_plan=plan)
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = report

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "ge-0/0/9": {
                "is_up": True, "is_enabled": True, "description": "",
                "last_flapped": -1.0, "speed": 1000.0, "mtu": 1500,
                "mac_address": "AA:BB:CC:DD:EE:31",
                "logical_interfaces": {
                    "ge-0/0/9.0": {
                        "families": {
                            "inet": {
                                "mtu": 1500, "ae_bundle": "",
                                "addresses": {
                                    "10.99.0.0/24": {
                                        "local": "10.99.0.1",
                                        "broadcast": "",
                                        "preferred": True,
                                        "primary": True,
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }

        collector.interfaces(driver)

        self.assertFalse(IPAddress.objects.filter(address="10.99.0.1/24").exists())
        ip_entries = [e for e in report.entries.all() if e.object_repr.startswith("IP ")]
        self.assertEqual(len(ip_entries), 1)
        self.assertEqual(ip_entries[0].status, "pending")


class ParseAddressFamiliesTest(TestCase):
    """Tests for EnhancedJunOSDriver._parse_address_families VRRP handling."""

    @staticmethod
    def _mock_table(items_list):
        """Create a mock PyEZ table-like object from a list of (key, value) tuples."""
        mock = MagicMock()
        mock.items.return_value = items_list
        return mock

    def test_vrrp_backup_keeps_preferred(self):
        """Duplicate destination: preferred (real) comes first, non-preferred (VGA) is dropped."""
        addr_table = self._mock_table([
            ("10.0.2.0/24", [
                ("local", "10.0.2.1"), ("broadcast", "10.0.2.255"),
                ("preferred", True), ("primary", True),
            ]),
            ("10.0.2.0/24", [
                ("local", "10.0.2.254"), ("broadcast", ""),
                ("preferred", False), ("primary", False),
            ]),
        ])
        family_table = self._mock_table([
            ("inet", [("mtu", 1500), ("ae_bundle", ""), ("addresses", addr_table)]),
        ])

        result = EnhancedJunOSDriver._parse_address_families(family_table)
        addresses = result["inet"]["addresses"]

        self.assertEqual(len(addresses), 1)
        self.assertEqual(addresses["10.0.2.0/24"]["local"], "10.0.2.1")

    def test_vrrp_master_keeps_first_preferred(self):
        """Duplicate destination: both preferred (master), first entry wins."""
        addr_table = self._mock_table([
            ("10.0.2.0/24", [
                ("local", "10.0.2.1"), ("broadcast", "10.0.2.255"),
                ("preferred", True), ("primary", True),
            ]),
            ("10.0.2.0/24", [
                ("local", "10.0.2.254"), ("broadcast", ""),
                ("preferred", True), ("primary", True),
            ]),
        ])
        family_table = self._mock_table([
            ("inet", [("mtu", 1500), ("ae_bundle", ""), ("addresses", addr_table)]),
        ])

        result = EnhancedJunOSDriver._parse_address_families(family_table)
        addresses = result["inet"]["addresses"]

        self.assertEqual(len(addresses), 1)
        self.assertEqual(addresses["10.0.2.0/24"]["local"], "10.0.2.1")

    def test_vrrp_vga_first_real_overwrites(self):
        """Duplicate destination: VGA (non-preferred) comes first, preferred overwrites it."""
        addr_table = self._mock_table([
            ("10.0.2.0/24", [
                ("local", "10.0.2.254"), ("broadcast", ""),
                ("preferred", False), ("primary", False),
            ]),
            ("10.0.2.0/24", [
                ("local", "10.0.2.1"), ("broadcast", "10.0.2.255"),
                ("preferred", True), ("primary", True),
            ]),
        ])
        family_table = self._mock_table([
            ("inet", [("mtu", 1500), ("ae_bundle", ""), ("addresses", addr_table)]),
        ])

        result = EnhancedJunOSDriver._parse_address_families(family_table)
        addresses = result["inet"]["addresses"]

        self.assertEqual(len(addresses), 1)
        self.assertEqual(addresses["10.0.2.0/24"]["local"], "10.0.2.1")


def _iface_driver_data(physical, logical, ip, prefix_len=24, mac="AA:BB:CC:DD:EE:99"):
    """Build minimal enhanced-driver iface dict with one IP."""
    net_part = ip.rsplit(".", 1)[0]
    return {
        physical: {
            "is_up": True, "is_enabled": True, "description": "",
            "last_flapped": -1.0, "speed": 1000.0, "mtu": 1500,
            "mac_address": mac,
            "logical_interfaces": {
                logical: {
                    "families": {
                        "inet": {
                            "mtu": 1500, "ae_bundle": "",
                            "addresses": {
                                f"{net_part}.0/{prefix_len}": {
                                    "local": ip,
                                    "broadcast": "",
                                    "preferred": True,
                                    "primary": True,
                                },
                            },
                        },
                    },
                },
            },
        },
    }


class InterfacesIPReprocessTest(CollectorTestMixin, TestCase):
    """Tests for reprocessing auto-discovered IPs that changed assignment."""

    def _make_collector(self, plan):
        import re as _re
        collector = super()._make_collector(plan)
        collector._interfaces_re = _re.compile(r".*")
        return collector

    def _make_driver(self, ifaces):
        driver = MagicMock()
        driver.get_interfaces.return_value = ifaces
        return driver

    def test_reassigns_auto_discovered_ip(self):
        """IP with AUTO_D_TAG on wrong interface should be reassigned."""
        from netbox_facts.models.facts_report import FactsReport

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-reassign",
        )
        device = self._create_device("reassign-dev")
        Interface.objects.create(device=device, name="ge-0/0/3", type="1000base-t")
        old_li = Interface.objects.create(device=device, name="ge-0/0/3.0", type="virtual")
        new_li = Interface.objects.create(device=device, name="ge-0/0/3.1", type="virtual")

        # Pre-create IP assigned to old interface with AUTO_D_TAG
        ip = IPAddress.objects.create(address="10.0.3.1/24", assigned_object=old_li)
        ip.tags.add(AUTO_D_TAG)

        report = FactsReport.objects.create(collection_plan=plan)
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = report

        # Driver says the IP is on ge-0/0/3.1, not ge-0/0/3.0
        driver = self._make_driver(_iface_driver_data(
            "ge-0/0/3", "ge-0/0/3.1", "10.0.3.1",
        ))
        collector.interfaces(driver)

        ip.refresh_from_db()
        self.assertEqual(ip.assigned_object, new_li)

        # Verify CHANGED entry recorded
        entries = report.entries.filter(object_repr__startswith="IP ")
        self.assertTrue(entries.filter(action=EntryActionChoices.ACTION_CHANGED).exists())

    def test_does_not_reassign_manual_ip(self):
        """IP without AUTO_D_TAG should be left alone (CONFIRMED, not reassigned)."""
        from netbox_facts.models.facts_report import FactsReport

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-manual",
        )
        device = self._create_device("manual-dev")
        Interface.objects.create(device=device, name="ge-0/0/4", type="1000base-t")
        old_li = Interface.objects.create(device=device, name="ge-0/0/4.0", type="virtual")
        new_li = Interface.objects.create(device=device, name="ge-0/0/4.1", type="virtual")

        # Pre-create IP on old interface WITHOUT AUTO_D_TAG
        ip = IPAddress.objects.create(address="10.0.4.1/24", assigned_object=old_li)

        report = FactsReport.objects.create(collection_plan=plan)
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = report

        driver = self._make_driver(_iface_driver_data(
            "ge-0/0/4", "ge-0/0/4.1", "10.0.4.1",
        ))
        collector.interfaces(driver)

        ip.refresh_from_db()
        self.assertEqual(ip.assigned_object, old_li)

        entries = report.entries.filter(object_repr__startswith="IP ")
        self.assertTrue(entries.filter(action=EntryActionChoices.ACTION_CONFIRMED).exists())
        self.assertFalse(entries.filter(action=EntryActionChoices.ACTION_CHANGED).exists())


class InterfacesStaleIPTest(CollectorTestMixin, TestCase):
    """Tests for detection and cleanup of stale auto-discovered IPs."""

    def _make_collector(self, plan):
        import re as _re
        collector = super()._make_collector(plan)
        collector._interfaces_re = _re.compile(r".*")
        return collector

    def _make_driver(self, ifaces):
        driver = MagicMock()
        driver.get_interfaces.return_value = ifaces
        return driver

    def test_stale_ip_unassigned_in_auto_apply(self):
        """Auto-discovered IP not in driver data should be unassigned."""
        from netbox_facts.models.facts_report import FactsReport

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-stale-apply",
        )
        device = self._create_device("stale-dev1")
        Interface.objects.create(device=device, name="ge-0/0/5", type="1000base-t")
        li = Interface.objects.create(device=device, name="ge-0/0/5.0", type="virtual")

        # Pre-create stale IP on the interface
        stale_ip = IPAddress.objects.create(address="10.0.99.1/24", assigned_object=li)
        stale_ip.tags.add(AUTO_D_TAG)

        report = FactsReport.objects.create(collection_plan=plan)
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = report

        # Driver returns a DIFFERENT IP, so the stale one is not seen
        driver = self._make_driver(_iface_driver_data(
            "ge-0/0/5", "ge-0/0/5.0", "10.0.5.1",
        ))
        collector.interfaces(driver)

        stale_ip.refresh_from_db()
        self.assertIsNone(stale_ip.assigned_object)

        entries = report.entries.filter(action=EntryActionChoices.ACTION_STALE)
        self.assertEqual(entries.count(), 1)

    def test_stale_ip_detected_in_detect_only(self):
        """Detect-only mode should record STALE entry but not unassign."""
        from netbox_facts.models.facts_report import FactsReport

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-stale-detect",
            detect_only=True,
        )
        device = self._create_device("stale-dev2")
        Interface.objects.create(device=device, name="ge-0/0/6", type="1000base-t")
        li = Interface.objects.create(device=device, name="ge-0/0/6.0", type="virtual")

        stale_ip = IPAddress.objects.create(address="10.0.98.1/24", assigned_object=li)
        stale_ip.tags.add(AUTO_D_TAG)

        report = FactsReport.objects.create(collection_plan=plan)
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = report

        driver = self._make_driver(_iface_driver_data(
            "ge-0/0/6", "ge-0/0/6.0", "10.0.6.1",
            mac="AA:BB:CC:DD:EE:A1",
        ))
        collector.interfaces(driver)

        stale_ip.refresh_from_db()
        # Still assigned (detect-only doesn't mutate)
        self.assertEqual(stale_ip.assigned_object, li)

        entries = report.entries.filter(action=EntryActionChoices.ACTION_STALE)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries[0].status, "pending")

    def test_manual_ip_not_flagged_stale(self):
        """IP without AUTO_D_TAG should not get a stale entry."""
        from netbox_facts.models.facts_report import FactsReport

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-stale-manual",
        )
        device = self._create_device("stale-dev3")
        Interface.objects.create(device=device, name="ge-0/0/7", type="1000base-t")
        li = Interface.objects.create(device=device, name="ge-0/0/7.0", type="virtual")

        # Manual IP (no AUTO_D_TAG)
        manual_ip = IPAddress.objects.create(address="10.0.97.1/24", assigned_object=li)

        report = FactsReport.objects.create(collection_plan=plan)
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = report

        driver = self._make_driver(_iface_driver_data(
            "ge-0/0/7", "ge-0/0/7.0", "10.0.7.1",
        ))
        collector.interfaces(driver)

        entries = report.entries.filter(action=EntryActionChoices.ACTION_STALE)
        self.assertEqual(entries.count(), 0)

        manual_ip.refresh_from_db()
        self.assertEqual(manual_ip.assigned_object, li)

    def test_seen_ip_not_flagged_stale(self):
        """IP present in driver data should not be flagged stale."""
        from netbox_facts.models.facts_report import FactsReport

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-stale-seen",
        )
        device = self._create_device("stale-dev4")
        Interface.objects.create(device=device, name="ge-0/0/8", type="1000base-t")
        li = Interface.objects.create(device=device, name="ge-0/0/8.0", type="virtual")

        # Pre-create auto-discovered IP that IS in the driver data
        ip = IPAddress.objects.create(address="10.0.8.1/24", assigned_object=li)
        ip.tags.add(AUTO_D_TAG)

        report = FactsReport.objects.create(collection_plan=plan)
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = report

        driver = self._make_driver(_iface_driver_data(
            "ge-0/0/8", "ge-0/0/8.0", "10.0.8.1",
        ))
        collector.interfaces(driver)

        entries = report.entries.filter(action=EntryActionChoices.ACTION_STALE)
        self.assertEqual(entries.count(), 0)

        ip.refresh_from_db()
        self.assertEqual(ip.assigned_object, li)

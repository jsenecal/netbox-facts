from django.test import TestCase
from dcim.choices import DeviceStatusChoices
from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Manufacturer,
    Site,
)
from dcim.models.device_components import Interface
from ipam.models.ip import IPAddress, Prefix
from ipam.models.vrfs import VRF

from netbox_facts.choices import (
    CollectionTypeChoices,
    EntryActionChoices,
    EntryStatusChoices,
    ReportStatusChoices,
)
from netbox_facts.constants import AUTO_D_TAG
from netbox_facts.helpers.applier import apply_entries, skip_entries
from netbox_facts.models import CollectionPlan, FactsReport, FactsReportEntry
from netbox_facts.models.mac import MACAddress


class ApplierTestMixin:
    """Shared setup for applier tests."""

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="Applier Site", slug="applier-site")
        cls.manufacturer = Manufacturer.objects.create(name="AppMfg", slug="appmfg")
        cls.device_type = DeviceType.objects.create(
            manufacturer=cls.manufacturer, model="AppModel", slug="appmodel"
        )
        cls.role = DeviceRole.objects.create(name="AppRole", slug="approle")
        cls.device = Device.objects.create(
            name="applier-dev",
            site=cls.site,
            device_type=cls.device_type,
            role=cls.role,
            status=DeviceStatusChoices.STATUS_ACTIVE,
        )
        cls.plan = CollectionPlan.objects.create(
            name="Applier Plan",
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
            detect_only=True,
        )


class ApplyInventoryEntryTest(ApplierTestMixin, TestCase):
    """Tests for applying inventory entries."""

    def test_apply_serial_change(self):
        """Applying a changed inventory entry should update the device serial."""
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_CHANGED,
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            device=self.device,
            object_repr=f"Device {self.device.name}",
            detected_values={"serial_number": "APPLIED_SERIAL", "os_version": "21.2R3"},
            current_values={"serial_number": self.device.serial},
        )

        applied, failed = apply_entries(report, [entry.pk])
        self.assertEqual(applied, 1)
        self.assertEqual(failed, 0)

        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_APPLIED)
        self.assertIsNotNone(entry.applied_at)

        self.device.refresh_from_db()
        self.assertEqual(self.device.serial, "APPLIED_SERIAL")


class ApplyInterfaceEntryTest(ApplierTestMixin, TestCase):
    """Tests for applying interface MAC entries."""

    def test_apply_creates_mac(self):
        """Applying a new interface entry should create a MACAddress."""
        Interface.objects.create(device=self.device, name="Ethernet1", type="1000base-t")
        report = FactsReport.objects.create(
            collection_plan=self.plan,
            status=ReportStatusChoices.STATUS_COMPLETED,
        )
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            device=self.device,
            object_repr="Interface Ethernet1 MAC AA:BB:CC:DD:EE:77",
            detected_values={"interface": "Ethernet1", "mac_address": "AA:BB:CC:DD:EE:77"},
        )

        applied, failed = apply_entries(report, [entry.pk])
        self.assertEqual(applied, 1)
        self.assertTrue(MACAddress.objects.filter(mac_address="AA:BB:CC:DD:EE:77").exists())


class ApplyEthernetSwitchingEntryTest(ApplierTestMixin, TestCase):
    """Tests for applying ethernet switching entries."""

    def test_apply_creates_mac(self):
        Interface.objects.create(device=self.device, name="Ethernet1", type="1000base-t")
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_L2,
            device=self.device,
            object_repr="MAC AA:BB:CC:DD:EE:88 on Ethernet1",
            detected_values={"mac": "AA:BB:CC:DD:EE:88", "interface": "Ethernet1", "vlan": 100},
        )

        applied, failed = apply_entries(report, [entry.pk])
        self.assertEqual(applied, 1)
        mac = MACAddress.objects.get(mac_address="AA:BB:CC:DD:EE:88")
        self.assertEqual(mac.discovery_method, CollectionTypeChoices.TYPE_L2)


class ApplyFailureTest(ApplierTestMixin, TestCase):
    """Tests for apply failures."""

    def test_apply_lldp_missing_device_fails(self):
        """Applying an LLDP entry with non-existent remote device should fail."""
        Interface.objects.create(device=self.device, name="Ethernet1", type="1000base-t")
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_LLDP,
            device=self.device,
            object_repr="Cable Ethernet1 ↔ nonexistent:Ethernet1",
            detected_values={
                "local_interface": "Ethernet1",
                "remote_device": "nonexistent-device",
                "remote_interface": "Ethernet1",
            },
        )

        applied, failed = apply_entries(report, [entry.pk])
        self.assertEqual(applied, 0)
        self.assertEqual(failed, 1)

        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_FAILED)
        self.assertTrue(len(entry.error_message) > 0)


class SkipEntriesTest(ApplierTestMixin, TestCase):
    """Tests for skip_entries."""

    def test_skip_entries(self):
        report = FactsReport.objects.create(collection_plan=self.plan)
        e1 = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            device=self.device,
            object_repr="Skip test 1",
        )
        e2 = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_CHANGED,
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            device=self.device,
            object_repr="Skip test 2",
        )

        count = skip_entries(report, [e1.pk, e2.pk])
        self.assertEqual(count, 2)

        e1.refresh_from_db()
        e2.refresh_from_db()
        self.assertEqual(e1.status, EntryStatusChoices.STATUS_SKIPPED)
        self.assertEqual(e2.status, EntryStatusChoices.STATUS_SKIPPED)

    def test_skip_only_pending(self):
        """Already-applied entries should not be skipped."""
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            status=EntryStatusChoices.STATUS_APPLIED,
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            device=self.device,
            object_repr="Applied entry",
        )

        count = skip_entries(report, [entry.pk])
        self.assertEqual(count, 0)

        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_APPLIED)


class ReportStatusTransitionTest(ApplierTestMixin, TestCase):
    """Tests for report status transitions after apply/skip."""

    def test_all_applied_sets_applied(self):
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_CHANGED,
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            device=self.device,
            object_repr="Status test",
            detected_values={"serial_number": "NEW"},
            current_values={"serial_number": "OLD"},
        )

        apply_entries(report, [entry.pk])
        report.refresh_from_db()
        self.assertEqual(report.status, ReportStatusChoices.STATUS_APPLIED)

    def test_partial_apply_sets_partial(self):
        report = FactsReport.objects.create(collection_plan=self.plan)
        e1 = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_EVPN,
            device=self.device,
            object_repr="Partial 1",
            detected_values={"mac": "AA:BB:CC:DD:EE:66"},
        )
        e2 = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_EVPN,
            device=self.device,
            object_repr="Partial 2",
            detected_values={"mac": "AA:BB:CC:DD:EE:55"},
        )

        # Apply only one
        apply_entries(report, [e1.pk])
        report.refresh_from_db()
        self.assertEqual(report.status, ReportStatusChoices.STATUS_PARTIAL)

    def test_all_skipped_sets_completed(self):
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            device=self.device,
            object_repr="Skip status test",
        )

        skip_entries(report, [entry.pk])
        report.refresh_from_db()
        self.assertEqual(report.status, ReportStatusChoices.STATUS_COMPLETED)


class ApplyInterfaceLAGEntryTest(ApplierTestMixin, TestCase):
    """Tests for applying LAG membership entries."""

    def test_apply_lag_sets_parent(self):
        """Applying a LAG entry should set the interface's lag field."""
        ge_iface = Interface.objects.create(
            device=self.device, name="ge-0/0/0", type="1000base-t"
        )
        ae_iface = Interface.objects.create(
            device=self.device, name="ae0", type="lag"
        )
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            device=self.device,
            object_repr="LAG ge-0/0/0 -> ae0",
            detected_values={"interface": "ge-0/0/0", "lag_parent": "ae0"},
            current_values={"lag_parent": None},
        )

        applied, failed = apply_entries(report, [entry.pk])
        self.assertEqual(applied, 1)
        self.assertEqual(failed, 0)

        ge_iface.refresh_from_db()
        self.assertEqual(ge_iface.lag, ae_iface)

    def test_apply_lag_missing_parent_creates_it(self):
        """Applying a LAG entry when ae parent doesn't exist should auto-create it."""
        Interface.objects.create(
            device=self.device, name="ge-0/0/1", type="1000base-t"
        )
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            device=self.device,
            object_repr="LAG ge-0/0/1 -> ae99",
            detected_values={"interface": "ge-0/0/1", "lag_parent": "ae99"},
            current_values={"lag_parent": None},
        )

        applied, failed = apply_entries(report, [entry.pk])
        self.assertEqual(applied, 1)
        self.assertEqual(failed, 0)

        # ae99 should have been auto-created as LAG type
        ae_iface = Interface.objects.get(device=self.device, name="ae99")
        self.assertEqual(ae_iface.type, "lag")
        self.assertTrue(ae_iface.tags.filter(name=AUTO_D_TAG).exists())

        # ge-0/0/1 should now point to ae99
        ge_iface = Interface.objects.get(device=self.device, name="ge-0/0/1")
        self.assertEqual(ge_iface.lag, ae_iface)


class ApplyInterfaceIPEntryTest(ApplierTestMixin, TestCase):
    """Tests for applying IP address entries."""

    def test_apply_ip_creates_address_and_prefix(self):
        """Applying an IP entry should create IPAddress and Prefix."""
        li = Interface.objects.create(
            device=self.device, name="ge-0/0/2.0", type="virtual"
        )
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            device=self.device,
            object_repr="IP 10.0.5.1/24 on ge-0/0/2.0",
            detected_values={
                "logical_interface": "ge-0/0/2.0",
                "ip_address": "10.0.5.1/24",
                "vrf": None,
                "prefix": "10.0.5.0/24",
            },
        )

        applied, failed = apply_entries(report, [entry.pk])
        self.assertEqual(applied, 1)
        self.assertEqual(failed, 0)

        ip = IPAddress.objects.get(address="10.0.5.1/24")
        self.assertEqual(ip.assigned_object, li)
        self.assertTrue(Prefix.objects.filter(prefix="10.0.5.0/24").exists())
        nb_prefix = Prefix.objects.get(prefix="10.0.5.0/24")
        self.assertTrue(nb_prefix.tags.filter(name=AUTO_D_TAG).exists())

    def test_apply_ip_with_vrf(self):
        """Applying an IP entry with VRF should link both IP and prefix to VRF."""
        vrf = VRF.objects.create(name="APPLY_VRF")
        li = Interface.objects.create(
            device=self.device, name="ge-0/0/3.100", type="virtual"
        )
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            device=self.device,
            object_repr="IP 172.16.5.1/30 on ge-0/0/3.100",
            detected_values={
                "logical_interface": "ge-0/0/3.100",
                "ip_address": "172.16.5.1/30",
                "vrf": "APPLY_VRF",
                "prefix": "172.16.5.0/30",
            },
        )

        applied, failed = apply_entries(report, [entry.pk])
        self.assertEqual(applied, 1)

        ip = IPAddress.objects.get(address="172.16.5.1/30")
        self.assertEqual(ip.vrf, vrf)
        self.assertEqual(ip.assigned_object, li)
        prefix = Prefix.objects.get(prefix="172.16.5.0/30")
        self.assertEqual(prefix.vrf, vrf)

    def test_apply_ip_host_route_no_prefix(self):
        """Applying a /32 host route should not create a prefix."""
        Interface.objects.create(
            device=self.device, name="lo0.0", type="virtual"
        )
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            device=self.device,
            object_repr="IP 192.0.2.1/32 on lo0.0",
            detected_values={
                "logical_interface": "lo0.0",
                "ip_address": "192.0.2.1/32",
                "vrf": None,
                "prefix": "192.0.2.1/32",
            },
        )

        applied, failed = apply_entries(report, [entry.pk])
        self.assertEqual(applied, 1)

        self.assertTrue(IPAddress.objects.filter(address="192.0.2.1/32").exists())
        self.assertFalse(Prefix.objects.filter(prefix="192.0.2.1/32").exists())

    def test_apply_ip_existing_unassigned_sets_interface(self):
        """Applying IP entry when IP exists but has no assigned_object should assign it."""
        li = Interface.objects.create(
            device=self.device, name="ge-0/0/4.0", type="virtual"
        )
        existing_ip = IPAddress.objects.create(address="10.0.6.1/24")
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            device=self.device,
            object_repr="IP 10.0.6.1/24 on ge-0/0/4.0",
            detected_values={
                "logical_interface": "ge-0/0/4.0",
                "ip_address": "10.0.6.1/24",
                "vrf": None,
                "prefix": "10.0.6.0/24",
            },
        )

        applied, failed = apply_entries(report, [entry.pk])
        self.assertEqual(applied, 1)

        existing_ip.refresh_from_db()
        self.assertEqual(existing_ip.assigned_object, li)


class ApplyInterfaceAutoCreateTest(ApplierTestMixin, TestCase):
    """Tests for auto-creation of missing interfaces during apply."""

    def test_apply_mac_creates_missing_interface(self):
        """Applying a MAC entry should auto-create the missing interface."""
        # No interface pre-created in NetBox
        report = FactsReport.objects.create(
            collection_plan=self.plan,
            status=ReportStatusChoices.STATUS_COMPLETED,
        )
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            device=self.device,
            object_repr="Interface ge-0/0/5 MAC AA:BB:CC:DD:EE:B1",
            detected_values={"interface": "ge-0/0/5", "mac_address": "AA:BB:CC:DD:EE:B1"},
        )

        applied, failed = apply_entries(report, [entry.pk])
        self.assertEqual(applied, 1)
        self.assertEqual(failed, 0)

        # Interface should have been auto-created with type 'other'
        nb_iface = Interface.objects.get(device=self.device, name="ge-0/0/5")
        self.assertEqual(nb_iface.type, "other")
        self.assertTrue(nb_iface.tags.filter(name=AUTO_D_TAG).exists())

        # MAC should be linked to the auto-created interface
        mac = MACAddress.objects.get(mac_address="AA:BB:CC:DD:EE:B1")
        self.assertEqual(mac.device_interface, nb_iface)

    def test_apply_ip_creates_missing_logical_interface(self):
        """Applying an IP entry should auto-create the missing logical interface."""
        # No interface pre-created in NetBox
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            device=self.device,
            object_repr="IP 10.0.7.1/24 on ge-0/0/6.0",
            detected_values={
                "logical_interface": "ge-0/0/6.0",
                "ip_address": "10.0.7.1/24",
                "vrf": None,
                "prefix": "10.0.7.0/24",
            },
        )

        applied, failed = apply_entries(report, [entry.pk])
        self.assertEqual(applied, 1)
        self.assertEqual(failed, 0)

        # Logical interface should have been auto-created as virtual
        nb_li = Interface.objects.get(device=self.device, name="ge-0/0/6.0")
        self.assertEqual(nb_li.type, "virtual")
        self.assertTrue(nb_li.tags.filter(name=AUTO_D_TAG).exists())

        # IP should be assigned to the auto-created interface
        ip = IPAddress.objects.get(address="10.0.7.1/24")
        self.assertEqual(ip.assigned_object, nb_li)


class ApplyIPReassignTest(ApplierTestMixin, TestCase):
    """Tests for applier handling of auto-discovered IP reassignment and stale cleanup."""

    def test_apply_ip_reassigns_auto_discovered(self):
        """Applier should update assignment on an AUTO_D_TAG IP with CHANGED action."""
        old_li = Interface.objects.create(device=self.device, name="ge-0/0/9.0", type="virtual")
        new_li = Interface.objects.create(device=self.device, name="ge-0/0/9.1", type="virtual")

        ip = IPAddress.objects.create(address="10.0.9.1/24", assigned_object=old_li)
        ip.tags.add(AUTO_D_TAG)

        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_CHANGED,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            device=self.device,
            object_repr="IP 10.0.9.1/24 on ge-0/0/9.1",
            detected_values={
                "logical_interface": "ge-0/0/9.1",
                "ip_address": "10.0.9.1/24",
                "vrf": None,
                "prefix": "10.0.9.0/24",
            },
            current_values={
                "assigned_object": str(old_li),
            },
        )

        applied, failed = apply_entries(report, [entry.pk])
        self.assertEqual(applied, 1)
        self.assertEqual(failed, 0)

        ip.refresh_from_db()
        self.assertEqual(ip.assigned_object, new_li)

    def test_apply_stale_entry_unassigns_ip(self):
        """Applier should unassign a stale AUTO_D_TAG IP."""
        li = Interface.objects.create(device=self.device, name="ge-0/0/10.0", type="virtual")

        ip = IPAddress.objects.create(address="10.0.10.1/24", assigned_object=li)
        ip.tags.add(AUTO_D_TAG)

        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_STALE,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            device=self.device,
            object_repr=f"IP 10.0.10.1/24 on {li}",
            detected_values={},
            current_values={
                "ip_address": "10.0.10.1/24",
                "vrf": None,
                "assigned_object": str(li),
            },
        )

        applied, failed = apply_entries(report, [entry.pk])
        self.assertEqual(applied, 1)
        self.assertEqual(failed, 0)

        ip.refresh_from_db()
        self.assertIsNone(ip.assigned_object)

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

from netbox_facts.choices import (
    CollectionTypeChoices,
    EntryActionChoices,
    EntryStatusChoices,
    ReportStatusChoices,
)
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

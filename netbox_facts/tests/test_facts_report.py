from django.test import TestCase
from dcim.choices import DeviceStatusChoices
from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Manufacturer,
    Site,
)

from netbox_facts.choices import (
    CollectionTypeChoices,
    EntryActionChoices,
    EntryStatusChoices,
    ReportStatusChoices,
)
from netbox_facts.models import CollectionPlan, FactsReport, FactsReportEntry


class FactsReportModelTest(TestCase):
    """Tests for the FactsReport model."""

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="Report Site", slug="report-site")
        cls.manufacturer = Manufacturer.objects.create(name="RMfg", slug="rmfg")
        cls.device_type = DeviceType.objects.create(
            manufacturer=cls.manufacturer, model="RModel", slug="rmodel"
        )
        cls.role = DeviceRole.objects.create(name="RRole", slug="rrole")
        cls.device = Device.objects.create(
            name="report-dev",
            site=cls.site,
            device_type=cls.device_type,
            role=cls.role,
            status=DeviceStatusChoices.STATUS_ACTIVE,
        )
        cls.plan = CollectionPlan.objects.create(
            name="Report Plan",
            collector_type=CollectionTypeChoices.TYPE_ARP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )

    def test_create_report(self):
        report = FactsReport.objects.create(collection_plan=self.plan)
        self.assertIsNotNone(report.pk)
        self.assertEqual(report.status, ReportStatusChoices.STATUS_PENDING)

    def test_str(self):
        report = FactsReport.objects.create(collection_plan=self.plan)
        self.assertIn("Report Plan", str(report))
        self.assertIn(str(report.pk), str(report))

    def test_get_absolute_url(self):
        report = FactsReport.objects.create(collection_plan=self.plan)
        url = report.get_absolute_url()
        self.assertIn(str(report.pk), url)

    def test_update_summary(self):
        report = FactsReport.objects.create(collection_plan=self.plan)
        FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_ARP,
            device=self.device,
            object_repr="MAC AA:BB:CC:DD:EE:01",
        )
        FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_ARP,
            device=self.device,
            object_repr="MAC AA:BB:CC:DD:EE:02",
        )
        FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_CHANGED,
            collector_type=CollectionTypeChoices.TYPE_ARP,
            device=self.device,
            object_repr="MAC AA:BB:CC:DD:EE:03",
        )
        report.update_summary()
        report.refresh_from_db()
        self.assertEqual(report.summary["new"], 2)
        self.assertEqual(report.summary["changed"], 1)
        self.assertEqual(report.summary["confirmed"], 0)
        self.assertEqual(report.summary["stale"], 0)

    def test_default_summary_empty(self):
        report = FactsReport.objects.create(collection_plan=self.plan)
        self.assertEqual(report.summary, {})


class FactsReportEntryModelTest(TestCase):
    """Tests for the FactsReportEntry model."""

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="Entry Site", slug="entry-site")
        cls.manufacturer = Manufacturer.objects.create(name="EMfg", slug="emfg")
        cls.device_type = DeviceType.objects.create(
            manufacturer=cls.manufacturer, model="EModel", slug="emodel"
        )
        cls.role = DeviceRole.objects.create(name="ERole", slug="erole")
        cls.device = Device.objects.create(
            name="entry-dev",
            site=cls.site,
            device_type=cls.device_type,
            role=cls.role,
            status=DeviceStatusChoices.STATUS_ACTIVE,
        )
        cls.plan = CollectionPlan.objects.create(
            name="Entry Plan",
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )

    def test_create_entry(self):
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            device=self.device,
            object_repr="Device entry-dev serial",
            detected_values={"serial_number": "NEW123"},
        )
        self.assertIsNotNone(entry.pk)
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_PENDING)

    def test_action_choices(self):
        report = FactsReport.objects.create(collection_plan=self.plan)
        for action_val, _, _ in EntryActionChoices.CHOICES:
            entry = FactsReportEntry.objects.create(
                report=report,
                action=action_val,
                collector_type=CollectionTypeChoices.TYPE_INVENTORY,
                device=self.device,
                object_repr=f"Test {action_val}",
            )
            self.assertEqual(entry.action, action_val)

    def test_status_choices(self):
        report = FactsReport.objects.create(collection_plan=self.plan)
        for status_val, _, _ in EntryStatusChoices.CHOICES:
            entry = FactsReportEntry.objects.create(
                report=report,
                action=EntryActionChoices.ACTION_NEW,
                status=status_val,
                collector_type=CollectionTypeChoices.TYPE_INVENTORY,
                device=self.device,
                object_repr=f"Test {status_val}",
            )
            self.assertEqual(entry.status, status_val)

    def test_str(self):
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            device=self.device,
            object_repr="Interface ge-0/0/0",
        )
        self.assertIn("Interface ge-0/0/0", str(entry))

    def test_cascade_delete(self):
        """Deleting a report should cascade-delete all its entries."""
        report = FactsReport.objects.create(collection_plan=self.plan)
        FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            device=self.device,
            object_repr="Cascade test 1",
        )
        FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_CHANGED,
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            device=self.device,
            object_repr="Cascade test 2",
        )
        report_pk = report.pk
        self.assertEqual(FactsReportEntry.objects.filter(report_id=report_pk).count(), 2)
        report.delete()
        self.assertEqual(FactsReportEntry.objects.filter(report_id=report_pk).count(), 0)

    def test_detected_values_json(self):
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_CHANGED,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            device=self.device,
            object_repr="Interface ge-0/0/0",
            detected_values={"interface": "ge-0/0/0", "speed": 1000, "mtu": 9000},
            current_values={"interface": "ge-0/0/0", "speed": 1000, "mtu": 1500},
        )
        entry.refresh_from_db()
        self.assertEqual(entry.detected_values["mtu"], 9000)
        self.assertEqual(entry.current_values["mtu"], 1500)


class CollectionPlanDetectOnlyTest(TestCase):
    """Tests for the detect_only field on CollectionPlan."""

    def test_detect_only_default(self):
        plan = CollectionPlan.objects.create(
            name="Detect Default",
            collector_type=CollectionTypeChoices.TYPE_ARP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        self.assertFalse(plan.detect_only)

    def test_detect_only_true(self):
        plan = CollectionPlan.objects.create(
            name="Detect Only",
            collector_type=CollectionTypeChoices.TYPE_ARP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
            detect_only=True,
        )
        self.assertTrue(plan.detect_only)

    def test_detect_only_in_clone_fields(self):
        self.assertIn("detect_only", CollectionPlan.clone_fields)

from django.test import TestCase
from dcim.choices import DeviceStatusChoices
from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Manufacturer,
    Platform,
    Site,
)
from netaddr import EUI

from netbox_facts.choices import (
    CollectionTypeChoices,
    CollectorPriorityChoices,
    CollectorStatusChoices,
)
from netbox_facts.models import CollectionPlan, MACAddress, MACVendor


class MACAddressModelTest(TestCase):
    """Tests for the MACAddress model."""

    def test_create_mac_address(self):
        mac = MACAddress.objects.create(mac_address="AA:BB:CC:DD:EE:FF")
        self.assertIsNotNone(mac.pk)
        self.assertEqual(str(mac), "AA:BB:CC:DD:EE:FF")

    def test_mac_address_eui_conversion(self):
        """MAC address strings should be converted to EUI objects on save."""
        mac = MACAddress(mac_address="aa:bb:cc:dd:ee:ff")
        mac.save()
        self.assertIsInstance(mac.mac_address, EUI)

    def test_mac_address_unique(self):
        MACAddress.objects.create(mac_address="AA:BB:CC:DD:EE:FF")
        with self.assertRaises(Exception):
            MACAddress.objects.create(mac_address="AA:BB:CC:DD:EE:FF")

    def test_first_seen_is_created(self):
        mac = MACAddress.objects.create(mac_address="AA:BB:CC:DD:EE:01")
        self.assertEqual(mac.first_seen, mac.created)

    def test_last_seen_defaults_to_none(self):
        mac = MACAddress.objects.create(mac_address="AA:BB:CC:DD:EE:02")
        self.assertIsNone(mac.last_seen)

    def test_get_absolute_url(self):
        mac = MACAddress.objects.create(mac_address="AA:BB:CC:DD:EE:03")
        url = mac.get_absolute_url()
        self.assertIn(str(mac.pk), url)


class MACVendorModelTest(TestCase):
    """Tests for the MACVendor model."""

    def test_create_vendor(self):
        vendor = MACVendor.objects.create(
            vendor_name="Test Vendor",
            mac_prefix=EUI("AA:BB:CC:00:00:00"),
        )
        self.assertIsNotNone(vendor.pk)
        self.assertIn("Test Vendor", str(vendor))

    def test_create_vendor_with_manufacturer(self):
        manufacturer = Manufacturer.objects.create(name="TestMfg", slug="testmfg")
        vendor = MACVendor.objects.create(
            vendor_name="Test Vendor",
            mac_prefix=EUI("AA:BB:CD:00:00:00"),
            manufacturer=manufacturer,
        )
        self.assertIn("TestMfg", str(vendor))

    def test_get_by_mac_address(self):
        vendor = MACVendor.objects.create(
            vendor_name="Lookup Vendor",
            mac_prefix=EUI("11:22:33:00:00:00"),
        )
        found = MACVendor.objects.get_by_mac_address(EUI("11:22:33:44:55:66"))
        self.assertEqual(found.pk, vendor.pk)

    def test_get_by_mac_address_not_found(self):
        with self.assertRaises(MACVendor.DoesNotExist):
            MACVendor.objects.get_by_mac_address(EUI("FF:FF:FF:00:00:01"))

    def test_get_absolute_url(self):
        vendor = MACVendor.objects.create(
            vendor_name="URL Vendor",
            mac_prefix=EUI("AA:BB:CE:00:00:00"),
        )
        url = vendor.get_absolute_url()
        self.assertIn(str(vendor.pk), url)


class MACAddressVendorSignalTest(TestCase):
    """Tests for the auto-vendor-lookup signal on MACAddress."""

    def test_vendor_auto_set_on_create(self):
        """When a MACVendor exists, creating a MACAddress should auto-set vendor."""
        vendor = MACVendor.objects.create(
            vendor_name="Signal Vendor",
            mac_prefix=EUI("CC:DD:EE:00:00:00"),
        )
        mac = MACAddress.objects.create(mac_address="CC:DD:EE:11:22:33")
        mac.refresh_from_db()
        self.assertEqual(mac.vendor_id, vendor.pk)

    def test_vendor_auto_set_when_vendor_created_after(self):
        """Creating a MACVendor should update existing matching MACAddresses."""
        mac = MACAddress.objects.create(mac_address="DD:EE:FF:11:22:33")
        mac.refresh_from_db()
        # No vendor yet
        vendor = MACVendor.objects.create(
            vendor_name="Late Vendor",
            mac_prefix=EUI("DD:EE:FF:00:00:00"),
        )
        mac.refresh_from_db()
        self.assertEqual(mac.vendor_id, vendor.pk)


class CollectionPlanModelTest(TestCase):
    """Tests for the CollectionPlan model."""

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="Test Site", slug="test-site")
        cls.manufacturer = Manufacturer.objects.create(
            name="TestMfg", slug="testmfg"
        )
        cls.device_type = DeviceType.objects.create(
            manufacturer=cls.manufacturer, model="TestModel", slug="testmodel"
        )
        cls.role = DeviceRole.objects.create(name="TestRole", slug="testrole")
        cls.platform = Platform.objects.create(name="junos", slug="junos")

    def _create_plan(self, **kwargs):
        defaults = {
            "name": "Test Plan",
            "collector_type": CollectionTypeChoices.TYPE_ARP,
            "napalm_driver": "junos",
            "device_status": [DeviceStatusChoices.STATUS_ACTIVE],
        }
        defaults.update(kwargs)
        return CollectionPlan.objects.create(**defaults)

    def _create_device(self, name, status=DeviceStatusChoices.STATUS_ACTIVE, **kwargs):
        return Device.objects.create(
            name=name,
            site=self.site,
            device_type=self.device_type,
            role=self.role,
            status=status,
            **kwargs,
        )

    def test_create_plan(self):
        plan = self._create_plan()
        self.assertIsNotNone(plan.pk)
        self.assertEqual(str(plan), "Test Plan")

    def test_default_status(self):
        plan = self._create_plan()
        self.assertEqual(plan.status, CollectorStatusChoices.NEW)

    def test_ready_property(self):
        plan = self._create_plan()
        self.assertTrue(plan.ready)

    def test_not_ready_when_disabled(self):
        plan = self._create_plan(enabled=False)
        self.assertFalse(plan.ready)

    def test_not_ready_when_working(self):
        plan = self._create_plan(status=CollectorStatusChoices.WORKING)
        self.assertFalse(plan.ready)

    def test_not_ready_when_queued(self):
        plan = self._create_plan(status=CollectorStatusChoices.QUEUED)
        self.assertFalse(plan.ready)

    def test_get_devices_queryset_all(self):
        """With no filters, all devices should be returned."""
        d1 = self._create_device("dev1")
        d2 = self._create_device("dev2")
        plan = self._create_plan(device_status=[])
        qs = plan.get_devices_queryset()
        self.assertIn(d1, qs)
        self.assertIn(d2, qs)

    def test_get_devices_queryset_by_status(self):
        """device_status ArrayField filter should use __in lookup."""
        d_active = self._create_device("active-dev")
        d_planned = self._create_device(
            "planned-dev", status=DeviceStatusChoices.STATUS_PLANNED
        )
        plan = self._create_plan(
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        qs = plan.get_devices_queryset()
        self.assertIn(d_active, qs)
        self.assertNotIn(d_planned, qs)

    def test_get_devices_queryset_by_site(self):
        site2 = Site.objects.create(name="Other Site", slug="other-site")
        d1 = self._create_device("site1-dev")
        d2 = Device.objects.create(
            name="site2-dev",
            site=site2,
            device_type=self.device_type,
            role=self.role,
        )
        plan = self._create_plan(device_status=[])
        plan.sites.add(self.site)
        qs = plan.get_devices_queryset()
        self.assertIn(d1, qs)
        self.assertNotIn(d2, qs)

    def test_get_devices_queryset_by_role(self):
        role2 = DeviceRole.objects.create(name="OtherRole", slug="otherrole")
        d1 = self._create_device("role1-dev")
        d2 = Device.objects.create(
            name="role2-dev",
            site=self.site,
            device_type=self.device_type,
            role=role2,
        )
        plan = self._create_plan(device_status=[])
        plan.roles.add(self.role)
        qs = plan.get_devices_queryset()
        self.assertIn(d1, qs)
        self.assertNotIn(d2, qs)

    def test_get_napalm_args_defaults(self):
        plan = self._create_plan()
        args = plan.get_napalm_args()
        self.assertIsInstance(args, dict)

    def test_get_napalm_args_merge(self):
        plan = self._create_plan(napalm_args={"timeout": 120})
        args = plan.get_napalm_args()
        self.assertEqual(args["timeout"], 120)

    def test_clean_string_napalm_args(self):
        plan = self._create_plan()
        plan.napalm_args = "invalid"
        plan.clean()
        self.assertEqual(plan.napalm_args, {})

    def test_get_absolute_url(self):
        plan = self._create_plan()
        url = plan.get_absolute_url()
        self.assertIn(str(plan.pk), url)

from django.test import TestCase
from dcim.choices import DeviceStatusChoices
from dcim.models import Manufacturer
from netaddr import EUI

from netbox_facts.choices import (
    CollectionTypeChoices,
    CollectorPriorityChoices,
    CollectorStatusChoices,
)
from netbox_facts.filtersets import (
    CollectorFilterSet,
    MACAddressFilterSet,
    MACVendorFilterSet,
)
from netbox_facts.models import CollectionPlan, MACAddress, MACVendor


class MACAddressFilterSetTest(TestCase):
    """Tests for MACAddressFilterSet."""

    @classmethod
    def setUpTestData(cls):
        cls.mac1 = MACAddress.objects.create(
            mac_address="AA:BB:CC:DD:EE:01", description="first"
        )
        cls.mac2 = MACAddress.objects.create(
            mac_address="AA:BB:CC:DD:EE:02", description="second"
        )
        cls.mac3 = MACAddress.objects.create(
            mac_address="11:22:33:44:55:66", description="other"
        )

    def test_filter_by_mac_address(self):
        params = {"mac_address": "AA:BB:CC"}
        fs = MACAddressFilterSet(params, MACAddress.objects.all())
        self.assertEqual(fs.qs.count(), 2)

    def test_filter_by_description(self):
        params = {"description": "first"}
        fs = MACAddressFilterSet(params, MACAddress.objects.all())
        self.assertEqual(fs.qs.count(), 1)
        self.assertEqual(fs.qs.first().pk, self.mac1.pk)

    def test_search(self):
        params = {"q": "11:22:33"}
        fs = MACAddressFilterSet(params, MACAddress.objects.all())
        self.assertEqual(fs.qs.count(), 1)
        self.assertEqual(fs.qs.first().pk, self.mac3.pk)


class MACVendorFilterSetTest(TestCase):
    """Tests for MACVendorFilterSet."""

    @classmethod
    def setUpTestData(cls):
        cls.mfg = Manufacturer.objects.create(name="Cisco", slug="cisco")
        cls.vendor1 = MACVendor.objects.create(
            vendor_name="Cisco Systems",
            mac_prefix=EUI("00:1A:2B:00:00:00"),
            manufacturer=cls.mfg,
        )
        cls.vendor2 = MACVendor.objects.create(
            vendor_name="Juniper Networks",
            mac_prefix=EUI("00:2C:3D:00:00:00"),
        )

    def test_filter_by_manufacturer(self):
        params = {"manufacturer": self.mfg.pk}
        fs = MACVendorFilterSet(params, MACVendor.objects.all())
        self.assertEqual(fs.qs.count(), 1)
        self.assertEqual(fs.qs.first().pk, self.vendor1.pk)

    def test_search_by_name(self):
        params = {"q": "Juniper"}
        fs = MACVendorFilterSet(params, MACVendor.objects.all())
        self.assertEqual(fs.qs.count(), 1)
        self.assertEqual(fs.qs.first().pk, self.vendor2.pk)


class CollectorFilterSetTest(TestCase):
    """Tests for CollectorFilterSet."""

    @classmethod
    def setUpTestData(cls):
        cls.plan1 = CollectionPlan.objects.create(
            name="ARP Plan",
            collector_type=CollectionTypeChoices.TYPE_ARP,
            napalm_driver="junos",
            priority=CollectorPriorityChoices.PRIORITY_HIGH,
            status=CollectorStatusChoices.NEW,
            enabled=True,
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        cls.plan2 = CollectionPlan.objects.create(
            name="NDP Plan",
            collector_type=CollectionTypeChoices.TYPE_NDP,
            napalm_driver="junos",
            priority=CollectorPriorityChoices.PRIORITY_LOW,
            status=CollectorStatusChoices.COMPLETED,
            enabled=False,
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )

    def test_filter_by_priority(self):
        params = {"priority": [CollectorPriorityChoices.PRIORITY_HIGH]}
        fs = CollectorFilterSet(params, CollectionPlan.objects.all())
        self.assertEqual(fs.qs.count(), 1)
        self.assertEqual(fs.qs.first().pk, self.plan1.pk)

    def test_filter_by_status(self):
        params = {"status": [CollectorStatusChoices.COMPLETED]}
        fs = CollectorFilterSet(params, CollectionPlan.objects.all())
        self.assertEqual(fs.qs.count(), 1)
        self.assertEqual(fs.qs.first().pk, self.plan2.pk)

    def test_filter_by_collector_type(self):
        params = {"collector_type": [CollectionTypeChoices.TYPE_ARP]}
        fs = CollectorFilterSet(params, CollectionPlan.objects.all())
        self.assertEqual(fs.qs.count(), 1)
        self.assertEqual(fs.qs.first().pk, self.plan1.pk)

    def test_filter_by_enabled(self):
        params = {"enabled": True}
        fs = CollectorFilterSet(params, CollectionPlan.objects.all())
        self.assertEqual(fs.qs.count(), 1)
        self.assertEqual(fs.qs.first().pk, self.plan1.pk)

    def test_filter_by_name(self):
        params = {"name": "ARP Plan"}
        fs = CollectorFilterSet(params, CollectionPlan.objects.all())
        self.assertEqual(fs.qs.count(), 1)
        self.assertEqual(fs.qs.first().pk, self.plan1.pk)

    def test_search(self):
        params = {"q": "NDP"}
        fs = CollectorFilterSet(params, CollectionPlan.objects.all())
        self.assertEqual(fs.qs.count(), 1)
        self.assertEqual(fs.qs.first().pk, self.plan2.pk)

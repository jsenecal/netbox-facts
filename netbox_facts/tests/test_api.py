from dcim.choices import DeviceStatusChoices
from dcim.models import Manufacturer
from django.urls import reverse
from netaddr import EUI
from rest_framework import status
from users.models import Token
from utilities.testing import APITestCase, APIViewTestCases

from netbox_facts.choices import (
    CollectionTypeChoices,
    CollectorPriorityChoices,
)
from netbox_facts.models import CollectionPlan, MACAddress, MACVendor


class MACAddressAPITest(
    APIViewTestCases.GetObjectViewTestCase,
    APIViewTestCases.ListObjectsViewTestCase,
    APIViewTestCases.CreateObjectViewTestCase,
    APIViewTestCases.UpdateObjectViewTestCase,
    APIViewTestCases.DeleteObjectViewTestCase,
):
    model = MACAddress
    brief_fields = [
        "display",
        "id",
        "mac_address",
        "url",
    ]

    @classmethod
    def setUpTestData(cls):
        manufacturer = Manufacturer.objects.create(name="VendorMfg", slug="vendormfg")
        vendor = MACVendor.objects.create(
            vendor_name="Test Vendor",
            mac_prefix=EUI("AA:BB:CC:00:00:00"),
            manufacturer=manufacturer,
        )

        MACAddress.objects.create(mac_address="AA:BB:CC:00:00:01", vendor=vendor)
        MACAddress.objects.create(mac_address="AA:BB:CC:00:00:02", vendor=vendor)
        MACAddress.objects.create(mac_address="AA:BB:CC:00:00:03", vendor=vendor)

        cls.create_data = [
            {"mac_address": "AA:BB:CC:00:00:04", "vendor": vendor.pk},
            {"mac_address": "AA:BB:CC:00:00:05", "vendor": vendor.pk},
            {"mac_address": "AA:BB:CC:00:00:06", "vendor": vendor.pk},
        ]


class MACVendorAPITest(
    APIViewTestCases.GetObjectViewTestCase,
    APIViewTestCases.ListObjectsViewTestCase,
    APIViewTestCases.CreateObjectViewTestCase,
    APIViewTestCases.UpdateObjectViewTestCase,
    APIViewTestCases.DeleteObjectViewTestCase,
):
    model = MACVendor
    brief_fields = [
        "display",
        "id",
        "manufacturer",
        "url",
        "vendor_name",
    ]

    @classmethod
    def setUpTestData(cls):
        MACVendor.objects.create(
            vendor_name="Vendor A",
            mac_prefix=EUI("11:22:33:00:00:00"),
        )
        MACVendor.objects.create(
            vendor_name="Vendor B",
            mac_prefix=EUI("44:55:66:00:00:00"),
        )
        MACVendor.objects.create(
            vendor_name="Vendor C",
            mac_prefix=EUI("77:88:99:00:00:00"),
        )

        cls.create_data = [
            {"vendor_name": "Vendor D", "mac_prefix": "AA:00:00:00:00:00"},
            {"vendor_name": "Vendor E", "mac_prefix": "BB:00:00:00:00:00"},
            {"vendor_name": "Vendor F", "mac_prefix": "CC:00:00:00:00:00"},
        ]


class CollectionPlanAPITest(
    APIViewTestCases.GetObjectViewTestCase,
    APIViewTestCases.ListObjectsViewTestCase,
    APIViewTestCases.CreateObjectViewTestCase,
    APIViewTestCases.UpdateObjectViewTestCase,
    APIViewTestCases.DeleteObjectViewTestCase,
):
    model = CollectionPlan
    brief_fields = [
        "display",
        "id",
        "name",
        "url",
    ]

    @classmethod
    def setUpTestData(cls):
        CollectionPlan.objects.create(
            name="Plan A",
            collector_type=CollectionTypeChoices.TYPE_ARP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        CollectionPlan.objects.create(
            name="Plan B",
            collector_type=CollectionTypeChoices.TYPE_NDP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        CollectionPlan.objects.create(
            name="Plan C",
            collector_type=CollectionTypeChoices.TYPE_LLDP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )

        cls.create_data = [
            {
                "name": "Plan D",
                "collector_type": CollectionTypeChoices.TYPE_ARP,
                "napalm_driver": "junos",
                "device_status": [DeviceStatusChoices.STATUS_ACTIVE],
            },
            {
                "name": "Plan E",
                "collector_type": CollectionTypeChoices.TYPE_NDP,
                "napalm_driver": "junos",
                "device_status": [DeviceStatusChoices.STATUS_ACTIVE],
            },
            {
                "name": "Plan F",
                "collector_type": CollectionTypeChoices.TYPE_LLDP,
                "napalm_driver": "junos",
                "device_status": [DeviceStatusChoices.STATUS_ACTIVE],
            },
        ]

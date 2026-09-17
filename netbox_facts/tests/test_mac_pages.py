"""Tests for the MAC address/vendor detail-page polish (issue #162).

Covers the URL-name consolidation onto register_model_view + get_model_urls
(replacing the hand-spelled MACVendor routes -- including the nonstandard
`macvendor_detail` name -- and the duplicate changelog/journal paths that
were wired manually for both MAC models even though NetBox auto-registers
them for every NetBoxModel) and the queryset logic backing the new
MACAddress "Interfaces" tab.
"""

from dcim.choices import DeviceStatusChoices
from dcim.models import Device, DeviceRole, DeviceType, Interface, Manufacturer, Site
from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from netaddr import EUI

from netbox_facts.models import MACAddress, MACAddressInterfaceRelation, MACVendor
from netbox_facts.views import _annotate_interface_last_seen


class MacUrlNamesTest(TestCase):
    """Canonical route names must resolve; the retired nonstandard one must not."""

    @classmethod
    def setUpTestData(cls):
        cls.mac = MACAddress.objects.create(mac_address="AA:BB:CC:DD:EE:F0")
        cls.vendor = MACVendor.objects.create(
            vendor_name="Route Test Vendor",
            mac_prefix=EUI("AA:BB:CF:00:00:00"),
        )

    def test_macaddress_detail_resolves(self):
        reverse("plugins:netbox_facts:macaddress", args=[self.mac.pk])

    def test_macaddress_changelog_resolves(self):
        reverse("plugins:netbox_facts:macaddress_changelog", args=[self.mac.pk])

    def test_macaddress_journal_resolves(self):
        reverse("plugins:netbox_facts:macaddress_journal", args=[self.mac.pk])

    def test_macaddress_interfaces_tab_resolves(self):
        reverse("plugins:netbox_facts:macaddress_interfaces", args=[self.mac.pk])

    def test_macvendor_detail_resolves_under_canonical_name(self):
        reverse("plugins:netbox_facts:macvendor", args=[self.vendor.pk])

    def test_macvendor_edit_resolves(self):
        reverse("plugins:netbox_facts:macvendor_edit", args=[self.vendor.pk])

    def test_macvendor_delete_resolves(self):
        reverse("plugins:netbox_facts:macvendor_delete", args=[self.vendor.pk])

    def test_macvendor_instances_resolves(self):
        reverse("plugins:netbox_facts:macvendor_instances", args=[self.vendor.pk])

    def test_macvendor_changelog_resolves(self):
        reverse("plugins:netbox_facts:macvendor_changelog", args=[self.vendor.pk])

    def test_macvendor_journal_resolves(self):
        reverse("plugins:netbox_facts:macvendor_journal", args=[self.vendor.pk])

    def test_macvendor_detail_old_name_is_retired(self):
        with self.assertRaises(NoReverseMatch):
            reverse("plugins:netbox_facts:macvendor_detail", args=[self.vendor.pk])

    def test_macvendor_get_absolute_url_uses_canonical_name(self):
        url = self.vendor.get_absolute_url()
        self.assertEqual(url, reverse("plugins:netbox_facts:macvendor", args=[self.vendor.pk]))


class AnnotateInterfaceLastSeenTest(TestCase):
    """Queryset logic backing the MACAddress 'Interfaces' tab."""

    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="Tab Test Site", slug="tab-test-site")
        manufacturer = Manufacturer.objects.create(name="TabMfg", slug="tabmfg")
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model="TabModel", slug="tabmodel")
        role = DeviceRole.objects.create(name="TabRole", slug="tabrole")
        cls.device = Device.objects.create(
            name="tab-dev",
            site=site,
            device_type=device_type,
            role=role,
            status=DeviceStatusChoices.STATUS_ACTIVE,
        )
        cls.iface_seen = Interface.objects.create(device=cls.device, name="eth0", type="1000base-t")
        cls.iface_unseen = Interface.objects.create(device=cls.device, name="eth1", type="1000base-t")
        cls.mac = MACAddress.objects.create(mac_address="AA:BB:CC:DD:EE:F1")
        cls.mac.interfaces.add(cls.iface_seen)

    def test_annotates_last_seen_from_relation_timestamp(self):
        relation = MACAddressInterfaceRelation.objects.get(mac_address=self.mac, interface=self.iface_seen)
        queryset = _annotate_interface_last_seen(
            Interface.objects.filter(macaddressinterfacerelation__mac_address=self.mac),
            self.mac,
        )
        annotated = queryset.get(pk=self.iface_seen.pk)
        self.assertEqual(annotated.last_seen, relation.last_updated)

    def test_does_not_include_unrelated_interfaces(self):
        queryset = _annotate_interface_last_seen(
            Interface.objects.filter(macaddressinterfacerelation__mac_address=self.mac),
            self.mac,
        )
        self.assertNotIn(self.iface_unseen, list(queryset))

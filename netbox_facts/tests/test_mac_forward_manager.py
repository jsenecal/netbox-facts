"""Regression tests for the MACAddress.interfaces forward-manager shadowing (issue #192).

`dcim.Interface.mac_addresses` is a GenericRelation owned by core NetBox's own
`dcim.MACAddress` model. The plugin's `MACAddress.interfaces` M2M field also
requests `mac_addresses` as its reverse name on `dcim.Interface`. Django's
field-name lookup for `mac_addresses` keeps resolving to the core relation
that registered it first, so any read that goes through the forward manager
(`mac.interfaces.all()`, `.filter()`, and anything built on top of them)
silently joins against the wrong table and comes back empty, even though the
plugin's own through-model (`MACAddressInterfaceRelation`) holds the sighting.
Writes (`.add()`) and `Count("interfaces")` annotations are unaffected because
neither resolves the colliding `mac_addresses` name; only reads through the
forward manager's reverse-name lookup are broken.
"""

from dcim.choices import DeviceStatusChoices
from dcim.models import Device, DeviceRole, DeviceType, Interface, Manufacturer, Site
from django.db.models import Count
from django.test import TestCase

from netbox_facts.models import MACAddress
from netbox_facts.views import MACInterfacesView


class MacForwardManagerBadgeTest(TestCase):
    """The Interfaces tab badge must count sightings even though the forward manager is shadowed."""

    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="Badge Test Site", slug="badge-test-site")
        manufacturer = Manufacturer.objects.create(name="BadgeMfg", slug="badgemfg")
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model="BadgeModel", slug="badgemodel")
        role = DeviceRole.objects.create(name="BadgeRole", slug="badgerole")
        device = Device.objects.create(
            name="badge-dev",
            site=site,
            device_type=device_type,
            role=role,
            status=DeviceStatusChoices.STATUS_ACTIVE,
        )
        cls.iface_seen = Interface.objects.create(device=device, name="eth0", type="1000base-t")
        cls.mac_with_sighting = MACAddress.objects.create(mac_address="AA:BB:CC:DD:EE:F2")
        cls.mac_with_sighting.interfaces.add(cls.iface_seen)
        cls.mac_without_sighting = MACAddress.objects.create(mac_address="AA:BB:CC:DD:EE:F3")

    def test_badge_is_nonzero_for_a_mac_with_a_sighting(self):
        rendered = MACInterfacesView.tab.render(self.mac_with_sighting)
        self.assertEqual(rendered["badge"], 1)

    def test_badge_is_zero_for_a_mac_without_a_sighting(self):
        rendered = MACInterfacesView.tab.render(self.mac_without_sighting)
        self.assertEqual(rendered["badge"], 0)

    def test_occurrence_count_annotation_is_unaffected_by_the_shadowing(self):
        """`Count("interfaces")` resolves the forward field directly and was never broken.

        This pins the empirical finding from the issue's audit: unlike the
        badge (which read through the forward manager's instance descriptor),
        the list view's `occurences=Count("interfaces")` annotation is
        compiled against the field declared on MACAddress itself and never
        needs to resolve the colliding `mac_addresses` name on Interface.
        """
        annotated = MACAddress.objects.filter(pk=self.mac_with_sighting.pk).annotate(
            occurences=Count("interfaces"),
        )
        self.assertEqual(annotated.first().occurences, 1)

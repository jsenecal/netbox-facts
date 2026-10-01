"""Tests for per-device NAPALM driver resolution and collector compatibility.

Covers issue #147: the plan's napalm_driver became optional and a blank one
resolves per device from the device's Platform. Also covers both halves of
issue #83 -- clean() rejecting an explicit driver its collector has no
implementation for, and a run skipping the devices that resolve to one.
"""

from unittest.mock import MagicMock, patch

from dcim.models import Platform
from django.core.exceptions import ValidationError
from django.test import TestCase
from napalm.base.exceptions import ConnectionException, ModuleImportError
from napalm.eos import EOSDriver
from napalm.ios import IOSDriver
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from netbox_facts.api.serializers import CollectionPlanSerializer
from netbox_facts.choices import (
    CollectionTypeChoices,
    driver_supports_collector,
    normalize_driver_name,
)
from netbox_facts.helpers.collector import DeviceSkipReasons, NapalmCollector
from netbox_facts.models.collection_plan import (
    CollectionPlan,
    load_napalm_driver,
    platform_napalm_driver_name,
)
from netbox_facts.napalm.junos import EnhancedJunOSDriver
from netbox_facts.tests.test_helpers import CollectorTestMixin


class DriverPlatformsMixin:
    """Platforms whose slugs name real NAPALM drivers, plus one that does not."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.junos_platform = Platform.objects.create(name="Juniper Junos", slug="junos")
        cls.eos_platform = Platform.objects.create(name="Arista EOS", slug="eos")
        cls.ios_platform = Platform.objects.create(name="Cisco IOS", slug="ios")
        cls.unknown_platform = Platform.objects.create(name="Mystery OS", slug="mystery-os")


class PlatformDriverNameTest(DriverPlatformsMixin, TestCase):
    """Tests for the Platform -> driver-name convention (issue #147)."""

    def test_no_platform_yields_no_name(self):
        """A device without a platform resolves to no driver name."""
        self.assertEqual(platform_napalm_driver_name(None), "")

    def test_slug_is_the_fallback_name(self):
        """With no custom field set, the platform slug names the driver."""
        self.assertEqual(platform_napalm_driver_name(self.junos_platform), "junos")

    def test_custom_field_overrides_slug(self):
        """The configured Platform custom field wins over the slug."""
        self.junos_platform.custom_field_data = {"napalm_driver": "iosxr"}
        self.assertEqual(platform_napalm_driver_name(self.junos_platform), "iosxr")

    def test_blank_custom_field_falls_back_to_slug(self):
        """A custom field present but empty must not mask the slug."""
        self.junos_platform.custom_field_data = {"napalm_driver": "   "}
        self.assertEqual(platform_napalm_driver_name(self.junos_platform), "junos")

    def test_custom_field_name_is_configurable(self):
        """The setting names which Platform custom field carries the driver."""
        self.junos_platform.custom_field_data = {"driver": "nxos", "napalm_driver": "ios"}
        with self.settings(PLUGINS_CONFIG={"netbox_facts": {"platform_driver_custom_field": "driver"}}):
            self.assertEqual(platform_napalm_driver_name(self.junos_platform), "nxos")


class PlanDriverResolutionTest(DriverPlatformsMixin, CollectorTestMixin, TestCase):
    """Tests for CollectionPlan per-device driver resolution (issue #147)."""

    def test_explicit_plan_driver_overrides_platform(self):
        """A plan driver is an override: it wins over the device's platform."""
        plan = self._create_plan(napalm_driver="eos")
        device = self._create_device("res-override", platform=self.junos_platform)

        self.assertEqual(plan.get_napalm_driver_name_for(device), "eos")
        self.assertIs(plan.get_napalm_driver_for(device), EOSDriver)

    def test_blank_plan_driver_resolves_from_platform(self):
        """A blank plan driver resolves per device from its platform."""
        plan = self._create_plan(name="res-blank-eos", napalm_driver="")
        device = self._create_device("res-blank-eos-dev", platform=self.eos_platform)

        self.assertEqual(plan.get_napalm_driver_name_for(device), "eos")
        self.assertIs(plan.get_napalm_driver_for(device), EOSDriver)

    def test_resolved_name_prefers_enhanced_driver(self):
        """The plugin-local enhanced driver wins for a resolved name too."""
        plan = self._create_plan(name="res-blank-junos", napalm_driver="")
        device = self._create_device("res-blank-junos-dev", platform=self.junos_platform)

        self.assertIs(plan.get_napalm_driver_for(device), EnhancedJunOSDriver)

    def test_platformless_device_resolves_to_nothing(self):
        """A blank plan driver and no platform leaves the device undialable."""
        plan = self._create_plan(name="res-no-platform", napalm_driver="")
        device = self._create_device("res-no-platform-dev")

        self.assertEqual(plan.get_napalm_driver_name_for(device), "")
        self.assertIsNone(plan.get_napalm_driver_for(device))

    def test_plan_level_driver_is_optional(self):
        """get_napalm_driver() returns nothing when the plan defers per device."""
        plan = self._create_plan(name="res-plan-blank", napalm_driver="")
        self.assertIsNone(plan.get_napalm_driver())

    def test_plugin_module_that_is_not_a_driver_is_not_loaded_as_one(self):
        """A support module under netbox_facts.napalm must not pass as a driver.

        The enhanced-driver lookup imports netbox_facts.napalm.<name>
        before consulting napalm, so it has to reject a module that holds
        no driver class of its own rather than return whatever it finds.
        """
        with self.assertRaises(ModuleImportError):
            load_napalm_driver("helpers")


class DriverCompatibilityTableTest(TestCase):
    """Tests for the collector/driver compatibility table (issue #83)."""

    def test_unlisted_collector_accepts_any_driver(self):
        """A collector with no vendor dispatch runs against any driver."""
        self.assertTrue(driver_supports_collector(CollectionTypeChoices.TYPE_ARP, "ios"))

    def test_junos_only_collector_rejects_other_drivers(self):
        """A Junos-only collector rejects a driver it cannot dispatch to."""
        self.assertFalse(driver_supports_collector(CollectionTypeChoices.TYPE_EVPN, "ios"))

    def test_junos_only_collector_accepts_junos(self):
        self.assertTrue(driver_supports_collector(CollectionTypeChoices.TYPE_EVPN, "junos"))

    def test_enhanced_driver_path_normalizes_to_its_vendor(self):
        """The dotted plugin-local driver path stands for the same vendor."""
        self.assertEqual(normalize_driver_name("netbox_facts.napalm.junos"), "junos")
        self.assertTrue(driver_supports_collector(CollectionTypeChoices.TYPE_OSPF, "netbox_facts.napalm.junos"))

    def test_blank_driver_defers_to_run_time(self):
        """A blank driver is compatible with everything; run time decides."""
        self.assertTrue(driver_supports_collector(CollectionTypeChoices.TYPE_EVPN, ""))


class PlanCleanDriverValidationTest(TestCase):
    """Tests for the clean()-time driver check (issue #83's form-time half)."""

    def _clean(self, **kwargs):
        defaults = {
            "name": "clean-driver-plan",
            "collector_type": CollectionTypeChoices.TYPE_EVPN,
            "napalm_driver": "junos",
            "device_status": ["active"],
        }
        defaults.update(kwargs)
        CollectionPlan(**defaults).clean()

    def test_incompatible_explicit_driver_is_rejected(self):
        """An EVPN plan pinned to ios fails validation instead of a job."""
        with self.assertRaises(ValidationError) as ctx:
            self._clean(napalm_driver="ios")
        self.assertIn("napalm_driver", ctx.exception.message_dict)

    def test_compatible_explicit_driver_is_accepted(self):
        self._clean(napalm_driver="junos")

    def test_enhanced_driver_path_is_accepted(self):
        self._clean(napalm_driver="netbox_facts.napalm.junos")

    def test_blank_driver_is_accepted(self):
        """A blank driver defers the check to per-device resolution."""
        self._clean(napalm_driver="")

    def test_unrestricted_collector_accepts_any_driver(self):
        self._clean(collector_type=CollectionTypeChoices.TYPE_ARP, napalm_driver="ios")


class CollectorDriverSelectionTest(DriverPlatformsMixin, CollectorTestMixin, TestCase):
    """Tests for execute()'s per-device driver selection (issues #147, #83)."""

    def _run(self, plan, devices, open_session=None, get_ips=None):
        """Run execute() with the connect path stubbed, returning the collector.

        Only device selection is under test, so no device is ever dialed:
        the session opener is replaced, and the collector bodies with it.
        """
        collector = self._make_collector(plan)
        collector._napalm_driver = plan.get_napalm_driver()
        collector._devices = devices
        collector.arp = MagicMock()
        collector.evpn = MagicMock()
        with (
            patch(
                "netbox_facts.helpers.collector.get_connection_ips",
                side_effect=get_ips or (lambda *args, **kwargs: [("10.0.0.1", "primary")]),
            ),
            patch.object(
                NapalmCollector,
                "_open_napalm_session",
                side_effect=open_session or (lambda *args, **kwargs: MagicMock()),
            ),
        ):
            collector.execute()
        return collector

    def test_platformless_device_is_skipped_and_counted(self):
        """A device whose platform yields no driver is skipped, not dialed."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_ARP,
            name="sel-no-driver",
            napalm_driver="",
        )
        device = self._create_device("sel-no-driver-dev")

        collector = self._run(plan, [device])

        self.assertEqual(collector._skipped_devices.get(DeviceSkipReasons.NO_DRIVER), 1)
        collector.arp.assert_not_called()
        messages = [entry["message"] for entry in plan.log]
        self.assertTrue(any("no NAPALM driver can be resolved" in message for message in messages), messages)
        self.assertTrue(
            any("1 skipped (no NAPALM driver from platform: 1)" in message for message in messages),
            messages,
        )

    def test_unresolvable_driver_name_is_skipped(self):
        """A platform naming no installed driver skips the device."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_ARP,
            name="sel-unknown-driver",
            napalm_driver="",
        )
        device = self._create_device("sel-unknown-dev", platform=self.unknown_platform)

        collector = self._run(plan, [device])

        self.assertEqual(collector._skipped_devices.get(DeviceSkipReasons.UNKNOWN_DRIVER), 1)
        collector.arp.assert_not_called()

    def test_incompatible_device_is_skipped_not_fatal(self):
        """A Junos-only collector skips a non-Junos device and keeps going."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_EVPN,
            name="sel-incompatible",
            napalm_driver="",
        )
        ios_device = self._create_device("sel-ios-dev", platform=self.ios_platform)
        junos_device = self._create_device("sel-junos-dev", platform=self.junos_platform)

        collector = self._run(plan, [ios_device, junos_device])

        self.assertEqual(collector._skipped_devices.get(DeviceSkipReasons.INCOMPATIBLE_DRIVER), 1)
        self.assertEqual(collector.evpn.call_count, 1)

    def test_mixed_vendor_plan_dials_each_device_with_its_own_driver(self):
        """One blank-driver plan spans vendors, a driver class per device."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_ARP,
            name="sel-mixed",
            napalm_driver="",
        )
        junos_device = self._create_device("sel-mixed-junos", platform=self.junos_platform)
        ios_device = self._create_device("sel-mixed-ios", platform=self.ios_platform)

        used = []

        def record(driver_class, *args, **kwargs):
            used.append(driver_class)
            return MagicMock()

        collector = self._run(plan, [junos_device, ios_device], open_session=record)

        self.assertEqual(used, [EnhancedJunOSDriver, IOSDriver])
        self.assertEqual(collector.arp.call_count, 2)
        self.assertEqual(collector._skipped_devices, {})

    def test_run_summary_counts_every_kind_of_skip(self):
        """The summary tallies IP and reachability skips, not just driver ones.

        One device per reason, so the one summary line has to name all
        three rather than collapse them into a single count.
        """
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_ARP,
            name="sel-summary",
            napalm_driver="",
        )
        no_driver = self._create_device("sel-sum-nodriver")
        no_ip = self._create_device("sel-sum-noip", platform=self.junos_platform)
        unreachable = self._create_device("sel-sum-unreachable", platform=self.ios_platform)

        def ips(device, _target):
            if device.pk == no_ip.pk:
                raise ValueError("no usable IP")
            return [("10.0.0.1", "primary")]

        def refuse(*args, **kwargs):
            raise ConnectionException("refused")

        collector = self._run(
            plan,
            [no_driver, no_ip, unreachable],
            open_session=refuse,
            get_ips=ips,
        )

        self.assertEqual(
            collector._skipped_devices,
            {
                DeviceSkipReasons.NO_DRIVER: 1,
                DeviceSkipReasons.NO_IP: 1,
                DeviceSkipReasons.UNREACHABLE: 1,
            },
        )
        summary = [entry["message"] for entry in plan.log if "Run summary" in entry["message"]]
        self.assertEqual(len(summary), 1, plan.log)
        self.assertIn("0 of 3 devices collected", summary[0])
        for label in (
            DeviceSkipReasons.LABELS[DeviceSkipReasons.NO_DRIVER],
            DeviceSkipReasons.LABELS[DeviceSkipReasons.NO_IP],
            DeviceSkipReasons.LABELS[DeviceSkipReasons.UNREACHABLE],
        ):
            self.assertIn(f"{label}: 1", summary[0])

    def test_vendor_dispatch_uses_the_resolved_driver(self):
        """Vendor dispatch follows the per-device driver, not the plan's."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_EVPN,
            name="sel-dispatch",
            napalm_driver="",
        )
        collector = self._make_collector(plan)
        collector._current_driver_name = "junos"

        self.assertTrue(callable(collector._get_vendor_method("evpn")))


class SerializerDriverOptionalityTest(TestCase):
    """The REST API must accept a plan without a driver (issue #147)."""

    @staticmethod
    def _context():
        return {"request": Request(APIRequestFactory().get("/"))}

    def test_plan_creates_without_a_driver(self):
        serializer = CollectionPlanSerializer(
            data={
                "name": "api-no-driver",
                "collector_type": CollectionTypeChoices.TYPE_ARP,
                "device_status": ["active"],
            },
            context=self._context(),
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        plan = serializer.save()
        self.assertEqual(plan.napalm_driver, "")

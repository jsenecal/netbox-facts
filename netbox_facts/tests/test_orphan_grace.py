"""Tests for the stale-object grace period (issue #156).

A run that finds a plugin-owned object missing can be wrong about it: a
device briefly unreachable mid-run, a transceiver reseated between two
passes. With a grace period configured, the first absence only marks the
object orphaned; the removal is not proposed until the object has been
missing for the whole period.

Time is advanced explicitly through the collector's _now stamp, which is
the clock every sweep reads, rather than by waiting or by patching the
module's imports.
"""

import copy
import re
from datetime import timedelta
from unittest.mock import MagicMock

from dcim.models.device_components import Interface, InventoryItem, ModuleBay
from dcim.models.modules import Module, ModuleType
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.utils import timezone
from extras.models.tags import Tag
from ipam.models.ip import IPAddress, Prefix

from netbox_facts.choices import (
    CollectionTypeChoices,
    EntryActionChoices,
    EntryKindChoices,
    EntryStatusChoices,
)
from netbox_facts.constants import AUTO_D_TAG, ORPHAN_TAG_NAME, ORPHAN_TAG_SLUG
from netbox_facts.helpers.applier import (
    apply_entries,
    rediff_entries,
    skip_entries,
    unskip_entries,
)
from netbox_facts.helpers.orphans import release_orphan_candidates
from netbox_facts.models.facts_report import FactsReport, FactsReportEntry
from netbox_facts.models.mac import MACAddress
from netbox_facts.models.orphans import OrphanCandidate
from netbox_facts.tests.test_applier import ApplierTestMixin
from netbox_facts.tests.test_helpers import CollectorTestMixin

GRACE_DAYS = 3


def plugins_config(**overrides):
    """Return a copy of PLUGINS_CONFIG with the plugin settings overridden."""
    config = copy.deepcopy(settings.PLUGINS_CONFIG)
    config["netbox_facts"].update(overrides)
    return config


def orphan_tag_on(obj):
    """Return True when the visibility tag is on an object."""
    return obj.tags.filter(slug=ORPHAN_TAG_SLUG).exists()


class OrphanTagTest(TestCase):
    """The visibility tag ships with the plugin, keyed on a stable slug."""

    def test_the_tag_exists_with_its_stable_slug(self):
        """A data migration creates the tag, so no run has to invent it."""
        tag = Tag.objects.filter(slug=ORPHAN_TAG_SLUG).first()
        self.assertIsNotNone(tag)
        self.assertEqual(tag.name, ORPHAN_TAG_NAME)


class OrphanCandidateHousekeepingTest(CollectorTestMixin, TestCase):
    """A grace row has to survive what happens around it.

    Its generic key has no cascade, so the object it names can be deleted
    from under it, and the model that object belonged to can leave the
    installation with an uninstalled plugin.
    """

    def setUp(self):
        self.plan = self._create_plan()
        self.device = self._create_device("orphan-housekeeping-dev")

    def _candidate(self, content_type, object_id):
        now = timezone.now()
        return OrphanCandidate.objects.create(
            plan=self.plan,
            device=self.device,
            content_type=content_type,
            object_id=object_id,
            first_missing=now,
            last_missing=now,
        )

    def test_a_row_names_the_object_it_tracks(self):
        """The row reads as the object plus when it went missing."""
        item = InventoryItem.objects.create(device=self.device, name="FPC 7", discovered=True)
        candidate = self._candidate(ContentType.objects.get_for_model(item), item.pk)

        self.assertIn("FPC 7", str(candidate))
        self.assertIn("missing since", str(candidate))

    def test_a_row_whose_object_is_gone_still_reads(self):
        """A dangling key is printed as the key, not as a crash."""
        item = InventoryItem.objects.create(device=self.device, name="FPC 8", discovered=True)
        content_type = ContentType.objects.get_for_model(item)
        candidate = self._candidate(content_type, item.pk)
        item.delete()

        self.assertIn(str(candidate.object_id), candidate.object_repr)

    def test_settling_a_row_whose_model_has_left_the_installation(self):
        """An uninstalled plugin's content type resolves to no class at all."""
        content_type = ContentType.objects.create(app_label="departed_plugin", model="departedmodel")
        self._candidate(content_type, 1)

        release_orphan_candidates(content_type.pk, 1)

        self.assertEqual(OrphanCandidate.objects.count(), 0)


class GraceDaysResolutionTest(CollectorTestMixin, TestCase):
    """How a plan resolves the grace period it runs under."""

    def test_grace_is_off_by_default(self):
        """The shipped default keeps today's remove-on-first-absence behavior."""
        plan = self._create_plan()
        self.assertEqual(plan.get_stale_grace_days(), 0)

    def test_the_plugin_setting_sets_the_default_grace(self):
        """A plan that names no override follows the plugin setting."""
        plan = self._create_plan()
        with override_settings(PLUGINS_CONFIG=plugins_config(stale_grace_period_days=7)):
            self.assertEqual(plan.get_stale_grace_days(), 7)

    def test_a_plan_override_beats_the_plugin_setting(self):
        """The per-plan field is the plan's answer, whatever the setting says."""
        plan = self._create_plan(stale_grace_days=2)
        with override_settings(PLUGINS_CONFIG=plugins_config(stale_grace_period_days=7)):
            self.assertEqual(plan.get_stale_grace_days(), 2)

    def test_a_plan_override_of_zero_disables_grace_for_that_plan(self):
        """Zero is a decision, not a blank: a plan can opt out of a global grace."""
        plan = self._create_plan(stale_grace_days=0)
        with override_settings(PLUGINS_CONFIG=plugins_config(stale_grace_period_days=7)):
            self.assertEqual(plan.get_stale_grace_days(), 0)


class InventoryGraceMixin(CollectorTestMixin):
    """One discovered InventoryItem and a chassis that no longer reports it."""

    ITEM_NAME = "FPC 9"
    ITEM_PART = "750-99999"
    ITEM_SERIAL = "FPC9_SN"
    ITEM_DESCRIPTION = "MPC 4e 3D"
    DETECT_ONLY = False
    GRACE = None

    def setUp(self):
        self.start = timezone.now()
        self.plan = self._create_plan(
            detect_only=self.DETECT_ONLY,
            stale_grace_days=self.GRACE,
        )
        self.device = self._create_device("grace-inv-dev", serial="CHASSIS_SN")
        self.item = InventoryItem.objects.create(
            device=self.device,
            name=self.ITEM_NAME,
            part_id=self.ITEM_PART,
            serial=self.ITEM_SERIAL,
            description=self.ITEM_DESCRIPTION,
            discovered=True,
        )

    def _chassis_driver(self, modules):
        driver = MagicMock()
        driver.get_facts.return_value = {
            "serial_number": "CHASSIS_SN",
            "os_version": "21.2R3",
            "hostname": "grace-router",
            "fqdn": "",
        }
        driver.get_chassis_inventory.return_value = iter(modules)
        return driver

    def _reported_item(self):
        """The chassis payload for the item, as the device would report it."""
        return {
            "name": self.ITEM_NAME,
            "component_name": self.ITEM_NAME,
            "parent_name": None,
            "serial": self.ITEM_SERIAL,
            "part_id": self.ITEM_PART,
            "description": self.ITEM_DESCRIPTION,
        }

    def _collector_at(self, offset_days=0):
        """Build a collector whose run happens offset_days from the first."""
        collector = self._make_collector(self.plan)
        collector._current_device = self.device
        collector._report = FactsReport.objects.create(collection_plan=self.plan)
        collector._now = self.start + timedelta(days=offset_days)
        collector._log_warning = MagicMock()
        return collector

    def _run_at(self, offset_days=0, modules=()):
        """Run one inventory pass and return the collector that ran it."""
        collector = self._collector_at(offset_days)
        collector.inventory(self._chassis_driver(list(modules)))
        return collector

    def _stale_entries(self, collector):
        return collector._report.entries.filter(action=EntryActionChoices.ACTION_STALE)

    def _candidates(self):
        return OrphanCandidate.objects.filter(
            plan=self.plan,
            content_type=ContentType.objects.get_for_model(InventoryItem),
            object_id=self.item.pk,
        )


class StaleGraceDisabledTest(InventoryGraceMixin, TestCase):
    """With grace off, a sweep behaves exactly as it did before #156."""

    GRACE = None

    def test_the_object_is_removed_on_the_first_absence(self):
        """The default is no grace at all, so nothing is held back."""
        collector = self._run_at()

        self.assertFalse(InventoryItem.objects.filter(pk=self.item.pk).exists())
        self.assertEqual(self._stale_entries(collector).count(), 1)

    def test_no_grace_row_is_written(self):
        """Grace off means no tracking rows: there is nothing to track."""
        self._run_at()

        self.assertEqual(OrphanCandidate.objects.count(), 0)

    def test_the_run_summary_says_nothing_about_grace(self):
        """A run with no grace to report does not mention it."""
        collector = self._run_at()
        collector._device_count = 1
        collector._skipped_devices = {}

        collector._log_run_summary()

        summary = [entry["message"] for entry in self.plan.log if "Run summary" in entry["message"]]
        self.assertNotIn("grace", summary[0])


class StaleGraceLifecycleTest(InventoryGraceMixin, TestCase):
    """An applying run holds a missing object for the whole grace period."""

    GRACE = GRACE_DAYS

    def test_the_first_absence_marks_the_object_orphaned(self):
        """The object stays, carrying the visibility tag, and nothing is proposed."""
        collector = self._run_at()

        self.assertTrue(InventoryItem.objects.filter(pk=self.item.pk).exists())
        self.assertEqual(self._stale_entries(collector).count(), 0)
        self.assertTrue(orphan_tag_on(self.item))

        candidate = self._candidates().get()
        self.assertEqual(candidate.device, self.device)
        self.assertEqual(candidate.first_missing, self.start)
        self.assertEqual(candidate.last_missing, self.start)

    def test_a_second_absence_within_grace_only_moves_last_missing(self):
        """The clock the grace is measured against is the first absence."""
        self._run_at(0)
        collector = self._run_at(1)

        candidate = self._candidates().get()
        self.assertEqual(candidate.first_missing, self.start)
        self.assertEqual(candidate.last_missing, self.start + timedelta(days=1))
        self.assertTrue(InventoryItem.objects.filter(pk=self.item.pk).exists())
        self.assertEqual(self._stale_entries(collector).count(), 0)

    def test_absence_past_the_grace_removes_the_object_and_forgets_it(self):
        """Once the period is served the sweep proceeds exactly as before."""
        self._run_at(0)
        collector = self._run_at(GRACE_DAYS)

        self.assertFalse(InventoryItem.objects.filter(pk=self.item.pk).exists())
        self.assertEqual(self._stale_entries(collector).count(), 1)
        self.assertEqual(self._candidates().count(), 0)

    def test_reappearance_clears_the_grace_row_and_the_tag(self):
        """Hardware that comes back is not on its way out any more."""
        self._run_at(0)
        collector = self._run_at(1, modules=[self._reported_item()])

        self.assertEqual(self._candidates().count(), 0)
        self.assertFalse(orphan_tag_on(self.item))
        self.assertEqual(self._stale_entries(collector).count(), 0)

    def test_a_deleted_tag_does_not_stop_the_grace_period(self):
        """The row is what the period is measured from; the tag is advisory."""
        Tag.objects.filter(slug=ORPHAN_TAG_SLUG).delete()

        collector = self._run_at()

        self.assertEqual(self._candidates().count(), 1)
        self.assertTrue(InventoryItem.objects.filter(pk=self.item.pk).exists())
        self.assertEqual(self._stale_entries(collector).count(), 0)

    def test_the_run_summary_counts_objects_held_in_grace(self):
        """A sweep that proposed nothing has to say why it proposed nothing."""
        collector = self._run_at()
        collector._device_count = 1
        collector._skipped_devices = {}

        collector._log_run_summary()

        summary = [entry["message"] for entry in self.plan.log if "Run summary" in entry["message"]]
        self.assertEqual(len(summary), 1, self.plan.log)
        self.assertIn("1 objects in grace", summary[0])

    def test_an_object_past_grace_is_not_counted_as_held(self):
        """The count is what the run held back, not what it let through."""
        self._run_at(0)
        collector = self._run_at(GRACE_DAYS)

        self.assertEqual(collector._objects_in_grace, 0)


class StaleGraceDetectOnlyTest(InventoryGraceMixin, TestCase):
    """A detect-only run proposes the removal only once grace has run out."""

    DETECT_ONLY = True
    GRACE = GRACE_DAYS

    def test_the_first_absence_records_no_stale_entry(self):
        """Nothing reaches the review queue while the object may yet come back."""
        collector = self._run_at()

        self.assertEqual(self._stale_entries(collector).count(), 0)
        self.assertEqual(self._candidates().count(), 1)
        self.assertTrue(orphan_tag_on(self.item))

    def test_absence_past_the_grace_records_the_stale_entry(self):
        """The entry a reviewer acts on is the one the sweep always produced."""
        self._run_at(0)
        collector = self._run_at(GRACE_DAYS)

        entry = self._stale_entries(collector).get()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_PENDING)
        self.assertEqual(entry.object_id, self.item.pk)
        # The object is still there, so the row has to outlive the proposal:
        # the applier is what finally acts on the absence.
        self.assertTrue(InventoryItem.objects.filter(pk=self.item.pk).exists())
        self.assertEqual(self._candidates().count(), 1)


class StaleGraceModuleSweepTest(CollectorTestMixin, TestCase):
    """The stale-module sweep is gated on the same grace period."""

    def setUp(self):
        self.start = timezone.now()
        self.plan = self._create_plan(stale_grace_days=GRACE_DAYS)
        self.device = self._create_device("grace-mod-dev", serial="CHASSIS_SN")
        self.bay = ModuleBay.objects.create(device=self.device, name="FPC 0")
        module_type = ModuleType.objects.create(
            manufacturer=self.manufacturer,
            model="MOD-750-11111",
            part_number="750-11111",
        )
        self.module = Module.objects.create(
            device=self.device,
            module_bay=self.bay,
            module_type=module_type,
            serial="FPC0_SN",
        )
        self.module.tags.add(AUTO_D_TAG)

    def _run_at(self, offset_days=0):
        collector = self._make_collector(self.plan)
        collector._current_device = self.device
        collector._report = FactsReport.objects.create(collection_plan=self.plan)
        collector._now = self.start + timedelta(days=offset_days)
        collector._log_warning = MagicMock()
        driver = MagicMock()
        driver.get_facts.return_value = {
            "serial_number": "CHASSIS_SN",
            "os_version": "21.2R3",
            "hostname": "grace-router",
            "fqdn": "",
        }
        driver.get_chassis_inventory.return_value = iter([])
        collector.inventory(driver)
        return collector

    def test_the_first_absence_holds_the_module(self):
        """A module missing for the first time is marked, not deleted."""
        collector = self._run_at()

        self.assertTrue(Module.objects.filter(pk=self.module.pk).exists())
        self.assertEqual(
            collector._report.entries.filter(
                action=EntryActionChoices.ACTION_STALE,
                entry_kind=EntryKindChoices.KIND_MODULE,
            ).count(),
            0,
        )
        self.assertTrue(orphan_tag_on(self.module))
        self.assertEqual(
            OrphanCandidate.objects.filter(
                content_type=ContentType.objects.get_for_model(Module),
                object_id=self.module.pk,
            ).count(),
            1,
        )

    def test_absence_past_the_grace_deletes_the_module(self):
        """The deletion still happens, a grace period later."""
        self._run_at(0)
        collector = self._run_at(GRACE_DAYS)

        self.assertFalse(Module.objects.filter(pk=self.module.pk).exists())
        self.assertEqual(
            collector._report.entries.filter(
                action=EntryActionChoices.ACTION_STALE,
                entry_kind=EntryKindChoices.KIND_MODULE,
            ).count(),
            1,
        )
        self.assertEqual(OrphanCandidate.objects.count(), 0)


class StaleGraceInterfaceSweepTest(CollectorTestMixin, TestCase):
    """The interfaces collector's stale-IP sweep is gated too.

    Its removal is an unassignment rather than a delete, so the object
    outlives it and the visibility tag has to be taken back off.
    """

    def setUp(self):
        self.start = timezone.now()
        self.plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            stale_grace_days=GRACE_DAYS,
        )
        self.device = self._create_device("grace-iface-dev")
        Interface.objects.create(device=self.device, name="ge-0/0/9", type="1000base-t")
        self.logical = Interface.objects.create(device=self.device, name="ge-0/0/9.0", type="virtual")
        self.ip = IPAddress.objects.create(address="10.0.90.1/24", assigned_object=self.logical)
        self.ip.tags.add(AUTO_D_TAG)

    def _sweep_at(self, offset_days=0, seen=()):
        collector = self._make_collector(self.plan)
        collector._interfaces_re = re.compile(r".*")
        collector._current_device = self.device
        collector._report = FactsReport.objects.create(collection_plan=self.plan)
        collector._now = self.start + timedelta(days=offset_days)
        collector._seen_ips = set(seen)
        collector._skipped_ip_ifaces = set()
        collector._detect_stale_ips(self.device)
        return collector

    def test_the_first_absence_keeps_the_assignment(self):
        """An address missing once keeps its interface and gains the tag."""
        collector = self._sweep_at()

        self.ip.refresh_from_db()
        self.assertEqual(self.ip.assigned_object, self.logical)
        self.assertTrue(orphan_tag_on(self.ip))
        self.assertEqual(collector._report.entries.count(), 0)

    def test_absence_past_the_grace_unassigns_and_forgets(self):
        """The unassignment happens a grace period later, and the mark goes."""
        self._sweep_at(0)
        collector = self._sweep_at(GRACE_DAYS)

        self.ip.refresh_from_db()
        self.assertIsNone(self.ip.assigned_object)
        self.assertFalse(orphan_tag_on(self.ip))
        self.assertEqual(collector._report.entries.count(), 1)
        self.assertEqual(OrphanCandidate.objects.count(), 0)

    def test_reappearance_clears_the_grace_row_and_the_tag(self):
        """An address the run sees again is no longer on its way out."""
        self._sweep_at(0)
        self._sweep_at(1, seen=[(str(self.ip.address), None)])

        self.ip.refresh_from_db()
        self.assertEqual(self.ip.assigned_object, self.logical)
        self.assertFalse(orphan_tag_on(self.ip))
        self.assertEqual(OrphanCandidate.objects.count(), 0)


class StaleGraceArpSweepTest(CollectorTestMixin, TestCase):
    """The ARP/NDP collector's stale-IP sweep is gated on the same period."""

    def setUp(self):
        self.start = timezone.now()
        self.plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_ARP,
            stale_grace_days=GRACE_DAYS,
            detect_only=True,
        )
        self.device = self._create_device("grace-arp-dev")
        self.interface = Interface.objects.create(device=self.device, name="Ethernet9", type="1000base-t")
        # Without a covering prefix the reported neighbor is never resolved
        # into an address, the run's seen set stays empty, and the sweep
        # that this test is about does not run at all.
        Prefix.objects.create(prefix="10.9.0.0/24")
        mac = MACAddress.objects.create(mac_address="AA:BB:CC:DD:EE:90")
        mac.interfaces.add(self.interface)
        self.ip = IPAddress.objects.create(address="10.9.0.99/24")
        self.ip.tags.add(AUTO_D_TAG)
        mac.ip_addresses.add(self.ip)

    def _run_at(self, offset_days=0):
        collector = self._make_collector(self.plan)
        collector._interfaces_re = re.compile(r".*")
        collector._current_device = self.device
        collector._report = FactsReport.objects.create(collection_plan=self.plan)
        collector._now = self.start + timedelta(days=offset_days)
        driver = MagicMock()
        driver.get_arp_table.return_value = [
            {
                "interface": "Ethernet9",
                "mac": "AA:BB:CC:DD:EE:91",
                "ip": "10.9.0.50",
                "age": 3.0,
            },
        ]
        driver.get_interfaces_ip.return_value = {
            "Ethernet9": {"ipv4": {"10.9.0.1": {"prefix_length": 24}}},
        }
        driver.get_network_instances.return_value = {
            "default": {
                "name": "default",
                "type": "DEFAULT_INSTANCE",
                "state": {"route_distinguisher": ""},
                "interfaces": {"interface": {"Ethernet9": {}}},
            },
        }
        collector.arp(driver)
        return collector

    def test_the_first_absence_records_no_stale_entry(self):
        """A neighbor that dropped off one table gets a grace period too."""
        collector = self._run_at()

        self.assertEqual(
            collector._report.entries.filter(action=EntryActionChoices.ACTION_STALE).count(),
            0,
        )
        self.assertTrue(orphan_tag_on(self.ip))
        self.assertEqual(OrphanCandidate.objects.count(), 1)

    def test_absence_past_the_grace_records_the_stale_entry(self):
        """The entry the sweep always produced is produced, a grace later."""
        self._run_at(0)
        collector = self._run_at(GRACE_DAYS)

        self.assertEqual(
            collector._report.entries.filter(action=EntryActionChoices.ACTION_STALE).count(),
            1,
        )


class StaleGraceApplyTest(ApplierTestMixin, TestCase):
    """Applying a stale entry is what finally settles an object's absence."""

    def _candidate_for(self, obj):
        return OrphanCandidate.objects.create(
            plan=self.plan,
            device=self.device,
            content_type=ContentType.objects.get_for_model(obj),
            object_id=obj.pk,
            first_missing=timezone.now(),
            last_missing=timezone.now(),
        )

    def _stale_ip_entry(self):
        """A stale interfaces entry for an orphaned, still-assigned address."""
        logical = Interface.objects.create(device=self.device, name="ge-0/0/11.0", type="virtual")
        ip = IPAddress.objects.create(address="10.0.11.1/24", assigned_object=logical)
        ip.tags.add(AUTO_D_TAG)
        ip.tags.add(Tag.objects.get(slug=ORPHAN_TAG_SLUG))
        self._candidate_for(ip)

        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_STALE,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            entry_kind=EntryKindChoices.KIND_IP_ADDRESS,
            device=self.device,
            object_type=ContentType.objects.get_for_model(ip),
            object_id=ip.pk,
            object_repr=f"IPAddress 10.0.11.1/24 on {logical}",
            detected_values={},
            current_values={
                "ip_address": "10.0.11.1/24",
                "vrf": None,
                "assigned_object": str(logical),
            },
        )
        return report, entry, ip

    def test_applying_a_stale_entry_clears_the_grace_row_and_the_tag(self):
        """The absence has been acted on, so nothing is pending on it."""
        report, entry, ip = self._stale_ip_entry()

        applied, failed = apply_entries(report, [entry.pk])

        self.assertEqual((applied, failed), (1, 0))
        ip.refresh_from_db()
        self.assertIsNone(ip.assigned_object)
        self.assertFalse(orphan_tag_on(ip))
        self.assertEqual(OrphanCandidate.objects.count(), 0)

    def test_applying_a_stale_entry_clears_the_row_of_a_deleted_object(self):
        """A deleted object cannot cascade a generic key, so the row goes here."""
        item = InventoryItem.objects.create(
            device=self.device,
            name="FPC 11",
            discovered=True,
        )
        self._candidate_for(item)

        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_STALE,
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            entry_kind=EntryKindChoices.KIND_INVENTORY_ITEM,
            device=self.device,
            object_type=ContentType.objects.get_for_model(item),
            object_id=item.pk,
            object_repr="InventoryItem FPC 11",
            detected_values={},
            current_values={"name": "FPC 11"},
        )

        applied, failed = apply_entries(report, [entry.pk])

        self.assertEqual((applied, failed), (1, 0))
        self.assertFalse(InventoryItem.objects.filter(pk=item.pk).exists())
        self.assertEqual(OrphanCandidate.objects.count(), 0)

    def test_a_rediff_that_finds_the_object_gone_clears_the_grace_row(self):
        """An object removed by hand settles its own absence."""
        item = InventoryItem.objects.create(
            device=self.device,
            name="FPC 12",
            discovered=True,
        )
        self._candidate_for(item)

        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_STALE,
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            entry_kind=EntryKindChoices.KIND_INVENTORY_ITEM,
            device=self.device,
            object_type=ContentType.objects.get_for_model(item),
            object_id=item.pk,
            object_repr="InventoryItem FPC 12",
            detected_values={},
            current_values={"name": "FPC 12"},
        )
        item.delete()

        resolved, refreshed, unsupported = rediff_entries(report, [entry.pk])

        self.assertEqual((resolved, refreshed, unsupported), (1, 0, 0))
        self.assertEqual(OrphanCandidate.objects.count(), 0)

    def test_deciding_not_to_apply_leaves_the_grace_row_alone(self):
        """A skip is not a verdict on the object, so its clock keeps running."""
        report, entry, ip = self._stale_ip_entry()

        self.assertEqual(skip_entries(report, [entry.pk]), 1)
        self.assertEqual(OrphanCandidate.objects.count(), 1)

        self.assertEqual(unskip_entries(report, [entry.pk]), 1)
        self.assertEqual(OrphanCandidate.objects.count(), 1)
        ip.refresh_from_db()
        self.assertIsNotNone(ip.assigned_object)
        self.assertTrue(orphan_tag_on(ip))

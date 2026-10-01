"""Tests for the rediff action and the skip-memory suppression added in #142.

A pending entry used to be reviewed against the NetBox state of the moment
it was detected, and a skipped one came back identically on every later
run. These tests cover the content hash that gives a proposed change an
identity, the suppression that hash buys at detect time, and the rediff
transition that re-reads the NetBox side of an entry without touching the
device.
"""

from dcim.models.device_components import Interface, InventoryItem, ModuleBay
from dcim.models.modules import Module, ModuleType
from django.contrib.messages import get_messages
from django.test import SimpleTestCase
from django.test import TestCase as DjangoTestCase
from django.urls import reverse
from ipam.models.ip import IPAddress
from ipam.models.vrfs import VRF
from rest_framework import status as http_status
from utilities.testing import APITestCase, TestCase

from netbox_facts.api.views import FactsMutationThrottle, FactsReportViewSet
from netbox_facts.choices import (
    CollectionTypeChoices,
    EntryActionChoices,
    EntryKindChoices,
    EntryStatusChoices,
    ReportStatusChoices,
)
from netbox_facts.entry_actions import entry_actions_for_status
from netbox_facts.helpers.applier import rediff_entries
from netbox_facts.helpers.change_hash import canonical_change_values, compute_change_hash
from netbox_facts.models import FactsReport, FactsReportEntry
from netbox_facts.models.mac import MACAddress
from netbox_facts.tests.test_applier import ApplierTestMixin
from netbox_facts.tests.test_helpers import CollectorTestMixin

SERIAL = "DETECTED_SERIAL"

#: The payload one detect run would record for a device serial change.
DEVICE_PAYLOAD = {"serial_number": SERIAL, "os_version": "21.2R3"}


def device_repr(device):
    """The label the inventory collector gives a device entry."""
    return f"Device {device.name}"


class ChangeHashTest(SimpleTestCase):
    """#142: the content hash identifies a proposed change, not a run."""

    def hash_for(self, values, kind=EntryKindChoices.KIND_DEVICE, object_repr="Device dev1"):
        return compute_change_hash(kind, object_repr, values)

    def test_key_order_does_not_change_the_hash(self):
        """Two runs that found the same thing hash the same, however ordered."""
        first = self.hash_for({"serial_number": SERIAL, "os_version": "21.2R3"})
        second = self.hash_for({"os_version": "21.2R3", "serial_number": SERIAL})

        self.assertEqual(first, second)

    def test_volatile_keys_are_excluded_from_the_hash(self):
        """State the device reports differently every run is not identity."""
        quiet = self.hash_for({"interface": "ge-0/0/0", "is_up": False, "speed": 1000, "raw_output": "a"})
        busy = self.hash_for({"interface": "ge-0/0/0", "is_up": True, "speed": 10000, "raw_output": "b"})

        self.assertEqual(quiet, busy)

    def test_a_changed_value_changes_the_hash(self):
        """A different proposal must get a different identity."""
        before = self.hash_for(DEVICE_PAYLOAD)
        after = self.hash_for({**DEVICE_PAYLOAD, "serial_number": "OTHER"})

        self.assertNotEqual(before, after)

    def test_the_kind_and_the_label_take_part_in_the_hash(self):
        """The same payload about another subject is another change."""
        baseline = self.hash_for(DEVICE_PAYLOAD)

        self.assertNotEqual(baseline, self.hash_for(DEVICE_PAYLOAD, kind=EntryKindChoices.KIND_INTERFACE))
        self.assertNotEqual(baseline, self.hash_for(DEVICE_PAYLOAD, object_repr="Device dev2"))

    def test_canonical_values_drop_only_the_volatile_keys(self):
        """The canonical payload keeps everything that changes what apply does."""
        canonical = canonical_change_values(
            {"mac_address": "AA:BB:CC:DD:EE:01", "interface": "ge-0/0/0", "is_up": True, "raw_output": "x"}
        )

        self.assertEqual(canonical, {"mac_address": "AA:BB:CC:DD:EE:01", "interface": "ge-0/0/0"})


class SkipMemoryTest(CollectorTestMixin, DjangoTestCase):
    """#142: a skipped change is not recorded again until its payload moves."""

    def setUp(self):
        self.plan = self._create_plan(name="skip-memory-plan", detect_only=True)
        self.device = self._create_device("skip-memory-dev")
        self.report = FactsReport.objects.create(collection_plan=self.plan)
        self.collector = self._make_collector(self.plan)
        self.collector._current_device = self.device
        self.collector._report = self.report

    def record(self, detected=None, device=None, kind=EntryKindChoices.KIND_DEVICE):
        """Record one entry the way the inventory collector records it."""
        device = device or self.device
        return self.collector._record_entry(
            action=EntryActionChoices.ACTION_CHANGED,
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            device=device,
            detected_values=detected if detected is not None else dict(DEVICE_PAYLOAD),
            entry_kind=kind,
            current_values={"serial_number": device.serial},
            object_repr=device_repr(device),
        )

    def remember(self, status=EntryStatusChoices.STATUS_SKIPPED, detected=None, device=None, report=None, **kwargs):
        """Store a decided entry a later run could be suppressed by."""
        device = device or self.device
        values = detected if detected is not None else dict(DEVICE_PAYLOAD)
        object_repr = device_repr(device)
        defaults = {
            "change_hash": compute_change_hash(EntryKindChoices.KIND_DEVICE, object_repr, values),
        }
        defaults.update(kwargs)
        return FactsReportEntry.objects.create(
            report=report or FactsReport.objects.create(collection_plan=self.plan),
            device=device,
            status=status,
            action=EntryActionChoices.ACTION_CHANGED,
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            entry_kind=EntryKindChoices.KIND_DEVICE,
            object_repr=object_repr,
            detected_values=values,
            **defaults,
        )

    def test_a_recorded_entry_carries_the_hash_of_its_change(self):
        """The identity is stored at detect time, not derived later."""
        entry = self.record()

        self.assertEqual(
            entry.change_hash,
            compute_change_hash(EntryKindChoices.KIND_DEVICE, device_repr(self.device), DEVICE_PAYLOAD),
        )

    def test_a_previously_skipped_change_is_not_recorded_again(self):
        """The reviewer's decision outlives the report it was made on."""
        self.remember()

        entry = self.record()

        self.assertIsNone(entry)
        self.assertFalse(self.report.entries.exists())
        self.assertEqual(self.collector._suppressed_changes, 1)

    def test_every_suppressed_change_is_counted(self):
        """The counter is what makes the memory visible in the run summary."""
        self.remember()
        other = self._create_device("skip-memory-dev2")
        self.remember(device=other)

        self.record()
        self.record(device=other)

        self.assertEqual(self.collector._suppressed_changes, 2)

    def test_a_changed_payload_resurfaces_a_skipped_change(self):
        """Suppression lasts only while the device keeps reporting the same thing."""
        self.remember()

        entry = self.record(detected={**DEVICE_PAYLOAD, "serial_number": "MOVED"})

        self.assertIsNotNone(entry)
        self.assertEqual(self.collector._suppressed_changes, 0)

    def test_volatile_movement_alone_does_not_resurface_a_skipped_change(self):
        """Link state flapping is not a reason to ask the reviewer again."""
        self.remember(detected={"interface": "ge-0/0/0", "is_up": True})

        entry = self.record(detected={"interface": "ge-0/0/0", "is_up": False})

        self.assertIsNone(entry)

    def test_applied_and_failed_history_never_suppresses(self):
        """Only a skip is a decision to stop being told; the rest are history."""
        for status in (
            EntryStatusChoices.STATUS_APPLIED,
            EntryStatusChoices.STATUS_FAILED,
            EntryStatusChoices.STATUS_PENDING,
        ):
            with self.subTest(status=status):
                self.remember(status=status)

                self.assertIsNotNone(self.record())

    def test_an_entry_recorded_before_the_hash_existed_never_suppresses(self):
        """A legacy row has no identity, so it can speak for no later change."""
        self.remember(change_hash="")

        self.assertIsNotNone(self.record())

    def test_skip_memory_is_scoped_to_the_plan(self):
        """Another plan's review decisions are not this plan's memory."""
        other_plan = self._create_plan(name="skip-memory-other-plan", detect_only=True)
        self.remember(report=FactsReport.objects.create(collection_plan=other_plan))

        self.assertIsNotNone(self.record())

    def test_skip_memory_is_scoped_to_the_device(self):
        """The same change on another device is another change."""
        self.remember(device=self._create_device("skip-memory-dev3"))

        self.assertIsNotNone(self.record())

    def test_an_applying_run_records_what_it_applied(self):
        """A run that writes to NetBox keeps its audit trail, skip or no skip."""
        self.collector._detect_only = False
        self.remember()

        entry = self.record()

        self.assertIsNotNone(entry)
        self.assertEqual(self.collector._suppressed_changes, 0)

    def test_the_run_summary_names_the_suppressed_count(self):
        """The memory is only trustworthy if the run says it is working."""
        self.collector._device_count = 2
        self.collector._skipped_devices = {}
        other = self._create_device("skip-memory-dev4")
        self.remember()
        self.remember(device=other)
        self.record()
        self.record(device=other)

        self.collector._log_run_summary()

        summary = [entry["message"] for entry in self.plan.log if "Run summary" in entry["message"]]
        self.assertEqual(len(summary), 1, self.plan.log)
        self.assertIn("suppressed 2 previously skipped changes", summary[0])

    def test_the_run_summary_stays_quiet_when_nothing_was_suppressed(self):
        """A run with nothing to say about the memory does not mention it."""
        self.collector._device_count = 1
        self.collector._skipped_devices = {}

        self.collector._log_run_summary()

        summary = [entry["message"] for entry in self.plan.log if "Run summary" in entry["message"]]
        self.assertNotIn("suppressed", summary[0])


class RediffTestMixin(ApplierTestMixin):
    """Entry factories for the rediff transition."""

    def setUp(self):
        super().setUp()
        self.report = FactsReport.objects.create(collection_plan=self.plan)

    def make_entry(self, report=None, **kwargs):
        """Create one pending entry, defaulting to a device serial change."""
        values = {
            "action": EntryActionChoices.ACTION_CHANGED,
            "status": EntryStatusChoices.STATUS_PENDING,
            "collector_type": CollectionTypeChoices.TYPE_INVENTORY,
            "entry_kind": EntryKindChoices.KIND_DEVICE,
            "object_repr": device_repr(self.device),
            "detected_values": dict(DEVICE_PAYLOAD),
            "current_values": {"serial_number": "AT_DETECT_TIME"},
        }
        values.update(kwargs)
        return FactsReportEntry.objects.create(report=report or self.report, device=self.device, **values)


class RediffEntriesTest(RediffTestMixin, DjangoTestCase):
    """#142: rediff re-reads the NetBox side of an entry, with no device."""

    def test_rediff_refreshes_the_netbox_side_of_the_comparison(self):
        """An entry reviewed later is reviewed against NetBox as it is now."""
        entry = self.make_entry()
        self.device.serial = "MEANWHILE"
        self.device.save()

        counts = rediff_entries(self.report, [entry.pk])

        self.assertEqual(counts, (0, 1, 0))
        entry.refresh_from_db()
        self.assertEqual(entry.current_values, {"serial_number": "MEANWHILE"})
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_PENDING)

    def test_rediff_resolves_an_entry_netbox_already_satisfies(self):
        """A change someone else made is not a change left to apply."""
        entry = self.make_entry()
        self.device.serial = SERIAL
        self.device.save()

        counts = rediff_entries(self.report, [entry.pk])

        self.assertEqual(counts, (1, 0, 0))
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_APPLIED)
        self.assertIsNotNone(entry.applied_at)
        self.assertEqual(entry.current_values, {"serial_number": SERIAL})

    def test_rediff_clears_a_resolved_entrys_recorded_failure(self):
        """An entry resolved by rediff carries no leftover apply error."""
        entry = self.make_entry(error_message="boom", apply_error={"error_type": "error", "__all__": ["boom"]})
        self.device.serial = SERIAL
        self.device.save()

        rediff_entries(self.report, [entry.pk])

        entry.refresh_from_db()
        self.assertEqual(entry.error_message, "")
        self.assertIsNone(entry.apply_error)

    def test_rediff_points_a_resolved_entry_at_the_object_it_found(self):
        """A resolved entry links to the NetBox object that satisfies it."""
        interface = Interface.objects.create(device=self.device, name="ge-0/0/0", type="1000base-t")
        lag = Interface.objects.create(device=self.device, name="ae0", type="lag")
        interface.lag = lag
        interface.save()
        entry = self.make_entry(
            entry_kind=EntryKindChoices.KIND_LAG,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            object_repr="LAG ge-0/0/0 -> ae0",
            detected_values={"interface": "ge-0/0/0", "lag_parent": "ae0"},
            current_values={"lag_parent": None},
        )

        counts = rediff_entries(self.report, [entry.pk])

        self.assertEqual(counts, (1, 0, 0))
        entry.refresh_from_db()
        self.assertEqual(entry.object, interface)

    def test_rediff_refreshes_a_lag_entry_that_still_drifts(self):
        """A membership NetBox still lacks stays pending with a fresh snapshot."""
        Interface.objects.create(device=self.device, name="ge-0/0/1", type="1000base-t")
        entry = self.make_entry(
            entry_kind=EntryKindChoices.KIND_LAG,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            object_repr="LAG ge-0/0/1 -> ae1",
            detected_values={"interface": "ge-0/0/1", "lag_parent": "ae1"},
            current_values={"lag_parent": "ae9"},
        )

        counts = rediff_entries(self.report, [entry.pk])

        self.assertEqual(counts, (0, 1, 0))
        entry.refresh_from_db()
        self.assertEqual(entry.current_values, {"lag_parent": None})

    def test_rediff_resolves_a_stale_entry_whose_object_is_gone(self):
        """Nothing is left to remove, so nothing is left to review."""
        entry = self.make_entry(
            action=EntryActionChoices.ACTION_STALE,
            entry_kind=EntryKindChoices.KIND_INVENTORY_ITEM,
            object_repr="InventoryItem FPC 9",
            detected_values={},
            current_values={"name": "FPC 9", "serial": "S9", "part_id": "P9", "description": ""},
        )

        counts = rediff_entries(self.report, [entry.pk])

        self.assertEqual(counts, (1, 0, 0))
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_APPLIED)
        self.assertEqual(entry.current_values, {})

    def test_rediff_keeps_a_stale_entry_whose_object_is_still_there(self):
        """Hardware NetBox still lists is still a decision to make."""
        InventoryItem.objects.create(device=self.device, name="FPC 8", serial="S8", part_id="P8", discovered=True)
        entry = self.make_entry(
            action=EntryActionChoices.ACTION_STALE,
            entry_kind=EntryKindChoices.KIND_INVENTORY_ITEM,
            object_repr="InventoryItem FPC 8",
            detected_values={},
            current_values={"name": "FPC 8", "serial": "OLD", "part_id": "P8", "description": ""},
        )

        counts = rediff_entries(self.report, [entry.pk])

        self.assertEqual(counts, (0, 1, 0))
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_PENDING)
        self.assertEqual(entry.current_values["serial"], "S8")

    def test_rediff_resolves_an_inventory_item_that_now_matches(self):
        """An item someone corrected by hand needs no apply."""
        InventoryItem.objects.create(
            device=self.device,
            name="FPC 7",
            serial="S7",
            part_id="P7",
            description="seven",
        )
        entry = self.make_entry(
            entry_kind=EntryKindChoices.KIND_INVENTORY_ITEM,
            object_repr="InventoryItem FPC 7",
            detected_values={"name": "FPC 7", "serial": "S7", "part_id": "P7", "description": "seven"},
            current_values={"serial": "OLD", "part_id": "P7", "description": "seven"},
        )

        counts = rediff_entries(self.report, [entry.pk])

        self.assertEqual(counts, (1, 0, 0))

    def test_rediff_resolves_a_module_entry_the_bay_now_satisfies(self):
        """A module installed meanwhile would make the apply a duplicate."""
        bay = ModuleBay.objects.create(device=self.device, name="FPC 0")
        module_type = ModuleType.objects.create(
            manufacturer=self.manufacturer,
            model="MPC-REDIFF",
            part_number="750-00001",
        )
        Module.objects.create(device=self.device, module_bay=bay, module_type=module_type, serial="MOD_SN")
        entry = self.make_entry(
            action=EntryActionChoices.ACTION_NEW,
            entry_kind=EntryKindChoices.KIND_MODULE,
            object_repr="Module FPC 0",
            detected_values={
                "name": "FPC 0",
                "serial": "MOD_SN",
                "part_id": "750-00001",
                "module_bay_id": bay.pk,
                "module_type_id": module_type.pk,
            },
            current_values={},
        )

        counts = rediff_entries(self.report, [entry.pk])

        self.assertEqual(counts, (1, 0, 0))
        entry.refresh_from_db()
        self.assertEqual(entry.current_values["serial"], "MOD_SN")

    def test_rediff_resolves_an_interface_someone_created(self):
        """A detect-only interface entry is satisfied by the interface existing."""
        Interface.objects.create(device=self.device, name="ge-0/0/2", type="1000base-t")
        entry = self.make_entry(
            action=EntryActionChoices.ACTION_NEW,
            entry_kind=EntryKindChoices.KIND_INTERFACE,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            object_repr="Interface ge-0/0/2",
            detected_values={"interface": "ge-0/0/2", "mtu": 9000},
            current_values={},
        )

        counts = rediff_entries(self.report, [entry.pk])

        self.assertEqual(counts, (1, 0, 0))

    def test_rediff_keeps_an_interface_entry_whose_mac_is_still_missing(self):
        """A detect-only interface entry also carries the MAC its apply claims."""
        Interface.objects.create(device=self.device, name="ge-0/0/7", type="1000base-t")
        entry = self.make_entry(
            action=EntryActionChoices.ACTION_NEW,
            entry_kind=EntryKindChoices.KIND_INTERFACE,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            object_repr="Interface ge-0/0/7",
            detected_values={"interface": "ge-0/0/7", "mac_address": "AA:BB:CC:DD:EE:07"},
            current_values={},
        )

        counts = rediff_entries(self.report, [entry.pk])

        self.assertEqual(counts, (0, 1, 0))
        entry.refresh_from_db()
        self.assertEqual(entry.current_values, {"interface": "ge-0/0/7", "mac_address": None})

    def test_rediff_resolves_an_interface_mac_already_claimed(self):
        """The MAC entry is satisfied once the interface holds that MAC."""
        interface = Interface.objects.create(device=self.device, name="ge-0/0/3", type="1000base-t")
        entry = self.make_entry(
            action=EntryActionChoices.ACTION_NEW,
            entry_kind=EntryKindChoices.KIND_INTERFACE_MAC,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            object_repr="Interface ge-0/0/3 MAC AA:BB:CC:DD:EE:03",
            detected_values={"interface": "ge-0/0/3", "mac_address": "AA:BB:CC:DD:EE:03"},
            current_values={},
        )

        self.assertEqual(rediff_entries(self.report, [entry.pk]), (0, 1, 0))

        MACAddress.objects.create(mac_address="AA:BB:CC:DD:EE:03", device_interface=interface)

        self.assertEqual(rediff_entries(self.report, [entry.pk]), (1, 0, 0))

    def test_rediff_resolves_a_neighbor_mac_seen_on_the_interface(self):
        """An ARP MAC entry is satisfied by the MAC-to-interface link."""
        interface = Interface.objects.create(device=self.device, name="ge-0/0/4", type="1000base-t")
        mac = MACAddress.objects.create(mac_address="AA:BB:CC:DD:EE:04")
        mac.interfaces.add(interface)
        entry = self.make_entry(
            action=EntryActionChoices.ACTION_NEW,
            entry_kind=EntryKindChoices.KIND_MAC_ADDRESS,
            collector_type=CollectionTypeChoices.TYPE_ARP,
            object_repr="MACAddress AA:BB:CC:DD:EE:04",
            detected_values={"mac": "AA:BB:CC:DD:EE:04", "interface": "ge-0/0/4", "ip": "10.9.0.4/24"},
            current_values={},
        )

        self.assertEqual(rediff_entries(self.report, [entry.pk]), (1, 0, 0))

    def test_rediff_resolves_an_ip_assigned_meanwhile(self):
        """An address someone assigned by hand needs no apply."""
        interface = Interface.objects.create(device=self.device, name="ge-0/0/5", type="1000base-t")
        address = IPAddress.objects.create(address="10.9.0.5/24")
        entry = self.make_entry(
            action=EntryActionChoices.ACTION_NEW,
            entry_kind=EntryKindChoices.KIND_IP_ADDRESS,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            object_repr="IPAddress 10.9.0.5/24",
            detected_values={
                "logical_interface": "ge-0/0/5",
                "ip_address": "10.9.0.5/24",
                "vrf": None,
                "prefix": "10.9.0.0/24",
            },
            current_values={},
        )

        counts = rediff_entries(self.report, [entry.pk])
        self.assertEqual(counts, (0, 1, 0))
        entry.refresh_from_db()
        self.assertEqual(entry.current_values["assigned_object"], None)

        address.assigned_object = interface
        address.save()

        self.assertEqual(rediff_entries(self.report, [entry.pk]), (1, 0, 0))

    def test_rediff_resolves_a_neighbor_ip_once_its_mac_holds_it(self):
        """A neighbor address is satisfied by the MAC-to-address link its apply writes."""
        address = IPAddress.objects.create(address="10.9.0.8/24")
        mac = MACAddress.objects.create(mac_address="AA:BB:CC:DD:EE:08")
        entry = self.make_entry(
            action=EntryActionChoices.ACTION_NEW,
            entry_kind=EntryKindChoices.KIND_IP_ADDRESS,
            collector_type=CollectionTypeChoices.TYPE_ARP,
            object_repr="IPAddress 10.9.0.8/24",
            detected_values={"mac": "AA:BB:CC:DD:EE:08", "ip": "10.9.0.8/24", "interface": "ge-0/0/8", "vrf": None},
            current_values={},
        )

        counts = rediff_entries(self.report, [entry.pk])

        self.assertEqual(counts, (0, 1, 0))
        entry.refresh_from_db()
        # The snapshot keeps the key the neighbor collectors use.
        self.assertEqual(entry.current_values["ip"], "10.9.0.8/24")

        mac.ip_addresses.add(address)

        self.assertEqual(rediff_entries(self.report, [entry.pk]), (1, 0, 0))

    def test_rediff_leaves_an_entry_whose_subject_netbox_does_not_hold(self):
        """Nothing to read means nothing to conclude: the entry stays pending.

        One row per kind that can fail to find its subject, because each
        one looks the subject up its own way and a missing subject must
        never read as a satisfied change.
        """
        VRF.objects.create(name="REDIFF-DUPLICATE")
        VRF.objects.create(name="REDIFF-DUPLICATE")
        cases = {
            "lag": (EntryKindChoices.KIND_LAG, {"interface": "ge-9/9/9", "lag_parent": "ae9"}),
            "vrf": (EntryKindChoices.KIND_VRF, {"name": "REDIFF-ABSENT"}),
            "module": (EntryKindChoices.KIND_MODULE, {"module_bay_id": 0, "serial": "S"}),
            "mac": (EntryKindChoices.KIND_MAC_ADDRESS, {"mac": "AA:BB:CC:DD:EE:F0"}),
            "interface_mac": (EntryKindChoices.KIND_INTERFACE_MAC, {"interface": "ge-9/9/9"}),
            "interface": (EntryKindChoices.KIND_INTERFACE, {"interface": "ge-9/9/9"}),
            "ip": (EntryKindChoices.KIND_IP_ADDRESS, {"logical_interface": "ge-9/9/9", "ip_address": "10.9.9.9/32"}),
            "ip_ambiguous_vrf": (
                EntryKindChoices.KIND_IP_ADDRESS,
                {"ip_address": "10.9.9.9/32", "vrf": "REDIFF-DUPLICATE"},
            ),
        }
        for label, (kind, detected) in cases.items():
            with self.subTest(case=label):
                entry = self.make_entry(
                    action=EntryActionChoices.ACTION_NEW,
                    entry_kind=kind,
                    object_repr=f"absent {label}",
                    detected_values=detected,
                    current_values={"stale": "snapshot"},
                )

                counts = rediff_entries(self.report, [entry.pk])

                self.assertEqual(counts, (0, 1, 0))
                entry.refresh_from_db()
                self.assertEqual(entry.current_values, {})
                self.assertEqual(entry.status, EntryStatusChoices.STATUS_PENDING)

    def test_rediff_survives_a_mac_netbox_cannot_parse(self):
        """A device can report a MAC NetBox rejects; a rediff must not raise."""
        Interface.objects.create(device=self.device, name="ge-0/0/10", type="1000base-t")
        entry = self.make_entry(
            action=EntryActionChoices.ACTION_NEW,
            entry_kind=EntryKindChoices.KIND_INTERFACE_MAC,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            object_repr="Interface ge-0/0/10 MAC not-a-mac",
            detected_values={"interface": "ge-0/0/10", "mac_address": "not-a-mac"},
            current_values={},
        )

        counts = rediff_entries(self.report, [entry.pk])

        self.assertEqual(counts, (0, 1, 0))
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_PENDING)

    def test_rediff_resolves_a_vrf_someone_created(self):
        """A missing-VRF entry is satisfied by the VRF existing."""
        VRF.objects.create(name="REDIFF-VRF")
        entry = self.make_entry(
            action=EntryActionChoices.ACTION_NEW,
            entry_kind=EntryKindChoices.KIND_VRF,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            object_repr="VRF REDIFF-VRF",
            detected_values={"name": "REDIFF-VRF"},
            current_values={},
        )

        self.assertEqual(rediff_entries(self.report, [entry.pk]), (1, 0, 0))

    def test_rediff_leaves_a_kind_it_cannot_re_analyze_alone(self):
        """A cable needs both ends of a live topology, so it stays pending."""
        entry = self.make_entry(
            action=EntryActionChoices.ACTION_NEW,
            entry_kind=EntryKindChoices.KIND_CABLE,
            collector_type=CollectionTypeChoices.TYPE_LLDP,
            object_repr="Cable ge-0/0/6 -> peer",
            detected_values={"local_interface": "ge-0/0/6", "remote_device": "peer"},
            current_values={"note": "untouched"},
        )

        counts = rediff_entries(self.report, [entry.pk])

        self.assertEqual(counts, (0, 0, 1))
        entry.refresh_from_db()
        self.assertEqual(entry.current_values, {"note": "untouched"})
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_PENDING)

    def test_rediff_ignores_entries_that_are_not_pending(self):
        """Rediff is pending-only, like every other review transition."""
        self.device.serial = SERIAL
        self.device.save()
        resolved = self.make_entry(status=EntryStatusChoices.STATUS_APPLIED)
        skipped = self.make_entry(status=EntryStatusChoices.STATUS_SKIPPED)

        counts = rediff_entries(self.report, [resolved.pk, skipped.pk])

        self.assertEqual(counts, (0, 0, 0))
        skipped.refresh_from_db()
        self.assertEqual(skipped.current_values, {"serial_number": "AT_DETECT_TIME"})

    def test_rediff_ignores_entries_from_another_report(self):
        """Entry PKs are scoped to the report that owns them."""
        other_report = FactsReport.objects.create(collection_plan=self.plan)
        other_entry = self.make_entry(report=other_report)

        counts = rediff_entries(self.report, [other_entry.pk])

        self.assertEqual(counts, (0, 0, 0))
        other_entry.refresh_from_db()
        self.assertEqual(other_entry.current_values, {"serial_number": "AT_DETECT_TIME"})

    def test_rediff_recomputes_the_report_status_when_it_resolves_an_entry(self):
        """A report whose last pending entry resolves is no longer in review."""
        entry = self.make_entry()
        self.device.serial = SERIAL
        self.device.save()

        rediff_entries(self.report, [entry.pk])

        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatusChoices.STATUS_APPLIED)

    def test_rediff_leaves_the_proposed_change_untouched(self):
        """Re-reading NetBox does not re-identify what the device reported."""
        entry = self.make_entry(change_hash="deadbeef")
        self.device.serial = "MEANWHILE"
        self.device.save()

        rediff_entries(self.report, [entry.pk])

        entry.refresh_from_db()
        self.assertEqual(entry.detected_values, dict(DEVICE_PAYLOAD))
        self.assertEqual(entry.change_hash, "deadbeef")


def action_url(name, report):
    """Build a report-scoped entry action URL."""
    return reverse(f"plugins:netbox_facts:factsreport_{name}", kwargs={"pk": report.pk})


class RediffViewTest(RediffTestMixin, TestCase):
    """#142: the pending tab offers rediff and the view performs it."""

    user_permissions = ("netbox_facts.view_factsreport", "netbox_facts.apply_factsreport")

    def test_the_pending_status_offers_a_rediff_transition(self):
        """Both button groups are rendered from the one action table."""
        names = [action.name for action in entry_actions_for_status(EntryStatusChoices.STATUS_PENDING)]

        self.assertIn("rediff", names)

    def test_the_pending_tab_renders_the_rediff_control(self):
        self.make_entry()

        response = self.client.get(
            reverse(
                f"plugins:netbox_facts:factsreport_entries_{EntryStatusChoices.STATUS_PENDING}",
                kwargs={"pk": self.report.pk},
            )
        )

        self.assertIn(action_url("rediff", self.report), response.content.decode())

    def test_the_rediff_view_refreshes_selected_entries(self):
        entry = self.make_entry()
        self.device.serial = "MEANWHILE"
        self.device.save()

        response = self.client.post(action_url("rediff", self.report), {"pk": [entry.pk]})

        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.current_values, {"serial_number": "MEANWHILE"})

    def test_the_rediff_view_reports_each_kind_of_outcome(self):
        """A reviewer is told what left the queue and what could not be read."""
        self.device.serial = SERIAL
        self.device.save()
        satisfied = self.make_entry()
        unreadable = self.make_entry(
            entry_kind=EntryKindChoices.KIND_CABLE,
            collector_type=CollectionTypeChoices.TYPE_LLDP,
            object_repr="Cable ge-0/0/11 -> peer",
            detected_values={"local_interface": "ge-0/0/11"},
        )

        response = self.client.post(
            action_url("rediff", self.report),
            {"pk": [satisfied.pk, unreadable.pk]},
        )

        notices = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any("already satisfied" in notice for notice in notices), notices)
        self.assertTrue(any("cannot be re-analyzed" in notice for notice in notices), notices)


class RediffViewPermissionTest(RediffTestMixin, TestCase):
    """#142: rediff writes to entries, so it is gated like apply and skip."""

    user_permissions = ()

    def test_rediff_requires_the_apply_permission(self):
        entry = self.make_entry()

        response = self.client.post(action_url("rediff", self.report), {"pk": [entry.pk]})

        self.assertEqual(response.status_code, 403)
        entry.refresh_from_db()
        self.assertEqual(entry.current_values, {"serial_number": "AT_DETECT_TIME"})


class RediffAPITest(RediffTestMixin, APITestCase):
    """#142: rediff is mirrored as a report-level REST action."""

    user_permissions = ("netbox_facts.view_factsreport", "netbox_facts.add_factsreport")

    def api_url(self, report):
        return reverse("plugins-api:netbox_facts-api:factsreport-rediff", kwargs={"pk": report.pk})

    def test_the_rediff_action_reports_what_it_did(self):
        entry = self.make_entry()
        self.device.serial = SERIAL
        self.device.save()

        response = self.client.post(
            self.api_url(self.report),
            {"entries": [entry.pk]},
            format="json",
            **self.header,
        )

        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.data, {"resolved": 1, "refreshed": 0, "unsupported": 0})
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_APPLIED)

    def test_the_rediff_action_rejects_entries_from_another_report(self):
        other_report = FactsReport.objects.create(collection_plan=self.plan)
        entry = self.make_entry()

        response = self.client.post(
            self.api_url(other_report),
            {"entries": [entry.pk]},
            format="json",
            **self.header,
        )

        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)

    def test_the_rediff_action_requires_an_entry_list(self):
        response = self.client.post(
            self.api_url(self.report),
            {"entries": []},
            format="json",
            **self.header,
        )

        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)

    def test_the_rediff_action_carries_the_mutation_throttle(self):
        """Rediff is a write, so it shares the throttle of the other writes."""
        self.assertIn(FactsMutationThrottle, FactsReportViewSet.rediff.kwargs["throttle_classes"])

"""Tests for the entry kind field, kind dispatch, and apply status plumbing.

Covers issues #153 (entry_kind, kind dispatch, display_title) and #154
(APPLYING status, structured apply errors).
"""

from importlib import import_module
from unittest.mock import MagicMock, patch

from dcim.models.device_components import Interface
from django.apps import apps as django_apps
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase
from ipam.models.ip import IPAddress, Prefix
from ipam.models.vrfs import VRF
from netbox.graphql.schema import schema as root_schema
from rest_framework.exceptions import ValidationError as DRFValidationError

from netbox_facts.api.serializers import FactsReportEntrySerializer
from netbox_facts.choices import (
    CollectionTypeChoices,
    EntryActionChoices,
    EntryKindChoices,
    EntryStatusChoices,
    entry_kind_from_object_repr,
)
from netbox_facts.filtersets import FactsReportEntryFilterSet
from netbox_facts.graphql.filters import FactsReportEntryFilter
from netbox_facts.helpers.applier import apply_entries
from netbox_facts.models import FactsReport, FactsReportEntry
from netbox_facts.models.mac import MACAddress
from netbox_facts.tests.test_applier import ApplierTestMixin
from netbox_facts.tests.test_arp_ndp_regressions import ArpNdpCollectorTestMixin
from netbox_facts.tests.test_helpers import CollectorTestMixin
from netbox_facts.tests.test_interfaces_regressions import InterfacesRegressionTestMixin
from netbox_facts.tests.test_inventory_regressions import ChassisFixtureMixin

BACKFILL_MIGRATION = import_module("netbox_facts.migrations.0028_entry_kind_and_apply_error")


class EntryKindDerivationTest(TestCase):
    """The kind resolver reproduces the applier's legacy prefix matching (#153)."""

    CASES = (
        ("MACAddress AA:BB:CC:DD:EE:01", EntryKindChoices.KIND_MAC_ADDRESS),
        ("IPAddress 10.0.0.1/24 on Interface ge-0/0/0", EntryKindChoices.KIND_IP_ADDRESS),
        ("InventoryItem FPC 0", EntryKindChoices.KIND_INVENTORY_ITEM),
        ("Module FPC 0", EntryKindChoices.KIND_MODULE),
        ("Interface ge-0/0/0", EntryKindChoices.KIND_INTERFACE),
        ("VRF CUST_A", EntryKindChoices.KIND_VRF),
        ("LAG ge-0/0/0 -> ae0", EntryKindChoices.KIND_LAG),
        ("Cable ge-0/0/0 to peer:ge-0/0/1", EntryKindChoices.KIND_CABLE),
        ("Device router1", EntryKindChoices.KIND_DEVICE),
        ("BGPRouter router1", EntryKindChoices.KIND_BGP_ROUTER),
        ("BGPScope router1 global", EntryKindChoices.KIND_BGP_SCOPE),
        ("BGPPeer 10.0.0.1 AS65001", EntryKindChoices.KIND_BGP_PEER),
        ("BGP peer 10.0.0.1 AS65001", EntryKindChoices.KIND_BGP_PEER_IP),
        ("OSPF neighbor 10.0.0.1 (RID: 1.1.1.1)", EntryKindChoices.KIND_OSPF_NEIGHBOR),
        ("L2 circuit data on router1", EntryKindChoices.KIND_L2_CIRCUIT),
    )

    def test_known_prefixes_resolve_to_their_kind(self):
        """Every prefix the applier dispatched on maps to exactly one kind."""
        for object_repr, expected in self.CASES:
            with self.subTest(object_repr=object_repr):
                self.assertEqual(entry_kind_from_object_repr(object_repr), expected)

    def test_unmatched_repr_falls_back_to_other(self):
        """A label with no known prefix never fails; it resolves to 'other'."""
        for object_repr in ("", "Skip test 1", "Modules everywhere"):
            with self.subTest(object_repr=object_repr):
                self.assertEqual(entry_kind_from_object_repr(object_repr), EntryKindChoices.KIND_OTHER)


class EntryKindPersistenceTest(ApplierTestMixin, TestCase):
    """Saving an entry always leaves it with a usable kind (#153)."""

    def _entry(self, **kwargs):
        report = FactsReport.objects.create(collection_plan=self.plan)
        kwargs.setdefault("action", EntryActionChoices.ACTION_NEW)
        kwargs.setdefault("collector_type", CollectionTypeChoices.TYPE_INTERFACES)
        return FactsReportEntry.objects.create(report=report, device=self.device, **kwargs)

    def test_missing_kind_is_derived_from_object_repr(self):
        """An entry saved without a kind derives one from its label."""
        entry = self._entry(object_repr="LAG ge-0/0/0 -> ae0")
        self.assertEqual(entry.entry_kind, EntryKindChoices.KIND_LAG)

    def test_explicit_kind_wins_over_the_label(self):
        """An explicitly set kind is never overwritten by the label prefix."""
        entry = self._entry(
            entry_kind=EntryKindChoices.KIND_INTERFACE_MAC,
            object_repr="Interface ge-0/0/0 MAC AA:BB:CC:DD:EE:01",
        )
        self.assertEqual(entry.entry_kind, EntryKindChoices.KIND_INTERFACE_MAC)


class DisplayTitleTest(ApplierTestMixin, TestCase):
    """display_title composes kind label, subject and action verb (#153)."""

    def _title(self, entry_kind, object_repr, action):
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            device=self.device,
            action=action,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            entry_kind=entry_kind,
            object_repr=object_repr,
        )
        return entry.display_title

    def test_title_does_not_repeat_the_type_token(self):
        """A label that already leads with its type is not doubled up."""
        self.assertEqual(
            self._title(EntryKindChoices.KIND_INTERFACE, "Interface xe-0/0/1", EntryActionChoices.ACTION_CHANGED),
            "Interface xe-0/0/1 changed",
        )

    def test_title_uses_the_kind_label_and_action_verb(self):
        """The kind label prefixes the subject and the action becomes a verb."""
        self.assertEqual(
            self._title(EntryKindChoices.KIND_IP_ADDRESS, "IPAddress 10.0.0.1/32", EntryActionChoices.ACTION_NEW),
            "IP address 10.0.0.1/32 discovered",
        )
        self.assertEqual(
            self._title(EntryKindChoices.KIND_MODULE, "Module FPC 0", EntryActionChoices.ACTION_STALE),
            "Module FPC 0 stale",
        )

    def test_title_without_a_label_still_reads(self):
        """An entry with no object_repr still yields a kind and a verb."""
        self.assertEqual(
            self._title(EntryKindChoices.KIND_MAC_ADDRESS, "", EntryActionChoices.ACTION_NEW),
            "MAC address discovered",
        )


class InventoryCollectorKindTest(ChassisFixtureMixin, CollectorTestMixin, TestCase):
    """The inventory collector stamps a kind on every entry it records (#153)."""

    def test_device_item_and_module_entries_get_their_kind(self):
        plan = self._create_plan(detect_only=True)
        device = self._create_device("kind-inv-dev", serial="CHASSIS_SN")
        self._install_module(device, "FPC 0", "750-11111", "FPC0_SN")

        collector = self._make_collector(plan)
        collector._current_device = device
        report = FactsReport.objects.create(collection_plan=plan)
        collector._report = report

        collector.inventory(
            self._make_chassis_driver(
                [
                    {
                        "name": "FPC 0",
                        "component_name": "FPC 0",
                        "parent_name": None,
                        "serial": "FPC0_SN",
                        "part_id": "750-11111",
                        "description": "MPC 4e 3D",
                    },
                ]
            )
        )

        entries = report.entries.all()
        self.assertEqual(
            entries.get(object_repr__startswith="Device ").entry_kind,
            EntryKindChoices.KIND_DEVICE,
        )
        self.assertEqual(
            entries.get(object_repr="InventoryItem FPC 0").entry_kind,
            EntryKindChoices.KIND_INVENTORY_ITEM,
        )
        self.assertEqual(
            entries.get(object_repr="Module FPC 0").entry_kind,
            EntryKindChoices.KIND_MODULE,
        )


class InterfacesCollectorKindTest(InterfacesRegressionTestMixin, TestCase):
    """The interfaces collector distinguishes its five entry kinds (#153)."""

    def test_interface_mac_lag_ip_and_vrf_entries_get_their_kind(self):
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-kind-ifaces",
            detect_only=True,
        )
        device = self._create_device("kind-iface-dev")
        for name in ("ge-0/0/0", "ge-0/0/0.0", "ge-0/0/1", "ge-0/0/2", "ge-0/0/2.0"):
            Interface.objects.create(device=device, name=name, type="1000base-t")
        report = FactsReport.objects.create(collection_plan=plan)
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = report

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "ge-0/0/0": {
                "is_up": True,
                "is_enabled": True,
                "mac_address": "AA:BB:CC:DD:EE:61",
                "mtu": 1500,
                "speed": 1000.0,
                "logical_interfaces": {
                    "ge-0/0/0.0": {
                        "vrf": "",
                        "families": {
                            "inet": {"addresses": {"10.61.0.0/24": {"local": "10.61.0.1", "preferred": True}}},
                        },
                    },
                },
            },
            "ge-0/0/1": {
                "is_up": True,
                "is_enabled": True,
                "mac_address": "AA:BB:CC:DD:EE:62",
                "mtu": 1500,
                "speed": 1000.0,
                "logical_interfaces": {
                    "ge-0/0/1.0": {"vrf": "", "families": {"aenet": {"ae_bundle": "ae0.0"}}},
                },
            },
            "ge-0/0/2": {
                "is_up": True,
                "is_enabled": True,
                "mac_address": "",
                "mtu": 1500,
                "speed": 1000.0,
                "logical_interfaces": {
                    "ge-0/0/2.0": {
                        "vrf": "MISSING_VRF",
                        "families": {
                            "inet": {"addresses": {"10.62.0.0/24": {"local": "10.62.0.1", "preferred": True}}},
                        },
                    },
                },
            },
            "ge-0/0/9": {
                "is_up": True,
                "is_enabled": True,
                "mac_address": "AA:BB:CC:DD:EE:69",
                "mtu": 1500,
                "speed": 1000.0,
            },
        }

        collector.interfaces(driver)

        entries = report.entries.all()
        self.assertEqual(
            entries.get(object_repr__contains="MAC AA:BB:CC:DD:EE:61").entry_kind,
            EntryKindChoices.KIND_INTERFACE_MAC,
        )
        self.assertEqual(
            entries.get(object_repr__startswith="LAG ").entry_kind,
            EntryKindChoices.KIND_LAG,
        )
        self.assertEqual(
            entries.get(object_repr__startswith="IPAddress 10.61.0.1").entry_kind,
            EntryKindChoices.KIND_IP_ADDRESS,
        )
        self.assertEqual(
            entries.get(object_repr="VRF MISSING_VRF").entry_kind,
            EntryKindChoices.KIND_VRF,
        )
        self.assertEqual(
            entries.get(object_repr="Interface ge-0/0/9").entry_kind,
            EntryKindChoices.KIND_INTERFACE,
        )


class ArpCollectorKindTest(ArpNdpCollectorTestMixin, TestCase):
    """ARP records one MAC entry and one IP entry, each with its kind (#153)."""

    def test_mac_and_ip_entries_get_their_kind(self):
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_ARP,
            name="Plan-kind-arp",
            detect_only=True,
        )
        device = self._create_device("kind-arp-dev")
        Interface.objects.create(device=device, name="Ethernet1", type="1000base-t")
        Prefix.objects.create(prefix="10.70.0.0/24")
        report = FactsReport.objects.create(collection_plan=plan)
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = report

        driver = MagicMock()
        driver.get_arp_table.return_value = [
            {"interface": "Ethernet1", "mac": "AA:BB:CC:DD:EE:70", "ip": "10.70.0.50", "age": 3.0},
        ]
        driver.get_interfaces_ip.return_value = {"Ethernet1": {"ipv4": {"10.70.0.1": {"prefix_length": 24}}}}
        driver.get_network_instances.return_value = self._default_instance_for("Ethernet1")

        collector.arp(driver)

        kinds = dict(report.entries.values_list("object_repr", "entry_kind"))
        self.assertEqual(kinds.get("MACAddress AA:BB:CC:DD:EE:70"), EntryKindChoices.KIND_MAC_ADDRESS)
        self.assertEqual(kinds.get("IPAddress 10.70.0.50/24"), EntryKindChoices.KIND_IP_ADDRESS)


class EntryKindBackfillTest(ApplierTestMixin, TestCase):
    """The data migration fills the kind of rows recorded before the field (#153)."""

    def _legacy_entry(self, object_repr):
        """Create an entry and clear its kind, as a pre-migration row would be."""
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            device=self.device,
            object_repr=object_repr,
        )
        FactsReportEntry.objects.filter(pk=entry.pk).update(entry_kind="")
        return entry

    def test_backfill_resolves_kinds_from_labels(self):
        """Every legacy prefix is translated into the kind it dispatched as."""
        vrf_entry = self._legacy_entry("VRF CUST_A")
        module_entry = self._legacy_entry("Module FPC 0")
        opaque_entry = self._legacy_entry("Skip test 1")

        BACKFILL_MIGRATION.backfill_entry_kind(django_apps, None)

        vrf_entry.refresh_from_db()
        module_entry.refresh_from_db()
        opaque_entry.refresh_from_db()
        self.assertEqual(vrf_entry.entry_kind, EntryKindChoices.KIND_VRF)
        self.assertEqual(module_entry.entry_kind, EntryKindChoices.KIND_MODULE)
        self.assertEqual(opaque_entry.entry_kind, EntryKindChoices.KIND_OTHER)

    def test_backfill_leaves_rows_that_already_have_a_kind(self):
        """Rows recorded with a kind are not re-derived from their label."""
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            device=self.device,
            entry_kind=EntryKindChoices.KIND_INTERFACE_MAC,
            object_repr="Interface ge-0/0/0 MAC AA:BB:CC:DD:EE:02",
        )

        BACKFILL_MIGRATION.backfill_entry_kind(django_apps, None)

        entry.refresh_from_db()
        self.assertEqual(entry.entry_kind, EntryKindChoices.KIND_INTERFACE_MAC)


class DispatchByKindTest(ApplierTestMixin, TestCase):
    """Apply dispatch follows entry_kind, not the object_repr text (#153)."""

    def _apply(self, collector_type, entry_kind, detected_values, action=EntryActionChoices.ACTION_NEW):
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=action,
            collector_type=collector_type,
            device=self.device,
            entry_kind=entry_kind,
            object_repr="an opaque label the applier must ignore",
            detected_values=detected_values,
        )
        return entry, apply_entries(report, [entry.pk])

    def test_vrf_kind_routes_to_the_vrf_handler(self):
        """A VRF entry creates the VRF even though its label says nothing."""
        _entry, (applied, failed) = self._apply(
            CollectionTypeChoices.TYPE_INTERFACES,
            EntryKindChoices.KIND_VRF,
            {"name": "KIND_VRF_A"},
        )
        self.assertEqual((applied, failed), (1, 0))
        self.assertTrue(VRF.objects.filter(name="KIND_VRF_A").exists())

    def test_lag_kind_routes_to_the_lag_handler(self):
        """A LAG entry sets the member's lag parent from detected_values."""
        Interface.objects.create(device=self.device, name="ge-0/0/5", type="1000base-t")
        Interface.objects.create(device=self.device, name="ae5", type="lag")

        _entry, (applied, failed) = self._apply(
            CollectionTypeChoices.TYPE_INTERFACES,
            EntryKindChoices.KIND_LAG,
            {"interface": "ge-0/0/5", "lag_parent": "ae5"},
        )

        self.assertEqual((applied, failed), (1, 0))
        member = Interface.objects.get(device=self.device, name="ge-0/0/5")
        self.assertEqual(member.lag.name, "ae5")

    def test_mac_kind_routes_to_the_arp_mac_branch(self):
        """An ARP MAC entry creates the MAC and never the neighbor IP."""
        Interface.objects.create(device=self.device, name="Ethernet7", type="1000base-t")

        _entry, (applied, failed) = self._apply(
            CollectionTypeChoices.TYPE_ARP,
            EntryKindChoices.KIND_MAC_ADDRESS,
            {"mac": "AA:BB:CC:DD:EE:71", "ip": "10.71.0.50/24", "interface": "Ethernet7"},
        )

        self.assertEqual((applied, failed), (1, 0))
        self.assertTrue(MACAddress.objects.filter(mac_address="AA:BB:CC:DD:EE:71").exists())
        self.assertFalse(IPAddress.objects.filter(address="10.71.0.50/24").exists())

    def test_inventory_device_kind_routes_to_the_serial_branch(self):
        """A device inventory entry still updates the serial under kind dispatch."""
        _entry, (applied, failed) = self._apply(
            CollectionTypeChoices.TYPE_INVENTORY,
            EntryKindChoices.KIND_DEVICE,
            {"serial_number": "KIND_SERIAL"},
            action=EntryActionChoices.ACTION_CHANGED,
        )

        self.assertEqual((applied, failed), (1, 0))
        self.device.refresh_from_db()
        self.assertEqual(self.device.serial, "KIND_SERIAL")


class ApplyStatusTransitionTest(ApplierTestMixin, TestCase):
    """Entries move through APPLYING on their way to applied or failed (#154)."""

    def _entry(self, **kwargs):
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            device=self.device,
            entry_kind=EntryKindChoices.KIND_DEVICE,
            object_repr="Device status-test",
            **kwargs,
        )
        return report, entry

    def test_entry_is_marked_applying_while_its_handler_runs(self):
        """The persisted status is 'applying' for the duration of the handler."""
        report, entry = self._entry()
        observed = {}

        def handler(entry_arg, now):
            observed["status"] = FactsReportEntry.objects.get(pk=entry_arg.pk).status

        with patch.dict(
            "netbox_facts.helpers.applier.APPLY_HANDLERS",
            {CollectionTypeChoices.TYPE_INVENTORY: handler},
        ):
            applied, failed = apply_entries(report, [entry.pk])

        self.assertEqual((applied, failed), (1, 0))
        self.assertEqual(observed["status"], EntryStatusChoices.STATUS_APPLYING)
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_APPLIED)

    def test_failed_entry_leaves_applying_for_failed(self):
        """A raising handler leaves the entry failed, never stuck in applying."""
        report, entry = self._entry()

        def handler(entry_arg, now):
            raise RuntimeError("device unreachable")

        with patch.dict(
            "netbox_facts.helpers.applier.APPLY_HANDLERS",
            {CollectionTypeChoices.TYPE_INVENTORY: handler},
        ):
            applied, failed = apply_entries(report, [entry.pk])

        self.assertEqual((applied, failed), (0, 1))
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_FAILED)


class ApplyErrorStructureTest(ApplierTestMixin, TestCase):
    """Failed entries persist a structured, field-addressed error (#154)."""

    def _apply_with(self, handler, **entry_kwargs):
        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            device=self.device,
            entry_kind=EntryKindChoices.KIND_DEVICE,
            object_repr="Device error-test",
            **entry_kwargs,
        )
        with patch.dict(
            "netbox_facts.helpers.applier.APPLY_HANDLERS",
            {CollectionTypeChoices.TYPE_INVENTORY: handler},
        ):
            apply_entries(report, [entry.pk])
        entry.refresh_from_db()
        return entry

    def test_infrastructure_error_is_stored_under_all(self):
        """A plain exception is an 'error', addressed to the whole entry."""

        def handler(entry, now):
            raise RuntimeError("device unreachable")

        entry = self._apply_with(handler)

        self.assertEqual(
            entry.apply_error,
            {"error_type": "error", "__all__": ["device unreachable"]},
        )
        self.assertIn("device unreachable", entry.error_message)

    def test_django_validation_error_is_stored_per_field(self):
        """A Django ValidationError keeps its field addressing."""

        def handler(entry, now):
            raise DjangoValidationError({"serial": ["Serial is too long."]})

        entry = self._apply_with(handler)

        self.assertEqual(
            entry.apply_error,
            {"error_type": "validation", "serial": ["Serial is too long."]},
        )

    def test_non_field_validation_error_is_stored_under_all(self):
        """A ValidationError without field addressing lands under __all__."""

        def handler(entry, now):
            raise DjangoValidationError("Device is already claimed.")

        entry = self._apply_with(handler)

        self.assertEqual(
            entry.apply_error,
            {"error_type": "validation", "__all__": ["Device is already claimed."]},
        )

    def test_drf_validation_error_is_stored_per_field(self):
        """A DRF ValidationError maps its detail dict the same way."""

        def handler(entry, now):
            raise DRFValidationError({"address": ["Enter a valid IPv4 address."]})

        entry = self._apply_with(handler)

        self.assertEqual(
            entry.apply_error,
            {"error_type": "validation", "address": ["Enter a valid IPv4 address."]},
        )

    def test_successful_apply_clears_a_previous_error(self):
        """Re-applying an entry that failed before wipes the stale error."""

        def handler(entry, now):
            return None

        entry = self._apply_with(
            handler,
            error_message="device unreachable",
            apply_error={"error_type": "error", "__all__": ["device unreachable"]},
        )

        self.assertEqual(entry.status, EntryStatusChoices.STATUS_APPLIED)
        self.assertIsNone(entry.apply_error)
        self.assertEqual(entry.error_message, "")


class EntryApiSurfaceTest(ApplierTestMixin, TestCase):
    """entry_kind, display_title and apply_error are reachable through the API (#153, #154)."""

    def test_serializer_exposes_the_new_entry_attributes(self):
        fields = FactsReportEntrySerializer().fields
        for field_name in ("entry_kind", "display_title", "apply_error"):
            self.assertIn(field_name, fields)
        self.assertTrue(fields["display_title"].read_only)
        self.assertTrue(fields["apply_error"].read_only)

    def test_filterset_filters_entries_by_kind(self):
        report = FactsReport.objects.create(collection_plan=self.plan)
        lag_entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            device=self.device,
            entry_kind=EntryKindChoices.KIND_LAG,
            object_repr="LAG ge-0/0/0 -> ae0",
        )
        FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            device=self.device,
            entry_kind=EntryKindChoices.KIND_VRF,
            object_repr="VRF CUST_B",
        )

        filtered = FactsReportEntryFilterSet(
            {"entry_kind": [EntryKindChoices.KIND_LAG]},
            queryset=FactsReportEntry.objects.filter(report=report),
        ).qs

        self.assertEqual([entry.pk for entry in filtered], [lag_entry.pk])

    def test_graphql_exposes_entry_kind(self):
        entry_type = root_schema.get_type_by_name("FactsReportEntryType")
        self.assertIsNotNone(entry_type)
        self.assertIn("entry_kind", [field.name for field in entry_type.fields])
        self.assertIn("entry_kind", FactsReportEntryFilter.__annotations__)

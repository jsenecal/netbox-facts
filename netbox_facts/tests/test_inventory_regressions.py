"""Regression tests for chassis inventory Module reconciliation defects."""

from unittest.mock import MagicMock

from dcim.models.device_components import ModuleBay
from dcim.models.modules import Module, ModuleType
from django.test import TestCase
from napalm.base.exceptions import ConnectionException

from netbox_facts.choices import CollectionTypeChoices, EntryActionChoices
from netbox_facts.constants import AUTO_D_TAG
from netbox_facts.helpers.applier import apply_entries
from netbox_facts.models.facts_report import FactsReport, FactsReportEntry
from netbox_facts.tests.test_applier import ApplierTestMixin
from netbox_facts.tests.test_helpers import CollectorTestMixin


class ChassisFixtureMixin:
    """Shared chassis-inventory fixtures for the regression tests below."""

    def _make_chassis_driver(self, modules):
        """Create a mock driver whose get_chassis_inventory returns modules."""
        driver = MagicMock()
        driver.get_facts.return_value = {
            "serial_number": "CHASSIS_SN",
            "os_version": "21.2R3",
            "hostname": "router1",
            "fqdn": "router1.example.com",
        }
        driver.get_chassis_inventory.return_value = iter(modules)
        return driver

    def _install_module(self, device, bay_name, part_number, serial):
        """Create a ModuleBay holding an AUTO_D-tagged Module of a new type."""
        bay = ModuleBay.objects.create(device=device, name=bay_name)
        mod_type = ModuleType.objects.create(
            manufacturer=self.manufacturer,
            model=f"MOD-{part_number}",
            part_number=part_number,
        )
        mod = Module.objects.create(
            device=device,
            module_bay=bay,
            module_type=mod_type,
            serial=serial,
        )
        mod.tags.add(AUTO_D_TAG)
        return bay, mod_type, mod


class ChassisModuleSweepRegressionTest(ChassisFixtureMixin, CollectorTestMixin, TestCase):
    """Regression tests for the stale-module sweep (issue #50).

    A failed ModuleBay or ModuleType resolution for a reported chassis
    module must never translate into the module being flagged stale or
    deleted -- the hardware is demonstrably still present.
    """

    def test_present_module_with_unresolved_type_not_swept(self):
        """Issue #50: a present module whose ModuleType lookup fails must not
        be deleted or flagged STALE by the stale-module sweep."""
        plan = self._create_plan()
        device = self._create_device("regr-mod-type", serial="CHASSIS_SN")
        bay, _mod_type, mod = self._install_module(device, "FPC 0", "750-11111", "FPC0_SN")

        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = FactsReport.objects.create(collection_plan=plan)
        collector._log_warning = MagicMock()

        # Same hardware, but the part_id no longer matches any ModuleType
        # (e.g. the ModuleType was renamed in NetBox).
        driver = self._make_chassis_driver(
            [
                {
                    "name": "FPC 0",
                    "component_name": "FPC 0",
                    "parent_name": None,
                    "serial": "FPC0_SN",
                    "part_id": "750-RENAMED",
                    "description": "MPC 4e 3D",
                },
            ]
        )

        collector.inventory(driver)

        self.assertTrue(Module.objects.filter(pk=mod.pk).exists())
        stale_entries = collector._report.entries.filter(
            action=EntryActionChoices.ACTION_STALE,
            object_repr__startswith="Module ",
        )
        self.assertEqual(stale_entries.count(), 0)
        warnings = [call.args[0] for call in collector._log_warning.call_args_list]
        self.assertTrue(any("750-RENAMED" in msg for msg in warnings))

    def test_sweep_suppressed_when_bay_unresolved(self):
        """Issue #50: when a reported module's bay cannot be resolved, the
        stale-module sweep must be suppressed for the whole device."""
        plan = self._create_plan()
        device = self._create_device("regr-mod-bay", serial="CHASSIS_SN")
        bay, mod_type, mod = self._install_module(device, "FPC 0", "750-11111", "FPC0_SN")

        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = FactsReport.objects.create(collection_plan=plan)
        collector._log_warning = MagicMock()

        # The device reports a module in a bay NetBox does not model; the
        # module in FPC 0 is not reported (e.g. renamed components), so a
        # sweep keyed on seen bays would wrongly delete it.
        driver = self._make_chassis_driver(
            [
                {
                    "name": "FPC 5",
                    "component_name": "FPC 5",
                    "parent_name": None,
                    "serial": "FPC5_SN",
                    "part_id": "750-11111",
                    "description": "MPC 4e 3D",
                },
            ]
        )

        collector.inventory(driver)

        self.assertTrue(Module.objects.filter(pk=mod.pk).exists())
        stale_entries = collector._report.entries.filter(
            action=EntryActionChoices.ACTION_STALE,
            object_repr__startswith="Module ",
        )
        self.assertEqual(stale_entries.count(), 0)
        warnings = [call.args[0] for call in collector._log_warning.call_args_list]
        self.assertTrue(any("FPC 5" in msg and "stale" in msg.lower() for msg in warnings))

    def test_rpc_failure_does_not_reach_module_sweep(self):
        """Issue #50: a failed get_chassis_inventory RPC must leave existing
        auto-discovered Modules untouched."""
        plan = self._create_plan()
        device = self._create_device("regr-mod-rpc", serial="CHASSIS_SN")
        _bay, _mod_type, mod = self._install_module(device, "FPC 0", "750-11111", "FPC0_SN")

        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = FactsReport.objects.create(collection_plan=plan)

        driver = self._make_chassis_driver([])
        driver.get_chassis_inventory.side_effect = ConnectionException("unreachable")

        collector.inventory(driver)

        self.assertTrue(Module.objects.filter(pk=mod.pk).exists())
        stale_entries = collector._report.entries.filter(
            action=EntryActionChoices.ACTION_STALE,
            object_repr__startswith="Module ",
        )
        self.assertEqual(stale_entries.count(), 0)


class ChassisModuleTypeSwapRegressionTest(ChassisFixtureMixin, CollectorTestMixin, TestCase):
    """Regression tests for module type swaps in a bay (issue #51).

    Replacing the hardware in a bay with a different part must be detected
    as CHANGED (even when the serial is unchanged), reported with the
    current module type, and applied by replacing the Module's type.
    """

    def test_type_swap_detected_as_changed_and_applied(self):
        """Issue #51: a different part in the bay must yield CHANGED with the
        old module_type in current_values, and apply the new ModuleType."""
        plan = self._create_plan()
        device = self._create_device("regr-swap-1", serial="CHASSIS_SN")
        bay, old_type, _mod = self._install_module(device, "FPC 0", "750-11111", "OLD_SN")
        new_type = ModuleType.objects.create(
            manufacturer=self.manufacturer,
            model="MOD-750-22222",
            part_number="750-22222",
        )

        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = FactsReport.objects.create(collection_plan=plan)

        driver = self._make_chassis_driver(
            [
                {
                    "name": "FPC 0",
                    "component_name": "FPC 0",
                    "parent_name": None,
                    "serial": "NEW_SN",
                    "part_id": "750-22222",
                    "description": "Replacement card",
                },
            ]
        )

        collector.inventory(driver)

        changed_entries = collector._report.entries.filter(
            action=EntryActionChoices.ACTION_CHANGED,
            object_repr__startswith="Module ",
        )
        self.assertEqual(changed_entries.count(), 1)
        entry = changed_entries.first()
        self.assertEqual(entry.current_values.get("module_type_id"), old_type.pk)

        installed = Module.objects.get(device=device, module_bay=bay)
        self.assertEqual(installed.module_type, new_type)
        self.assertEqual(installed.serial, "NEW_SN")

    def test_same_serial_type_swap_detected_as_changed(self):
        """Issue #51: a different part reporting the same serial must be
        CHANGED, not silently CONFIRMED."""
        plan = self._create_plan(detect_only=True)
        device = self._create_device("regr-swap-2", serial="CHASSIS_SN")
        _bay, old_type, _mod = self._install_module(device, "FPC 0", "750-11111", "SAME_SN")
        ModuleType.objects.create(
            manufacturer=self.manufacturer,
            model="MOD-750-22222",
            part_number="750-22222",
        )

        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = FactsReport.objects.create(collection_plan=plan)

        driver = self._make_chassis_driver(
            [
                {
                    "name": "FPC 0",
                    "component_name": "FPC 0",
                    "parent_name": None,
                    "serial": "SAME_SN",
                    "part_id": "750-22222",
                    "description": "Replacement card",
                },
            ]
        )

        collector.inventory(driver)

        changed_entries = collector._report.entries.filter(
            action=EntryActionChoices.ACTION_CHANGED,
            object_repr__startswith="Module ",
        )
        self.assertEqual(changed_entries.count(), 1)
        self.assertEqual(changed_entries.first().current_values.get("module_type_id"), old_type.pk)
        confirmed_entries = collector._report.entries.filter(
            action=EntryActionChoices.ACTION_CONFIRMED,
            object_repr__startswith="Module ",
        )
        self.assertEqual(confirmed_entries.count(), 0)

    def test_serial_only_swap_preserves_module_identity(self):
        """A same-type serial change must update the Module in place, not
        replace it."""
        plan = self._create_plan()
        device = self._create_device("regr-swap-3", serial="CHASSIS_SN")
        bay, mod_type, mod = self._install_module(device, "FPC 0", "750-11111", "OLD_SN")

        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = FactsReport.objects.create(collection_plan=plan)

        driver = self._make_chassis_driver(
            [
                {
                    "name": "FPC 0",
                    "component_name": "FPC 0",
                    "parent_name": None,
                    "serial": "NEW_SN",
                    "part_id": "750-11111",
                    "description": "Same card, RMA replacement",
                },
            ]
        )

        collector.inventory(driver)

        installed = Module.objects.get(device=device, module_bay=bay)
        self.assertEqual(installed.pk, mod.pk)
        self.assertEqual(installed.module_type, mod_type)
        self.assertEqual(installed.serial, "NEW_SN")


class ApplyModuleTypeSwapRegressionTest(ChassisFixtureMixin, ApplierTestMixin, TestCase):
    """Regression tests for applying a module type swap (issue #51)."""

    def test_apply_changed_entry_replaces_module_type(self):
        """Issue #51: applying a CHANGED Module entry with a new module_type
        must leave the bay holding the new ModuleType."""
        bay, old_type, _mod = self._install_module(self.device, "FPC 0", "750-11111", "OLD_SN")
        new_type = ModuleType.objects.create(
            manufacturer=self.manufacturer,
            model="MOD-750-22222",
            part_number="750-22222",
        )

        report = FactsReport.objects.create(collection_plan=self.plan)
        entry = FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_CHANGED,
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            device=self.device,
            object_repr="Module FPC 0",
            detected_values={
                "name": "FPC 0",
                "component_name": "FPC 0",
                "serial": "NEW_SN",
                "part_id": "750-22222",
                "module_bay_id": bay.pk,
                "module_type_id": new_type.pk,
            },
            current_values={"serial": "OLD_SN", "module_type_id": old_type.pk},
        )

        applied, failed = apply_entries(report, [entry.pk])
        self.assertEqual(applied, 1)
        self.assertEqual(failed, 0)

        installed = Module.objects.get(device=self.device, module_bay=bay)
        self.assertEqual(installed.module_type, new_type)
        self.assertEqual(installed.serial, "NEW_SN")
        self.assertTrue(installed.tags.filter(name=AUTO_D_TAG).exists())

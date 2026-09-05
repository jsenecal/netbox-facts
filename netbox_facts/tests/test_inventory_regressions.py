"""Regression tests for chassis inventory Module reconciliation defects."""

from unittest.mock import MagicMock

from dcim.models.device_components import ModuleBay
from dcim.models.modules import Module, ModuleType
from django.test import TestCase
from napalm.base.exceptions import ConnectionException

from netbox_facts.choices import EntryActionChoices
from netbox_facts.constants import AUTO_D_TAG
from netbox_facts.models.facts_report import FactsReport
from netbox_facts.tests.test_helpers import CollectorTestMixin


class ChassisModuleSweepRegressionTest(CollectorTestMixin, TestCase):
    """Regression tests for the stale-module sweep (issue #50).

    A failed ModuleBay or ModuleType resolution for a reported chassis
    module must never translate into the module being flagged stale or
    deleted -- the hardware is demonstrably still present.
    """

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

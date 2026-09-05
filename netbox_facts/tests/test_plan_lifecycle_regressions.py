"""Regression tests for CollectionPlan lifecycle and credential handling."""

from dcim.choices import DeviceStatusChoices
from django.test import TestCase

from netbox_facts.choices import CollectionTypeChoices
from netbox_facts.models import CollectionPlan


def _build_plan(**kwargs):
    """Return an unsaved CollectionPlan with sensible defaults."""
    defaults = {
        "name": "Lifecycle Test Plan",
        "collector_type": CollectionTypeChoices.TYPE_ARP,
        "napalm_driver": "junos",
        "device_status": [DeviceStatusChoices.STATUS_ACTIVE],
    }
    defaults.update(kwargs)
    return CollectionPlan(**defaults)


class GetNapalmDriverTest(TestCase):
    """Tests for CollectionPlan.get_napalm_driver driver resolution."""

    def test_plugin_driver_resolved_for_junos(self):
        """Regression test for issue #42.

        Under napalm 5.2.0, get_network_driver() rejects dotted driver
        names before importing them, so the plugin-local enhanced driver
        must be loaded directly instead of through napalm's loader.
        """
        from netbox_facts.napalm.junos import EnhancedJunOSDriver

        plan = _build_plan(napalm_driver="junos")
        self.assertIs(plan.get_napalm_driver(), EnhancedJunOSDriver)

    def test_stock_driver_resolved_for_eos(self):
        """Regression test for issue #42.

        A driver without a plugin-local override must fall back to the
        stock napalm driver instead of raising ModuleImportError.
        """
        from napalm.eos import EOSDriver

        plan = _build_plan(napalm_driver="eos")
        self.assertIs(plan.get_napalm_driver(), EOSDriver)

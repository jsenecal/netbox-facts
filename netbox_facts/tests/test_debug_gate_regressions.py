"""Regression tests for the debugpy hook gating in CollectionPlan (issue #132)."""

import sys
from unittest.mock import MagicMock, patch

from dcim.choices import DeviceStatusChoices
from django.test import TestCase, override_settings

from netbox_facts.choices import CollectionTypeChoices
from netbox_facts.models import CollectionPlan


def _build_plan(**kwargs):
    """Return an unsaved CollectionPlan with sensible defaults."""
    defaults = {
        "name": "Debug Gate Test Plan",
        "collector_type": CollectionTypeChoices.TYPE_ARP,
        "napalm_driver": "junos",
        "device_status": [DeviceStatusChoices.STATUS_ACTIVE],
    }
    defaults.update(kwargs)
    return CollectionPlan(**defaults)


class GetNapalmArgsStripsDebugTest(TestCase):
    """get_napalm_args() must never surface the debug key to the NAPALM driver."""

    def test_debug_key_absent_from_merged_args(self):
        """Regression test for issue #132.

        A plan's free-form napalm_args JSON field let any user with
        change permission set debug: true. That key must be stripped
        from the merged args returned to callers (and ultimately the
        NAPALM driver) regardless of settings.DEBUG.
        """
        plan = _build_plan(napalm_args={"debug": True, "timeout": 5})
        merged = plan.get_napalm_args()
        self.assertNotIn("debug", merged)
        self.assertEqual(merged["timeout"], 5)

    @override_settings(DEBUG=True)
    def test_debug_key_absent_from_merged_args_even_with_settings_debug_true(self):
        """Regression test for issue #132.

        Stripping in get_napalm_args() is unconditional: even when
        settings.DEBUG is True, the driver-facing args must not carry
        the debug key (only run()'s own gate may act on it).
        """
        plan = _build_plan(napalm_args={"debug": True})
        merged = plan.get_napalm_args()
        self.assertNotIn("debug", merged)


class RunDebugpyGateTest(TestCase):
    """CollectionPlan.run() must gate the debugpy hook behind settings.DEBUG."""

    def setUp(self):
        self.plan = CollectionPlan.objects.create(
            name="Debug Gate Run Plan",
            collector_type=CollectionTypeChoices.TYPE_ARP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
            napalm_args={"debug": True},
        )
        self.mock_debugpy = MagicMock()
        self._debugpy_patcher = patch.dict(sys.modules, {"debugpy": self.mock_debugpy})
        self._debugpy_patcher.start()
        self.addCleanup(self._debugpy_patcher.stop)

    @override_settings(DEBUG=False)
    def test_debug_key_ignored_when_settings_debug_false(self):
        """Regression test for issue #132.

        With debug: true in napalm_args but settings.DEBUG False, the
        debugpy hook must never be imported, must never listen on any
        socket, and must never block the worker.
        """
        with patch("netbox_facts.models.collection_plan.NapalmCollector") as mock_collector_cls:
            mock_collector_cls.return_value.execute.return_value = None
            self.plan.run()

        self.mock_debugpy.listen.assert_not_called()
        self.mock_debugpy.wait_for_client.assert_not_called()

    @override_settings(DEBUG=True)
    def test_debugpy_binds_localhost_when_settings_debug_true(self):
        """Regression test for issue #132.

        When settings.DEBUG is True and debug: true is requested, the
        hook may still run for local development, but it must bind to
        127.0.0.1, never 0.0.0.0.
        """
        with patch("netbox_facts.models.collection_plan.NapalmCollector") as mock_collector_cls:
            mock_collector_cls.return_value.execute.return_value = None
            self.plan.run()

        self.mock_debugpy.listen.assert_called_once_with(("127.0.0.1", 5678))
        self.mock_debugpy.wait_for_client.assert_called_once()

    @override_settings(DEBUG=False)
    def test_napalm_args_instance_field_not_mutated(self):
        """Regression test for issue #132.

        The old code called self.napalm_args.pop("debug") as a side
        effect of the debugpy branch, mutating the model instance in
        place. The debug key must remain in the stored field; only the
        merged copy returned by get_napalm_args() is ever filtered.
        """
        with patch("netbox_facts.models.collection_plan.NapalmCollector") as mock_collector_cls:
            mock_collector_cls.return_value.execute.return_value = None
            self.plan.run()

        self.assertIn("debug", self.plan.napalm_args)
        self.assertTrue(self.plan.napalm_args["debug"])

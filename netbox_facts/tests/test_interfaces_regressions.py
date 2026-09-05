"""Regression tests for the interfaces collector and its helpers."""

import re

from dcim.models.device_components import Interface
from django.test import TestCase
from ipam.models.ip import IPAddress, Prefix
from unittest.mock import MagicMock

from netbox_facts.choices import CollectionTypeChoices
from netbox_facts.tests.test_helpers import CollectorTestMixin


class InterfacesRegressionTestMixin(CollectorTestMixin):
    """CollectorTestMixin with a real catch-all interface regex."""

    def _make_collector(self, plan):
        collector = super()._make_collector(plan)
        collector._interfaces_re = re.compile(r".*")
        return collector


class JunosAbbreviatedDestinationTest(InterfacesRegressionTestMixin, TestCase):
    """Junos trims trailing zero octets from inet destinations."""

    def test_abbreviated_inet_destinations_keep_real_prefix_length(self):
        """Regression test for issue #56: '10/8' and '172.16/16' destinations
        must produce /8 and /16 addresses, never fall back to /32."""
        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            name="Plan-dest-pad",
        )
        device = self._create_device("dest-pad-dev")
        Interface.objects.create(device=device, name="xe-0/0/0", type="other")
        collector = self._make_collector(plan)
        collector._current_device = device

        driver = MagicMock()
        driver.get_interfaces.return_value = {
            "xe-0/0/0": {
                "is_up": True,
                "is_enabled": True,
                "description": "",
                "last_flapped": -1.0,
                "speed": 10000.0,
                "mtu": 1500,
                "mac_address": "",
                "logical_interfaces": {
                    "xe-0/0/0.0": {
                        "vrf": "",
                        "families": {
                            "inet": {
                                "addresses": {
                                    "10/8": {"local": "10.0.0.1", "preferred": True},
                                    "172.16/16": {"local": "172.16.5.1", "preferred": True},
                                    "10.0.1/24": {"local": "10.0.1.5", "preferred": True},
                                },
                            },
                        },
                    },
                },
            },
        }

        collector.interfaces(driver)

        self.assertTrue(IPAddress.objects.filter(address="10.0.0.1/8").exists())
        self.assertTrue(IPAddress.objects.filter(address="172.16.5.1/16").exists())
        self.assertTrue(IPAddress.objects.filter(address="10.0.1.5/24").exists())
        self.assertFalse(IPAddress.objects.filter(address="10.0.0.1/32").exists())
        self.assertFalse(IPAddress.objects.filter(address="172.16.5.1/32").exists())
        self.assertTrue(Prefix.objects.filter(prefix="10.0.0.0/8").exists())
        self.assertTrue(Prefix.objects.filter(prefix="172.16.0.0/16").exists())
        self.assertTrue(Prefix.objects.filter(prefix="10.0.1.0/24").exists())

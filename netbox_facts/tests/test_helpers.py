from unittest.mock import MagicMock, patch

from django.test import TestCase

from netbox_facts.helpers.napalm import (
    get_network_instances_by_interface,
    parse_network_instances,
)
from netbox_facts.helpers.netbox import get_absolute_url_markdown, get_primary_ip


class ParseNetworkInstancesTest(TestCase):
    """Tests for parse_network_instances."""

    def test_parse_l3vrf(self):
        raw = {
            "default": {
                "name": "default",
                "type": "DEFAULT_INSTANCE",
                "state": {"route_distinguisher": ""},
                "interfaces": {"interface": {"ge-0/0/0.0": {}, "lo0.0": {}}},
            },
            "VRF_A": {
                "name": "VRF_A",
                "type": "L3VRF",
                "state": {"route_distinguisher": "65000:100"},
                "interfaces": {"interface": {"ge-0/0/1.100": {}}},
            },
        }
        result = parse_network_instances(raw)
        self.assertIn("default", result)
        self.assertIn("VRF_A", result)
        self.assertEqual(result["default"]["instance_type"], "DEFAULT_INSTANCE")
        self.assertIsNone(result["default"]["route_distinguisher"])
        self.assertEqual(result["VRF_A"]["instance_type"], "L3VRF")
        self.assertEqual(result["VRF_A"]["route_distinguisher"], "65000:100")
        self.assertIn("ge-0/0/1.100", result["VRF_A"]["interfaces"])

    def test_parse_empty(self):
        result = parse_network_instances({})
        self.assertEqual(result, {})


class GetNetworkInstancesByInterfaceTest(TestCase):
    """Tests for get_network_instances_by_interface."""

    def test_yields_per_interface(self):
        instances = [
            (
                "default",
                {
                    "instance_type": "DEFAULT_INSTANCE",
                    "route_distinguisher": None,
                    "interfaces": ["ge-0/0/0.0", "lo0.0"],
                },
            ),
        ]
        result = dict(get_network_instances_by_interface(instances))
        self.assertIn("ge-0/0/0.0", result)
        self.assertIn("lo0.0", result)
        self.assertEqual(result["ge-0/0/0.0"]["name"], "default")
        self.assertNotIn("interfaces", result["ge-0/0/0.0"])


class GetAbsoluteUrlMarkdownTest(TestCase):
    """Tests for get_absolute_url_markdown."""

    def test_basic_link(self):
        obj = MagicMock()
        obj.__str__ = MagicMock(return_value="TestObj")
        obj.get_absolute_url.return_value = "/test/1/"
        result = get_absolute_url_markdown(obj)
        self.assertEqual(result, "[TestObj](/test/1/)")

    def test_bold_link(self):
        obj = MagicMock()
        obj.__str__ = MagicMock(return_value="TestObj")
        obj.get_absolute_url.return_value = "/test/1/"
        result = get_absolute_url_markdown(obj, bold=True)
        self.assertEqual(result, "**[TestObj](/test/1/)**")

    def test_code_link(self):
        obj = MagicMock()
        obj.__str__ = MagicMock(return_value="TestObj")
        obj.get_absolute_url.return_value = "/test/1/"
        result = get_absolute_url_markdown(obj, code=True)
        self.assertEqual(result, "[`TestObj`](/test/1/)")

    def test_raises_on_no_url(self):
        obj = MagicMock(spec=[])
        with self.assertRaises(ValueError):
            get_absolute_url_markdown(obj)


class GetPrimaryIpTest(TestCase):
    """Tests for get_primary_ip."""

    def test_returns_ip_string(self):
        device = MagicMock()
        ip_mock = MagicMock()
        ip_mock.address.ip = "10.0.0.1"
        device.primary_ip = ip_mock
        result = get_primary_ip(device)
        self.assertEqual(result, "10.0.0.1")

    def test_raises_when_no_primary_ip(self):
        device = MagicMock()
        device.primary_ip = None
        device.__str__ = MagicMock(return_value="device1")
        with self.assertRaises(ValueError):
            get_primary_ip(device)

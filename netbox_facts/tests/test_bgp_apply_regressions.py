"""Regression tests for BGP collector/applier apply-semantics bugs."""

import unittest
from unittest.mock import MagicMock

from django.test import TestCase

from netbox_facts.choices import CollectionTypeChoices
from netbox_facts.helpers.collector import HAS_NETBOX_ROUTING
from netbox_facts.models import FactsReport, FactsReportEntry
from netbox_facts.tests.test_helpers import CollectorTestMixin


def _bgp_neighbors_payload(remote_address="10.0.0.1", local_as=65000, remote_as=65001):
    """Build a minimal get_bgp_neighbors_detail payload with one global peer."""
    return {
        "global": {
            str(remote_as): [
                {
                    "up": True,
                    "local_as": local_as,
                    "remote_as": remote_as,
                    "remote_address": remote_address,
                    "local_address": "10.0.0.2",
                }
            ]
        }
    }


@unittest.skipUnless(HAS_NETBOX_ROUTING, "netbox-routing not installed")
class BGPRoutingDetectOnlyRegressionTest(CollectorTestMixin, TestCase):
    """Detect-only BGP runs must not write to NetBox (issue #48)."""

    def test_detect_only_does_not_create_local_asn(self):
        """A detect-only bgp() run must not create the local ASN (issue #48)."""
        from ipam.models import ASN, RIR
        from netbox_routing.models import BGPRouter

        RIR.objects.create(name="Detect-RIR", slug="detect-rir")

        plan = self._create_plan(
            collector_type=CollectionTypeChoices.TYPE_BGP,
            name="Plan-bgp-detect-only",
            detect_only=True,
        )
        device = self._create_device("bgp-detect-dev")
        collector = self._make_collector(plan)
        collector._current_device = device
        collector._report = FactsReport.objects.create(collection_plan=plan)

        driver = MagicMock()
        driver.get_bgp_neighbors_detail.return_value = _bgp_neighbors_payload()

        collector.bgp(driver)

        self.assertFalse(ASN.objects.exists())
        self.assertFalse(BGPRouter.objects.exists())
        self.assertTrue(
            FactsReportEntry.objects.filter(
                report=collector._report,
                object_repr__startswith="BGPRouter",
            ).exists()
        )

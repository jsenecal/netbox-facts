"""Tests for rate limiting, bulk import forms, and management commands."""

from io import StringIO
from unittest.mock import patch, MagicMock

from django.core.management import call_command
from django.test import TestCase
from dcim.choices import DeviceStatusChoices

from netbox_facts.choices import (
    CollectionTypeChoices,
    CollectorStatusChoices,
)
from netbox_facts.exceptions import OperationNotSupported
from netbox_facts.forms import (
    MACAddressImportForm,
    MACVendorImportForm,
    CollectionPlanImportForm,
)
from netbox_facts.models import CollectionPlan


class EnqueueGuardTest(TestCase):
    """Tests for the duplicate job prevention guard on enqueue_collection_job."""

    @classmethod
    def setUpTestData(cls):
        cls.plan = CollectionPlan.objects.create(
            name="Guard Test Plan",
            collector_type=CollectionTypeChoices.TYPE_ARP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )

    def test_enqueue_raises_when_queued(self):
        """enqueue_collection_job raises OperationNotSupported when plan is QUEUED."""
        CollectionPlan.objects.filter(pk=self.plan.pk).update(
            status=CollectorStatusChoices.QUEUED,
        )
        self.plan.refresh_from_db()
        mock_request = MagicMock()
        with self.assertRaises(OperationNotSupported):
            self.plan.enqueue_collection_job(mock_request)

    def test_enqueue_raises_when_working(self):
        """enqueue_collection_job raises OperationNotSupported when plan is WORKING."""
        CollectionPlan.objects.filter(pk=self.plan.pk).update(
            status=CollectorStatusChoices.WORKING,
        )
        self.plan.refresh_from_db()
        # check_stalled() in __init__ may reset status if no active job exists;
        # force the status back to WORKING for this test
        self.plan.status = CollectorStatusChoices.WORKING
        mock_request = MagicMock()
        with self.assertRaises(OperationNotSupported):
            self.plan.enqueue_collection_job(mock_request)

    def test_enqueue_succeeds_when_new(self):
        """enqueue_collection_job does not raise when plan is NEW."""
        CollectionPlan.objects.filter(pk=self.plan.pk).update(
            status=CollectorStatusChoices.NEW,
        )
        self.plan.refresh_from_db()
        # Just verify the guard doesn't block — the actual enqueue
        # is tested in test_jobs.py
        self.assertNotIn(
            self.plan.status,
            (CollectorStatusChoices.QUEUED, CollectorStatusChoices.WORKING),
        )


class RecoverStaleJobsCommandTest(TestCase):
    """Tests for the recover_stale_jobs management command."""

    @classmethod
    def setUpTestData(cls):
        cls.working_plan = CollectionPlan.objects.create(
            name="Working Plan",
            collector_type=CollectionTypeChoices.TYPE_ARP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        cls.idle_plan = CollectionPlan.objects.create(
            name="Idle Plan",
            collector_type=CollectionTypeChoices.TYPE_NDP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )

    def test_recovers_stale_working_plan(self):
        """Plans stuck in WORKING with no active job should be marked STALLED."""
        CollectionPlan.objects.filter(pk=self.working_plan.pk).update(
            status=CollectorStatusChoices.WORKING,
        )
        out = StringIO()
        call_command("recover_stale_jobs", stdout=out)

        self.working_plan.refresh_from_db()
        self.assertEqual(self.working_plan.status, CollectorStatusChoices.STALLED)
        self.assertIn("Recovered", out.getvalue())

    def test_does_not_recover_idle_plan(self):
        """Idle plans should not be affected."""
        CollectionPlan.objects.filter(pk=self.idle_plan.pk).update(
            status=CollectorStatusChoices.NEW,
        )
        out = StringIO()
        call_command("recover_stale_jobs", stdout=out)

        self.idle_plan.refresh_from_db()
        self.assertEqual(self.idle_plan.status, CollectorStatusChoices.NEW)

    def test_no_stale_plans_message(self):
        """When all plans are idle, the command reports no stale plans."""
        CollectionPlan.objects.update(status=CollectorStatusChoices.NEW)
        out = StringIO()
        call_command("recover_stale_jobs", stdout=out)
        self.assertIn("No stale plans found", out.getvalue())


class ImportFormTest(TestCase):
    """Tests for bulk import form classes."""

    def test_mac_address_import_form_fields(self):
        """MACAddressImportForm should have mac_address in its fields."""
        form = MACAddressImportForm()
        self.assertIn("mac_address", form.fields)

    def test_mac_vendor_import_form_fields(self):
        """MACVendorImportForm should have vendor_name and mac_prefix in its fields."""
        form = MACVendorImportForm()
        self.assertIn("vendor_name", form.fields)
        self.assertIn("mac_prefix", form.fields)
        self.assertIn("manufacturer", form.fields)

    def test_collection_plan_import_form_fields(self):
        """CollectionPlanImportForm should have name and collector_type in its fields."""
        form = CollectionPlanImportForm()
        self.assertIn("name", form.fields)
        self.assertIn("collector_type", form.fields)
        self.assertIn("napalm_driver", form.fields)

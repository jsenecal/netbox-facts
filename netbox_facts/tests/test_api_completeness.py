"""Tests for REST API completeness (regression tests for #151)."""

from dcim.choices import DeviceStatusChoices
from django.test import TestCase
from netbox.constants import CENSOR_TOKEN
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from netbox_facts.api.serializers import CollectionPlanSerializer
from netbox_facts.choices import CollectionTypeChoices, ConnectionTargetChoices
from netbox_facts.models import CollectionPlan


class CollectionPlanSerializerFieldsTest(TestCase):
    """The plan serializer must expose the scheduling and connection fields (#151)."""

    def test_scheduling_fields_are_exposed(self):
        """interval, scheduled_at, last_run, run_as and connection_target are serialized."""
        fields = CollectionPlanSerializer().fields
        for field_name in (
            "interval",
            "scheduled_at",
            "last_run",
            "run_as",
            "connection_target",
        ):
            self.assertIn(field_name, fields)

    def test_last_run_is_read_only(self):
        """last_run is maintained by the collection job, never by API clients."""
        self.assertTrue(CollectionPlanSerializer().fields["last_run"].read_only)

    def test_schedule_is_writable(self):
        """A client can set the schedule fields when creating or updating a plan."""
        fields = CollectionPlanSerializer().fields
        for field_name in ("interval", "scheduled_at", "run_as", "connection_target"):
            self.assertFalse(fields[field_name].read_only, msg=field_name)


class CollectionPlanCredentialMaskingTest(TestCase):
    """Credential masking must survive the serializer field additions (#151)."""

    @classmethod
    def setUpTestData(cls):
        cls.plan = CollectionPlan.objects.create(
            name="Masking Plan",
            collector_type=CollectionTypeChoices.TYPE_ARP,
            napalm_driver="junos",
            napalm_args={"username": "admin", "password": "s3cret", "port": 22},
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
            interval=60,
            connection_target=ConnectionTargetChoices.TARGET_PRIMARY,
        )

    def _serializer_context(self):
        return {"request": Request(APIRequestFactory().get("/"))}

    def test_credentials_are_masked_on_read(self):
        """Sensitive napalm_args values are censored in the API representation."""
        data = CollectionPlanSerializer(self.plan, context=self._serializer_context()).data
        self.assertEqual(data["napalm_args"]["username"], CENSOR_TOKEN)
        self.assertEqual(data["napalm_args"]["password"], CENSOR_TOKEN)
        self.assertEqual(data["napalm_args"]["port"], 22)

    def test_masked_credentials_round_trip_without_loss(self):
        """Writing back the censored representation keeps the stored credentials."""
        context = self._serializer_context()
        masked = CollectionPlanSerializer(self.plan, context=context).data["napalm_args"]

        serializer = CollectionPlanSerializer(
            instance=self.plan,
            data={"napalm_args": masked, "interval": 120},
            partial=True,
            context=context,
        )
        self.assertTrue(serializer.is_valid(), msg=serializer.errors)
        plan = serializer.save()

        plan.refresh_from_db()
        self.assertEqual(plan.napalm_args["username"], "admin")
        self.assertEqual(plan.napalm_args["password"], "s3cret")
        self.assertEqual(plan.interval, 120)

"""Tests for REST and GraphQL API completeness (regression tests for #151)."""

from types import SimpleNamespace

from dcim.choices import DeviceStatusChoices
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.test import TestCase
from netbox.constants import CENSOR_TOKEN
from netbox.graphql.schema import schema as root_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from users.models import User
from utilities.testing import APITestCase

from netbox_facts.api.serializers import CollectionPlanSerializer, FactsReportEntrySerializer
from netbox_facts.api.views import FactsReportEntryViewSet
from netbox_facts.choices import (
    CollectionTypeChoices,
    ConnectionTargetChoices,
    EntryActionChoices,
    EntryStatusChoices,
)
from netbox_facts.filtersets import FactsReportEntryFilterSet
from netbox_facts.graphql.types import FactsReportEntryType
from netbox_facts.models import CollectionPlan, FactsReport, FactsReportEntry


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
        for field_name in ("interval", "scheduled_at", "connection_target"):
            self.assertFalse(fields[field_name].read_only, msg=field_name)

    def test_run_as_cannot_be_set_through_the_api(self):
        """Scheduled runs are enqueued as run_as with no superuser check, so a
        plan editor must not be able to pick whose credentials they run under.
        """
        plan = CollectionPlan.objects.create(
            name="Attribution Plan",
            collector_type=CollectionTypeChoices.TYPE_ARP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        target = User.objects.create_user(username="attribution-target")

        serializer = CollectionPlanSerializer(
            instance=plan,
            data={"run_as": target.pk, "interval": 30},
            partial=True,
            context={"request": Request(APIRequestFactory().get("/"))},
        )
        self.assertTrue(serializer.is_valid(), msg=serializer.errors)
        serializer.save()

        plan.refresh_from_db()
        self.assertIsNone(plan.run_as)
        self.assertEqual(plan.interval, 30)


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


class FactsReportEntryViewSetWiringTest(TestCase):
    """The entry viewset must be read-only and reuse the existing API plumbing (#151)."""

    def test_viewset_reuses_existing_serializer_and_filterset(self):
        """The viewset is bound to the entry model, serializer and filterset."""
        self.assertIs(FactsReportEntryViewSet.queryset.model, FactsReportEntry)
        self.assertIs(FactsReportEntryViewSet.serializer_class, FactsReportEntrySerializer)
        self.assertIs(FactsReportEntryViewSet.filterset_class, FactsReportEntryFilterSet)

    def test_viewset_exposes_no_write_handlers(self):
        """Entries are mutated through the report apply/skip actions only."""
        for handler in ("create", "update", "partial_update", "destroy", "bulk_update", "bulk_destroy"):
            self.assertFalse(hasattr(FactsReportEntryViewSet, handler), msg=handler)

    def test_queryset_is_restrictable(self):
        """Object-level permission enforcement requires a restrictable queryset."""
        self.assertTrue(hasattr(FactsReportEntryViewSet.queryset, "restrict"))


class FactsReportEntryAPITest(APITestCase):
    """The entry list endpoint must expose filterable entries to API clients (#151)."""

    model = FactsReportEntry
    view_namespace = "plugins-api:netbox_facts"
    user_permissions = ("netbox_facts.view_factsreportentry",)

    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="Entry Site", slug="entry-site")
        manufacturer = Manufacturer.objects.create(name="EntryMfg", slug="entrymfg")
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model="EntryModel", slug="entrymodel")
        role = DeviceRole.objects.create(name="EntryRole", slug="entryrole")
        device = Device.objects.create(
            name="entry-dev",
            site=site,
            device_type=device_type,
            role=role,
            status=DeviceStatusChoices.STATUS_ACTIVE,
        )
        plan = CollectionPlan.objects.create(
            name="Entry Plan",
            collector_type=CollectionTypeChoices.TYPE_ARP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        cls.report = FactsReport.objects.create(collection_plan=plan)
        cls.pending_entry = FactsReportEntry.objects.create(
            report=cls.report,
            action=EntryActionChoices.ACTION_NEW,
            status=EntryStatusChoices.STATUS_PENDING,
            collector_type=CollectionTypeChoices.TYPE_ARP,
            device=device,
            object_repr="MACAddress AA:BB:CC:DD:EE:01",
        )
        cls.applied_entry = FactsReportEntry.objects.create(
            report=cls.report,
            action=EntryActionChoices.ACTION_NEW,
            status=EntryStatusChoices.STATUS_APPLIED,
            collector_type=CollectionTypeChoices.TYPE_ARP,
            device=device,
            object_repr="MACAddress AA:BB:CC:DD:EE:02",
        )
        FactsReportEntry.objects.create(
            report=FactsReport.objects.create(collection_plan=plan),
            action=EntryActionChoices.ACTION_NEW,
            status=EntryStatusChoices.STATUS_APPLIED,
            collector_type=CollectionTypeChoices.TYPE_ARP,
            device=device,
            object_repr="MACAddress AA:BB:CC:DD:EE:03",
        )

    def test_list_entries_filtered_by_status(self):
        """Clients can discover the PKs of pending entries to apply or skip."""
        url = f"{self._get_list_url()}?status={EntryStatusChoices.STATUS_PENDING}"
        response = self.client.get(url, **self.header)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([entry["id"] for entry in response.data["results"]], [self.pending_entry.pk])

    def test_entries_are_filtered_by_report(self):
        """Listing the entries of one report is what the endpoint exists for."""
        filtered = FactsReportEntryFilterSet(
            {"report": str(self.report.pk)},
            queryset=FactsReportEntry.objects.all(),
        ).qs

        self.assertCountEqual(
            [entry.pk for entry in filtered],
            [self.pending_entry.pk, self.applied_entry.pk],
        )


class GraphQLReportEntryPermissionTest(TestCase):
    """GraphQL entry queries must honor object permissions (#151).

    FactsReportEntry keeps Django's default manager, so the object type has to
    restrict the queryset itself.
    """

    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="GraphQL Site", slug="graphql-site")
        manufacturer = Manufacturer.objects.create(name="GraphQLMfg", slug="graphqlmfg")
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model="GraphQLModel", slug="graphqlmodel")
        role = DeviceRole.objects.create(name="GraphQLRole", slug="graphqlrole")
        device = Device.objects.create(
            name="graphql-dev",
            site=site,
            device_type=device_type,
            role=role,
            status=DeviceStatusChoices.STATUS_ACTIVE,
        )
        plan = CollectionPlan.objects.create(
            name="GraphQL Plan",
            collector_type=CollectionTypeChoices.TYPE_ARP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        report = FactsReport.objects.create(collection_plan=plan)
        FactsReportEntry.objects.create(
            report=report,
            action=EntryActionChoices.ACTION_NEW,
            status=EntryStatusChoices.STATUS_PENDING,
            collector_type=CollectionTypeChoices.TYPE_ARP,
            device=device,
            object_repr="MACAddress AA:BB:CC:DD:EE:03",
        )

    def test_entries_are_hidden_from_users_without_permission(self):
        """A user without view permission sees no entries."""
        user = User.objects.create_user(username="graphqluser")
        info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))

        queryset = FactsReportEntryType.get_queryset(FactsReportEntry.objects.all(), info)

        self.assertEqual(queryset.count(), 0)


class GraphQLSchemaTest(TestCase):
    """The plugin models must be queryable through NetBox's GraphQL schema (#151)."""

    PLUGIN_TYPE_NAMES = (
        "FactsMACAddressType",
        "FactsMACVendorType",
        "FactsCollectionPlanType",
        "FactsReportType",
        "FactsReportEntryType",
    )

    def test_plugin_types_are_registered(self):
        """Every plugin model is exposed as a GraphQL object type."""
        for type_name in self.PLUGIN_TYPE_NAMES:
            self.assertIsNotNone(root_schema.get_type_by_name(type_name), msg=type_name)

    def test_napalm_args_are_not_exposed(self):
        """Stored NAPALM credentials never reach the GraphQL schema."""
        plan_type = root_schema.get_type_by_name("FactsCollectionPlanType")
        self.assertIsNotNone(plan_type)
        self.assertNotIn("napalm_args", [field.name for field in plan_type.fields])

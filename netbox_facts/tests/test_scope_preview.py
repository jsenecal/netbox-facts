"""Tests for collection plan scope resolution, readiness and the empty-scope guard (#145)."""

import copy

from dcim.choices import DeviceStatusChoices
from dcim.models import (
    Device,
    Location,
    Platform,
    Region,
    SiteGroup,
)
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from extras.models import Tag
from ipam.models import IPAddress
from tenancy.models import Tenant, TenantGroup

from netbox_facts.choices import CollectionTypeChoices, ConnectionTargetChoices
from netbox_facts.forms import CollectionPlanImportForm, CollectorForm
from netbox_facts.models import CollectionPlan
from netbox_facts.models.collection_plan import (
    MAX_URL_PKS_PER_DIMENSION,
    SCOPE_DIMENSIONS,
    exceeds_scope_warning_threshold,
)
from netbox_facts.tests.test_helpers import CollectorTestMixin


def plugins_config(**overrides):
    """Return a copy of PLUGINS_CONFIG with the plugin settings overridden."""
    config = copy.deepcopy(settings.PLUGINS_CONFIG)
    config["netbox_facts"].update(overrides)
    return config


class ScopeFixtureMixin(CollectorTestMixin):
    """Builds one object per scoping dimension plus the plan factory."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.region = Region.objects.create(name="Scope Region", slug="scope-region")
        cls.site_group = SiteGroup.objects.create(name="Scope Site Group", slug="scope-site-group")
        cls.location = Location.objects.create(name="Scope Location", slug="scope-location", site=cls.site)
        cls.platform = Platform.objects.create(name="Scope Platform", slug="scope-platform")
        cls.tenant_group = TenantGroup.objects.create(name="Scope Tenant Group", slug="scope-tenant-group")
        cls.tenant = Tenant.objects.create(name="Scope Tenant", slug="scope-tenant")
        cls.tag = Tag.objects.create(name="Scope Tag", slug="scope-tag")
        cls.device = Device.objects.create(
            name="scope-dev",
            site=cls.site,
            device_type=cls.device_type,
            role=cls.role,
            status=DeviceStatusChoices.STATUS_ACTIVE,
        )
        cls.scope_objects = {
            "devices": cls.device,
            "regions": cls.region,
            "site_groups": cls.site_group,
            "sites": cls.site,
            "locations": cls.location,
            "device_types": cls.device_type,
            "roles": cls.role,
            "platforms": cls.platform,
            "tenant_groups": cls.tenant_group,
            "tenants": cls.tenant,
            "tags": cls.tag,
        }

    def _unscoped_plan(self, **kwargs):
        """Return an unsaved plan carrying no scoping dimension at all."""
        defaults = {
            "name": f"Unscoped-{id(self)}",
            "collector_type": CollectionTypeChoices.TYPE_ARP,
            "napalm_driver": "junos",
            "device_status": [],
        }
        defaults.update(kwargs)
        return CollectionPlan(**defaults)


class ScopeGuardTest(ScopeFixtureMixin, TestCase):
    """clean() must refuse a plan that would silently target every device (#145)."""

    def test_unscoped_plan_is_rejected(self):
        plan = self._unscoped_plan()

        with self.assertRaises(ValidationError) as context:
            plan.clean()

        self.assertIn("every device", str(context.exception))

    def test_allow_unscoped_bypasses_the_guard(self):
        plan = self._unscoped_plan(allow_unscoped=True)

        plan.clean()

        self.assertFalse(plan.has_scope())

    def test_device_status_alone_satisfies_the_guard(self):
        plan = self._unscoped_plan(device_status=[DeviceStatusChoices.STATUS_ACTIVE])

        plan.clean()

        self.assertTrue(plan.has_scope())

    def test_each_scope_dimension_satisfies_the_guard(self):
        """Every dimension get_devices_queryset() honors must also count as scope."""
        for dimension in SCOPE_DIMENSIONS:
            with self.subTest(dimension=dimension.field):
                plan = self._create_plan(
                    name=f"Scoped-{dimension.field}",
                    device_status=[],
                )
                getattr(plan, dimension.field).set([self.scope_objects[dimension.field]])

                plan.clean()

                self.assertTrue(plan.has_scope())

    def test_staged_scope_satisfies_the_guard_before_save(self):
        """Forms and serializers stage m2m values on _m2m_values before full_clean()."""
        plan = self._unscoped_plan()
        plan._m2m_values = {"sites": [self.site]}

        plan.clean()

        self.assertTrue(plan.has_scope())

    def test_staged_empty_scope_overrides_the_stored_scope(self):
        """Clearing every dimension on a saved plan must trip the guard."""
        plan = self._create_plan(name="Cleared Scope", device_status=[])
        plan.sites.set([self.site])
        plan.device_status = []
        plan._m2m_values = {dimension.field: [] for dimension in SCOPE_DIMENSIONS}

        with self.assertRaises(ValidationError):
            plan.clean()

    def test_napalm_args_normalization_still_runs(self):
        """The guard must not short-circuit the existing clean() behavior."""
        plan = self._unscoped_plan(
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
            napalm_args="invalid",
        )

        plan.clean()

        self.assertEqual(plan.napalm_args, {})


class ScopeWarningThresholdTest(TestCase):
    """The save-time warning fires strictly above the configured threshold (#145)."""

    def test_count_below_threshold_does_not_warn(self):
        with override_settings(PLUGINS_CONFIG=plugins_config(scope_warning_threshold=2)):
            self.assertFalse(exceeds_scope_warning_threshold(1))

    def test_count_at_threshold_does_not_warn(self):
        with override_settings(PLUGINS_CONFIG=plugins_config(scope_warning_threshold=2)):
            self.assertFalse(exceeds_scope_warning_threshold(2))

    def test_count_above_threshold_warns(self):
        with override_settings(PLUGINS_CONFIG=plugins_config(scope_warning_threshold=2)):
            self.assertTrue(exceeds_scope_warning_threshold(3))

    def test_zero_threshold_disables_the_warning(self):
        with override_settings(PLUGINS_CONFIG=plugins_config(scope_warning_threshold=0)):
            self.assertFalse(exceeds_scope_warning_threshold(10_000))


class ScopeReadinessTest(ScopeFixtureMixin, TestCase):
    """Matched devices without a usable IP for the plan target must be counted (#145)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.primary_only = cls._make_device("readiness-primary", primary="10.10.0.1/24")
        cls.oob_only = cls._make_device("readiness-oob", oob="10.20.0.1/24")
        cls.no_ip = cls._make_device("readiness-none")

    @classmethod
    def _make_device(cls, name, primary=None, oob=None):
        device = Device.objects.create(
            name=name,
            site=cls.site,
            device_type=cls.device_type,
            role=cls.role,
            status=DeviceStatusChoices.STATUS_ACTIVE,
        )
        if primary:
            device.primary_ip4 = IPAddress.objects.create(address=primary)
        if oob:
            device.oob_ip = IPAddress.objects.create(address=oob)
        device.save()
        return device

    def _readiness_plan(self, target):
        plan = self._create_plan(
            name=f"Readiness-{target}",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
            connection_target=target,
        )
        plan.sites.set([self.site])
        return plan

    def test_matched_device_count_counts_the_resolved_scope(self):
        plan = self._readiness_plan(ConnectionTargetChoices.TARGET_PRIMARY)

        self.assertEqual(plan.get_matched_device_count(), plan.get_devices_queryset().count())

    def test_primary_target_flags_devices_without_a_primary_ip(self):
        plan = self._readiness_plan(ConnectionTargetChoices.TARGET_PRIMARY)

        unready = set(plan.get_unready_devices())

        self.assertIn(self.oob_only, unready)
        self.assertIn(self.no_ip, unready)
        self.assertNotIn(self.primary_only, unready)

    def test_oob_target_flags_devices_without_an_oob_ip(self):
        plan = self._readiness_plan(ConnectionTargetChoices.TARGET_OOB)

        unready = set(plan.get_unready_devices())

        self.assertIn(self.primary_only, unready)
        self.assertIn(self.no_ip, unready)
        self.assertNotIn(self.oob_only, unready)

    def test_fallback_targets_accept_either_address(self):
        for target in (
            ConnectionTargetChoices.TARGET_PRIMARY_THEN_OOB,
            ConnectionTargetChoices.TARGET_OOB_THEN_PRIMARY,
        ):
            with self.subTest(target=target):
                plan = self._readiness_plan(target)

                unready = set(plan.get_unready_devices())

                self.assertIn(self.no_ip, unready)
                self.assertNotIn(self.primary_only, unready)
                self.assertNotIn(self.oob_only, unready)

    def test_unready_devices_can_be_sampled(self):
        """The detail page names a handful of offenders, never the whole list."""
        plan = self._readiness_plan(ConnectionTargetChoices.TARGET_PRIMARY)

        self.assertEqual(len(plan.get_unready_devices(limit=1)), 1)

    def test_devices_list_url_carries_the_scope(self):
        plan = self._readiness_plan(ConnectionTargetChoices.TARGET_PRIMARY)
        plan.roles.set([self.role])

        url = plan.get_devices_list_url()

        self.assertIn(f"site_id={self.site.pk}", url)
        self.assertIn(f"role_id={self.role.pk}", url)
        self.assertIn(f"status={DeviceStatusChoices.STATUS_ACTIVE}", url)

    def test_scope_warning_reports_the_resolved_count(self):
        plan = self._readiness_plan(ConnectionTargetChoices.TARGET_PRIMARY)

        with override_settings(PLUGINS_CONFIG=plugins_config(scope_warning_threshold=1)):
            warning = plan.get_scope_warning()

        self.assertIn(str(plan.get_matched_device_count()), warning)

    def test_no_scope_warning_below_the_threshold(self):
        plan = self._readiness_plan(ConnectionTargetChoices.TARGET_PRIMARY)

        with override_settings(PLUGINS_CONFIG=plugins_config(scope_warning_threshold=500)):
            self.assertEqual(plan.get_scope_warning(), "")


class ScopeLinkCapTest(ScopeFixtureMixin, TestCase):
    """get_devices_list_url() must not serialize an unbounded pk list into a URL (#145)."""

    def test_small_plan_still_yields_the_filtered_url(self):
        plan = self._create_plan(name="Small Scope", device_status=[])
        plan.sites.set([self.site])

        url = plan.get_devices_list_url()

        self.assertIsNotNone(url)
        self.assertIn(f"site_id={self.site.pk}", url)

    def test_plan_exceeding_the_cap_returns_no_url(self):
        devices = Device.objects.bulk_create(
            [
                Device(
                    name=f"cap-device-{i}",
                    site=self.site,
                    device_type=self.device_type,
                    role=self.role,
                    status=DeviceStatusChoices.STATUS_ACTIVE,
                )
                for i in range(MAX_URL_PKS_PER_DIMENSION + 1)
            ]
        )
        plan = self._create_plan(name="Huge Scope", device_status=[])
        plan.devices.set(devices)

        self.assertIsNone(plan.get_devices_list_url())


class CollectionPlanImportFormTest(ScopeFixtureMixin, TestCase):
    """CSV-imported plans must be scopeable, and unscoped ones rejected (#145)."""

    def _import_data(self, **overrides):
        data = {
            "name": "Imported Plan",
            "collector_type": CollectionTypeChoices.TYPE_ARP,
            "napalm_driver": "junos",
            "connection_target": ConnectionTargetChoices.TARGET_PRIMARY,
        }
        data.update(overrides)
        return data

    def test_import_form_exposes_every_scope_column(self):
        fields = CollectionPlanImportForm().fields

        for dimension in SCOPE_DIMENSIONS:
            self.assertIn(dimension.field, fields, msg=dimension.field)
        self.assertIn("device_status", fields)
        self.assertIn("allow_unscoped", fields)

    def test_importing_a_scoped_plan_stores_the_scope(self):
        form = CollectionPlanImportForm(
            data=self._import_data(
                sites=self.site.name,
                roles=self.role.name,
                device_status=DeviceStatusChoices.STATUS_ACTIVE,
            )
        )

        self.assertTrue(form.is_valid(), msg=form.errors)
        plan = form.save()

        self.assertEqual(list(plan.sites.all()), [self.site])
        self.assertEqual(list(plan.roles.all()), [self.role])
        self.assertEqual(plan.device_status, [DeviceStatusChoices.STATUS_ACTIVE])

    def test_unscoped_import_row_is_rejected(self):
        form = CollectionPlanImportForm(data=self._import_data())

        self.assertFalse(form.is_valid())
        self.assertIn("every device", str(form.errors))

    def test_allow_unscoped_column_permits_a_fleet_wide_import(self):
        form = CollectionPlanImportForm(data=self._import_data(allow_unscoped="true"))

        self.assertTrue(form.is_valid(), msg=form.errors)
        self.assertTrue(form.save().allow_unscoped)


class CollectorFormScopeTest(ScopeFixtureMixin, TestCase):
    """The edit form must surface the resolved scope and honor the guard (#145)."""

    def test_form_shows_the_matched_device_count_for_a_saved_plan(self):
        plan = self._create_plan(name="Preview Plan", device_status=[DeviceStatusChoices.STATUS_ACTIVE])
        plan.sites.set([self.site])

        form = CollectorForm(instance=plan)

        self.assertIn(str(plan.get_matched_device_count()), form.initial["matched_devices"])

    def test_form_rejects_a_submission_that_clears_every_dimension(self):
        plan = self._create_plan(name="Cleared Plan", device_status=[DeviceStatusChoices.STATUS_ACTIVE])
        plan.sites.set([self.site])

        form = CollectorForm(
            instance=plan,
            data={
                "name": plan.name,
                "priority": plan.priority,
                "collector_type": plan.collector_type,
                "napalm_driver": plan.napalm_driver,
                "connection_target": plan.connection_target,
                "enabled": True,
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn("every device", str(form.errors))

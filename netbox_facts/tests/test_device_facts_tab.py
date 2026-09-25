"""Tests for the Device Facts tab and the pending-changes dashboard widget (#150)."""

from datetime import timedelta

from dcim.models import Device
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone
from django.utils.html import escape
from django.utils.module_loading import import_string
from netbox.registry import registry

from netbox_facts.choices import (
    CollectionTypeChoices,
    EntryActionChoices,
    EntryStatusChoices,
    ReportStatusChoices,
)
from netbox_facts.dashboard import (
    REVIEW_REPORT_STATUSES,
    PendingFactsChangesWidget,
    pending_facts_counts,
    review_list_url,
)
from netbox_facts.device_views import (
    DeviceFactsView,
    last_collected_by_type,
    pending_entries,
    plans_covering_device,
)
from netbox_facts.models import FactsReport, FactsReportEntry
from netbox_facts.tests.test_helpers import CollectorTestMixin


def registered_view(model, name=""):
    """Return the view class register_model_view() recorded for a model under `name`.

    Newer cores ship utilities.views.get_view() for this, but NetBox 4.5 does
    not, and the plugin supports 4.5 through 4.7. The registry layout read here
    is instead the one get_model_urls() has consumed since long before that
    helper existed, so it resolves the same view on every supported core. A
    registration may hold either the view class or its dotted path -- that is
    the branch get_model_urls() takes -- so both forms are resolved.

    registry["views"] is a defaultdict, so membership is probed with .get() to
    avoid a test inserting empty stores into a global registry.
    """
    registrations = registry["views"].get(model._meta.app_label, {}).get(model._meta.model_name, [])
    for registration in registrations:
        if registration["name"] == name:
            view = registration["view"]
            return import_string(view) if isinstance(view, str) else view
    return None


class DeviceFactsFixtureMixin(CollectorTestMixin):
    """Two devices plus report and entry factories sharing one lazily created plan."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = get_user_model().objects.create_user(username="facts-tab-user", is_superuser=True)

    def setUp(self):
        super().setUp()
        self.device = self._create_device("facts-tab-dev1")
        self.other_device = self._create_device("facts-tab-dev2")

    def _request(self):
        """Return a request carrying the user the views and widget restrict against."""
        request = RequestFactory().get("/")
        request.user = self.user
        return request

    def _default_plan(self):
        """Return the shared ARP plan, created on first use.

        Built lazily so the tests that assert which plans cover a device
        are not handed an unscoped plan they never asked for.
        """
        if not hasattr(self, "_shared_plan"):
            self._shared_plan = self._create_plan(
                name="Facts Tab Plan",
                collector_type=CollectionTypeChoices.TYPE_ARP,
            )
        return self._shared_plan

    def _site_scoped_plan(self, name, **kwargs):
        """Return a plan scoped to the fixture site, so its scope covers both devices."""
        plan = self._create_plan(name=name, device_status=[], **kwargs)
        plan.sites.set([self.site])
        return plan

    def _create_report(self, plan=None, **kwargs):
        return FactsReport.objects.create(collection_plan=plan or self._default_plan(), **kwargs)

    def _create_entry(self, report, device=None, **kwargs):
        defaults = {
            "action": EntryActionChoices.ACTION_NEW,
            "collector_type": report.collection_plan.collector_type,
            "object_repr": "MACAddress AA:BB:CC:DD:EE:FF",
        }
        defaults.update(kwargs)
        return FactsReportEntry.objects.create(report=report, device=device or self.device, **defaults)


class DeviceEntriesAccessorTest(DeviceFactsFixtureMixin, TestCase):
    """The entry device FK must expose a reverse accessor (#150)."""

    def test_device_exposes_its_own_entries(self):
        report = self._create_report()
        mine = self._create_entry(report)
        self._create_entry(report, device=self.other_device)

        self.assertEqual(list(self.device.facts_entries.all()), [mine])

    def test_pending_entries_excludes_resolved_entries(self):
        report = self._create_report()
        pending = self._create_entry(report)
        self._create_entry(report, status=EntryStatusChoices.STATUS_APPLIED)
        self._create_entry(report, status=EntryStatusChoices.STATUS_SKIPPED)

        self.assertEqual(list(pending_entries(self.device)), [pending])


class DeviceFactsTabRegistrationTest(DeviceFactsFixtureMixin, TestCase):
    """The Facts tab must be attached to dcim.Device with a pending-count badge (#150)."""

    def test_view_is_registered_against_device(self):
        self.assertIs(registered_view(Device, "facts"), DeviceFactsView)

    def test_tab_requires_the_report_view_permission(self):
        self.assertEqual(DeviceFactsView.tab.permission, "netbox_facts.view_factsreport")
        self.assertEqual(str(DeviceFactsView.tab.label), "Facts")

    def test_tab_badge_counts_pending_entries_for_the_device(self):
        report = self._create_report()
        self._create_entry(report)
        self._create_entry(report)
        self._create_entry(report, status=EntryStatusChoices.STATUS_APPLIED)
        self._create_entry(report, device=self.other_device)

        self.assertEqual(DeviceFactsView.tab.badge(self.device), 2)

    def test_tab_is_hidden_on_a_device_with_no_facts_data(self):
        """A device the plugin has never recorded anything for gets no tab at all."""
        self.assertIsNone(DeviceFactsView.tab.render(self.device))

    def test_tab_is_visible_once_the_device_has_entries_even_if_none_are_pending(self):
        """Freshness and plan coverage are worth reaching on a collected-but-clean device."""
        self._create_entry(self._create_report(), status=EntryStatusChoices.STATUS_APPLIED)

        rendered = DeviceFactsView.tab.render(self.device)

        self.assertIsNotNone(rendered)
        self.assertEqual(rendered["badge"], 0)

    def test_tab_visibility_is_scoped_to_the_device(self):
        """Another device's entries must not surface a tab on this one."""
        self._create_entry(self._create_report(), device=self.other_device)

        self.assertIsNone(DeviceFactsView.tab.render(self.device))
        self.assertIsNotNone(DeviceFactsView.tab.render(self.other_device))

    def test_tab_is_visible_with_pending_entries(self):
        self._create_entry(self._create_report())

        rendered = DeviceFactsView.tab.render(self.device)

        self.assertIsNotNone(rendered)
        self.assertEqual(rendered["badge"], 1)

    def test_children_are_the_devices_pending_entries(self):
        report = self._create_report()
        pending = self._create_entry(report)
        self._create_entry(report, status=EntryStatusChoices.STATUS_APPLIED)
        self._create_entry(report, device=self.other_device)

        children = DeviceFactsView().get_children(self._request(), self.device)

        self.assertEqual(list(children), [pending])

    def test_extra_context_carries_freshness_and_covering_plans(self):
        plan = self._site_scoped_plan("Context Plan", collector_type=CollectionTypeChoices.TYPE_ARP)
        self._create_entry(self._create_report(plan))

        context = DeviceFactsView().get_extra_context(None, self.device)

        self.assertEqual(context["covering_plans"], [plan])
        self.assertFalse(context["plans_truncated"])
        self.assertEqual(
            [row["collector_type"] for row in context["last_collected"]],
            [CollectionTypeChoices.TYPE_ARP],
        )


class LastCollectedByTypeTest(DeviceFactsFixtureMixin, TestCase):
    """Per-collector-type freshness is derived from the device's own entries (#150)."""

    def test_reports_the_latest_timestamp_per_collector_type(self):
        older = timezone.now() - timedelta(days=2)
        newer = timezone.now() - timedelta(hours=1)
        arp_plan = self._create_plan(name="ARP Plan", collector_type=CollectionTypeChoices.TYPE_ARP)
        lldp_plan = self._create_plan(name="LLDP Plan", collector_type=CollectionTypeChoices.TYPE_LLDP)
        newer_report = self._create_report(arp_plan, completed_at=newer)
        self._create_entry(newer_report)
        # Two more ARP entries, created last and reported oldest, so a collapse
        # to one row per type is what produces the answer -- not row order.
        older_report = self._create_report(arp_plan, completed_at=older)
        self._create_entry(older_report)
        self._create_entry(older_report)
        self._create_entry(self._create_report(lldp_plan, completed_at=older))

        collected = last_collected_by_type(self.device)
        rows = {row["collector_type"]: row["last_collected"] for row in collected}

        self.assertEqual(len(collected), 2)
        self.assertEqual(rows[CollectionTypeChoices.TYPE_ARP], newer)
        self.assertEqual(rows[CollectionTypeChoices.TYPE_LLDP], older)

    def test_falls_back_to_report_creation_when_not_completed(self):
        report = self._create_report(completed_at=None)
        self._create_entry(report)

        rows = last_collected_by_type(self.device)

        self.assertEqual(rows[0]["last_collected"], report.created)

    def test_rows_follow_the_declared_collector_type_order(self):
        arp_plan = self._create_plan(name="Ordered ARP", collector_type=CollectionTypeChoices.TYPE_ARP)
        lldp_plan = self._create_plan(name="Ordered LLDP", collector_type=CollectionTypeChoices.TYPE_LLDP)
        # LLDP entries are created first so insertion order cannot be what orders the result.
        self._create_entry(self._create_report(lldp_plan))
        self._create_entry(self._create_report(arp_plan))

        types = [row["collector_type"] for row in last_collected_by_type(self.device)]

        self.assertEqual(types, [CollectionTypeChoices.TYPE_ARP, CollectionTypeChoices.TYPE_LLDP])

    def test_rows_carry_the_collector_type_label(self):
        self._create_entry(self._create_report())

        self.assertEqual(last_collected_by_type(self.device)[0]["label"], "ARP")

    def test_other_devices_entries_do_not_leak_in(self):
        self._create_entry(self._create_report(), device=self.other_device)

        self.assertEqual(last_collected_by_type(self.device), [])


class PlansCoveringDeviceTest(DeviceFactsFixtureMixin, TestCase):
    """Which plans would collect this device, resolved within a bounded budget (#150)."""

    def test_returns_plans_whose_scope_resolves_to_the_device(self):
        covering = self._site_scoped_plan("Covering")
        missing = self._create_plan(name="Missing", device_status=[])
        missing.devices.set([self.other_device])

        plans, truncated = plans_covering_device(self.device)

        self.assertEqual(plans, [covering])
        self.assertFalse(truncated)

    def test_disabled_plans_are_ignored(self):
        self._site_scoped_plan("Disabled", enabled=False)

        plans, _truncated = plans_covering_device(self.device)

        self.assertEqual(plans, [])

    def test_candidate_plans_are_capped_and_the_cap_is_reported(self):
        for index in range(3):
            self._site_scoped_plan(f"Capped {index}")

        plans, truncated = plans_covering_device(self.device, limit=2)

        self.assertEqual([plan.name for plan in plans], ["Capped 0", "Capped 1"])
        self.assertTrue(truncated)


class PendingFactsChangesWidgetTest(DeviceFactsFixtureMixin, TestCase):
    """The dashboard widget and the counts it renders (#150)."""

    def test_widget_is_registered_under_the_plugin_label(self):
        self.assertIs(registry["widgets"]["netbox_facts.PendingFactsChangesWidget"], PendingFactsChangesWidget)

    def test_counts_pending_entries_and_reports_awaiting_review(self):
        awaiting = self._create_report(status=ReportStatusChoices.STATUS_PENDING)
        self._create_entry(awaiting)
        self._create_entry(awaiting)
        partial = self._create_report(status=ReportStatusChoices.STATUS_PARTIAL)
        self._create_entry(partial)
        resolved = self._create_report(status=ReportStatusChoices.STATUS_APPLIED)
        self._create_entry(resolved, status=EntryStatusChoices.STATUS_APPLIED)

        counts = pending_facts_counts(self.user)

        self.assertEqual(counts["pending_entries"], 3)
        self.assertEqual(counts["pending_reports"], 2)

    def test_an_empty_report_counts_as_awaiting_review(self):
        """The report number has to agree with the list its link opens, which filters on status."""
        self._create_report(status=ReportStatusChoices.STATUS_PENDING)

        counts = pending_facts_counts(self.user)

        self.assertEqual(counts["pending_entries"], 0)
        self.assertEqual(counts["pending_reports"], 1)

    def test_review_statuses_are_the_ones_holding_undecided_entries(self):
        self.assertEqual(
            set(REVIEW_REPORT_STATUSES),
            {ReportStatusChoices.STATUS_PENDING, ReportStatusChoices.STATUS_PARTIAL},
        )

    def test_review_list_url_filters_on_every_review_status(self):
        url = review_list_url()

        for status in REVIEW_REPORT_STATUSES:
            self.assertIn(f"status={status}", url)

    def test_counts_are_zero_without_data(self):
        counts = pending_facts_counts(self.user)

        self.assertEqual(counts["pending_entries"], 0)
        self.assertEqual(counts["pending_reports"], 0)

    def test_render_puts_both_counts_behind_the_review_link(self):
        self._create_entry(self._create_report(status=ReportStatusChoices.STATUS_PENDING))

        html = PendingFactsChangesWidget().render(self._request())

        self.assertIn(escape(review_list_url()), html)
        self.assertIn("Pending entries", html)
        self.assertIn("Reports awaiting review", html)

"""Tests for the entry lifecycle transitions added in #141.

Entry review used to be a one-way street: apply_entries and skip_entries
both filter on pending, so a failed entry could never be retried and a
skipped entry could never be reconsidered. These tests cover the
retry/un-skip transitions, the POST-only views behind them (including
the per-row and select-all selection modes), the status-aware controls
the entry tabs render, and the REST mirrors.
"""

from django.test import TestCase as DjangoTestCase
from django.urls import reverse
from rest_framework import status as http_status
from utilities.testing import APITestCase, TestCase

from netbox_facts.api.views import FactsMutationThrottle, FactsReportViewSet
from netbox_facts.choices import (
    CollectionTypeChoices,
    EntryActionChoices,
    EntryStatusChoices,
    ReportStatusChoices,
)
from netbox_facts.helpers.applier import (
    apply_entries,
    retry_entries,
    skip_entries,
    unskip_entries,
)
from netbox_facts.models import FactsReport, FactsReportEntry
from netbox_facts.tests.test_applier import ApplierTestMixin

SERIAL = "RETRIED_SERIAL"
STALE_ERROR = "Device matching query does not exist"


def make_entry(report, device, status, **kwargs):
    """Create one inventory serial-change entry in the given status.

    The serial path is the cheapest apply handler that leaves an
    observable trace (the device serial), so a test can tell an entry
    that was applied from one that was only moved back to pending.
    """
    values = {
        "action": EntryActionChoices.ACTION_CHANGED,
        "collector_type": CollectionTypeChoices.TYPE_INVENTORY,
        "object_repr": f"Device {device.name}",
        "detected_values": {"serial_number": SERIAL},
        "current_values": {"serial_number": device.serial},
    }
    values.update(kwargs)
    return FactsReportEntry.objects.create(report=report, device=device, status=status, **values)


def make_failed_entry(report, device, **kwargs):
    """Create an entry carrying the residue of an earlier failed apply."""
    values = {
        "error_message": STALE_ERROR,
        "apply_error": {"error_type": "error", "__all__": [STALE_ERROR]},
    }
    values.update(kwargs)
    return make_entry(report, device, EntryStatusChoices.STATUS_FAILED, **values)


def make_unappliable_entry(report, device, status=EntryStatusChoices.STATUS_FAILED):
    """Create an entry whose apply handler always raises."""
    return make_entry(
        report,
        device,
        status,
        collector_type=CollectionTypeChoices.TYPE_LLDP,
        object_repr="Cable to nowhere",
        detected_values={},
        current_values={},
        error_message=STALE_ERROR,
        apply_error={"error_type": "error", "__all__": [STALE_ERROR]},
    )


class RetryEntriesTest(ApplierTestMixin, DjangoTestCase):
    """#141: retry_entries returns failed entries to pending and re-applies them."""

    def setUp(self):
        self.report = FactsReport.objects.create(collection_plan=self.plan)

    def test_retry_reapplies_a_failed_entry(self):
        """A retried entry is applied and loses the previous failure."""
        entry = make_failed_entry(self.report, self.device)

        applied, failed = retry_entries(self.report, [entry.pk])

        self.assertEqual((applied, failed), (1, 0))
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_APPLIED)
        self.assertIsNone(entry.apply_error)
        self.assertEqual(entry.error_message, "")
        self.device.refresh_from_db()
        self.assertEqual(self.device.serial, SERIAL)

    def test_retry_ignores_entries_that_did_not_fail(self):
        """Retry is failed-only: it never re-applies a resolved or pending entry."""
        pending = make_entry(self.report, self.device, EntryStatusChoices.STATUS_PENDING)
        skipped = make_entry(self.report, self.device, EntryStatusChoices.STATUS_SKIPPED)
        applied_entry = make_entry(self.report, self.device, EntryStatusChoices.STATUS_APPLIED)

        applied, failed = retry_entries(self.report, [pending.pk, skipped.pk, applied_entry.pk])

        self.assertEqual((applied, failed), (0, 0))
        for entry, expected in (
            (pending, EntryStatusChoices.STATUS_PENDING),
            (skipped, EntryStatusChoices.STATUS_SKIPPED),
            (applied_entry, EntryStatusChoices.STATUS_APPLIED),
        ):
            entry.refresh_from_db()
            self.assertEqual(entry.status, expected)
        self.device.refresh_from_db()
        self.assertNotEqual(self.device.serial, SERIAL)

    def test_retry_that_fails_again_records_a_fresh_apply_error(self):
        """A second failure re-records the error rather than leaving the stale one."""
        entry = make_unappliable_entry(self.report, self.device)

        applied, failed = retry_entries(self.report, [entry.pk])

        self.assertEqual((applied, failed), (0, 1))
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_FAILED)
        self.assertEqual(entry.apply_error["error_type"], "error")
        self.assertIn("Missing LLDP entry data", entry.error_message)
        self.assertNotIn(STALE_ERROR, entry.error_message)

    def test_retry_ignores_entries_from_another_report(self):
        """Entry PKs are validated against the report that owns them."""
        other_report = FactsReport.objects.create(collection_plan=self.plan)
        other_entry = make_failed_entry(other_report, self.device)

        applied, failed = retry_entries(self.report, [other_entry.pk])

        self.assertEqual((applied, failed), (0, 0))
        other_entry.refresh_from_db()
        self.assertEqual(other_entry.status, EntryStatusChoices.STATUS_FAILED)
        self.device.refresh_from_db()
        self.assertNotEqual(self.device.serial, SERIAL)


class UnskipEntriesTest(ApplierTestMixin, DjangoTestCase):
    """#141: unskip_entries returns skipped entries to pending without applying them."""

    def setUp(self):
        self.report = FactsReport.objects.create(collection_plan=self.plan)

    def test_unskip_returns_skipped_entries_to_pending(self):
        """Un-skipping restores review status only; nothing is written to NetBox."""
        entry = make_entry(self.report, self.device, EntryStatusChoices.STATUS_SKIPPED)

        count = unskip_entries(self.report, [entry.pk])

        self.assertEqual(count, 1)
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_PENDING)
        self.assertIsNone(entry.applied_at)
        self.device.refresh_from_db()
        self.assertNotEqual(self.device.serial, SERIAL)

    def test_unskip_ignores_entries_in_other_statuses(self):
        """Un-skip is skipped-only: a failed or applied entry is left alone."""
        pending = make_entry(self.report, self.device, EntryStatusChoices.STATUS_PENDING)
        failed = make_failed_entry(self.report, self.device)
        applied_entry = make_entry(self.report, self.device, EntryStatusChoices.STATUS_APPLIED)

        count = unskip_entries(self.report, [pending.pk, failed.pk, applied_entry.pk])

        self.assertEqual(count, 0)
        failed.refresh_from_db()
        applied_entry.refresh_from_db()
        self.assertEqual(failed.status, EntryStatusChoices.STATUS_FAILED)
        self.assertEqual(applied_entry.status, EntryStatusChoices.STATUS_APPLIED)

    def test_unskip_ignores_entries_from_another_report(self):
        """Entry PKs are validated against the report that owns them."""
        other_report = FactsReport.objects.create(collection_plan=self.plan)
        other_entry = make_entry(other_report, self.device, EntryStatusChoices.STATUS_SKIPPED)

        count = unskip_entries(self.report, [other_entry.pk])

        self.assertEqual(count, 0)
        other_entry.refresh_from_db()
        self.assertEqual(other_entry.status, EntryStatusChoices.STATUS_SKIPPED)

    def test_unskip_recomputes_the_report_status(self):
        """A report whose only entry returns to pending is pending again."""
        entry = make_entry(self.report, self.device, EntryStatusChoices.STATUS_PENDING)
        skip_entries(self.report, [entry.pk])
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatusChoices.STATUS_COMPLETED)

        unskip_entries(self.report, [entry.pk])

        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatusChoices.STATUS_PENDING)


class PendingOnlyGuardTest(ApplierTestMixin, DjangoTestCase):
    """#141: the new transitions must not loosen the pending-only gates."""

    def setUp(self):
        self.report = FactsReport.objects.create(collection_plan=self.plan)

    def test_apply_still_ignores_a_failed_entry(self):
        """apply_entries stays pending-only; retrying is the explicit verb."""
        entry = make_failed_entry(self.report, self.device)

        applied, failed = apply_entries(self.report, [entry.pk])

        self.assertEqual((applied, failed), (0, 0))
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_FAILED)
        self.device.refresh_from_db()
        self.assertNotEqual(self.device.serial, SERIAL)

    def test_skip_still_ignores_a_failed_entry(self):
        """skip_entries stays pending-only."""
        entry = make_failed_entry(self.report, self.device)

        count = skip_entries(self.report, [entry.pk])

        self.assertEqual(count, 0)
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_FAILED)


def action_url(name, report, query=""):
    """Build a report action URL, optionally carrying the tab's filters."""
    return reverse(f"plugins:netbox_facts:factsreport_{name}", kwargs={"pk": report.pk}) + query


class EntryActionViewTest(ApplierTestMixin, TestCase):
    """#141: the retry/un-skip views and the per-row and select-all selection modes."""

    user_permissions = ("netbox_facts.apply_factsreport",)

    def setUp(self):
        super().setUp()
        self.report = FactsReport.objects.create(collection_plan=self.plan)

    def test_retry_view_reapplies_selected_entries(self):
        entry = make_failed_entry(self.report, self.device)

        response = self.client.post(action_url("retry", self.report), {"pk": [entry.pk]})

        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_APPLIED)

    def test_unskip_view_returns_selected_entries_to_pending(self):
        entry = make_entry(self.report, self.device, EntryStatusChoices.STATUS_SKIPPED)

        response = self.client.post(action_url("unskip", self.report), {"pk": [entry.pk]})

        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_PENDING)

    def test_row_button_acts_on_its_own_entry_only(self):
        """A per-row button posts one PK and must not sweep in ticked checkboxes."""
        row = make_entry(self.report, self.device, EntryStatusChoices.STATUS_PENDING)
        ticked = make_entry(self.report, self.device, EntryStatusChoices.STATUS_PENDING)

        self.client.post(action_url("skip", self.report), {"row_pk": row.pk, "pk": [ticked.pk]})

        row.refresh_from_db()
        ticked.refresh_from_db()
        self.assertEqual(row.status, EntryStatusChoices.STATUS_SKIPPED)
        self.assertEqual(ticked.status, EntryStatusChoices.STATUS_PENDING)

    def test_get_request_does_not_mutate_entries(self):
        """The action views are POST-only; a GET just bounces to the report."""
        entry = make_failed_entry(self.report, self.device)

        response = self.client.get(action_url("retry", self.report))

        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_FAILED)

    def test_select_all_covers_every_entry_of_the_tab_status(self):
        """Select-all is resolved server side from the report plus the tab status."""
        failed = [make_failed_entry(self.report, self.device) for _ in range(3)]
        skipped = make_entry(self.report, self.device, EntryStatusChoices.STATUS_SKIPPED)

        self.client.post(
            action_url("retry", self.report),
            {"_all": "on", "entry_status": EntryStatusChoices.STATUS_FAILED},
        )

        for entry in failed:
            entry.refresh_from_db()
            self.assertEqual(entry.status, EntryStatusChoices.STATUS_APPLIED)
        skipped.refresh_from_db()
        self.assertEqual(skipped.status, EntryStatusChoices.STATUS_SKIPPED)

    def test_select_all_honors_the_tab_filters(self):
        """The filters shown on the tab narrow what select-all resolves."""
        inventory = make_failed_entry(self.report, self.device)
        lldp = make_unappliable_entry(self.report, self.device)

        self.client.post(
            action_url(
                "retry",
                self.report,
                query=f"?collector_type={CollectionTypeChoices.TYPE_INVENTORY}",
            ),
            {"_all": "on", "entry_status": EntryStatusChoices.STATUS_FAILED},
        )

        inventory.refresh_from_db()
        lldp.refresh_from_db()
        self.assertEqual(inventory.status, EntryStatusChoices.STATUS_APPLIED)
        self.assertEqual(lldp.status, EntryStatusChoices.STATUS_FAILED)
        self.assertEqual(lldp.error_message, STALE_ERROR)

    def test_select_all_is_scoped_to_the_posted_report(self):
        """Select-all never reaches another report's entries."""
        other_report = FactsReport.objects.create(collection_plan=self.plan)
        other_entry = make_failed_entry(other_report, self.device)

        self.client.post(
            action_url("retry", self.report),
            {"_all": "on", "entry_status": EntryStatusChoices.STATUS_FAILED},
        )

        other_entry.refresh_from_db()
        self.assertEqual(other_entry.status, EntryStatusChoices.STATUS_FAILED)

    def test_select_all_without_a_status_still_respects_the_transition_gate(self):
        """An unscoped select-all cannot retry an entry that never failed."""
        failed = make_failed_entry(self.report, self.device)
        pending = make_entry(self.report, self.device, EntryStatusChoices.STATUS_PENDING)

        self.client.post(action_url("retry", self.report), {"_all": "on"})

        failed.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(failed.status, EntryStatusChoices.STATUS_APPLIED)
        self.assertEqual(pending.status, EntryStatusChoices.STATUS_PENDING)


class EntryActionPermissionTest(ApplierTestMixin, TestCase):
    """#141: the new transitions are gated on apply_factsreport like apply/skip."""

    user_permissions = ()

    def setUp(self):
        super().setUp()
        self.report = FactsReport.objects.create(collection_plan=self.plan)

    def test_retry_requires_the_apply_permission(self):
        entry = make_failed_entry(self.report, self.device)

        response = self.client.post(action_url("retry", self.report), {"pk": [entry.pk]})

        self.assertEqual(response.status_code, 403)
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_FAILED)

    def test_unskip_requires_the_apply_permission(self):
        entry = make_entry(self.report, self.device, EntryStatusChoices.STATUS_SKIPPED)

        response = self.client.post(action_url("unskip", self.report), {"pk": [entry.pk]})

        self.assertEqual(response.status_code, 403)
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_SKIPPED)


def tab_url(status_value, report, query=""):
    """Build the URL of one per-status entry tab."""
    return reverse(f"plugins:netbox_facts:factsreport_entries_{status_value}", kwargs={"pk": report.pk}) + query


class EntryTabControlsTest(ApplierTestMixin, TestCase):
    """#141: each entry tab renders the controls its status can act on."""

    user_permissions = ("netbox_facts.view_factsreport", "netbox_facts.apply_factsreport")

    def setUp(self):
        super().setUp()
        self.report = FactsReport.objects.create(collection_plan=self.plan)

    def assert_controls(self, response, present, absent):
        content = response.content.decode()
        for name in present:
            self.assertIn(action_url(name, self.report), content)
        for name in absent:
            self.assertNotIn(action_url(name, self.report), content)

    def test_pending_tab_offers_apply_and_skip(self):
        make_entry(self.report, self.device, EntryStatusChoices.STATUS_PENDING)

        response = self.client.get(tab_url(EntryStatusChoices.STATUS_PENDING, self.report))

        self.assert_controls(response, present=("apply", "skip"), absent=("retry", "unskip"))

    def test_failed_tab_offers_retry(self):
        make_failed_entry(self.report, self.device)

        response = self.client.get(tab_url(EntryStatusChoices.STATUS_FAILED, self.report))

        self.assert_controls(response, present=("retry",), absent=("apply", "skip", "unskip"))

    def test_skipped_tab_offers_unskip(self):
        make_entry(self.report, self.device, EntryStatusChoices.STATUS_SKIPPED)

        response = self.client.get(tab_url(EntryStatusChoices.STATUS_SKIPPED, self.report))

        self.assert_controls(response, present=("unskip",), absent=("apply", "skip", "retry"))

    def test_applied_tab_offers_no_lifecycle_controls(self):
        make_entry(self.report, self.device, EntryStatusChoices.STATUS_APPLIED)

        response = self.client.get(tab_url(EntryStatusChoices.STATUS_APPLIED, self.report))

        self.assert_controls(response, present=(), absent=("apply", "skip", "retry", "unskip"))

    def test_row_button_carries_its_own_entry_pk(self):
        """Each row posts its own PK under a name the bulk checkboxes do not use."""
        entry = make_entry(self.report, self.device, EntryStatusChoices.STATUS_PENDING)

        response = self.client.get(tab_url(EntryStatusChoices.STATUS_PENDING, self.report))

        self.assertIn(f'name="row_pk" value="{entry.pk}"', response.content.decode())

    def test_multi_page_tab_offers_select_all(self):
        """Once the tab paginates, it offers to resolve the whole selection.

        NetBox's paginator folds up to five orphans into the last page, so
        a second page needs more than per_page + orphans entries.
        """
        for _ in range(12):
            make_failed_entry(self.report, self.device)

        response = self.client.get(tab_url(EntryStatusChoices.STATUS_FAILED, self.report, query="?per_page=6"))

        content = response.content.decode()
        self.assertIn('name="_all"', content)
        self.assertIn(f'name="entry_status" value="{EntryStatusChoices.STATUS_FAILED}"', content)


def api_action_url(name, report):
    """Build a report-level REST action URL."""
    return reverse(f"plugins-api:netbox_facts-api:factsreport-{name}", kwargs={"pk": report.pk})


class EntryLifecycleAPITest(ApplierTestMixin, APITestCase):
    """#141: retry and un-skip are mirrored as report-level REST actions."""

    user_permissions = ("netbox_facts.view_factsreport", "netbox_facts.add_factsreport")

    def setUp(self):
        super().setUp()
        self.report = FactsReport.objects.create(collection_plan=self.plan)

    def test_retry_action_reapplies_failed_entries(self):
        entry = make_failed_entry(self.report, self.device)

        response = self.client.post(
            api_action_url("retry", self.report),
            {"entries": [entry.pk]},
            format="json",
            **self.header,
        )

        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.data, {"applied": 1, "failed": 0})
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_APPLIED)

    def test_unskip_action_returns_entries_to_pending(self):
        entry = make_entry(self.report, self.device, EntryStatusChoices.STATUS_SKIPPED)

        response = self.client.post(
            api_action_url("unskip", self.report),
            {"entries": [entry.pk]},
            format="json",
            **self.header,
        )

        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.data, {"unskipped": 1})
        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_PENDING)

    def test_apply_and_skip_actions_keep_resolving_pending_entries(self):
        """The selection contract the new actions share is the one apply and skip use."""
        to_apply = make_entry(self.report, self.device, EntryStatusChoices.STATUS_PENDING)
        to_skip = make_entry(self.report, self.device, EntryStatusChoices.STATUS_PENDING)

        applied = self.client.post(
            api_action_url("apply", self.report),
            {"entries": [to_apply.pk]},
            format="json",
            **self.header,
        )
        skipped = self.client.post(
            api_action_url("skip", self.report),
            {"entries": [to_skip.pk]},
            format="json",
            **self.header,
        )

        self.assertEqual(applied.data, {"applied": 1, "failed": 0})
        self.assertEqual(skipped.data, {"skipped": 1})

    def test_actions_reject_entries_from_another_report(self):
        other_report = FactsReport.objects.create(collection_plan=self.plan)
        other_entry = make_failed_entry(other_report, self.device)

        for name in ("retry", "unskip"):
            with self.subTest(action=name):
                response = self.client.post(
                    api_action_url(name, self.report),
                    {"entries": [other_entry.pk]},
                    format="json",
                    **self.header,
                )
                self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)

        other_entry.refresh_from_db()
        self.assertEqual(other_entry.status, EntryStatusChoices.STATUS_FAILED)

    def test_actions_require_an_entry_list(self):
        for name in ("retry", "unskip"):
            with self.subTest(action=name):
                response = self.client.post(
                    api_action_url(name, self.report),
                    {"entries": []},
                    format="json",
                    **self.header,
                )
                self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)

    def test_mutating_actions_are_throttled(self):
        """retry and un-skip carry the same throttle as apply and skip."""
        for name in ("apply", "skip", "retry", "unskip"):
            with self.subTest(action=name):
                action = getattr(FactsReportViewSet, name)
                self.assertIn(FactsMutationThrottle, action.kwargs["throttle_classes"])


class EntryLifecycleAPIPermissionTest(ApplierTestMixin, APITestCase):
    """#141: the REST mirrors refuse a token without write permission."""

    user_permissions = ("netbox_facts.view_factsreport",)

    def setUp(self):
        super().setUp()
        self.report = FactsReport.objects.create(collection_plan=self.plan)

    def test_actions_refuse_a_read_only_token(self):
        entry = make_failed_entry(self.report, self.device)

        for name in ("retry", "unskip"):
            with self.subTest(action=name):
                response = self.client.post(
                    api_action_url(name, self.report),
                    {"entries": [entry.pk]},
                    format="json",
                    **self.header,
                )
                self.assertEqual(response.status_code, http_status.HTTP_403_FORBIDDEN)

        entry.refresh_from_db()
        self.assertEqual(entry.status, EntryStatusChoices.STATUS_FAILED)

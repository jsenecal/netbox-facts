"""Tests for the FactsReportEntry detail view and its display helpers (#139).

An entry stores the whole detected/current payload but had no page of its
own: the table rendered a flat markdown summary and nothing surfaced the
raw evidence or the structured apply failure. These tests cover the
plugin-owned pieces that page is built from -- the per-key diff
classification shared with the entry table, the apply-error display
shape, and the view's permission gate and context assembly.

The diff and apply-error helpers touch no ORM, so they are exercised
directly against lightweight stub entries rather than through a request.
"""

from types import SimpleNamespace

import pytest
from django.urls import resolve, reverse
from users.models import ObjectPermission
from utilities.testing import TestCase

from netbox_facts.choices import (
    CollectionTypeChoices,
    EntryActionChoices,
    EntryKindChoices,
    EntryStatusChoices,
)
from netbox_facts.helpers.entry_display import (
    ABSENT,
    CHANGE_ADDED,
    CHANGE_MODIFIED,
    CHANGE_REMOVED,
    GENERAL_ERROR_LABEL,
    build_apply_error_display,
    build_entry_diff,
)
from netbox_facts.models import FactsReport, FactsReportEntry
from netbox_facts.tests.test_helpers import CollectorTestMixin
from netbox_facts.views import FactsReportEntryView


def make_entry(detected_values=None, current_values=None, action=EntryActionChoices.ACTION_CHANGED):
    """Build the minimal stand-in the diff helper reads."""
    return SimpleNamespace(
        action=action,
        detected_values=detected_values or {},
        current_values=current_values or {},
    )


def rows_by_key(entry):
    return {row.key: row for row in build_entry_diff(entry)}


class TestEntryDiffClassification:
    """The per-key classification the detail page renders side by side."""

    def test_modified_key_carries_both_sides(self):
        row = rows_by_key(make_entry({"serial_number": "NEW"}, {"serial_number": "OLD"}))["serial_number"]

        assert row.change == CHANGE_MODIFIED
        assert row.current == "OLD"
        assert row.detected == "NEW"
        assert row.label == "serial"

    def test_unchanged_key_is_omitted(self):
        assert build_entry_diff(make_entry({"serial_number": "SAME"}, {"serial_number": "SAME"})) == []

    def test_detected_only_key_is_added(self):
        row = rows_by_key(make_entry({"mac_address": "AA:BB:CC:DD:EE:FF"}, {}))["mac_address"]

        assert row.change == CHANGE_ADDED
        assert row.current == ABSENT
        assert row.detected == "AA:BB:CC:DD:EE:FF"

    def test_current_only_key_is_removed(self):
        row = rows_by_key(make_entry({}, {"remote_as": "65000"}))["remote_as"]

        assert row.change == CHANGE_REMOVED
        assert row.current == "65000"
        assert row.detected == ABSENT

    def test_skip_fields_are_excluded(self):
        entry = make_entry({"raw_output": "new-blob", "name": "ge-0/0/0"}, {"raw_output": "old-blob"})

        assert build_entry_diff(entry) == []

    def test_new_entry_rows_are_all_added(self):
        rows = build_entry_diff(make_entry({"ip_address": "10.0.0.1/24"}, action=EntryActionChoices.ACTION_NEW))

        assert [(row.key, row.change, row.current) for row in rows] == [("ip_address", CHANGE_ADDED, ABSENT)]

    def test_stale_entry_rows_are_all_removed(self):
        rows = build_entry_diff(
            make_entry(current_values={"ip_address": "10.0.0.1/24"}, action=EntryActionChoices.ACTION_STALE)
        )

        assert [(row.key, row.change, row.detected) for row in rows] == [("ip_address", CHANGE_REMOVED, ABSENT)]

    def test_confirmed_entry_has_no_rows(self):
        entry = make_entry(
            {"serial_number": "SAME"},
            {"serial_number": "OTHER"},
            action=EntryActionChoices.ACTION_CONFIRMED,
        )

        assert build_entry_diff(entry) == []

    def test_rows_are_grouped_modified_then_added_then_removed(self):
        entry = make_entry(
            detected_values={"serial_number": "NEW", "mac_address": "AA:BB:CC:DD:EE:FF"},
            current_values={"serial_number": "OLD", "remote_as": "65000"},
        )

        assert [row.change for row in build_entry_diff(entry)] == [
            CHANGE_MODIFIED,
            CHANGE_ADDED,
            CHANGE_REMOVED,
        ]


class TestApplyErrorDisplay:
    """The structured apply failure, prepared for field-by-field rendering."""

    def test_validation_error_keeps_field_addressing(self):
        display = build_apply_error_display(
            {"error_type": "validation", "name": ["This field is required."], "serial": ["Too long."]}
        )

        assert display["is_validation"] is True
        assert {field["label"]: field["messages"] for field in display["fields"]} == {
            "name": ["This field is required."],
            "serial": ["Too long."],
        }

    def test_infrastructure_error_is_labeled_generally(self):
        display = build_apply_error_display({"error_type": "error", "__all__": ["device unreachable"]})

        assert display["is_validation"] is False
        assert display["fields"] == [{"label": GENERAL_ERROR_LABEL, "messages": ["device unreachable"]}]

    def test_bare_message_is_normalized_to_a_list(self):
        display = build_apply_error_display({"error_type": "error", "__all__": "device unreachable"})

        assert display["fields"] == [{"label": GENERAL_ERROR_LABEL, "messages": ["device unreachable"]}]

    def test_missing_error_type_is_treated_as_infrastructure(self):
        display = build_apply_error_display({"__all__": ["boom"]})

        assert display["is_validation"] is False

    @pytest.mark.parametrize("apply_error", [None, {}, {"error_type": "validation"}])
    def test_nothing_to_show_returns_none(self, apply_error):
        assert build_apply_error_display(apply_error) is None


class EntryDetailViewTestMixin(CollectorTestMixin):
    """Fixtures for the entry detail view: a report with a changed entry."""

    def setUp(self):
        super().setUp()
        self.device = self._create_device("entry-detail-dev")
        self.plan = self._create_plan(collector_type=CollectionTypeChoices.TYPE_INVENTORY)
        self.report = FactsReport.objects.create(collection_plan=self.plan)
        self.entry = self._create_entry(
            detected_values={"serial_number": "NEW123", "mac_address": "AA:BB:CC:DD:EE:FF"},
            current_values={"serial_number": "OLD123"},
        )

    def _create_entry(self, **kwargs):
        defaults = {
            "report": self.report,
            "action": EntryActionChoices.ACTION_CHANGED,
            "status": EntryStatusChoices.STATUS_PENDING,
            "collector_type": CollectionTypeChoices.TYPE_INVENTORY,
            "entry_kind": EntryKindChoices.KIND_DEVICE,
            "device": self.device,
            "object_repr": "Device entry-detail-dev",
        }
        defaults.update(kwargs)
        return FactsReportEntry.objects.create(**defaults)

    def _get(self, entry=None):
        return self.client.get(reverse("plugins:netbox_facts:factsreportentry", args=[(entry or self.entry).pk]))


class FactsReportEntryViewTest(EntryDetailViewTestMixin, TestCase):
    """The entry page assembles its diff, evidence and failure context."""

    user_permissions = ("netbox_facts.view_factsreport",)

    def test_entry_url_resolves_to_the_detail_view(self):
        match = resolve(reverse("plugins:netbox_facts:factsreportentry", args=[self.entry.pk]))

        self.assertIs(match.func.view_class, FactsReportEntryView)

    def test_page_renders_the_per_key_diff(self):
        response = self._get()

        self.assertEqual(response.status_code, 200)
        changes = {row.key: row.change for row in response.context["diff_rows"]}
        self.assertEqual(changes, {"serial_number": CHANGE_MODIFIED, "mac_address": CHANGE_ADDED})

    def test_raw_evidence_carries_both_payloads(self):
        response = self._get()

        self.assertIn("serial_number", response.context["detected_json"])
        self.assertIn("OLD123", response.context["current_json"])

    def test_failed_entry_surfaces_the_structured_apply_error(self):
        entry = self._create_entry(
            status=EntryStatusChoices.STATUS_FAILED,
            apply_error={"error_type": "validation", "serial": ["Too long."]},
            error_message="serial: Too long.",
        )

        response = self._get(entry)

        self.assertEqual(response.context["apply_error"]["fields"], [{"label": "serial", "messages": ["Too long."]}])

    def test_unfailed_entry_does_not_surface_a_stale_apply_error(self):
        """A leftover payload on a non-failed entry is not a current failure."""
        entry = self._create_entry(apply_error={"error_type": "error", "__all__": ["old news"]})

        self.assertIsNone(self._get(entry).context["apply_error"])

    def test_back_link_points_at_the_parent_report_tab(self):
        response = self._get()

        self.assertEqual(
            response.context["parent_tab_url"],
            reverse("plugins:netbox_facts:factsreport_entries_pending", args=[self.report.pk]),
        )

    def test_back_link_falls_back_to_the_report_without_a_status_tab(self):
        """An applying entry is listed on no tab, so it links to the report."""
        entry = self._create_entry(status=EntryStatusChoices.STATUS_APPLYING)

        self.assertEqual(self._get(entry).context["parent_tab_url"], self.report.get_absolute_url())


class FactsReportEntryViewPermissionTest(EntryDetailViewTestMixin, TestCase):
    """Entries carry no permissions of their own: the report's gate them."""

    user_permissions = ()

    def test_view_is_denied_without_the_report_view_permission(self):
        self.assertEqual(self._get().status_code, 403)

    def test_entry_of_an_unpermitted_report_is_not_found(self):
        """Report-level object permissions narrow which entries are reachable."""
        self.add_permissions("netbox_facts.view_factsreport")
        other_report = FactsReport.objects.create(collection_plan=self.plan)
        entry = self._create_entry(report=other_report)
        ObjectPermission.objects.filter(users=self.user).update(constraints={"pk": self.report.pk})

        self.assertEqual(self._get(entry).status_code, 404)
        self.assertEqual(self._get().status_code, 200)

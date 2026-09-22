"""Tests for the entry-review ergonomics of a facts report (#140).

Reviewing a report means working its per-status entry tabs, so those tabs
carry a real filter form (instead of hand-typed query parameters), keep
their place while entries move from pending to applied, and hand the
reviewer a CSV of whatever the current filters select.
"""

import csv

from django.test import TestCase
from django.urls import resolve, reverse
from netbox.object_actions import BulkExport
from utilities.testing import TestCase as NetBoxViewTestCase

from netbox_facts.choices import (
    CollectionTypeChoices,
    EntryActionChoices,
    EntryKindChoices,
    EntryStatusChoices,
)
from netbox_facts.filtersets import FactsReportEntryFilterSet
from netbox_facts.forms import FactsReportEntryFilterForm
from netbox_facts.models import FactsReportEntry

from .test_api_completeness import ReportEntryFixtureMixin

ENTRY_TAB_STATUSES = (
    EntryStatusChoices.STATUS_PENDING,
    EntryStatusChoices.STATUS_APPLIED,
    EntryStatusChoices.STATUS_SKIPPED,
    EntryStatusChoices.STATUS_FAILED,
)


def entry_tab_url(report_pk, status):
    """Return the URL of one per-status entry tab."""
    return reverse(f"plugins:netbox_facts:factsreport_entries_{status}", args=[report_pk])


def entry_tab_view(status):
    """Return the view class registered for one per-status entry tab."""
    return resolve(entry_tab_url(1, status)).func.view_class


def fieldset_field_names(form):
    """Flatten the field names a filter form declares across its fieldsets."""
    names = set()
    for fieldset in form.fieldsets:
        for item in fieldset.items:
            if isinstance(item, str):
                names.add(item)
            else:
                names.update(getattr(item, "items", ()))
    return names


class EntryFilterFormTest(TestCase):
    """The entry tabs need a filter form covering the documented filters (#140)."""

    def test_form_exposes_the_review_filters(self):
        """Reviewers filter by device, what happened, where it came from, and free text."""
        form = FactsReportEntryFilterForm()

        for field_name in ("q", "device", "action", "status", "collector_type", "entry_kind"):
            with self.subTest(field=field_name):
                self.assertIn(field_name, form.fields)

    def test_choice_fields_offer_the_model_choices(self):
        """A filter that offers stale choices silently hides entries."""
        form = FactsReportEntryFilterForm()
        expected = {
            "action": EntryActionChoices,
            "status": EntryStatusChoices,
            "collector_type": CollectionTypeChoices,
            "entry_kind": EntryKindChoices,
        }

        for field_name, choices in expected.items():
            with self.subTest(field=field_name):
                self.assertEqual(
                    [value for value, _label in form.fields[field_name].choices],
                    [value for value, _label in choices],
                )

    def test_every_filter_is_rendered_by_a_fieldset(self):
        """A field missing from the fieldsets is never rendered, so it cannot be used."""
        form = FactsReportEntryFilterForm()

        self.assertLessEqual(
            {"q", "device", "action", "status", "collector_type", "entry_kind"},
            fieldset_field_names(form),
        )


class EntryQFilterTest(ReportEntryFixtureMixin, TestCase):
    """The entry filterset must answer the `q` search the filter form posts (#140)."""

    @classmethod
    def setUpTestData(cls):
        cls.device, cls.report = cls.create_report("Search")
        cls.mac_entry = cls.create_entry(
            cls.report,
            cls.device,
            EntryStatusChoices.STATUS_PENDING,
            "MACAddress AA:BB:CC:DD:EE:01",
        )
        cls.interface_entry = cls.create_entry(
            cls.report,
            cls.device,
            EntryStatusChoices.STATUS_PENDING,
            "Interface ge-0/0/0",
        )

    def filtered(self, **params):
        return FactsReportEntryFilterSet(params, queryset=FactsReportEntry.objects.all()).qs

    def test_q_matches_an_object_repr_substring(self):
        """The label is what a reviewer reads, so it is what they search."""
        self.assertCountEqual(self.filtered(q="ge-0/0/0"), [self.interface_entry])

    def test_q_ignores_case(self):
        """Labels are capitalized by the collector, not by the reviewer typing them."""
        self.assertCountEqual(self.filtered(q="macaddress aa:bb"), [self.mac_entry])

    def test_q_matches_the_device_name(self):
        """A report spans devices, so narrowing to one device is a search too."""
        self.assertCountEqual(
            self.filtered(q=self.device.name),
            [self.mac_entry, self.interface_entry],
        )

    def test_q_that_matches_nothing_returns_no_entries(self):
        self.assertFalse(self.filtered(q="no-such-entry").exists())

    def test_blank_q_does_not_narrow_the_list(self):
        """A stray space in the search box must not hide the whole report."""
        self.assertCountEqual(
            self.filtered(q="   "),
            [self.mac_entry, self.interface_entry],
        )


class EntryTabConfigurationTest(ReportEntryFixtureMixin, TestCase):
    """The four status tabs must stay in place as entries move between them (#140)."""

    @classmethod
    def setUpTestData(cls):
        _device, cls.report = cls.create_report("Tabs")

    def test_tabs_do_not_hide_when_empty(self):
        """A tab set that reshuffles mid-review moves the tab under the cursor."""
        for status in ENTRY_TAB_STATUSES:
            with self.subTest(status=status):
                self.assertFalse(entry_tab_view(status).tab.hide_if_empty)

    def test_empty_tab_renders_with_a_zero_badge(self):
        """An empty tab is still rendered, reporting a count of zero."""
        for status in ENTRY_TAB_STATUSES:
            with self.subTest(status=status):
                rendered = entry_tab_view(status).tab.render(self.report)

                self.assertIsNotNone(rendered)
                self.assertEqual(rendered["badge"], 0)

    def test_tabs_wire_the_entry_filter_form(self):
        for status in ENTRY_TAB_STATUSES:
            with self.subTest(status=status):
                self.assertIs(entry_tab_view(status).filterset_form, FactsReportEntryFilterForm)

    def test_tabs_offer_the_export_action(self):
        for status in ENTRY_TAB_STATUSES:
            with self.subTest(status=status):
                actions = entry_tab_view(status).actions

                self.assertTrue(
                    any(issubclass(action, BulkExport) for action in actions),
                    msg=f"{status} tab offers no export action",
                )


class EntryExportTest(ReportEntryFixtureMixin, NetBoxViewTestCase):
    """The entry tabs must export what the reviewer is looking at (#140)."""

    user_permissions = (
        "netbox_facts.view_factsreport",
        "netbox_facts.view_factsreportentry",
    )

    def setUp(self):
        super().setUp()
        device, self.report = self.create_report("Export")
        self.pending_entry = self.create_entry(
            self.report,
            device,
            EntryStatusChoices.STATUS_PENDING,
            "Interface ge-0/0/1",
        )
        self.applied_entry = self.create_entry(
            self.report,
            device,
            EntryStatusChoices.STATUS_APPLIED,
            "Interface ge-0/0/2",
        )
        self.url = entry_tab_url(self.report.pk, EntryStatusChoices.STATUS_PENDING)

    @staticmethod
    def csv_rows(response):
        return list(csv.reader(response.content.decode().splitlines()))

    def test_export_returns_the_entries_of_that_tab_as_csv(self):
        """The pending tab exports pending entries, not the whole report."""
        response = self.client.get(f"{self.url}?export=table")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        body = response.content.decode()
        self.assertIn(self.pending_entry.object_repr, body)
        self.assertNotIn(self.applied_entry.object_repr, body)

    def test_export_applies_the_active_filters(self):
        """Exporting a filtered view that matches nothing yields a header only."""
        response = self.client.get(f"{self.url}?export=table&q=ge-0/0/9")

        self.assertEqual(len(self.csv_rows(response)), 1)

    def test_a_request_without_export_still_renders_the_tab(self):
        """The export branch must not swallow ordinary tab views."""
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("text/csv", response["Content-Type"])


class ReportStatCardLinkTest(ReportEntryFixtureMixin, NetBoxViewTestCase):
    """The report's entry-status cards are the way into the tabs (#140)."""

    user_permissions = ("netbox_facts.view_factsreport",)

    def setUp(self):
        super().setUp()
        _device, self.report = self.create_report("Cards")

    def test_each_status_card_links_to_its_entry_tab(self):
        response = self.client.get(self.report.get_absolute_url())

        body = response.content.decode()
        for status in ENTRY_TAB_STATUSES:
            with self.subTest(status=status):
                self.assertIn(entry_tab_url(self.report.pk, status), body)

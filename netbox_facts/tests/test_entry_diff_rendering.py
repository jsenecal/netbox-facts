"""Regression tests for FactsReportEntryTable.render_details (#133).

The changed-entry branch of render_details used to iterate only over
``set(detected) & set(current)`` -- the key intersection. A key reported
only by the device (added on an existing object) or only known to NetBox
(no longer reported by the device) was silently dropped from the
rendered Details column, so a reviewer approving a "changed" entry never
saw those keys at all.

These tests exercise render_details directly against lightweight stub
entries (an object carrying just ``action``, ``detected_values``, and
``current_values``), which is the plugin-owned logic under test. No
database access or view round-trip is needed since render_details does
not touch the ORM.
"""

from types import SimpleNamespace

import pytest

from netbox_facts.choices import EntryActionChoices
from netbox_facts.tables import FactsReportEntryTable

# FactsReportEntryTable() (a NetBoxTable subclass) resolves its ActionsColumn
# against the content type registry on instantiation even when given an
# empty list, so building one requires database access despite render_details
# itself never touching the ORM.
pytestmark = pytest.mark.django_db


def make_entry(detected_values, current_values, action=EntryActionChoices.ACTION_CHANGED):
    return SimpleNamespace(
        action=action,
        detected_values=detected_values,
        current_values=current_values,
    )


def render(entry):
    table = FactsReportEntryTable([])
    return table.render_details(entry)


class TestModifiedKeysUnchanged:
    def test_modified_key_renders_old_arrow_new(self):
        entry = make_entry(
            detected_values={"serial_number": "NEW123"},
            current_values={"serial_number": "OLD123"},
        )

        details = render(entry)

        assert "**serial**: OLD123 → NEW123" in details

    def test_unchanged_value_is_not_rendered(self):
        entry = make_entry(
            detected_values={"serial_number": "SAME"},
            current_values={"serial_number": "SAME"},
        )

        details = render(entry)

        assert details == ""


class TestAddedKeys:
    def test_detected_only_key_is_rendered(self):
        """A key newly reported by the device on an existing object must
        still appear in the diff, not be silently dropped (#133)."""
        entry = make_entry(
            detected_values={"mac_address": "AA:BB:CC:DD:EE:FF"},
            current_values={},
        )

        details = render(entry)

        assert "MAC" in details
        assert "AA:BB:CC:DD:EE:FF" in details
        assert "not set" in details

    def test_added_key_alongside_modified_key(self):
        entry = make_entry(
            detected_values={"serial_number": "NEW123", "mac_address": "AA:BB:CC:DD:EE:FF"},
            current_values={"serial_number": "OLD123"},
        )

        details = render(entry)

        assert "**serial**: OLD123 → NEW123" in details
        assert "AA:BB:CC:DD:EE:FF" in details


class TestRemovedKeys:
    def test_current_only_key_is_rendered(self):
        """A key the device no longer reports must still appear in the
        diff, with an explicit marker for the missing new value (#133)."""
        entry = make_entry(
            detected_values={},
            current_values={"remote_as": "65000"},
        )

        details = render(entry)

        assert "AS" in details
        assert "65000" in details
        assert "not set" in details

    def test_removed_key_alongside_modified_key(self):
        entry = make_entry(
            detected_values={"serial_number": "NEW123"},
            current_values={"serial_number": "OLD123", "remote_as": "65000"},
        )

        details = render(entry)

        assert "**serial**: OLD123 → NEW123" in details
        assert "65000" in details


class TestSkipFieldsHonored:
    def test_skip_field_excluded_from_modified_group(self):
        entry = make_entry(
            detected_values={"raw_output": "new-blob"},
            current_values={"raw_output": "old-blob"},
        )

        details = render(entry)

        assert details == ""

    def test_skip_field_excluded_from_added_group(self):
        entry = make_entry(
            detected_values={"raw_output": "new-blob"},
            current_values={},
        )

        details = render(entry)

        assert details == ""

    def test_skip_field_excluded_from_removed_group(self):
        entry = make_entry(
            detected_values={},
            current_values={"raw_output": "old-blob"},
        )

        details = render(entry)

        assert details == ""

"""Display helpers shared by the entry table and the entry detail page.

An entry stores what the device reported alongside what NetBox held at
detect time. The table summarizes that comparison in one markdown line
and the entry detail page renders it side by side, so the knowledge of
which keys are worth showing, what to call them, and how to classify a
difference lives here once instead of in each renderer.
"""

from dataclasses import dataclass
from typing import Any

from django.utils.translation import gettext_lazy as _

from netbox_facts.choices import EntryActionChoices
from netbox_facts.helpers.applier import (
    ERROR_KEY_ALL,
    ERROR_TYPE_ERROR,
    ERROR_TYPE_KEY,
    ERROR_TYPE_VALIDATION,
    normalize_error_messages,
)

__all__ = (
    "ABSENT",
    "CHANGE_ADDED",
    "CHANGE_MODIFIED",
    "CHANGE_REMOVED",
    "GENERAL_ERROR_LABEL",
    "LABEL_MAP",
    "SKIP_FIELDS",
    "EntryDiffRow",
    "build_apply_error_display",
    "build_entry_diff",
    "visible_labeled_keys",
)

# Keys a collector carries for its own bookkeeping (the object's identity,
# the raw command output it was parsed from), which repeat what the entry
# label already says or are too large to belong in a comparison.
SKIP_FIELDS = {
    "name",
    "component_name",
    "parent_name",
    "module_bay_id",
    "module_type_id",
    "interface",
    "logical_interface",
    "raw_output",
}

# Collected keys whose stored name reads poorly in a review context.
LABEL_MAP = {
    "serial_number": "serial",
    "mac_address": "MAC",
    "ip_address": "IP",
    "lag_parent": "LAG",
    "remote_device": "remote",
    "remote_interface": "remote port",
    "remote_address": "peer",
    "remote_as": "AS",
}

# Stands in for the side of a comparison that holds no value at all, which
# is distinct from a value that is empty.
ABSENT = "(not set)"

CHANGE_MODIFIED = "modified"
CHANGE_ADDED = "added"
CHANGE_REMOVED = "removed"

CHANGE_LABELS = {
    CHANGE_MODIFIED: _("Modified"),
    CHANGE_ADDED: _("Added"),
    CHANGE_REMOVED: _("Removed"),
}

CHANGE_COLORS = {
    CHANGE_MODIFIED: "orange",
    CHANGE_ADDED: "green",
    CHANGE_REMOVED: "gray",
}

# What an apply failure addressed to the entry as a whole is called, since
# "__all__" is a Django convention rather than something to show a reviewer.
GENERAL_ERROR_LABEL = _("General")


@dataclass(frozen=True)
class EntryDiffRow:
    """One key of an entry's comparison, as the detail page renders it."""

    key: str
    label: str
    change: str
    current: Any
    detected: Any

    @property
    def change_label(self):
        return CHANGE_LABELS[self.change]

    @property
    def change_color(self):
        return CHANGE_COLORS[self.change]


def visible_labeled_keys(keys):
    """Yield (key, label) pairs for sorted keys, skipping SKIP_FIELDS."""
    for key in sorted(keys):
        if key in SKIP_FIELDS:
            continue
        yield key, LABEL_MAP.get(key, key)


def build_entry_diff(entry):
    """Classify an entry's comparison key by key.

    A changed entry reports keys whose value moved, keys only the device
    reported, and keys only NetBox still holds -- in that order, each
    group sorted by key. A new or stale entry has a single side, so every
    visible key is an addition or a removal respectively. A confirmed
    entry found nothing to review and yields no rows.
    """
    detected = entry.detected_values or {}
    current = entry.current_values or {}
    rows = []

    if entry.action == EntryActionChoices.ACTION_CHANGED:
        detected_keys = set(detected)
        current_keys = set(current)

        for key, label in visible_labeled_keys(detected_keys & current_keys):
            old, new = current[key], detected[key]
            if str(old) != str(new):
                rows.append(EntryDiffRow(key, label, CHANGE_MODIFIED, old, new))
        for key, label in visible_labeled_keys(detected_keys - current_keys):
            rows.append(EntryDiffRow(key, label, CHANGE_ADDED, ABSENT, detected[key]))
        for key, label in visible_labeled_keys(current_keys - detected_keys):
            rows.append(EntryDiffRow(key, label, CHANGE_REMOVED, current[key], ABSENT))
    elif entry.action == EntryActionChoices.ACTION_NEW:
        for key, label in visible_labeled_keys(detected):
            rows.append(EntryDiffRow(key, label, CHANGE_ADDED, ABSENT, detected[key]))
    elif entry.action == EntryActionChoices.ACTION_STALE:
        for key, label in visible_labeled_keys(current):
            rows.append(EntryDiffRow(key, label, CHANGE_REMOVED, current[key], ABSENT))

    return rows


def build_apply_error_display(apply_error):
    """Prepare a stored apply failure for field-by-field rendering.

    Returns None when the payload carries no messages, so a caller can
    treat "nothing to show" and "no failure" alike. Messages are
    normalized on the way out because a row may predate the structured
    field or have been written by hand.
    """
    payload = apply_error or {}
    fields = [
        {
            "label": GENERAL_ERROR_LABEL if field == ERROR_KEY_ALL else field,
            "messages": normalize_error_messages(messages),
        }
        for field, messages in payload.items()
        if field != ERROR_TYPE_KEY
    ]
    if not fields:
        return None

    error_type = payload.get(ERROR_TYPE_KEY, ERROR_TYPE_ERROR)
    return {
        "error_type": error_type,
        "is_validation": error_type == ERROR_TYPE_VALIDATION,
        "fields": fields,
    }

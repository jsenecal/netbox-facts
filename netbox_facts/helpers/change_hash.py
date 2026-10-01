"""The content identity of a proposed change.

A detect run records what it found; the next run finds the same thing
again. Telling "the same change, again" from "a different change about the
same subject" is what lets a reviewer's decision outlive the report it was
made on, and it is the one thing a stored hash has to get right: two runs
that found the same thing must hash the same, and a payload whose apply
would do something different must not.

The canonicalization lives here once. Both the collector that stamps a
hash on a new entry and anything that asks "is this the change we were
already told about" go through the same helper, so the two cannot come to
disagree about what counts as the same change.
"""

import hashlib
import json

__all__ = (
    "VOLATILE_FIELDS",
    "canonical_change_values",
    "compute_change_hash",
)

# Collected keys left out of a change's identity: what a device reports
# differently from one run to the next without changing what applying the
# entry would do. Link state flaps, a neighbor's age ticks up, and
# raw_output carries whole command output that differs every time, so
# hashing any of them would resurface every skipped entry on every run.
#
# The display side hides a wider set (SKIP_FIELDS in entry_display): it
# also drops the identity keys an entry's label already states. Those stay
# here on purpose -- they are exactly what identifies the subject of the
# change.
VOLATILE_FIELDS = frozenset(
    {
        "raw_output",
        "age",
        "uptime",
        "last_seen",
        "is_up",
        "is_enabled",
        "speed",
        "mtu",
    }
)


def canonical_change_values(detected_values):
    """Return the part of a detected payload that identifies the change."""
    return {key: value for key, value in (detected_values or {}).items() if key not in VOLATILE_FIELDS}


def compute_change_hash(entry_kind, object_repr, detected_values):
    """Hash what an entry proposes: its kind, its subject, and its payload.

    The kind and the label carry the subject -- two devices, or two
    interfaces of one device, propose different changes from identical
    payloads -- and the canonical payload carries the proposal itself.
    Keys are sorted so the order a collector happened to build its dict in
    cannot change the result, and values that are not JSON types fall back
    to their string form rather than failing the run.
    """
    payload = json.dumps(
        {
            "kind": entry_kind or "",
            "subject": object_repr or "",
            "values": canonical_change_values(detected_values),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()

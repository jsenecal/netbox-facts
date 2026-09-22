"""Display helpers for report entries.

An entry stores what the device reported alongside what NetBox held at
detect time. Which keys of that comparison are worth showing and what to
call them is display knowledge rather than table knowledge, so it lives
here and every renderer of an entry reads the same map.
"""

__all__ = (
    "ABSENT",
    "LABEL_MAP",
    "SKIP_FIELDS",
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


def visible_labeled_keys(keys):
    """Yield (key, label) pairs for sorted keys, skipping SKIP_FIELDS."""
    for key in sorted(keys):
        if key in SKIP_FIELDS:
            continue
        yield key, LABEL_MAP.get(key, key)

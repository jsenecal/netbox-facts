"""Shared constants for the netbox_facts plugin."""

AUTO_D_TAG = "Automatically Discovered"

# The display name above is editable in the UI and carries no identity
# guarantee -- renaming it does not move the tag, but it also does not
# protect anything. Every ownership gate in helpers/collector.py and
# helpers/applier.py matches the discovery tag by this slug instead, which
# is what the signal in signals.py blocks from changing. See
# helpers/netbox.py:get_discovery_tag() for the lookup, and migration 0035
# for how the tag's row is first established.
AUTO_D_TAG_SLUG = "automatically-discovered"

# Kept within extras.Tag.description's 200-character max_length.
AUTO_D_TAG_DESCRIPTION = (
    "Marks objects netbox_facts created automatically. Stale handling and "
    "ownership checks match by slug; renaming the slug or deleting the tag "
    "breaks them. Name, color, and this text are safe to edit."
)

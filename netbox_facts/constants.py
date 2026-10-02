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

#: Visibility tag put on a plugin-owned object that a run no longer finds,
#: for as long as its grace period runs. It makes a pending removal visible
#: on the object itself -- in a list view, on the object's own page -- before
#: anything is removed. A tag of its own rather than the discovery tag: that
#: one says who created the object, this one says it is on its way out.
ORPHAN_TAG_NAME = "Orphaned (netbox-facts)"

#: The handle the orphan tag is looked up by, for the same reason the
#: discovery tag has one: a name is editable in the UI and a slug is what
#: migration 0036 fixes. Every read of this tag in helpers/orphans.py
#: matches on the slug.
ORPHAN_TAG_SLUG = "netbox-facts-orphaned"

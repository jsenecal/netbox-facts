"""Shared constants for the netbox_facts plugin."""

AUTO_D_TAG = "Automatically Discovered"

#: Visibility tag put on a plugin-owned object that a run no longer finds,
#: for as long as its grace period runs. It makes a pending removal visible
#: on the object itself -- in a list view, on the object's own page -- before
#: anything is removed.
ORPHAN_TAG_NAME = "Orphaned (netbox-facts)"

#: The handle the tag is looked up by. A tag's name and description are
#: editable in the UI and its colour is nobody's business but the operator's;
#: the slug is what a data migration fixes and what every lookup here uses,
#: so renaming the tag cannot make the plugin lose track of it.
ORPHAN_TAG_SLUG = "netbox-facts-orphaned"

"""Template helpers for netbox_facts."""

from django import template

from netbox_facts import entry_actions

register = template.Library()


@register.filter(name="entry_actions")
def entry_actions_filter(status):
    """Return the lifecycle transitions an entry in this status offers.

    The entry table renders its per-row buttons from a template string that
    django-tables2 hands one record at a time, with no view context to read,
    so the transition table is reached from the row's own status instead of
    being passed in.
    """
    return entry_actions.entry_actions_for_status(status)

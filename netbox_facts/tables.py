import django_tables2 as tables
from dcim.tables import InterfaceTable
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from netbox.tables import NetBoxTable
from netbox.tables.columns import ActionsColumn, ChoiceFieldColumn, DateTimeColumn, MarkdownColumn, ToggleColumn

from .choices import EntryActionChoices
from .helpers import entry_display
from .models import CollectionPlan, FactsReport, FactsReportEntry, MACAddress, MACVendor

__all__ = [
    "MACAddressTable",
    "MACInterfaceTable",
    "MACVendorTable",
    "CollectorTable",
    "FactsReportTable",
    "FactsReportEntryTable",
]


class DatedNetboxTable(NetBoxTable):
    """Table representation of the DatedModel model."""

    created = DateTimeColumn()
    last_updated = DateTimeColumn()

    class Meta(NetBoxTable.Meta):
        fields = ("pk", "id", "created", "last_updated", "actions")
        default_columns = ("created", "last_updated")


class MACAddressTable(DatedNetboxTable):
    """Table representation of the MACAddress model."""

    mac_address = tables.Column(linkify=True)
    vendor = tables.Column(linkify=True)
    occurences = tables.Column(accessor="occurences", verbose_name=_("Occurrences"))
    last_seen = DateTimeColumn()

    class Meta(NetBoxTable.Meta):
        model = MACAddress
        fields = (
            "pk",
            "id",
            "mac_address",
            "vendor",
            "description",
            "occurences",
            "last_seen",
            "actions",
            "discovery_method",
        )
        default_columns = (
            "mac_address",
            "vendor",
            "description",
            "last_seen",
        )


class MACInterfaceTable(InterfaceTable):
    """Interface table extended with the MAC-to-interface link timestamp."""

    last_seen = DateTimeColumn(verbose_name=_("Last Seen"))

    class Meta(InterfaceTable.Meta):
        fields = InterfaceTable.Meta.fields + ("last_seen",)
        default_columns = ("pk", "name", "device", "type", "last_seen")


class MACVendorTable(DatedNetboxTable):
    """Table representation of the MACVendor model."""

    manufacturer = tables.Column(linkify=True)
    vendor_name = tables.Column(verbose_name=_("Vendor Name"), linkify=True)
    mac_prefix = tables.Column(verbose_name=_("MAC Prefix"))
    instance_count = tables.Column(verbose_name=_("Instances"), accessor="instance_count", orderable=True, default=0)

    class Meta(NetBoxTable.Meta):
        model = MACVendor
        fields = ("pk", "id", "manufacturer", "mac_prefix", "instance_count", "actions")
        default_columns = ("vendor_name", "mac_prefix", "instance_count")


class CollectorTable(NetBoxTable):
    """Table representation of the Collector model."""

    name = tables.Column(linkify=True)  # type: ignore

    class Meta(NetBoxTable.Meta):
        model = CollectionPlan
        fields = (
            "pk",
            "id",
            "name",
            "priority",
            "status",
            "collector_type",
            "detect_only",
            "description",
            "tags",
            "actions",
        )
        default_columns = (
            "name",
            "status",
            "collector_type",
            "description",
        )


class FactsReportTable(NetBoxTable):
    """Table representation of the FactsReport model."""

    pk = ToggleColumn()
    collection_plan = tables.Column(linkify=True)
    status = ChoiceFieldColumn()
    actions = ActionsColumn(actions=("delete",))
    entry_count = tables.Column(verbose_name=_("Entries"), accessor="entry_count", orderable=True, default=0)
    new_count = tables.Column(verbose_name=_("New"), accessor="new_count", orderable=True, default=0)
    changed_count = tables.Column(verbose_name=_("Changed"), accessor="changed_count", orderable=True, default=0)
    stale_count = tables.Column(verbose_name=_("Stale"), accessor="stale_count", orderable=True, default=0)
    created = DateTimeColumn()

    class Meta(NetBoxTable.Meta):
        model = FactsReport
        order_by = ("-created",)
        fields = (
            "pk",
            "id",
            "collection_plan",
            "status",
            "entry_count",
            "new_count",
            "changed_count",
            "stale_count",
            "created",
            "completed_at",
            "actions",
        )
        default_columns = (
            "pk",
            "id",
            "collection_plan",
            "status",
            "entry_count",
            "new_count",
            "changed_count",
            "created",
        )

    def render_id(self, value, record):
        return format_html('<a href="{}">{}</a>', record.get_absolute_url(), value)


# Per-row lifecycle shortcuts. The buttons live inside the bulk form the
# entry tabs render, so they submit it with their own PK under "row_pk" --
# a name the bulk checkboxes do not use, which is how the action views tell
# a single-row click from a checkbox selection. Each row offers only the
# transitions its current status allows.
ENTRY_ROW_BUTTONS = """
{% load i18n %}
{% if perms.netbox_facts.apply_factsreport %}
  {% if record.status == 'pending' %}
    <button type="submit" formmethod="post" name="row_pk" value="{{ record.pk }}"
            formaction="{% url 'plugins:netbox_facts:factsreport_apply' pk=record.report_id %}"
            class="btn btn-sm btn-green" title="{% trans "Apply" %}" aria-label="{% trans "Apply" %}">
      <i class="mdi mdi-check" aria-hidden="true"></i>
    </button>
    <button type="submit" formmethod="post" name="row_pk" value="{{ record.pk }}"
            formaction="{% url 'plugins:netbox_facts:factsreport_skip' pk=record.report_id %}"
            class="btn btn-sm btn-secondary" title="{% trans "Skip" %}" aria-label="{% trans "Skip" %}">
      <i class="mdi mdi-close" aria-hidden="true"></i>
    </button>
  {% elif record.status == 'failed' %}
    <button type="submit" formmethod="post" name="row_pk" value="{{ record.pk }}"
            formaction="{% url 'plugins:netbox_facts:factsreport_retry' pk=record.report_id %}"
            class="btn btn-sm btn-warning" title="{% trans "Retry" %}" aria-label="{% trans "Retry" %}">
      <i class="mdi mdi-refresh" aria-hidden="true"></i>
    </button>
  {% elif record.status == 'skipped' %}
    <button type="submit" formmethod="post" name="row_pk" value="{{ record.pk }}"
            formaction="{% url 'plugins:netbox_facts:factsreport_unskip' pk=record.report_id %}"
            class="btn btn-sm btn-secondary" title="{% trans "Un-skip" %}" aria-label="{% trans "Un-skip" %}">
      <i class="mdi mdi-undo-variant" aria-hidden="true"></i>
    </button>
  {% endif %}
{% endif %}
"""


# How one classified diff row reads in the table's one-line summary, per
# entry action. A changed entry shows both sides of the comparison, with the
# absent marker standing in for a side that holds no value; a new or stale
# entry has only one side, so it shows that side on its own.
ENTRY_DETAIL_FORMATTERS = {
    EntryActionChoices.ACTION_CHANGED: lambda row: f"**{row.label}**: {row.current} → {row.detected}",
    EntryActionChoices.ACTION_NEW: lambda row: f"**{row.label}**: {row.detected}",
    EntryActionChoices.ACTION_STALE: lambda row: f"**{row.label}**: {row.current}",
}


class FactsReportEntryTable(NetBoxTable):
    """Table representation of the FactsReportEntry model."""

    pk = ToggleColumn()
    action = ChoiceFieldColumn()
    status = ChoiceFieldColumn()
    device = tables.Column(linkify=True)
    display_title = tables.Column(
        verbose_name=_("Entry"),
        accessor="display_title",
        orderable=False,
        linkify=lambda record: reverse("plugins:netbox_facts:factsreportentry", args=[record.pk]),
    )
    object_repr = MarkdownColumn(verbose_name=_("Object"))
    collector_type = ChoiceFieldColumn()
    details = MarkdownColumn(verbose_name=_("Details"), orderable=False, empty_values=())
    actions = ActionsColumn(actions=(), extra_buttons=ENTRY_ROW_BUTTONS)

    class Meta(NetBoxTable.Meta):
        model = FactsReportEntry
        fields = (
            "pk",
            "action",
            "status",
            "collector_type",
            "device",
            "display_title",
            "object_repr",
            "details",
            "created",
            "applied_at",
            "error_message",
        )
        default_columns = (
            "pk",
            "action",
            "status",
            "collector_type",
            "device",
            "display_title",
            "details",
            "error_message",
        )

    def render_details(self, record):
        """Summarize the entry's comparison as one markdown cell.

        The classification itself -- which keys are worth showing, what to
        call them, and which side of the comparison each one sits on --
        comes from build_entry_diff, the same source the entry detail page
        renders from, so this only formats the rows it is handed. An action
        with nothing to review yields no rows and so an empty cell.
        """
        formatter = ENTRY_DETAIL_FORMATTERS.get(record.action)
        if formatter is None:
            return ""
        return "  \n".join(formatter(row) for row in entry_display.build_entry_diff(record))

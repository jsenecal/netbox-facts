import django_tables2 as tables
from dcim.tables import InterfaceTable
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


class FactsReportEntryTable(NetBoxTable):
    """Table representation of the FactsReportEntry model."""

    # The marker for a side of the comparison that holds no value. It comes
    # from the same module as the skip/label map _visible_labeled_keys()
    # delegates to, so this summary and the entry detail page agree.
    ABSENT = entry_display.ABSENT

    pk = ToggleColumn()
    action = ChoiceFieldColumn()
    status = ChoiceFieldColumn()
    device = tables.Column(linkify=True)
    object_repr = MarkdownColumn(verbose_name=_("Object"))
    collector_type = ChoiceFieldColumn()
    details = MarkdownColumn(verbose_name=_("Details"), orderable=False, empty_values=())
    actions = ActionsColumn(actions=())

    class Meta(NetBoxTable.Meta):
        model = FactsReportEntry
        fields = (
            "pk",
            "action",
            "status",
            "collector_type",
            "device",
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
            "object_repr",
            "details",
            "error_message",
        )

    def _visible_labeled_keys(self, keys):
        """Yield (key, label) pairs for sorted keys, skipping hidden ones.

        Shared by every render_details loop so the skip/label preamble is
        defined once instead of repeated per diff group, and delegated so
        the table and the entry detail page read one map.
        """
        return entry_display.visible_labeled_keys(keys)

    def render_details(self, record):
        detected = record.detected_values or {}
        current = record.current_values or {}
        lines = []

        if record.action == EntryActionChoices.ACTION_CHANGED:
            detected_keys = set(detected)
            current_keys = set(current)

            for key, label in self._visible_labeled_keys(detected_keys & current_keys):
                old, new = current[key], detected[key]
                if str(old) != str(new):
                    lines.append(f"**{label}**: {old} → {new}")
            for key, label in self._visible_labeled_keys(detected_keys - current_keys):
                lines.append(f"**{label}**: {self.ABSENT} → {detected[key]}")
            for key, label in self._visible_labeled_keys(current_keys - detected_keys):
                lines.append(f"**{label}**: {current[key]} → {self.ABSENT}")
        elif record.action == EntryActionChoices.ACTION_NEW:
            for key, label in self._visible_labeled_keys(detected):
                lines.append(f"**{label}**: {detected[key]}")
        elif record.action == EntryActionChoices.ACTION_STALE:
            for key, label in self._visible_labeled_keys(current):
                lines.append(f"**{label}**: {current[key]}")

        return "  \n".join(lines)

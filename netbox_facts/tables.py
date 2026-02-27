import django_tables2 as tables
from django.utils.translation import gettext_lazy as _
from django.utils.html import format_html

from netbox.tables import NetBoxTable
from netbox.tables.columns import ActionsColumn, ChoiceFieldColumn, DateTimeColumn, ToggleColumn

from .models import MACAddress, MACVendor, CollectionPlan, FactsReport, FactsReportEntry

__all__ = [
    "MACAddressTable",
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
    occurences = tables.Column(accessor="occurences", verbose_name=_("Occurences"))
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
            "discovery_method"
        )
        default_columns = (
            "mac_address",
            "vendor",
            "description",
            "last_seen",
        )


class MACVendorTable(DatedNetboxTable):
    """Table representation of the MACVendor model."""

    manufacturer = tables.Column(linkify=True)
    vendor_name = tables.Column(verbose_name=_("Vendor Name"), linkify=True)
    mac_prefix = tables.Column(verbose_name=_("MAC Prefix"))

    class Meta(NetBoxTable.Meta):
        model = MACVendor
        fields = ("pk", "id", "manufacturer", "mac_prefix", "actions")
        default_columns = ("vendor_name", "mac_prefix")


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
    entry_count = tables.Column(verbose_name=_("Entries"), accessor="entry_count", orderable=True, default=0)
    new_count = tables.Column(verbose_name=_("New"), accessor="new_count", orderable=True, default=0)
    changed_count = tables.Column(verbose_name=_("Changed"), accessor="changed_count", orderable=True, default=0)
    stale_count = tables.Column(verbose_name=_("Stale"), accessor="stale_count", orderable=True, default=0)
    created = DateTimeColumn()

    class Meta(NetBoxTable.Meta):
        model = FactsReport
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

    pk = ToggleColumn()
    action = ChoiceFieldColumn()
    status = ChoiceFieldColumn()
    device = tables.Column(linkify=True)
    object_repr = tables.Column(verbose_name=_("Object"))
    collector_type = ChoiceFieldColumn()

    class Meta(NetBoxTable.Meta):
        model = FactsReportEntry
        fields = (
            "pk",
            "action",
            "status",
            "collector_type",
            "device",
            "object_repr",
            "created",
            "applied_at",
        )
        default_columns = (
            "pk",
            "action",
            "status",
            "collector_type",
            "device",
            "object_repr",
        )

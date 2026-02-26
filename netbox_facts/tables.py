import django_tables2 as tables
from django.utils.translation import gettext_lazy as _

from netbox.tables import NetBoxTable
from netbox.tables.columns import ActionsColumn, ChoiceFieldColumn, DateTimeColumn

from .models import MACAddress, MACVendor, CollectionPlan

__all__ = ["MACAddressTable", "MACVendorTable", "CollectorTable"]


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

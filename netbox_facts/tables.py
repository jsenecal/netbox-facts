import django_tables2 as tables
from django.utils.translation import gettext_lazy as _

from netbox.tables import NetBoxTable
from netbox.tables.columns import ActionsColumn, ChoiceFieldColumn, DateTimeColumn
from netbox.tables.tables import BaseTable

from .models import MACAddress, MACVendor, CollectorDefinition, CollectionJob

__all__ = ["MACAddressTable", "MACVendorTable", "CollectorDefinitionTable"]


class DatedNetboxTable(NetBoxTable):
    """Table representation of the DatedModel model."""

    created = tables.DateTimeColumn(format="Y-m-d H:i:s")
    last_updated = tables.DateTimeColumn(format="Y-m-d H:i:s")

    class Meta(NetBoxTable.Meta):
        fields = ("pk", "id", "created", "last_updated", "actions")
        default_columns = ("created", "last_updated")


class MACAddressTable(DatedNetboxTable):
    """Table representation of the MACAddress model."""

    mac_address = tables.Column(linkify=True)
    vendor = tables.Column(linkify=True)
    occurences = tables.Column(accessor="occurences", verbose_name=_("Occurences"))

    class Meta(NetBoxTable.Meta):
        model = MACAddress
        fields = ("pk", "id", "mac_address", "vendor", "description", "occurences", "actions")
        default_columns = (
            "mac_address",
            "vendor",
            "description",
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


class CollectorDefinitionTable(NetBoxTable):
    """Table representation of the CollectorDefinition model."""

    name = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = CollectorDefinition
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


class CollectionJobTable(NetBoxTable):
    """Table representation of the CollectionJob model."""

    # job_id = tables.Column(linkify=True)
    status = ChoiceFieldColumn(
        verbose_name=_("Status"),
    )
    created = DateTimeColumn()
    scheduled = DateTimeColumn()
    started = DateTimeColumn()
    completed = DateTimeColumn()
    actions = ActionsColumn(actions=("delete",))

    class Meta(NetBoxTable.Meta):
        model = CollectionJob
        fields = (
            "pk",
            "job_id",
            "status",
            "job_type",
            "job_definition",
            "created",
            "scheduled",
            "started",
            "completed",
        )
        default_columns = (
            "name",
            "job_id",
            "status",
        )

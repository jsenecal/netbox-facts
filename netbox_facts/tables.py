import django_tables2 as tables
from django.utils.translation import gettext_lazy as _

from netbox.tables import NetBoxTable

from .models import MACAddress, MACVendor


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

    name = tables.Column(linkify=True)
    mac_prefix = tables.Column()

    class Meta(NetBoxTable.Meta):
        model = MACVendor
        fields = ("pk", "id", "name", "mac_prefix", "actions")
        default_columns = ("name", "mac_prefix")

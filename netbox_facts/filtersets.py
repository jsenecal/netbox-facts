from netbox.filtersets import NetBoxModelFilterSet
from .models import MACAddress, MACVendor, CollectionPlan
from dcim.fields import MACAddressField
from .fields import MACPrefixField
import django_filters

__all__ = ["MACAddressFilterSet", "MACVendorFilterSet", "CollectorFilterSet"]


class MACAddressFilterSet(NetBoxModelFilterSet):
    """Filter set for the MACAddress model."""

    class Meta:
        """Meta class for MACAddressFilterSet."""

        model = MACAddress
        fields = [
            "mac_address",
            "vendor",
            "description",
        ]
        filter_overrides = {
            MACAddressField: {
                "filter_class": django_filters.CharFilter,
                "extra": lambda f: {
                    "lookup_expr": "icontains",
                },
            },
        }

    def search(self, queryset, name, value):
        return queryset.filter(mac_address__icontains=value)


class MACVendorFilterSet(NetBoxModelFilterSet):
    """Filter set for the MACAddress model."""

    class Meta:
        """Meta class for MACAddressFilterSet."""

        model = MACVendor
        fields = ["mac_prefix", "manufacturer"]
        filter_overrides = {
            MACPrefixField: {
                "filter_class": django_filters.CharFilter,
                "extra": lambda f: {
                    "lookup_expr": "icontains",
                },
            },
        }


class CollectorFilterSet(NetBoxModelFilterSet):
    """Filter set for the Collector model."""

    class Meta:
        """Meta class for CollectorFilterSet."""

        model = CollectionPlan
        fields = ["name"]

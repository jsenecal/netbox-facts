import django_filters

from dcim.fields import MACAddressField
from netbox.filtersets import NetBoxModelFilterSet

from .choices import (
    CollectionTypeChoices,
    CollectorPriorityChoices,
    CollectorStatusChoices,
)
from .fields import MACPrefixField
from .models import MACAddress, MACVendor, CollectionPlan

__all__ = ["MACAddressFilterSet", "MACVendorFilterSet", "CollectorFilterSet"]


class MACAddressFilterSet(NetBoxModelFilterSet):
    """Filter set for the MACAddress model."""

    description = django_filters.CharFilter(lookup_expr="icontains")

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
    """Filter set for the MACVendor model."""

    class Meta:
        """Meta class for MACVendorFilterSet."""

        model = MACVendor
        fields = ["mac_prefix", "manufacturer", "vendor_name"]
        filter_overrides = {
            MACPrefixField: {
                "filter_class": django_filters.CharFilter,
                "extra": lambda f: {
                    "lookup_expr": "icontains",
                },
            },
        }

    def search(self, queryset, name, value):
        return queryset.filter(vendor_name__icontains=value)


class CollectorFilterSet(NetBoxModelFilterSet):
    """Filter set for the CollectionPlan model."""

    name = django_filters.CharFilter(lookup_expr="icontains")
    priority = django_filters.MultipleChoiceFilter(
        choices=CollectorPriorityChoices,
    )
    status = django_filters.MultipleChoiceFilter(
        choices=CollectorStatusChoices,
    )
    collector_type = django_filters.MultipleChoiceFilter(
        choices=CollectionTypeChoices,
    )
    enabled = django_filters.BooleanFilter()

    class Meta:
        """Meta class for CollectorFilterSet."""

        model = CollectionPlan
        fields = ["name", "priority", "status", "collector_type", "enabled"]

    def search(self, queryset, name, value):
        return queryset.filter(name__icontains=value)

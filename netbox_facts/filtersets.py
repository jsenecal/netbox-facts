import django_filters
from dcim.fields import MACAddressField
from dcim.models import Device
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from netbox.filtersets import BaseFilterSet, NetBoxModelFilterSet

from .choices import (
    CollectionTypeChoices,
    CollectorPriorityChoices,
    CollectorStatusChoices,
    EntryActionChoices,
    EntryKindChoices,
    EntryStatusChoices,
    ReportStatusChoices,
)
from .fields import MACPrefixField
from .models import CollectionPlan, FactsReport, FactsReportEntry, MACAddress, MACVendor

__all__ = [
    "MACAddressFilterSet",
    "MACVendorFilterSet",
    "CollectorFilterSet",
    "FactsReportFilterSet",
    "FactsReportEntryFilterSet",
]


class QuickSearchMixin(django_filters.FilterSet):
    """The `q` search shared by every filterset in this plugin.

    A filterset names the fields its search spans in `search_fields`; the
    value is matched case-insensitively against each of them, and a value
    worn down to nothing narrows nothing.

    Composes with either base: NetBoxModelFilterSet declares an identical
    `q` filter of its own, and since a filter's `method` is resolved on the
    filterset instance, this mixin coming first in the bases is what makes
    the search below the one that runs.
    """

    search_fields = ()

    q = django_filters.CharFilter(
        method="search",
        label=_("Search"),
    )

    def search(self, queryset, name, value):
        value = value.strip()
        if not value:
            return queryset
        query = Q()
        for field_name in self.search_fields:
            query |= Q(**{f"{field_name}__icontains": value})
        return queryset.filter(query)


class MACAddressFilterSet(QuickSearchMixin, NetBoxModelFilterSet):
    """Filter set for the MACAddress model."""

    search_fields = ("mac_address",)

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


class MACVendorFilterSet(QuickSearchMixin, NetBoxModelFilterSet):
    """Filter set for the MACVendor model."""

    search_fields = ("vendor_name",)

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


class CollectorFilterSet(QuickSearchMixin, NetBoxModelFilterSet):
    """Filter set for the CollectionPlan model."""

    search_fields = ("name",)

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


class FactsReportFilterSet(QuickSearchMixin, BaseFilterSet):
    """Filter set for the FactsReport model.

    Reports and their entries are plain models: they carry no tags, no
    custom fields and no change log, so NetBoxModelFilterSet (which filters
    on all three) does not apply to them. BaseFilterSet is the part that
    does -- saved filters and the standard lookup expressions -- without
    assuming model features these two lack.
    """

    search_fields = ("collection_plan__name",)

    collection_plan = django_filters.ModelMultipleChoiceFilter(
        queryset=CollectionPlan.objects.all(),
    )
    status = django_filters.MultipleChoiceFilter(
        choices=ReportStatusChoices,
    )

    class Meta:
        model = FactsReport
        fields = ["collection_plan", "status"]


class FactsReportEntryFilterSet(QuickSearchMixin, BaseFilterSet):
    """Filter set for the FactsReportEntry model.

    The search spans the entry label a reviewer reads in the table and the
    device column they scan a long report by.
    """

    search_fields = ("object_repr", "device__name")

    action = django_filters.MultipleChoiceFilter(choices=EntryActionChoices)
    status = django_filters.MultipleChoiceFilter(choices=EntryStatusChoices)
    collector_type = django_filters.MultipleChoiceFilter(choices=CollectionTypeChoices)
    entry_kind = django_filters.MultipleChoiceFilter(choices=EntryKindChoices)
    device = django_filters.ModelMultipleChoiceFilter(
        queryset=Device.objects.all(),
        label=_("Device"),
    )

    class Meta:
        model = FactsReportEntry
        fields = ["report", "action", "status", "collector_type", "entry_kind", "device"]

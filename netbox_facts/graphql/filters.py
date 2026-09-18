"""GraphQL filters for the netbox_facts models.

Filter input names share a single global namespace with NetBox core and every
other installed plugin, so each filter here carries a "Facts" prefix.
"""

import strawberry_django
from netbox.graphql.filters import BaseModelFilter, NetBoxModelFilter
from strawberry.scalars import ID
from strawberry_django import FilterLookup, StrFilterLookup

from netbox_facts import models

__all__ = (
    "CollectionPlanFilter",
    "FactsReportEntryFilter",
    "FactsReportFilter",
    "MACAddressFilter",
    "MACVendorFilter",
)


@strawberry_django.filter_type(models.MACAddress, name="FactsMACAddressFilter", lookups=True)
class MACAddressFilter(NetBoxModelFilter):
    mac_address: StrFilterLookup | None = strawberry_django.filter_field()
    description: StrFilterLookup | None = strawberry_django.filter_field()
    vendor_id: ID | None = strawberry_django.filter_field()


@strawberry_django.filter_type(models.MACVendor, name="FactsMACVendorFilter", lookups=True)
class MACVendorFilter(NetBoxModelFilter):
    vendor_name: StrFilterLookup | None = strawberry_django.filter_field()
    mac_prefix: StrFilterLookup | None = strawberry_django.filter_field()
    manufacturer_id: ID | None = strawberry_django.filter_field()


@strawberry_django.filter_type(models.CollectionPlan, name="FactsCollectionPlanFilter", lookups=True)
class CollectionPlanFilter(NetBoxModelFilter):
    name: StrFilterLookup | None = strawberry_django.filter_field()
    status: StrFilterLookup | None = strawberry_django.filter_field()
    priority: StrFilterLookup | None = strawberry_django.filter_field()
    collector_type: StrFilterLookup | None = strawberry_django.filter_field()
    enabled: FilterLookup[bool] | None = strawberry_django.filter_field()
    detect_only: FilterLookup[bool] | None = strawberry_django.filter_field()


@strawberry_django.filter_type(models.FactsReport, name="FactsReportFilter", lookups=True)
class FactsReportFilter(BaseModelFilter):
    status: StrFilterLookup | None = strawberry_django.filter_field()
    collection_plan_id: ID | None = strawberry_django.filter_field()


@strawberry_django.filter_type(models.FactsReportEntry, name="FactsReportEntryFilter", lookups=True)
class FactsReportEntryFilter(BaseModelFilter):
    action: StrFilterLookup | None = strawberry_django.filter_field()
    status: StrFilterLookup | None = strawberry_django.filter_field()
    collector_type: StrFilterLookup | None = strawberry_django.filter_field()
    report_id: ID | None = strawberry_django.filter_field()
    device_id: ID | None = strawberry_django.filter_field()

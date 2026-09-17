"""GraphQL query definitions for the netbox_facts plugin."""

import strawberry
import strawberry_django

from .types import (
    CollectionPlanType,
    FactsReportEntryType,
    FactsReportType,
    MACAddressType,
    MACVendorType,
)

__all__ = ("FactsQuery",)


@strawberry.type(name="Query")
class FactsQuery:
    """Query fields contributed by netbox_facts.

    Field names are prefixed to keep them distinct from the core NetBox
    queries (dcim already provides mac_address and mac_address_list).
    """

    facts_mac_address: MACAddressType = strawberry_django.field()
    facts_mac_address_list: list[MACAddressType] = strawberry_django.field()

    facts_mac_vendor: MACVendorType = strawberry_django.field()
    facts_mac_vendor_list: list[MACVendorType] = strawberry_django.field()

    facts_collection_plan: CollectionPlanType = strawberry_django.field()
    facts_collection_plan_list: list[CollectionPlanType] = strawberry_django.field()

    facts_report: FactsReportType = strawberry_django.field()
    facts_report_list: list[FactsReportType] = strawberry_django.field()

    facts_report_entry: FactsReportEntryType = strawberry_django.field()
    facts_report_entry_list: list[FactsReportEntryType] = strawberry_django.field()

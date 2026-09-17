"""GraphQL object types for the netbox_facts models.

GraphQL type names share a single global namespace with NetBox core and every
other installed plugin, so each type here carries a "Facts" prefix.
"""

import strawberry_django
from netbox.graphql.types import BaseObjectType, NetBoxObjectType
from utilities.querysets import RestrictedQuerySet

from netbox_facts import models

from .filters import (
    CollectionPlanFilter,
    FactsReportEntryFilter,
    FactsReportFilter,
    MACAddressFilter,
    MACVendorFilter,
)

__all__ = (
    "CollectionPlanType",
    "FactsReportEntryType",
    "FactsReportType",
    "MACAddressType",
    "MACVendorType",
)


@strawberry_django.type(
    models.MACAddress,
    name="FactsMACAddressType",
    fields="__all__",
    filters=MACAddressFilter,
    pagination=True,
)
class MACAddressType(NetBoxObjectType):
    # MACAddressField has no GraphQL mapping of its own.
    mac_address: str


@strawberry_django.type(
    models.MACVendor,
    name="FactsMACVendorType",
    fields="__all__",
    filters=MACVendorFilter,
    pagination=True,
)
class MACVendorType(NetBoxObjectType):
    # MACPrefixField has no GraphQL mapping of its own.
    mac_prefix: str


@strawberry_django.type(
    models.CollectionPlan,
    name="FactsCollectionPlanType",
    # napalm_args holds connection credentials and is never exposed here.
    exclude=["napalm_args"],
    filters=CollectionPlanFilter,
    pagination=True,
)
class CollectionPlanType(NetBoxObjectType):
    pass


@strawberry_django.type(
    models.FactsReport,
    name="FactsReportType",
    fields="__all__",
    filters=FactsReportFilter,
    pagination=True,
)
class FactsReportType(BaseObjectType):
    pass


@strawberry_django.type(
    models.FactsReportEntry,
    name="FactsReportEntryType",
    fields="__all__",
    filters=FactsReportEntryFilter,
    pagination=True,
)
class FactsReportEntryType(BaseObjectType):
    @classmethod
    def get_queryset(cls, queryset, info, **kwargs):
        """Restrict entries to those the requesting user may view.

        The inherited implementation relies on the queryset carrying
        RestrictedQuerySet.restrict(), which the entry model's default
        manager does not provide.
        """
        permitted = RestrictedQuerySet(model=models.FactsReportEntry).restrict(info.context.request.user, "view")
        return queryset.filter(pk__in=permitted.values("pk"))

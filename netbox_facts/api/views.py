from django.db.models import Count

from netbox.api.viewsets import NetBoxModelViewSet

from .. import filtersets, models
from .serializers import MACAddressSerializer, MACVendorSerializer


class MACAddressViewSet(NetBoxModelViewSet):
    """
    Defines the view set for the django MACAddress model & associates it to a view.
    """

    queryset = models.MACAddress.objects.prefetch_related("tags").annotate(
        interfaces_count=Count("seen_by_interfaces"),
    )
    serializer_class = MACAddressSerializer
    filterset_class = filtersets.MACAddressFilterSet


class MACVendorViewSet(NetBoxModelViewSet):
    """
    Defines the view set for the django MACVendor model & associates it to a view.
    """

    queryset = models.MACVendor.objects.prefetch_related("tags").annotate(
        instances_count=Count("instances"),
    )
    serializer_class = MACVendorSerializer
    filterset_class = filtersets.MACVendorFilterSet

from django.db.models import Count
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from netbox.api.viewsets import NetBoxModelViewSet

from .. import filtersets, models
from ..helpers.applier import apply_entries, skip_entries
from .serializers import (
    MACAddressSerializer,
    MACVendorSerializer,
    CollectionPlanSerializer,
    FactsReportSerializer,
    FactsReportEntrySerializer,
)


class MACAddressViewSet(NetBoxModelViewSet):
    """
    Defines the view set for the django MACAddress model & associates it to a view.
    """

    queryset = models.MACAddress.objects.prefetch_related("tags").annotate(
        interfaces_count=Count("interfaces"),
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


class CollectorViewSet(NetBoxModelViewSet):
    """
    Defines the view set for the django Collector model & associates it to a view.
    """

    queryset = models.CollectionPlan.objects.prefetch_related("tags")
    serializer_class = CollectionPlanSerializer
    filterset_class = filtersets.CollectorFilterSet


class FactsReportViewSet(NetBoxModelViewSet):
    """ViewSet for FactsReport with apply/skip actions."""

    queryset = models.FactsReport.objects.prefetch_related("tags").annotate(
        entry_count=Count("entries"),
    )
    serializer_class = FactsReportSerializer
    filterset_class = filtersets.FactsReportFilterSet

    @action(detail=True, methods=["post"])
    def apply(self, request, pk=None):
        """Apply selected entries: POST with {"entries": [pk, pk, ...]}"""
        report = self.get_object()
        entry_pks = request.data.get("entries", [])
        if not entry_pks:
            return Response(
                {"detail": "No entries specified."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        applied, failed = apply_entries(report, entry_pks)
        return Response({
            "applied": applied,
            "failed": failed,
        })

    @action(detail=True, methods=["post"])
    def skip(self, request, pk=None):
        """Skip selected entries: POST with {"entries": [pk, pk, ...]}"""
        report = self.get_object()
        entry_pks = request.data.get("entries", [])
        if not entry_pks:
            return Response(
                {"detail": "No entries specified."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        count = skip_entries(report, entry_pks)
        return Response({"skipped": count})

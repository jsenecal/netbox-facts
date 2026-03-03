from django.db.models import Count
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from netbox.api.viewsets import NetBoxModelViewSet

from ..exceptions import OperationNotSupported
from .. import filtersets, models
from ..helpers.applier import apply_entries, skip_entries
from .serializers import (
    MACAddressSerializer,
    MACVendorSerializer,
    CollectionPlanSerializer,
    FactsReportSerializer,
    FactsReportEntrySerializer,
)


class FactsMutationThrottle(UserRateThrottle):
    """Throttle mutating actions (run, apply, skip) to 30 requests/minute."""

    rate = "30/minute"


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

    @action(detail=True, methods=["post"], throttle_classes=[FactsMutationThrottle])
    def run(self, request, pk=None):
        """Enqueue a collection job for this plan."""
        plan = self.get_object()
        try:
            job = plan.enqueue_collection_job(request)
        except OperationNotSupported as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        return Response({"job": job.pk}, status=status.HTTP_202_ACCEPTED)


class FactsReportViewSet(NetBoxModelViewSet):
    """ViewSet for FactsReport with apply/skip actions."""

    queryset = models.FactsReport.objects.annotate(
        entry_count=Count("entries"),
    )
    serializer_class = FactsReportSerializer
    filterset_class = filtersets.FactsReportFilterSet

    def _validate_entry_ownership(self, report, entry_pks):
        """Check all entry PKs belong to the report. Returns invalid PKs or None."""
        valid_pks = set(
            report.entries.filter(pk__in=entry_pks).values_list("pk", flat=True)
        )
        invalid_pks = set(entry_pks) - valid_pks
        return invalid_pks or None

    @action(detail=True, methods=["post"], throttle_classes=[FactsMutationThrottle])
    def apply(self, request, pk=None):
        """Apply selected entries: POST with {"entries": [pk, pk, ...]}"""
        report = self.get_object()
        entry_pks = request.data.get("entries", [])
        if not entry_pks:
            return Response(
                {"detail": "No entries specified."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        invalid_pks = self._validate_entry_ownership(report, entry_pks)
        if invalid_pks:
            return Response(
                {"detail": f"Entries {sorted(invalid_pks)} do not belong to this report."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        applied, failed = apply_entries(report, entry_pks)
        return Response({
            "applied": applied,
            "failed": failed,
        })

    @action(detail=True, methods=["post"], throttle_classes=[FactsMutationThrottle])
    def skip(self, request, pk=None):
        """Skip selected entries: POST with {"entries": [pk, pk, ...]}"""
        report = self.get_object()
        entry_pks = request.data.get("entries", [])
        if not entry_pks:
            return Response(
                {"detail": "No entries specified."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        invalid_pks = self._validate_entry_ownership(report, entry_pks)
        if invalid_pks:
            return Response(
                {"detail": f"Entries {sorted(invalid_pks)} do not belong to this report."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        count = skip_entries(report, entry_pks)
        return Response({"skipped": count})

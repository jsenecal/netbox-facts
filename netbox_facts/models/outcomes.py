"""Per-device outcomes of a collection run.

A report says what a run found; without these rows it cannot say what it
tried. One row per device the run attempted records whether the device was
collected from and, when it was not, which of the collector's categorized
reasons applies -- so "0 of 50 devices answered" survives the job log the
run wrote it to, and a plan's history can be read device by device.
"""

from django.db import models
from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _
from utilities.querysets import RestrictedQuerySet

from ..choices import (
    FAILED_DEVICE_OUTCOMES,
    SKIPPED_DEVICE_OUTCOMES,
    DeviceOutcomeChoices,
)

__all__ = (
    "DEVICE_OUTCOME_COUNT_ANNOTATIONS",
    "OUTCOME_MESSAGE_LENGTH",
    "FactsReportDeviceOutcome",
)

#: How much of a failure detail an outcome row keeps. The message is a
#: one-line hint pointing at the job log, not a transcript.
OUTCOME_MESSAGE_LENGTH = 500

#: The device counts a report is read by, as queryset annotations. Defined
#: once and shared by the API queryset and the report page, so the two cannot
#: come to group the outcomes differently. Keys are the names both surfaces
#: use for them. Each count is over distinct rows because the API queryset
#: also counts entries: two multi-valued joins in one query multiply each
#: other's rows, and a plain Count would then report the product.
DEVICE_OUTCOME_COUNT_ANNOTATIONS = {
    "device_count": Count("device_outcomes", distinct=True),
    "device_ok_count": Count(
        "device_outcomes",
        filter=Q(device_outcomes__outcome=DeviceOutcomeChoices.OUTCOME_OK),
        distinct=True,
    ),
    "device_failed_count": Count(
        "device_outcomes",
        filter=Q(device_outcomes__outcome__in=FAILED_DEVICE_OUTCOMES),
        distinct=True,
    ),
    "device_skipped_count": Count(
        "device_outcomes",
        filter=Q(device_outcomes__outcome__in=SKIPPED_DEVICE_OUTCOMES),
        distinct=True,
    ),
}


class FactsReportDeviceOutcome(models.Model):
    """What one collection run made of one device."""

    report = models.ForeignKey(
        to="netbox_facts.FactsReport",
        on_delete=models.CASCADE,
        related_name="device_outcomes",
    )
    device = models.ForeignKey(
        to="dcim.Device",
        on_delete=models.CASCADE,
        related_name="+",
    )
    outcome = models.CharField(
        max_length=50,
        choices=DeviceOutcomeChoices,
        help_text=_("Whether the device was collected from, and why not when it was not."),
    )
    duration = models.FloatField(
        null=True,
        blank=True,
        help_text=_("Seconds spent connecting to and collecting from the device. Null when it was never dialed."),
    )
    entry_count = models.PositiveIntegerField(
        default=0,
        help_text=_("How many report entries this device's pass produced."),
    )
    message = models.CharField(
        max_length=OUTCOME_MESSAGE_LENGTH,
        blank=True,
        default="",
        help_text=_("Short detail for a device that was not collected from."),
    )

    # Outcomes are plain rows of a report rather than NetBox BaseModels, so
    # the default manager has to be swapped explicitly for object-level
    # permissions (queryset.restrict()) to work in the REST layer.
    objects = RestrictedQuerySet.as_manager()

    class Meta:
        # Insertion order is the order the run attempted the devices in,
        # which is the order its log reads in too.
        ordering = ["pk"]
        verbose_name = _("Facts Report Device Outcome")
        verbose_name_plural = _("Facts Report Device Outcomes")
        indexes = [
            models.Index(fields=["report", "outcome"]),
        ]

    def __str__(self):
        return f"{self.device}: {self.get_outcome_display()}"

    def get_outcome_color(self):
        return DeviceOutcomeChoices.colors.get(self.outcome)

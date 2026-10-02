"""Grace-period tracking for objects a collection run no longer finds.

A sweep that moves straight from "absent from the device" to "deleted"
believes every run it makes. One flapping collection -- a device briefly
unreachable mid-run, a transceiver reseated between two passes -- is then
enough to remove real data. A row here is the memory that makes the
second stage possible: it records when a plan first failed to find an
object, so a later run can tell a one-off miss from an object that has
been gone for days.

The row is the authority on the grace period; the visibility tag the
sweep also applies is advisory, there so a pending removal shows up on
the object itself before anything is removed.
"""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.db import models
from django.utils.translation import gettext_lazy as _
from utilities.querysets import RestrictedQuerySet

__all__ = ("OrphanCandidate",)


class OrphanCandidate(models.Model):
    """One object a plan has stopped finding, and since when."""

    plan = models.ForeignKey(
        to="netbox_facts.CollectionPlan",
        on_delete=models.CASCADE,
        related_name="orphan_candidates",
    )
    device = models.ForeignKey(
        to="dcim.Device",
        on_delete=models.CASCADE,
        related_name="+",
        help_text=_("The device whose collection stopped reporting the object."),
    )
    # A generic key because the sweeps that write these rows reconcile
    # several unrelated models -- addresses, inventory items, modules --
    # and a row says only "this object is missing", which none of them
    # needs a column of its own for.
    content_type = models.ForeignKey(
        to="contenttypes.ContentType",
        on_delete=models.CASCADE,
        related_name="+",
    )
    object_id = models.PositiveBigIntegerField()
    object = GenericForeignKey("content_type", "object_id")
    first_missing = models.DateTimeField(
        verbose_name=_("first missing"),
        help_text=_("When the plan first failed to find the object. The grace period is measured from here."),
    )
    last_missing = models.DateTimeField(
        verbose_name=_("last missing"),
        help_text=_("When the plan last confirmed the object still absent."),
    )

    # Candidates are plain rows rather than NetBox BaseModels, so the
    # default manager has to be swapped explicitly for object-level
    # permissions (queryset.restrict()) to work wherever they are read.
    objects = RestrictedQuerySet.as_manager()

    class Meta:
        # Oldest absence first: the row at the top is the one closest to
        # having its removal proposed.
        ordering = ["first_missing", "pk"]
        verbose_name = _("Orphan Candidate")
        verbose_name_plural = _("Orphan Candidates")
        constraints = [
            # One clock per plan and object. Two plans may both be missing
            # the same object and each keeps its own first sighting of the
            # absence, because each runs on its own schedule.
            models.UniqueConstraint(
                fields=["plan", "content_type", "object_id"],
                name="netbox_facts_orphancandidate_unique",
            ),
        ]
        indexes = [
            # The terminal cleanup looks a row up by the object alone, for
            # every plan holding one.
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self):
        return f"{self.object_repr} missing since {self.first_missing}"

    @property
    def object_repr(self):
        """Name the absent object, falling back to its generic key.

        An object deleted outside the plugin leaves the key dangling, and
        a row that can no longer say what it tracked is still worth
        printing by what it points at.
        """
        obj = self.object
        if obj is None:
            return f"{self.content_type} {self.object_id}"
        return f"{self.content_type.name} {obj}"

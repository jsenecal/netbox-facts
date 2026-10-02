"""The two-stage stale lifecycle: mark an object orphaned, remove it later.

Both ends of the lifecycle live here. The collector calls in when a sweep
finds an object missing, or finds one again; the applier calls in when a
reviewer finally acts on an absence. Keeping the row and the visibility
tag in step is the whole job, so neither caller writes one without the
other.
"""

import logging

from django.contrib.contenttypes.models import ContentType
from extras.models.tags import Tag

from netbox_facts.constants import ORPHAN_TAG_SLUG
from netbox_facts.models.orphans import OrphanCandidate

logger = logging.getLogger("netbox_facts")

__all__ = (
    "clear_orphan_mark",
    "forget_absences",
    "mark_orphaned",
    "record_absence",
    "release_orphan_candidates",
)


def orphan_tag():
    """Return the visibility tag, or None when it is not there.

    Looked up by slug, which a data migration fixes, rather than by a name
    an operator can edit. A missing tag is a misconfiguration worth
    logging but not worth failing a run over: the candidate rows are what
    the grace period is actually measured from, and the tag only makes a
    pending removal visible.
    """
    tag = Tag.objects.filter(slug=ORPHAN_TAG_SLUG).first()
    if tag is None:
        logger.warning("Tag with slug '%s' is missing; orphaned objects will not be marked", ORPHAN_TAG_SLUG)
    return tag


def mark_orphaned(obj) -> None:
    """Put the visibility tag on an object whose grace period is running."""
    tag = orphan_tag()
    if tag is not None and hasattr(obj, "tags"):
        obj.tags.add(tag)


def clear_orphan_mark(obj) -> None:
    """Take the visibility tag back off an object that is no longer orphaned."""
    tag = orphan_tag()
    if tag is not None and hasattr(obj, "tags"):
        obj.tags.remove(tag)


def record_absence(plan, device, obj, now):
    """Note that a plan did not find an object, and return the first time it did not.

    The first absence starts the clock the grace period is measured
    against; every later one only moves the confirmation stamp, so a run
    cannot push an object's removal further away by finding it missing
    again. The device recorded is the one the first absence was seen from.
    """
    candidate, created = OrphanCandidate.objects.get_or_create(
        plan=plan,
        content_type=ContentType.objects.get_for_model(obj),
        object_id=obj.pk,
        defaults={"device": device, "first_missing": now, "last_missing": now},
    )
    if not created:
        candidate.last_missing = now
        candidate.save(update_fields=["last_missing"])
    return candidate.first_missing


def _settle(rows, obj=None) -> None:
    """Take the visibility tag off what a set of grace rows named, and drop them.

    Both ends of the lifecycle settle rows through here -- a sweep that
    found an object again, and a removal that has been carried out -- so
    what it takes to resolve a row's object, including the content type
    whose model has left the installation, is decided once and cannot
    come to cover one end and not the other.

    A caller already holding the object hands it over rather than having
    it read back; every row reaching such a call names that one object.
    The objects are resolved before the rows go, because the rows are
    the only record of which objects they were.
    """
    if obj is not None:
        resolved = [obj]
    else:
        resolved = [
            found
            for found in (_resolve_generic_object(row.content_type_id, row.object_id) for row in rows)
            if found is not None
        ]

    for found in resolved:
        clear_orphan_mark(found)
    rows.delete()


def forget_absences(plan, device, model, absent_ids) -> None:
    """Forget a plan's grace rows for objects of one model it saw again.

    Driven off the rows rather than off the objects the run saw: a sweep
    looks at every object of its kind on the device and holds very few of
    them in grace, so only a row-keyed pass stays cheap on a device with
    nothing orphaned. Whatever the sweep did not list as absent, it found.

    Scoped to the plan and device whose sweep is reporting, because a
    sighting is that sweep's news: another plan's clock on the same object
    is its own business.
    """
    _settle(
        OrphanCandidate.objects.filter(
            plan=plan,
            device=device,
            content_type=ContentType.objects.get_for_model(model),
        ).exclude(object_id__in=absent_ids)
    )


def release_orphan_candidates(content_type_id, object_id, obj=None) -> None:
    """Settle an object's absence: drop its grace rows and its visibility tag.

    Called where the absence has been acted on -- the object removed, or
    its assignment taken away -- by the run that acted or by the reviewer
    who applied the entry. Rows are dropped for every plan rather than
    only the acting one: what the rows point at is gone or no longer
    assigned, so no plan's clock on it still means anything, and a generic
    foreign key has no cascade to drop them later.

    ``obj`` is the instance for a caller that is holding it already -- the
    run about to remove it -- which saves reading it back.
    """
    _settle(
        OrphanCandidate.objects.filter(content_type_id=content_type_id, object_id=object_id),
        obj=obj,
    )


def _resolve_generic_object(content_type_id, object_id):
    """Load the object a generic key names, or None if it is not there.

    A stored key outlives what it pointed at twice over: the row being
    settled may name an object this run has just deleted, and a content
    type whose model has left the installation -- an uninstalled plugin --
    keeps its row and resolves to no class at all.
    """
    model = ContentType.objects.get_for_id(content_type_id).model_class()
    if model is None:
        return None
    return model.objects.filter(pk=object_id).first()

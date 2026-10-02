from core.choices import JobStatusChoices
from dcim.fields import mac_unix_expanded_uppercase
from dcim.models.devices import Manufacturer
from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver
from extras.models import Tag
from netaddr import EUI
from utilities.exceptions import AbortRequest

from .constants import AUTO_D_TAG_SLUG
from .models import CollectionPlan, MACAddress, MACVendor

DISCOVERY_TAG_RENAME_MESSAGE = (
    "netbox-facts relies on this tag's slug for stale handling and ownership "
    "checks across every collector and applier. Renaming the slug would "
    "silently break those checks for every object already tagged. Change "
    "the display name, color, or description instead."
)
DISCOVERY_TAG_DELETE_MESSAGE = (
    "netbox-facts relies on this tag for stale handling and ownership "
    "checks across every collector and applier. Deleting it would silently "
    "break those checks for every object already tagged."
)


@receiver(pre_save, sender=Tag)
def protect_discovery_tag_slug(instance, **kwargs):  # pylint: disable=unused-argument
    """Block changing the discovery tag's slug.

    The slug is the stable handle every ownership gate in
    helpers/collector.py and helpers/applier.py matches against (see
    helpers/netbox.py:get_discovery_tag()); nothing else about the tag is
    load-bearing, so name, color, and description edits are left alone. A
    pk-less instance is a row that does not exist yet -- including the one
    migration 0035 creates -- and has no prior slug to protect, so it is
    never blocked here.
    """
    if not instance.pk:
        return
    previous_slug = Tag.objects.filter(pk=instance.pk).values_list("slug", flat=True).first()
    if previous_slug == AUTO_D_TAG_SLUG and instance.slug != AUTO_D_TAG_SLUG:
        raise AbortRequest(DISCOVERY_TAG_RENAME_MESSAGE)


@receiver(pre_delete, sender=Tag)
def protect_discovery_tag_delete(instance, **kwargs):  # pylint: disable=unused-argument
    """Block deleting the discovery tag outright.

    Raising AbortRequest here is what NetBox's generic object views, bulk
    delete view, and REST API viewsets all already catch and turn into a
    clean user-facing error (see utilities/exceptions.py and
    utilities/error_handlers.py upstream) instead of a 500: it is the same
    mechanism core NetBox uses to cleanly abort a request from a signal
    receiver, not a plugin-specific convention.
    """
    if instance.slug == AUTO_D_TAG_SLUG:
        raise AbortRequest(DISCOVERY_TAG_DELETE_MESSAGE)


@receiver(post_save, sender=MACAddress)
def handle_mac_change(instance: MACAddress, **kwargs):  # pylint: disable=unused-argument
    """
    Update vendor foreign key when MACAddress is created or updated.
    """
    if instance.vendor is None:
        try:
            vendor = MACVendor.objects.get_by_mac_address(instance.mac_address)
            MACAddress.objects.filter(pk=instance.pk).update(vendor=vendor)
        except MACVendor.DoesNotExist:  # pylint: disable=no-member # type: ignore
            vendor_name = instance.vendor_name_from_mac_address
            if vendor_name is None:
                return
            try:
                manufacturer = Manufacturer.objects.get(name=vendor_name)
            except (
                Manufacturer.DoesNotExist  # pylint: disable=no-member # type: ignore
            ):
                other_vendor = MACVendor.objects.filter(vendor_name=vendor_name).exclude(manufacturer=None).first()
                if other_vendor is not None:
                    manufacturer = other_vendor.manufacturer
                else:
                    manufacturer = None
            vendor = MACVendor(
                manufacturer=manufacturer,
                vendor_name=vendor_name,
                mac_prefix=instance.mac_address,
            )
            vendor.save()
    elif int(instance.vendor.mac_prefix) & ~0x0000FFFFFF != int(instance.mac_address) & ~0x0000FFFFFF:
        vendor = MACVendor.objects.get_by_mac_address(instance.mac_address)
        MACAddress.objects.filter(pk=instance.pk).update(vendor=vendor)


@receiver(post_save, sender=MACVendor)
def handle_mac_vendor_change(instance: MACVendor, **kwargs):  # pylint: disable=unused-argument
    """
    Update vendor foreign key when a MACVendor is created or updated.
    """
    # Normalize prefix to colon-separated lowercase format matching PostgreSQL
    # macaddr::text output (e.g. "dd:ee:ff"). The instance attribute may have
    # any dialect depending on how it was created, so we normalize explicitly.
    prefix = EUI(
        int(instance.mac_prefix) & ~0x0000FFFFFF,
        version=48,
        dialect=mac_unix_expanded_uppercase,
    )
    prefix_str = str(prefix).lower()[:8]  # "dd:ee:ff"
    mac_addresses = MACAddress.objects.filter(mac_address__startswith=prefix_str)
    mac_addresses.update(vendor=instance)


@receiver(post_save, sender=CollectionPlan)
def handle_collection_job_change(instance: CollectionPlan, created=False, **kwargs):  # pylint: disable=unused-argument
    """
    Schedule or cancel collection jobs when a CollectionPlan is saved.
    Mirrors the DataSource sync scheduling pattern from core/signals.py.

    The plan enqueues whatever schedule its fields now describe; this
    handler only has to clear up after a schedule the plan no longer
    has.
    """
    from netbox_facts.jobs import CollectionJobRunner

    if not instance.enqueue_schedule() and not created:
        # Drop the schedule this plan no longer describes. Only
        # future-dated jobs are candidates: a pending job has already
        # been handed to a worker, and a cron or one-time job carries no
        # interval, so the scheduled status is the only thing they have
        # in common.
        for job in (
            CollectionJobRunner.get_jobs(instance).defer("data").filter(status=JobStatusChoices.STATUS_SCHEDULED)
        ):
            job.delete()

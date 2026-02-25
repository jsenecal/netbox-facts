from django.db.models.signals import post_save
from django.dispatch import receiver

from dcim.models.devices import Manufacturer

from .models import MACAddress, MACVendor, CollectionPlan


@receiver(post_save, sender=MACAddress)
def handle_mac_change(
    instance: MACAddress, **kwargs
):  # pylint: disable=unused-argument
    """
    Update vendor foreign key when MACAddress is created or updated.
    """
    if instance.vendor is None:
        try:
            instance.vendor = MACVendor.objects.get_by_mac_address(instance.mac_address)  # type: ignore
            instance.save()
        except MACVendor.DoesNotExist:  # pylint: disable=no-member # type: ignore
            vendor_name = instance.vendor_name_from_mac_address
            if vendor_name is None:
                return
            try:
                manufacturer = Manufacturer.objects.get(name=vendor_name)
            except (
                Manufacturer.DoesNotExist  # pylint: disable=no-member # type: ignore
            ):
                other_vendor = (
                    MACVendor.objects.filter(vendor_name=vendor_name)
                    .exclude(manufacturer=None)
                    .first()
                )
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
    elif (
        int(instance.vendor.mac_prefix) & ~0x0000FFFFFF
        != int(instance.mac_address) & ~0x0000FFFFFF
    ):
        instance.vendor = MACVendor.objects.get_by_mac_address(instance.mac_address)  # type: ignore
        instance.save()


@receiver(post_save, sender=MACVendor)
def handle_mac_vendor_change(
    instance: MACVendor, **kwargs
):  # pylint: disable=unused-argument
    """
    Update vendor foreign key when a MACVendor is created or updated.
    """
    mac_addresses = MACAddress.objects.filter(
        mac_address__startswith=str(instance.mac_prefix)[:6]
    )
    mac_addresses.update(vendor=instance)

@receiver(post_save, sender=CollectionPlan)
def handle_collection_job_change(
    instance: CollectionPlan, **kwargs
): # pylint: disable=unused-argument
    """
    Schedule collection job when a collection plan is created or updated.
    """
    pass
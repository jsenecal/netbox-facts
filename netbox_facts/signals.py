import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import MACAddress, MACVendor


@receiver(post_save, sender=MACAddress)
def handle_mac_change(instance: MACAddress, **kwargs):  # pylint: disable=unused-argument
    """
    Update vendor foreign key when MACAddress is created or updated.
    """
    if instance.vendor is None:
        try:
            instance.vendor = MACVendor.objects.get_by_mac_address(instance.mac_address)
            instance.save()
        except MACVendor.DoesNotExist:
            vendor = MACVendor(name=instance.vendor_name_from_mac_address, mac_prefix=instance.mac_address)
            vendor.save()
        # Handled by MACVendor post_save signal below
        # instance.vendor = vendor  # type: ignore
        # instance.save()
    elif int(instance.vendor.mac_prefix) & ~0x0000FFFFFF != int(instance.mac_address) & ~0x0000FFFFFF:
        instance.vendor = MACVendor.objects.get_by_mac_address(instance.mac_address)
        instance.save()


@receiver(post_save, sender=MACVendor)
def handle_mac_vendor_change(instance: MACVendor, **kwargs):  # pylint: disable=unused-argument
    """
    Update vendor foreign key when a MACVendor is created or updated.
    """
    mac_addresses = MACAddress.objects.filter(mac_address__startswith=str(instance.mac_prefix)[:6])
    mac_addresses.update(vendor=instance)

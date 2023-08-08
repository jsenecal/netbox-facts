import logging

from django.db.models.signals import post_save, post_delete, pre_delete
from django.dispatch import receiver

from .models import MACAddress, MACVendor


@receiver(post_save, sender=MACAddress)
def handle_mac_change(instance: MACAddress, created, **kwargs):
    """
    Update vendor foreign key when MACAddress is created or updated.
    """
    if instance.vendor is None:
        vendor = MACVendor(name=instance.vendor_name_from_mac_address, mac_prefix=instance.mac_address)
        vendor.save()
        instance.vendor = vendor  # type: ignore
        instance.save()
    elif int(instance.vendor.mac_prefix) & ~0xFFF != int(instance.mac_address) & ~0xFFF:
        instance.vendor = MACVendor.objects.get_by_mac_address(instance.mac_address)
        instance.save()

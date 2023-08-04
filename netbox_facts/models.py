import re
from django.db import models
from django.urls import reverse

from netbox.models import NetBoxModel
from dcim.fields import MACAddressField


class MACAddress(NetBoxModel):
    """Model representing a MAC Address seen by one or multiple devices."""

    mac_address = MACAddressField(unique=True)
    seen_by_interfaces = models.ManyToManyField("dcim.interface", related_name="known_mac_addresses")

    class Meta:
        ordering = ("mac_address",)

    @property
    def last_seen(self):
        """Proxy to the last_updated attribute."""
        return self.last_updated

    @property
    def first_seen(self):
        """Proxy to the created attribute."""
        return self.created

    @property
    def vendor(self):
        return self.mac_address.oui.registration().org

    def __str__(self):
        return str(self.mac_address)

    def get_absolute_url(self):
        """Return the absolute URL of the MAC Address object."""
        return reverse("plugins:netbox_facts:mac_address", args=[self.pk])


class MACVendorManager(models.Manager):
    def get_by_mac_address(self, mac):
        clean_mac = re.sub(r"[^A-F0-9]+", "", mac.upper())
        return self.get(mac_prefix=clean_mac[:6])


class MACVendor(NetBoxModel):
    """Model representing MAC Address Vendor Information."""

    name = models.CharField(max_length=255)
    mac_prefix = models.CharField(max_length=8, unique=True)

    objects = MACVendorManager()

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return str(self.name)

    def get_absolute_url(self):
        return reverse("plugins:netbox_facts:mac_vendor", args=[self.pk])

""" Models for NetBox Facts Plugin. """
from django.db import models
from django.urls import reverse
from netaddr import EUI

from netbox.models import NetBoxModel
from dcim.fields import MACAddressField


class MACAddress(NetBoxModel):
    """Model representing a MAC Address seen by one or multiple devices."""

    mac_address = MACAddressField(unique=True)
    seen_by_interfaces = models.ManyToManyField("dcim.interface", related_name="known_mac_addresses")

    class Meta:
        """Meta class for MACAddress."""
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
        """Return the vendor name from the MAC Address."""
        return self.mac_address.oui.registration().org  # type: ignore

    def __str__(self):
        return str(self.mac_address)

    def get_absolute_url(self):
        """Return the absolute URL of the MAC Address object."""
        return reverse("plugins:netbox_facts:mac_address", args=[self.pk])

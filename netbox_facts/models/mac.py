""" Models for NetBox Facts Plugin. """
import re

from netaddr import EUI, NotRegisteredError

from dcim.fields import MACAddressField, mac_unix_expanded_uppercase
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from utilities.querysets import RestrictedQuerySet

from netbox.models import NetBoxModel

from .fields import MACPrefixField

__all__ = ["MACAddress", "MACVendor"]


class MACAddress(NetBoxModel):
    """Model representing a MAC Address seen by one or multiple devices."""

    mac_address = MACAddressField(
        _("MAC Address"),
        unique=True,
    )
    description = models.CharField(_("Description"), max_length=200, blank=True)
    vendor = models.ForeignKey(
        "netbox_facts.MACVendor",
        on_delete=models.PROTECT,
        related_name="instances",
        help_text=_("MAC Address Vendor automatically overriden from MAC Address when known."),
        null=True,
        blank=True,
    )

    known_by = models.ManyToManyField("dcim.interface", related_name="known_mac_addresses")

    comments = models.TextField(
        _("Comments"),
        blank=True,
    )

    class Meta:
        """Meta class for MACAddress."""

        indexes = [
            models.Index(
                fields=[
                    "mac_address",
                ]
            ),
        ]
        ordering = ("mac_address",)
        verbose_name = _("MAC Address")
        verbose_name_plural = _("MAC Addresses")

    @property
    def last_seen(self):
        """Proxy to the last_updated attribute."""
        return self.last_updated

    @property
    def first_seen(self):
        """Proxy to the created attribute."""
        return self.created

    @property
    def vendor_name_from_mac_address(self) -> str | None:
        """Return the vendor name from the MAC Address."""
        if not hasattr(self.mac_address, "oui"):
            self.refresh_from_db()
        try:
            return self.mac_address.oui.registration().org  # pylint: disable=no-member
        except NotRegisteredError:
            return None

    def save(self, *args, **kwargs):
        try:
            self.vendor = MACVendor.objects.get_by_mac_address(self.mac_address)
        except MACVendor.DoesNotExist:  # pylint: disable=no-member
            pass
        super().save(*args, **kwargs)

    def __str__(self):
        return str(self.mac_address)

    def get_absolute_url(self):
        """Return the absolute URL of the MAC Address object."""
        return reverse("plugins:netbox_facts:macaddress_detail", args=[self.pk])


class MACVendorManager(models.Manager.from_queryset(RestrictedQuerySet)):
    """Manager for the MACVendor model."""

    use_in_migrations = True

    def get_by_mac_address(self, mac):
        """Return the MACVendor object matching the MAC Address first 6 bytes."""
        clean_mac = EUI(int(mac) & ~0x0000FFFFFF, version=48, dialect=mac_unix_expanded_uppercase)
        return self.get(mac_prefix=clean_mac)


class MACVendor(NetBoxModel):
    """Model representing MAC Address Vendor Information."""

    name = models.CharField(_("Name"), max_length=100)
    mac_prefix = MACPrefixField(_("MAC Prefix"), max_length=8, unique=True)
    comments = models.TextField(
        _("Comments"),
        blank=True,
    )

    objects = MACVendorManager()

    class Meta:
        """Meta class for MACVendor."""

        verbose_name = _("MAC Vendor")
        verbose_name_plural = _("MAC Vendors")
        ordering = ("name",)
        indexes = [
            models.Index(
                fields=[
                    "mac_prefix",
                ]
            ),
        ]

    def __str__(self):
        return str(self.name)

    def get_absolute_url(self):
        """Return the absolute URL of the MAC Vendor object."""
        return reverse("plugins:netbox_facts:macvendor_detail", args=[self.pk])

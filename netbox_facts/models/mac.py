""" Models for NetBox Facts Plugin. """
from dcim.fields import MACAddressField, mac_unix_expanded_uppercase
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from netaddr import EUI, NotRegisteredError
from netbox_facts.choices import CollectionTypeChoices
from taggit.managers import TaggableManager
from utilities.querysets import RestrictedQuerySet

from netbox.models import NetBoxModel

from ..fields import MACPrefixField

__all__ = ["MACAddress", "MACVendor"]


class MACAddress(NetBoxModel):
    """Model representing a MAC Address seen by one or multiple devices."""

    # Override tags with explicit related_name to avoid clash with dcim.MACAddress
    tags = TaggableManager(
        through='extras.TaggedItem',
        ordering=('weight', 'name'),
        related_name='netbox_facts_macaddress_set',
    )

    mac_address = MACAddressField(
        _("MAC Address"),
        unique=True,
    )
    description = models.CharField(_("Description"), max_length=200, blank=True)
    vendor = models.ForeignKey(
        "netbox_facts.MACVendor",
        on_delete=models.PROTECT,
        related_name="instances",
        help_text=_(
            "This field is automatically overriden from MAC Address when known."
        ),
        null=True,
        blank=True,
        editable=False,
    )

    interfaces = models.ManyToManyField(
        "dcim.Interface",
        related_name="mac_addresses",
        through="MACAddressInterfaceRelation",
        editable=False,
    )

    ip_addresses = models.ManyToManyField(
        "ipam.IPAddress",
        related_name="mac_addresses",
        through="MACAddressIPAddressRelation",
        editable=False,
    )

    device_interface = models.OneToOneField(
        "dcim.Interface",
        on_delete=models.SET_NULL,
        related_name="+",
        editable=False,
        null=True,
        blank=True,
    )

    last_seen = models.DateTimeField(
        _("Last Seen"),
        null=True,
        blank=True,
        editable=False,
    )

    discovery_method = models.CharField(
        _("Discovery Method"),
        max_length=50,
        blank=True,
        null=True,
        editable=False,
        choices=CollectionTypeChoices,
    )

    comments = models.TextField(
        _("Comments"),
        blank=True,
    )

    class Meta:  # pylint: disable=too-few-public-methods
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
    def first_seen(self):
        """Proxy to the created attribute."""
        return self.created

    @property
    def vendor_name_from_mac_address(self) -> str | None:
        """Return the vendor name from the MAC Address."""
        try:
            if not hasattr(self.mac_address, "oui"):
                self.refresh_from_db()
            return (
                self.mac_address.oui.registration().org  # pylint: disable=no-member # type: ignore
            )
        except NotRegisteredError:
            return None

    def save(self, *args, **kwargs):
        try:
            if isinstance(self.mac_address, str):
                self.mac_address = EUI(
                    self.mac_address,
                    version=48,
                    dialect=mac_unix_expanded_uppercase,
                )
            self.vendor = MACVendor.objects.get_by_mac_address(self.mac_address)  # type: ignore
        except MACVendor.DoesNotExist:  # pylint: disable=no-member
            pass
        super().save(*args, **kwargs)

    def __str__(self):
        return str(self.mac_address)

    def get_absolute_url(self):
        """Return the absolute URL of the MAC Address object."""
        return reverse("plugins:netbox_facts:macaddress", args=[self.pk])


class MACAddressInterfaceRelation(models.Model):
    """Model representing an Interface Assignment."""

    mac_address = models.ForeignKey("MACAddress", on_delete=models.CASCADE)
    interface = models.ForeignKey("dcim.Interface", on_delete=models.CASCADE)
    created = models.DateTimeField(
        verbose_name=_("created"), auto_now_add=True, blank=True, null=True
    )
    last_updated = models.DateTimeField(
        verbose_name=_("last updated"), auto_now=True, blank=True, null=True
    )

    class Meta:
        unique_together = ("mac_address", "interface")


class MACAddressIPAddressRelation(models.Model):
    """Model representing an IPAddress Assignment."""

    mac_address = models.ForeignKey("MACAddress", on_delete=models.CASCADE)
    ip_address = models.ForeignKey("ipam.ipaddress", on_delete=models.CASCADE)
    created = models.DateTimeField(
        verbose_name=_("created"), auto_now_add=True, blank=True, null=True
    )
    last_updated = models.DateTimeField(
        verbose_name=_("last updated"), auto_now=True, blank=True, null=True
    )

    class Meta:
        unique_together = ("mac_address", "ip_address")


class MACVendorManager(models.Manager.from_queryset(RestrictedQuerySet)):
    """Manager for the MACVendor model."""

    use_in_migrations = True

    def get_by_mac_address(self, mac):
        """Return the MACVendor object matching the MAC Address first 6 bytes."""
        clean_mac = EUI(
            int(mac) & ~0x0000FFFFFF, version=48, dialect=mac_unix_expanded_uppercase
        )
        return self.get(mac_prefix=clean_mac)


class MACVendor(NetBoxModel):
    """Model representing MAC Address Vendor Prefix Information."""

    manufacturer = models.ForeignKey(
        "dcim.Manufacturer",
        on_delete=models.PROTECT,
        related_name="mac_prefixes",
        blank=True,
        null=True,
    )
    vendor_name = models.CharField(_("Vendor Name"), max_length=200)
    mac_prefix = MACPrefixField(_("MAC Prefix"), max_length=8, unique=True)
    comments = models.TextField(
        _("Comments"),
        blank=True,
    )

    objects = MACVendorManager()

    class Meta:
        """Meta class for MACVendorPrefix."""

        verbose_name = _("MAC Vendor Prefix")
        verbose_name_plural = _("MAC Vendor Prefixes")
        ordering = ("manufacturer", "vendor_name")

    def __str__(self):
        if self.manufacturer:
            return f"{self.manufacturer} ({str(self.mac_prefix)[:8]})"
        return f"{self.vendor_name} ({str(self.mac_prefix)[:8]})"

    def get_absolute_url(self):
        """Return the absolute URL of the MAC Vendor object."""
        return reverse("plugins:netbox_facts:macvendor_detail", args=[self.pk])

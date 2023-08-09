from netbox.forms import NetBoxModelForm, NetBoxModelFilterSetForm
from .models import MACAddress, MACVendor

__all__ = ["MACAddressForm", "MACVendorForm"]


class MACAddressForm(NetBoxModelForm):
    class Meta:
        model = MACAddress
        fields = ("mac_address", "vendor", "description", "comments", "tags")


class MACVendorForm(NetBoxModelForm):
    class Meta:
        model = MACVendor
        fields = ("name", "mac_prefix", "comments", "tags")

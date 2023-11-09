from netbox.api.serializers import WritableNestedSerializer
from rest_framework import serializers

from ..models import MACVendor


class NestedMACVendorSerializer(WritableNestedSerializer):
    """
    Defines the nested serializer for the django MACVendor model
    """

    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_facts-api:macvendor-detail",
    )

    class Meta:
        """
        Associates the django model MACVendor & fields to the nested serializer.
        """

        model = MACVendor
        fields = ("id", "url", "display", "vendor_name", "manufacturer")

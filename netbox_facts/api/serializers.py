"""
Serializers control the translation of client data to and from Python objects,
while Django itself handles the database abstraction.
"""

from rest_framework import serializers

from netbox.api.serializers import NetBoxModelSerializer

from ..models import MACAddress, MACVendor, CollectorDefinition
from .nested_serializers import NestedMACVendorSerializer


class MACAddressSerializer(NetBoxModelSerializer):
    """
    Defines the serializer for the django MACAddress model.
    """

    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_facts-api:macaddress-detail",
    )
    vendor = NestedMACVendorSerializer(required=True, allow_null=False)
    interfaces_count = serializers.IntegerField(read_only=True)

    class Meta:
        """
        Associates the django model MACAddress & fields to the serializer.
        """

        model = MACAddress
        fields = (
            "id",
            "url",
            "display",
            "mac_address",
            "vendor",
            "description",
            "comments",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
            "interfaces_count",
        )


class MACVendorSerializer(NetBoxModelSerializer):
    """
    Defines the serializer for the django MACVendor model.
    """

    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_facts-api:macvendor-detail",
    )
    instances_count = serializers.IntegerField(read_only=True)

    class Meta:
        """
        Associates the django model MACVendor & fields to the serializer.
        """

        model = MACVendor
        fields = (
            "id",
            "url",
            "display",
            "manufacturer",
            "vendor_name",
            "mac_prefix",
            "comments",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
            "instances_count",
        )


class CollectorDefinitionSerializer(NetBoxModelSerializer):
    """
    Defines the serializer for the django CollectorDefinition model.
    """

    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_facts-api:collectordefinition-detail",
    )
    # instances_count = serializers.IntegerField(read_only=True)

    class Meta:
        """
        Associates the django model CollectorDefinition & fields to the serializer.
        """

        model = CollectorDefinition
        fields = (
            "id",
            "url",
            "display",
            "name",
            "priority",
            "status",
            "description",
            "collector_type",
            "comments",
            "devices",
            "device_status",
            "regions",
            "site_groups",
            "sites",
            "locations",
            "device_types",
            "roles",
            "platforms",
            "tenant_groups",
            "tenants",
            "napalm_driver",
            "napalm_args",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )

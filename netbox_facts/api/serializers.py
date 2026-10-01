"""
Serializers control the translation of client data to and from Python objects,
while Django itself handles the database abstraction.
"""

from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers
from users.api.serializers import UserSerializer

from ..helpers.napalm import mask_napalm_credentials, restore_masked_credentials
from ..models import (
    CollectionPlan,
    FactsReport,
    FactsReportDeviceOutcome,
    FactsReportEntry,
    MACAddress,
    MACVendor,
)
from ..models.outcomes import DEVICE_OUTCOME_COUNT_ANNOTATIONS
from .nested_serializers import NestedMACVendorSerializer


class MACAddressSerializer(NetBoxModelSerializer):
    """
    Defines the serializer for the django MACAddress model.
    """

    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_facts-api:macaddress-detail",
    )
    vendor = NestedMACVendorSerializer(required=False, allow_null=True, read_only=True)
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
            "last_seen",
            "interfaces_count",
        )
        brief_fields = (
            "id",
            "url",
            "display",
            "mac_address",
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
        brief_fields = (
            "id",
            "url",
            "display",
            "manufacturer",
            "vendor_name",
        )


class CollectionPlanSerializer(NetBoxModelSerializer):
    """
    Defines the serializer for the django Collector model.
    """

    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_facts-api:collectionplan-detail",
    )
    # Read-only: scheduled runs are enqueued as run_as unconditionally, while
    # the interactive path only honors it for superusers. Letting any account
    # with change permission set it would hand them another user's credentials.
    run_as = UserSerializer(nested=True, read_only=True)
    # Computed from the plan's schedule rather than stored, so a client
    # reads when the next run is due without replaying the rules itself.
    next_run = serializers.DateTimeField(read_only=True)

    def to_representation(self, instance):
        """Censor credential values stored in napalm_args."""
        data = super().to_representation(instance)
        if data.get("napalm_args"):
            data["napalm_args"] = mask_napalm_credentials(data["napalm_args"])
        return data

    def validate_napalm_args(self, value):
        """Keep stored credentials when a client round-trips censored values."""
        if value and self.instance is not None:
            value = restore_masked_credentials(value, self.instance.napalm_args)
        return value

    class Meta:
        """
        Associates the django model Collector & fields to the serializer.
        """

        model = CollectionPlan
        fields = (
            "id",
            "url",
            "display",
            "name",
            "priority",
            "status",
            "enabled",
            "detect_only",
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
            "allow_unscoped",
            "napalm_driver",
            "napalm_args",
            "connection_target",
            "scheduled_at",
            "interval",
            "cron_schedule",
            "last_run",
            "next_run",
            "run_as",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = (
            "id",
            "url",
            "display",
            "name",
        )
        extra_kwargs = {
            # The edit form offers dedicated credential fields; over the API
            # the same three keys of this document are the supported path.
            "napalm_args": {
                "help_text": (
                    "Arguments passed to the NAPALM driver as optional_args (JSON format). The username, "
                    "password and secret keys hold this plan's credentials, overriding the plugin-level "
                    "defaults; username and password are consumed as the driver's positional credentials "
                    "and never reach optional_args. All three are censored on read; submitting a censored "
                    "value back preserves the stored one."
                ),
            },
        }


class FactsReportEntrySerializer(serializers.ModelSerializer):
    """Serializer for FactsReportEntry."""

    class Meta:
        model = FactsReportEntry
        fields = (
            "id",
            "report",
            "action",
            "status",
            "collector_type",
            "entry_kind",
            "device",
            "object_type",
            "object_id",
            "object_repr",
            "display_title",
            "detected_values",
            "current_values",
            "change_hash",
            "error_message",
            "apply_error",
            "created",
            "applied_at",
        )
        read_only_fields = fields


class FactsReportDeviceOutcomeSerializer(serializers.ModelSerializer):
    """Serializer for FactsReportDeviceOutcome."""

    class Meta:
        model = FactsReportDeviceOutcome
        fields = (
            "id",
            "report",
            "device",
            "outcome",
            "duration",
            "entry_count",
            "message",
        )
        read_only_fields = fields


class FactsReportSerializer(serializers.ModelSerializer):
    """Serializer for FactsReport."""

    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_facts-api:factsreport-detail",
    )
    display = serializers.SerializerMethodField()
    entry_count = serializers.IntegerField(read_only=True)
    # Counted on the viewset's queryset rather than stored on the report, so
    # a client reads how a run fared device by device without listing the
    # outcome rows. Like entry_count, each is absent from a representation
    # built from an unannotated instance rather than rendered as a zero.
    device_count = serializers.IntegerField(read_only=True)
    device_ok_count = serializers.IntegerField(read_only=True)
    device_failed_count = serializers.IntegerField(read_only=True)
    device_skipped_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = FactsReport
        fields = (
            "id",
            "url",
            "display",
            "collection_plan",
            "status",
            "summary",
            "error_message",
            "entry_count",
            *DEVICE_OUTCOME_COUNT_ANNOTATIONS,
            "created",
            "completed_at",
        )
        brief_fields = (
            "id",
            "url",
            "display",
            "status",
        )

    def get_display(self, obj):
        return str(obj)

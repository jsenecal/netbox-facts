from django import forms
from django.forms import MultipleChoiceField
from dcim.choices import DeviceStatusChoices
from dcim.models.devices import Device, DeviceRole, DeviceType, Platform
from dcim.models.sites import Region, Site, SiteGroup, Location
from extras.choices import DurationChoices
from extras.models.tags import Tag
from netbox.forms import (
    NetBoxModelForm,
    NetBoxModelFilterSetForm,
    NetBoxModelBulkEditForm,
)
from utilities.forms.rendering import FieldSet
from netbox.forms.base import NetBoxModelImportForm
from tenancy.models.tenants import Tenant, TenantGroup
from utilities.forms.fields.dynamic import DynamicModelMultipleChoiceField
from utilities.forms.fields import CommentField
from django.utils.translation import gettext_lazy as _

from utilities.forms.widgets.datetime import DateTimePicker
from utilities.forms.widgets.misc import NumberWithOptions
from utilities.datetime import local_now
from .models import MACAddress, MACVendor, CollectionPlan

__all__ = ["MACAddressForm", "MACVendorForm", "CollectorForm"]


class MACAddressForm(NetBoxModelForm):
    class Meta:
        model = MACAddress
        fields = ("mac_address", "description", "comments", "tags")


class MACAddressBulkEditForm(NetBoxModelBulkEditForm):
    description = forms.CharField(
        label=_("Description"), max_length=200, required=False
    )
    comments = CommentField()

    model = MACAddress
    fieldsets = (FieldSet("mac_address", "description"),)
    nullable_fields = ("description", "comments", "tags")


class MACVendorForm(NetBoxModelForm):
    class Meta:
        model = MACVendor
        fields = ("vendor_name", "manufacturer", "mac_prefix", "comments", "tags")


class MACVendorBulkEditForm(NetBoxModelBulkEditForm):
    description = forms.CharField(
        label=_("Description"), max_length=200, required=False
    )
    comments = CommentField()

    model = MACVendor
    fieldsets = (
        FieldSet(
            "manufacturer",
        ),
    )
    nullable_fields = ("manufacturer", "comments", "tags")


class CollectorForm(NetBoxModelForm):
    """Form for creating and modifying a collectionplan."""

    regions = DynamicModelMultipleChoiceField(
        label=_("Regions"), queryset=Region.objects.all(), required=False
    )
    site_groups = DynamicModelMultipleChoiceField(
        label=_("Site groups"), queryset=SiteGroup.objects.all(), required=False
    )
    sites = DynamicModelMultipleChoiceField(
        label=_("Sites"), queryset=Site.objects.all(), required=False
    )
    locations = DynamicModelMultipleChoiceField(
        label=_("Locations"), queryset=Location.objects.all(), required=False
    )
    device = DynamicModelMultipleChoiceField(
        label=_("Devices"), queryset=Device.objects.all(), required=False
    )
    device_status = MultipleChoiceField(choices=DeviceStatusChoices, required=False)
    device_types = DynamicModelMultipleChoiceField(
        label=_("Device types"), queryset=DeviceType.objects.all(), required=False
    )
    roles = DynamicModelMultipleChoiceField(
        label=_("Roles"), queryset=DeviceRole.objects.all(), required=False
    )
    platforms = DynamicModelMultipleChoiceField(
        label=_("Platforms"), queryset=Platform.objects.all(), required=False
    )
    tenant_groups = DynamicModelMultipleChoiceField(
        label=_("Tenant groups"), queryset=TenantGroup.objects.all(), required=False
    )
    tenants = DynamicModelMultipleChoiceField(
        label=_("Tenants"), queryset=Tenant.objects.all(), required=False
    )
    tags = DynamicModelMultipleChoiceField(
        label=_("Tags"), queryset=Tag.objects.all(), required=False
    )

    scheduled_at = forms.DateTimeField(
        required=False,
        widget=DateTimePicker(),
        label=_("Schedule at"),
        help_text=_("Schedule execution to a set time"),
    )
    interval = forms.IntegerField(
        required=False,
        min_value=1,
        label=_("Repeat every"),
        widget=NumberWithOptions(options=DurationChoices),
        help_text=_("Interval at which this collection task is re-run (in minutes)"),
    )

    fieldsets = (
        FieldSet(
            "name",
            "priority",
            "collector_type",
            "description",
            "enabled",
            name=_("Collector"),
        ),
        FieldSet(
            "regions",
            "site_groups",
            "sites",
            "locations",
            "devices",
            "device_status",
            "device_types",
            "roles",
            "platforms",
            "tenant_groups",
            "tenants",
            "tags",
            name=_("Assignment"),
        ),
        FieldSet(
            "scheduled_at",
            "interval",
            name=_("Scheduling"),
        ),
        FieldSet("napalm_driver", "napalm_args", name=_("Runtime settings")),
    )

    class Meta:
        model = CollectionPlan
        fields = (
            "name",
            "priority",
            "collector_type",
            "description",
            "enabled",
            "regions",
            "site_groups",
            "sites",
            "locations",
            "devices",
            "device_status",
            "roles",
            "device_types",
            "platforms",
            "tenant_groups",
            "tenants",
            "tags",
            "comments",
            "scheduled_at",
            "interval",
            "napalm_driver",
            "napalm_args",
        )

    def __init__(self, *args, **kwargs):  # pylint: disable=no-member
        super().__init__(*args, **kwargs)
        now = local_now().strftime("%Y-%m-%d %H:%M:%S %Z")
        self.fields["scheduled_at"].help_text += _(
            " (current server time: <strong>{now}</strong>)"
        ).format(now=now)

    def clean(self):
        scheduled_time = self.cleaned_data.get("scheduled_at")
        if scheduled_time and scheduled_time < local_now():
            raise forms.ValidationError(
                {"scheduled_at": _("Scheduled time must be in the future.")}
            )

        # When interval is used without schedule at, schedule for the current time
        if self.cleaned_data.get("interval") and not scheduled_time:
            self.cleaned_data["scheduled_at"] = local_now()

        return self.cleaned_data

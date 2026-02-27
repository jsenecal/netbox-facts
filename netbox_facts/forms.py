from django import forms
from django.forms import MultipleChoiceField
from dcim.choices import DeviceStatusChoices
from dcim.models.devices import Device, DeviceRole, DeviceType, Platform, Manufacturer
from dcim.models.sites import Region, Site, SiteGroup, Location
from core.choices import JobIntervalChoices
from extras.models.tags import Tag
from netbox.forms import (
    NetBoxModelForm,
    NetBoxModelFilterSetForm,
    NetBoxModelBulkEditForm,
)
from utilities.forms.rendering import FieldSet
from netbox.forms.bulk_import import NetBoxModelImportForm
from tenancy.models.tenants import Tenant, TenantGroup
from utilities.forms.fields.dynamic import DynamicModelMultipleChoiceField
from utilities.forms.fields import CommentField
from django.utils.translation import gettext_lazy as _

from utilities.forms.widgets.datetime import DateTimePicker
from utilities.forms.widgets.misc import NumberWithOptions
from utilities.datetime import local_now
from .choices import (
    CollectionTypeChoices,
    CollectorPriorityChoices,
    CollectorStatusChoices,
    ReportStatusChoices,
)
from .models import MACAddress, MACVendor, CollectionPlan, FactsReport


def get_napalm_driver_choices():
    """Enumerate available NAPALM drivers (built-in + custom)."""
    import os
    from napalm._SUPPORTED_DRIVERS import SUPPORTED_DRIVERS

    # Custom drivers in netbox_facts.napalm (tried first by get_napalm_driver)
    custom_dir = os.path.join(os.path.dirname(__file__), "napalm")
    custom_drivers = sorted(
        f.replace(".py", "")
        for f in os.listdir(custom_dir)
        if f.endswith(".py") and f not in ("__init__.py", "helpers.py")
    )

    # Built-in NAPALM drivers (exclude "base")
    builtin_drivers = sorted(d for d in SUPPORTED_DRIVERS if d != "base")

    choices = [("", "---------")]
    if custom_drivers:
        choices += [(d, f"{d} (enhanced)") for d in custom_drivers]
    choices += [(d, d) for d in builtin_drivers if d not in custom_drivers]
    return choices

__all__ = [
    "MACAddressForm",
    "MACAddressBulkEditForm",
    "MACAddressFilterForm",
    "MACVendorForm",
    "MACVendorBulkEditForm",
    "MACVendorFilterForm",
    "CollectorForm",
    "CollectionPlanBulkEditForm",
    "CollectionPlanFilterForm",
    "FactsReportFilterForm",
]


# --------------------------------------------------------------------------
# MACAddress forms
# --------------------------------------------------------------------------

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
    fieldsets = (FieldSet("description"),)
    nullable_fields = ("description", "comments")


class MACAddressFilterForm(NetBoxModelFilterSetForm):
    model = MACAddress
    fieldsets = (
        FieldSet("q", "filter_id"),
        FieldSet("mac_address", "vendor", "description", name=_("Attributes")),
    )
    mac_address = forms.CharField(required=False, label=_("MAC Address"))
    vendor = DynamicModelMultipleChoiceField(
        queryset=MACVendor.objects.all(), required=False, label=_("Vendor")
    )
    description = forms.CharField(required=False, label=_("Description"))


# --------------------------------------------------------------------------
# MACVendor forms
# --------------------------------------------------------------------------

class MACVendorForm(NetBoxModelForm):
    class Meta:
        model = MACVendor
        fields = ("vendor_name", "manufacturer", "mac_prefix", "comments", "tags")


class MACVendorBulkEditForm(NetBoxModelBulkEditForm):
    manufacturer = DynamicModelMultipleChoiceField(
        queryset=Manufacturer.objects.all(), required=False, label=_("Manufacturer")
    )
    comments = CommentField()

    model = MACVendor
    fieldsets = (
        FieldSet("manufacturer",),
    )
    nullable_fields = ("manufacturer", "comments")


class MACVendorFilterForm(NetBoxModelFilterSetForm):
    model = MACVendor
    fieldsets = (
        FieldSet("q", "filter_id"),
        FieldSet("manufacturer", "mac_prefix", name=_("Attributes")),
    )
    manufacturer = DynamicModelMultipleChoiceField(
        queryset=Manufacturer.objects.all(), required=False, label=_("Manufacturer")
    )
    mac_prefix = forms.CharField(required=False, label=_("MAC Prefix"))


# --------------------------------------------------------------------------
# CollectionPlan forms
# --------------------------------------------------------------------------

class CollectorForm(NetBoxModelForm):
    """Form for creating and modifying a collectionplan."""

    regions = DynamicModelMultipleChoiceField(
        label=_("Regions"), queryset=Region.objects.all(), required=False, selector=True
    )
    site_groups = DynamicModelMultipleChoiceField(
        label=_("Site groups"), queryset=SiteGroup.objects.all(), required=False, selector=True
    )
    sites = DynamicModelMultipleChoiceField(
        label=_("Sites"), queryset=Site.objects.all(), required=False, selector=True
    )
    locations = DynamicModelMultipleChoiceField(
        label=_("Locations"), queryset=Location.objects.all(), required=False, selector=True
    )
    devices = DynamicModelMultipleChoiceField(
        label=_("Devices"), queryset=Device.objects.all(), required=False, selector=True
    )
    device_status = MultipleChoiceField(choices=DeviceStatusChoices, required=False, label=_("Device Statuses"))
    device_types = DynamicModelMultipleChoiceField(
        label=_("Device types"), queryset=DeviceType.objects.all(), required=False, selector=True
    )
    roles = DynamicModelMultipleChoiceField(
        label=_("Roles"), queryset=DeviceRole.objects.all(), required=False, selector=True
    )
    platforms = DynamicModelMultipleChoiceField(
        label=_("Platforms"), queryset=Platform.objects.all(), required=False, selector=True
    )
    tenant_groups = DynamicModelMultipleChoiceField(
        label=_("Tenant groups"), queryset=TenantGroup.objects.all(), required=False, selector=True
    )
    tenants = DynamicModelMultipleChoiceField(
        label=_("Tenants"), queryset=Tenant.objects.all(), required=False, selector=True
    )
    tags = DynamicModelMultipleChoiceField(
        label=_("Tags"), queryset=Tag.objects.all(), required=False, selector=True
    )

    napalm_driver = forms.ChoiceField(
        choices=get_napalm_driver_choices,
        label=_("NAPALM Driver"),
        help_text=_("The NAPALM driver to use when connecting to devices"),
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
        widget=NumberWithOptions(options=JobIntervalChoices),
        help_text=_("Interval at which this collection task is re-run (in minutes)"),
    )

    fieldsets = (
        FieldSet(
            "name",
            "priority",
            "collector_type",
            "description",
            "enabled",
            "detect_only",
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
            "detect_only",
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


class CollectionPlanBulkEditForm(NetBoxModelBulkEditForm):
    enabled = forms.NullBooleanField(required=False, label=_("Enabled"))
    priority = forms.ChoiceField(
        choices=CollectorPriorityChoices, required=False, label=_("Priority")
    )
    description = forms.CharField(
        label=_("Description"), max_length=200, required=False
    )
    comments = CommentField()

    model = CollectionPlan
    fieldsets = (
        FieldSet("enabled", "priority", "description"),
    )
    nullable_fields = ("description", "comments")


class CollectionPlanFilterForm(NetBoxModelFilterSetForm):
    model = CollectionPlan
    fieldsets = (
        FieldSet("q", "filter_id"),
        FieldSet(
            "priority", "status", "collector_type", "enabled",
            name=_("Attributes"),
        ),
    )
    priority = forms.MultipleChoiceField(
        choices=CollectorPriorityChoices, required=False, label=_("Priority")
    )
    status = forms.MultipleChoiceField(
        choices=CollectorStatusChoices, required=False, label=_("Status")
    )
    collector_type = forms.MultipleChoiceField(
        choices=CollectionTypeChoices, required=False, label=_("Collector Type")
    )
    enabled = forms.NullBooleanField(required=False, label=_("Enabled"))


class FactsReportFilterForm(NetBoxModelFilterSetForm):
    model = FactsReport
    fieldsets = (
        FieldSet("q", "filter_id"),
        FieldSet("collection_plan", "status", name=_("Attributes")),
    )
    collection_plan = DynamicModelMultipleChoiceField(
        queryset=CollectionPlan.objects.all(), required=False, label=_("Collection Plan")
    )
    status = forms.MultipleChoiceField(
        choices=ReportStatusChoices, required=False, label=_("Status")
    )

from core.choices import JobIntervalChoices
from dcim.choices import DeviceStatusChoices
from dcim.models.devices import Device, DeviceRole, DeviceType, Manufacturer, Platform
from dcim.models.sites import Location, Region, Site, SiteGroup
from django import forms
from django.contrib import messages
from django.forms import MultipleChoiceField
from django.utils.translation import gettext_lazy as _
from extras.models.tags import Tag
from netbox.context import current_request
from netbox.forms import (
    NetBoxModelBulkEditForm,
    NetBoxModelFilterSetForm,
    NetBoxModelForm,
    NetBoxModelImportForm,
)
from netbox.forms.bulk_import import NetBoxModelImportForm
from tenancy.models.tenants import Tenant, TenantGroup
from utilities.datetime import local_now
from utilities.forms.fields import (
    CommentField,
    CSVChoiceField,
    CSVModelChoiceField,
    CSVModelMultipleChoiceField,
    CSVMultipleChoiceField,
)
from utilities.forms.fields.dynamic import DynamicModelMultipleChoiceField
from utilities.forms.rendering import FieldSet
from utilities.forms.widgets.datetime import DateTimePicker
from utilities.forms.widgets.misc import NumberWithOptions

from netbox_facts.helpers.collector import HAS_NETBOX_ROUTING
from netbox_facts.helpers.napalm import mask_napalm_credentials, restore_masked_credentials

from .choices import (
    CollectionTypeChoices,
    CollectorPriorityChoices,
    CollectorStatusChoices,
    EntryActionChoices,
    EntryKindChoices,
    EntryStatusChoices,
    ReportStatusChoices,
)
from .models import CollectionPlan, FactsReport, FactsReportEntry, MACAddress, MACVendor


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
    "MACAddressImportForm",
    "MACAddressBulkEditForm",
    "MACAddressFilterForm",
    "MACVendorForm",
    "MACVendorImportForm",
    "MACVendorBulkEditForm",
    "MACVendorFilterForm",
    "CollectorForm",
    "CollectionPlanImportForm",
    "CollectionPlanBulkEditForm",
    "CollectionPlanFilterForm",
    "FactsReportFilterForm",
    "FactsReportEntryFilterForm",
]


# --------------------------------------------------------------------------
# MACAddress forms
# --------------------------------------------------------------------------


class MACAddressForm(NetBoxModelForm):
    class Meta:
        model = MACAddress
        fields = ("mac_address", "description", "comments", "tags")


class MACAddressImportForm(NetBoxModelImportForm):
    class Meta:
        model = MACAddress
        fields = ("mac_address", "description", "comments", "tags")


class MACAddressBulkEditForm(NetBoxModelBulkEditForm):
    description = forms.CharField(label=_("Description"), max_length=200, required=False)
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
    vendor = DynamicModelMultipleChoiceField(queryset=MACVendor.objects.all(), required=False, label=_("Vendor"))
    description = forms.CharField(required=False, label=_("Description"))


# --------------------------------------------------------------------------
# MACVendor forms
# --------------------------------------------------------------------------


class MACVendorForm(NetBoxModelForm):
    class Meta:
        model = MACVendor
        fields = ("vendor_name", "manufacturer", "mac_prefix", "comments", "tags")


class MACVendorImportForm(NetBoxModelImportForm):
    manufacturer = CSVModelChoiceField(
        queryset=Manufacturer.objects.all(),
        to_field_name="name",
        required=False,
        label=_("Manufacturer"),
    )

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
        FieldSet(
            "manufacturer",
        ),
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


def describe_plan_scope(plan: CollectionPlan) -> str:
    """Return a one-line summary of the devices a saved plan resolves to."""
    unready = plan.get_unready_devices().count()
    matched = _("{count} devices").format(count=plan.get_matched_device_count())
    if not unready:
        return matched
    return _("{matched} ({unready} without a usable IP for the selected connection target)").format(
        matched=matched, unready=unready
    )


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
    tags = DynamicModelMultipleChoiceField(label=_("Tags"), queryset=Tag.objects.all(), required=False, selector=True)

    matched_devices = forms.CharField(
        label=_("Currently matched"),
        required=False,
        disabled=True,
        help_text=_("Devices resolved by the scope stored on this plan. Refreshed when the plan is saved."),
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
            "allow_unscoped",
            "matched_devices",
            name=_("Assignment"),
        ),
        FieldSet(
            "scheduled_at",
            "interval",
            name=_("Scheduling"),
        ),
        FieldSet("napalm_driver", "napalm_args", "connection_target", name=_("Runtime settings")),
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
            "allow_unscoped",
            "comments",
            "scheduled_at",
            "interval",
            "napalm_driver",
            "napalm_args",
            "connection_target",
        )

    def __init__(self, *args, **kwargs):  # pylint: disable=no-member
        super().__init__(*args, **kwargs)
        if not HAS_NETBOX_ROUTING:
            routing_types = {
                CollectionTypeChoices.TYPE_BGP,
                CollectionTypeChoices.TYPE_OSPF,
            }
            self.fields["collector_type"].choices = [
                c for c in self.fields["collector_type"].choices if c[0] not in routing_types
            ]
        now = local_now().strftime("%Y-%m-%d %H:%M:%S %Z")
        self.fields["scheduled_at"].help_text += _(" (current server time: <strong>{now}</strong>)").format(now=now)
        if self.instance.pk:
            # Censor stored credentials instead of rendering them verbatim
            if isinstance(self.instance.napalm_args, dict):
                self.initial["napalm_args"] = mask_napalm_credentials(self.instance.napalm_args)
            self.initial["matched_devices"] = describe_plan_scope(self.instance)
        else:
            del self.fields["matched_devices"]

    def clean_napalm_args(self):
        """Keep stored credentials when the censored values are submitted unchanged."""
        value = self.cleaned_data.get("napalm_args")
        if value and self.instance.pk and isinstance(value, dict):
            value = restore_masked_credentials(value, self.instance.napalm_args)
        return value

    def clean(self):
        scheduled_time = self.cleaned_data.get("scheduled_at")
        if scheduled_time and scheduled_time < local_now():
            raise forms.ValidationError({"scheduled_at": _("Scheduled time must be in the future.")})

        # When interval is used without schedule at, schedule for the current time
        if self.cleaned_data.get("interval") and not scheduled_time:
            self.cleaned_data["scheduled_at"] = local_now()

        return self.cleaned_data

    def save(self, *args, **kwargs):
        """Save the plan and report the scope it now resolves to.

        The resolved count only becomes accurate once the scoping
        assignments are stored, which rules out reporting it during
        validation. NetBox exposes the active request through a context
        variable, so the feedback rides along with the redirect to the plan.
        """
        instance = super().save(*args, **kwargs)
        request = current_request.get()
        if instance.pk is None or request is None or not hasattr(request, "_messages"):
            return instance

        warning = instance.get_scope_warning()
        if warning:
            messages.warning(request, warning)
        else:
            messages.info(
                request, _("This collection plan matches {scope}.").format(scope=describe_plan_scope(instance))
            )
        return instance


def scope_import_field(queryset, label, to_field_name="name"):
    """Return a CSV column importing one device-scoping dimension."""
    return CSVModelMultipleChoiceField(
        queryset=queryset,
        to_field_name=to_field_name,
        required=False,
        label=label,
        help_text=_("{field} values separated by commas, encased with double quotes").format(
            field=to_field_name.capitalize()
        ),
    )


class CollectionPlanImportForm(NetBoxModelImportForm):
    collector_type = CSVChoiceField(
        choices=CollectionTypeChoices,
        label=_("Collector Type"),
    )
    priority = CSVChoiceField(
        choices=CollectorPriorityChoices,
        required=False,
        label=_("Priority"),
    )
    # Scope columns: without them every imported plan is born unscoped, which
    # resolves to the whole device fleet. The tags column is inherited from
    # NetBoxModelImportForm and doubles as the device-tag dimension.
    devices = scope_import_field(Device.objects.all(), _("Devices"))
    regions = scope_import_field(Region.objects.all(), _("Regions"))
    site_groups = scope_import_field(SiteGroup.objects.all(), _("Site groups"))
    sites = scope_import_field(Site.objects.all(), _("Sites"))
    locations = scope_import_field(Location.objects.all(), _("Locations"))
    device_types = scope_import_field(DeviceType.objects.all(), _("Device types"), to_field_name="model")
    roles = scope_import_field(DeviceRole.objects.all(), _("Roles"))
    platforms = scope_import_field(Platform.objects.all(), _("Platforms"))
    tenant_groups = scope_import_field(TenantGroup.objects.all(), _("Tenant groups"))
    tenants = scope_import_field(Tenant.objects.all(), _("Tenants"))
    device_status = CSVMultipleChoiceField(
        choices=DeviceStatusChoices,
        required=False,
        label=_("Device Statuses"),
        help_text=_("Device statuses separated by commas, encased with double quotes"),
    )

    class Meta:
        model = CollectionPlan
        fields = (
            "name",
            "collector_type",
            "napalm_driver",
            "connection_target",
            "priority",
            "enabled",
            "detect_only",
            "devices",
            "regions",
            "site_groups",
            "sites",
            "locations",
            "device_types",
            "roles",
            "platforms",
            "tenant_groups",
            "tenants",
            "device_status",
            "allow_unscoped",
            "description",
            "comments",
            "tags",
        )


class CollectionPlanBulkEditForm(NetBoxModelBulkEditForm):
    enabled = forms.NullBooleanField(required=False, label=_("Enabled"))
    priority = forms.ChoiceField(choices=CollectorPriorityChoices, required=False, label=_("Priority"))
    description = forms.CharField(label=_("Description"), max_length=200, required=False)
    comments = CommentField()

    model = CollectionPlan
    fieldsets = (FieldSet("enabled", "priority", "description"),)
    nullable_fields = ("description", "comments")


class CollectionPlanFilterForm(NetBoxModelFilterSetForm):
    model = CollectionPlan
    fieldsets = (
        FieldSet("q", "filter_id"),
        FieldSet(
            "priority",
            "status",
            "collector_type",
            "enabled",
            name=_("Attributes"),
        ),
    )
    priority = forms.MultipleChoiceField(choices=CollectorPriorityChoices, required=False, label=_("Priority"))
    status = forms.MultipleChoiceField(choices=CollectorStatusChoices, required=False, label=_("Status"))
    collector_type = forms.MultipleChoiceField(choices=CollectionTypeChoices, required=False, label=_("Collector Type"))
    enabled = forms.NullBooleanField(required=False, label=_("Enabled"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not HAS_NETBOX_ROUTING:
            routing_types = {
                CollectionTypeChoices.TYPE_BGP,
                CollectionTypeChoices.TYPE_OSPF,
            }
            self.fields["collector_type"].choices = [
                c for c in self.fields["collector_type"].choices if c[0] not in routing_types
            ]


class FactsReportFilterForm(NetBoxModelFilterSetForm):
    model = FactsReport
    fieldsets = (
        FieldSet("q", "filter_id"),
        FieldSet("collection_plan", "status", name=_("Attributes")),
    )
    collection_plan = DynamicModelMultipleChoiceField(
        queryset=CollectionPlan.objects.all(), required=False, label=_("Collection Plan")
    )
    status = forms.MultipleChoiceField(choices=ReportStatusChoices, required=False, label=_("Status"))


class FactsReportEntryFilterForm(NetBoxModelFilterSetForm):
    """Filter form for the per-status entry tabs of a report."""

    model = FactsReportEntry
    fieldsets = (
        FieldSet("q", "filter_id"),
        FieldSet("device", "action", "status", name=_("Review")),
        FieldSet("collector_type", "entry_kind", name=_("Source")),
    )
    device = DynamicModelMultipleChoiceField(queryset=Device.objects.all(), required=False, label=_("Device"))
    action = forms.MultipleChoiceField(choices=EntryActionChoices, required=False, label=_("Action"))
    status = forms.MultipleChoiceField(choices=EntryStatusChoices, required=False, label=_("Status"))
    collector_type = forms.MultipleChoiceField(choices=CollectionTypeChoices, required=False, label=_("Collector Type"))
    entry_kind = forms.MultipleChoiceField(choices=EntryKindChoices, required=False, label=_("Entry Kind"))

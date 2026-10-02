"""Collection models."""

from __future__ import annotations

import copy
import importlib
import json
import logging
from datetime import timedelta
from typing import Any, NamedTuple

from core.choices import JobStatusChoices
from core.models import Job
from dcim.choices import DeviceStatusChoices
from dcim.models import Device
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from extras.choices import LogLevelChoices
from napalm import get_network_driver
from napalm.base.base import NetworkDriver
from netbox.context_managers import event_tracking
from netbox.models import NetBoxModel
from netbox.models.features import EventRulesMixin, JobsMixin
from netbox.plugins.utils import get_plugin_config
from utilities.querysets import RestrictedQuerySet
from utilities.request import copy_safe_request

from netbox_facts.exceptions import OperationNotSupported

from ..choices import (
    ENHANCED_DRIVER_PREFIX,
    CollectionTypeChoices,
    CollectorPriorityChoices,
    CollectorStatusChoices,
    ConnectionTargetChoices,
    collector_supported_drivers,
    driver_supports_collector,
    normalize_driver_name,
)
from ..helpers import NapalmCollector
from ..helpers.napalm import (
    NAPALM_SENSITIVE_KEYS,
    resolve_napalm_credentials,
    strip_napalm_credentials,
)
from ..helpers.netbox import filtered_list_url
from ..helpers.scheduling import next_cron_occurrence, validate_cron_expression

logger = logging.getLogger("netbox_facts")


class ScopeDimension(NamedTuple):
    """One device-scoping field and how it maps onto the device list."""

    field: str
    """Name of the many-to-many field on CollectionPlan."""
    lookup: str
    """ORM lookup applied to Device by get_devices_queryset()."""
    url_param: str
    """Query parameter understood by the NetBox device list."""
    label: str
    """Display title for this dimension in the plan detail panel."""
    url_value: str = "pk"
    """Attribute of the related object carried in that query parameter."""


#: Panel and URL order: matches the historical Assignment panel layout.
SCOPE_DIMENSIONS: tuple[ScopeDimension, ...] = (
    ScopeDimension("regions", "region__in", "region_id", "Regions"),
    ScopeDimension("site_groups", "site__group__in", "site_group_id", "Site Groups"),
    ScopeDimension("sites", "site__in", "site_id", "Sites"),
    ScopeDimension("locations", "location__in", "location_id", "Locations"),
    ScopeDimension("devices", "pk__in", "id", "Devices"),
    ScopeDimension("device_types", "device_type__in", "device_type_id", "Device Types"),
    ScopeDimension("roles", "role__in", "role_id", "Roles"),
    ScopeDimension("platforms", "platform__in", "platform_id", "Platforms"),
    ScopeDimension("tenant_groups", "tenant__group__in", "tenant_group_id", "Tenant Groups"),
    ScopeDimension("tenants", "tenant__in", "tenant_id", "Tenants"),
    ScopeDimension("tags", "tags__in", "tag", "Tags", "slug"),
)

#: Per-dimension cap on pks serialized into get_devices_list_url()'s query
#: string. A plan pinning thousands of objects in one dimension would
#: otherwise emit a multi-hundred-KB href that exceeds browser and server
#: URL length limits.
MAX_URL_PKS_PER_DIMENSION = 100


def platform_driver_custom_field() -> str:
    """Return the Platform custom field that carries a NAPALM driver name."""
    return get_plugin_config("netbox_facts", "platform_driver_custom_field", "napalm_driver") or ""


def platform_napalm_driver_name(platform) -> str:
    """Return the NAPALM driver name a Platform stands for, or an empty string.

    NetBox carried Platform.napalm_driver only until 3.6, which removed it
    along with the rest of core NAPALM support, and 4.x offers no
    replacement field. The mapping is therefore the plugin's own
    convention: a text or selection custom field on dcim.Platform, named by
    the platform_driver_custom_field setting, falling back to the
    platform's slug -- which already reads as a driver name on the usual
    platforms (junos, ios, eos).

    The custom field is read straight off the stored JSON rather than
    through .cf, which would resolve every custom field defined for
    Platform and cache the result on the instance; a text value needs no
    deserializing, and resolution runs once per device in a collection run.
    """
    if platform is None:
        return ""
    field_name = platform_driver_custom_field()
    if field_name:
        value = (platform.custom_field_data or {}).get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return platform.slug or ""


def load_napalm_driver(driver_name: str) -> type[NetworkDriver]:
    """Return a NAPALM driver class, preferring plugin-local enhanced drivers.

    Plugin-local drivers are imported directly because napalm's
    get_network_driver() rejects dotted module paths outside its own
    namespaces before attempting any import.

    Raises ModuleImportError when no driver answers to the name.
    """
    bare_name = normalize_driver_name(driver_name)
    try:
        module = importlib.import_module(f"{ENHANCED_DRIVER_PREFIX}{bare_name}")
    except ModuleNotFoundError:
        return get_network_driver(bare_name)
    for obj in vars(module).values():
        if isinstance(obj, type) and issubclass(obj, NetworkDriver) and obj.__module__ == module.__name__:
            return obj
    return get_network_driver(bare_name)


def scope_warning_threshold() -> int:
    """Return the device count above which a plan's scope is worth flagging."""
    return get_plugin_config("netbox_facts", "scope_warning_threshold", 500) or 0


def exceeds_scope_warning_threshold(count: int) -> bool:
    """Return True when a resolved device count is above the threshold.

    A threshold of 0 disables the warning entirely; a count equal to the
    threshold is still considered acceptable.
    """
    threshold = scope_warning_threshold()
    return bool(threshold) and count > threshold


class CollectionPlan(NetBoxModel, EventRulesMixin, JobsMixin):
    """Model representing a Collection Plan"""

    name = models.CharField(verbose_name=_("name"), max_length=100, unique=True)
    priority = models.CharField(choices=CollectorPriorityChoices, default=CollectorPriorityChoices.PRIORITY_LOW)
    status = models.CharField(
        max_length=50,
        choices=CollectorStatusChoices,
        default=CollectorStatusChoices.NEW,
    )
    enabled = models.BooleanField(verbose_name=_("enabled"), default=True)
    description = models.CharField(verbose_name=_("description"), max_length=200, blank=True)
    run_as = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True)

    devices = models.ManyToManyField(to="dcim.Device", related_name="+", blank=True)
    device_status = ArrayField(
        models.CharField(
            max_length=50,
            choices=DeviceStatusChoices,
            default=DeviceStatusChoices.STATUS_ACTIVE,
        ),
        blank=True,
    )
    regions = models.ManyToManyField(to="dcim.Region", related_name="+", blank=True)
    site_groups = models.ManyToManyField(to="dcim.SiteGroup", related_name="+", blank=True)
    sites = models.ManyToManyField(to="dcim.Site", related_name="+", blank=True)
    locations = models.ManyToManyField(to="dcim.Location", related_name="+", blank=True)
    device_types = models.ManyToManyField(to="dcim.DeviceType", related_name="+", blank=True)
    roles = models.ManyToManyField(to="dcim.DeviceRole", related_name="+", blank=True)
    platforms = models.ManyToManyField(to="dcim.Platform", related_name="+", blank=True)
    tenant_groups = models.ManyToManyField(to="tenancy.TenantGroup", related_name="+", blank=True)
    tenants = models.ManyToManyField(to="tenancy.Tenant", related_name="+", blank=True)
    tags = models.ManyToManyField(to="extras.Tag", related_name="+", blank=True)

    allow_unscoped = models.BooleanField(
        verbose_name=_("allow unscoped"),
        default=False,
        help_text=_(
            "Allow this plan to run without any scoping. A plan with no scope resolves to every device in NetBox, "
            "so every run dials the whole fleet; enable this only for a deliberate fleet-wide plan."
        ),
    )

    collector_type = models.CharField(_("Collector Type"), max_length=50, choices=CollectionTypeChoices)

    napalm_driver = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="NAPALM driver",
        help_text=_(
            "The NAPALM driver to use for every device this plan targets. Leave blank to resolve the driver "
            "per device from its platform, which lets one plan span several vendors."
        ),
    )
    napalm_args = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="NAPALM arguments",
        help_text=_("Additional arguments to pass when initiating the NAPALM driver (JSON format)"),
    )

    scheduled_at = models.DateTimeField(
        verbose_name=_("scheduled at"),
        help_text=_(
            "When the first run is due. On its own this schedules a single run; alongside an interval or a cron "
            "schedule it holds the first run back until this time."
        ),
        blank=True,
        null=True,
    )
    interval = models.PositiveIntegerField(
        help_text=_("Interval at which this collection task is re-run (in minutes)<br>Leave blank to run only once."),
        verbose_name=_("Interval (minutes)"),
        blank=True,
        null=True,
    )
    cron_schedule = models.CharField(
        verbose_name=_("cron schedule"),
        max_length=100,
        blank=True,
        help_text=_(
            "Five-field cron expression evaluated in the server time zone, such as <code>0 2 * * 1-5</code> for "
            "02:00 on weekdays. Mutually exclusive with the interval."
        ),
    )

    last_run = models.DateTimeField(verbose_name=_("last run"), blank=True, null=True, editable=False)

    detect_only = models.BooleanField(
        default=False,
        help_text=_(
            "When enabled, collection runs produce a report without modifying "
            "NetBox objects. Changes can be reviewed and selectively applied."
        ),
    )

    stale_grace_days = models.PositiveSmallIntegerField(
        verbose_name=_("stale grace period (days)"),
        blank=True,
        null=True,
        help_text=_(
            "How many days an object this plan stops finding is marked orphaned before its removal is "
            "proposed. Leave blank to follow the stale_grace_period_days plugin setting; 0 removes a "
            "missing object on the first run that does not find it."
        ),
    )

    connection_target = models.CharField(
        max_length=20,
        choices=ConnectionTargetChoices,
        default=ConnectionTargetChoices.TARGET_PRIMARY,
        help_text=_(
            "Which IP address to use when connecting to devices. "
            '"Both" options try the first, then fall back to the second on connection failure.'
        ),
    )

    comments = models.TextField(
        _("Comments"),
        blank=True,
    )

    ## Netbox Models

    events = GenericRelation(
        "extras.EventRule",
        content_type_field="action_object_type",
        object_id_field="action_object_id",
    )

    ip_addresses = models.ManyToManyField(to="ipam.IPAddress", related_name="discovered_by", blank=True, editable=False)

    objects = RestrictedQuerySet.as_manager()

    clone_fields = (
        "status",
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
        "napalm_driver",
        "napalm_args",
        "interval",
        "cron_schedule",
        "detect_only",
        "connection_target",
        "allow_unscoped",
        "stale_grace_days",
    )

    class Meta:
        """Meta class for CollectionPlan."""

        ordering = ["priority", "name"]
        verbose_name = _("Collection Plan")
        verbose_name_plural = _("Collection Plans")
        permissions = [
            ("run_collector", "Can run a collection plan"),
            ("view_collector_results", "Can view collection plan run results"),
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.log = []
        self.current_job: Job | None = None
        self.check_stalled()

    def __str__(self):
        """String representation of the Collector object."""

        return str(self.name)

    def clean(self):
        """Clean the object."""
        if isinstance(self.napalm_args, str):
            self.napalm_args = dict()

        if not driver_supports_collector(self.collector_type, self.napalm_driver):
            raise ValidationError(
                {
                    "napalm_driver": _(
                        "The {collector} collector only has an implementation for these NAPALM drivers: "
                        "{drivers}. Pick one of those, or leave the driver blank to resolve it per device "
                        "from the device platform -- devices that resolve to another driver are then skipped "
                        "instead of failing the run."
                    ).format(
                        collector=self.get_collector_type_display(),
                        drivers=", ".join(collector_supported_drivers(self.collector_type) or ()),
                    )
                }
            )

        if self.cron_schedule and self.interval:
            message = _("A plan repeats either on an interval or on a cron schedule, not both.")
            raise ValidationError({"interval": message, "cron_schedule": message})

        if self.cron_schedule:
            try:
                validate_cron_expression(self.cron_schedule)
            except ValidationError as error:
                raise ValidationError({"cron_schedule": error.messages}) from error

        if not self.allow_unscoped and not self.has_scope():
            raise ValidationError(
                _(
                    "This plan has no scope, so it would target every device in NetBox. Select at least one of "
                    "devices, regions, site groups, sites, locations, device types, roles, platforms, tenant "
                    "groups, tenants, tags or device statuses -- or enable 'allow unscoped' to run it fleet-wide "
                    "on purpose."
                )
            )

    @property
    def ready(self):
        """Return True if the collector is ready to be run."""
        return not self.run_disabled_reason

    @property
    def run_disabled_reason(self):
        """Return why the collection plan cannot currently be run."""
        if not self.enabled:
            return _("Plan is disabled")
        if self.status in (
            CollectorStatusChoices.QUEUED,
            CollectorStatusChoices.WORKING,
        ):
            return _("A run is already queued or in progress")
        return ""

    @property
    def result(self):
        """Return the last created job"""
        return self.jobs.all().order_by("-created").first()

    @property
    def next_run(self):
        """Return when this plan is next due to run, or None."""
        return self.get_next_run()

    def _schedule_anchor(self, reference):
        """Return scheduled_at while it is still a not-before anchor.

        An anchor that has passed constrains nothing: a recurrence has
        already started by then, and a single run has already happened.
        """
        if self.scheduled_at and self.scheduled_at > reference:
            return self.scheduled_at
        return None

    def get_next_run(self, reference=None):
        """Return when the plan is next due, measured from reference (now by default).

        Three schedule shapes, in the order they take precedence:

        * A cron schedule fires next at the first occurrence after the
          reference, or after scheduled_at while that anchor is ahead.
        * An interval waits for scheduled_at while that anchor is ahead,
          then runs one interval after the last run. A plan that has
          never run is due immediately, which reads as the reference.
        * scheduled_at on its own is a single run at that time, so
          nothing is due once it has passed.

        A disabled plan is never due: saving one drops its schedule.
        """
        if not self.enabled:
            return None

        reference = reference or timezone.now()
        if self.interval and not self._schedule_anchor(reference):
            if self.last_run:
                return self.last_run + timedelta(minutes=self.interval)
            return reference

        parameters = self.get_schedule_parameters(reference)
        return parameters[0] if parameters else None

    def get_schedule_parameters(self, reference=None):
        """Return the (schedule_at, interval) a background job should carry.

        None means the plan has no schedule at all, so any job already
        scheduled for it is stale.

        An interval plan with no future anchor deliberately carries no
        schedule_at. NetBox's enqueue_once() compares the schedule of the
        job it finds against the one being requested, so handing it a
        fresh "now" on every save would delete the pending job and start
        another run every time the plan is edited. The next occurrence of
        a cron expression is safe to pass because it does not move until
        that occurrence has happened.
        """
        if not self.enabled:
            return None

        reference = reference or timezone.now()
        anchor = self._schedule_anchor(reference)

        if self.cron_schedule:
            next_occurrence = next_cron_occurrence(self.cron_schedule, anchor or reference)
            return (next_occurrence, None) if next_occurrence else None
        if self.interval:
            return anchor, self.interval
        if anchor:
            return anchor, None
        return None

    def enqueue_schedule(self) -> bool:
        """Enqueue the schedule this plan currently describes.

        Returns False when the plan has no schedule, which tells the
        caller that any job still scheduled for it is stale.
        """
        from netbox_facts.jobs import CollectionJobRunner

        parameters = self.get_schedule_parameters()
        if parameters is None:
            return False

        schedule_at, interval = parameters
        CollectionJobRunner.enqueue_once(
            instance=self,
            schedule_at=schedule_at,
            interval=interval,
            user=self.run_as,
            queue_name=self.priority,
        )
        return True

    enqueue_schedule.alters_data = True

    def check_stalled(self):
        """Update the status of the collector if stalled.

        A WORKING plan without a last_run is in its first-ever run:
        get_current_job() cannot identify the running job without a
        last_run reference, so this opportunistic check must not touch
        it. Genuinely stuck first runs are recovered by the
        recover_stale_jobs management command.
        """
        if self.pk and self.last_run and self.current_job is None and self.status == CollectorStatusChoices.WORKING:
            job = self.get_current_job()
            if job is None:
                self.status = CollectorStatusChoices.STALLED
                CollectionPlan.objects.filter(pk=self.pk).update(status=self.status)

    def get_current_job(self):
        """Return the current job for the collectionplan."""
        if self.pk and self.last_run:
            object_type = ContentType.objects.get_for_model(  # type: ignore
                self, for_concrete_model=False
            )
            try:
                self.current_job = (
                    Job.objects.filter(object_id=self.pk, object_type=object_type)
                    .filter(started__gte=self.last_run)
                    .exclude(status__in=JobStatusChoices.TERMINAL_STATE_CHOICES)
                    .last()
                )
            except Job.DoesNotExist:  # pylint: disable=no-member
                pass
        return self.current_job

    def get_absolute_url(self):
        """Return the absolute URL of the Collector object."""
        return reverse("plugins:netbox_facts:collectionplan", args=[self.pk])

    def get_collector_type_color(self):
        """Return the color of the collector type."""
        return CollectionTypeChoices.colors.get(self.collector_type)  # type: ignore # pylint: disable=no-member

    def get_status_color(self):
        """Return the color of the collector status."""
        return CollectorStatusChoices.colors.get(self.status)  # type: ignore # pylint: disable=no-member

    def get_priority_color(self):
        """Return the color of the collector priority."""
        return CollectorPriorityChoices.colors.get(self.priority)  # type: ignore # pylint: disable=no-member

    # pylint: disable=no-member
    def get_devices_queryset(self):
        """Return a queryset of devices matching the collection plan."""
        q = Q()
        # Each populated filter narrows the result (AND logic across dimensions)
        for dimension in SCOPE_DIMENSIONS:
            pks = getattr(self, dimension.field).values_list("pk", flat=True)
            if pks:
                q &= Q(**{dimension.lookup: pks})

        if self.device_status:
            q &= Q(status__in=self.device_status)

        return Device.objects.filter(q).distinct()

    def get_scope_values(self, dimension: ScopeDimension):
        """Return the objects currently assigned to one scoping dimension.

        Many-to-many assignments only exist once the plan has been saved,
        so validation of a new or re-scoped plan reads the values staged on
        _m2m_values instead -- the attribute NetBox's model forms and API
        serializers populate before they call full_clean().
        """
        staged = getattr(self, "_m2m_values", None) or {}
        if dimension.field in staged:
            return staged[dimension.field]
        if self.pk is None:
            return []
        return getattr(self, dimension.field).all()

    def has_scope(self) -> bool:
        """Return True when at least one scoping dimension is populated."""
        if self.device_status:
            return True
        return any(self.get_scope_values(dimension) for dimension in SCOPE_DIMENSIONS)

    def get_matched_device_count(self) -> int:
        """Return how many devices the plan's scope currently resolves to."""
        return self.get_devices_queryset().count()

    def get_unready_devices(self, limit: int | None = None):
        """Return matched devices with no usable IP for connection_target.

        These are the devices a run would skip with a warning. Callers pass
        a limit to name a handful of offenders without loading the scope.
        """
        from netbox_facts.helpers.netbox import connection_ip_filter

        queryset = self.get_devices_queryset().exclude(connection_ip_filter(self.connection_target))
        return queryset[:limit] if limit else queryset

    def get_devices_list_url(self) -> str | None:
        """Return a device list URL filtered by this plan's scope.

        This is a browsing aid rather than the resolved queryset: the device
        list ANDs multiple tags and includes the descendants of a selected
        region, site group, location or tenant group, while the plan ORs
        tags and matches those objects exactly.

        Returns None when any dimension pins more than
        MAX_URL_PKS_PER_DIMENSION objects, since serializing that many pks
        into a query string would produce a href too long for browsers and
        proxies to handle. Callers should render the matched count unlinked
        in that case.
        """
        params = {}
        for dimension in SCOPE_DIMENSIONS:
            values = list(getattr(self, dimension.field).values_list(dimension.url_value, flat=True))
            if len(values) > MAX_URL_PKS_PER_DIMENSION:
                return None
            params[dimension.url_param] = values
        params["status"] = list(self.device_status or ())

        return filtered_list_url("dcim:device_list", params)

    def get_scope_warning(self) -> str:
        """Return a warning when the resolved scope is larger than expected.

        Empty when the plan stays at or below the configured
        scope_warning_threshold, or when the threshold is disabled.
        """
        count = self.get_matched_device_count()
        if not exceeds_scope_warning_threshold(count):
            return ""
        return _(
            "This plan matches {count} devices, above the configured warning threshold of {threshold}. "
            "Every run will connect to each of them."
        ).format(count=count, threshold=scope_warning_threshold())

    def clone(self) -> dict[str, Any]:
        """Return the attributes a new plan cloned from this one starts with.

        The NAPALM arguments are worth carrying over for their driver
        options, but not for their credentials: NetBox renders cloned
        attributes into the creation link's querystring, which would put a
        stored password into browser history and proxy logs, and then into
        an add form that has no stored value to censor it against. A clone
        therefore starts with the driver options and no credentials at
        all, including the enable secret.
        """
        attrs = super().clone()
        if "napalm_args" not in attrs:
            return attrs
        stored = self.napalm_args if isinstance(self.napalm_args, dict) else {}
        cloneable = strip_napalm_credentials(stored, keys=NAPALM_SENSITIVE_KEYS)
        if cloneable:
            attrs["napalm_args"] = json.dumps(cloneable)
        else:
            del attrs["napalm_args"]
        return attrs

    def _merge_napalm_args(self) -> dict[str, Any]:
        """Merge global and per-plan NAPALM arguments without filtering.

        The merged result is a deep copy: get_plugin_config() returns the
        live settings object, and callers pop credentials from and inject
        keys into the returned dict.
        """
        napalm_args = copy.deepcopy(get_plugin_config("netbox_facts", "global_napalm_args", {}) or {})
        napalm_args.update(self.napalm_args if self.napalm_args else {})
        return napalm_args

    def get_napalm_args(self) -> dict[str, Any]:
        """Return the NAPALM arguments to use when initiating the driver.

        The free-form napalm_args field is user-reachable by anyone with
        change permission on the plan, so any key that controls in-process
        behavior rather than the NAPALM connection itself must never reach
        the driver. The debug key is stripped here unconditionally; only
        run() may act on it, and only under its own explicit gate.
        """
        napalm_args = self._merge_napalm_args()
        napalm_args.pop("debug", None)
        return napalm_args

    def get_stale_grace_days(self) -> int:
        """Return how long this plan marks a missing object before removing it.

        The per-plan field is the plan's answer whenever it holds one,
        including a deliberate 0 that opts a plan out of a fleet-wide
        grace period; blank falls back to the plugin setting. Zero means
        no grace at all, which is the shipped default and the behavior
        every sweep had before the grace period existed.
        """
        if self.stale_grace_days is not None:
            return self.stale_grace_days
        return get_plugin_config("netbox_facts", "stale_grace_period_days", 0) or 0

    def get_napalm_driver(self) -> type[NetworkDriver] | None:
        """Return the driver class this plan forces on every device.

        None when the plan names no driver: the driver is then resolved per
        device from the device's platform, which only a run can do.
        """
        if not self.napalm_driver:
            return None
        return load_napalm_driver(self.napalm_driver)

    def get_napalm_driver_name_for(self, device) -> str:
        """Return the NAPALM driver name to dial one device with, or "".

        A driver named on the plan is an override and applies to every
        device; otherwise the device's own platform decides, so a single
        plan can span several vendors.
        """
        if self.napalm_driver:
            return self.napalm_driver
        return platform_napalm_driver_name(device.platform)

    def get_napalm_driver_for(self, device) -> type[NetworkDriver] | None:
        """Return the driver class for one device, or None when unresolvable.

        The enhanced-driver preference applies to a name resolved from a
        platform exactly as it does to one named on the plan.

        Raises ModuleImportError when a name resolves to no installed
        driver, which a caller iterating devices treats as a per-device
        skip rather than a failed run.
        """
        driver_name = self.get_napalm_driver_name_for(device)
        if not driver_name:
            return None
        return load_napalm_driver(driver_name)

    def enqueue_collection_job(self, request):
        """
        Enqueue a background job to perform the facts collection.

        Raises OperationNotSupported if the plan is already queued or
        working, or if no credential resolves for it. The credential check
        is worth making here because the alternative is one connection
        failure per device, logged inside a job the operator has to open to
        learn that nothing was ever configured.
        """
        from netbox_facts.jobs import CollectionJobRunner

        if self.status in (
            CollectorStatusChoices.QUEUED,
            CollectorStatusChoices.WORKING,
        ):
            raise OperationNotSupported(
                f"Cannot enqueue collection job; plan is already {self.get_status_display().lower()}."
            )

        username, _password = resolve_napalm_credentials(self.get_napalm_args())
        if not username:
            raise OperationNotSupported(
                "Cannot enqueue collection job; no NAPALM credentials are configured for this plan. "
                "Set a username and password on the plan, or set napalm_username and napalm_password "
                "in the plugin configuration."
            )

        user = self.run_as if request.user.is_superuser and self.run_as is not None else request.user

        self.current_job = CollectionJobRunner.enqueue(
            instance=self,
            user=user,
            queue_name=self.priority,
            request=copy_safe_request(request),
        )
        return self.current_job

    def run(self, request=None, *args, **kwargs):  # pylint: disable=missing-function-docstring,unused-argument
        if self.status == CollectorStatusChoices.WORKING:
            raise OperationNotSupported("Cannot initiate collection job; Collector already working.")

        self.status = CollectorStatusChoices.WORKING
        CollectionPlan.objects.filter(pk=self.pk).update(status=self.status)

        if settings.DEBUG and self._merge_napalm_args().get("debug", False):
            import debugpy  # pylint: disable=import-outside-toplevel

            debugpy.listen(("127.0.0.1", 5678))
            debugpy.wait_for_client()  # blocks execution until client is attached

        # Create a new NapalmCollector instance
        try:
            runner = NapalmCollector(self)

            if request:
                with event_tracking(request):
                    runner.execute()
            else:
                runner.execute()

            # Update status & last_synced time
            self.status = CollectorStatusChoices.COMPLETED
            self.last_run = timezone.now()
            CollectionPlan.objects.filter(pk=self.pk).update(status=self.status, last_run=self.last_run)
        except Exception:
            CollectionPlan.objects.filter(pk=self.pk).update(status=CollectorStatusChoices.FAILED)
            raise

    run.alters_data = True

    def _log(self, level, message):
        """Append a timestamped log entry."""
        self.log.append(
            {
                "time": timezone.now().isoformat(),
                "status": level,
                "message": str(message),
            }
        )

    def log_debug(self, message):
        """Log a message at DEBUG level."""
        logger.log(logging.DEBUG, message)
        self._log(LogLevelChoices.LOG_DEFAULT, message)

    def log_success(self, message):
        """Log a message at SUCCESS level."""
        logger.log(logging.INFO, message)  # No syslog equivalent for SUCCESS
        self._log(LogLevelChoices.LOG_SUCCESS, message)

    def log_info(self, message):
        """Log a message at INFO level."""
        logger.log(logging.INFO, message)
        self._log(LogLevelChoices.LOG_INFO, message)

    def log_warning(self, message):
        """Log a message at WARNING level."""
        logger.log(logging.WARNING, message)
        self._log(LogLevelChoices.LOG_WARNING, message)

    def log_failure(self, message):
        """Log a message at ERROR level."""
        logger.log(logging.ERROR, message)
        self._log(LogLevelChoices.LOG_FAILURE, message)

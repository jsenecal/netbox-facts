"""Collection models."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any, Dict, Type

import django_rq
from core.choices import JobStatusChoices
from core.models import Job
from dcim.choices import DeviceStatusChoices
from dcim.models import Device
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from extras.choices import LogLevelChoices
from netbox.context_managers import event_tracking
from napalm import get_network_driver
from napalm.base.base import NetworkDriver
from netbox.models import NetBoxModel
from netbox.models.features import EventRulesMixin, JobsMixin
from netbox.plugins.utils import get_plugin_config
from utilities.querysets import RestrictedQuerySet
from utilities.request import copy_safe_request

from netbox_facts.exceptions import OperationNotSupported

from ..choices import (
    CollectionTypeChoices,
    CollectorPriorityChoices,
    CollectorStatusChoices,
)
from ..helpers import NapalmCollector

logger = logging.getLogger("netbox_facts")


class CollectionPlan(NetBoxModel, EventRulesMixin, JobsMixin):
    """Model representing a Collection Plan"""

    name = models.CharField(verbose_name=_("name"), max_length=100, unique=True)
    priority = models.CharField(
        choices=CollectorPriorityChoices, default=CollectorPriorityChoices.PRIORITY_LOW
    )
    status = models.CharField(
        max_length=50,
        choices=CollectorStatusChoices,
        default=CollectorStatusChoices.NEW,
    )
    enabled = models.BooleanField(verbose_name=_("enabled"), default=True)
    description = models.CharField(
        verbose_name=_("description"), max_length=200, blank=True
    )
    run_as = models.ForeignKey(
        get_user_model(), on_delete=models.SET_NULL, null=True, blank=True
    )

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
    site_groups = models.ManyToManyField(
        to="dcim.SiteGroup", related_name="+", blank=True
    )
    sites = models.ManyToManyField(to="dcim.Site", related_name="+", blank=True)
    locations = models.ManyToManyField(to="dcim.Location", related_name="+", blank=True)
    device_types = models.ManyToManyField(
        to="dcim.DeviceType", related_name="+", blank=True
    )
    roles = models.ManyToManyField(to="dcim.DeviceRole", related_name="+", blank=True)
    platforms = models.ManyToManyField(to="dcim.Platform", related_name="+", blank=True)
    tenant_groups = models.ManyToManyField(
        to="tenancy.TenantGroup", related_name="+", blank=True
    )
    tenants = models.ManyToManyField(to="tenancy.Tenant", related_name="+", blank=True)
    tags = models.ManyToManyField(to="extras.Tag", related_name="+", blank=True)

    collector_type = models.CharField(
        _("Collector Type"), max_length=50, choices=CollectionTypeChoices
    )

    napalm_driver = models.CharField(
        max_length=50,
        verbose_name="NAPALM driver",
        help_text=_(
            "The name of the NAPALM driver to use when interacting with devices"
        ),
    )
    napalm_args = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="NAPALM arguments",
        help_text=_(
            "Additional arguments to pass when initiating the NAPALM driver (JSON format)"
        ),
    )

    scheduled_at = models.DateTimeField(
        verbose_name=_("scheduled at"),
        blank=True,
        null=True,
    )
    interval = models.PositiveIntegerField(
        help_text=_("Interval at which this collection task is re-run (in minutes)<br>Leave blank to run only once."),
        verbose_name=_("Interval (minutes)"),
        blank=True,
        null=True,
    )

    last_run = models.DateTimeField(
        verbose_name=_("last run"), blank=True, null=True, editable=False
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

    ip_addresses = models.ManyToManyField(
        to="ipam.IPAddress", related_name="discovered_by", blank=True, editable=False
    )

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
    )

    class Meta:
        """Meta class for CollectionPlan."""

        ordering = ["priority", "name"]
        verbose_name = _("Collection Plan")
        verbose_name_plural = _("Collection Plans")

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

    @property
    def ready(self):
        """Return True if the collector is ready to be run."""
        return self.enabled and self.status not in (
            CollectorStatusChoices.QUEUED,
            CollectorStatusChoices.WORKING,
        )

    @property
    def result(self):
        """Return the last created job"""
        return self.jobs.all().order_by("-created").first()

    @property
    def scheduled_at_next(self):
        """Return the scheduled time of the next run."""
        return self.last_run + timedelta(minutes=self.interval)

    def check_stalled(self):
        """Update the status of the collector if stalled."""
        if (
            self.pk
            and self.current_job is None
            and self.status == CollectorStatusChoices.WORKING
        ):
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

        devices = Device.objects.all()
        if self.devices.exists():
            devices = devices.filter(pk__in=self.devices.all())
        if self.device_status:
            devices = devices.filter(status__in=self.device_status)
        if self.regions.exists():
            devices = devices.filter(region__in=self.regions.all())
        if self.site_groups.exists():
            devices = devices.filter(site__group__in=self.site_groups.all())
        if self.sites.exists():
            devices = devices.filter(site__in=self.sites.all())
        if self.locations.exists():
            devices = devices.filter(location__in=self.locations.all())
        if self.device_types.exists():
            devices = devices.filter(device_type__in=self.device_types.all())
        if self.roles.exists():
            devices = devices.filter(role__in=self.roles.all())
        if self.platforms.exists():
            devices = devices.filter(platform__in=self.platforms.all())
        if self.tenant_groups.exists():
            devices = devices.filter(tenant__group__in=self.tenant_groups.all())
        if self.tenants.exists():
            devices = devices.filter(tenant__in=self.tenants.all())
        if self.tags.exists():
            devices = devices.filter(tags__in=self.tags.all())
        return devices.distinct()

    def get_napalm_args(self) -> Dict[str, Any]:
        """Return the NAPALM arguments to use when initiating the driver."""
        napalm_args = get_plugin_config("netbox_facts", "global_napalm_args", {})
        napalm_args.update(self.napalm_args if self.napalm_args else {})
        return napalm_args

    def get_napalm_driver(self) -> Type[NetworkDriver]:
        """Return a NAPALM driver instance."""
        try:
            driver = get_network_driver(f"netbox_facts.napalm.{self.napalm_driver}")
        except ModuleNotFoundError:
            driver = get_network_driver(self.napalm_driver)
        return driver

    def enqueue_collection_job(self, request):
        """
        Enqueue a background job to perform the facts collection.
        """
        # Set the status to "syncing"
        self.status = CollectorStatusChoices.QUEUED
        CollectionPlan.objects.filter(pk=self.pk).update(status=self.status)

        # Enqueue a sync job
        self.current_job = CollectionPlan.enqueue(
            import_string("netbox_facts.jobs.collection_job"),
            name=f'Facts collection job for "{self.name}"',
            instance=self,
            user=(
                self.run_as
                if request.user.is_superuser and self.run_as is not None
                else request.user
            ),
            request=copy_safe_request(request),
        )
        return self.current_job

    @classmethod
    def enqueue(  # pylint: disable=too-many-arguments
        cls,
        func,
        instance,
        name="",
        user=None,
        schedule_at=None,
        interval=None,
        **kwargs,
    ):
        """
        Create a Job instance and enqueue a job using the given callable

        Args:
            func: The callable object to be enqueued for execution
            instance: The NetBox object to which this job pertains
            name: Name for the job (optional)
            user: The user responsible for running the job
            schedule_at: Schedule the job to be executed at the passed date and time
            interval: Recurrence interval (in minutes)
        """
        object_type = ContentType.objects.get_for_model(  # type: ignore
            instance, for_concrete_model=False
        )
        rq_queue_name = instance.priority
        queue = django_rq.get_queue(rq_queue_name)
        status = (
            JobStatusChoices.STATUS_SCHEDULED
            if schedule_at
            else JobStatusChoices.STATUS_PENDING
        )
        job = Job.objects.create(
            object_type=object_type,
            object_id=instance.pk,
            name=name,
            status=status,
            scheduled=schedule_at,
            interval=interval,
            user=user,
            job_id=uuid.uuid4(),
        )

        if schedule_at:
            queue.enqueue_at(
                schedule_at, func, job_id=str(job.job_id), job=job, **kwargs
            )
        else:
            queue.enqueue(func, job_id=str(job.job_id), job=job, **kwargs)

        return job

    def run(
        self, request, *args, **kwargs
    ):  # pylint: disable=missing-function-docstring,unused-argument
        if self.status == CollectorStatusChoices.WORKING:
            raise OperationNotSupported(
                "Cannot initiate collection job; Collector already working."
            )

        self.status = CollectorStatusChoices.WORKING
        CollectionPlan.objects.filter(pk=self.pk).update(status=self.status)

        napalm_args = self.get_napalm_args()
        if napalm_args and napalm_args.get("debug", False):
            import debugpy  # pylint: disable=import-outside-toplevel

            debugpy.listen(("0.0.0.0", 5678))
            debugpy.wait_for_client()  # blocks execution until client is attached
            self.napalm_args.pop("debug")

        # Create a new NapalmRunner instance
        runner = NapalmCollector(self)

        with event_tracking(request):
            # Run the collection job
            runner.execute()

        # Update status & last_synced time
        self.status = CollectorStatusChoices.COMPLETED
        self.last_run = timezone.now()
        CollectionPlan.objects.filter(pk=self.pk).update(
            status=self.status, last_run=self.last_run
        )

    run.alters_data = True

    def log_debug(self, message):
        """Log a message at DEBUG level."""
        logger.log(logging.DEBUG, message)
        self.log.append((LogLevelChoices.LOG_DEFAULT, str(message)))

    def log_success(self, message):
        """Log a message at SUCCESS level."""
        logger.log(logging.INFO, message)  # No syslog equivalent for SUCCESS
        self.log.append((LogLevelChoices.LOG_SUCCESS, str(message)))

    def log_info(self, message):
        """Log a message at INFO level."""
        logger.log(logging.INFO, message)
        self.log.append((LogLevelChoices.LOG_INFO, str(message)))

    def log_warning(self, message):
        """Log a message at WARNING level."""
        logger.log(logging.WARNING, message)
        self.log.append((LogLevelChoices.LOG_WARNING, str(message)))

    def log_failure(self, message):
        """Log a message at ERROR level."""
        logger.log(logging.ERROR, message)
        self.log.append((LogLevelChoices.LOG_FAILURE, str(message)))

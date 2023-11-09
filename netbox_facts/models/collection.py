"""Collection models."""
import importlib
import logging
import uuid
from django.urls import reverse

from django.contrib.postgres.fields import ArrayField
from django.utils import timezone
from netbox_facts.exceptions import OperationNotSupported
from dcim.choices import DeviceStatusChoices
from dcim.models import Device

from django.db import models

from django.utils.translation import gettext_lazy as _
from extras.choices import JobResultStatusChoices


from django_rq import get_queue
from rq.job import Job

from netbox.models import NetBoxModel

from ..choices import CollectionTypeChoices, CollectorPriorityChoices, CollectorStatusChoices
from . import BigIDModel


logger = logging.getLogger("netbox_facts")


class CollectorDefinition(NetBoxModel):
    """Model representing a collector definition."""

    name = models.CharField(verbose_name=_("name"), max_length=100, unique=True)
    priority = models.CharField(choices=CollectorPriorityChoices, default=CollectorPriorityChoices.PRIORITY_LOW)
    status = models.CharField(
        max_length=50, choices=CollectorStatusChoices, default=CollectorStatusChoices.STATUS_ENABLED
    )
    description = models.CharField(verbose_name=_("description"), max_length=200, blank=True)

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

    collector_type = models.CharField(_("Collector Type"), max_length=50, choices=CollectionTypeChoices)

    napalm_driver = models.CharField(
        max_length=50,
        verbose_name="NAPALM driver",
        help_text=_("The name of the NAPALM driver to use when interacting with devices"),
    )
    napalm_args = models.JSONField(
        blank=True,
        null=True,
        verbose_name="NAPALM arguments",
        help_text=_("Additional arguments to pass when initiating the NAPALM driver (JSON format)"),
    )

    schedule_at = models.DateTimeField(help_text=_("Schedule execution to a set time"))
    interval = models.PositiveIntegerField(
        help_text=_("Interval at which this collection task is re-run (in minutes)"),
        default=60,
        verbose_name=_("Interval (minutes)"),
    )

    comments = models.TextField(
        _("Comments"),
        blank=True,
    )

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
        "schedule_at",
        "interval",
    )

    class Meta:
        """Meta class for CollectorDefinition."""

        ordering = ["priority", "name"]
        verbose_name = _("Collector Definition")
        verbose_name_plural = _("Collector Definitions")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._queue = None

    def __str__(self):
        """String representation of the CollectorDefinition object."""

        return str(self.name)

    def get_absolute_url(self):
        """Return the absolute URL of the CollectorDefinition object."""
        return reverse("plugins:netbox_facts:collectordefinition_detail", args=[self.pk])

    @property
    def is_enabled(self):
        """Return True if the collector definition is enabled."""
        return self.status == CollectorStatusChoices.STATUS_ENABLED

    @property
    def last_run(self):
        """Return the last job run of this collector definition."""
        collection_job: CollectionJob = (
            self.jobs.filter(status=JobResultStatusChoices.STATUS_COMPLETED)  # type: ignore pylint: disable=no-member
            .order_by("completed")
            .last()
        )
        if collection_job is not None:
            return collection_job.completed
        return None

    def get_devices_queryset(self):
        """Return a queryset of devices matching the collector definition."""

        devices = Device.objects.all()
        if self.devices.exists():
            devices = devices.filter(pk__in=self.devices.all())
        if self.device_status:
            devices = devices.filter(status=self.device_status)
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

    def get_queue(self):
        """Return the cached RQ queue or fetch it."""
        if self._queue is None:
            self._queue = get_queue(self.priority)
        return self._queue

    def enqueue(self, delay=None, debug=False):
        """Enqueue a job for this collector definition."""
        scheduled = timezone.now()
        if delay is not None:
            logger.info(f"{self}: Scheduling for: {scheduled + delay} at {scheduled} ")
            scheduled = timezone.now() + delay

        collection_job = CollectionJob.objects.create(
            job_definition=self,
            job_id=uuid.uuid4(),
            job_type=self.collector_type,
            scheduled=scheduled,
        )
        queue = self.get_queue()

        if delay is None:
            rq_task = queue.enqueue(
                CollectionJob.run,
                description=str(self),
                job_id=str(collection_job.job_id),
                collection_job_pk=collection_job.pk,
                debug=debug,
            )
        else:
            rq_task = queue.enqueue_in(
                delay,
                CollectionJob.run,
                description=str(self),
                job_id=str(collection_job.job_id),
                collection_job_pk=collection_job.pk,
                debug=debug,
            )
        logger.info(f"{self}: {collection_job.job_id}")
        logger.debug(f"{self}: Enqueued {rq_task} :: delay: {delay}")
        return collection_job

    def enqueue_if_needed(self, delay=None, job_id=None):
        if self.needs_enqueue(job_id=job_id):
            return self.enqueue(delay=delay)
        return False

    def needs_enqueue(self, job_id=None):
        if not self.get_devices_queryset().exists():
            logger.warn(f"No suitable device(s) found for {self}")
            return False
        elif self.status == CollectorStatusChoices.STATUS_DISABLED:
            logger.warn(f"Collection disabled for {self}")
            return False
        elif self.is_queued(job_id):
            logger.info(f"Collection Job already queued for {self}")
            return False

        return True

    def is_queued(self, job_id=None):
        return self.get_scheduled_jobs(job_id) is not None

    def get_scheduled_jobs(self, job_id=None):
        queue = self.get_queue()

        scheduled_jobs = queue.scheduled_job_registry.get_job_ids()
        started_jobs = queue.started_job_registry.get_job_ids()
        queued_jobs = queue.get_job_ids()

        jobs = self.jobs.all()  # type: ignore
        queued = jobs.filter(status__in=[JobResultStatusChoices.STATUS_RUNNING, JobResultStatusChoices.STATUS_PENDING])

        if job_id is not None:
            queued.exclude(job_id=job_id)

        for collection_job in queued.all():
            job = queue.fetch_job(f"{collection_job.job_id}")
            if job and (job.is_scheduled or job.is_queued) and job.id in scheduled_jobs + started_jobs + queued_jobs:
                if job.enqueued_at is not None:
                    return job.enqueued_at
                else:
                    return queue.scheduled_job_registry.get_scheduled_time(job)
            elif job and (job.is_scheduled or job.is_queued) and job.id not in scheduled_jobs + started_jobs:
                status = {
                    "is_canceled": job.is_canceled,
                    "is_deferred": job.is_deferred,
                    "is_failed": job.is_failed,
                    "is_finished": job.is_finished,
                    "is_queued": job.is_queued,
                    "is_scheduled": job.is_scheduled,
                    "is_started": job.is_started,
                    "is_stopped": job.is_stopped,
                }
                job.cancel()
                collection_job.status = JobResultStatusChoices.STATUS_FAILED
                collection_job.save()
                logger.warning(
                    f"{self}: Job in scheduled or started queue but not in a registry, cancelling {status} {scheduled_jobs + started_jobs + queued_jobs}"
                )
            elif job and job.is_canceled:
                collection_job.status = JobResultStatusChoices.STATUS_FAILED
                collection_job.save()
        return None


class CollectionJob(BigIDModel):
    """Model representing a collection job."""

    job_definition = models.ForeignKey("CollectorDefinition", on_delete=models.CASCADE, related_name="jobs")
    created = models.DateTimeField(auto_now_add=True)
    scheduled = models.DateTimeField(null=True, blank=True)
    started = models.DateTimeField(null=True, blank=True)
    completed = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=30, choices=JobResultStatusChoices, default=JobResultStatusChoices.STATUS_PENDING
    )
    job_id = models.UUIDField(unique=True)
    job_type = models.CharField(_("Job Type"), max_length=50, choices=CollectionTypeChoices)

    result = models.JSONField(null=True, blank=True)

    def __str__(self):
        return str(self.job_id)

    @property
    def queue(self):
        """Return the RQ queue."""
        return self.job_definition.get_queue()

    def delete(self, using=None, keep_parents=False):
        """Delete the job from the queue."""
        queue = self.queue

        job: Job = queue.fetch_job(f"{self.job_id}")
        if job is not None:
            if not job.is_canceled:
                job.cancel()
            job.delete()

        super().delete(using=using, keep_parents=keep_parents)

    def get_status_color(self):
        return JobResultStatusChoices.colors.get(self.status)  # type: ignore

    @property
    def duration(self):
        """Return a string representation of the duration of the job."""
        if not self.completed:
            return None

        duration = self.completed - self.started
        minutes, seconds = divmod(duration.total_seconds(), 60)

        return f"{int(minutes)} minutes, {seconds:.2f} seconds"

    def set_status(self, status):
        """
        Helper method to change the status of the job result. If the target status is terminal, the  completion
        time is also set.
        """
        self.status = status
        if status in JobResultStatusChoices.TERMINAL_STATE_CHOICES:
            self.completed = timezone.now()

    def reschedule(self, time):
        """
        Reschedule a job
        """
        if self.status == JobResultStatusChoices.STATUS_PENDING:
            self.scheduled = time
            job = self.queue.fetch_job(f"{self.job_id}")
            self.queue.schedule(job, time)
        else:
            raise OperationNotSupported("Job is not in a state for rescheduling")

    @classmethod
    def run(cls, collection_job_pk, debug=False):
        self = cls.objects.get(pk=collection_job_pk)

        if debug:
            import debugpy  # pylint: disable=import-outside-toplevel

            debugpy.listen(("0.0.0.0", 5678))
            debugpy.wait_for_client()  # blocks execution until client is attached

        self.started = timezone.now()

        try:
            # Do Something
            print(self)
        except Exception as e:
            logger.error(f"{self}: {e}")
            self.result = {"error": str(e)}
            self.set_status(JobResultStatusChoices.STATUS_FAILED)

        self.completed = timezone.now()
        self.status = JobResultStatusChoices.STATUS_COMPLETED
        self.save()
        return

    # def is_running(backup, job_id=None):
    #     queue = self.get_queue()

    #     jobs = backup.jobs.all()
    #     queued = jobs.filter(status__in=[JobResultStatusChoices.STATUS_RUNNING])

    #     if job_id is not None:
    #         queued.exclude(job_id=job_id)

    #     for backupjob in queued.all():
    #         job = queue.fetch_job(f"{backupjob.job_id}")
    #         if job and job.is_started and job.id in queue.started_job_registry.get_job_ids() + queue.get_job_ids():
    #             return True
    #         elif job and job.is_started and job.id not in queue.started_job_registry.get_job_ids():
    #             status = {
    #                 "is_canceled": job.is_canceled,
    #                 "is_deferred": job.is_deferred,
    #                 "is_failed": job.is_failed,
    #                 "is_finished": job.is_finished,
    #                 "is_queued": job.is_queued,
    #                 "is_scheduled": job.is_scheduled,
    #                 "is_started": job.is_started,
    #                 "is_stopped": job.is_stopped,
    #             }
    #             job.cancel()
    #             backupjob.status = JobResultStatusChoices.STATUS_FAILED
    #             backupjob.save()
    #             logger.warning(f"{backup}: Job in started queue but not in a registry, cancelling {status}")
    #         elif job and job.is_canceled:
    #             backupjob.status = JobResultStatusChoices.STATUS_FAILED
    #             backupjob.save()
    #     return False

    # def is_queued(backup, job_id=None):
    #     if get_scheduled(backup, job_id) is not None:
    #         return True
    #     return False

    # def remove_orphaned():
    #     queue = self.get_queue()
    #     registry = ScheduledJobRegistry(queue=queue)

    #     for job_id in registry.get_job_ids():
    #         try:
    #             BackupJob.objects.get(job_id=job_id)
    #         except BackupJob.DoesNotExist:
    #             registry.remove(job_id)

    # def remove_queued(backup):
    #     queue = self.get_queue()
    #     registry = ScheduledJobRegistry(queue=queue)
    #     for job_id in registry.get_job_ids():
    #         job = queue.fetch_job(f"{job_id}")
    #         if job.description == f"{backup.uuid}":
    #             registry.remove(f"{job_id}")

    # @classmethod
    # def enqueue(cls, backup, delay=None):
    #     from ..utils import enqueue

    #     return enqueue(backup, delay)

    # @classmethod
    # def enqueue_if_needed(cls, backup, delay=None, job_id=None):
    #     from netbox_config_backup.utils import enqueue_if_needed

    #     return enqueue_if_needed(backup, delay, job_id)

    # @classmethod
    # def needs_enqueue(cls, backup, job_id=None):
    #     from netbox_config_backup.utils import needs_enqueue

    #     return needs_enqueue(backup, job_id)

    # @classmethod
    # def is_running(cls, backup, job_id=None):
    #     from netbox_config_backup.utils import is_running

    #     return is_running(backup, job_id)

    # @classmethod
    # def is_queued(cls, backup, job_id=None):
    #     from netbox_config_backup.utils import is_queued

    #     return is_queued(backup, job_id)

    # @classmethod
    # def remove_orphaned(cls):
    #     from netbox_config_backup.utils import remove_orphaned

    #     return remove_orphaned()

    # @classmethod
    # def remove_queued(cls, backup):
    #     from netbox_config_backup.utils import remove_queued

    #     return remove_queued()

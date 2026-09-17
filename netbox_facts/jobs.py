import logging

from core.choices import JobStatusChoices
from django.db import DatabaseError
from netbox.context_managers import event_tracking
from netbox.jobs import JobRunner
from netbox.plugins.utils import get_plugin_config

from netbox_facts.choices import CollectorStatusChoices, EntryStatusChoices

logger = logging.getLogger(__name__)


class CollectionJobRunner(JobRunner):
    """JobRunner for NetBox Facts collection jobs."""

    class Meta:
        name = "Facts Collection"

    @classmethod
    def enqueue(cls, *args, **kwargs):
        """Enqueue a collection job, setting the plan status to QUEUED."""
        from netbox_facts.models import CollectionPlan

        # Apply default job timeout from plugin settings if not explicitly set
        if "job_timeout" not in kwargs:
            kwargs["job_timeout"] = get_plugin_config("netbox_facts", "job_timeout", 1800)

        job = super().enqueue(*args, **kwargs)

        # Update the CollectionPlan's status to queued
        if instance := job.object:
            instance.status = CollectorStatusChoices.QUEUED
            CollectionPlan.objects.filter(pk=instance.pk).update(status=CollectorStatusChoices.QUEUED)

        return job

    def run(self, request=None, *args, **kwargs):
        """Execute the collection plan."""
        from netbox_facts.models import CollectionPlan
        from netbox_facts.models.facts_report import FactsReport

        plan = CollectionPlan.objects.get(pk=self.job.object_id)
        try:
            plan.run(request=request)
        finally:
            # Persist the in-memory log to the Job's data field so
            # the results view can display it, even on failure.
            self.job.data = {
                "log": list(plan.log),
            }

            # Link the most recent report to this job
            try:
                report = FactsReport.objects.filter(collection_plan_id=plan.pk).order_by("-created").first()
                if report and not report.job_id:
                    report.job = self.job
                    report.save(update_fields=["job"])
            except DatabaseError:
                logger.warning(
                    "Failed to link FactsReport to job %s for plan %s",
                    self.job.pk,
                    plan.pk,
                    exc_info=True,
                )


class ApplyEntriesJobRunner(JobRunner):
    """JobRunner that applies every pending entry of a FactsReport."""

    class Meta:
        name = "Facts Report Apply"

    @classmethod
    def get_active_jobs(cls, report):
        """Return the apply jobs for this report that are queued, scheduled, or running."""
        return cls.get_jobs(report).filter(status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES)

    @classmethod
    def enqueue(cls, *args, **kwargs):
        """Enqueue an apply job, applying the plugin's default job timeout."""
        if "job_timeout" not in kwargs:
            kwargs["job_timeout"] = get_plugin_config("netbox_facts", "job_timeout", 1800)

        return super().enqueue(*args, **kwargs)

    def run(self, request=None, *args, **kwargs):
        """Apply the report's pending entries, resolving them from the report itself."""
        from netbox_facts.helpers.applier import apply_entries
        from netbox_facts.models.facts_report import FactsReport

        report = FactsReport.objects.get(pk=self.job.object_id)
        entry_pks = list(report.entries.filter(status=EntryStatusChoices.STATUS_PENDING).values_list("pk", flat=True))

        if not entry_pks:
            self.logger.info("No pending entries to apply for report %s.", report.pk)
            self.job.data = {"applied": 0, "failed": 0}
            return

        self.logger.info("Applying %s pending entries for report %s.", len(entry_pks), report.pk)

        # Reuse the request captured at enqueue time so object changes made by the
        # job are attributed to the user who confirmed the apply.
        if request is not None:
            with event_tracking(request):
                applied, failed = apply_entries(report, entry_pks)
        else:
            applied, failed = apply_entries(report, entry_pks)

        self.job.data = {"applied": applied, "failed": failed}
        self.logger.info("Applied %s entries; %s failed.", applied, failed)

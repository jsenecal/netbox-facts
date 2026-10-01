import logging
from contextlib import nullcontext

from core.choices import JobStatusChoices
from django.db import DatabaseError
from netbox.context_managers import event_tracking
from netbox.jobs import JobRunner
from netbox.plugins.utils import get_plugin_config

from netbox_facts.choices import CollectorStatusChoices

logger = logging.getLogger(__name__)


class FactsJobRunner(JobRunner):
    """Base JobRunner applying the plugin's shared job defaults."""

    @classmethod
    def enqueue(cls, *args, **kwargs):
        """Enqueue a job, applying the plugin's default job timeout if not explicitly set."""
        if "job_timeout" not in kwargs:
            kwargs["job_timeout"] = get_plugin_config("netbox_facts", "job_timeout", 1800)

        return super().enqueue(*args, **kwargs)


class CollectionJobRunner(FactsJobRunner):
    """JobRunner for NetBox Facts collection jobs."""

    class Meta:
        name = "Facts Collection"

    @classmethod
    def enqueue(cls, *args, **kwargs):
        """Enqueue a collection job, setting the plan status to QUEUED.

        Only an immediate job marks the plan queued. A future-dated
        successor -- the next firing of a cron schedule, or a first run
        the user deferred -- leaves the status alone: the plan is idle
        until that time arrives, and claiming otherwise would hide the
        Run button and refuse manual runs for the whole wait.
        """
        from netbox_facts.models import CollectionPlan

        job = super().enqueue(*args, **kwargs)

        if kwargs.get("schedule_at"):
            return job

        # Update the CollectionPlan's status to queued
        if instance := job.object:
            instance.status = CollectorStatusChoices.QUEUED
            CollectionPlan.objects.filter(pk=instance.pk).update(status=CollectorStatusChoices.QUEUED)

        return job

    @classmethod
    def handle(cls, job, *args, **kwargs):
        """Run the job, then give a cron-scheduled plan its successor.

        Core's JobRunner.handle() reschedules a recurring job from the
        interval stored on the job itself, which cannot express "02:00 on
        weekdays"; a cron plan therefore enqueues jobs with no interval
        and computes each successor from its expression instead.
        Scheduling from a finally block follows the same shape as core,
        so a run that failed still leaves the plan with a schedule.
        """
        try:
            super().handle(job, *args, **kwargs)
        finally:
            cls.schedule_next_cron_run(job)

    @classmethod
    def schedule_next_cron_run(cls, job):
        """Enqueue the next firing of the plan's cron schedule, if it has one."""
        from netbox_facts.models import CollectionPlan

        if job.interval:
            # A recurring interval job is rescheduled by core itself.
            return

        plan = CollectionPlan.objects.filter(pk=job.object_id).first()
        if plan is None or not plan.cron_schedule:
            return

        plan.enqueue_schedule()

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


class ApplyEntriesJobRunner(FactsJobRunner):
    """JobRunner that applies every pending entry of a FactsReport."""

    class Meta:
        name = "Facts Report Apply"

    @classmethod
    def get_active_jobs(cls, report):
        """Return the apply jobs for this report that are queued, scheduled, or running."""
        return cls.get_jobs(report).filter(status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES)

    def run(self, request=None, *args, **kwargs):
        """Apply the report's pending entries, resolving them from the report itself."""
        from netbox_facts.helpers.applier import apply_entries
        from netbox_facts.models.facts_report import FactsReport

        report = FactsReport.objects.get(pk=self.job.object_id)
        entry_pks = list(report.pending_entries.values_list("pk", flat=True))

        if not entry_pks:
            self.logger.info("No pending entries to apply for report %s.", report.pk)
            self.job.data = {"applied": 0, "failed": 0}
            return

        self.logger.info("Applying %s pending entries for report %s.", len(entry_pks), report.pk)

        # Reuse the request captured at enqueue time so object changes made by the
        # job are attributed to the user who confirmed the apply.
        with event_tracking(request) if request is not None else nullcontext():
            applied, failed = apply_entries(report, entry_pks)

        self.job.data = {"applied": applied, "failed": failed}
        self.logger.info("Applied %s entries; %s failed.", applied, failed)

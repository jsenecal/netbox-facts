import logging

from netbox.jobs import JobRunner

from netbox_facts.choices import CollectorStatusChoices

logger = logging.getLogger(__name__)


class CollectionJobRunner(JobRunner):
    """JobRunner for NetBox Facts collection jobs."""

    class Meta:
        name = "Facts Collection"

    @classmethod
    def enqueue(cls, *args, **kwargs):
        """Enqueue a collection job, setting the plan status to QUEUED."""
        from netbox_facts.models import CollectionPlan

        job = super().enqueue(*args, **kwargs)

        # Update the CollectionPlan's status to queued
        if instance := job.object:
            instance.status = CollectorStatusChoices.QUEUED
            CollectionPlan.objects.filter(pk=instance.pk).update(
                status=CollectorStatusChoices.QUEUED
            )

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
                "log": [
                    {"status": level, "message": message}
                    for level, message in plan.log
                ],
            }

            # Link the most recent report to this job
            try:
                report = (
                    FactsReport.objects.filter(collection_plan_id=plan.pk)
                    .order_by("-created")
                    .first()
                )
                if report and not report.job_id:
                    report.job = self.job
                    report.save(update_fields=["job"])
            except Exception:
                logger.warning(
                    "Failed to link FactsReport to job %s for plan %s",
                    self.job.pk, plan.pk, exc_info=True,
                )

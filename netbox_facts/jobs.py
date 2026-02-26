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

        plan = CollectionPlan.objects.get(pk=self.job.object_id)
        plan.run(request=request)

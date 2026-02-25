import logging
import traceback

from core.choices import JobStatusChoices
from core.models.jobs import Job
from netbox_facts.choices import CollectorStatusChoices
from netbox_facts.models import CollectionPlan

# from extras.api.serializers import JobS

logger = logging.getLogger(__name__)


def collection_job(job: Job, *args, **kwargs):
    """Collection job for NetBox Facts."""
    plan: CollectionPlan = CollectionPlan.objects.get(pk=job.object_id)

    try:
        job.start()
        plan.run(*args, **kwargs)
        # job.data = job.get_job_data()
        job.terminate()
    except Exception as e:  # pylint: disable=broad-except
        stacktrace = traceback.format_exc()
        plan.log_failure(
            f"An exception occurred: `{type(e).__name__}: {e}`\n```\n{stacktrace}\n```"
        )
        logger.error("Exception raised during collector execution: %s", e)

        # job.data = ScriptOutputSerializer(plan).data
        job.terminate(status=JobStatusChoices.STATUS_ERRORED)
        CollectionPlan.objects.filter(pk=plan.pk).update(
            status=CollectorStatusChoices.FAILED
        )

    logger.info("Collection completed in %s", job.duration)

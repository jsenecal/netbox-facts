"""Retention policy for Facts Reports.

Every collection run creates a FactsReport, so a plan on a short interval
accumulates tens of thousands of reports per year. Retention is opt-in:
the plugin keeps reports forever until an operator sets
``report_retention_days`` to a positive number of days.

The selection policy lives in :func:`get_prunable_reports` so it can be
exercised without the job machinery.
"""

import logging
from datetime import datetime, timedelta
from typing import NamedTuple

from core.choices import JobIntervalChoices
from django.db.models import QuerySet
from django.utils import timezone
from netbox.jobs import JobRunner, system_job
from netbox.plugins.utils import get_plugin_config

from netbox_facts.choices import EntryStatusChoices

logger = logging.getLogger("netbox_facts.retention")

RETENTION_SETTING = "report_retention_days"

# Reports are deleted in batches so that a deployment which enables retention
# after years of collection does not assemble one enormous cascade delete.
PRUNE_BATCH_SIZE = 1000


class PruneResult(NamedTuple):
    """Outcome of a single pruning pass."""

    deleted: int
    cutoff: datetime | None


def get_retention_days() -> int:
    """Return the configured retention window in days (0 disables pruning)."""
    raw = get_plugin_config("netbox_facts", RETENTION_SETTING, 0)
    try:
        days = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Ignoring invalid %s value %r; report retention stays disabled",
            RETENTION_SETTING,
            raw,
        )
        return 0
    return days if days > 0 else 0


def get_retention_cutoff(retention_days: int, now: datetime | None = None) -> datetime | None:
    """Return the timestamp before which reports may be deleted, or None when disabled."""
    if retention_days <= 0:
        return None
    return (now or timezone.now()) - timedelta(days=retention_days)


def _resolve_retention_days(retention_days: int | None) -> int:
    if retention_days is None:
        return get_retention_days()
    return max(int(retention_days), 0)


def get_prunable_reports(retention_days: int | None = None, now: datetime | None = None) -> QuerySet:
    """Return the reports eligible for deletion under the retention policy.

    A report is eligible when it was created strictly before the cutoff and
    carries no entry still awaiting review: pending entries represent work an
    operator has not acted on yet, so they hold their report regardless of age.
    Reports with no recorded creation timestamp are never selected.
    """
    from netbox_facts.models import FactsReport

    cutoff = get_retention_cutoff(_resolve_retention_days(retention_days), now=now)
    if cutoff is None:
        return FactsReport.objects.none()

    return FactsReport.objects.filter(created__lt=cutoff).exclude(entries__status=EntryStatusChoices.STATUS_PENDING)


def prune_reports(retention_days: int | None = None, now: datetime | None = None) -> PruneResult:
    """Delete the reports selected by the retention policy and report the count."""
    from netbox_facts.models import FactsReport

    days = _resolve_retention_days(retention_days)
    cutoff = get_retention_cutoff(days, now=now)
    if cutoff is None:
        logger.debug("Facts report retention is disabled; nothing pruned")
        return PruneResult(deleted=0, cutoff=None)

    queryset = get_prunable_reports(days, now=now)
    deleted = 0
    while batch := list(queryset.values_list("pk", flat=True)[:PRUNE_BATCH_SIZE]):
        FactsReport.objects.filter(pk__in=batch).delete()
        deleted += len(batch)

    logger.info(
        "Pruned %d facts report(s) older than %d day(s) (created before %s)",
        deleted,
        days,
        cutoff.isoformat(),
    )
    return PruneResult(deleted=deleted, cutoff=cutoff)


@system_job(interval=JobIntervalChoices.INTERVAL_DAILY)
class FactsReportRetentionJob(JobRunner):
    """Delete aged-out facts reports once per day."""

    class Meta:
        name = "Facts Report Retention"

    def run(self, *args, **kwargs):
        days = get_retention_days()
        if days <= 0:
            self.logger.info(
                "Facts report retention is disabled (%s = 0); no reports deleted",
                RETENTION_SETTING,
            )
            return

        result = prune_reports(days)
        self.logger.info(
            "Deleted %d facts report(s) older than %d day(s) (created before %s)",
            result.deleted,
            days,
            result.cutoff.isoformat(),
        )

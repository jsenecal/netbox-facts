"""Cron-expression helpers backing a collection plan's schedule."""

from __future__ import annotations

import logging
from datetime import datetime

from croniter import croniter
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger("netbox_facts")

#: Fields in a standard cron expression: minute, hour, day of month,
#: month, day of week. croniter also accepts a seconds field and the
#: @daily-style nicknames; both are rejected here so that what a plan
#: stores is exactly what crontab(5) documents.
CRON_FIELD_COUNT = 5

CRON_HELP = _(
    "Five-field cron expression (minute hour day-of-month month day-of-week), such as '0 2 * * 1-5' for "
    "02:00 on weekdays."
)


def validate_cron_expression(expression: str) -> None:
    """Raise ValidationError unless expression is a 5-field cron expression."""
    if len(expression.split()) != CRON_FIELD_COUNT or not croniter.is_valid(expression):
        raise ValidationError(CRON_HELP)


def next_cron_occurrence(expression: str, reference: datetime) -> datetime | None:
    """Return the first time expression fires strictly after reference.

    The expression is evaluated in NetBox's configured time zone, since
    an operator writing '0 2 * * 1-5' means 02:00 in the time zone their
    maintenance window is defined in rather than 02:00 UTC. croniter
    carries the base datetime's tzinfo through to the value it returns,
    so converting the reference is what anchors the whole calculation.

    Returns None for an expression that does not parse. Validation keeps
    those out of the database, but a fixture load or a direct queryset
    update can still store one, and neither a plan detail page nor the
    save that schedules the next run may blow up on stored data.
    """
    try:
        validate_cron_expression(expression)
    except ValidationError:
        logger.warning("Ignoring unusable cron schedule %r.", expression)
        return None

    return croniter(expression, timezone.localtime(reference)).get_next(datetime)

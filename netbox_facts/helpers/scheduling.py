"""Cron-expression helpers backing a collection plan's schedule."""

from __future__ import annotations

import logging
from datetime import datetime

from croniter import CroniterBadDateError, croniter
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

CRON_NEVER_FIRES = _("This expression matches no date that will ever occur, such as February 31st.")


def _first_occurrence_after(expression: str, base: datetime) -> datetime | None:
    """Return the first firing of expression after base, or None.

    None means the expression matches no date that can occur: croniter
    parses "0 0 31 2 *" happily and only reports the impossibility when
    asked for an occurrence, by raising rather than returning.
    """
    try:
        return croniter(expression, base).get_next(datetime)
    except CroniterBadDateError:
        return None


def validate_cron_expression(expression: str) -> None:
    """Raise ValidationError unless expression is a usable cron schedule.

    Usable means both well-formed as a 5-field expression and able to
    fire: an expression that can never come around would otherwise be
    stored and then raise on every read of the plan's next run.
    """
    if len(expression.split()) != CRON_FIELD_COUNT or not croniter.is_valid(expression):
        raise ValidationError(CRON_HELP)

    if _first_occurrence_after(expression, timezone.localtime()) is None:
        raise ValidationError(CRON_NEVER_FIRES)


def next_cron_occurrence(expression: str, reference: datetime) -> datetime | None:
    """Return the first time expression fires strictly after reference.

    The expression is evaluated in NetBox's configured time zone, since
    an operator writing '0 2 * * 1-5' means 02:00 in the time zone their
    maintenance window is defined in rather than 02:00 UTC. croniter
    carries the base datetime's tzinfo through to the value it returns,
    so converting the reference is what anchors the whole calculation.

    Returns None, never an exception, for an expression that does not
    parse or never fires. Validation keeps those out of the database,
    but a fixture load or a direct queryset update can still store one,
    and every read path -- plan list, detail page, REST API -- plus the
    save that schedules the next run asks this question.
    """
    try:
        validate_cron_expression(expression)
    except ValidationError as error:
        logger.warning("Ignoring unusable cron schedule %r: %s", expression, "; ".join(error.messages))
        return None

    return _first_occurrence_after(expression, timezone.localtime(reference))

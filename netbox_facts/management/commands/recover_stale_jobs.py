"""Management command to recover collection plans stuck in WORKING or QUEUED state."""

import logging

from django.core.management.base import BaseCommand

from netbox_facts.choices import CollectorStatusChoices
from netbox_facts.models import CollectionPlan

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Recover collection plans stuck in WORKING or QUEUED state with no active job."

    def handle(self, *args, **options):
        stale_plans = CollectionPlan.objects.filter(
            status__in=(
                CollectorStatusChoices.WORKING,
                CollectorStatusChoices.QUEUED,
            )
        )

        recovered = 0
        for plan in stale_plans:
            job = plan.get_current_job()
            if job is None:
                CollectionPlan.objects.filter(pk=plan.pk).update(
                    status=CollectorStatusChoices.STALLED,
                )
                recovered += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"Recovered stale plan '{plan.name}' (pk={plan.pk})"
                    )
                )

        if recovered:
            self.stdout.write(
                self.style.SUCCESS(f"Recovered {recovered} stale plan(s).")
            )
        else:
            self.stdout.write("No stale plans found.")

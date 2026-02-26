from unittest.mock import patch, MagicMock

from django.test import TestCase
from dcim.choices import DeviceStatusChoices

from netbox_facts.choices import CollectionTypeChoices
from netbox_facts.models import CollectionPlan


class HandleCollectionJobChangeSignalTest(TestCase):
    """Tests for the handle_collection_job_change signal."""

    def _create_plan(self, **kwargs):
        defaults = {
            "name": "Signal Test Plan",
            "collector_type": CollectionTypeChoices.TYPE_ARP,
            "napalm_driver": "junos",
            "device_status": [DeviceStatusChoices.STATUS_ACTIVE],
        }
        defaults.update(kwargs)
        return CollectionPlan(**defaults)

    @patch("netbox_facts.jobs.CollectionJobRunner")
    def test_enqueue_once_called_when_enabled_with_interval(self, mock_runner):
        """Saving an enabled plan with interval should call enqueue_once."""
        plan = self._create_plan(enabled=True, interval=60)
        plan.save()

        mock_runner.enqueue_once.assert_called_once()
        call_kwargs = mock_runner.enqueue_once.call_args
        self.assertEqual(call_kwargs.kwargs.get("interval") or call_kwargs[1].get("interval", call_kwargs[0][1] if len(call_kwargs[0]) > 1 else None), 60)

    @patch("netbox_facts.jobs.CollectionJobRunner")
    def test_no_enqueue_when_disabled(self, mock_runner):
        """Saving a disabled plan should not call enqueue_once."""
        plan = self._create_plan(enabled=False, interval=60)
        plan.save()

        mock_runner.enqueue_once.assert_not_called()

    @patch("netbox_facts.jobs.CollectionJobRunner")
    def test_no_enqueue_when_no_interval(self, mock_runner):
        """Saving an enabled plan without interval should not call enqueue_once."""
        plan = self._create_plan(enabled=True, interval=None)
        plan.save()

        mock_runner.enqueue_once.assert_not_called()

    @patch("netbox_facts.jobs.CollectionJobRunner")
    def test_deletes_jobs_when_disabled(self, mock_runner):
        """Disabling a plan should delete pending scheduled jobs."""
        plan = self._create_plan(enabled=True, interval=60, name="Delete Test Plan")
        plan.save()
        mock_runner.reset_mock()

        plan.enabled = False
        plan.save()

        mock_runner.get_jobs.assert_called()

    @patch("netbox_facts.jobs.CollectionJobRunner")
    def test_deletes_jobs_when_interval_cleared(self, mock_runner):
        """Clearing interval on existing plan should delete pending jobs."""
        plan = self._create_plan(enabled=True, interval=60, name="Interval Clear Plan")
        plan.save()
        mock_runner.reset_mock()

        plan.interval = None
        plan.save()

        mock_runner.enqueue_once.assert_not_called()

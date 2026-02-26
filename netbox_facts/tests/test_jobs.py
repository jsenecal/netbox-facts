from unittest.mock import patch, MagicMock

from django.test import TestCase
from dcim.choices import DeviceStatusChoices

from netbox_facts.choices import CollectionTypeChoices, CollectorStatusChoices
from netbox_facts.jobs import CollectionJobRunner
from netbox_facts.models import CollectionPlan


class CollectionJobRunnerTest(TestCase):
    """Tests for CollectionJobRunner."""

    @classmethod
    def setUpTestData(cls):
        cls.plan = CollectionPlan.objects.create(
            name="Job Test Plan",
            collector_type=CollectionTypeChoices.TYPE_ARP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )

    def test_runner_name(self):
        """CollectionJobRunner.name should be 'Facts Collection'."""
        self.assertEqual(CollectionJobRunner.name, "Facts Collection")

    @patch("netbox_facts.models.CollectionPlan")
    def test_enqueue_sets_status_to_queued(self, mock_plan_cls):
        """enqueue() should update the plan status to QUEUED."""
        mock_plan_cls.objects.filter.return_value.update = MagicMock()

        with patch("core.models.jobs.Job.enqueue") as mock_enqueue:
            mock_job = MagicMock()
            mock_job.object = self.plan
            mock_enqueue.return_value = mock_job

            CollectionJobRunner.enqueue(
                instance=self.plan,
                user=None,
                queue_name=self.plan.priority,
            )

            mock_plan_cls.objects.filter.assert_called_with(pk=self.plan.pk)

    @patch("netbox_facts.models.CollectionPlan")
    def test_run_calls_plan_run(self, mock_plan_cls):
        """run() should fetch the plan and call plan.run()."""
        mock_plan = MagicMock()
        mock_plan_cls.objects.get.return_value = mock_plan

        mock_job = MagicMock()
        mock_job.object_id = self.plan.pk

        runner = CollectionJobRunner(mock_job)
        runner.run()

        mock_plan_cls.objects.get.assert_called_once_with(pk=self.plan.pk)
        mock_plan.run.assert_called_once()

    @patch("netbox_facts.models.CollectionPlan")
    def test_run_passes_request_kwarg(self, mock_plan_cls):
        """run() should forward the request kwarg to plan.run()."""
        mock_plan = MagicMock()
        mock_plan_cls.objects.get.return_value = mock_plan
        mock_request = MagicMock()

        mock_job = MagicMock()
        mock_job.object_id = self.plan.pk

        runner = CollectionJobRunner(mock_job)
        runner.run(request=mock_request)

        mock_plan.run.assert_called_once_with(request=mock_request)

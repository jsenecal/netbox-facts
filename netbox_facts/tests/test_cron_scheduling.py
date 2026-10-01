"""Tests for cron schedules, next-run computation and the plan list columns.

Covers issue #146 (cron-style schedules plus operational columns on the
plan list) and issue #90 (scheduled_at was collected and validated but
never consumed, so one-time schedules never ran and interval plans
started immediately).

Every time-dependent assertion is made against an explicit reference
datetime passed into the scheduling helpers, so none of these tests
depend on the wall clock.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from core.choices import JobStatusChoices
from core.models import Job
from dcim.choices import DeviceStatusChoices
from django.core.exceptions import ValidationError
from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from netbox.jobs import JobRunner
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from netbox_facts.api.serializers import CollectionPlanSerializer
from netbox_facts.choices import CollectionTypeChoices, CollectorStatusChoices
from netbox_facts.helpers.scheduling import next_cron_occurrence, validate_cron_expression
from netbox_facts.jobs import CollectionJobRunner
from netbox_facts.models import CollectionPlan
from netbox_facts.tables import COLLECTION_PLAN_RUN_BUTTON, CollectorTable
from netbox_facts.tests.test_helpers import CollectorTestMixin


def _local(year, month, day, hour=0, minute=0):
    """Return an aware datetime in NetBox's configured time zone."""
    return datetime(year, month, day, hour, minute, tzinfo=timezone.get_current_timezone())


#: Fixed point every schedule assertion is measured from: a Monday at
#: 10:00 local, well away from any daylight-saving transition.
REFERENCE = _local(2026, 6, 1, 10, 0)


def _build_plan(**kwargs):
    """Return an unsaved, in-scope CollectionPlan with sensible defaults."""
    defaults = {
        "name": "Cron Test Plan",
        "collector_type": CollectionTypeChoices.TYPE_ARP,
        "napalm_driver": "junos",
        "device_status": [DeviceStatusChoices.STATUS_ACTIVE],
    }
    defaults.update(kwargs)
    return CollectionPlan(**defaults)


class CronExpressionHelperTest(TestCase):
    """Tests for the cron helpers backing the plan's schedule (#146)."""

    def test_next_occurrence_is_strictly_after_the_reference(self):
        """A firing exactly at the reference is in the past, not the future."""
        self.assertEqual(
            next_cron_occurrence("0 10 * * *", REFERENCE),
            _local(2026, 6, 2, 10, 0),
        )

    def test_unusable_expression_yields_no_occurrence(self):
        """Data that bypassed validation must not break the scheduler."""
        self.assertIsNone(next_cron_occurrence("not a cron", REFERENCE))


class CronScheduleValidationTest(TestCase):
    """Tests for the cron_schedule validation rules (#146)."""

    def test_five_field_expressions_are_accepted(self):
        for expression in ("0 2 * * 1-5", "*/15 * * * *", "0 0 1 * *", "30 3 * * 0"):
            with self.subTest(expression=expression):
                validate_cron_expression(expression)
                _build_plan(cron_schedule=expression).full_clean()

    def test_malformed_expressions_are_rejected(self):
        for expression in ("not a cron", "0 2 * *", "99 * * * *", "@daily", "0 0 2 * * *"):
            with self.subTest(expression=expression):
                with self.assertRaises(ValidationError):
                    validate_cron_expression(expression)
                with self.assertRaises(ValidationError) as context:
                    _build_plan(cron_schedule=expression).full_clean()
                self.assertIn("cron_schedule", context.exception.error_dict)

    def test_cron_and_interval_are_mutually_exclusive(self):
        with self.assertRaises(ValidationError) as context:
            _build_plan(cron_schedule="0 2 * * *", interval=60).full_clean()
        self.assertIn("cron_schedule", context.exception.error_dict)
        self.assertIn("interval", context.exception.error_dict)

    def test_a_plan_with_neither_is_valid(self):
        """No interval and no cron means manual-only runs, not an error."""
        _build_plan().full_clean()


class NextRunComputationTest(TestCase):
    """Tests for CollectionPlan.get_next_run across the schedule shapes."""

    def test_manual_only_plan_is_never_due(self):
        self.assertIsNone(_build_plan().get_next_run(REFERENCE))

    def test_disabled_plan_is_never_due(self):
        plan = _build_plan(enabled=False, interval=60)
        self.assertIsNone(plan.get_next_run(REFERENCE))

    def test_one_shot_plan_is_due_at_its_scheduled_time(self):
        """Regression test for issue #90: scheduled_at now means something."""
        scheduled_at = REFERENCE + timedelta(hours=5)
        plan = _build_plan(scheduled_at=scheduled_at)
        self.assertEqual(plan.get_next_run(REFERENCE), scheduled_at)

    def test_one_shot_plan_is_not_due_once_its_time_has_passed(self):
        plan = _build_plan(scheduled_at=REFERENCE - timedelta(hours=1), last_run=REFERENCE - timedelta(hours=1))
        self.assertIsNone(plan.get_next_run(REFERENCE))

    def test_interval_plan_is_due_immediately_before_its_first_run(self):
        plan = _build_plan(interval=60)
        self.assertEqual(plan.get_next_run(REFERENCE), REFERENCE)

    def test_interval_plan_is_due_one_interval_after_its_last_run(self):
        plan = _build_plan(interval=90, last_run=REFERENCE - timedelta(minutes=30))
        self.assertEqual(plan.get_next_run(REFERENCE), REFERENCE + timedelta(minutes=60))

    def test_interval_plan_waits_for_a_future_scheduled_at(self):
        """Regression test for issue #90: scheduled_at anchors the first run."""
        scheduled_at = REFERENCE + timedelta(days=1)
        plan = _build_plan(interval=60, scheduled_at=scheduled_at)
        self.assertEqual(plan.get_next_run(REFERENCE), scheduled_at)

    def test_cron_plan_is_due_at_the_next_firing(self):
        plan = _build_plan(cron_schedule="0 2 * * 1-5")
        self.assertEqual(plan.get_next_run(REFERENCE), _local(2026, 6, 2, 2, 0))

    def test_cron_plan_does_not_fire_before_a_future_scheduled_at(self):
        plan = _build_plan(cron_schedule="0 2 * * 1-5", scheduled_at=_local(2026, 6, 10, 12, 0))
        self.assertEqual(plan.get_next_run(REFERENCE), _local(2026, 6, 11, 2, 0))

    def test_unusable_stored_cron_leaves_the_plan_not_due(self):
        plan = _build_plan(cron_schedule="not a cron")
        self.assertIsNone(plan.get_next_run(REFERENCE))

    def test_next_run_property_reads_the_same_schedule(self):
        """The property is the clock-reading wrapper around get_next_run."""
        scheduled_at = timezone.now() + timedelta(days=2)
        plan = _build_plan(scheduled_at=scheduled_at)
        self.assertEqual(plan.next_run, scheduled_at)


class ScheduleParametersTest(TestCase):
    """Tests for the (schedule_at, interval) pair handed to enqueue_once."""

    def test_interval_only_keeps_the_historical_immediate_first_run(self):
        plan = _build_plan(interval=60)
        self.assertEqual(plan.get_schedule_parameters(REFERENCE), (None, 60))

    def test_interval_with_a_future_scheduled_at_defers_the_first_run(self):
        """Regression test for issue #90: an interval plan no longer starts on save."""
        scheduled_at = REFERENCE + timedelta(days=1)
        plan = _build_plan(interval=60, scheduled_at=scheduled_at)
        self.assertEqual(plan.get_schedule_parameters(REFERENCE), (scheduled_at, 60))

    def test_one_shot_carries_only_its_scheduled_time(self):
        """Regression test for issue #90: a scheduled_at-only plan gets a job."""
        scheduled_at = REFERENCE + timedelta(hours=3)
        plan = _build_plan(scheduled_at=scheduled_at)
        self.assertEqual(plan.get_schedule_parameters(REFERENCE), (scheduled_at, None))

    def test_a_past_one_shot_is_not_scheduled(self):
        plan = _build_plan(scheduled_at=REFERENCE - timedelta(minutes=1))
        self.assertIsNone(plan.get_schedule_parameters(REFERENCE))

    def test_cron_carries_the_next_firing_and_no_interval(self):
        plan = _build_plan(cron_schedule="0 2 * * 1-5")
        self.assertEqual(plan.get_schedule_parameters(REFERENCE), (_local(2026, 6, 2, 2, 0), None))

    def test_disabled_plan_has_no_schedule(self):
        plan = _build_plan(enabled=False, cron_schedule="0 2 * * 1-5")
        self.assertIsNone(plan.get_schedule_parameters(REFERENCE))

    def test_manual_only_plan_has_no_schedule(self):
        self.assertIsNone(_build_plan().get_schedule_parameters(REFERENCE))


class ScheduleSignalTest(CollectorTestMixin, TestCase):
    """Tests that saving a plan enqueues the schedule it now describes."""

    @patch("netbox_facts.jobs.CollectionJobRunner")
    def test_one_shot_plan_is_enqueued_for_its_scheduled_time(self, mock_runner):
        """Regression test for issue #90.

        A plan with scheduled_at and no interval -- the 'schedule
        execution to a set time' case the form advertises -- got no job
        at all, because the signal only enqueued interval plans.
        """
        scheduled_at = timezone.now() + timedelta(days=1)
        self._create_plan(name="One Shot Plan", scheduled_at=scheduled_at)

        mock_runner.enqueue_once.assert_called_once()
        kwargs = mock_runner.enqueue_once.call_args.kwargs
        self.assertEqual(kwargs["schedule_at"], scheduled_at)
        self.assertIsNone(kwargs["interval"])

    @patch("netbox_facts.jobs.CollectionJobRunner")
    def test_interval_plan_honors_a_future_scheduled_at(self, mock_runner):
        """Regression test for issue #90.

        An interval plan was enqueued with no schedule_at, so it started
        collecting immediately on save no matter which start time the
        user picked -- mutating NetBox at a time they had deferred.
        """
        scheduled_at = timezone.now() + timedelta(days=1)
        self._create_plan(name="Deferred Interval Plan", interval=60, scheduled_at=scheduled_at)

        kwargs = mock_runner.enqueue_once.call_args.kwargs
        self.assertEqual(kwargs["schedule_at"], scheduled_at)
        self.assertEqual(kwargs["interval"], 60)

    @patch("netbox_facts.jobs.CollectionJobRunner")
    def test_interval_plan_without_an_anchor_still_runs_immediately(self, mock_runner):
        """The historical behavior: no anchor means start now, repeat forever."""
        self._create_plan(name="Immediate Interval Plan", interval=60)

        kwargs = mock_runner.enqueue_once.call_args.kwargs
        self.assertIsNone(kwargs["schedule_at"])
        self.assertEqual(kwargs["interval"], 60)

    @patch("netbox_facts.jobs.CollectionJobRunner")
    def test_signal_forwards_the_computed_schedule(self, mock_runner):
        """The signal enqueues what the plan computes, nothing of its own."""
        computed = timezone.now() + timedelta(hours=7)
        with patch.object(CollectionPlan, "get_schedule_parameters", return_value=(computed, None)):
            plan = self._create_plan(name="Forwarding Plan", cron_schedule="0 2 * * *")

        mock_runner.enqueue_once.assert_called_once_with(
            instance=plan,
            schedule_at=computed,
            interval=None,
            user=plan.run_as,
            queue_name=plan.priority,
        )

    def test_clearing_a_cron_schedule_deletes_the_scheduled_successor(self):
        """A dropped schedule leaves no future-dated job behind.

        Cron and one-shot successors carry no interval, so the cleanup
        selects on the job's scheduled status rather than on its
        interval.
        """
        plan = self._create_plan(name="Cron Cleanup Plan", cron_schedule="0 2 * * *")
        jobs = CollectionJobRunner.get_jobs(plan).filter(status=JobStatusChoices.STATUS_SCHEDULED)
        self.assertEqual(jobs.count(), 1)
        self.assertIsNone(jobs.first().interval)

        plan.cron_schedule = ""
        plan.save()

        self.assertFalse(CollectionJobRunner.get_jobs(plan).exists())

    def test_a_future_dated_job_does_not_mark_the_plan_queued(self):
        """A plan waiting for its slot is idle, so manual runs stay open."""
        plan = self._create_plan(
            name="Idle Until Scheduled Plan",
            scheduled_at=timezone.now() + timedelta(days=1),
        )
        plan.refresh_from_db()
        self.assertNotEqual(plan.status, CollectorStatusChoices.QUEUED)
        self.assertTrue(plan.ready)


class CronSuccessorTest(CollectorTestMixin, TestCase):
    """Tests for the hook that re-enqueues a cron plan after each run."""

    def _job_for(self, plan, interval=None):
        job = MagicMock(spec=Job)
        job.object_id = plan.pk
        job.interval = interval
        return job

    def test_handle_schedules_the_next_cron_run(self):
        plan = self._create_plan(name="Handle Cron Plan", cron_schedule="0 2 * * *")
        job = self._job_for(plan)

        with (
            patch.object(JobRunner, "handle") as mock_handle,
            patch.object(CollectionJobRunner, "schedule_next_cron_run") as mock_schedule,
        ):
            CollectionJobRunner.handle(job)

        mock_handle.assert_called_once()
        mock_schedule.assert_called_once_with(job)

    def test_handle_schedules_the_successor_even_when_the_run_fails(self):
        plan = self._create_plan(name="Failing Cron Plan", cron_schedule="0 2 * * *")
        job = self._job_for(plan)

        with (
            patch.object(JobRunner, "handle", side_effect=RuntimeError("boom")),
            patch.object(CollectionJobRunner, "schedule_next_cron_run") as mock_schedule,
            self.assertRaises(RuntimeError),
        ):
            CollectionJobRunner.handle(job)

        mock_schedule.assert_called_once_with(job)

    def test_next_firing_is_enqueued_in_the_future(self):
        plan = self._create_plan(name="Successor Cron Plan", cron_schedule="0 2 * * *")

        with patch.object(CollectionJobRunner, "enqueue_once") as mock_enqueue:
            CollectionJobRunner.schedule_next_cron_run(self._job_for(plan))

        kwargs = mock_enqueue.call_args.kwargs
        schedule_at = timezone.localtime(kwargs["schedule_at"])
        self.assertGreater(schedule_at, timezone.now())
        self.assertEqual((schedule_at.hour, schedule_at.minute), (2, 0))
        self.assertIsNone(kwargs["interval"])
        self.assertEqual(kwargs["queue_name"], plan.priority)

    def test_interval_jobs_are_left_to_core(self):
        """Core's JobRunner.handle already reschedules an interval job."""
        plan = self._create_plan(name="Interval Successor Plan", interval=60)

        with patch.object(CollectionJobRunner, "enqueue_once") as mock_enqueue:
            CollectionJobRunner.schedule_next_cron_run(self._job_for(plan, interval=60))

        mock_enqueue.assert_not_called()

    def test_plan_without_a_cron_schedule_is_not_rescheduled(self):
        plan = self._create_plan(name="Manual Successor Plan")

        with patch.object(CollectionJobRunner, "enqueue_once") as mock_enqueue:
            CollectionJobRunner.schedule_next_cron_run(self._job_for(plan))

        mock_enqueue.assert_not_called()

    def test_disabled_cron_plan_is_not_rescheduled(self):
        plan = self._create_plan(name="Disabled Cron Plan", cron_schedule="0 2 * * *", enabled=False)

        with patch.object(CollectionJobRunner, "enqueue_once") as mock_enqueue:
            CollectionJobRunner.schedule_next_cron_run(self._job_for(plan))

        mock_enqueue.assert_not_called()


class PlanSerializerSchedulingTest(TestCase):
    """Tests that the REST API exposes the cron schedule and next run (#146)."""

    @staticmethod
    def _context():
        return {"request": Request(APIRequestFactory().get("/"))}

    def test_cron_schedule_is_writable_and_next_run_is_read_only(self):
        fields = CollectionPlanSerializer().fields
        self.assertFalse(fields["cron_schedule"].read_only)
        self.assertTrue(fields["next_run"].read_only)

    def test_next_run_is_serialized_from_the_plan(self):
        """A one-time plan reports its scheduled time as the next run."""
        plan = _build_plan(scheduled_at=timezone.now() + timedelta(days=1))
        plan.save()

        serializer = CollectionPlanSerializer(plan, context=self._context())
        expected = serializer.fields["next_run"].to_representation(plan.scheduled_at)
        self.assertEqual(serializer.data["next_run"], expected)
        self.assertEqual(serializer.data["cron_schedule"], "")


class PlanTableSchedulingTest(TestCase):
    """Tests for the operational columns and row action on the plan list (#146)."""

    def test_operational_columns_are_offered_and_the_live_ones_shown(self):
        """The list carries the schedule state, without customizing columns."""
        for name in ("enabled", "last_run", "next_run", "interval", "cron_schedule"):
            self.assertIn(name, CollectorTable.base_columns)
        for name in ("enabled", "last_run", "next_run"):
            self.assertIn(name, CollectorTable.Meta.default_columns)

    def test_detect_only_renders_as_a_badge(self):
        table = CollectorTable(CollectionPlan.objects.none())
        self.assertIn("badge", table.render_detect_only(True))
        self.assertIn("badge", table.render_detect_only(False))
        self.assertNotEqual(table.render_detect_only(True), table.render_detect_only(False))

    def _render_run_button(self, plan):
        context = Context(
            {
                "record": plan,
                "perms": {"netbox_facts": {"run_collector": True}},
            }
        )
        return Template(COLLECTION_PLAN_RUN_BUTTON).render(context)

    def test_run_button_posts_to_the_run_view(self):
        plan = _build_plan()
        plan.save()

        rendered = self._render_run_button(plan)
        self.assertIn(reverse("plugins:netbox_facts:collectionplan_run", args=[plan.pk]), rendered)
        self.assertNotIn("disabled", rendered)

    def test_run_button_is_disabled_with_the_reason_a_plan_cannot_run(self):
        plan = _build_plan(enabled=False)
        plan.save()

        rendered = self._render_run_button(plan)
        self.assertIn("disabled", rendered)
        self.assertIn(str(plan.run_disabled_reason), rendered)

"""Regression tests for the Apply All Pending flow on facts reports (#134)."""

import uuid
from unittest.mock import MagicMock, patch

from core.choices import JobStatusChoices
from core.models import Job
from dcim.choices import DeviceStatusChoices
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase as DjangoTestCase
from django.urls import reverse
from utilities.testing import TestCase

from netbox_facts.choices import (
    CollectionTypeChoices,
    EntryActionChoices,
    EntryStatusChoices,
)
from netbox_facts.jobs import ApplyEntriesJobRunner
from netbox_facts.models import CollectionPlan, FactsReport, FactsReportEntry


def create_device():
    """Create the minimal device an entry needs."""
    site = Site.objects.create(name="Apply All Site", slug="apply-all-site")
    manufacturer = Manufacturer.objects.create(name="ApplyAllMfg", slug="applyallmfg")
    device_type = DeviceType.objects.create(manufacturer=manufacturer, model="AAModel", slug="aamodel")
    role = DeviceRole.objects.create(name="AARole", slug="aarole")
    return Device.objects.create(
        name="apply-all-dev",
        site=site,
        device_type=device_type,
        role=role,
        status=DeviceStatusChoices.STATUS_ACTIVE,
    )


def create_plan():
    """Create a collection plan to hang reports off."""
    return CollectionPlan.objects.create(
        name="Apply All Plan",
        collector_type=CollectionTypeChoices.TYPE_ARP,
        napalm_driver="junos",
        device_status=[DeviceStatusChoices.STATUS_ACTIVE],
    )


def create_entry(report, device, status=EntryStatusChoices.STATUS_PENDING, label="entry"):
    """Create a single report entry in the given status."""
    return FactsReportEntry.objects.create(
        report=report,
        action=EntryActionChoices.ACTION_NEW,
        collector_type=CollectionTypeChoices.TYPE_ARP,
        device=device,
        status=status,
        object_repr=label,
    )


def create_apply_job(report, status=JobStatusChoices.STATUS_RUNNING, name=None):
    """Create a Job row standing in for an in-flight apply job."""
    return Job.objects.create(
        object_type=ContentType.objects.get_for_model(FactsReport),
        object_id=report.pk,
        name=name if name is not None else ApplyEntriesJobRunner.name,
        status=status,
        job_id=uuid.uuid4(),
    )


class FactsReportApplyAllViewTest(TestCase):
    """Apply All Pending must confirm, resolve entries server-side, and run in the background (#134)."""

    user_permissions = ("netbox_facts.apply_factsreport",)

    def setUp(self):
        super().setUp()
        self.device = create_device()
        self.plan = create_plan()
        self.report = FactsReport.objects.create(collection_plan=self.plan)
        self.pending = [create_entry(self.report, self.device, label=f"pending-{i}") for i in range(3)]
        create_entry(self.report, self.device, status=EntryStatusChoices.STATUS_SKIPPED, label="skipped")
        self.url = reverse("plugins:netbox_facts:factsreport_apply", kwargs={"pk": self.report.pk})

    def test_apply_all_confirms_before_mutating(self):
        """#134: an unconfirmed apply_all POST renders a confirmation showing the pending count."""
        with patch.object(ApplyEntriesJobRunner, "enqueue") as mock_enqueue:
            response = self.client.post(self.url, {"apply_all": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["pending_count"], 3)
        # The confirmation re-posts the flag, so confirming stays on the apply-all path.
        self.assertContains(response, 'name="apply_all"')
        mock_enqueue.assert_not_called()
        self.assertEqual(self.report.entries.filter(status=EntryStatusChoices.STATUS_PENDING).count(), 3)

    def test_confirmed_apply_all_enqueues_job_without_posted_pks(self):
        """#134: apply_all resolves the pending entries server-side, so no pk inputs are needed."""
        with patch.object(ApplyEntriesJobRunner, "enqueue") as mock_enqueue:
            mock_enqueue.return_value = MagicMock(pk=42, **{"get_absolute_url.return_value": "/core/jobs/42/"})
            response = self.client.post(self.url, {"apply_all": "1", "confirm": "True"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.report.get_absolute_url())
        mock_enqueue.assert_called_once()
        self.assertEqual(mock_enqueue.call_args.kwargs["instance"], self.report)

    def test_confirmed_apply_all_does_not_apply_in_request(self):
        """#134: the apply itself moves to the job, so the request must not mutate entries."""
        with (
            patch.object(ApplyEntriesJobRunner, "enqueue") as mock_enqueue,
            patch("netbox_facts.helpers.applier.apply_entries") as mock_apply,
        ):
            mock_enqueue.return_value = MagicMock(pk=42, **{"get_absolute_url.return_value": "/core/jobs/42/"})
            self.client.post(self.url, {"apply_all": "1", "confirm": "True"})

        mock_apply.assert_not_called()
        self.assertEqual(self.report.entries.filter(status=EntryStatusChoices.STATUS_PENDING).count(), 3)

    def test_apply_all_refuses_second_enqueue_while_job_active(self):
        """#134: a queued or running apply job for the report blocks another enqueue."""
        create_apply_job(self.report)

        with patch.object(ApplyEntriesJobRunner, "enqueue") as mock_enqueue:
            response = self.client.post(self.url, {"apply_all": "1", "confirm": "True"})

        self.assertEqual(response.status_code, 302)
        mock_enqueue.assert_not_called()

    def test_apply_all_ignores_terminated_job_for_other_report(self):
        """#134: the double-enqueue guard is scoped to active jobs of this report."""
        other_report = FactsReport.objects.create(collection_plan=self.plan)
        create_apply_job(other_report)
        create_apply_job(self.report, status=JobStatusChoices.STATUS_COMPLETED)

        with patch.object(ApplyEntriesJobRunner, "enqueue") as mock_enqueue:
            mock_enqueue.return_value = MagicMock(pk=43, **{"get_absolute_url.return_value": "/core/jobs/43/"})
            self.client.post(self.url, {"apply_all": "1", "confirm": "True"})

        mock_enqueue.assert_called_once()

    def test_apply_all_without_pending_entries_enqueues_nothing(self):
        """#134: a report with nothing pending never reaches the confirmation or the queue."""
        self.report.entries.update(status=EntryStatusChoices.STATUS_APPLIED)

        with patch.object(ApplyEntriesJobRunner, "enqueue") as mock_enqueue:
            response = self.client.post(self.url, {"apply_all": "1"})

        self.assertEqual(response.status_code, 302)
        mock_enqueue.assert_not_called()

    def test_selected_entries_still_apply_synchronously(self):
        """#134: the checkbox selection path keeps applying in-request."""
        with (
            patch("netbox_facts.helpers.applier.apply_entries", return_value=(1, 0)) as mock_apply,
            patch.object(ApplyEntriesJobRunner, "enqueue") as mock_enqueue,
        ):
            response = self.client.post(self.url, {"pk": [str(self.pending[0].pk)]})

        self.assertEqual(response.status_code, 302)
        mock_enqueue.assert_not_called()
        mock_apply.assert_called_once()
        self.assertEqual(mock_apply.call_args.args[1], [str(self.pending[0].pk)])

    def test_apply_all_requires_apply_permission(self):
        """#134: the background path stays behind apply_factsreport."""
        self.remove_permissions("netbox_facts.apply_factsreport")

        with patch.object(ApplyEntriesJobRunner, "enqueue") as mock_enqueue:
            response = self.client.post(self.url, {"apply_all": "1", "confirm": "True"})

        self.assertEqual(response.status_code, 403)
        mock_enqueue.assert_not_called()


class ApplyEntriesJobRunnerTest(DjangoTestCase):
    """The apply job resolves and applies its own report's pending entries (#134)."""

    @classmethod
    def setUpTestData(cls):
        cls.device = create_device()
        cls.plan = create_plan()
        cls.report = FactsReport.objects.create(collection_plan=cls.plan)
        cls.pending = [create_entry(cls.report, cls.device, label=f"pending-{i}") for i in range(2)]
        create_entry(cls.report, cls.device, status=EntryStatusChoices.STATUS_SKIPPED, label="skipped")
        cls.other_report = FactsReport.objects.create(collection_plan=cls.plan)
        create_entry(cls.other_report, cls.device, label="other-pending")

    def test_run_applies_only_its_reports_pending_entries(self):
        """#134: the job derives the entry set from the report, not from posted input."""
        job = create_apply_job(self.report)

        with patch("netbox_facts.helpers.applier.apply_entries", return_value=(2, 0)) as mock_apply:
            ApplyEntriesJobRunner(job).run()

        mock_apply.assert_called_once()
        report_arg, pks_arg = mock_apply.call_args.args
        self.assertEqual(report_arg.pk, self.report.pk)
        self.assertEqual(set(pks_arg), {entry.pk for entry in self.pending})

    def test_run_records_counts_on_the_job(self):
        """#134: apply results are recorded on the job so the report page can link to them."""
        job = create_apply_job(self.report)

        with patch("netbox_facts.helpers.applier.apply_entries", return_value=(1, 1)):
            ApplyEntriesJobRunner(job).run()

        self.assertEqual(job.data, {"applied": 1, "failed": 1})

    def test_enqueue_applies_default_job_timeout(self):
        """#134: applying a whole report is the long-running path, so it gets the plugin timeout."""
        with patch("core.models.jobs.Job.enqueue") as mock_enqueue:
            ApplyEntriesJobRunner.enqueue(instance=self.report, user=None)

        self.assertIn("job_timeout", mock_enqueue.call_args.kwargs)

    def test_run_tracks_events_for_the_enqueuing_request(self):
        """#134: the captured request is reused so job-made changes stay attributed."""
        job = create_apply_job(self.report)
        request = MagicMock()

        with patch("netbox_facts.helpers.applier.apply_entries", return_value=(2, 0)) as mock_apply:
            ApplyEntriesJobRunner(job).run(request=request)

        mock_apply.assert_called_once()
        self.assertEqual(job.data, {"applied": 2, "failed": 0})

    def test_run_without_pending_entries_skips_apply(self):
        """#134: a report with nothing pending must not call the applier."""
        empty_report = FactsReport.objects.create(collection_plan=self.plan)
        job = create_apply_job(empty_report)

        with patch("netbox_facts.helpers.applier.apply_entries") as mock_apply:
            ApplyEntriesJobRunner(job).run()

        mock_apply.assert_not_called()

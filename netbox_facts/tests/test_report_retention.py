"""Tests for the Facts Report retention policy."""

from datetime import timedelta

from dcim.choices import DeviceStatusChoices
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from netbox_facts.choices import (
    CollectionTypeChoices,
    EntryActionChoices,
    EntryStatusChoices,
)
from netbox_facts.models import CollectionPlan, FactsReport, FactsReportEntry
from netbox_facts.retention import (
    FactsReportRetentionJob,
    get_prunable_reports,
    get_retention_days,
    prune_reports,
)

RETENTION_DAYS = 30


class _StubJob:
    """Stand-in for core.Job: the JobRunner only uses it to record log records."""

    def log(self, record):
        pass


class ReportRetentionSettingTest(SimpleTestCase):
    """Tests for reading the report_retention_days plugin setting."""

    def setUp(self):
        self.plugin_config = settings.PLUGINS_CONFIG["netbox_facts"]
        self.original = self.plugin_config.get("report_retention_days")
        self.addCleanup(self._restore)

    def _restore(self):
        if self.original is None:
            self.plugin_config.pop("report_retention_days", None)
        else:
            self.plugin_config["report_retention_days"] = self.original

    def test_default_setting_disables_retention(self):
        """Retention must be opt-in: the shipped default keeps reports forever."""
        from netbox_facts import FactsConfig

        self.assertEqual(FactsConfig.default_settings["report_retention_days"], 0)

    def test_reads_configured_value(self):
        self.plugin_config["report_retention_days"] = 14
        self.assertEqual(get_retention_days(), 14)

    def test_invalid_values_disable_retention(self):
        """A garbage or negative setting must never delete anything."""
        for value in (None, "", "forever", -5):
            with self.subTest(value=value):
                self.plugin_config["report_retention_days"] = value
                self.assertEqual(get_retention_days(), 0)


class ReportRetentionSelectionTest(TestCase):
    """Tests for the pure selection helper used by the retention job."""

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="Retention Site", slug="retention-site")
        cls.manufacturer = Manufacturer.objects.create(name="RetMfg", slug="retmfg")
        cls.device_type = DeviceType.objects.create(manufacturer=cls.manufacturer, model="RetModel", slug="retmodel")
        cls.role = DeviceRole.objects.create(name="RetRole", slug="retrole")
        cls.device = Device.objects.create(
            name="retention-dev",
            site=cls.site,
            device_type=cls.device_type,
            role=cls.role,
            status=DeviceStatusChoices.STATUS_ACTIVE,
        )
        cls.plan = CollectionPlan.objects.create(
            name="Retention Plan",
            collector_type=CollectionTypeChoices.TYPE_ARP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )

    def setUp(self):
        self.now = timezone.now()

    def _make_report(self, age, entry_statuses=()):
        """Create a report aged by `age` (timedelta) carrying the given entries."""
        report = FactsReport.objects.create(collection_plan=self.plan)
        for index, status in enumerate(entry_statuses):
            FactsReportEntry.objects.create(
                report=report,
                action=EntryActionChoices.ACTION_NEW,
                status=status,
                collector_type=CollectionTypeChoices.TYPE_ARP,
                device=self.device,
                object_repr=f"MACAddress AA:BB:CC:DD:EE:{index:02d}",
            )
        FactsReport.objects.filter(pk=report.pk).update(created=self.now - age)
        report.refresh_from_db()
        return report

    def _prunable_pks(self, retention_days=RETENTION_DAYS):
        return set(get_prunable_reports(retention_days, now=self.now).values_list("pk", flat=True))

    def _set_retention_setting(self, value):
        plugin_config = settings.PLUGINS_CONFIG["netbox_facts"]
        original = plugin_config.get("report_retention_days")
        plugin_config["report_retention_days"] = value
        self.addCleanup(plugin_config.__setitem__, "report_retention_days", original)

    def test_retention_disabled_selects_nothing(self):
        """Zero days means keep forever, no matter how old the report is."""
        self._make_report(timedelta(days=3650), [EntryStatusChoices.STATUS_APPLIED])
        self.assertEqual(self._prunable_pks(retention_days=0), set())
        # Without an explicit window the helper falls back to the plugin setting.
        self._set_retention_setting(0)
        self.assertFalse(get_prunable_reports(now=self.now).exists())

    def test_old_report_without_pending_entries_is_selected(self):
        report = self._make_report(
            timedelta(days=45),
            [
                EntryStatusChoices.STATUS_APPLIED,
                EntryStatusChoices.STATUS_SKIPPED,
                EntryStatusChoices.STATUS_FAILED,
            ],
        )
        self.assertEqual(self._prunable_pks(), {report.pk})

    def test_old_report_with_a_pending_entry_is_excluded(self):
        """Work awaiting review must never age out (issue #152)."""
        self._make_report(
            timedelta(days=400),
            [EntryStatusChoices.STATUS_APPLIED, EntryStatusChoices.STATUS_PENDING],
        )
        self.assertEqual(self._prunable_pks(), set())

    def test_recent_report_is_excluded(self):
        self._make_report(timedelta(days=5), [EntryStatusChoices.STATUS_APPLIED])
        self.assertEqual(self._prunable_pks(), set())

    def test_retention_boundary_is_exclusive(self):
        """A report aged exactly N days is kept; one older by a second is pruned."""
        self._make_report(timedelta(days=RETENTION_DAYS), [EntryStatusChoices.STATUS_APPLIED])
        older = self._make_report(
            timedelta(days=RETENTION_DAYS, seconds=1),
            [EntryStatusChoices.STATUS_APPLIED],
        )
        self.assertEqual(self._prunable_pks(), {older.pk})

    def test_prune_deletes_only_selected_reports(self):
        stale = self._make_report(timedelta(days=90), [EntryStatusChoices.STATUS_APPLIED])
        awaiting_review = self._make_report(timedelta(days=90), [EntryStatusChoices.STATUS_PENDING])
        recent = self._make_report(timedelta(days=1), [EntryStatusChoices.STATUS_APPLIED])

        result = prune_reports(RETENTION_DAYS, now=self.now)

        self.assertEqual(result.deleted, 1)
        self.assertEqual(result.cutoff, self.now - timedelta(days=RETENTION_DAYS))
        self.assertEqual(
            set(FactsReport.objects.values_list("pk", flat=True)),
            {awaiting_review.pk, recent.pk},
        )
        self.assertFalse(FactsReportEntry.objects.filter(report_id=stale.pk).exists())

    def test_prune_is_a_noop_when_retention_is_disabled(self):
        report = self._make_report(timedelta(days=3650), [EntryStatusChoices.STATUS_APPLIED])

        result = prune_reports(0, now=self.now)

        self.assertEqual(result.deleted, 0)
        self.assertIsNone(result.cutoff)
        self.assertTrue(FactsReport.objects.filter(pk=report.pk).exists())

    def test_job_prunes_using_the_configured_window(self):
        stale = self._make_report(timedelta(days=90), [EntryStatusChoices.STATUS_APPLIED])
        recent = self._make_report(timedelta(days=1), [EntryStatusChoices.STATUS_APPLIED])
        self._set_retention_setting(RETENTION_DAYS)

        FactsReportRetentionJob(_StubJob()).run()

        self.assertFalse(FactsReport.objects.filter(pk=stale.pk).exists())
        self.assertTrue(FactsReport.objects.filter(pk=recent.pk).exists())

    def test_job_deletes_nothing_when_retention_is_disabled(self):
        report = self._make_report(timedelta(days=3650), [EntryStatusChoices.STATUS_APPLIED])
        self._set_retention_setting(0)

        FactsReportRetentionJob(_StubJob()).run()

        self.assertTrue(FactsReport.objects.filter(pk=report.pk).exists())


class ReportRetentionJobRegistrationTest(SimpleTestCase):
    """The pruning job must be registered with NetBox as a recurring system job."""

    def test_registered_as_daily_system_job(self):
        from core.choices import JobIntervalChoices
        from netbox.registry import registry

        self.assertIn(FactsReportRetentionJob, registry["system_jobs"])
        self.assertEqual(
            registry["system_jobs"][FactsReportRetentionJob]["interval"],
            JobIntervalChoices.INTERVAL_DAILY,
        )

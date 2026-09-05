"""Regression tests for CollectionPlan lifecycle and credential handling."""

import json

from dcim.choices import DeviceStatusChoices
from django.conf import settings
from django.test import TestCase
from netbox.constants import CENSOR_TOKEN
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from netbox_facts.api.serializers import CollectionPlanSerializer
from netbox_facts.choices import CollectionTypeChoices, CollectorStatusChoices
from netbox_facts.forms import CollectorForm
from netbox_facts.models import CollectionPlan


def _build_plan(**kwargs):
    """Return an unsaved CollectionPlan with sensible defaults."""
    defaults = {
        "name": "Lifecycle Test Plan",
        "collector_type": CollectionTypeChoices.TYPE_ARP,
        "napalm_driver": "junos",
        "device_status": [DeviceStatusChoices.STATUS_ACTIVE],
    }
    defaults.update(kwargs)
    return CollectionPlan(**defaults)


class GetNapalmDriverTest(TestCase):
    """Tests for CollectionPlan.get_napalm_driver driver resolution."""

    def test_plugin_driver_resolved_for_junos(self):
        """Regression test for issue #42.

        Under napalm 5.2.0, get_network_driver() rejects dotted driver
        names before importing them, so the plugin-local enhanced driver
        must be loaded directly instead of through napalm's loader.
        """
        from netbox_facts.napalm.junos import EnhancedJunOSDriver

        plan = _build_plan(napalm_driver="junos")
        self.assertIs(plan.get_napalm_driver(), EnhancedJunOSDriver)

    def test_stock_driver_resolved_for_eos(self):
        """Regression test for issue #42.

        A driver without a plugin-local override must fall back to the
        stock napalm driver instead of raising ModuleImportError.
        """
        from napalm.eos import EOSDriver

        plan = _build_plan(napalm_driver="eos")
        self.assertIs(plan.get_napalm_driver(), EOSDriver)


class GetNapalmArgsIsolationTest(TestCase):
    """Tests that get_napalm_args never mutates the shared plugin config."""

    def setUp(self):
        self.plugin_config = settings.PLUGINS_CONFIG["netbox_facts"]
        self.original_global_args = self.plugin_config.get("global_napalm_args")
        self.plugin_config["global_napalm_args"] = {"transport": "ssh"}

    def tearDown(self):
        self.plugin_config["global_napalm_args"] = self.original_global_args

    def test_get_napalm_args_leaves_global_config_untouched(self):
        """Regression test for issue #58.

        get_napalm_args() merged per-plan args (including credentials)
        directly into the live PLUGINS_CONFIG dict, leaking them into
        every later plan run in the same worker process.
        """
        plan_with_creds = _build_plan(
            name="Creds Plan",
            napalm_args={"username": "plan-user", "password": "plan-pass"},
        )
        plan_plain = _build_plan(name="Plain Plan", napalm_args={"timeout": 5})

        merged = plan_with_creds.get_napalm_args()
        self.assertEqual(merged["username"], "plan-user")
        self.assertEqual(merged["transport"], "ssh")
        self.assertEqual(self.plugin_config["global_napalm_args"], {"transport": "ssh"})

        other = plan_plain.get_napalm_args()
        self.assertNotIn("username", other)
        self.assertNotIn("password", other)
        self.assertEqual(other, {"transport": "ssh", "timeout": 5})

    def test_credential_pops_do_not_reach_global_config(self):
        """Regression test for issue #58.

        NapalmCollector.__init__ pops username/password and injects a
        timeout into the dict returned by get_napalm_args(); none of
        that may reach the shared global config.
        """
        plan = _build_plan(name="Popping Plan", napalm_args={"username": "u", "password": "p"})
        merged = plan.get_napalm_args()
        merged.pop("username")
        merged.pop("password")
        merged["timeout"] = 60
        self.assertEqual(self.plugin_config["global_napalm_args"], {"transport": "ssh"})


class CheckStalledFirstRunTest(TestCase):
    """Tests that loading a plan during its first run does not stall it."""

    def test_first_run_working_plan_is_not_marked_stalled(self):
        """Regression test for issue #60.

        During a plan's first-ever run, status is WORKING while last_run
        is still None. get_current_job() cannot find the running job
        without last_run, so check_stalled() flipped the row to STALLED
        on any model instantiation (list view, API read), defeating the
        concurrency guard. Genuinely stuck plans are recovered by the
        recover_stale_jobs management command instead.
        """
        plan = _build_plan(name="First Run Plan")
        plan.save()
        CollectionPlan.objects.filter(pk=plan.pk).update(status=CollectorStatusChoices.WORKING)

        reloaded = CollectionPlan.objects.get(pk=plan.pk)
        self.assertEqual(reloaded.status, CollectorStatusChoices.WORKING)
        self.assertEqual(
            CollectionPlan.objects.values_list("status", flat=True).get(pk=plan.pk),
            CollectorStatusChoices.WORKING,
        )


class NapalmArgsCredentialExposureTest(TestCase):
    """Tests that napalm_args credentials are masked on read paths."""

    @classmethod
    def setUpTestData(cls):
        cls.plan = CollectionPlan.objects.create(
            name="Credential Plan",
            collector_type=CollectionTypeChoices.TYPE_ARP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
            napalm_args={"username": "svc-user", "password": "s3cret", "timeout": 15},
        )

    @staticmethod
    def _serializer_context():
        return {"request": Request(APIRequestFactory().get("/"))}

    def test_api_representation_masks_credentials(self):
        """Regression test for issue #59.

        The REST API serialized napalm_args verbatim, exposing per-plan
        device credentials to anyone with view permission on the plan.
        """
        data = CollectionPlanSerializer(self.plan, context=self._serializer_context()).data
        self.assertEqual(data["napalm_args"]["username"], CENSOR_TOKEN)
        self.assertEqual(data["napalm_args"]["password"], CENSOR_TOKEN)
        self.assertEqual(data["napalm_args"]["timeout"], 15)

    def test_api_update_with_masked_values_preserves_credentials(self):
        """Regression test for issue #59.

        A PATCH that round-trips the masked representation must not
        overwrite the stored real credential values.
        """
        serializer = CollectionPlanSerializer(
            self.plan,
            data={"napalm_args": {"username": CENSOR_TOKEN, "password": CENSOR_TOKEN, "timeout": 30}},
            partial=True,
            context=self._serializer_context(),
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        plan = serializer.save()
        self.assertEqual(plan.napalm_args["username"], "svc-user")
        self.assertEqual(plan.napalm_args["password"], "s3cret")
        self.assertEqual(plan.napalm_args["timeout"], 30)

    def test_form_initial_masks_credentials(self):
        """Regression test for issue #59.

        The edit form rendered the stored napalm_args JSON verbatim,
        exposing credentials in the UI.
        """
        form = CollectorForm(instance=self.plan)
        self.assertEqual(form.initial["napalm_args"]["username"], CENSOR_TOKEN)
        self.assertEqual(form.initial["napalm_args"]["password"], CENSOR_TOKEN)
        self.assertEqual(form.initial["napalm_args"]["timeout"], 15)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.napalm_args["password"], "s3cret")

    def test_form_masked_submission_preserves_credentials(self):
        """Regression test for issue #59.

        Submitting the edit form with the masked values unchanged must
        not overwrite the stored real credential values.
        """
        form = CollectorForm(
            data={
                "name": self.plan.name,
                "priority": self.plan.priority,
                "collector_type": self.plan.collector_type,
                "napalm_driver": self.plan.napalm_driver,
                "connection_target": self.plan.connection_target,
                "device_status": [DeviceStatusChoices.STATUS_ACTIVE],
                "napalm_args": json.dumps(
                    {"username": CENSOR_TOKEN, "password": CENSOR_TOKEN, "timeout": 30},
                ),
            },
            instance=self.plan,
        )
        self.assertTrue(form.is_valid(), form.errors)
        plan = form.save()
        self.assertEqual(plan.napalm_args["username"], "svc-user")
        self.assertEqual(plan.napalm_args["password"], "s3cret")
        self.assertEqual(plan.napalm_args["timeout"], 30)

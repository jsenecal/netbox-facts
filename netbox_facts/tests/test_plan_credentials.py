"""Tests for the per-plan NAPALM credential fields and the enqueue preflight.

Covers the first-class credential fields and the pre-run credential check
asked for by issue #149.
"""

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from dcim.choices import DeviceStatusChoices
from django.conf import settings
from django.test import TestCase as DjangoTestCase
from django.urls import reverse
from netbox.constants import CENSOR_TOKEN
from rest_framework import status as http_status
from utilities.testing import APITestCase

from netbox_facts.choices import CollectionTypeChoices, CollectorPriorityChoices
from netbox_facts.exceptions import OperationNotSupported
from netbox_facts.forms import CollectorForm
from netbox_facts.helpers.collector import NapalmCollector
from netbox_facts.helpers.napalm import (
    resolve_napalm_credentials,
    strip_napalm_credentials,
)
from netbox_facts.models import CollectionPlan
from netbox_facts.tests.test_helpers import CollectorTestMixin


@contextmanager
def plugin_credentials(username=None, password=None):
    """Temporarily set the plugin-level NAPALM credentials.

    The plugin config is the live settings dict, so the keys are restored
    on the way out rather than replaced wholesale.
    """
    config = settings.PLUGINS_CONFIG["netbox_facts"]
    previous = {key: config.get(key) for key in ("napalm_username", "napalm_password")}
    config["napalm_username"] = username or ""
    config["napalm_password"] = password or ""
    try:
        yield
    finally:
        for key, value in previous.items():
            config[key] = value


def form_data(**overrides):
    """Build a minimally valid CollectorForm payload."""
    data = {
        "name": "Credential Form Plan",
        "priority": CollectorPriorityChoices.PRIORITY_DEFAULT,
        "collector_type": CollectionTypeChoices.TYPE_ARP,
        "napalm_driver": "junos",
        "connection_target": "primary",
        "device_status": [DeviceStatusChoices.STATUS_ACTIVE],
    }
    data.update(overrides)
    return data


class NapalmCredentialResolutionTest(CollectorTestMixin, DjangoTestCase):
    """The resolution order shared by the preflight and the collector."""

    def test_plan_values_override_plugin_config(self):
        with plugin_credentials(username="config-user", password="config-pass"):
            username, password = resolve_napalm_credentials(
                {"username": "plan-user", "password": "plan-pass"},
            )
        self.assertEqual(username, "plan-user")
        self.assertEqual(password, "plan-pass")

    def test_plugin_config_used_when_the_plan_sets_nothing(self):
        with plugin_credentials(username="config-user", password="config-pass"):
            username, password = resolve_napalm_credentials({"timeout": 30})
        self.assertEqual(username, "config-user")
        self.assertEqual(password, "config-pass")

    def test_blank_plan_value_falls_back_to_plugin_config(self):
        """An emptied plan-level key must not shadow a configured credential."""
        with plugin_credentials(username="config-user", password="config-pass"):
            username, password = resolve_napalm_credentials({"username": "", "password": ""})
        self.assertEqual(username, "config-user")
        self.assertEqual(password, "config-pass")

    def test_nothing_configured_resolves_to_empty(self):
        with plugin_credentials():
            username, password = resolve_napalm_credentials({})
        self.assertEqual(username, "")
        self.assertEqual(password, "")

    def test_strip_leaves_driver_arguments_and_the_enable_secret(self):
        """Only the two positional credentials are consumed by the driver call.

        NAPALM reads an enable secret from optional_args, so `secret`
        stays in the stripped arguments even though it is masked on the
        read paths.
        """
        stripped = strip_napalm_credentials(
            {"username": "u", "password": "p", "secret": "enable", "port": 22},
        )
        self.assertEqual(stripped, {"secret": "enable", "port": 22})

    def test_collector_resolves_credentials_through_the_shared_helper(self):
        plan = self._create_plan(
            name="Collector Credential Plan",
            napalm_args={"username": "plan-user", "password": "plan-pass", "secret": "enable", "port": 22},
        )

        with plugin_credentials(username="config-user", password="config-pass"):
            collector = NapalmCollector(plan)

        self.assertEqual(collector._napalm_username, "plan-user")
        self.assertEqual(collector._napalm_password, "plan-pass")
        self.assertNotIn("username", collector._napalm_args)
        self.assertNotIn("password", collector._napalm_args)
        self.assertEqual(collector._napalm_args["secret"], "enable")
        self.assertEqual(collector._napalm_args["port"], 22)

    def test_collector_falls_back_to_the_plugin_configuration(self):
        plan = self._create_plan(name="Collector Fallback Plan")

        with plugin_credentials(username="config-user", password="config-pass"):
            collector = NapalmCollector(plan)

        self.assertEqual(collector._napalm_username, "config-user")
        self.assertEqual(collector._napalm_password, "config-pass")


class PlanCredentialFormFieldsTest(CollectorTestMixin, DjangoTestCase):
    """The dedicated credential fields on the plan edit form."""

    def _stored_plan(self, name="Stored Credential Plan", **napalm_args):
        args = {"username": "svc-user", "password": "s3cret", "secret": "en4ble", "port": 22}
        args.update(napalm_args)
        return self._create_plan(name=name, napalm_args=args)

    def test_fields_store_their_values_into_napalm_args(self):
        form = CollectorForm(
            data=form_data(
                name="New Credential Plan",
                napalm_username="new-user",
                napalm_password="new-pass",
                napalm_secret="new-secret",
            ),
        )

        self.assertTrue(form.is_valid(), form.errors)
        plan = form.save()
        self.assertEqual(plan.napalm_args["username"], "new-user")
        self.assertEqual(plan.napalm_args["password"], "new-pass")
        self.assertEqual(plan.napalm_args["secret"], "new-secret")

    def test_stored_secrets_are_never_echoed(self):
        plan = self._stored_plan()

        form = CollectorForm(instance=plan)

        self.assertEqual(form.initial["napalm_username"], "svc-user")
        for name in ("napalm_password", "napalm_secret"):
            rendered = str(form[name])
            self.assertNotIn("s3cret", rendered)
            self.assertNotIn("en4ble", rendered)
            # The censor token stands in for the stored value, so the
            # field advertises that one exists without echoing it.
            self.assertIn(CENSOR_TOKEN, rendered)
        self.assertNotIn("s3cret", str(form["napalm_args"]))

    def test_fields_do_not_claim_a_stored_value_when_none_is_set(self):
        plan = self._create_plan(name="Credential-less Plan", napalm_args={"port": 22})

        form = CollectorForm(instance=plan)

        self.assertEqual(form.initial["napalm_username"], "")
        for name in ("napalm_password", "napalm_secret"):
            self.assertNotIn(CENSOR_TOKEN, str(form[name]))

    def test_blank_submission_keeps_the_stored_credentials(self):
        plan = self._stored_plan(name="Blank Submission Plan")

        form = CollectorForm(
            data=form_data(
                name=plan.name,
                napalm_username="svc-user",
                napalm_password="",
                napalm_secret="",
                napalm_args=json.dumps(
                    {"username": CENSOR_TOKEN, "password": CENSOR_TOKEN, "secret": CENSOR_TOKEN, "port": 22},
                ),
            ),
            instance=plan,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.napalm_args["username"], "svc-user")
        self.assertEqual(saved.napalm_args["password"], "s3cret")
        self.assertEqual(saved.napalm_args["secret"], "en4ble")

    def test_blanking_the_username_field_clears_the_plan_credential(self):
        """The username field renders its value, so a blank submission means "drop it".

        Its help text promises a fallback to the plugin-level setting, and
        the field is the only credential input able to show the operator
        what it is clearing.
        """
        plan = self._stored_plan(name="Cleared Username Plan")

        form = CollectorForm(
            data=form_data(
                name=plan.name,
                napalm_username="",
                napalm_password="",
                napalm_secret="",
                napalm_args=json.dumps(
                    {"username": CENSOR_TOKEN, "password": CENSOR_TOKEN, "port": 22},
                ),
            ),
            instance=plan,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertNotIn("username", saved.napalm_args)
        # A blank password cannot be told from an unchanged one, so it keeps.
        self.assertEqual(saved.napalm_args["password"], "s3cret")
        with plugin_credentials(username="config-user", password="config-pass"):
            username, _password = resolve_napalm_credentials(saved.napalm_args)
        self.assertEqual(username, "config-user")

    def test_censored_submission_keeps_the_stored_credentials(self):
        """A round-tripped censor token means "keep the stored value"."""
        plan = self._stored_plan(name="Censored Submission Plan")

        form = CollectorForm(
            data=form_data(
                name=plan.name,
                napalm_username="svc-user",
                napalm_password=CENSOR_TOKEN,
                napalm_secret=CENSOR_TOKEN,
                napalm_args=json.dumps({"port": 22}),
            ),
            instance=plan,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.napalm_args["password"], "s3cret")
        self.assertEqual(saved.napalm_args["secret"], "en4ble")

    def test_fields_replace_the_stored_credentials_when_filled_in(self):
        plan = self._stored_plan(name="Rotation Plan")

        form = CollectorForm(
            data=form_data(
                name=plan.name,
                napalm_username="rotated-user",
                napalm_password="rotated-pass",
                napalm_args=json.dumps(
                    {"username": CENSOR_TOKEN, "password": CENSOR_TOKEN, "port": 22},
                ),
            ),
            instance=plan,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.napalm_args["username"], "rotated-user")
        self.assertEqual(saved.napalm_args["password"], "rotated-pass")

    def test_other_napalm_args_keys_are_left_alone(self):
        plan = self._stored_plan(name="Driver Args Plan", transport="ssh")

        form = CollectorForm(
            data=form_data(
                name=plan.name,
                napalm_username="svc-user",
                napalm_password="rotated-pass",
                napalm_args=json.dumps({"port": 2222, "transport": "ssh"}),
            ),
            instance=plan,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.napalm_args["port"], 2222)
        self.assertEqual(saved.napalm_args["transport"], "ssh")
        self.assertEqual(saved.napalm_args["password"], "rotated-pass")

    def test_napalm_args_help_text_points_at_the_credential_fields(self):
        form = CollectorForm()

        self.assertIn("credential", str(form.fields["napalm_args"].help_text).lower())


class ClonedPlanCredentialsTest(CollectorTestMixin, DjangoTestCase):
    """Cloning a plan carries its driver options but none of its secrets.

    The attributes clone() returns are rendered into the creation link's
    querystring, so a credential left in them reaches browser history,
    proxy logs, and an unmasked add form.
    """

    def test_clone_drops_the_credentials_and_keeps_the_driver_options(self):
        plan = self._create_plan(
            name="Cloneable Plan",
            napalm_args={"username": "svc-user", "password": "s3cret", "secret": "en4ble", "port": 22},
        )

        attrs = plan.clone()

        self.assertEqual(json.loads(attrs["napalm_args"]), {"port": 22})
        for value in ("svc-user", "s3cret", "en4ble"):
            self.assertNotIn(value, str(attrs))

    def test_clone_omits_napalm_args_when_only_credentials_are_stored(self):
        plan = self._create_plan(
            name="Credentials Only Plan",
            napalm_args={"username": "svc-user", "password": "s3cret"},
        )

        attrs = plan.clone()

        self.assertNotIn("napalm_args", attrs)


class EnqueuePreflightTest(CollectorTestMixin, DjangoTestCase):
    """enqueue_collection_job refuses a plan with no resolvable credentials."""

    def test_enqueue_is_blocked_when_no_credential_resolves(self):
        plan = self._create_plan(name="No Credentials Plan")

        with plugin_credentials(), self.assertRaises(OperationNotSupported) as caught:
            plan.enqueue_collection_job(MagicMock())

        self.assertIn("no NAPALM credentials are configured", str(caught.exception))

    def test_plan_level_credentials_satisfy_the_preflight(self):
        plan = self._create_plan(
            name="Plan Credentials Plan",
            napalm_args={"username": "plan-user", "password": "plan-pass"},
        )

        with plugin_credentials(), patch("netbox_facts.jobs.CollectionJobRunner.enqueue") as enqueue:
            plan.enqueue_collection_job(MagicMock())

        enqueue.assert_called_once()

    def test_plugin_configuration_credentials_satisfy_the_preflight(self):
        plan = self._create_plan(name="Config Credentials Plan")

        with (
            plugin_credentials(username="config-user", password="config-pass"),
            patch("netbox_facts.jobs.CollectionJobRunner.enqueue") as enqueue,
        ):
            plan.enqueue_collection_job(MagicMock())

        enqueue.assert_called_once()


class RunActionPreflightAPITest(APITestCase):
    """The REST run action surfaces the preflight message."""

    user_permissions = ("netbox_facts.view_collectionplan", "netbox_facts.add_collectionplan")

    def test_run_action_reports_the_missing_credentials(self):
        plan = CollectionPlan.objects.create(
            name="API Preflight Plan",
            collector_type=CollectionTypeChoices.TYPE_ARP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        url = reverse("plugins-api:netbox_facts-api:collectionplan-run", kwargs={"pk": plan.pk})

        with plugin_credentials():
            response = self.client.post(url, {}, format="json", **self.header)

        self.assertEqual(response.status_code, http_status.HTTP_409_CONFLICT)
        self.assertIn("no NAPALM credentials are configured", response.data["detail"])

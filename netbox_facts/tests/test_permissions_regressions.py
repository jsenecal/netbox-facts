"""Regression tests for undeclared permissions.

Covers issue #131: the menu gate points at a permission the Collector ->
CollectionPlan rename left behind, and the custom run/results permissions
the views and template check are never declared.
"""

from django.test import TestCase

from netbox_facts import navigation
from netbox_facts.models import CollectionPlan


class NavigationPermissionTest(TestCase):
    """Regression test for #131: menu gate must match the real permission."""

    def test_collection_plans_menu_item_gated_on_view_collectionplan(self):
        """The Collection Plans menu entry must require view_collectionplan.

        The model was renamed from Collector to CollectionPlan, but the
        menu item was left pointing at the old, nonexistent
        `view_collector` permission, so a correctly permissioned user
        never saw the menu entry.
        """
        (menu_item,) = navigation.facts_collection_menu
        self.assertEqual(menu_item.permissions, ["netbox_facts.view_collectionplan"])
        self.assertNotIn("netbox_facts.view_collector", menu_item.permissions)


class CollectionPlanMetaPermissionsTest(TestCase):
    """Regression test for #131: run/results permissions must be declared."""

    def test_run_collector_permission_declared(self):
        """`run_collector` is checked by views.py and the template but was
        never declared on the model, so it could never be granted through
        Django groups.
        """
        codenames = dict(CollectionPlan._meta.permissions)
        self.assertIn("run_collector", codenames)
        self.assertTrue(codenames["run_collector"])

    def test_view_collector_results_permission_declared(self):
        """`view_collector_results` is checked by the Results tab view but
        was never declared on the model.
        """
        codenames = dict(CollectionPlan._meta.permissions)
        self.assertIn("view_collector_results", codenames)
        self.assertTrue(codenames["view_collector_results"])

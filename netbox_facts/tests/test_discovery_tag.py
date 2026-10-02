"""Tests for the protected discovery tag (issue #157).

Covers the migration that establishes the tag's row, the slug-based
lookup helper, the rename/delete guard signal, and the backward-
compatibility guarantee that objects tagged by the old name-based path
are still recognized by the new slug-based lookups -- they carry the
same tag row.
"""

import importlib
from contextlib import contextmanager

from dcim.models.device_components import Interface
from django.apps import apps
from django.db import IntegrityError, transaction
from django.db.models.signals import pre_delete
from django.test import TestCase
from extras.models import Tag
from utilities.exceptions import AbortRequest

from netbox_facts.constants import AUTO_D_TAG, AUTO_D_TAG_DESCRIPTION, AUTO_D_TAG_SLUG
from netbox_facts.helpers.netbox import get_discovery_tag
from netbox_facts.signals import protect_discovery_tag_delete
from netbox_facts.tests.test_helpers import CollectorTestMixin

migration_module = importlib.import_module("netbox_facts.migrations.0035_discovery_tag")
get_or_create_discovery_tag = migration_module.get_or_create_discovery_tag


def _reset_discovery_tag_cache():
    """Clear helpers.netbox's module-level pk cache between tests.

    The cache is deliberately process-lifetime, not per-test, so a test
    that deletes or replaces the tag row must reset it itself or it would
    see another test's stale pk.
    """
    import netbox_facts.helpers.netbox as netbox_helpers

    netbox_helpers._discovery_tag_pk = None


@contextmanager
def _delete_guard_disabled():
    """Temporarily disconnect the delete guard to set up test fixtures.

    Some tests need to remove the baseline tag the 0035 migration already
    created (to exercise "the tag does not exist yet") -- doing that
    through the ORM would otherwise trip the exact guard tested elsewhere
    in this module. That guard is a production safeguard against an
    operator or API client deleting the tag, not a constraint on how test
    fixtures set up a scenario, so it is fine to lift here.
    """
    pre_delete.disconnect(protect_discovery_tag_delete, sender=Tag)
    try:
        yield
    finally:
        pre_delete.connect(protect_discovery_tag_delete, sender=Tag)


class DiscoveryTagMigrationTest(TestCase):
    """Tests for get_or_create_discovery_tag(), called directly."""

    def setUp(self):
        with _delete_guard_disabled():
            Tag.objects.filter(slug=AUTO_D_TAG_SLUG).delete()
            Tag.objects.filter(name=AUTO_D_TAG).delete()
        _reset_discovery_tag_cache()

    def test_creates_tag_when_missing(self):
        """With no matching tag at all, the function creates one."""
        get_or_create_discovery_tag(apps, None)

        tag = Tag.objects.get(slug=AUTO_D_TAG_SLUG)
        self.assertEqual(tag.name, AUTO_D_TAG)
        self.assertEqual(tag.description, AUTO_D_TAG_DESCRIPTION)

    def test_idempotent_on_repeated_runs(self):
        """Running the migration function twice does not duplicate the tag."""
        get_or_create_discovery_tag(apps, None)
        first_pk = Tag.objects.get(slug=AUTO_D_TAG_SLUG).pk

        get_or_create_discovery_tag(apps, None)

        self.assertEqual(Tag.objects.filter(slug=AUTO_D_TAG_SLUG).count(), 1)
        self.assertEqual(Tag.objects.get(slug=AUTO_D_TAG_SLUG).pk, first_pk)

    def test_adopts_pre_existing_tag_with_different_slug(self):
        """A tag already carrying the display name is adopted, not duplicated.

        This is the state an install predating this migration is in: every
        tags.add(AUTO_D_TAG) call before the migration existed created the
        tag by name alone, under whatever slug happened to result.
        """
        legacy_tag = Tag.objects.create(
            name=AUTO_D_TAG,
            slug="auto-discovered-legacy-slug",
            description="",
        )

        get_or_create_discovery_tag(apps, None)

        self.assertEqual(Tag.objects.filter(name=AUTO_D_TAG).count(), 1)
        adopted = Tag.objects.get(slug=AUTO_D_TAG_SLUG)
        self.assertEqual(adopted.pk, legacy_tag.pk)
        self.assertEqual(adopted.description, AUTO_D_TAG_DESCRIPTION)

    def test_adopts_pre_existing_tag_without_overwriting_description(self):
        """An operator's own description on the legacy tag is preserved."""
        legacy_tag = Tag.objects.create(
            name=AUTO_D_TAG,
            slug="auto-discovered-legacy-slug",
            description="Custom operator note",
        )

        get_or_create_discovery_tag(apps, None)

        adopted = Tag.objects.get(pk=legacy_tag.pk)
        self.assertEqual(adopted.slug, AUTO_D_TAG_SLUG)
        self.assertEqual(adopted.description, "Custom operator note")


class GetDiscoveryTagTest(TestCase):
    """Tests for helpers.netbox.get_discovery_tag()."""

    def setUp(self):
        _reset_discovery_tag_cache()

    def test_resolves_the_migration_created_tag(self):
        tag = get_discovery_tag()
        self.assertEqual(tag.slug, AUTO_D_TAG_SLUG)

    def test_resolves_after_display_name_rename(self):
        """Renaming the display name must not break the slug-based lookup."""
        tag = get_discovery_tag()
        tag.name = "Renamed By Operator"
        tag.save()
        _reset_discovery_tag_cache()

        resolved = get_discovery_tag()

        self.assertEqual(resolved.pk, tag.pk)
        self.assertEqual(resolved.slug, AUTO_D_TAG_SLUG)

    def test_recreates_tag_if_missing(self):
        """A cached handle must tolerate the tag being deleted and recreated."""
        first = get_discovery_tag()
        first_pk = first.pk
        with _delete_guard_disabled():
            Tag.objects.filter(pk=first_pk).delete()

        second = get_discovery_tag()

        self.assertNotEqual(second.pk, first_pk)
        self.assertEqual(second.slug, AUTO_D_TAG_SLUG)


class DiscoveryTagGuardTest(TestCase):
    """Tests for the pre_save/pre_delete signal guard in signals.py."""

    def setUp(self):
        _reset_discovery_tag_cache()
        self.tag = get_discovery_tag()

    def test_rename_slug_is_blocked(self):
        self.tag.slug = "renamed-slug"
        with self.assertRaises(AbortRequest):
            self.tag.save()

    def test_delete_is_blocked(self):
        # A real request path wraps delete() in its own atomic block for
        # exactly this reason (see object_views.py's ObjectDeleteView) --
        # it turns the AbortRequest into a clean rollback to a savepoint
        # rather than poisoning the whole transaction.
        with self.assertRaises(AbortRequest), transaction.atomic():
            self.tag.delete()
        self.assertTrue(Tag.objects.filter(pk=self.tag.pk).exists())

    def test_color_change_is_allowed(self):
        self.tag.color = "ff0000"
        self.tag.save()

        self.tag.refresh_from_db()
        self.assertEqual(self.tag.color, "ff0000")

    def test_description_change_is_allowed(self):
        self.tag.description = "Updated description"
        self.tag.save()

        self.tag.refresh_from_db()
        self.assertEqual(self.tag.description, "Updated description")

    def test_display_name_change_is_allowed(self):
        self.tag.name = "Something Else"
        self.tag.save()

        self.tag.refresh_from_db()
        self.assertEqual(self.tag.name, "Something Else")
        self.assertEqual(self.tag.slug, AUTO_D_TAG_SLUG)

    def test_creating_a_different_tag_at_the_slug_is_unaffected_by_guard(self):
        """The guard only fires for an existing row's slug changing underneath it.

        A brand-new, unrelated tag happening to collide on this slug is a
        database-level uniqueness problem, not something the signal needs
        to referee.
        """
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Tag.objects.create(name="Unrelated", slug=AUTO_D_TAG_SLUG)


class DiscoveryTagOwnershipParityTest(CollectorTestMixin, TestCase):
    """Objects tagged before this change carry the exact same tag row.

    get_discovery_tag() and the old tags.add(AUTO_D_TAG) string-based path
    both resolve to one Tag row (same slug), so an object tagged by
    whichever helper existed when it was created is still recognized by
    every slug-based ownership gate added to collector.py/applier.py.
    """

    def setUp(self):
        _reset_discovery_tag_cache()

    def test_object_tagged_by_name_is_found_by_slug_filter(self):
        device = self._create_device("discovery-tag-parity")
        iface = Interface.objects.create(device=device, name="ge-0/0/0")

        # Old call site shape: tags.add() with the display-name string.
        iface.tags.add(AUTO_D_TAG)

        self.assertTrue(Interface.objects.filter(pk=iface.pk, tags__slug=AUTO_D_TAG_SLUG).exists())

    def test_object_tagged_by_name_carries_the_same_row_get_discovery_tag_returns(self):
        device = self._create_device("discovery-tag-parity-2")
        iface = Interface.objects.create(device=device, name="ge-0/0/1")
        iface.tags.add(AUTO_D_TAG)

        tagged = iface.tags.get()
        resolved = get_discovery_tag()

        self.assertEqual(tagged.pk, resolved.pk)

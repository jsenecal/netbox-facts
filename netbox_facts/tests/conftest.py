"""Pytest configuration for netbox_facts tests.

The meta-repo's other plugin test suites share a single PostgreSQL test
database (test_netbox, reused via --reuse-db), so concurrent runs from
different plugins or sessions can corrupt each other unless serialized on
a shared lock (see db-test-lock.sh in the meta-repo's scripts/ directory).

This plugin opts out of that shared lock by giving pytest-django its own
test database name, so its runs can never collide with another plugin's
schema. The override happens through pytest-django's documented
django_db_modify_db_settings extension point, which runs after Django
settings are loaded but before the test database is created -- so it
works regardless of which configuration module produced settings.DATABASES
(the devcontainer's manifest-driven loader or CI's inline configuration).
"""

import pytest


@pytest.fixture(scope="session")
def django_db_modify_db_settings(django_db_modify_db_settings_parallel_suffix):
    """Point the default database's TEST NAME at a plugin-specific database.

    Without this, pytest-django falls back to Django's default naming
    (test_<NAME>), which resolves to the same test_netbox database every
    other plugin in the meta-repo uses.
    """
    from django.conf import settings

    # Merge into the existing TEST dict rather than replacing it: Django's
    # connection handler has already populated defaults (MIRROR, CHARSET,
    # COLLATION, MIGRATE) by the time this fixture runs, and setup_databases()
    # indexes those keys directly rather than through .get().
    settings.DATABASES["default"].setdefault("TEST", {})
    settings.DATABASES["default"]["TEST"]["NAME"] = "test_netbox_facts"

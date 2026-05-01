"""Regression tests for plugin Table __init__ contracts.

NetBox 4.5 removed the ``user`` kwarg from ``BaseTable.__init__`` (the
deprecation warning landed earlier in the 4.5 series and the kwarg was
silently dropped later in the cycle). Any call site still passing
``user=...`` raises ``TypeError`` at request time on current releases.

These tests pin the post-fix call contract for the tables this plugin
instantiates itself (``MACAddressTable``) and the upstream table it
mounts in a child view (``ipam.tables.IPAddressTable``): both must
accept a queryset positionally with no extra kwargs. We deliberately do
not assert that ``user=`` raises, because mid-4.5 patch releases still
accept it as a no-op deprecation -- the plugin's CI matrix straddles
that boundary.
"""

import pytest
from ipam.models import IPAddress
from ipam.tables.ip import IPAddressTable

from netbox_facts.models import MACAddress
from netbox_facts.tables import MACAddressTable


@pytest.mark.django_db
def test_mac_address_table_accepts_queryset_only():
    MACAddressTable(MACAddress.objects.none())


@pytest.mark.django_db
def test_ip_address_table_accepts_queryset_only():
    IPAddressTable(IPAddress.objects.none())

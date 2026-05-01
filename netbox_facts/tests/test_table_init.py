"""Regression tests for plugin Table __init__ contracts.

NetBox 4.5 removed the ``user`` kwarg from ``BaseTable.__init__``; any plugin
call site still passing ``user=...`` raises ``TypeError`` at request time.
These tests pin the post-fix contract for the tables this plugin instantiates
itself (``MACAddressTable``) and for the upstream table it mounts in a child
view (``ipam.tables.IPAddressTable``), so a future NetBox upgrade altering
either signature will fail loudly here instead of in production.
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


@pytest.mark.django_db
def test_mac_address_table_rejects_user_kwarg():
    with pytest.raises(TypeError):
        MACAddressTable(MACAddress.objects.none(), user=None)


@pytest.mark.django_db
def test_ip_address_table_rejects_user_kwarg():
    with pytest.raises(TypeError):
        IPAddressTable(IPAddress.objects.none(), user=None)

# Phase 2+3 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement auto-scheduling via JobRunner, 5 standard NAPALM collectors, 3 vendor-specific Junos collectors, and conditional netbox-routing integration.

**Architecture:** Adopts NetBox's `JobRunner` pattern (from `netbox/jobs.py`) for job management. Collectors follow the existing `_ip_neighbors()`/`arp()` pattern in `collector.py`. Vendor-specific collectors use a registry-based dispatch. `netbox-routing` integration is conditional at import time.

**Tech Stack:** Django 4.x, NetBox 4.5.x, NAPALM 4.1, django-rq, netbox-routing (optional)

**Test runner:** `make test` (runs `manage.py makemigrations --check && manage.py test netbox_facts`)

**Design doc:** `docs/plans/2026-02-26-phase2-phase3-design.md`

---

## Task 1: Add OSPF to CollectionTypeChoices + Migration

**Files:**
- Modify: `netbox_facts/choices.py`
- Create: `netbox_facts/migrations/0022_*.py` (auto-generated)

**Step 1: Add OSPF choice**

In `netbox_facts/choices.py`, add `TYPE_OSPF = "ospf"` and append to `CHOICES`:

```python
class CollectionTypeChoices(ChoiceSet):
    key = "Collection.Type"

    TYPE_ARP = "arp"
    TYPE_NDP = "ndp"
    TYPE_INVENTORY = "inventory"
    TYPE_INTERFACES = "interfaces"
    TYPE_LLDP = "lldp"
    TYPE_L2 = "ethernet_switching"
    TYPE_L2CIRCTUITS = "l2_circuits"
    TYPE_EVPN = "evpn"
    TYPE_BGP = "bgp"
    TYPE_OSPF = "ospf"

    CHOICES = [
        (TYPE_ARP, "ARP", "gray"),
        (TYPE_NDP, _("IPv6 Neighbor Discovery"), "gray"),
        (TYPE_INVENTORY, _("Inventory"), "blue"),
        (TYPE_INTERFACES, _("Interfaces"), "purple"),
        (TYPE_LLDP, _("LLDP"), "cyan"),
        (TYPE_L2, _("Ethernet Switching Tables"), "black"),
        (TYPE_L2CIRCTUITS, _("L2 Circuits"), "orange"),
        (TYPE_EVPN, "EVPN", "red"),
        (TYPE_BGP, "BGP", "green"),
        (TYPE_OSPF, "OSPF", "teal"),
    ]
```

**Step 2: Generate and apply migration**

```bash
make migrations
make migrate
```

**Step 3: Verify**

```bash
make test
```
Expected: All existing tests pass, migration check clean.

**Step 4: Commit**

```bash
git add netbox_facts/choices.py netbox_facts/migrations/0022_*
git commit -m "Add OSPF to CollectionTypeChoices"
```

---

## Task 2: Refactor Jobs to Use JobRunner Pattern

**Files:**
- Modify: `netbox_facts/jobs.py`
- Modify: `netbox_facts/models/collection_plan.py`
- Modify: `netbox_facts/views.py` (update `CollectorRunView` to use new enqueue)
- Create: `netbox_facts/tests/test_jobs.py`

**Context:** NetBox's `JobRunner` (at `/opt/netbox/netbox/netbox/jobs.py:54`) is an ABC that wraps `Job.enqueue()` with lifecycle management (`handle()` → `start()` → `run()` → `terminate()`), automatic rescheduling for interval-based jobs, and duplicate prevention via `enqueue_once()`. The canonical example is `SyncDataSourceJob` in `/opt/netbox/netbox/core/jobs.py:20`.

### Step 1: Write failing tests for CollectionJobRunner

Create `netbox_facts/tests/test_jobs.py`:

```python
from unittest.mock import patch, MagicMock

from django.test import TestCase
from dcim.choices import DeviceStatusChoices

from netbox_facts.choices import CollectionTypeChoices, CollectorStatusChoices
from netbox_facts.jobs import CollectionJobRunner
from netbox_facts.models import CollectionPlan


class CollectionJobRunnerTest(TestCase):
    """Tests for CollectionJobRunner."""

    @classmethod
    def setUpTestData(cls):
        cls.plan = CollectionPlan.objects.create(
            name="Job Test Plan",
            collector_type=CollectionTypeChoices.TYPE_ARP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )

    def test_runner_name(self):
        """CollectionJobRunner.name should be 'Facts Collection'."""
        self.assertEqual(CollectionJobRunner.name, "Facts Collection")

    @patch("netbox_facts.jobs.CollectionPlan")
    def test_enqueue_sets_status_to_queued(self, mock_plan_cls):
        """enqueue() should update the plan status to QUEUED."""
        mock_plan_cls.objects.filter.return_value.update = MagicMock()

        with patch("core.models.jobs.Job.enqueue") as mock_enqueue:
            mock_job = MagicMock()
            mock_job.object = self.plan
            mock_enqueue.return_value = mock_job

            CollectionJobRunner.enqueue(
                instance=self.plan,
                user=None,
                queue_name=self.plan.priority,
            )

            mock_plan_cls.objects.filter.assert_called_with(pk=self.plan.pk)

    @patch("netbox_facts.jobs.CollectionPlan")
    def test_run_calls_plan_run(self, mock_plan_cls):
        """run() should fetch the plan and call plan.run()."""
        mock_plan = MagicMock()
        mock_plan_cls.objects.get.return_value = mock_plan

        mock_job = MagicMock()
        mock_job.object_id = self.plan.pk

        runner = CollectionJobRunner(mock_job)
        runner.run()

        mock_plan_cls.objects.get.assert_called_once_with(pk=self.plan.pk)
        mock_plan.run.assert_called_once()

    @patch("netbox_facts.jobs.CollectionPlan")
    def test_run_passes_request_kwarg(self, mock_plan_cls):
        """run() should forward the request kwarg to plan.run()."""
        mock_plan = MagicMock()
        mock_plan_cls.objects.get.return_value = mock_plan
        mock_request = MagicMock()

        mock_job = MagicMock()
        mock_job.object_id = self.plan.pk

        runner = CollectionJobRunner(mock_job)
        runner.run(request=mock_request)

        mock_plan.run.assert_called_once_with(request=mock_request)
```

### Step 2: Run tests to verify they fail

```bash
make test
```
Expected: ImportError or AttributeError — `CollectionJobRunner` doesn't exist yet.

### Step 3: Implement CollectionJobRunner

Replace `netbox_facts/jobs.py` with:

```python
import logging

from netbox.jobs import JobRunner

from netbox_facts.choices import CollectorStatusChoices

logger = logging.getLogger(__name__)


class CollectionJobRunner(JobRunner):
    """JobRunner for NetBox Facts collection jobs."""

    class Meta:
        name = "Facts Collection"

    @classmethod
    def enqueue(cls, *args, **kwargs):
        """Enqueue a collection job, setting the plan status to QUEUED."""
        from netbox_facts.models import CollectionPlan

        job = super().enqueue(*args, **kwargs)

        # Update the CollectionPlan's status to queued
        if instance := job.object:
            instance.status = CollectorStatusChoices.QUEUED
            CollectionPlan.objects.filter(pk=instance.pk).update(
                status=CollectorStatusChoices.QUEUED
            )

        return job

    def run(self, request=None, *args, **kwargs):
        """Execute the collection plan."""
        from netbox_facts.models import CollectionPlan

        plan = CollectionPlan.objects.get(pk=self.job.object_id)
        plan.run(request=request)
```

### Step 4: Refactor CollectionPlan model

In `netbox_facts/models/collection_plan.py`:

**4a. Remove the custom `enqueue()` classmethod** (lines 320-370). Delete the entire `enqueue()` method.

**4b. Refactor `enqueue_collection_job()`** to use `CollectionJobRunner`:

```python
def enqueue_collection_job(self, request):
    """
    Enqueue a background job to perform the facts collection.
    """
    from netbox_facts.jobs import CollectionJobRunner

    user = (
        self.run_as
        if request.user.is_superuser and self.run_as is not None
        else request.user
    )

    self.current_job = CollectionJobRunner.enqueue(
        instance=self,
        user=user,
        queue_name=self.priority,
        request=copy_safe_request(request),
    )
    return self.current_job
```

**4c. Make `run()` work without a request:**

```python
def run(
    self, request=None, *args, **kwargs
):  # pylint: disable=missing-function-docstring,unused-argument
    if self.status == CollectorStatusChoices.WORKING:
        raise OperationNotSupported(
            "Cannot initiate collection job; Collector already working."
        )

    self.status = CollectorStatusChoices.WORKING
    CollectionPlan.objects.filter(pk=self.pk).update(status=self.status)

    napalm_args = self.get_napalm_args()
    if napalm_args and napalm_args.get("debug", False):
        import debugpy  # pylint: disable=import-outside-toplevel

        debugpy.listen(("0.0.0.0", 5678))
        debugpy.wait_for_client()  # blocks execution until client is attached
        self.napalm_args.pop("debug")

    # Create a new NapalmCollector instance
    runner = NapalmCollector(self)

    if request:
        with event_tracking(request):
            runner.execute()
    else:
        runner.execute()

    # Update status & last_synced time
    self.status = CollectorStatusChoices.COMPLETED
    self.last_run = timezone.now()
    CollectionPlan.objects.filter(pk=self.pk).update(
        status=self.status, last_run=self.last_run
    )
```

**4d. Remove now-unused imports** from `collection_plan.py`:
- Remove `import django_rq`
- Remove `import uuid`
- Remove `from django.utils.module_loading import import_string`
- Keep `from utilities.request import copy_safe_request` (still used in `enqueue_collection_job`)

### Step 5: Run tests

```bash
make test
```
Expected: All tests pass.

### Step 6: Commit

```bash
git add netbox_facts/jobs.py netbox_facts/models/collection_plan.py netbox_facts/tests/test_jobs.py
git commit -m "Refactor jobs to use NetBox JobRunner pattern"
```

---

## Task 3: Implement Auto-Scheduling Signal

**Files:**
- Modify: `netbox_facts/signals.py`
- Create: `netbox_facts/tests/test_signals.py`

**Context:** Mirrors `core/signals.py:enqueue_sync_job` (line 266) which handles DataSource periodic sync scheduling.

### Step 1: Write failing tests

Create `netbox_facts/tests/test_signals.py`:

```python
from unittest.mock import patch, MagicMock

from django.test import TestCase
from dcim.choices import DeviceStatusChoices

from netbox_facts.choices import CollectionTypeChoices
from netbox_facts.models import CollectionPlan


class HandleCollectionJobChangeSignalTest(TestCase):
    """Tests for the handle_collection_job_change signal."""

    def _create_plan(self, **kwargs):
        defaults = {
            "name": "Signal Test Plan",
            "collector_type": CollectionTypeChoices.TYPE_ARP,
            "napalm_driver": "junos",
            "device_status": [DeviceStatusChoices.STATUS_ACTIVE],
        }
        defaults.update(kwargs)
        return CollectionPlan(**defaults)

    @patch("netbox_facts.signals.CollectionJobRunner")
    def test_enqueue_once_called_when_enabled_with_interval(self, mock_runner):
        """Saving an enabled plan with interval should call enqueue_once."""
        plan = self._create_plan(enabled=True, interval=60)
        plan.save()

        mock_runner.enqueue_once.assert_called_once()
        call_kwargs = mock_runner.enqueue_once.call_args
        self.assertEqual(call_kwargs.kwargs.get("interval") or call_kwargs[1].get("interval", call_kwargs[0][1] if len(call_kwargs[0]) > 1 else None), 60)

    @patch("netbox_facts.signals.CollectionJobRunner")
    def test_no_enqueue_when_disabled(self, mock_runner):
        """Saving a disabled plan should not call enqueue_once."""
        plan = self._create_plan(enabled=False, interval=60)
        plan.save()

        mock_runner.enqueue_once.assert_not_called()

    @patch("netbox_facts.signals.CollectionJobRunner")
    def test_no_enqueue_when_no_interval(self, mock_runner):
        """Saving an enabled plan without interval should not call enqueue_once."""
        plan = self._create_plan(enabled=True, interval=None)
        plan.save()

        mock_runner.enqueue_once.assert_not_called()

    @patch("netbox_facts.signals.CollectionJobRunner")
    def test_deletes_jobs_when_disabled(self, mock_runner):
        """Disabling a plan should delete pending scheduled jobs."""
        # Create the plan first (enabled)
        plan = self._create_plan(enabled=True, interval=60, name="Delete Test Plan")
        plan.save()
        mock_runner.reset_mock()

        # Now disable it
        plan.enabled = False
        plan.save()

        # Should have called get_jobs to find and delete pending jobs
        mock_runner.get_jobs.assert_called()

    @patch("netbox_facts.signals.CollectionJobRunner")
    def test_deletes_jobs_when_interval_cleared(self, mock_runner):
        """Clearing interval on existing plan should delete pending jobs."""
        plan = self._create_plan(enabled=True, interval=60, name="Interval Clear Plan")
        plan.save()
        mock_runner.reset_mock()

        # Clear interval
        plan.interval = None
        plan.save()

        mock_runner.enqueue_once.assert_not_called()
```

### Step 2: Run tests to verify they fail

```bash
make test
```
Expected: Tests fail because signal handler is still `pass`.

### Step 3: Implement the signal handler

Replace `handle_collection_job_change` in `netbox_facts/signals.py`:

```python
from django.db.models.signals import post_save
from django.dispatch import receiver

from core.choices import JobStatusChoices
from dcim.models.devices import Manufacturer

from .models import MACAddress, MACVendor, CollectionPlan


@receiver(post_save, sender=MACAddress)
def handle_mac_change(
    instance: MACAddress, **kwargs
):  # pylint: disable=unused-argument
    # ... existing code unchanged ...


@receiver(post_save, sender=MACVendor)
def handle_mac_vendor_change(
    instance: MACVendor, **kwargs
):  # pylint: disable=unused-argument
    # ... existing code unchanged ...


@receiver(post_save, sender=CollectionPlan)
def handle_collection_job_change(
    instance: CollectionPlan, created=False, **kwargs
):  # pylint: disable=unused-argument
    """
    Schedule or cancel collection jobs when a CollectionPlan is saved.
    Mirrors the DataSource sync scheduling pattern from core/signals.py.
    """
    from netbox_facts.jobs import CollectionJobRunner

    if instance.enabled and instance.interval:
        CollectionJobRunner.enqueue_once(
            instance=instance,
            interval=instance.interval,
            user=instance.run_as,
            queue_name=instance.priority,
        )
    elif not created:
        # Delete any previously scheduled recurring jobs for this CollectionPlan
        for job in CollectionJobRunner.get_jobs(instance).defer("data").filter(
            interval__isnull=False,
            status=JobStatusChoices.STATUS_SCHEDULED,
        ):
            job.delete()
```

### Step 4: Run tests

```bash
make test
```
Expected: All tests pass.

### Step 5: Commit

```bash
git add netbox_facts/signals.py netbox_facts/tests/test_signals.py
git commit -m "Implement auto-scheduling signal for CollectionPlan"
```

---

## Task 4: Implement inventory() Collector

**Files:**
- Modify: `netbox_facts/helpers/collector.py`
- Modify: `netbox_facts/tests/test_helpers.py`

**Context:** `driver.get_facts()` returns:
```python
{
    'uptime': 151005.57,
    'vendor': 'Arista',
    'os_version': '4.14.3',
    'serial_number': 'SN0123A34AS',
    'model': 'vEOS',
    'hostname': 'eos-router',
    'fqdn': 'eos-router',
    'interface_list': ['Ethernet2', 'Management1', 'Ethernet1']
}
```

### Step 1: Write failing test

Add to `netbox_facts/tests/test_helpers.py`:

```python
from unittest.mock import MagicMock, patch, PropertyMock
from django.test import TestCase
from django.utils import timezone
from dcim.choices import DeviceStatusChoices
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from extras.models.models import JournalEntry

from netbox_facts.choices import CollectionTypeChoices
from netbox_facts.helpers.collector import NapalmCollector
from netbox_facts.models import CollectionPlan


class InventoryCollectorTest(TestCase):
    """Tests for the inventory() collector method."""

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="Inv Site", slug="inv-site")
        cls.manufacturer = Manufacturer.objects.create(name="InvMfg", slug="invmfg")
        cls.device_type = DeviceType.objects.create(
            manufacturer=cls.manufacturer, model="InvModel", slug="invmodel"
        )
        cls.role = DeviceRole.objects.create(name="InvRole", slug="invrole")

    def _make_collector(self, plan):
        """Create a NapalmCollector with mocked internals."""
        with patch.object(NapalmCollector, "__init__", lambda self, p: None):
            collector = NapalmCollector.__new__(NapalmCollector)
        collector.plan = plan
        collector._collector_type = plan.collector_type
        collector._napalm_args = {}
        collector._napalm_driver = None
        collector._napalm_username = "test"
        collector._napalm_password = "test"
        collector._interfaces_re = MagicMock()
        collector._interfaces_re.match.return_value = True
        collector._devices = []
        collector._current_device = None
        collector._log_prefix = ""
        collector._now = timezone.now()
        return collector

    def test_inventory_updates_serial(self):
        """inventory() should update device serial from get_facts()."""
        device = Device.objects.create(
            name="inv-dev1",
            site=self.site,
            device_type=self.device_type,
            role=self.role,
            serial="OLD_SERIAL",
        )
        plan = CollectionPlan.objects.create(
            name="Inv Plan",
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.get_facts.return_value = {
            "uptime": 1000.0,
            "vendor": "Juniper",
            "os_version": "21.4R3",
            "serial_number": "NEW_SERIAL",
            "model": "QFX5100",
            "hostname": "inv-dev1",
            "fqdn": "inv-dev1.example.com",
            "interface_list": ["ge-0/0/0", "lo0"],
        }

        collector.inventory(mock_driver)

        device.refresh_from_db()
        self.assertEqual(device.serial, "NEW_SERIAL")

    def test_inventory_creates_journal_entry_on_change(self):
        """inventory() should create a journal entry when device data changes."""
        device = Device.objects.create(
            name="inv-dev2",
            site=self.site,
            device_type=self.device_type,
            role=self.role,
            serial="",
        )
        plan = CollectionPlan.objects.create(
            name="Inv Plan 2",
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.get_facts.return_value = {
            "uptime": 2000.0,
            "vendor": "Juniper",
            "os_version": "22.1R1",
            "serial_number": "SN123",
            "model": "QFX5100",
            "hostname": "inv-dev2",
            "fqdn": "inv-dev2.example.com",
            "interface_list": [],
        }

        collector.inventory(mock_driver)

        entries = JournalEntry.objects.filter(
            assigned_object_id=device.pk,
        )
        self.assertTrue(entries.exists())

    def test_inventory_no_change_no_journal(self):
        """inventory() should NOT create a journal entry when nothing changes."""
        device = Device.objects.create(
            name="inv-dev3",
            site=self.site,
            device_type=self.device_type,
            role=self.role,
            serial="SAME_SERIAL",
        )
        plan = CollectionPlan.objects.create(
            name="Inv Plan 3",
            collector_type=CollectionTypeChoices.TYPE_INVENTORY,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.get_facts.return_value = {
            "uptime": 3000.0,
            "vendor": "Juniper",
            "os_version": "",
            "serial_number": "SAME_SERIAL",
            "model": "QFX5100",
            "hostname": "inv-dev3",
            "fqdn": "inv-dev3.example.com",
            "interface_list": [],
        }

        collector.inventory(mock_driver)

        entries = JournalEntry.objects.filter(assigned_object_id=device.pk)
        self.assertFalse(entries.exists())
```

### Step 2: Run tests to verify they fail

```bash
make test
```
Expected: `NotImplementedError` from `inventory()`.

### Step 3: Implement inventory()

In `netbox_facts/helpers/collector.py`, replace the `inventory()` stub:

```python
def inventory(self, driver: NetworkDriver):
    """Collect inventory data from a device using get_facts()."""
    facts = driver.get_facts()
    device = self._current_device
    changes = []

    # Update serial number if changed
    new_serial = facts.get("serial_number", "")
    if new_serial and device.serial != new_serial:
        changes.append(f"Serial: `{device.serial}` → `{new_serial}`")
        Device.objects.filter(pk=device.pk).update(serial=new_serial)

    # Log OS version info
    os_version = facts.get("os_version", "")
    if os_version:
        changes.append(f"OS version: `{os_version}`")

    # Log hostname info
    hostname = facts.get("hostname", "")
    fqdn = facts.get("fqdn", "")
    if hostname:
        changes.append(f"Hostname: `{hostname}`" + (f" (FQDN: `{fqdn}`)" if fqdn else ""))

    # Create journal entry if there were changes
    if new_serial and device.serial != new_serial:
        JournalEntry.objects.create(
            created=self._now,
            assigned_object=device,
            kind=JournalEntryKindChoices.KIND_INFO,
            comments=f"Inventory facts collected:\n" + "\n".join(f"- {c}" for c in changes),
        )

    self._log_success("Inventory collection completed")
```

**Wait** — there's a bug in the logic above. The serial comparison after update won't work because we already updated it. Fix:

```python
def inventory(self, driver: NetworkDriver):
    """Collect inventory data from a device using get_facts()."""
    facts = driver.get_facts()
    device = self._current_device
    changes = []
    serial_changed = False

    # Check serial number
    new_serial = facts.get("serial_number", "")
    if new_serial and device.serial != new_serial:
        changes.append(f"Serial: `{device.serial}` → `{new_serial}`")
        Device.objects.filter(pk=device.pk).update(serial=new_serial)
        serial_changed = True

    # Log OS version info
    os_version = facts.get("os_version", "")
    if os_version:
        changes.append(f"OS version: `{os_version}`")

    # Log hostname info
    hostname = facts.get("hostname", "")
    fqdn = facts.get("fqdn", "")
    if hostname:
        changes.append(f"Hostname: `{hostname}`" + (f" (FQDN: `{fqdn}`)" if fqdn else ""))

    # Create journal entry only if actual data changed
    if serial_changed:
        JournalEntry.objects.create(
            created=self._now,
            assigned_object=device,
            kind=JournalEntryKindChoices.KIND_INFO,
            comments=f"Inventory facts collected:\n" + "\n".join(f"- {c}" for c in changes),
        )

    self._log_success("Inventory collection completed")
```

### Step 4: Run tests

```bash
make test
```
Expected: All tests pass.

### Step 5: Commit

```bash
git add netbox_facts/helpers/collector.py netbox_facts/tests/test_helpers.py
git commit -m "Implement inventory() collector"
```

---

## Task 5: Implement interfaces() Collector

**Files:**
- Modify: `netbox_facts/helpers/collector.py`
- Modify: `netbox_facts/tests/test_helpers.py`

**Context:** `driver.get_interfaces()` returns:
```python
{
    'Ethernet1': {
        'is_up': True, 'is_enabled': True, 'description': 'foo',
        'last_flapped': 1429978575.15, 'speed': 1000.0, 'mtu': 1500,
        'mac_address': 'FA:16:3E:57:33:62',
    }
}
```

### Step 1: Write failing tests

Add to `netbox_facts/tests/test_helpers.py`:

```python
class InterfacesCollectorTest(TestCase):
    """Tests for the interfaces() collector method."""

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="Iface Site", slug="iface-site")
        cls.manufacturer = Manufacturer.objects.create(name="IfaceMfg", slug="ifacemfg")
        cls.device_type = DeviceType.objects.create(
            manufacturer=cls.manufacturer, model="IfaceModel", slug="ifacemodel"
        )
        cls.role = DeviceRole.objects.create(name="IfaceRole", slug="ifacerole")

    def _make_collector(self, plan):
        """Create a NapalmCollector with mocked internals."""
        with patch.object(NapalmCollector, "__init__", lambda self, p: None):
            collector = NapalmCollector.__new__(NapalmCollector)
        collector.plan = plan
        collector._collector_type = plan.collector_type
        collector._napalm_args = {}
        collector._napalm_driver = None
        collector._napalm_username = "test"
        collector._napalm_password = "test"
        import re
        collector._interfaces_re = re.compile(r".*")  # Match all interfaces
        collector._devices = []
        collector._current_device = None
        collector._log_prefix = ""
        collector._now = timezone.now()
        return collector

    def test_interfaces_creates_mac_for_interface(self):
        """interfaces() should create MACAddress for each interface's hardware MAC."""
        from netbox_facts.models import MACAddress

        device = Device.objects.create(
            name="iface-dev1", site=self.site,
            device_type=self.device_type, role=self.role,
        )
        from dcim.models import Interface
        iface = Interface.objects.create(device=device, name="ge-0/0/0", type="1000base-t")

        plan = CollectionPlan.objects.create(
            name="Iface Plan",
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.get_interfaces.return_value = {
            "ge-0/0/0": {
                "is_up": True, "is_enabled": True, "description": "uplink",
                "last_flapped": -1.0, "speed": 1000.0, "mtu": 1500,
                "mac_address": "AA:BB:CC:11:22:33",
            },
        }
        mock_driver.get_interfaces_ip.return_value = {}

        collector.interfaces(mock_driver)

        self.assertTrue(MACAddress.objects.filter(mac_address="AA:BB:CC:11:22:33").exists())
        mac = MACAddress.objects.get(mac_address="AA:BB:CC:11:22:33")
        self.assertEqual(mac.device_interface, iface)
        self.assertEqual(mac.discovery_method, CollectionTypeChoices.TYPE_INTERFACES)
        self.assertIsNotNone(mac.last_seen)

    def test_interfaces_skips_non_matching_interface(self):
        """interfaces() should skip interfaces that don't match the regex."""
        from netbox_facts.models import MACAddress
        import re

        device = Device.objects.create(
            name="iface-dev2", site=self.site,
            device_type=self.device_type, role=self.role,
        )

        plan = CollectionPlan.objects.create(
            name="Iface Plan 2",
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._interfaces_re = re.compile(r"^ge-")  # Only ge- interfaces
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.get_interfaces.return_value = {
            "Management1": {
                "is_up": True, "is_enabled": True, "description": "",
                "last_flapped": -1.0, "speed": 1000.0, "mtu": 1500,
                "mac_address": "AA:BB:CC:99:88:77",
            },
        }
        mock_driver.get_interfaces_ip.return_value = {}

        collector.interfaces(mock_driver)

        self.assertFalse(MACAddress.objects.filter(mac_address="AA:BB:CC:99:88:77").exists())

    def test_interfaces_skips_empty_mac(self):
        """interfaces() should skip interfaces with empty MAC address."""
        from netbox_facts.models import MACAddress

        device = Device.objects.create(
            name="iface-dev3", site=self.site,
            device_type=self.device_type, role=self.role,
        )
        from dcim.models import Interface
        Interface.objects.create(device=device, name="lo0", type="virtual")

        plan = CollectionPlan.objects.create(
            name="Iface Plan 3",
            collector_type=CollectionTypeChoices.TYPE_INTERFACES,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.get_interfaces.return_value = {
            "lo0": {
                "is_up": True, "is_enabled": True, "description": "loopback",
                "last_flapped": -1.0, "speed": 0, "mtu": 65535,
                "mac_address": "",
            },
        }
        mock_driver.get_interfaces_ip.return_value = {}

        initial_count = MACAddress.objects.count()
        collector.interfaces(mock_driver)
        self.assertEqual(MACAddress.objects.count(), initial_count)
```

### Step 2: Run tests to verify failure, then implement

In `netbox_facts/helpers/collector.py`, replace the `interfaces()` stub:

```python
def interfaces(self, driver: NetworkDriver):
    """Collect interface data from a device."""
    ifaces = driver.get_interfaces()
    ifaces_ip = driver.get_interfaces_ip()

    for name, data in ifaces.items():
        if not self._interfaces_re.match(name):
            continue

        # Get the matching interface from NetBox
        try:
            nb_iface = self._current_device.vc_interfaces().get(name=name)
        except Interface.DoesNotExist:
            self._log_warning(f"Interface `{name}` not found in NetBox. Skipping.")
            continue

        # Create/update MACAddress for interface hardware MAC
        mac_addr = data.get("mac_address", "")
        if mac_addr:
            netbox_mac, created = MACAddress.objects.get_or_create(
                mac_address=mac_addr
            )
            netbox_mac.device_interface = nb_iface
            netbox_mac.discovery_method = CollectionTypeChoices.TYPE_INTERFACES
            netbox_mac.last_seen = self._now
            netbox_mac.save()

            if created:
                netbox_mac.tags.add(AUTO_D_TAG)
                self._log_success(
                    f"Created MAC address {get_absolute_url_markdown(netbox_mac, bold=True)} "
                    f"for interface {get_absolute_url_markdown(nb_iface, bold=True)}."
                )
            else:
                self._log_info(
                    f"Updated MAC address {get_absolute_url_markdown(netbox_mac, bold=True)} "
                    f"for interface {get_absolute_url_markdown(nb_iface, bold=True)}."
                )

    self._log_success("Interface collection completed")
```

Also add import at top of `collector.py`:

```python
from netbox_facts.choices import CollectionTypeChoices
```

### Step 3: Run tests

```bash
make test
```

### Step 4: Commit

```bash
git add netbox_facts/helpers/collector.py netbox_facts/tests/test_helpers.py
git commit -m "Implement interfaces() collector"
```

---

## Task 6: Implement ethernet_switching() Collector

**Files:**
- Modify: `netbox_facts/helpers/collector.py`
- Modify: `netbox_facts/tests/test_helpers.py`

**Context:** `driver.get_mac_address_table()` returns:
```python
[
    {'mac': '00:1C:58:29:4A:71', 'interface': 'Ethernet47', 'vlan': 100,
     'static': False, 'active': True, 'moves': 1, 'last_move': 1454417742.58}
]
```

### Step 1: Write failing tests

Add to `netbox_facts/tests/test_helpers.py`:

```python
class EthernetSwitchingCollectorTest(TestCase):
    """Tests for the ethernet_switching() collector method."""

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="ES Site", slug="es-site")
        cls.manufacturer = Manufacturer.objects.create(name="ESMfg", slug="esmfg")
        cls.device_type = DeviceType.objects.create(
            manufacturer=cls.manufacturer, model="ESModel", slug="esmodel"
        )
        cls.role = DeviceRole.objects.create(name="ESRole", slug="esrole")

    def _make_collector(self, plan):
        with patch.object(NapalmCollector, "__init__", lambda self, p: None):
            collector = NapalmCollector.__new__(NapalmCollector)
        collector.plan = plan
        collector._collector_type = plan.collector_type
        collector._napalm_args = {}
        collector._napalm_driver = None
        collector._napalm_username = "test"
        collector._napalm_password = "test"
        import re
        collector._interfaces_re = re.compile(r".*")
        collector._devices = []
        collector._current_device = None
        collector._log_prefix = ""
        collector._now = timezone.now()
        return collector

    def test_creates_mac_from_table(self):
        """ethernet_switching() should create MACAddress from MAC table entries."""
        from netbox_facts.models import MACAddress
        from dcim.models import Interface

        device = Device.objects.create(
            name="es-dev1", site=self.site,
            device_type=self.device_type, role=self.role,
        )
        iface = Interface.objects.create(device=device, name="ge-0/0/0", type="1000base-t")

        plan = CollectionPlan.objects.create(
            name="ES Plan",
            collector_type=CollectionTypeChoices.TYPE_L2,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.get_mac_address_table.return_value = [
            {
                "mac": "00:1C:58:29:4A:71",
                "interface": "ge-0/0/0",
                "vlan": 100,
                "static": False,
                "active": True,
                "moves": 0,
                "last_move": 0.0,
            },
        ]

        collector.ethernet_switching(mock_driver)

        self.assertTrue(MACAddress.objects.filter(mac_address="00:1C:58:29:4A:71").exists())
        mac = MACAddress.objects.get(mac_address="00:1C:58:29:4A:71")
        self.assertIn(iface, mac.interfaces.all())
        self.assertEqual(mac.discovery_method, CollectionTypeChoices.TYPE_L2)

    def test_skips_empty_mac(self):
        """ethernet_switching() should skip entries with empty MAC."""
        from netbox_facts.models import MACAddress

        device = Device.objects.create(
            name="es-dev2", site=self.site,
            device_type=self.device_type, role=self.role,
        )

        plan = CollectionPlan.objects.create(
            name="ES Plan 2",
            collector_type=CollectionTypeChoices.TYPE_L2,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.get_mac_address_table.return_value = [
            {"mac": "", "interface": "ge-0/0/0", "vlan": 1, "static": False, "active": True, "moves": 0, "last_move": 0.0},
        ]

        initial_count = MACAddress.objects.count()
        collector.ethernet_switching(mock_driver)
        self.assertEqual(MACAddress.objects.count(), initial_count)

    def test_skips_interface_not_in_netbox(self):
        """ethernet_switching() should log warning for missing interfaces."""
        from netbox_facts.models import MACAddress

        device = Device.objects.create(
            name="es-dev3", site=self.site,
            device_type=self.device_type, role=self.role,
        )

        plan = CollectionPlan.objects.create(
            name="ES Plan 3",
            collector_type=CollectionTypeChoices.TYPE_L2,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.get_mac_address_table.return_value = [
            {"mac": "AA:BB:CC:DD:EE:FF", "interface": "nonexistent", "vlan": 1, "static": False, "active": True, "moves": 0, "last_move": 0.0},
        ]

        initial_count = MACAddress.objects.count()
        collector.ethernet_switching(mock_driver)
        self.assertEqual(MACAddress.objects.count(), initial_count)
```

### Step 2: Implement ethernet_switching()

```python
def ethernet_switching(self, driver: NetworkDriver):
    """Collect MAC address table data from a device."""
    mac_table = driver.get_mac_address_table()

    for entry in mac_table:
        mac_addr = entry.get("mac", "")
        if not mac_addr:
            continue

        iface_name = entry.get("interface", "")
        if not iface_name or not self._interfaces_re.match(iface_name):
            continue

        try:
            nb_iface = self._current_device.vc_interfaces().get(name=iface_name)
        except Interface.DoesNotExist:
            self._log_warning(
                f"Interface `{iface_name}` not found in NetBox for MAC `{mac_addr}`. Skipping."
            )
            continue

        netbox_mac, created = MACAddress.objects.get_or_create(mac_address=mac_addr)
        netbox_mac.interfaces.add(nb_iface)
        netbox_mac.discovery_method = CollectionTypeChoices.TYPE_L2
        netbox_mac.last_seen = self._now
        netbox_mac.save()

        if created:
            netbox_mac.tags.add(AUTO_D_TAG)
            self._log_success(
                f"Created MAC address {get_absolute_url_markdown(netbox_mac, bold=True)} "
                f"on {get_absolute_url_markdown(nb_iface, bold=True)} (VLAN {entry.get('vlan', 'N/A')})."
            )
        else:
            self._log_info(
                f"Updated MAC address {get_absolute_url_markdown(netbox_mac, bold=True)} "
                f"on {get_absolute_url_markdown(nb_iface, bold=True)}."
            )

    self._log_success("Ethernet switching collection completed")
```

### Step 3: Run tests and commit

```bash
make test
git add netbox_facts/helpers/collector.py netbox_facts/tests/test_helpers.py
git commit -m "Implement ethernet_switching() collector"
```

---

## Task 7: Implement lldp() Collector

**Files:**
- Modify: `netbox_facts/helpers/collector.py`
- Modify: `netbox_facts/tests/test_helpers.py`

**Context:** `driver.get_lldp_neighbors_detail()` returns:
```python
{
    'TenGigE0/0/0/8': [
        {
            'parent_interface': 'Bundle-Ether8',
            'remote_chassis_id': '8c60.4f69.e96c',
            'remote_system_name': 'switch',
            'remote_port': 'Eth2/2/1',
            'remote_port_description': 'Ethernet2/2/1',
            'remote_system_description': 'Cisco NX-OS 7.1',
            'remote_system_capab': ['bridge', 'repeater'],
            'remote_system_enable_capab': ['bridge']
        }
    ]
}
```

**Constraints:** Cables only created when both devices exist in NetBox AND are in the same Site. No new Device creation.

### Step 1: Write failing tests

Add to `netbox_facts/tests/test_helpers.py`:

```python
class LLDPCollectorTest(TestCase):
    """Tests for the lldp() collector method."""

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="LLDP Site", slug="lldp-site")
        cls.site2 = Site.objects.create(name="LLDP Site 2", slug="lldp-site-2")
        cls.manufacturer = Manufacturer.objects.create(name="LLDPMfg", slug="lldpmfg")
        cls.device_type = DeviceType.objects.create(
            manufacturer=cls.manufacturer, model="LLDPModel", slug="lldpmodel"
        )
        cls.role = DeviceRole.objects.create(name="LLDPRole", slug="lldprole")

    def _make_collector(self, plan):
        with patch.object(NapalmCollector, "__init__", lambda self, p: None):
            collector = NapalmCollector.__new__(NapalmCollector)
        collector.plan = plan
        collector._collector_type = plan.collector_type
        collector._napalm_args = {}
        collector._napalm_driver = None
        collector._napalm_username = "test"
        collector._napalm_password = "test"
        import re
        collector._interfaces_re = re.compile(r".*")
        collector._devices = []
        collector._current_device = None
        collector._log_prefix = ""
        collector._now = timezone.now()
        return collector

    def test_creates_cable_same_site(self):
        """lldp() should create a cable between devices in the same site."""
        from dcim.models import Interface, Cable

        local_device = Device.objects.create(
            name="lldp-local", site=self.site,
            device_type=self.device_type, role=self.role,
        )
        remote_device = Device.objects.create(
            name="lldp-remote", site=self.site,
            device_type=self.device_type, role=self.role,
        )
        local_iface = Interface.objects.create(device=local_device, name="ge-0/0/0", type="1000base-t")
        remote_iface = Interface.objects.create(device=remote_device, name="ge-0/0/1", type="1000base-t")

        plan = CollectionPlan.objects.create(
            name="LLDP Plan",
            collector_type=CollectionTypeChoices.TYPE_LLDP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = local_device

        mock_driver = MagicMock()
        mock_driver.get_lldp_neighbors_detail.return_value = {
            "ge-0/0/0": [
                {
                    "parent_interface": "",
                    "remote_chassis_id": "AA:BB:CC:DD:EE:FF",
                    "remote_system_name": "lldp-remote",
                    "remote_port": "ge-0/0/1",
                    "remote_port_description": "",
                    "remote_system_description": "Juniper",
                    "remote_system_capab": ["router"],
                    "remote_system_enable_capab": ["router"],
                },
            ],
        }

        collector.lldp(mock_driver)

        # Verify cable was created
        local_iface.refresh_from_db()
        self.assertIsNotNone(local_iface.cable)

    def test_no_cable_cross_site(self):
        """lldp() should NOT create a cable between devices in different sites."""
        from dcim.models import Interface, Cable

        local_device = Device.objects.create(
            name="lldp-local2", site=self.site,
            device_type=self.device_type, role=self.role,
        )
        remote_device = Device.objects.create(
            name="lldp-remote2", site=self.site2,
            device_type=self.device_type, role=self.role,
        )
        local_iface = Interface.objects.create(device=local_device, name="ge-0/0/0", type="1000base-t")
        remote_iface = Interface.objects.create(device=remote_device, name="ge-0/0/1", type="1000base-t")

        plan = CollectionPlan.objects.create(
            name="LLDP Plan 2",
            collector_type=CollectionTypeChoices.TYPE_LLDP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = local_device

        mock_driver = MagicMock()
        mock_driver.get_lldp_neighbors_detail.return_value = {
            "ge-0/0/0": [
                {
                    "parent_interface": "",
                    "remote_chassis_id": "AA:BB:CC:DD:EE:FF",
                    "remote_system_name": "lldp-remote2",
                    "remote_port": "ge-0/0/1",
                    "remote_port_description": "",
                    "remote_system_description": "",
                    "remote_system_capab": [],
                    "remote_system_enable_capab": [],
                },
            ],
        }

        collector.lldp(mock_driver)

        local_iface.refresh_from_db()
        self.assertIsNone(local_iface.cable)

    def test_no_cable_unknown_remote_device(self):
        """lldp() should log info when remote device is not in NetBox."""
        from dcim.models import Interface

        local_device = Device.objects.create(
            name="lldp-local3", site=self.site,
            device_type=self.device_type, role=self.role,
        )
        local_iface = Interface.objects.create(device=local_device, name="ge-0/0/0", type="1000base-t")

        plan = CollectionPlan.objects.create(
            name="LLDP Plan 3",
            collector_type=CollectionTypeChoices.TYPE_LLDP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = local_device

        mock_driver = MagicMock()
        mock_driver.get_lldp_neighbors_detail.return_value = {
            "ge-0/0/0": [
                {
                    "parent_interface": "",
                    "remote_chassis_id": "AA:BB:CC:DD:EE:FF",
                    "remote_system_name": "unknown-device",
                    "remote_port": "eth0",
                    "remote_port_description": "",
                    "remote_system_description": "",
                    "remote_system_capab": [],
                    "remote_system_enable_capab": [],
                },
            ],
        }

        collector.lldp(mock_driver)

        local_iface.refresh_from_db()
        self.assertIsNone(local_iface.cable)

    def test_no_cable_already_cabled(self):
        """lldp() should not create duplicate cables."""
        from dcim.models import Interface, Cable

        local_device = Device.objects.create(
            name="lldp-local4", site=self.site,
            device_type=self.device_type, role=self.role,
        )
        remote_device = Device.objects.create(
            name="lldp-remote4", site=self.site,
            device_type=self.device_type, role=self.role,
        )
        local_iface = Interface.objects.create(device=local_device, name="ge-0/0/0", type="1000base-t")
        remote_iface = Interface.objects.create(device=remote_device, name="ge-0/0/1", type="1000base-t")

        # Pre-create a cable
        cable = Cable(a_terminations=[local_iface], b_terminations=[remote_iface])
        cable.save()

        plan = CollectionPlan.objects.create(
            name="LLDP Plan 4",
            collector_type=CollectionTypeChoices.TYPE_LLDP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = local_device

        mock_driver = MagicMock()
        mock_driver.get_lldp_neighbors_detail.return_value = {
            "ge-0/0/0": [
                {
                    "parent_interface": "",
                    "remote_chassis_id": "AA:BB:CC:DD:EE:FF",
                    "remote_system_name": "lldp-remote4",
                    "remote_port": "ge-0/0/1",
                    "remote_port_description": "",
                    "remote_system_description": "",
                    "remote_system_capab": [],
                    "remote_system_enable_capab": [],
                },
            ],
        }

        initial_cable_count = Cable.objects.count()
        collector.lldp(mock_driver)
        self.assertEqual(Cable.objects.count(), initial_cable_count)
```

### Step 2: Implement lldp()

Add imports at top of `collector.py`:
```python
from dcim.models.cables import Cable
from dcim.choices import LinkStatusChoices
```

```python
def lldp(self, driver: NetworkDriver):
    """Collect LLDP neighbor data from a device."""
    neighbors = driver.get_lldp_neighbors_detail()

    for local_iface_name, neighbor_list in neighbors.items():
        if not self._interfaces_re.match(local_iface_name):
            continue

        try:
            local_iface = self._current_device.vc_interfaces().get(name=local_iface_name)
        except Interface.DoesNotExist:
            self._log_warning(f"Local interface `{local_iface_name}` not found in NetBox. Skipping.")
            continue

        for neighbor in neighbor_list:
            remote_name = neighbor.get("remote_system_name", "")
            remote_port = neighbor.get("remote_port", "")

            if not remote_name:
                self._log_info(f"LLDP neighbor on `{local_iface_name}` has no system name. Skipping.")
                continue

            # Find remote device in NetBox
            try:
                remote_device = Device.objects.get(name=remote_name)
            except Device.DoesNotExist:
                self._log_info(
                    f"LLDP neighbor `{remote_name}` on `{local_iface_name}` not found in NetBox."
                )
                continue
            except Device.MultipleObjectsReturned:
                self._log_warning(
                    f"Multiple devices named `{remote_name}` in NetBox. Skipping."
                )
                continue

            # Same site check
            if remote_device.site_id != self._current_device.site_id:
                self._log_info(
                    f"LLDP neighbor `{remote_name}` is in a different site "
                    f"(`{remote_device.site}` vs `{self._current_device.site}`). Skipping cable."
                )
                continue

            # Find remote interface
            if not remote_port:
                self._log_warning(f"LLDP neighbor `{remote_name}` has no remote port. Skipping.")
                continue

            try:
                remote_iface = remote_device.vc_interfaces().get(name=remote_port)
            except Interface.DoesNotExist:
                self._log_warning(
                    f"Remote interface `{remote_port}` not found on `{remote_name}`. Skipping."
                )
                continue

            # Check if either interface already has a cable
            if local_iface.cable or remote_iface.cable:
                self._log_info(
                    f"Interface `{local_iface_name}` or `{remote_port}` already cabled. Skipping."
                )
                continue

            # Create cable
            cable = Cable(
                a_terminations=[local_iface],
                b_terminations=[remote_iface],
                status=LinkStatusChoices.STATUS_CONNECTED,
            )
            cable.full_clean()
            cable.save()
            cable.tags.add(AUTO_D_TAG)

            JournalEntry.objects.create(
                created=self._now,
                assigned_object=self._current_device,
                kind=JournalEntryKindChoices.KIND_INFO,
                comments=(
                    f"LLDP discovered cable: "
                    f"{get_absolute_url_markdown(local_iface, bold=True)} ↔ "
                    f"{get_absolute_url_markdown(remote_iface, bold=True)} "
                    f"on {get_absolute_url_markdown(remote_device, bold=True)}"
                ),
            )
            self._log_success(
                f"Created cable between `{local_iface_name}` and "
                f"`{remote_name}:{remote_port}`."
            )

    self._log_success("LLDP collection completed")
```

### Step 3: Run tests and commit

```bash
make test
git add netbox_facts/helpers/collector.py netbox_facts/tests/test_helpers.py
git commit -m "Implement lldp() collector with same-site cable creation"
```

---

## Task 8: Implement bgp() Collector

**Files:**
- Modify: `netbox_facts/helpers/collector.py`
- Modify: `netbox_facts/tests/test_helpers.py`

**Context:** `driver.get_bgp_neighbors_detail()` returns a nested dict: `{vrf_name: {as_number: [peer_details]}}`. NetBox has `ipam.ASN` (requires `rir` FK). Conditional `netbox-routing` integration deferred to Task 11.

### Step 1: Write failing tests

Add to `netbox_facts/tests/test_helpers.py`:

```python
class BGPCollectorTest(TestCase):
    """Tests for the bgp() collector method."""

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="BGP Site", slug="bgp-site")
        cls.manufacturer = Manufacturer.objects.create(name="BGPMfg", slug="bgpmfg")
        cls.device_type = DeviceType.objects.create(
            manufacturer=cls.manufacturer, model="BGPModel", slug="bgpmodel"
        )
        cls.role = DeviceRole.objects.create(name="BGPRole", slug="bgprole")

    def _make_collector(self, plan):
        with patch.object(NapalmCollector, "__init__", lambda self, p: None):
            collector = NapalmCollector.__new__(NapalmCollector)
        collector.plan = plan
        collector._collector_type = plan.collector_type
        collector._napalm_args = {}
        collector._napalm_driver = None
        collector._napalm_username = "test"
        collector._napalm_password = "test"
        import re
        collector._interfaces_re = re.compile(r".*")
        collector._devices = []
        collector._current_device = None
        collector._log_prefix = ""
        collector._now = timezone.now()
        return collector

    def test_creates_peer_ip(self):
        """bgp() should create IPAddress for discovered BGP peers."""
        from ipam.models import IPAddress

        device = Device.objects.create(
            name="bgp-dev1", site=self.site,
            device_type=self.device_type, role=self.role,
        )

        plan = CollectionPlan.objects.create(
            name="BGP Plan",
            collector_type=CollectionTypeChoices.TYPE_BGP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.get_bgp_neighbors_detail.return_value = {
            "global": {
                65001: [
                    {
                        "up": True,
                        "local_as": 65000,
                        "remote_as": 65001,
                        "remote_address": "10.0.0.1",
                        "local_address": "10.0.0.2",
                        "router_id": "10.0.0.1",
                        "connection_state": "Established",
                        "active_prefix_count": 100,
                        "received_prefix_count": 150,
                        "accepted_prefix_count": 100,
                        "advertised_prefix_count": 50,
                    },
                ],
            },
        }

        collector.bgp(mock_driver)

        self.assertTrue(
            IPAddress.objects.filter(address="10.0.0.1/32").exists()
        )

    def test_creates_asn_when_rir_exists(self):
        """bgp() should create ASN objects when a default RIR exists."""
        from ipam.models import IPAddress, ASN, RIR

        rir = RIR.objects.create(name="TestRIR", slug="testrir")
        device = Device.objects.create(
            name="bgp-dev2", site=self.site,
            device_type=self.device_type, role=self.role,
        )

        plan = CollectionPlan.objects.create(
            name="BGP Plan 2",
            collector_type=CollectionTypeChoices.TYPE_BGP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.get_bgp_neighbors_detail.return_value = {
            "global": {
                65001: [
                    {
                        "up": True,
                        "local_as": 65000,
                        "remote_as": 65001,
                        "remote_address": "10.1.0.1",
                        "local_address": "10.1.0.2",
                        "router_id": "10.1.0.1",
                        "connection_state": "Established",
                        "active_prefix_count": 0,
                        "received_prefix_count": 0,
                        "accepted_prefix_count": 0,
                        "advertised_prefix_count": 0,
                    },
                ],
            },
        }

        collector.bgp(mock_driver)

        self.assertTrue(ASN.objects.filter(asn=65001).exists())

    def test_vrf_awareness(self):
        """bgp() should associate peer IPs with the correct VRF."""
        from ipam.models import IPAddress
        from ipam.models.vrfs import VRF

        vrf = VRF.objects.create(name="VRF_BGP", rd="65000:100")
        device = Device.objects.create(
            name="bgp-dev3", site=self.site,
            device_type=self.device_type, role=self.role,
        )

        plan = CollectionPlan.objects.create(
            name="BGP Plan 3",
            collector_type=CollectionTypeChoices.TYPE_BGP,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.get_bgp_neighbors_detail.return_value = {
            "VRF_BGP": {
                65002: [
                    {
                        "up": True,
                        "local_as": 65000,
                        "remote_as": 65002,
                        "remote_address": "10.2.0.1",
                        "local_address": "10.2.0.2",
                        "router_id": "10.2.0.1",
                        "connection_state": "Established",
                        "active_prefix_count": 0,
                        "received_prefix_count": 0,
                        "accepted_prefix_count": 0,
                        "advertised_prefix_count": 0,
                    },
                ],
            },
        }

        collector.bgp(mock_driver)

        ip = IPAddress.objects.get(address="10.2.0.1/32")
        self.assertEqual(ip.vrf, vrf)
```

### Step 2: Implement bgp()

Add imports at top of `collector.py`:
```python
from ipam.models import ASN, RIR
from ipam.models.vrfs import VRF
```

```python
def bgp(self, driver: NetworkDriver):
    """Collect BGP neighbor data from a device."""
    bgp_data = driver.get_bgp_neighbors_detail()

    for vrf_name, peers_by_as in bgp_data.items():
        # Resolve VRF
        vrf = None
        if vrf_name and vrf_name not in ("global", "default"):
            try:
                vrf = VRF.objects.get(name=vrf_name)
            except VRF.DoesNotExist:
                self._log_warning(f"VRF `{vrf_name}` not found in NetBox.")

        for remote_as_number, peer_list in peers_by_as.items():
            # Try to find or create ASN
            asn_obj = None
            try:
                asn_obj = ASN.objects.get(asn=remote_as_number)
            except ASN.DoesNotExist:
                # Try to create with first available RIR
                default_rir = RIR.objects.first()
                if default_rir:
                    asn_obj, created = ASN.objects.get_or_create(
                        asn=remote_as_number,
                        defaults={"rir": default_rir},
                    )
                    if created:
                        self._log_success(f"Created ASN {remote_as_number}.")
                else:
                    self._log_info(
                        f"No RIR exists in NetBox. Skipping ASN {remote_as_number} creation."
                    )

            for peer_data in peer_list:
                peer_ip = peer_data.get("remote_address", "")
                if not peer_ip:
                    continue

                # Create/find peer IPAddress
                ip_obj, created = IPAddress.objects.get_or_create(
                    address=f"{peer_ip}/32",
                    vrf=vrf,
                    defaults={
                        "description": (
                            f"BGP peer AS{remote_as_number} discovered on "
                            f"{self._current_device} ({self._now.date()})"
                        ),
                    },
                )
                if created:
                    ip_obj.tags.add(AUTO_D_TAG)
                    self._log_success(
                        f"Created peer IP {get_absolute_url_markdown(ip_obj, bold=True)} "
                        f"(AS{remote_as_number})."
                    )
                else:
                    self._log_info(
                        f"Found existing peer IP {get_absolute_url_markdown(ip_obj, bold=True)}."
                    )

                # Create journal entry with BGP session details
                state = peer_data.get("connection_state", "Unknown")
                prefixes = peer_data.get("accepted_prefix_count", 0)
                JournalEntry.objects.create(
                    created=self._now,
                    assigned_object=ip_obj,
                    kind=JournalEntryKindChoices.KIND_INFO,
                    comments=(
                        f"BGP session from {get_absolute_url_markdown(self._current_device, bold=True)}: "
                        f"State: `{state}`, AS: `{remote_as_number}`, "
                        f"Accepted prefixes: `{prefixes}`"
                        + (f", VRF: `{vrf_name}`" if vrf else "")
                    ),
                )

                # Conditional netbox-routing integration (see Task 11)
                self._bgp_routing_integration(peer_data, ip_obj, asn_obj, vrf)

    self._log_success("BGP collection completed")

def _bgp_routing_integration(self, peer_data, ip_obj, asn_obj, vrf):
    """Hook for netbox-routing BGP integration. Implemented in Task 11."""
    pass
```

### Step 3: Run tests and commit

```bash
make test
git add netbox_facts/helpers/collector.py netbox_facts/tests/test_helpers.py
git commit -m "Implement bgp() collector with ASN and VRF support"
```

---

## Task 9: Vendor Dispatch Framework + l2_circuits() Junos

**Files:**
- Modify: `netbox_facts/helpers/collector.py`
- Modify: `netbox_facts/napalm/junos.py`
- Modify: `netbox_facts/napalm/utils/junos_views.py`
- Modify: `netbox_facts/tests/test_helpers.py`

### Step 1: Write tests for vendor dispatch + l2_circuits

Add to `netbox_facts/tests/test_helpers.py`:

```python
class VendorDispatchTest(TestCase):
    """Tests for the vendor-specific dispatch mechanism."""

    def _make_collector(self, driver_name="junos"):
        plan = MagicMock()
        plan.napalm_driver = driver_name
        plan.collector_type = "l2_circuits"

        with patch.object(NapalmCollector, "__init__", lambda self, p: None):
            collector = NapalmCollector.__new__(NapalmCollector)
        collector.plan = plan
        collector._collector_type = "l2_circuits"
        collector._now = timezone.now()
        collector._current_device = None
        collector._log_prefix = ""
        return collector

    def test_dispatch_junos(self):
        """_get_vendor_method() should return Junos implementation for junos driver."""
        collector = self._make_collector("junos")
        method = collector._get_vendor_method("l2_circuits")
        self.assertTrue(callable(method))

    def test_dispatch_enhanced_junos(self):
        """_get_vendor_method() should work for netbox_facts.napalm.junos driver."""
        collector = self._make_collector("netbox_facts.napalm.junos")
        method = collector._get_vendor_method("l2_circuits")
        self.assertTrue(callable(method))

    def test_dispatch_unsupported_driver(self):
        """_get_vendor_method() should raise NotImplementedError for unsupported drivers."""
        collector = self._make_collector("eos")
        with self.assertRaises(NotImplementedError):
            collector._get_vendor_method("l2_circuits")


class L2CircuitsCollectorTest(TestCase):
    """Tests for the l2_circuits() Junos collector."""

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="L2C Site", slug="l2c-site")
        cls.manufacturer = Manufacturer.objects.create(name="L2CMfg", slug="l2cmfg")
        cls.device_type = DeviceType.objects.create(
            manufacturer=cls.manufacturer, model="L2CModel", slug="l2cmodel"
        )
        cls.role = DeviceRole.objects.create(name="L2CRole", slug="l2crole")

    def _make_collector(self, plan):
        with patch.object(NapalmCollector, "__init__", lambda self, p: None):
            collector = NapalmCollector.__new__(NapalmCollector)
        collector.plan = plan
        collector._collector_type = plan.collector_type
        collector._napalm_args = {}
        collector._napalm_driver = None
        collector._napalm_username = "test"
        collector._napalm_password = "test"
        import re
        collector._interfaces_re = re.compile(r".*")
        collector._devices = []
        collector._current_device = None
        collector._log_prefix = ""
        collector._now = timezone.now()
        return collector

    def test_l2_circuits_creates_journal_entry(self):
        """l2_circuits() should create journal entries for discovered circuits."""
        device = Device.objects.create(
            name="l2c-dev1", site=self.site,
            device_type=self.device_type, role=self.role,
        )
        plan = CollectionPlan.objects.create(
            name="L2C Plan",
            collector_type=CollectionTypeChoices.TYPE_L2CIRCTUITS,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        # Mock the Junos L2 circuit data structure
        mock_driver.cli.return_value = {
            "show l2circuit connections": (
                "Legend for connection status (active = Up)\n"
                "Neighbor: 10.0.0.1\n"
                "  Interface: ge-0/0/0.100\n"
                "  Type: rmt  Status: Up  Circuit-ID: 100\n"
            )
        }

        collector.l2_circuits(mock_driver)

        entries = JournalEntry.objects.filter(assigned_object_id=device.pk)
        self.assertTrue(entries.exists())
```

### Step 2: Implement vendor dispatch + l2_circuits

Add to `netbox_facts/helpers/collector.py`:

```python
def _get_vendor_method(self, method_name):
    """
    Get vendor-specific implementation based on NAPALM driver.

    To add support for a new vendor:
    1. Implement a method named _{method_name}_{vendor}(self, driver)
    2. Add the driver name to the vendor_map below

    Example for adding EOS support for l2_circuits:
        def _l2_circuits_eos(self, driver):
            ...
        # Then add to vendor_map: 'eos': f'_{method_name}_eos'
    """
    vendor_map = {
        "junos": f"_{method_name}_junos",
        "netbox_facts.napalm.junos": f"_{method_name}_junos",
    }
    driver_name = self.plan.napalm_driver
    impl_name = vendor_map.get(driver_name)
    if impl_name and hasattr(self, impl_name):
        return getattr(self, impl_name)
    supported = [k for k, v in vendor_map.items() if hasattr(self, v)]
    raise NotImplementedError(
        f"{method_name} is not implemented for driver '{driver_name}'. "
        f"Supported drivers: {supported}"
    )

def l2_circuits(self, driver: NetworkDriver):
    """Collect L2 circuit data. Dispatches to vendor-specific implementation."""
    impl = self._get_vendor_method("l2_circuits")
    impl(driver)

def _l2_circuits_junos(self, driver):
    """Junos L2 circuit collection via CLI."""
    try:
        output = driver.cli(["show l2circuit connections"])
        raw = output.get("show l2circuit connections", "")
    except Exception as exc:
        self._log_failure(f"Failed to retrieve L2 circuit data: {exc}")
        return

    if not raw.strip():
        self._log_info("No L2 circuit data found.")
        return

    # Parse the L2 circuit output and create journal entry
    JournalEntry.objects.create(
        created=self._now,
        assigned_object=self._current_device,
        kind=JournalEntryKindChoices.KIND_INFO,
        comments=f"L2 circuit data collected:\n```\n{raw[:2000]}\n```",
    )
    self._log_success("L2 circuit collection completed")
```

### Step 3: Run tests and commit

```bash
make test
git add netbox_facts/helpers/collector.py netbox_facts/tests/test_helpers.py
git commit -m "Add vendor dispatch framework and l2_circuits() Junos collector"
```

---

## Task 10: Implement evpn() Junos Collector

**Files:**
- Modify: `netbox_facts/helpers/collector.py`
- Modify: `netbox_facts/tests/test_helpers.py`

### Step 1: Write failing tests

```python
class EVPNCollectorTest(TestCase):
    """Tests for the evpn() Junos collector."""

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="EVPN Site", slug="evpn-site")
        cls.manufacturer = Manufacturer.objects.create(name="EVPNMfg", slug="evpnmfg")
        cls.device_type = DeviceType.objects.create(
            manufacturer=cls.manufacturer, model="EVPNModel", slug="evpnmodel"
        )
        cls.role = DeviceRole.objects.create(name="EVPNRole", slug="evpnrole")

    def _make_collector(self, plan):
        with patch.object(NapalmCollector, "__init__", lambda self, p: None):
            collector = NapalmCollector.__new__(NapalmCollector)
        collector.plan = plan
        collector._collector_type = plan.collector_type
        collector._napalm_args = {}
        collector._napalm_driver = None
        collector._napalm_username = "test"
        collector._napalm_password = "test"
        import re
        collector._interfaces_re = re.compile(r".*")
        collector._devices = []
        collector._current_device = None
        collector._log_prefix = ""
        collector._now = timezone.now()
        return collector

    def test_evpn_creates_mac_with_evpn_discovery(self):
        """evpn() should create MACAddress objects with discovery_method='evpn'."""
        from netbox_facts.models import MACAddress

        device = Device.objects.create(
            name="evpn-dev1", site=self.site,
            device_type=self.device_type, role=self.role,
        )
        plan = CollectionPlan.objects.create(
            name="EVPN Plan",
            collector_type=CollectionTypeChoices.TYPE_EVPN,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.cli.return_value = {
            "show evpn mac-table": (
                "MAC address      Logical interface   NH Index  Flags\n"
                "AA:BB:CC:DD:11:22  vtep.32769         0         D\n"
                "AA:BB:CC:DD:33:44  vtep.32769         0         D\n"
            )
        }

        collector.evpn(mock_driver)

        # Should create MAC entries
        self.assertTrue(MACAddress.objects.filter(mac_address="AA:BB:CC:DD:11:22").exists())
        mac = MACAddress.objects.get(mac_address="AA:BB:CC:DD:11:22")
        self.assertEqual(mac.discovery_method, CollectionTypeChoices.TYPE_EVPN)

    def test_evpn_unsupported_driver(self):
        """evpn() should raise NotImplementedError for non-Junos drivers."""
        plan = MagicMock()
        plan.napalm_driver = "eos"
        plan.collector_type = CollectionTypeChoices.TYPE_EVPN

        with patch.object(NapalmCollector, "__init__", lambda self, p: None):
            collector = NapalmCollector.__new__(NapalmCollector)
        collector.plan = plan
        collector._collector_type = CollectionTypeChoices.TYPE_EVPN
        collector._now = timezone.now()
        collector._current_device = None
        collector._log_prefix = ""

        with self.assertRaises(NotImplementedError):
            collector.evpn(MagicMock())
```

### Step 2: Implement evpn()

```python
def evpn(self, driver: NetworkDriver):
    """Collect EVPN data. Dispatches to vendor-specific implementation."""
    impl = self._get_vendor_method("evpn")
    impl(driver)

def _evpn_junos(self, driver):
    """Junos EVPN collection via CLI."""
    try:
        output = driver.cli(["show evpn mac-table"])
        raw = output.get("show evpn mac-table", "")
    except Exception as exc:
        self._log_failure(f"Failed to retrieve EVPN data: {exc}")
        return

    if not raw.strip():
        self._log_info("No EVPN data found.")
        return

    # Parse MAC addresses from EVPN table
    import re as _re
    mac_pattern = _re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")
    for line in raw.strip().split("\n"):
        match = mac_pattern.search(line)
        if match:
            mac_str = match.group(1)
            netbox_mac, created = MACAddress.objects.get_or_create(
                mac_address=mac_str
            )
            netbox_mac.discovery_method = CollectionTypeChoices.TYPE_EVPN
            netbox_mac.last_seen = self._now
            netbox_mac.save()

            if created:
                netbox_mac.tags.add(AUTO_D_TAG)
                self._log_success(
                    f"Created EVPN MAC {get_absolute_url_markdown(netbox_mac, bold=True)}."
                )

    # Create journal entry with raw data
    JournalEntry.objects.create(
        created=self._now,
        assigned_object=self._current_device,
        kind=JournalEntryKindChoices.KIND_INFO,
        comments=f"EVPN data collected:\n```\n{raw[:2000]}\n```",
    )
    self._log_success("EVPN collection completed")
```

### Step 3: Run tests and commit

```bash
make test
git add netbox_facts/helpers/collector.py netbox_facts/tests/test_helpers.py
git commit -m "Implement evpn() Junos collector"
```

---

## Task 11: Implement ospf() Junos Collector

**Files:**
- Modify: `netbox_facts/helpers/collector.py`
- Modify: `netbox_facts/tests/test_helpers.py`

### Step 1: Write failing tests

```python
class OSPFCollectorTest(TestCase):
    """Tests for the ospf() Junos collector."""

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="OSPF Site", slug="ospf-site")
        cls.manufacturer = Manufacturer.objects.create(name="OSPFMfg", slug="ospfmfg")
        cls.device_type = DeviceType.objects.create(
            manufacturer=cls.manufacturer, model="OSPFModel", slug="ospfmodel"
        )
        cls.role = DeviceRole.objects.create(name="OSPFRole", slug="ospfrole")

    def _make_collector(self, plan):
        with patch.object(NapalmCollector, "__init__", lambda self, p: None):
            collector = NapalmCollector.__new__(NapalmCollector)
        collector.plan = plan
        collector._collector_type = plan.collector_type
        collector._napalm_args = {}
        collector._napalm_driver = None
        collector._napalm_username = "test"
        collector._napalm_password = "test"
        import re
        collector._interfaces_re = re.compile(r".*")
        collector._devices = []
        collector._current_device = None
        collector._log_prefix = ""
        collector._now = timezone.now()
        return collector

    def test_ospf_creates_journal_entry(self):
        """ospf() should create journal entries for discovered neighbors."""
        device = Device.objects.create(
            name="ospf-dev1", site=self.site,
            device_type=self.device_type, role=self.role,
        )
        plan = CollectionPlan.objects.create(
            name="OSPF Plan",
            collector_type=CollectionTypeChoices.TYPE_OSPF,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.cli.return_value = {
            "show ospf neighbor": (
                "Address          Interface              State     ID               Pri  Dead\n"
                "10.0.0.1         ge-0/0/0.0             Full      10.0.0.1         128    35\n"
                "10.0.0.2         ge-0/0/1.0             Full      10.0.0.2         128    33\n"
            )
        }

        collector.ospf(mock_driver)

        entries = JournalEntry.objects.filter(assigned_object_id=device.pk)
        self.assertTrue(entries.exists())

    def test_ospf_creates_peer_ips(self):
        """ospf() should create IPAddress objects for OSPF neighbors."""
        from ipam.models import IPAddress

        device = Device.objects.create(
            name="ospf-dev2", site=self.site,
            device_type=self.device_type, role=self.role,
        )
        plan = CollectionPlan.objects.create(
            name="OSPF Plan 2",
            collector_type=CollectionTypeChoices.TYPE_OSPF,
            napalm_driver="junos",
            device_status=[DeviceStatusChoices.STATUS_ACTIVE],
        )
        collector = self._make_collector(plan)
        collector._current_device = device

        mock_driver = MagicMock()
        mock_driver.cli.return_value = {
            "show ospf neighbor": (
                "Address          Interface              State     ID               Pri  Dead\n"
                "10.0.0.1         ge-0/0/0.0             Full      10.0.0.1         128    35\n"
            )
        }

        collector.ospf(mock_driver)

        self.assertTrue(IPAddress.objects.filter(address="10.0.0.1/32").exists())

    def test_ospf_unsupported_driver(self):
        """ospf() should raise NotImplementedError for non-Junos drivers."""
        plan = MagicMock()
        plan.napalm_driver = "eos"
        plan.collector_type = CollectionTypeChoices.TYPE_OSPF

        with patch.object(NapalmCollector, "__init__", lambda self, p: None):
            collector = NapalmCollector.__new__(NapalmCollector)
        collector.plan = plan
        collector._collector_type = CollectionTypeChoices.TYPE_OSPF
        collector._now = timezone.now()
        collector._current_device = None
        collector._log_prefix = ""

        with self.assertRaises(NotImplementedError):
            collector.ospf(MagicMock())
```

### Step 2: Implement ospf()

```python
def ospf(self, driver: NetworkDriver):
    """Collect OSPF data. Dispatches to vendor-specific implementation."""
    impl = self._get_vendor_method("ospf")
    impl(driver)

def _ospf_junos(self, driver):
    """Junos OSPF collection via CLI."""
    try:
        output = driver.cli(["show ospf neighbor"])
        raw = output.get("show ospf neighbor", "")
    except Exception as exc:
        self._log_failure(f"Failed to retrieve OSPF data: {exc}")
        return

    if not raw.strip():
        self._log_info("No OSPF neighbor data found.")
        return

    # Parse OSPF neighbor IPs
    import re as _re
    ip_pattern = _re.compile(r"^(\d+\.\d+\.\d+\.\d+)\s+(\S+)\s+(\S+)\s+(\d+\.\d+\.\d+\.\d+)", _re.MULTILINE)
    neighbors = []
    for match in ip_pattern.finditer(raw):
        neighbor_ip = match.group(1)
        iface_name = match.group(2)
        state = match.group(3)
        router_id = match.group(4)
        neighbors.append({
            "address": neighbor_ip,
            "interface": iface_name,
            "state": state,
            "router_id": router_id,
        })

        # Create/find neighbor IP
        ip_obj, created = IPAddress.objects.get_or_create(
            address=f"{neighbor_ip}/32",
            defaults={
                "description": (
                    f"OSPF neighbor (Router ID: {router_id}) discovered on "
                    f"{self._current_device} ({self._now.date()})"
                ),
            },
        )
        if created:
            ip_obj.tags.add(AUTO_D_TAG)
            self._log_success(
                f"Created OSPF neighbor IP {get_absolute_url_markdown(ip_obj, bold=True)} "
                f"(Router ID: {router_id})."
            )

        # Conditional netbox-routing integration (see Task 12)
        self._ospf_routing_integration(ip_obj, neighbors[-1])

    # Create journal entry
    if neighbors:
        neighbor_lines = "\n".join(
            f"- `{n['address']}` on `{n['interface']}` (State: {n['state']}, "
            f"Router ID: {n['router_id']})"
            for n in neighbors
        )
        JournalEntry.objects.create(
            created=self._now,
            assigned_object=self._current_device,
            kind=JournalEntryKindChoices.KIND_INFO,
            comments=f"OSPF neighbors discovered:\n{neighbor_lines}",
        )

    self._log_success("OSPF collection completed")

def _ospf_routing_integration(self, ip_obj, neighbor_data):
    """Hook for netbox-routing OSPF integration. Implemented in Task 12."""
    pass
```

### Step 3: Run tests and commit

```bash
make test
git add netbox_facts/helpers/collector.py netbox_facts/tests/test_helpers.py
git commit -m "Implement ospf() Junos collector"
```

---

## Task 12: Add netbox-routing Integration

**Files:**
- Modify: `netbox_facts/helpers/collector.py`
- Modify: `.devcontainer/Dockerfile-plugin_dev` (or equivalent)
- Modify: `netbox_facts/tests/test_helpers.py`

### Step 1: Add netbox-routing to devcontainer

In the Dockerfile or setup, add:
```bash
uv pip install netbox-routing
```

Also add to `pyproject.toml` optional dependencies:
```toml
[project.optional-dependencies]
routing = ["netbox-routing"]
dev = ["netbox-routing", ...]  # add to existing dev deps
```

### Step 2: Write tests for conditional integration

Add to `netbox_facts/tests/test_helpers.py`:

```python
class NetboxRoutingIntegrationTest(TestCase):
    """Tests for conditional netbox-routing integration."""

    def test_bgp_integration_when_available(self):
        """When netbox-routing is installed, BGP integration should attempt to use it."""
        try:
            from netbox_routing.models import BGPPeer
            routing_available = True
        except ImportError:
            routing_available = False

        # This test verifies the detection mechanism works
        from netbox_facts.helpers.collector import _has_netbox_routing
        self.assertEqual(_has_netbox_routing(), routing_available)

    def test_bgp_integration_fallback(self):
        """BGP collector should work without netbox-routing."""
        # The bgp() method from Task 8 should work regardless
        # This is already covered by BGPCollectorTest
        pass
```

### Step 3: Implement netbox-routing detection and integration hooks

Add at top of `netbox_facts/helpers/collector.py`:

```python
def _has_netbox_routing():
    """Check if netbox-routing plugin is installed."""
    try:
        import netbox_routing  # noqa: F401
        return True
    except ImportError:
        return False
```

Replace the `_bgp_routing_integration` stub:

```python
def _bgp_routing_integration(self, peer_data, ip_obj, asn_obj, vrf):
    """Create/update BGP session in netbox-routing if available."""
    if not _has_netbox_routing():
        return

    try:
        from netbox_routing.models import BGPPeer, BGPRouter, BGPScope

        # Find or create BGP router for local device
        router = BGPRouter.objects.filter(
            assigned_object_id=self._current_device.pk,
        ).first()
        if not router:
            self._log_info(
                f"No BGPRouter found for {self._current_device} in netbox-routing. "
                f"Skipping BGP session creation."
            )
            return

        # Find or create scope (VRF context)
        scope = BGPScope.objects.filter(router=router, vrf=vrf).first()
        if not scope:
            self._log_info(
                f"No BGPScope found for router {router} "
                + (f"in VRF {vrf}" if vrf else "in global table")
                + ". Skipping BGP session creation."
            )
            return

        remote_as = peer_data.get("remote_as")
        # Check if peer already exists
        existing = BGPPeer.objects.filter(
            scope=scope,
            peer=ip_obj,
        ).first()
        if existing:
            self._log_info(
                f"BGP peer already exists in netbox-routing for {ip_obj}."
            )
            return

        peer = BGPPeer.objects.create(
            name=f"AS{remote_as} - {ip_obj}",
            scope=scope,
            peer=ip_obj,
            remote_as=remote_as,
            enabled=peer_data.get("up", False),
        )
        self._log_success(
            f"Created BGP peer in netbox-routing: {peer.name}"
        )

    except Exception as exc:
        self._log_warning(
            f"netbox-routing BGP integration error: {exc}"
        )
```

Replace the `_ospf_routing_integration` stub:

```python
def _ospf_routing_integration(self, ip_obj, neighbor_data):
    """Create/update OSPF data in netbox-routing if available."""
    if not _has_netbox_routing():
        return

    try:
        from netbox_routing.models import OSPFInstance

        # Just log that we found OSPF neighbor — full integration
        # requires OSPFInstance to be pre-configured in netbox-routing
        instance = OSPFInstance.objects.filter(
            device=self._current_device,
        ).first()
        if instance:
            self._log_info(
                f"Found OSPF instance `{instance}` for {self._current_device} "
                f"in netbox-routing. Neighbor: {neighbor_data['address']} "
                f"(State: {neighbor_data['state']})"
            )

    except Exception as exc:
        self._log_warning(
            f"netbox-routing OSPF integration error: {exc}"
        )
```

### Step 4: Run tests and commit

```bash
make test
git add netbox_facts/helpers/collector.py netbox_facts/tests/test_helpers.py .devcontainer/ pyproject.toml
git commit -m "Add conditional netbox-routing integration for BGP and OSPF"
```

---

## Task 13: Final Integration Test + Cleanup

**Files:**
- All modified files from previous tasks
- Run full test suite

### Step 1: Run the full test suite

```bash
make test
```

Verify all tests pass and migration check is clean.

### Step 2: Verify the collector dispatch in execute()

Check that `execute()` in `collector.py` correctly dispatches to all new methods. The existing `getattr(self, self._collector_type)(driver)` pattern should work because:
- `self._collector_type` is one of: `arp`, `ndp`, `inventory`, `interfaces`, `lldp`, `ethernet_switching`, `l2_circuits`, `evpn`, `bgp`, `ospf`
- Each of these is now a method on `NapalmCollector`

### Step 3: Verify signal and JobRunner import chain

Check that `__init__.py` imports `signals` (it does — line 31-33), and that the signal handler correctly imports `CollectionJobRunner` at call time (lazy import to avoid circular dependency).

### Step 4: Final commit if any cleanup needed

```bash
make test
git add -A
git commit -m "Final integration cleanup for Phase 2+3"
```

---

## Summary of Implementation Order

| Task | Description | Key Files |
|------|-------------|-----------|
| 1 | Add OSPF choice + migration | `choices.py`, migration |
| 2 | JobRunner refactor | `jobs.py`, `collection_plan.py`, `test_jobs.py` |
| 3 | Auto-scheduling signal | `signals.py`, `test_signals.py` |
| 4 | inventory() collector | `collector.py`, `test_helpers.py` |
| 5 | interfaces() collector | `collector.py`, `test_helpers.py` |
| 6 | ethernet_switching() | `collector.py`, `test_helpers.py` |
| 7 | lldp() collector | `collector.py`, `test_helpers.py` |
| 8 | bgp() collector | `collector.py`, `test_helpers.py` |
| 9 | Vendor dispatch + l2_circuits | `collector.py`, `test_helpers.py` |
| 10 | evpn() Junos | `collector.py`, `test_helpers.py` |
| 11 | ospf() Junos | `collector.py`, `test_helpers.py` |
| 12 | netbox-routing integration | `collector.py`, devcontainer, `test_helpers.py` |
| 13 | Final integration test | All |

# Design: Phase 2 + Phase 3 — Auto-Scheduling, Standard & Vendor-Specific Collectors

**Date:** 2026-02-26
**Status:** Approved
**Scope:** Phase 2 (auto-scheduling + standard NAPALM collectors) and Phase 3 (vendor-specific collectors)

---

## Context

Phase 1 is complete: runtime bugs fixed, UI forms/filters wired, tests added, dead code removed (commit `e531adc`). This design covers the remaining work to bring NetBox Facts to full functionality.

## Implementation Approach

**Bottom-Up:** Build in dependency order — jobs refactor first, then auto-scheduling, then standard collectors (simple to complex), then vendor-specific collectors. Tests accompany each piece.

---

## 1. Jobs Refactor + Auto-Scheduling

### 1.1 Adopt NetBox's JobRunner Pattern

**Problem:** The plugin uses a custom `CollectionPlan.enqueue()` classmethod that duplicates NetBox's `Job.enqueue()` without `transaction.on_commit()` safety or duplicate prevention. The `enqueue_collection_job(request)` method requires a request object, preventing signal-based scheduling.

**Solution:** Create a `CollectionJobRunner(JobRunner)` subclass following the `SyncDataSourceJob` pattern from `core/jobs.py`.

**Files changed:**
- `netbox_facts/jobs.py` — Replace raw `collection_job()` function with `CollectionJobRunner` class
- `netbox_facts/models/collection_plan.py` — Remove custom `enqueue()` classmethod, refactor `enqueue_collection_job()` to use `CollectionJobRunner.enqueue()`, make `run()` work without a request

**CollectionJobRunner design:**
- Override `enqueue()` to pass `queue_name=instance.priority` (preserving per-plan queue selection)
- Override `enqueue()` to update CollectionPlan status to QUEUED
- `run()` calls `CollectionPlan.run(request=None)` for signal path, `CollectionPlan.run(request=request)` for manual trigger
- Auto-rescheduling handled by `JobRunner.handle()` when `interval` is set

### 1.2 Auto-Scheduling Signal

**Pattern:** Mirrors `core/signals.py:enqueue_sync_job` for DataSource.

**`netbox_facts/signals.py` — `handle_collection_job_change`:**
- On post_save of CollectionPlan:
  - If `enabled=True` and `interval` is set: call `CollectionJobRunner.enqueue_once(instance, interval, user=instance.run_as, queue_name=instance.priority)`
  - If `enabled=False` or interval cleared (and not a new instance): delete pending scheduled jobs
- `enqueue_once()` provides advisory locking to prevent duplicate schedules

### 1.3 CollectionPlan.run() Refactor

Make `request` parameter optional. When called from signal/scheduled path (no request), skip `event_tracking` context manager or use a minimal context. The `event_tracking` context is only needed when the run is triggered by a user action with a real HTTP request.

---

## 2. Standard NAPALM Collectors

All methods in `netbox_facts/helpers/collector.py`, following the existing `_ip_neighbors()` / `arp()` / `ndp()` pattern. Each receives a `driver: NetworkDriver` argument.

### 2.1 `inventory(driver)` — `driver.get_facts()`

- Collects: hostname, vendor, model, serial, OS version, interface list
- Updates: Device.serial if changed
- Creates: JournalEntry documenting any changes detected
- Sets `discovery_method='inventory'` on any created objects

### 2.2 `interfaces(driver)` — `driver.get_interfaces()` + `driver.get_interfaces_ip()`

- Collects: interface status, speed, MTU, MAC, description, IPs
- Creates: MACAddress for each interface's hardware MAC
- Sets: `MACAddress.device_interface` FK to link MAC to its owning interface
- Sets: `discovery_method='interfaces'`, `last_seen=now`
- Updates: Interface description/MTU if different from NetBox (with journal entry)

### 2.3 `ethernet_switching(driver)` — `driver.get_mac_address_table()`

- Collects: learned MAC addresses per interface/VLAN
- Creates: MACAddress objects with interface M2M associations
- L2-only — no IP correlation
- Sets: `discovery_method='ethernet_switching'`, `last_seen=now`
- Handles: static vs dynamic entries, filters by `_interfaces_re`

### 2.4 `lldp(driver)` — `driver.get_lldp_neighbors_detail()`

- Collects: remote chassis ID, system name, port, description
- Cable creation constraints:
  - Both local and remote devices must exist in NetBox
  - Both must be in the **same Site** (no cross-site cables)
  - Both interfaces must exist and not already be cabled
  - If constraints not met: log info/warning and skip
- Tags created cables with "Automatically Discovered"
- Creates: JournalEntry on cable creation

### 2.5 `bgp(driver)` — `driver.get_bgp_neighbors_detail()`

- Collects: BGP peer state, AS numbers, prefix counts, per-VRF
- Creates/finds: ASN objects (`ipam.ASN`) for remote AS numbers
- Creates/finds: IPAddress for peer IPs (with VRF awareness)
- Tags peer IPs with "Automatically Discovered"
- **Conditional `netbox-routing` integration:**
  - At import time, check if `netbox_routing` is importable
  - If available: create/update BGP session objects linking local device to peer
  - If unavailable: graceful fallback — only create IP/ASN objects, log info message

---

## 3. Vendor-Specific Collectors (Junos Only)

### 3.1 Extensibility Pattern

Registry-style dispatch in collector:

```python
def _get_vendor_method(self, method_name):
    vendor_map = {
        'junos': f'_{method_name}_junos',
        'netbox_facts.napalm.junos': f'_{method_name}_junos',
    }
    driver_name = self.plan.napalm_driver
    impl_name = vendor_map.get(driver_name)
    if impl_name and hasattr(self, impl_name):
        return getattr(self, impl_name)
    raise NotImplementedError(
        f"{method_name} is not implemented for driver '{driver_name}'. "
        f"Supported drivers: {list(vendor_map.keys())}"
    )
```

To add a new vendor: implement `_{method}_{vendor}()` and add to `vendor_map`.

### 3.2 `l2_circuits(driver)`

- Dispatches to `_l2_circuits_junos(driver)`
- Junos: Uses NETCONF tables to parse L2 circuit connections
- Records: circuit ID, remote PE, interface, status
- Creates: JournalEntry documenting L2 circuit state

### 3.3 `evpn(driver)`

- Dispatches to `_evpn_junos(driver)`
- Junos: Parses EVPN instance data via NETCONF
- Records: instance name, route targets, VLAN-to-VNI mappings
- Creates: MACAddress objects for learned EVPN MACs with `discovery_method='evpn'`

### 3.4 `ospf(driver)`

- Dispatches to `_ospf_junos(driver)`
- Junos: Parses OSPF neighbor adjacencies via NETCONF tables
- Records: neighbor ID, state, interface, area
- Conditional `netbox-routing` integration for OSPF neighbor data

### 3.5 EnhancedJunOSDriver Changes

- `netbox_facts/napalm/junos.py` — Add methods for L2 circuit, EVPN, OSPF data retrieval
- `netbox_facts/napalm/utils/junos_views.py` — Add NETCONF table definitions for new data types

---

## 4. Testing Strategy

### New Test Files
- `netbox_facts/tests/test_jobs.py` — CollectionJobRunner tests
- `netbox_facts/tests/test_signals.py` — Auto-scheduling signal tests

### Expanded Test Files
- `netbox_facts/tests/test_helpers.py` — Tests for each collector method

### Test Approach

**Collectors:** Mock NAPALM driver returning known data structures. Verify correct NetBox objects created/updated. Test edge cases (missing interfaces, empty tables, unreachable devices).

**Scheduling:** Verify `enqueue_once()` called on save with `enabled=True` + `interval`. Verify jobs deleted on `enabled=False`. Verify duplicate prevention.

**LLDP:** Same-site cable creation works. Cross-site skipped with log. Missing device/interface handled gracefully.

**BGP with netbox-routing:** Mock as installed → verify session creation. Mock as absent → verify IP/ASN-only fallback.

**Vendor-specific:** Mock NETCONF responses. Verify NotImplementedError for unsupported drivers.

---

## 5. Files Modified Summary

| File | Changes |
|------|---------|
| `netbox_facts/jobs.py` | Replace raw function with `CollectionJobRunner(JobRunner)` |
| `netbox_facts/signals.py` | Implement `handle_collection_job_change` with `enqueue_once()` |
| `netbox_facts/models/collection_plan.py` | Remove custom `enqueue()`, refactor `enqueue_collection_job()`, make `run()` request-optional |
| `netbox_facts/helpers/collector.py` | Implement all 8 collector methods + vendor dispatch |
| `netbox_facts/napalm/junos.py` | Add L2 circuit, EVPN, OSPF NETCONF methods |
| `netbox_facts/napalm/utils/junos_views.py` | Add NETCONF table definitions |
| `netbox_facts/choices.py` | Add OSPF to CollectionTypeChoices if missing |
| `netbox_facts/tests/test_jobs.py` | New — JobRunner tests |
| `netbox_facts/tests/test_signals.py` | New — Scheduling signal tests |
| `netbox_facts/tests/test_helpers.py` | Expand — Collector method tests |
| `.devcontainer/` | Add `netbox-routing` as dev dependency |

## 6. Implementation Order

1. Jobs refactor (JobRunner subclass)
2. Auto-scheduling signal
3. `inventory()` collector
4. `interfaces()` collector
5. `ethernet_switching()` collector
6. `lldp()` collector
7. `bgp()` collector + netbox-routing integration
8. Vendor dispatch framework
9. `l2_circuits()` Junos implementation
10. `evpn()` Junos implementation
11. `ospf()` Junos implementation + netbox-routing integration
12. Tests for all of the above

## Design Decisions

- **JobRunner over custom enqueue:** NetBox 4.x `Job.enqueue()` supports `queue_name`, eliminating the need for the plugin's custom implementation
- **LLDP same-site only:** Cables only created between devices in the same Site to avoid incorrect cross-location topology
- **LLDP no device creation:** Does not create Device objects for unknown LLDP neighbors
- **BGP netbox-routing conditional:** Runtime import check; graceful fallback if plugin not installed
- **Vendor dispatch registry:** Simple dict-based dispatch, extensible by adding methods + map entries
- **Junos only for Phase 3:** Clear extension points documented for adding other vendors

# Collectors Overview

Each `CollectionPlan` runs exactly one collector. The full set is defined
in `CollectionTypeChoices` (`netbox_facts/choices.py`):

| Type key | Display | NAPALM call(s) | Vendor-specific dispatch? |
|---|---|---|---|
| `arp` | ARP | `get_arp_table()` | No (Junos driver enriched) |
| `ndp` | IPv6 Neighbor Discovery | `get_ipv6_neighbors_table()` | No (Junos driver enriched) |
| `inventory` | Inventory | `get_facts()` + (Junos) `get_chassis_inventory()` | Junos has chassis path |
| `interfaces` | Interfaces | `get_interfaces()` + `get_interfaces_ip()` + `get_network_instances()` | Enhanced Junos data unlocks the logical-interfaces path |
| `lldp` | LLDP | `get_lldp_neighbors_detail()` | No |
| `ethernet_switching` | Ethernet Switching Tables | `get_mac_address_table()` | No |
| `l2_circuits` | L2 Circuits | CLI: `show l2circuit connections` | Yes (Junos only) |
| `evpn` | EVPN | CLI: `show evpn mac-table` | Yes (Junos only) |
| `bgp` | BGP | `get_bgp_neighbors_detail()` | No |
| `ospf` | OSPF | CLI: `show ospf neighbor` | Yes (Junos only) |

The runner is `NapalmCollector` in `netbox_facts/helpers/collector.py`.
Each collector method matches the type key (e.g. `arp()`,
`ethernet_switching()`).

## Driver compatibility

The collectors marked "Yes (Junos only)" above reach the device through a
vendor-specific implementation rather than a standard NAPALM getter, so
they can only run against a driver that has one. That table is encoded in
`COLLECTOR_SUPPORTED_DRIVERS` (`netbox_facts/choices.py`) and enforced
twice:

- **At save time.** A plan that names an incompatible driver fails
  validation on the `napalm_driver` field. An `evpn` plan cannot be saved
  with `ios`.
- **At run time.** A plan that leaves `napalm_driver` blank resolves a
  driver per device, so compatibility is not knowable until the run. Each
  device whose resolved driver has no implementation is skipped with a
  warning and counted in the run summary; the rest of the scope is still
  collected. This is how a Junos-only collector behaves on a mixed-vendor
  scope: it collects from the Junos devices and passes over the others.

A driver named by its plugin-local dotted path
(`netbox_facts.napalm.junos`) is treated as the vendor it enhances
(`junos`) by both checks and by the vendor dispatch itself.

Adding a vendor to one of these collectors means implementing
`_<collector>_<vendor>()` and adding the driver to that collector's row in
`COLLECTOR_SUPPORTED_DRIVERS`. `_get_vendor_method()` dispatches off that
same table, so there is no second list to keep in step.

## Detect-only and apply

Every collector follows the same pattern:

1. Compare the device-reported value with NetBox state.
2. Call `_record_entry()` to create a `FactsReportEntry` with one of
   `new`, `changed`, `confirmed`, or `stale`.
3. If `_should_apply()` returns `True` (i.e. `detect_only=False`),
   perform the mutation and call `_mark_entry_applied()`.

A second apply path (`netbox_facts/helpers/applier.py`) re-implements the
same handlers in a per-entry-savepoint form so reviewers can selectively
apply pending entries from a detect-only report. The dispatch table is
`APPLY_HANDLERS`.

## Auto-discovered tag

Objects created by collectors are tagged
**Automatically Discovered** (constant: `AUTO_D_TAG`). Stale detection
relies on this tag, so manually-added objects are never considered stale.

## Interface filter

The plugin-wide `valid_interfaces_re` setting filters which interfaces a
collector iterates. It is compiled once per run and applied to:

- ARP / NDP entries grouped by interface.
- Interface entries from `get_interfaces()`.
- Ethernet switching MAC entries.

Interfaces whose names do not match are silently skipped.

## Per-collector pages

- [ARP and NDP](arp-ndp.md)
- [Inventory](inventory.md)
- [Interfaces](interfaces.md)
- [LLDP](lldp.md)
- [Ethernet Switching](ethernet-switching.md)
- [EVPN](evpn.md)
- [L2 Circuits](l2-circuits.md)
- [BGP](bgp.md)
- [OSPF](ospf.md)

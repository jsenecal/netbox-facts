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

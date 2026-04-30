# ARP and IPv6 Neighbor Discovery

The `arp` and `ndp` collectors share the same logic
(`NapalmCollector._ip_neighbors`); they differ only in the NAPALM call
used and the IP family they consider.

## NAPALM calls

| Collector | Call |
|---|---|
| `arp` | `driver.get_arp_table()` |
| `ndp` | `driver.get_ipv6_neighbors_table()` |

The bundled `EnhancedJunOSDriver` overrides both calls to produce
generators directly from PyEZ tables, normalizing MAC and IP formats.

## What it produces per entry

Two `FactsReportEntry` rows per detected neighbor:

1. A MAC entry with `object_repr = "MACAddress <mac>"`.
2. An IP entry with `object_repr` pointing to the matched
   `IPAddress` (or the literal CIDR for new entries).

Both entries share the same `detected_values`:

```json
{
  "mac": "aa:bb:cc:11:22:33",
  "ip": "10.0.0.5/24",
  "interface": "ge-0/0/0.0",
  "vrf": "VRF_A"
}
```

## Action selection

- MAC: `confirmed` if a `MACAddress` already exists, otherwise `new`.
- IP: `confirmed` if an `IPAddress` exists in the matching VRF,
  otherwise `new`.
- Stale IPs: previously auto-discovered IPs (`AUTO_D_TAG` tag) on this
  device, in the matching family (`v4` for ARP, `v6` for NDP), that were
  not seen this run are flagged with action `stale`.

## VRF resolution

For each interface, the collector reads `get_network_instances()` and
matches the interface to a VRF via
`resolve_napalm_network_instances()`. Unknown VRFs are logged as a
warning and the IP is skipped.

## Apply behavior

Apply mode (or applying a pending entry):

- Get-or-create the `MACAddress`. New rows get the **Automatically
  Discovered** tag.
- Add the device's local interface to `MACAddress.interfaces`.
- Get-or-create the `IPAddress` in the resolved VRF. Newly-created IPs
  also get the auto-discovery tag and a `JournalEntry` recording the
  device, MAC, and interface.
- Add the IP to `MACAddress.ip_addresses` and update `last_seen`.

Stale IPs are unassigned (`assigned_object = None`) when applied.

## Performance

Both bulk-prefetch existing MACs and IPs once per device to avoid N+1
lookups. Annotation `HOST(address)` is used to match raw IP strings
against `IPAddress.address` regardless of mask.

## Edge cases handled

- Empty MAC strings, MACs with state `unreachable`, and incomplete entries
  are skipped.
- Interfaces present in the ARP/NDP table but not in NetBox are logged
  (collapsed to a count when there are more than five).
- Duplicate MACs / IPs in NetBox surface as a warning and the entry is
  skipped (manual cleanup is expected).

# OSPF

The `ospf` collector dispatches to a vendor-specific implementation. Only
`junos` is wired up today.

## Junos collection

- Runs `driver.cli(["show ospf neighbor"])`.
- Parses each line with the regex
  `^(\d+\.\d+\.\d+\.\d+)\s+(\S+)\s+(\S+)\s+(\d+\.\d+\.\d+\.\d+)`,
  yielding `(neighbor_ip, interface, state, router_id)`.

## What it produces

Per neighbor:

```json
{
  "address": "10.0.0.1",
  "interface": "ge-0/0/0.0",
  "state": "Full",
  "router_id": "10.255.0.1"
}
```

Action: `confirmed` if an `IPAddress` already exists at `<address>/32`,
else `new`.

`object_repr` is
`OSPF neighbor <ip|markdown link> (RID: <router_id>)`.

## Apply behavior

- Get-or-create the `IPAddress` at `<address>/32` (auto-discovered tag).
- Record a single `JournalEntry` per device summarizing all neighbors.
- If `netbox_routing` is installed, look up the device's
  `OSPFInstance` and log it (no row creation today).

## Limitations

- IPv4 only (the regex requires dotted-quad addresses).
- No `OSPFNeighbor` row creation; the integration with `netbox-routing`
  is currently informational. Extending it requires registering an
  `OSPFNeighbor` model and adapting `_ospf_routing_integration()`.
- Only Junos is supported. See
  [Vendor Dispatch](../developer/vendor-dispatch.md) for adding a vendor.

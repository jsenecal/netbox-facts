# Interfaces

The `interfaces` collector reconciles physical and logical interface state,
LAG membership, IP addresses, prefixes, and (where applicable) VRF
assignment.

## NAPALM calls

- `driver.get_interfaces()` for physical / logical port data and
  attributes.
- `driver.get_interfaces_ip()` for IP-per-interface data on drivers that
  do not return logical-interface data inline.
- `driver.get_network_instances()` for VRF resolution.

The bundled `EnhancedJunOSDriver.get_interfaces()` returns a
`logical_interfaces` sub-dict, which lets the collector go through a
richer code path that also captures LAG (`aenet`) membership and per-LU
VRF binding.

## Auto-create behavior

Interfaces seen on the device but missing in NetBox are auto-created with:

- type inferred by `detect_interface_type()`:
    - `lag` for `ae*` (no dot)
    - `virtual` for names containing a dot (logical units), or starting
      with `lo`, `irb`, `vlan`
    - `other` otherwise
- `description`, `enabled`, `mtu` populated from NAPALM data when
  available
- parent set to the physical interface for sub-interfaces
- the **Automatically Discovered** tag

## What it produces

For each physical interface with a MAC:

```json
{
  "interface": "ge-0/0/0",
  "mac_address": "aa:bb:cc:11:22:33",
  "is_enabled": true,
  "speed": 1000,
  "mtu": 9000,
  "is_up": true
}
```

Action: `confirmed` if a matching `MACAddress` exists, else `new`. Apply
links the MAC to the interface as `device_interface` (one-to-one) and
sets `discovery_method = interfaces`.

For LAG members (Junos `aenet` family):

```json
{"interface": "ge-0/0/0.0", "lag_parent": "ae0"}
```

Action: `confirmed` / `changed` / `new` based on the current `lag` FK.
Apply auto-creates the `aeN` interface if necessary and sets `lag`.

For each IP discovered on a logical interface:

```json
{
  "logical_interface": "ge-0/0/0.0",
  "ip_address": "10.0.0.1/30",
  "vrf": "VRF_A",
  "prefix": "10.0.0.0/30"
}
```

Action: `new`, `changed` (the IP is reassigning to a different interface
and is auto-discovered), or `confirmed`. Apply creates the `Prefix` (for
non-host routes) and the `IPAddress`, both tagged auto-discovered.

For each VRF the collector cannot find in NetBox, an entry is recorded
with `object_repr = "VRF <name>"` and action `new`. Apply creates the
VRF row.

## Stale IP detection

After processing, `_detect_stale_ips()` finds every `IPAddress` assigned
to one of the device's virtual-chassis interfaces and tagged
auto-discovered, then flags any that were not visited this run as
`stale`. Apply unassigns the IP (sets `assigned_object = None`).

## VRP / generic fallback

Drivers without enhanced logical-interface data (most non-Junos drivers)
go through `_interfaces_ip_generic()`, which walks
`get_interfaces_ip()` directly and pulls VRFs from the resolved network
instances. The same entry/apply shape is used.

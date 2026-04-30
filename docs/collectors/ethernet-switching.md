# Ethernet Switching Tables

The `ethernet_switching` collector ingests learned MACs from a switch's
forwarding table.

## NAPALM call

- `driver.get_mac_address_table()`.

## What it produces

For each MAC table entry:

```json
{
  "mac": "aa:bb:cc:11:22:33",
  "interface": "ge-0/0/12",
  "vlan": 100
}
```

Action: `confirmed` if a `MACAddress` already exists, else `new`.

`object_repr` is
`MACAddress <mac> on <interface markdown link>`.

## Apply behavior

- Get-or-create the `MACAddress` (tagged auto-discovered if new).
- Add the local interface to `MACAddress.interfaces`.
- Set `discovery_method = ethernet_switching` and refresh `last_seen`.

## Skip conditions

- MAC or interface name is empty.
- The interface name does not match `valid_interfaces_re`.
- The interface does not exist in NetBox (logged warning, skipped).
- Duplicate `MACAddress` rows exist for the address (logged warning,
  manual cleanup expected).

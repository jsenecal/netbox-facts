# MAC Address Tracking

The plugin maintains its own `MACAddress` table (separate from
`dcim.MACAddress` in core NetBox) so that observed MACs can be tracked
with discovery metadata and OUI vendor information.

Both models live in `netbox_facts/models/mac.py`.

## MACAddress

| Field | Notes |
|---|---|
| `mac_address` | Unique. Stored canonically as colon-separated uppercase. |
| `description` | Free text. |
| `vendor` | FK to `MACVendor`. Auto-populated; not user-editable. |
| `interfaces` | M2M to `dcim.Interface` via `MACAddressInterfaceRelation`. Populated by ARP/NDP, ethernet switching, and EVPN collectors. |
| `ip_addresses` | M2M to `ipam.IPAddress` via `MACAddressIPAddressRelation`. Populated by ARP/NDP collectors. |
| `device_interface` | One-to-one to `dcim.Interface`. Set by the **interfaces** collector to record where the MAC is physically owned. |
| `last_seen` | Timestamp of the most recent collection that touched this MAC. |
| `discovery_method` | One of `CollectionTypeChoices` values; whichever collector last touched the record. |
| `comments` | Free text. |

## MACVendor

| Field | Notes |
|---|---|
| `manufacturer` | Optional FK to `dcim.Manufacturer`. |
| `vendor_name` | Free text vendor label (often the OUI registrant). |
| `mac_prefix` | First 6 bytes of the MAC, unique. Stored as a 24-bit `MACPrefixField`. |

`MACVendorManager.get_by_mac_address(mac)` looks up the vendor by masking
the MAC to its OUI.

## Auto-vendor lookup

`signals.handle_mac_change` runs on every `MACAddress` save:

1. If `vendor` is unset, try `MACVendor.objects.get_by_mac_address()`.
2. If no `MACVendor` exists yet, fall back to
   `MACAddress.vendor_name_from_mac_address` (which queries the bundled
   `netaddr` IEEE OUI registry).
3. If a name is found, look up a matching `dcim.Manufacturer`; otherwise
   inherit the manufacturer from another `MACVendor` with the same name;
   otherwise leave `manufacturer` null.
4. Create a new `MACVendor` row with that prefix and the resolved
   manufacturer.

`signals.handle_mac_vendor_change` runs on every `MACVendor` save: it
updates every existing `MACAddress` whose first 6 bytes match the prefix
to point at the saved vendor. This means manually editing a vendor row
back-fills correctly.

## Interactions with collectors

| Collector | What gets written |
|---|---|
| ARP / NDP | `MACAddress.interfaces` (the device's local interface) and `MACAddress.ip_addresses`. The matching `IPAddress` is also created. |
| Interfaces | `MACAddress.device_interface` (one-to-one). The MAC is recorded as belonging to the device's port. |
| Ethernet switching | `MACAddress.interfaces` only. Tracks MACs learned in the L2 switching table. |
| EVPN | Creates the `MACAddress`, sets `discovery_method`, but does not link an interface (the EVPN collector reads from `show evpn mac-table`). |

`last_seen` is refreshed and `discovery_method` is overwritten on every
write, so it reflects the most recent collector to touch the row.

## REST API

- `GET /api/plugins/facts/macaddresses/` -- list/filter.
  `interfaces_count` is annotated.
- `GET /api/plugins/facts/macvendors/` -- list/filter.
  `instances_count` is annotated.

Filters on `MACAddress`: `mac_address`, `vendor`, `description`.
Filters on `MACVendor`: `mac_prefix`, `manufacturer`, `vendor_name`.

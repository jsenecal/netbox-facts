# BGP Collector: netbox-routing Integration

## Problem

The BGP collector creates IPAddress and ASN objects but does not populate
netbox-routing's BGP models (BGPRouter, BGPScope, BGPPeer, etc.).
The `_bgp_routing_integration()` method is a stub.

## Decisions

- **Auto-create the full chain** (BGPRouter -> BGPScope -> BGPPeer) from
  NAPALM data. Tag all created objects with `AUTO_D_TAG`.
- **Follow plan setting** for apply mode — respect `detect_only` flag.
  When detect-only, record report entries; otherwise create objects.
- **Hide BGP/OSPF collector types** when netbox-routing is not installed.
  The collector method fails immediately; the form filters out the choices.
- **Missing VRFs skip** the entire VRF's peers with a warning and report
  entry (already implemented).

## netbox-routing Model Chain

```
Device
  -> BGPRouter(device, asn)           # one per device
     -> BGPScope(router, vrf?)        # one per VRF (or global)
        -> BGPPeer(scope, peer, remote_as)  # one per neighbor
```

All models use NetBox core's `ipam.ASN` — no custom ASN model.
`FactsReportEntry` points at created objects via GenericForeignKey, so no
migration dependency on netbox-routing.

## Integration Flow (per device)

```
1. Extract local_as from NAPALM data (first peer's local_as field)
2. Get-or-create ipam.ASN for local_as
3. Get-or-create BGPRouter(device=device, asn=local_asn)
   -> tag with AUTO_D_TAG

For each VRF:
  4. Resolve VRF in NetBox (skip + report entry if missing)
  5. Get-or-create BGPScope(router=bgp_router, vrf=nb_vrf)
     -> tag with AUTO_D_TAG

  For each peer:
    6. Determine address family (IPv4/IPv6) from remote_address
    7. Get-or-create BGPPeer on the scope
       -> tag with AUTO_D_TAG
    8. Create IPAddress + ASN (existing logic, unchanged)
    9. Record report entry for each new object
```

All get-or-create steps only execute when `_should_apply()` is True.
Otherwise, only report entries are created.

## Report Entries

New netbox-routing objects get entries with `object_repr` prefixes:

| Prefix | Object | Apply action |
|--------|--------|--------------|
| `BGPRouter {device}` | BGPRouter | Create router with device + local ASN |
| `BGPScope {device} {vrf}` | BGPScope | Create scope under router |
| `BGPPeer {ip} AS{n}` | BGPPeer | Create peer under scope |

The applier dispatches on prefix (same pattern as VRF entries).

## Hiding Collector Types

- `bgp()` and `ospf()` check `HAS_NETBOX_ROUTING` at entry and fail
  with a log message if missing.
- `CollectionPlan` form filters `collector_type` choices to exclude
  `bgp`/`ospf` when netbox-routing is absent.
- DB schema unchanged — `"bgp"` remains a valid CharField value.

## Not Changing

- Existing IP/ASN creation logic (steps 7-8 above)
- `FactsReportEntry` model (GFK, no new fields)
- Migration files (no FK to netbox-routing)

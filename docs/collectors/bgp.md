# BGP

The `bgp` collector ingests BGP neighbor data and, when the
[netbox-routing](https://github.com/netbox-community/netbox-routing) plugin
is installed, populates `BGPRouter`, `BGPScope`, and `BGPPeer` rows in
addition to creating the peer IP and ASN.

## NAPALM call

- `driver.get_bgp_neighbors_detail()`. Returns a nested dict keyed by VRF
  name, then by remote AS.

## What it produces

For every peer in every VRF, a peer-IP entry:

```json
{
  "remote_address": "10.0.0.2",
  "remote_as": 65002,
  "vrf": "VRF_A",
  "state": "up"
}
```

`remote_address` is normalized to a `/32` (IPv4) or `/128` (IPv6) before
being matched against NetBox.

Action: `confirmed` if an `IPAddress` exists at that CIDR in the resolved
VRF, else `new`.

A separate entry is emitted for every unknown VRF
(`object_repr = "VRF <name>"`, action `new`). Apply creates the VRF.

## Apply behavior

- Get-or-create an `ASN` for the remote AS. Requires that at least one
  `RIR` exists in NetBox; if none exists, the ASN creation is skipped
  with a warning.
- Get-or-create the `IPAddress` in the resolved VRF, tagged
  auto-discovered.
- Record a `JournalEntry` summarizing the peer when the IP is new.

## netbox-routing integration

If `netbox_routing` can be imported, after processing all peers the
collector additionally records:

- `BGPRouter` (`object_repr = "BGPRouter <device>"`) keyed by
  `(device, local_asn)`.
- One `BGPScope` per VRF (`object_repr = "BGPScope <device> <vrf|global>"`)
  keyed by `(router, vrf)`.
- One `BGPPeer` per peer (`object_repr = "BGPPeer <ip> AS<asn>"`) keyed by
  `(scope, peer_ip)`.

In detect-only mode these entries reflect what would be created. In apply
mode the rows are written immediately, all tagged
**Automatically Discovered**.

The dedicated apply handlers
(`_apply_bgp_router_entry`, `_apply_bgp_scope_entry`,
`_apply_bgp_peer_routing_entry`) re-run the same `get_or_create` chain,
so a pending detect-only entry can be applied later just like the inline
path.

## Without netbox-routing

The collector still produces peer-IP entries and creates the
`IPAddress` / `ASN` rows. The routing integration block is skipped with a
single info-level log.

To enable the integration:

```bash
pip install "netbox-facts[routing]"
```

then add `netbox_routing` to `PLUGINS` and migrate.

## Skip conditions

- Empty `remote_address`.
- Unparseable IP (logged as a warning, peer skipped).
- Unknown VRF (the missing-VRF entry is recorded; peers in that VRF are
  skipped this run).
- Duplicate `IPAddress` or `ASN` rows (logged, skipped, manual cleanup
  expected).

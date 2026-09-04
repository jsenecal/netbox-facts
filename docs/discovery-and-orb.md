# netbox-facts and NetBox Discovery

NetBox Labs ships its own discovery stack: [NetBox
Discovery](https://netboxlabs.com/docs/discovery/), built on the open-source
[Orb agent](https://github.com/netboxlabs/orb-agent) and the
[Diode](https://netboxlabs.com/docs/diode/) ingestion service. The two
projects overlap less than the names suggest. Discovery finds equipment
NetBox does not know about; netbox-facts reads operational state from
devices NetBox already models. You can run both.

## Architecture

NetBox Discovery deploys containerized Orb agents near the network segments
they cover. Agents take YAML policies and discover hosts with nmap, SNMP,
and NAPALM. They never write to NetBox directly: discovered entities stream
through Diode, which maps them onto NetBox objects.

netbox-facts has no agents and no ingestion pipeline. Collection Plans run
in NetBox's own background workers and connect out to devices over NAPALM
(SSH or NETCONF). The trade-off is onboarding: a device must already exist
in NetBox, with a reachable primary or out-of-band IP address, before
anything can be collected from it.

## Scope

Discovery answers "what is on this network": populating an empty NetBox,
finding unmanaged hosts, covering SNMP-only gear, and scaling across
segmented networks by placing agents where the visibility is.

netbox-facts answers "what are my modeled devices actually doing". Its
collectors read ARP and IPv6 neighbor tables, MAC address tables, LLDP
adjacencies (recorded as NetBox cables), LAG membership, interface
addressing, chassis inventory, and BGP sessions. What separates it from a
plain sync script is the write path: a
[Collection Plan](user-guide/collection-plans.md) can run in
[detect-only mode](user-guide/detect-only.md), which produces a
[Facts Report](user-guide/facts-reports.md) instead of touching the
database, and each detected change is then applied or skipped individually.
Stale handling only ever removes objects the plugin created itself (tagged
"Automatically Discovered"), never operator-entered records.

Diode matches data at ingestion time; ongoing drift detection on the
Discovery side belongs to NetBox Labs' commercial NetBox Assurance product.
In netbox-facts, the detect/review/apply loop is part of the plugin.

## Running both

Let Discovery seed NetBox with the devices, interfaces, and addresses it
finds. Once a device has reachable management addressing and a NAPALM
driver, add it to a Collection Plan and let netbox-facts fill in what
scanning cannot see: neighbor tables, switching tables, cabling, chassis
modules. Where a bad write would be expensive, run the plan detect-only and
treat the Facts Report as the record of what the network reported versus
what NetBox believed.

There is no integration between the two projects and none is needed. Both
write the same NetBox objects, and netbox-facts treats records created
elsewhere the way it treats operator data: it confirms and updates them,
but never removes them.

## Further reading

- [NetBox Discovery documentation](https://netboxlabs.com/docs/discovery/)
- [Orb agent documentation](https://netboxlabs.com/docs/orb-agent/)
- [Diode documentation](https://netboxlabs.com/docs/diode/)
- [Collection Plans](user-guide/collection-plans.md) and the
  [Detect-Only Workflow](user-guide/detect-only.md) in this documentation

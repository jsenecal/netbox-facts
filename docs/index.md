# NetBox Facts

NetBox Facts is a NetBox 4.5+ plugin that gathers operational facts from
network devices managed in NetBox via [NAPALM](https://napalm.readthedocs.io/)
and stores them as queryable records.

## What it does

The plugin connects to devices using the credentials and driver configured
on a Collection Plan, runs one of ten collector types (ARP, NDP, inventory,
interfaces, LLDP, ethernet switching, L2 circuits, EVPN, BGP, OSPF), and
persists what it finds. Results are either:

- written directly to NetBox (apply mode); or
- captured in a `FactsReport` for review (detect-only mode), where each
  detected fact is a `FactsReportEntry` that can be applied or skipped
  individually or in bulk.

Recurring collection is handled by NetBox's `JobRunner` framework so plans
re-run on a configurable interval.

## When to use it

- You want to keep IP, MAC, neighbor, and chassis inventory data in NetBox
  in sync with what devices actually report.
- You want auditable, reviewable changes rather than silent mutations.
- You operate Junos and want richer ARP/NDP, chassis inventory, EVPN, L2
  circuits, and OSPF collection out of the box.

## Where to start

- New users: read [Installation](getting-started/installation.md), then
  [Configuration](getting-started/configuration.md), then
  [Quick Start](getting-started/quick-start.md).
- Operators: read [Collection Plans](user-guide/collection-plans.md) and
  [Detect-Only Workflow](user-guide/detect-only.md).
- Integrators: read the [REST API](reference/rest-api.md) reference.
- Plugin developers: read [Architecture](developer/architecture.md) and
  [Vendor Dispatch](developer/vendor-dispatch.md).

## Project status

Alpha. Compatible with NetBox 4.5.x. Source on
[GitHub](https://github.com/jsenecal/netbox-facts), released to PyPI as
`netbox-facts`.

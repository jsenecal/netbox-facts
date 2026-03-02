# Changelog

## Unreleased

### Breaking Changes

* Migrated from setuptools to hatchling build backend with `pyproject.toml`
* Removed hardcoded default NAPALM credentials from plugin settings — `napalm_username` and `napalm_password` now default to empty strings and must be configured explicitly

### Added

* **NetBox 4.5.x compatibility** — updated models, views, and APIs for the NetBox 4.x plugin framework
* **10 collector types**: ARP, NDP, Inventory, Interfaces, LLDP, Ethernet Switching, L2 Circuits, EVPN, BGP, OSPF
* **Detect-only mode** (`detect_only` flag on CollectionPlan) — collection runs produce a `FactsReport` without modifying NetBox objects; changes can be reviewed and selectively applied or skipped
* **FactsReport / FactsReportEntry models** — track detected facts with action types (new/changed/confirmed/stale) and apply status (pending/applied/skipped/failed)
* **Auto-scheduling** — `CollectionPlan` with an interval automatically schedules recurring jobs via `CollectionJobRunner.enqueue_once()`, mirroring NetBox's DataSource sync pattern
* **JobRunner integration** — `CollectionJobRunner` extends NetBox's `JobRunner` with job log persistence and report linking
* **Vendor dispatch framework** — extensible per-vendor collector methods with Junos-specific L2 circuits, EVPN, and OSPF collectors
* **LLDP collector** with same-site cable auto-creation
* **BGP collector** with ASN and VRF support
* **Optional netbox-routing integration** — BGP and OSPF collectors use `netbox-routing` plugin models when installed
* **REST API** — full CRUD endpoints for MAC addresses, MAC vendors, collection plans, and facts reports
* **CI test workflow** — GitHub Actions running tests inside the NetBox container with PostgreSQL and Redis services
* **Dev container improvements** — updated to NetBox 4.5.3, migrated dependency management to uv

### Fixed

* MAC prefix handling and OUI vendor lookup
* Infinite recursion risk in MAC signal handlers
* UI forms with proper selectors, device field filtering, and driver selection
* Stalled job detection and status management
* Entry ownership validation in apply/skip API endpoints
* Transaction safety in applier with per-entry savepoints

## 0.0.1 (2023-08-02)

* First release on PyPI.

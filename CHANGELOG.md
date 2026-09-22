# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Releases prior to 1.0.x use the legacy `## VERSION (DATE)` heading style.

## [Unreleased]

### Fixed

- The "Occurrences" column header on the MAC Address list was misspelled "Occurences". (#162)
- The Details column for a CHANGED report entry now shows attributes newly reported by the device (detected-only keys) and attributes the device no longer reports (current-only keys), instead of silently dropping them from the diff; both render with an explicit "(not set)" marker on the missing side. (#133)
- `CollectionPlan.run()` no longer starts a debugpy listener on `0.0.0.0:5678` and blocks the worker whenever a plan's free-form NAPALM arguments contain `debug: true`; the hook now requires `settings.DEBUG` to be True and binds to `127.0.0.1` only, and the `debug` key is stripped from the merged args returned by `get_napalm_args()` unconditionally so it never reaches the NAPALM driver. (#132)
- The Collection Plans menu item now checks `view_collectionplan` instead of
  the nonexistent `view_collector`, and the `run_collector` and
  `view_collector_results` permissions checked by the Run button, run view,
  and Results tab are now declared on `CollectionPlan.Meta.permissions`, so
  these actions can be granted to non-superusers through Django groups. (#131)
- The disabled Run button's tooltip now shows the actual reason a
  `CollectionPlan` cannot be run ("Plan is disabled" or "A run is already
  queued or in progress") instead of an empty tooltip; `ready` now derives
  from the same reason so the two cannot drift. (#135, #164 by @EthemKD)
- README's `PLUGINS_CONFIG` example no longer ships a placeholder `valid_interfaces_re` that silently matches zero interfaces; the docs nav no longer links to nine Reference/Developer pages that do not exist; and the quick-start and configuration docs now describe the real `device_status` and empty-credentials behavior instead of a friendlier default that the code does not implement. (#136, #137, #138)
- Detect-only interfaces runs no longer create Interface objects in NetBox; missing interfaces are recorded as pending report entries that the applier creates on apply. (#47)
- The stale-IP sweep is skipped when IP collection fails and no longer covers interfaces excluded by `valid_interfaces_re` or skipped for unresolvable VRFs, so transient RPC errors and scope changes cannot mass-unassign still-configured addresses. (#49)
- A changed hardware MAC no longer aborts the collection run with an IntegrityError; the previous MACAddress row releases the interface before the new row claims it, in both the collector and the applier. (#55)
- Junos inet destinations abbreviated below three octets (such as `10/8` and `172.16/16`) keep their real prefix length instead of being recorded and created as /32 host routes. (#56)
- The generic IP path skips addresses in a device VRF that has no NetBox counterpart, recording a pending missing-VRF entry instead of booking them into the global table or stealing existing global IPs. (#57)
- Duplicate VRF names in NetBox no longer abort the entire collection run with MultipleObjectsReturned; the affected instance's IPs are skipped with a warning. (#46)
- `CollectionPlan.get_napalm_driver` no longer routes plugin-local driver names through napalm's `get_network_driver`, which rejects dotted module paths under napalm 5.2.0 and made every collection run fail before contacting a device. (#42)
- `CollectionPlan.get_napalm_args` no longer mutates the live `PLUGINS_CONFIG` dict, which leaked one plan's NAPALM arguments (including username/password overrides) into every later plan run in the same worker process. (#58)
- Credential values (`username`, `password`, `secret`) stored in a plan's NAPALM arguments are now censored in REST API responses and in the edit form; submitting the censored values back preserves the stored real values. (#59)
- Loading a collection plan during its first-ever run no longer flips its status to `stalled`, which allowed a second concurrent collection to be enqueued against the same devices mid-run. (#60)
- ARP/NDP collection no longer aborts on standard NAPALM drivers that return neighbor IPs as strings, and execute() no longer disguises collector-body AttributeErrors as NotImplementedError (#43).
- RPC errors raised while iterating the enhanced Junos generator getters are now translated to NAPALM exceptions and handled per device instead of aborting the whole run (#44).
- Drivers without get_network_instances (e.g. iosxr) no longer abort ARP/interface collection; VRF context degrades to an empty mapping with an informational log (#45).
- ARP/NDP report entries now record the VRF name instead of str(VRF), so detect-then-apply resolves the VRF instead of silently writing IPs into the global table when the VRF has a route distinguisher (#52).
- The stale-module sweep no longer deletes or STALE-flags Modules for hardware that is still installed when the ModuleBay or ModuleType of a reported chassis component cannot be resolved; an unresolved bay now suppresses the sweep for the whole device with a warning. (#50)
- Swapping the hardware in a module bay for a different part is now detected as CHANGED (even with an unchanged serial), reported with the current module type, and applied by replacing the Module with one of the new ModuleType instead of only updating the serial. (#51)
- Detect-only BGP runs no longer create the device's local ASN in NetBox; the BGPRouter report entry is still recorded. (#48)
- BGP collection and the applier BGP handlers no longer crash with an IntegrityError when NetBox has no RIR: the collector skips ASN creation with a warning, and applier entries that require the ASN fail with a clear message. (#54)
- Applying an ARP/NDP, interfaces IP, or BGP peer entry whose detected VRF no longer resolves now fails the entry instead of silently writing the IP address into the global routing table. (#53)

### Added

- Report entry review ergonomics: every per-status entry tab now renders a
  filter form (device, action, status, collector type, entry kind) backed by a
  new `q` search matching the entry label or the device name, and an Export
  button that writes the filtered entries of that tab to CSV. The four tabs are
  no longer hidden when empty, so the tab set does not shift as entries move
  from pending to applied mid-review, and the entry-status counts on the report
  page link to the matching tab. The report and entry filtersets now build on
  NetBox's `BaseFilterSet`, which also makes saved filters apply to them.
  (#140)
- Collection Plan scope preview: the plan detail page now shows a "Resolved
  scope" panel with the number of devices the plan currently matches (linking
  to the device list filtered by the plan's scope), the connection target, and
  how many matched devices have no usable IP for that target, naming the first
  offenders in a tooltip. The edit form shows the same resolved count for a
  saved plan and reports it again after every save. (#145)
- Empty-scope guard: `CollectionPlan.clean()` now rejects a plan that sets no
  scoping dimension at all, because such a plan resolves to every device in
  NetBox. Deliberate fleet-wide plans set the new `allow_unscoped` field to opt
  out. Saving a plan whose scope resolves to more devices than the new
  `scope_warning_threshold` plugin setting (default `500`, `0` disables) shows a
  warning message. (#145)
- The Collection Plan CSV import form gained the device-scoping columns
  (`devices`, `regions`, `site_groups`, `sites`, `locations`, `device_types`,
  `roles`, `platforms`, `tenant_groups`, `tenants`, `device_status` and
  `allow_unscoped`; `tags` was already importable), so imported plans are no
  longer born scopeless. (#145)
- Report entries now carry an `entry_kind` field naming what kind of object
  the entry concerns (interface, interface MAC, LAG, IP address, MAC address,
  VRF, inventory item, module, cable, L2 circuit, BGP router/scope/peer/peer
  address, OSPF neighbor, device, or other). It is set at detect time, exposed
  and filterable over REST and GraphQL, and a data migration backfills existing
  rows from their labels. (#153)
- Report entries gain a `display_title` property composing a one-line human
  title from the entry kind, its subject and the action ("Interface xe-0/0/1
  changed", "IP address 10.0.0.1/32 discovered"). Read-only over REST. (#153)
- Report entries gain an `applying` status, set while an entry's apply handler
  runs, so a long apply is visible as in-progress rather than still pending.
  (#154)
- Report entries gain a read-only `apply_error` field holding the structured
  failure of the last apply: field-addressed messages for validation errors
  (`{"serial": ["..."], "error_type": "validation"}`) and an `__all__` message
  with `"error_type": "error"` for infrastructure failures such as an
  unreachable device. It is cleared when the entry applies successfully. (#154)
- Facts Reports now raise a NetBox event when a collection run finishes, so
  reviewers can be notified through a standard event rule (webhook, script, or
  notification group) instead of polling the report list. The report model
  gained the `event_rules` feature and the plugin registers a dedicated
  `netbox_facts.report_ready` event type ("Facts report ready for review"),
  raised once per run with the final status and summary counts in the payload.
  (#143)
- Optional Facts Report retention: the new `report_retention_days` plugin
  setting (default `0`, meaning keep forever) enables a daily
  "Facts Report Retention" system job that deletes reports older than the
  configured window. Reports holding pending entries are never pruned, so
  work awaiting review cannot age out. (#152)
- Read-only REST endpoint `/api/plugins/facts/factsreportentries/` listing the
  entries of a facts report, so API clients can discover the entry PKs that the
  report-level apply and skip actions take. Entries can be filtered by report,
  action, status, collector type, and device. (#151)
- `CollectionPlanSerializer` now exposes the scheduling and connection fields
  (`interval`, `scheduled_at`, `last_run`, `run_as`, `connection_target`), so
  recurring collection plans can be created and inspected over REST. `last_run`
  and `run_as` are read-only -- scheduled runs are enqueued as `run_as` without
  a superuser check, so the acting user is not something a plan editor may pick
  over the API. NAPALM credentials stay censored. (#151)
- GraphQL support: MAC addresses, MAC vendors, collection plans, facts reports,
  and facts report entries are exposed in NetBox's GraphQL schema. A plan's
  `napalm_args` is excluded from the GraphQL type because it holds connection
  credentials. (#151)
- MAC Address detail page now shows Last Seen and Discovery Method
  alongside the fields already shown in the table, and gains the standard
  plugin_left_page/plugin_right_page/plugin_full_width_page hook blocks
  that the MAC Vendor detail page already had. (#162)
- MAC Address detail page gains an "Interfaces" tab listing the interfaces
  this MAC has been seen on (device, interface, last seen). (#162)
- `FactsConfig` now declares `min_version = "4.5.0"` and
  `max_version = "4.7.99"`. NetBox refuses to start with an out-of-range
  release instead of failing later with an obscure import or template
  error.
- Documentation page "netbox-facts and NetBox Discovery" positioning the
  plugin against NetBox Labs' Orb/Diode discovery stack.
- README and docs now state plainly that the detect -> review -> apply
  loop runs entirely in open-source NetBox, complementing rather than
  competing with discovery tools; "NetBox Discovery and Orb" gains the
  commercial-boundary detail (Diode's review UI moved to NetBox Assurance,
  Cloud/Enterprise-only) and the Detect-Only Workflow guide gains a
  "Working as a team" section on review cadence and queue ownership.
  (#163)

### Changed

- The Collection Plan detail page's Assignment panel no longer dumps every
  assigned object: each scoping dimension lists at most ten entries and
  reports the rest as a count, so a plan pinning thousands of devices stays
  readable. (#145)
- Apply now dispatches on an entry's `entry_kind` instead of matching the
  leading words of its `object_repr`, so renaming a display label can no
  longer route an entry to the wrong handler. `object_repr` remains the
  display value. (#153)
- Developer-facing: the plugin's pytest runs now use their own
  `test_netbox_facts` database instead of the meta-repo's shared
  `test_netbox`, and carry the `.testdb-isolated` marker so they no longer
  take the cross-plugin test lock.
- CI: the NetBox 4.5 lanes now run without the netbox-routing integration; its current migrations require NetBox 4.6+. Routing tests skip on those lanes and coverage still uploads from the 4.7 lane.
- "Apply All Pending" on a facts report now asks for confirmation and runs
  as a background job (`Facts Report Apply`) instead of applying inline in
  the web request. The button posts a single flag and the pending entries
  are resolved server-side, so the report page no longer renders one hidden
  input per entry and large reports no longer risk a request timeout. Only
  one apply job may be in flight per report. Applying a tick-selected subset
  of entries is unchanged and still runs inline. (#134)
- MACVendor detail, edit, delete, instances, changelog, and journal routes
  are now generated via `register_model_view` + `get_model_urls` instead of
  being spelled out manually in `urls.py`; the nonstandard `macvendor_detail`
  route name is retired in favor of `macvendor` (matching the MACAddress
  and CollectionPlan convention). The manually wired changelog/journal
  routes for MACAddress and MACVendor are also removed, since NetBox
  already auto-registers them for every model. (#162)
- NetBox 4.7 support. CI adds a 4.7.0 lane alongside 4.5.10 and 4.6.10,
  Renovate keeps a `4.7.x` lane pinned to the newest release of that
  minor, and the coverage upload now runs on the 4.7 lane. The README
  and docs compatibility lists add NetBox 4.7.x.
- Dropped the `Django>=5.2,<5.3` runtime dependency. NetBox pins the
  Django version it supports, and the plugin-side pin conflicted with
  NetBox 4.7, which ships Django 6.1.
- CI now tests against the latest NetBox 4.5 and 4.6 releases (4.5.10
  and 4.6.10, previously 4.5.8 and 4.5.10), with Renovate keeping the
  matrix pinned to the newest release of each supported minor. The
  README compatibility matrix now lists NetBox 4.5.x and 4.6.x.

## [0.1.1] - 2026-05-01

### Fixed

* `MACIPAddressesView.get_table` and `MACVendorInstancesView.get_table` no longer pass the `user` kwarg to `BaseTable.__init__`. NetBox 4.5 removed that kwarg, so the "IP Addresses" tab on the MAC address detail and the "Instances" tab on the MAC vendor detail crashed with `TypeError: Table.__init__() got an unexpected keyword argument 'user'`. User-specific column/ordering preferences are now applied entirely via `table.configure(request)`, which both overrides already invoke. (#4)

### Changed

* Removed the redundant `MACIPAddressesView.get_table` override; the view now inherits upstream `TableMixin.get_table`, which adds support for persisting saved `TableConfig` selections via the `tableconfig_id` query parameter.

### Tests

* Added a regression test pinning the post-fix Table init contract for `MACAddressTable` and the upstream `IPAddressTable` mounted in the IP-addresses child view.

## [0.1.0] - 2026-04-28

### Breaking Changes

* Migrated from setuptools to hatchling build backend with `pyproject.toml`.
* Removed hardcoded default NAPALM credentials from plugin settings; `napalm_username` and `napalm_password` now default to empty strings and must be configured explicitly.

### Added (toolkit normalization)

* Canonical 5 GHA workflows (ci.yml, publish.yml, docs.yml, release-drafter.yml, pr-title.yml) + `.github/release-drafter.yml`. Replaces the previous `tests.yml` + `mkdocs.yml` workflow setup. CI now matrixes Python 3.12-3.14 x NetBox 4.5.3/4.5.8 with full migrate / pytest / makemigrations check / system check / build steps and OIDC codecov upload.
* `.pre-commit-config.yaml` with ruff hooks + standard pre-commit-hooks + commit-msg AI-attribution rejecter.
* `.git-template/hooks/commit-msg` (canonical hook tracked in-tree).
* `docs/zensical.toml` (replaces root `mkdocs.yml`; same nav).
* `uv.lock` committed.
* Empty `tests/` directory and pytest config (was using `manage.py test netbox_facts`).

### Removed (toolkit normalization)

* `tests.yml` workflow (replaced by `ci.yml`).
* `mkdocs.yml` workflow (replaced by `docs.yml`).
* Root `mkdocs.yml` config (replaced by `docs/zensical.toml`).
* Dev deps no longer needed: `autoflake`, `autopep8`, `bandit`, `black`, `bump2version`, `debugpy`, `flake8`, `ipython`, `isort`, `mypy`, `mypy-extensions`, `pycodestyle`, `pydocstyle`, `pylint`, `pylint-django`, `rich`, `sourcery-analytics`, `mkdocs`, `mkdocs-material`, `mkdocs-include-markdown-plugin`, `mkdocstrings[python]`, `twine`, `watchdog`, `wheel`, `wily`, `yapf`. Kept: `pre-commit`, `pytest`, `ruff`. Added: `pytest-django`, `pytest-cov`, `bumpver`. `zensical` lives in a separate `[docs]` extra.
* `[tool.isort]`, `[tool.mypy]`, `[tool.bumpversion]` sections.

### Changed (toolkit normalization)

* Build is now `uv build` (was `python -m build`).
* `[tool.bumpversion]` -> `[tool.bumpver]` with the canonical file_patterns including a `CHANGELOG.md` promotion pattern. Tag pattern `vMAJOR.MINOR.PATCH`.
* Ruff selectors expanded from `["E", "F", "W"]` to the canonical set (`E, F, W, I, N, UP, S, B, A, C4, DJ, PIE`); ignore `N806` globally for the Django `User = get_user_model()` idiom; per-file ignores added for migrations and tests.
* CHANGELOG converted to Keep-a-Changelog format with bracketed `[Unreleased]` heading. Pre-1.0.x entries kept in their existing `## VERSION (DATE)` style.

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

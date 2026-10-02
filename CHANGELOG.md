# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Releases prior to 1.0.x use the legacy `## VERSION (DATE)` heading style.

## [Unreleased]

### Changed

- Ownership checks in `helpers/collector.py` and `helpers/applier.py` now
  match the **Automatically Discovered** tag by its stable slug
  (`automatically-discovered`) instead of its display name, so renaming
  the tag in the UI no longer breaks stale detection or any ownership
  gate. A migration creates the tag's row (adopting a pre-existing tag of
  the same name if one exists under a different slug), and a signal
  blocks renaming its slug or deleting it outright with a message naming
  the plugin; its display name, color, and description remain freely
  editable.

### Fixed

- The MAC address detail page's Interfaces tab badge always showed 0 even
  when the tab listed rows. NetBox's own `dcim.MACAddress` model already
  owns the `mac_addresses` reverse name that the plugin's `interfaces` M2M
  field also asks for on `dcim.Interface`, so a read through the forward
  manager silently resolved to the wrong relation and came back empty. The
  badge now counts through the plugin's own `MACAddressInterfaceRelation`
  through-model, the same way the tab's row listing already did. The MAC
  list's "Occurrences" column was not affected: its `Count("interfaces")`
  annotation resolves the field declared on `MACAddress` directly and never
  needed the colliding reverse name. (#192)
- Cloning a Collection Plan no longer carries its credentials into the new
  plan's creation link. NetBox renders cloned attributes into the link's
  querystring, so a stored username, password or enable secret ended up in
  browser history and proxy logs, and then in an add form with no stored
  value to censor it against. A clone now starts with the plan's other NAPALM
  driver options and no credentials. (#149)
- `scheduled_at` is now honored instead of collected, validated and discarded: a plan with a future `scheduled_at` and no recurrence runs exactly once at that time (previously it got no job at all), and an interval plan with a future `scheduled_at` waits for it instead of starting its first run the moment the plan is saved. A `scheduled_at` already in the past schedules nothing, so editing a plan no longer triggers an unexpected run. A future-dated job also no longer sets the plan's status to `queued`, so the Run button and manual runs stay available while a plan waits for its slot. (#90)
- The Facts Reports and detect-only docs claimed `completed_at` is stamped
  "whenever the status reaches a non-Pending state"; that is wrong for
  `Partial`, which does not stamp it. Corrected to describe the real rule:
  `completed_at` is set when a report leaves the review states (`Pending`,
  `Partial`) and lands on `Applied`, `Completed`, or `Failed`, and is left
  untouched otherwise, so a report reopened by an un-skip keeps the
  completion timestamp it already recorded.
- The Facts Reports and configuration docs did not say how the REST API
  authorizes the report-level write actions (`apply`, `skip`, `retry`,
  `unskip`) and the plan's `run` action. They are plain `POST` actions, so
  NetBox's token permission class maps them to the standard
  `add_factsreport` / `add_collectionplan` permissions, not the UI's
  `apply_factsreport` / `run_collector` custom permissions. Documented so
  API consumers granted only the custom permissions are not surprised by a
  403.
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

- A collection run now records what it made of every device it attempted, as
  one `FactsReportDeviceOutcome` row per device on the report: the outcome
  (`ok`, `unreachable`, `auth_failed`, `driver_error`, `skipped_no_ip`,
  `skipped_no_driver`, `skipped_incompatible`), the seconds spent dialing the
  device, how many entries its pass produced, and one line of evidence from
  the connection failure. Previously a per-device failure reached only the job
  log, so a report could not say whether it was empty because the network was
  clean or because nothing answered. The outcomes are the same categories the
  run-summary log line already tallied, so a row and the log agree by
  construction. The report page gains collected / failed / skipped device
  counts, a **Devices** tab lists the rows (badged with the number of devices
  attempted), and `GET /api/plugins/facts/factsreportdeviceoutcomes/` exposes
  them read-only, filterable by `report`, `outcome` and `device`. The report
  serializer gains read-only `device_count`, `device_ok_count`,
  `device_failed_count` and `device_skipped_count`. An authentication failure
  is now logged and recorded as itself rather than as a generic connection
  failure. (#144)
- A **Rediff** action on pending report entries -- per row and in bulk, on the
  Pending tab and as `POST /api/plugins/facts/factsreports/<id>/rediff/` --
  re-reads what NetBox holds for each selected entry without contacting the
  device, so a reviewer acts on the current comparison rather than the one the
  run recorded. `current_values` and the diff are refreshed, the entry is
  pointed at the NetBox object it resolves to, and an entry NetBox already
  satisfies is recorded as applied instead of being applied again: its handler
  would write nothing it has not already got, or fail on a duplicate it cannot
  create twice. Device serials, chassis inventory items, modules, interfaces,
  interface MACs, neighbor MACs, LAG memberships, IP addresses and VRFs are
  re-analyzed; cables, L2 circuits, BGP objects and OSPF neighbors cannot be
  re-analyzed without the device and are left untouched and reported as such.
  (#142)
- Skip memory: each report entry now carries a `change_hash`, the content
  identity of the change it proposes (the entry kind, the entry label, and the
  detected payload minus the keys an apply never acts on). A detect-only run no
  longer records a change the same plan has a skipped entry for on the same
  device, and its summary line reports "suppressed N previously skipped
  changes" so the memory is visible. A payload that actually moves resurfaces
  normally, link-state and other volatile movement does not, un-skipping an
  entry forgets the decision, and applied, failed or pending history never
  suppresses anything. Entries recorded before the field existed carry a blank
  hash and suppress nothing; no backfill is attempted. (#142)
- Per-plan credentials are now first-class fields on the Collection Plan edit
  form -- **NAPALM username**, **NAPALM password** and **NAPALM enable
  secret** -- instead of undocumented magic keys inside the NAPALM arguments
  JSON. The two secret fields never render a stored value: a stored secret
  shows as a `********` placeholder, leaving the field blank keeps it, and
  submitting the censored value back preserves it, exactly as the JSON field
  already did. The fields write to the same `napalm_args` keys, so the JSON
  path stays supported for the REST API and bulk import, and no migration is
  involved. (#149)
- A plan is now checked for credentials before it is enqueued: when no
  username resolves from the plan, `global_napalm_args`, or the plugin-level
  `napalm_username` setting, the run is refused with "no NAPALM credentials
  are configured for this plan" (a warning in the UI, HTTP 409 on
  `POST .../collectionplans/<id>/run/`) rather than failing once per device
  inside the job log. The collector and the check share one resolution
  helper, so they cannot drift. (#149)
- A Collection Plan's `napalm_driver` is now optional. Left blank, the driver
  is resolved per device from `device.platform`, so one plan can span several
  vendors; set, it stays an override applied to every device in scope. NetBox
  removed `Platform.napalm_driver` in 3.6 and 4.x has no replacement, so the
  mapping is the plugin's own convention: a `dcim.Platform` custom field named
  by the new `platform_driver_custom_field` setting (default `napalm_driver`),
  falling back to the platform's slug. The enhanced-driver preference
  (`netbox_facts.napalm.<name>`) applies to a resolved name exactly as to an
  explicit one. A device whose platform yields no usable driver is skipped with
  a warning naming the reason, and counted in a new end-of-run summary line
  that also tallies devices skipped for a missing IP or an unreachable host.
  (#147)
- A collector/driver compatibility table (`COLLECTOR_SUPPORTED_DRIVERS` in
  `choices.py`) encodes which NAPALM drivers each collector has an
  implementation for -- `l2_circuits`, `evpn` and `ospf` are Junos-only.
  `CollectionPlan.clean()` now rejects a plan whose explicit driver its
  collector cannot serve, so an `evpn` plan can no longer be saved with `ios`
  and rediscovered as a failed job on every scheduled run. A blank-driver plan
  defers the check to run time, where an incompatible device is skipped before
  any connection is opened rather than aborting the whole report. (#83, #147)
- Cron-style scheduling: a collection plan accepts a five-field cron expression
  (`cron_schedule`, for example `0 2 * * 1-5`) as an alternative to the flat
  interval, evaluated in NetBox's configured time zone. The expression is
  validated on save, is mutually exclusive with the interval, and each run
  enqueues its own successor so a cron schedule survives a failed run. Plans
  now expose a computed `next_run` on the detail page, in the REST API, and on
  the plan list, which also gains Enabled, Last run, Next run, Interval and
  Cron schedule columns, a per-row Run button, and a badge-style Detect Only
  column. (#146, #90)
- `FactsReportEntry` now advertises the `export_templates` model feature, so
  it appears in the object-type picker when creating an Export Template
  under Operations > Export Templates. The entry export path already
  rendered ExportTemplates correctly; only the picker was missing the type.

- Entry lifecycle actions: a failed entry can be retried and a skipped entry
  can be un-skipped. Retry returns the selected failed entries to pending,
  clears the recorded failure, and re-applies them; un-skip returns skipped
  entries to pending for review without applying anything. Both are exposed as
  status-aware bulk controls on the Failed and Skipped tabs, as per-row
  shortcuts on every entry row (apply/skip on pending, retry on failed,
  un-skip on skipped), and as the report-level REST actions
  `POST .../factsreports/<id>/retry/` and `POST .../factsreports/<id>/unskip/`,
  with the same ownership validation and throttle as apply and skip. Entry tabs
  that span more than one page also gained a "select all N matching entries"
  affordance: the browser submits only the flag and the server re-resolves the
  selection from the report, the tab's status, and the tab's current filters.
  (#141)
- Report entry review ergonomics: every per-status entry tab now renders a
  filter form (device, action, status, collector type, entry kind) backed by a
  new `q` search matching the entry label or the device name, and an Export
  button offering the same choices as any NetBox object list -- the configured
  columns, all columns, or an export template -- honoring the user's CSV
  delimiter preference and the `STREAMING_EXPORTS` setting. The four tabs are
  no longer hidden when empty, so the tab set does not shift as entries move
  from pending to applied mid-review, and the entry-status counts on the report
  page link to the matching tab. The report and entry filtersets now build on
  NetBox's `BaseFilterSet`, which also makes saved filters apply to them.
  (#140)
- Report entries now have a detail page of their own, linked from the new
  "Entry" column of the entry tables: an overview of the entry's kind, action,
  status, device, report, collector type and timestamps; a Changes panel
  comparing what NetBox holds against what the device reported, key by key,
  marked modified/added/removed; the raw `detected_values` and
  `current_values` payloads as collected; and, for a failed entry, its
  structured `apply_error` rendered field by field, distinguishing a
  validation rejection from an infrastructure failure. The page is gated on
  `netbox_facts.view_factsreport`, the same permission as the report it
  belongs to. (#139)
- Device page Facts tab: a device the plugin has recorded facts for now carries
  a "Facts" tab, badged with the number of entries for that device still
  awaiting a decision. It lists those pending entries and adds two panels -- the
  most recent collection timestamp per collector type, derived from the reports
  that produced this device's own entries, and the enabled Collection Plans
  whose scope currently resolves to this device, with each plan's last run. The
  tab requires `netbox_facts.view_factsreport` and is hidden on devices with no
  facts data at all; a device that has been collected and is simply clean keeps
  the tab with a `0` badge. Plan coverage is resolved per plan, so the tab caps
  how many enabled plans it checks for one page view and says so when the cap is
  reached. (#150)
- "Pending Facts Changes" dashboard widget: shows the total entries awaiting a
  decision and how many reports hold them, both linking to the report list
  filtered to the reports awaiting review. Counts respect the viewing user's
  object permissions. (#150)
- `FactsReportEntry.device` now has a reverse accessor, `device.facts_entries`,
  in place of the previous `related_name="+"`. Migration `0030` is
  metadata-only and applies no schema change. (#150)
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

- A run that collected from none of the devices it attempted now finishes
  **Failed**, with the distribution in the report's error message (for
  example `0 of 12 devices collected: 8 unreachable, 4 no NAPALM driver from
  platform`), instead of finishing `Pending` like a clean detect-only run. No
  new report status is involved, and partial failure is unchanged: one device
  collected still leaves a report worth reviewing, with the shortfall visible
  in the new device counts. The `netbox_facts.report_ready` event is still
  raised for such a run, so an event rule can act on exactly that case.
  (#144)
- An empty-string `username` or `password` stored in a plan's NAPALM arguments
  no longer shadows the plugin-level credential; it now falls back to
  `napalm_username` / `napalm_password`. Clearing the plan's NAPALM username
  field removes the plan-level key, so the plan authenticates with the
  plugin-level credential again. (#149)
- The Collection Plan form's NAPALM driver dropdown now offers
  `(from device platform)` as a real first choice instead of a `---------`
  prompt, and the REST API no longer requires `napalm_driver` when creating a
  plan. Vendor dispatch inside a run follows the driver resolved for the device
  being collected rather than the plan's field. (#147)
- The quick search (`q`) on the MAC address, MAC vendor and collection plan lists now trims surrounding whitespace before matching, aligning it with the report and entry searches; a whitespace-only query returns the unfiltered list instead of matching literal spaces.
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

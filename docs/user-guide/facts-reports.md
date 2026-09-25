# Facts Reports

A `FactsReport` is created by every collection run and accumulates one
`FactsReportEntry` per detected fact. The model lives at
`netbox_facts/models/facts_report.py`.

## Report fields

| Field | Notes |
|---|---|
| `collection_plan` | FK to `CollectionPlan`. |
| `job` | FK to the `core.Job` that produced the report. Set after the run completes. |
| `status` | One of `pending`, `completed`, `partial`, `applied`, `failed`. |
| `created_by` | User who triggered the run, when available. |
| `completed_at` | Timestamp set when the report reaches a terminal status. |
| `summary` | Cached counts by action: `{new, changed, confirmed, stale}`. Recomputed by `update_summary()`. |
| `error_message` | Populated when a top-level collection failure aborts the run. |

## Entry fields

| Field | Notes |
|---|---|
| `report` | FK to the parent report. |
| `action` | `new`, `changed`, `confirmed`, or `stale`. |
| `status` | `pending`, `applying`, `applied`, `skipped`, or `failed`. `applying` is set while the entry's apply handler runs. |
| `collector_type` | The collector that produced this entry. Selects the apply handler family. |
| `entry_kind` | What kind of object the entry concerns: `device`, `interface`, `interface_mac`, `lag`, `ip_address`, `mac_address`, `vrf`, `inventory_item`, `module`, `cable`, `l2_circuit`, `bgp_peer_ip`, `bgp_router`, `bgp_scope`, `bgp_peer`, `ospf_neighbor`, or `other`. Set at detect time; selects the apply handler within the collector type. |
| `device` | The device the fact was detected on. Reachable in reverse as `device.facts_entries`. |
| `object_type` / `object_id` | Generic FK to the NetBox object the entry refers to. Nullable for `new` entries that have not been applied yet. |
| `object_repr` | Human-readable label (e.g. `Interface ge-0/0/0`, `MACAddress 00:11:22:33:44:55`). Display only -- apply never parses it. |
| `display_title` | Read-only. One-line title composed from the kind, the label and the action (e.g. `Interface xe-0/0/1 changed`). |
| `detected_values` | JSON. What the device reported. |
| `current_values` | JSON. What NetBox currently has. Empty for `new` entries. |
| `error_message` | Populated on apply failure (max 1000 chars). |
| `apply_error` | Read-only JSON. Structured form of the last apply failure: `{"<field>": ["message", ...], "error_type": "validation"}` for validation errors, `{"__all__": ["message"], "error_type": "error"}` for infrastructure failures. Cleared on a successful apply. |
| `created`, `applied_at` | Timestamps. |

## Indexes

The entry table indexes `(report, action)`, `(report, status)`,
`(report, entry_kind)`, and `(object_type, object_id)` for the common UI
filter paths.

## Status reconciliation

`FactsReport.status` is derived from its entries by
`netbox_facts.helpers.applier._update_report_status()`:

| Entry distribution | Resulting status |
|---|---|
| All `pending` (or no entries) | `Pending` |
| All `applied` | `Applied` |
| All `failed` | `Failed` |
| No `pending`, contains `applied` | `Applied` |
| No `pending`, no `applied` (mix of `skipped` / `failed`) | `Completed` |
| Otherwise | `Partial` |

`completed_at` is stamped whenever the status reaches a non-`Pending`
state.

## Notifications

A collection run raises a NetBox event as soon as it has finished writing
its report, so reviewers can be told that a report is waiting instead of
polling the list. The event type is `netbox_facts.report_ready`, shown as
**Facts report ready for review**. It is raised once per run -- started
from the UI, the API, or the scheduler alike -- after the final status and
the summary counts have been saved, and it is not raised for a run that
failed before finalizing.

Build an event rule under **Operations > Integrations > Event Rules**:

1. **Object types**: `Facts Report`.
2. **Event types**: `Facts report ready for review`.
3. **Action**: the webhook, script, or notification group that should
   carry the message (Slack, ServiceNow, email, and so on).
4. **Conditions**: optional, to narrow which reports notify you.

The payload is the report as the REST API serializes it:

| Key | Notes |
|---|---|
| `id`, `url`, `display` | Identify the report; `url` is its API path, relative to your NetBox host. |
| `collection_plan` | ID of the plan that produced the report. |
| `status` | `pending` when entries await review; `applied` for a plan that is not detect-only. |
| `summary` | Counts by action: `{"new": N, "changed": N, "confirmed": N, "stale": N}`. |
| `error_message`, `created`, `completed_at` | As stored on the report. |

`entry_count` is annotated onto the API queryset rather than stored on the
report, so it is absent from the payload; use `summary` instead.

Conditions are evaluated against that payload, so a rule that fires only
when a detect-only run found something to review looks like:

```json
{
  "and": [
    {"attr": "status", "value": "pending"},
    {"attr": "summary.changed", "op": "gt", "value": 0}
  ]
}
```

If `Facts Report` does not appear in the event rule object type picker
after an upgrade, run `python manage.py migrate` once: NetBox records the
features a model supports on its object type as part of the migration
step.

## Reviewing entries in the UI

A report's entries are split across four tabs -- Pending, Applied,
Skipped and Failed -- each badged with its count. All four are always
shown, including at zero, so the tab you are working does not move as
entries change status. The entry-status counts on the report page link to
the matching tab.

Each tab carries a filter form over `device`, `action`, `status`,
`collector_type` and `entry_kind`, plus a `q` search matching the entry
label (`object_repr`) or the device name; the quick-search box above the
table posts the same `q`. Filters can be stored as NetBox saved filters
and recalled from the selector beside the quick search.

The **Export** button writes the entries currently selected by those
filters, exactly as the NetBox object lists do: "Current View" exports the
columns you have configured, "All Data" every available column, and any
export template defined for `Facts Report Entry` is offered below those.
CSV output uses the delimiter from your user preferences, and deployments
that set `STREAMING_EXPORTS` stream the rows instead of buffering them.

## The entry detail page

Every row in a report's entry tabs links to a page for that single entry,
at `/plugins/facts/facts-report-entry/<id>/`. It is where you review a
pending change before applying it, or find out why one failed.

- **Overview** -- the entry's title, kind, action and status, the device
  and report it belongs to, the collector type that produced it, the
  NetBox object it resolves to once applied, and the detection and apply
  timestamps.
- **Changes** -- the comparison key by key, with what NetBox holds beside
  what the device reported. A key is marked `Modified` when both sides
  differ, `Added` when only the device reported it, and `Removed` when
  only NetBox still holds it; `(not set)` marks a side that holds no
  value at all. Keys whose value did not move are not listed, and a
  `confirmed` entry lists none. This is the same comparison the entry
  table's Details column summarizes in one line.
- **Raw evidence** -- the full `detected_values` and `current_values`
  payloads as stored, in collapsible blocks. They include the keys the
  Changes panel hides, such as `raw_output` and the collector's internal
  identity fields.

A `failed` entry also shows what the apply hit. A validation failure is
rendered field by field exactly as NetBox rejected it; an infrastructure
failure (an unreachable dependency, a missing handler) is shown as a
single general message. Entries written before `apply_error` existed fall
back to their flat `error_message`.

Viewing an entry requires `netbox_facts.view_factsreport`: entries carry
no permissions of their own and are visible exactly when their report is,
object-level constraints included. Breadcrumbs and the **Back to Report**
button return to the report tab the entry is listed on.

## Applying entries from the UI

A report offers two apply paths:

- **Apply Selected** (entry tabs) -- tick the pending entries you want and
  submit. The selected entries are applied inline, in the web request, and
  the result is reported immediately. Use it for a handful of entries.
- **Apply All Pending** (report detail) -- applies every pending entry in
  the report. The button submits a single flag; the server resolves the
  pending entries itself, so nothing about the report size is carried in
  the request. You are first shown a confirmation page stating how many
  entries will be applied. Nothing is mutated until you confirm.

Confirming **Apply All Pending** enqueues a background job
(`Facts Report Apply`) and returns you to the report with a message
linking to that job. Progress, log output, and the final
`{"applied": N, "failed": N}` counts are visible on the job. Only one
apply job may be queued, scheduled, or running per report: submitting
again while one is in flight is refused with a warning rather than
queueing a second pass over the same entries.

Both paths require the `netbox_facts.apply_factsreport` permission.

### Retrying and un-skipping

Resolving an entry is not final. Each entry tab offers only the
transitions its entries can make:

| Tab | Controls |
|---|---|
| Pending | **Apply Selected**, **Skip Selected** |
| Failed | **Retry Selected** |
| Skipped | **Un-skip Selected** |
| Applied | none |

**Retry** returns the selected failed entries to pending, clears the
recorded failure (`error_message` and `apply_error`), and re-applies them
in the same request, so a retry that fails again shows the new error
rather than the old one. Fix whatever NetBox rejected -- a missing VRF, a
cable that is already connected -- and retry the entry rather than
re-running the whole collection.

**Un-skip** returns the selected skipped entries to pending without
applying anything; they reappear on the Pending tab for review.

Both are gated on `netbox_facts.apply_factsreport`, like apply and skip.

### Per-row and cross-page selection

Every row carries the shortcuts for its own status -- apply and skip on a
pending row, retry on a failed one, un-skip on a skipped one -- so a
single entry can be resolved without ticking a checkbox first. A row
button acts on that row only, even when other rows are ticked.

When a tab spans more than one page, ticking the header checkbox reveals
a **Select all N matching entries** option. Submitting with it ticked
sends only the flag: the server re-resolves the selection from the
report, the tab's status, and the filters currently applied to the tab,
so the action covers every matching entry rather than just the visible
page. The transition itself is still gated by status -- a select-all
retry only touches entries that actually failed.

## Device page integration

Reports are organised by collection run, but operators work device by
device. Two entry points bring the review queue to them.

### The Facts tab

A device the plugin has recorded facts for carries a **Facts** tab, badged
with the number of entries for that device still awaiting a decision. The
tab shows:

- **Pending entries** -- the same table the report tabs use, filtered to
  this device. Entries are reviewed from their report, so the tab is a
  reading view: apply and skip stay on the report page.
- **Last Collected** -- the most recent collection timestamp per collector
  type, taken from the reports that produced this device's own entries. It
  answers "when was this device last seen by an ARP run", not "when did
  some ARP plan last run".
- **Collection Plans** -- the enabled plans whose scope currently resolves
  to this device, with each plan's last run.

The tab is visible to users holding `netbox_facts.view_factsreport`, and
appears only once the plugin holds at least one entry for the device, so it
does not clutter the pages of devices no plan has ever collected. It is
deliberately keyed off "has any entry", not "has a pending entry": a device
that has been collected and is simply clean still shows the tab with a `0`
badge, because that is exactly the device whose freshness and plan-coverage
panels are worth reading.

The listed entries respect object-level permissions; the tab's badge does
not, because NetBox hands a tab badge only the object it is counting for. A
user restricted to a subset of entries can therefore see a badge higher
than the rows below it.

A plan's scope is a set of assignment dimensions rather than a stored
device list, so answering "does this plan cover this device" means
resolving the plan. The tab caps how many enabled plans it resolves for
one page view and says so when the cap is reached, rather than letting a
deployment with hundreds of plans turn a device page into a sweep.

### The dashboard widget

**Pending Facts Changes** is a dashboard widget, available from the widget
picker on the NetBox home page like any other. It shows two numbers -- the
total entries awaiting a decision and the reports holding them -- and both
link to the report list filtered to the reports awaiting review (status
`pending` or `partial`). Counts respect the viewing user's object
permissions.

## REST endpoints

- `GET /api/plugins/facts/factsreports/` -- list/filter reports.
- `GET /api/plugins/facts/factsreports/<id>/` -- single report.
- `POST /api/plugins/facts/factsreports/<id>/apply/` -- apply selected
  pending entries. Body: `{"entries": [pk, ...]}`.
- `POST /api/plugins/facts/factsreports/<id>/skip/` -- bulk-skip selected
  pending entries. Same body.
- `POST /api/plugins/facts/factsreports/<id>/retry/` -- return selected
  failed entries to pending and re-apply them. Same body; responds with
  `{"applied": N, "failed": N}`.
- `POST /api/plugins/facts/factsreports/<id>/unskip/` -- return selected
  skipped entries to pending without applying them. Same body; responds
  with `{"unskipped": N}`.
- `GET /api/plugins/facts/factsreportentries/` -- list/filter entries.
- `GET /api/plugins/facts/factsreportentries/<id>/` -- single entry.

The `apply`, `skip`, `retry`, and `unskip` endpoints validate that all
submitted entry PKs belong to the report (returns `400` if not) and are
throttled to 30 requests per minute per user. Each one acts only on the
entries in the status it applies to and ignores the rest, so a mixed
selection is safe.

The entry endpoint is read-only: entries are produced by a collection run
and resolved through the report-level lifecycle actions, never created or
edited directly. It is how an API client discovers the entry PKs to pass
in those request bodies, for example:

```
GET /api/plugins/facts/factsreportentries/?report=12&status=pending
```

Supported filters are `q`, `report`, `action`, `status`,
`collector_type`, `entry_kind`, and `device`. `q` matches a substring of
`object_repr` or of the device name; `action`, `status`,
`collector_type`, `entry_kind`, and `device` accept multiple values
(repeat the parameter). Results are limited to the entries the requesting
user is permitted to view.

## GraphQL

The plugin contributes five object types to NetBox's GraphQL schema. Type
names share a global namespace with core and every other plugin, so each
one carries a `Facts` prefix:

| Type | Query fields |
|---|---|
| `FactsMACAddressType` | `facts_mac_address`, `facts_mac_address_list` |
| `FactsMACVendorType` | `facts_mac_vendor`, `facts_mac_vendor_list` |
| `FactsCollectionPlanType` | `facts_collection_plan`, `facts_collection_plan_list` |
| `FactsReportType` | `facts_report`, `facts_report_list` |
| `FactsReportEntryType` | `facts_report_entry`, `facts_report_entry_list` |

`napalm_args` is excluded from `FactsCollectionPlanType`: it holds
connection credentials, which are never exposed through GraphQL. Queries
are filtered by the requesting user's object permissions, the same as the
REST endpoints.

## Filters

The list view supports these filters via `FactsReportFilterSet`:

- `q` -- substring match on `collection_plan__name`.
- `collection_plan` -- one or more plan IDs.
- `status` -- one or more `ReportStatusChoices` values.

The entry tabs (within a report) support `q` -- a substring match on
`object_repr` or on the device name -- plus `device`, `action`, `status`,
`collector_type`, and `entry_kind`, via `FactsReportEntryFilterSet` and
the filter form each tab renders.

Both filtersets build on NetBox's `BaseFilterSet`, so saved filters and
the standard lookup expressions apply. Neither builds on
`NetBoxModelFilterSet`: reports and entries are plain models with no
tags, custom fields, or change log for it to filter on.

## Retention

Reports are kept forever unless retention is enabled. Setting
`report_retention_days` to a positive number of days in
`PLUGINS_CONFIG["netbox_facts"]` activates the **Facts Report Retention**
system job, which runs daily and deletes reports created more than that many
days ago, along with their entries.

Reports that still hold at least one `pending` entry are exempt regardless of
age, so retention never discards work awaiting review. Apply or skip the
outstanding entries and the report becomes eligible on the next pass. See
[Configuration](../getting-started/configuration.md) for the setting itself.

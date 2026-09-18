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
| `status` | `pending`, `applied`, `skipped`, or `failed`. |
| `collector_type` | The collector that produced this entry. Determines which apply handler is dispatched. |
| `device` | The device the fact was detected on. |
| `object_type` / `object_id` | Generic FK to the NetBox object the entry refers to. Nullable for `new` entries that have not been applied yet. |
| `object_repr` | Human-readable label (e.g. `Interface ge-0/0/0`, `MACAddress 00:11:22:33:44:55`). |
| `detected_values` | JSON. What the device reported. |
| `current_values` | JSON. What NetBox currently has. Empty for `new` entries. |
| `error_message` | Populated on apply failure (max 1000 chars). |
| `created`, `applied_at` | Timestamps. |

## Indexes

The entry table indexes `(report, action)`, `(report, status)`, and
`(object_type, object_id)` for the common UI filter paths.

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

## REST endpoints

- `GET /api/plugins/facts/factsreports/` -- list/filter reports.
- `GET /api/plugins/facts/factsreports/<id>/` -- single report.
- `POST /api/plugins/facts/factsreports/<id>/apply/` -- apply selected
  pending entries. Body: `{"entries": [pk, ...]}`.
- `POST /api/plugins/facts/factsreports/<id>/skip/` -- bulk-skip selected
  pending entries. Same body.
- `GET /api/plugins/facts/factsreportentries/` -- list/filter entries.
- `GET /api/plugins/facts/factsreportentries/<id>/` -- single entry.

The `apply` and `skip` endpoints validate that all submitted entry PKs
belong to the report (returns `400` if not) and are throttled to 30
requests per minute per user.

The entry endpoint is read-only: entries are produced by a collection run
and resolved through the report-level `apply` and `skip` actions, never
created or edited directly. It is how an API client discovers the entry
PKs to pass in those request bodies, for example:

```
GET /api/plugins/facts/factsreportentries/?report=12&status=pending
```

Supported filters are `report`, `action`, `status`, `collector_type`, and
`device`. `action`, `status`, and `collector_type` accept multiple values
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

The entry list (within a report) supports `action`, `status`,
`collector_type`, and `device`.

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

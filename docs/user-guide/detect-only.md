# Detect-Only Workflow

Detect-only mode lets a collection plan inspect devices and produce a
review-able report without changing NetBox. It is the recommended mode for
new plans, especially in production.

## Enabling

Set the `detect_only` flag on the `CollectionPlan` (UI checkbox, or
`detect_only=true` via the REST API).

When enabled:

- The collector still connects to every in-scope device.
- For each detected fact it creates a `FactsReportEntry` with one of four
  actions and `status = pending`.
- It does **not** create or modify NetBox objects.

When disabled (apply mode):

- The collector creates entries the same way, then marks each one
  `applied` immediately after the corresponding NetBox object is written.
- The final report status is `Applied`.

## Action types

`FactsReportEntry.action` is one of:

| Action | Meaning |
|---|---|
| `new` | The fact was detected on the device and no matching object exists in NetBox yet. |
| `changed` | A matching object exists in NetBox but its values differ from what the device reports. |
| `confirmed` | The fact matches NetBox exactly. Entries are recorded for visibility; applying them is a no-op for most collectors. |
| `stale` | The object exists in NetBox (and carries the **Automatically Discovered** tag) but was not seen during this run. Applying it removes or unassigns the object. |

## Entry status

`FactsReportEntry.status` tracks the apply lifecycle:

| Status | Meaning |
|---|---|
| `pending` | Default. The entry has not been applied or skipped. |
| `applied` | The per-collector handler ran successfully. `applied_at` is set. |
| `skipped` | A reviewer chose not to apply this entry. |
| `failed` | The handler raised an exception; `error_message` holds the truncated reason (1000 chars). The savepoint for this entry was rolled back; other entries are unaffected. |

## Reviewing a report

You do not have to watch the report list: a finished run raises the
`netbox_facts.report_ready` event ("Facts report ready for review"), so an
event rule can push the summary counts to a webhook, script, or
notification group the moment a report is ready. See
[Notifications](facts-reports.md#notifications).

Open **Operational Facts -> Facts Reports** and pick a report. The page
shows:

- A summary card with counts by action (cached on
  `FactsReport.summary`).
- The full entry list with filters for `action`, `status`,
  `collector_type`, and `device`.
- For each entry: `object_repr`, `detected_values`, `current_values`, and
  any error message.

`object_repr` is a human-readable label such as
`MACAddress aa:bb:cc:11:22:33`, `InventoryItem PIC 0`, or
`Cable ge-0/0/0 <-> switch2:ge-0/0/4`. It is constructed by
`NapalmCollector._object_repr()`.

## Applying or skipping entries

Tick one or more pending entries and use the **Apply selected** or
**Skip selected** action. Both act on the entries you ticked and run
inline, in the web request.

To apply a whole report, use **Apply All Pending** on the report detail
page. It asks for confirmation first, showing how many entries will be
applied, then hands the work to a background job (`Facts Report Apply`)
that resolves the report's pending entries server-side and calls the same
`apply_entries()` helper. You are returned to the report with a link to
the job; a report can have only one apply job in flight at a time.

Apply runs through `apply_entries()` in
`netbox_facts/helpers/applier.py`:

1. Fetch the requested pending entries scoped to the report.
2. Open an outer atomic block.
3. For each entry:
    - Open a per-entry savepoint.
    - Look up the collector-type-specific handler in `APPLY_HANDLERS`.
    - Run the handler. On success, set `status=applied` and
      `applied_at=now`.
    - On exception, mark `status=failed`, save the truncated error, and
      release the inner savepoint so the outer transaction continues.
4. After the loop, recompute and persist the report status.

The same path is exposed over the REST API:

```http
POST /api/plugins/facts/factsreports/<id>/apply/
Content-Type: application/json
Authorization: Token <token>

{"entries": [12, 13, 19]}
```

Response: `{"applied": 2, "failed": 1}`.

Skip is similar but does not invoke handlers; it just bulk-updates
`status=skipped`.

## Report status transitions

`_update_report_status()` derives the report status from its entries:

- All `pending` -> `Pending`.
- All `applied` -> `Applied` (and `completed_at` is set).
- All `failed` -> `Failed`.
- No `pending` left, mix of `applied` and others -> `Applied`.
- No `pending` left, no `applied` -> `Completed`.
- Otherwise -> `Partial`.

## Permissions

Applying or skipping requires the `netbox_facts.apply_factsreport`
permission. Grant it explicitly to the operators who should be allowed to
mutate NetBox from a report; viewing a report only requires
`netbox_facts.view_factsreport`.

## Why a savepoint per entry

A handler can fail for many independent reasons (a duplicate IP, a missing
ModuleType, an interface that has gained a cable since collection). The
per-entry savepoint guarantees that one bad entry does not undo every
successful apply that came before it in the same batch.

## Working as a team

Detect-only mode turns review into a recurring task rather than a one-off
migration step. A few practices keep that sustainable as the number of
monitored devices grows:

- **Start with a segment you understand.** Scope the first Collection
  Plans to a well-known slice of the network -- 10 to 20 devices is enough
  to see real `changed` and `stale` entries without producing a queue
  bigger than a reviewer can get through. Widening scope later is just
  adding devices, sites, or roles to the plan; nothing about starting
  narrow has to be undone.
- **Set a review cadence.** Decide whether reports get reviewed daily or
  weekly, and set each plan's **Interval (minutes)** (see
  [Scheduling and Jobs](scheduling.md)) to match, so reports land on a
  schedule reviewers can plan around instead of arriving at random.
- **Give the review queue an owner.** Grant
  `netbox_facts.apply_factsreport` to whoever is responsible for triage,
  and treat the **Operational Facts -> Facts Reports** list, filtered to
  pending entries, as an owned queue -- the same way an unassigned ticket
  queue would be treated. `netbox_facts.view_factsreport` is enough for
  the rest of the team to watch the queue without being able to mutate
  NetBox.
- **Treat recurring drift as a process signal, not just a chore.** An
  entry that comes back `changed` or `stale` against the same object on
  every run usually means NetBox and the device disagree about who owns
  that value: a manual edit that keeps getting overwritten, a
  decommission that was never reflected in NetBox, or a plan scoped to
  the wrong device. A report that never reaches zero pending entries is a
  prompt to fix that underlying disagreement, not a report to keep
  skipping.

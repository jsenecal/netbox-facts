# Collection Plans

A `CollectionPlan` is the central object that ties together:

- which devices to collect from;
- which collector to run;
- which NAPALM driver and credentials to use;
- whether to mutate NetBox or only produce a report;
- whether to run once or on an interval.

The model lives at `netbox_facts/models/collection_plan.py`.

## Identity and lifecycle

| Field | Notes |
|---|---|
| `name` | Unique. Free-form. |
| `priority` | One of `high`, `default`, `low`. Maps directly to the RQ queue used at enqueue time. |
| `status` | Plugin-managed. One of `new`, `queued`, `working`, `completed`, `scheduled`, `failed`, `stalled`. |
| `enabled` | If `False`, recurring schedules are removed and the plan cannot enqueue. |
| `description` / `comments` | Free-form text. |
| `run_as` | Optional user. When set and the requesting user is a superuser, the job runs as this user. |

`stalled` is set automatically by `CollectionPlan.check_stalled()` when the
plan is `working` but no live job exists.

## Device scoping

The plan exposes the following many-to-many fields that
`get_devices_queryset()` ANDs together to resolve the device list:

- `devices`, `regions`, `site_groups`, `sites`, `locations`
- `device_types`, `roles`, `platforms`
- `tenant_groups`, `tenants`
- `tags`

Plus an `ArrayField` of `device_status` values from
`dcim.choices.DeviceStatusChoices`. When empty, no status filter is
applied.

## Collector type and driver

| Field | Notes |
|---|---|
| `collector_type` | One of the values in `CollectionTypeChoices`. See [Collectors Overview](../collectors/index.md). |
| `napalm_driver` | A NAPALM driver name (e.g. `junos`, `ios`, `eos`). Resolved by `get_network_driver()`; the plugin first tries `netbox_facts.napalm.<name>` so internal vendor overrides win, then falls back to upstream. |
| `napalm_args` | JSON merged on top of the plugin-level `global_napalm_args`. Special keys `username` and `password` are extracted before the rest is passed as `optional_args`. |

## Connection target

`connection_target` controls dial order:

| Value | Behavior |
|---|---|
| `primary` | Use `device.primary_ip` only. |
| `oob` | Use `device.oob_ip` only. |
| `primary_then_oob` | Try primary; on `ConnectionException`, try OOB. |
| `oob_then_primary` | Try OOB; on `ConnectionException`, try primary. |

The IP list is resolved by
`netbox_facts.helpers.netbox.get_connection_ips()`. A device with no
usable IP for the chosen target is logged as a warning and skipped.

## Detect-only vs apply mode

`detect_only=True` makes the collector record `FactsReportEntry` rows
without mutating NetBox. The report is finalized with status `Pending` and
entries can be applied selectively from the UI or REST API.

`detect_only=False` writes directly. A `FactsReport` is still created so
every applied object has a record; in this mode entries are marked
`applied` immediately. The final report status becomes `Applied`.

See [Detect-Only Workflow](detect-only.md) for the full apply flow.

## Scheduling

Scheduling is driven entirely by the `interval` field plus the plan's
`enabled` flag:

- `interval` blank: the plan does not auto-schedule. Manual runs only.
- `interval = N`: every save schedules `CollectionJobRunner.enqueue_once()`
  to run every N minutes via NetBox's `JobRunner` framework.
- `enabled = False`: the `post_save` signal cancels any pending scheduled
  job for the plan.

Implementation: see `handle_collection_job_change()` in
`netbox_facts/signals.py`.

## Logs and history

Each run records a structured log on the `Job.data["log"]` field
(persisted by `CollectionJobRunner.run()`) and the `last_run` timestamp on
the plan. Each run also creates exactly one `FactsReport`, linked from
`plan.reports`.

## Cloning

The plan declares `clone_fields` so the **Clone** action in the UI
preserves scoping, driver, args, schedule, detect-only, and connection
target. Name, status, and last_run are reset.

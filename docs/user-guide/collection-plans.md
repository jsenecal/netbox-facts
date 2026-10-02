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

### Empty-scope guard

A plan with no scoping dimension at all resolves to *every* device in
NetBox, which is rarely what anyone means. `CollectionPlan.clean()`
therefore rejects a plan when every many-to-many field above is empty
*and* `device_status` is empty. The same guard runs on the edit form, the
CSV import, and the REST API, so a plan cannot be created scopeless by any
route.

Fleet-wide plans stay possible: tick **Allow unscoped**
(`allow_unscoped`, default `False`) to opt out of the guard. The field
exists so that targeting the whole fleet is a deliberate, reviewable
choice rather than an oversight.

The guard runs on save, not on run, so a plan that predates it keeps
running as before. The next time such a plan is edited, it has to either
gain a scope or have **Allow unscoped** ticked.

### Scope preview and readiness

The plan detail page shows a **Resolved scope** panel:

- **Matched devices** -- the number of devices `get_devices_queryset()`
  currently resolves to, linking to the device list filtered by the plan's
  scope. The link is a browsing aid, not the queryset: the device list ANDs
  multiple tags and includes the descendants of a selected region, site
  group, location or tenant group, while the plan ORs tags and matches
  those objects exactly. When any dimension pins more than 100 objects, the
  count is shown unlinked instead, since that many pks would no longer fit
  in a URL.
- **Connection target** -- the address the run will dial.
- **Missing a usable IP** -- how many matched devices have no address for
  that target, with the first few named in a tooltip. These are exactly the
  devices a run would skip with a warning, so the count should normally be
  zero before the first run.

The Assignment panel lists at most ten objects per dimension and reports
the remainder as a count, so a plan pinning thousands of devices does not
produce an unbounded page.

The edit form shows the same resolved count for a saved plan
("Currently matched"), refreshed on save, and every save reports the
resolved count as a message.

### Scope size warning

Saving a plan whose scope resolves to more devices than the
`scope_warning_threshold` plugin setting (default `500`) produces a warning
message; the plan is still saved. Set the threshold to `0` to disable the
warning. See [Configuration](../getting-started/configuration.md).

## Collector type and driver

| Field | Notes |
|---|---|
| `collector_type` | One of the values in `CollectionTypeChoices`. See [Collectors Overview](../collectors/index.md). |
| `napalm_driver` | Optional. A NAPALM driver name (e.g. `junos`, `ios`, `eos`) forced on every device in the plan's scope. Leave it blank -- `(from device platform)` in the form -- to resolve the driver per device instead. |
| `napalm_args` | JSON merged on top of the plugin-level `global_napalm_args`. Special keys `username` and `password` are extracted before the rest is passed as `optional_args`. |

The edit form fills the `username`, `password` and `secret` keys from a
dedicated **Credentials** fieldset rather than from the `napalm_args` JSON
box, so credentials are never typed into (or echoed from) the raw JSON;
see [per-plan credentials](../getting-started/configuration.md#per-plan-credentials).

### Driver resolution

A driver name is turned into a driver class the same way whether it came
from the plan or from a platform: the plugin first tries
`netbox_facts.napalm.<name>` so its enhanced vendor drivers win, then falls
back to upstream `get_network_driver()`, which also finds community
`napalm_<name>` packages.

Where the *name* comes from depends on whether the plan sets one:

- **`napalm_driver` set** -- that driver is used for every device in scope.
  This is the override, and it is what a single-vendor plan wants.
- **`napalm_driver` blank** -- the driver is resolved per device from
  `device.platform`, so one plan can span several vendors.

NetBox dropped `Platform.napalm_driver` in 3.6 together with the rest of
core NAPALM support, and 4.x offers no replacement field, so the
platform-to-driver mapping is this plugin's own convention:

1. A custom field on `dcim.Platform` named by the
   `platform_driver_custom_field` plugin setting (default `napalm_driver`).
   Create it as a **text** or **selection** custom field assigned to the
   Platform object type, and set it to a driver name such as `junos`. An
   unset or blank value falls through to the next step.
2. Otherwise the platform's **slug**, which already reads as a driver name
   on the usual platforms (`junos`, `ios`, `eos`, `nxos`, `iosxr`).

The mapping is read off the platform the device points at directly; it is
not inherited from a parent platform, so a nested platform such as
`junos-21.4R3` needs its own custom-field value (its slug is not a driver
name).

A device the convention yields nothing usable for is **skipped**, with a
warning naming the reason on the plan's run log and a tally in the run
summary line. The run itself continues. Three cases skip a device:

| Case | Log reason |
|---|---|
| The device has no platform and the plan names no driver | `no NAPALM driver from platform` |
| The resolved name matches no installed driver | `NAPALM driver not installed` |
| The resolved driver has no implementation for this collector | `driver unsupported by this collector` |

The last case is the run-time half of collector/driver compatibility; see
[Collectors Overview](../collectors/index.md). Resolution happens before
any network access, so a device that cannot be collected from costs no
connection to discover.

One thing a mixed-vendor plan does *not* get is per-vendor connection
options: `napalm_args` (and the credentials in it) are per plan, so every
driver the plan resolves receives the same `optional_args`. Keep separate
plans when the vendors need different transports, ports or credentials.

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

## Stale grace period

| Field | Notes |
|---|---|
| `stale_grace_days` | Optional. Days an object this plan no longer finds is marked **Orphaned (netbox-facts)** before its removal is proposed. Blank -- the default -- follows the `stale_grace_period_days` plugin setting. `0` means no grace period for this plan, whatever the setting says: a missing object's removal is proposed on the first run that does not find it. |

The field is on the **Collector** fieldset of the edit form, next to
**Detect only**, and the plan page shows the period in force along with
whether it came from the plan or from the plugin configuration.

Both modes honor the period, and they differ only in what happens at the
end of it: a detect-only plan records the `stale` entry for review, an
applying plan performs the removal. Either way, the first absence only
marks the object, so a pending removal is visible on the object itself --
and in any list view filtered on the tag -- before anything is lost.

Applying a `stale` entry settles the absence: the tag comes off and the
plan's record of it is dropped. Skipping one does not -- a skip is a
decision about the entry, not about the object -- so the object stays
marked and the clock keeps running.

See [Stale grace period](../getting-started/configuration.md#stale-grace-period)
for the lifecycle in full.

## Scheduling

Scheduling is driven by the `interval` and `cron_schedule` fields, the
`scheduled_at` start time, and the plan's `enabled` flag:

- `interval` and `cron_schedule` both blank: the plan does not
  auto-schedule. Manual runs only, unless `scheduled_at` is set -- which
  schedules exactly one run at that time.
- `interval = N`: runs every N minutes via NetBox's `JobRunner`
  framework, starting at `scheduled_at` when that time is still ahead.
- `cron_schedule = <expression>`: runs at each firing of a five-field
  cron expression. Mutually exclusive with `interval`.
- `enabled = False`: the `post_save` signal cancels any future-dated job
  for the plan.

The plan's **Next run** is computed from these fields and `last_run`, and
is shown on the detail page, the plan list, and the REST API.

Implementation: see `handle_collection_job_change()` in
`netbox_facts/signals.py`.

## Logs and history

Each run records a structured log on the `Job.data["log"]` field
(persisted by `CollectionJobRunner.run()`) and the `last_run` timestamp on
the plan. Each run also creates exactly one `FactsReport`, linked from
`plan.reports`.

## Cloning

The plan declares `clone_fields` so the **Clone** action in the UI
preserves scoping (including the `allow_unscoped` opt-out), driver, args,
schedule, detect-only, and connection target. Name, status, and last_run
are reset.

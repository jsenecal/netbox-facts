# Configuration

All plugin-wide options live under `PLUGINS_CONFIG["netbox_facts"]` in your
NetBox configuration. The defaults match what `FactsConfig.default_settings`
declares in `netbox_facts/__init__.py`.

## Settings reference

| Setting | Type | Default | Description |
|---|---|---|---|
| `top_level_menu` | bool | `True` | Render the plugin as an **Operational Facts** top-level menu. When `False`, entries appear under **Plugins**. |
| `napalm_username` | str | `""` | Default NAPALM username for device connections, used by every plan that does not set its own. A plan with no username here and none of its own is refused when it is run (see [per-plan credentials](#per-plan-credentials)). |
| `napalm_password` | str | `""` | Default NAPALM password, used by every plan that does not set its own. Left empty, an empty password is passed to NAPALM and each device connection fails on authentication; only a missing username is refused up front. |
| `napalm_timeout` | int | `60` | Connection timeout passed to the NAPALM driver as `optional_args["timeout"]` when the per-plan `napalm_args` does not already set it. |
| `global_napalm_args` | dict | `{}` | Extra NAPALM `optional_args` merged into every plan. The plan's own `napalm_args` overrides matching keys. |
| `platform_driver_custom_field` | str | `"napalm_driver"` | Name of the `dcim.Platform` custom field consulted for a NAPALM driver name when a Collection Plan leaves `napalm_driver` blank. Create it as a text or selection custom field on the Platform object type. When it is unset or blank on a platform, the platform's slug is used instead. Set this setting to `""` to use slugs only. See [Driver resolution](../user-guide/collection-plans.md#driver-resolution). |
| `valid_interfaces_re` | str | `".*"` | Regex applied to interface names by collectors that walk per-interface tables (ARP, NDP, interfaces, ethernet switching). Interfaces whose name does not match are skipped. |
| `job_timeout` | int | `1800` | Maximum runtime in seconds passed to RQ when enqueuing a `CollectionJobRunner` job. |
| `report_retention_days` | int | `0` | Age in days after which Facts Reports are deleted by the daily retention job. `0` disables pruning and keeps every report forever. Reports holding pending entries are never deleted, whatever their age. |
| `scope_warning_threshold` | int | `500` | Number of devices above which saving a Collection Plan warns that its scope is large. The plan is still saved; `0` disables the warning. See [Device scoping](../user-guide/collection-plans.md#device-scoping). |
| `stale_grace_period_days` | int | `0` | Days an object a run no longer finds is marked **Orphaned (netbox-facts)** before its removal is proposed. `0` keeps the historical behavior: the first run that does not find an object proposes (or performs) its removal. A plan can override this with its own `stale_grace_days`. See [Stale grace period](#stale-grace-period). |

## Example

```python
PLUGINS_CONFIG = {
    "netbox_facts": {
        "top_level_menu": True,
        "napalm_username": "netbox-collector",
        "napalm_password": "use-secrets-management-here",
        "napalm_timeout": 90,
        "global_napalm_args": {
            "port": 22,
            "keepalive": 30,
        },
        "valid_interfaces_re": r"^(ge|xe|et|ae|et|lo|irb|vlan)\S*$",
        "job_timeout": 3600,
        "report_retention_days": 90,
    },
}
```

## Per-plan credentials

Each Collection Plan carries its own credentials in a **Credentials**
section on the edit form:

| Field | Purpose |
|---|---|
| **NAPALM username** | Username this plan connects with. |
| **NAPALM password** | Password this plan connects with. |
| **NAPALM enable secret** | Enable / privileged-mode secret, passed to the driver as `optional_args["secret"]`. Only some drivers use it. |

A field left blank falls back to the plugin-level settings
(`napalm_username`, `napalm_password`), so a plan only needs these filled
in when it must authenticate differently from the rest of the fleet.

Blank means something slightly different for the username than for the
two secrets, because only the username field can show you what it holds:

- **NAPALM username** renders its stored value. Clearing the field drops
  the plan's own username, and the plan goes back to the plugin-level
  `napalm_username`.
- **NAPALM password** and **NAPALM enable secret** never render a stored
  value. When one is set, the field shows `********` as a placeholder, and
  leaving it blank keeps the stored value -- a blank field there cannot be
  told from an untouched one, so saving a plan never silently wipes its
  password. To clear one, remove its key from the **NAPALM arguments**
  JSON field.

Resolution order -- the same one the collector and the pre-run check
share:

1. the plan's own credential fields (stored in its `napalm_args`);
2. `global_napalm_args` from the plugin configuration;
3. the plugin-level `napalm_username` / `napalm_password` settings.

A plan that resolves no username from any of the three is refused when it
is run, with "no NAPALM credentials are configured for this plan",
instead of failing once per device deep in the job log.

### The JSON path (REST API and bulk import)

The credential fields are a front end for three keys in the plan's
`napalm_args` JSON, which remains the supported path for the REST API and
for bulk import. Cloning is the exception: a cloned plan deliberately
starts with the original's other driver options and no credentials,
because NetBox renders cloned attributes into the creation link's
querystring.

```json
{
    "username": "collector-user",
    "password": "collector-pass",
    "secret": "enable-secret"
}
```

`username` and `password` are consumed as the driver's positional
credentials and never reach `optional_args`; every other key -- `secret`
included -- is passed through to the driver as `optional_args`, so
credentials cannot interfere with driver options. All three values are
censored as `********` in REST API responses and on the edit form, and
submitting a censored value back preserves the stored one. See
`resolve_napalm_credentials()` in `netbox_facts/helpers/napalm.py` for the
resolution order both the collector and the pre-run check use.

## Connection target

Each plan also has a **Connection target** field (`connection_target`) that
controls which device IP address the collector dials:

| Value | Behavior |
|---|---|
| `primary` | Use `device.primary_ip` only. |
| `oob` | Use `device.oob_ip` only. |
| `primary_then_oob` | Try the primary IP first; on `ConnectionException`, fall back to OOB. |
| `oob_then_primary` | Try the OOB IP first; on `ConnectionException`, fall back to the primary. |

The "both" options are useful when devices are reachable via either path
depending on network conditions. Each attempt logs the IP and the label
(`primary` / `oob`) being used.

## Permissions

The plugin ships standard Django permissions for each model
(`view_*`, `add_*`, `change_*`, `delete_*`) plus custom permissions for
actions that are not plain CRUD:

- `netbox_facts.apply_factsreport` -- required to apply or skip pending
  entries on a `FactsReport`.
- `netbox_facts.run_collector` -- required to trigger a run of a
  `CollectionPlan` (the "Run" button and the run view).
- `netbox_facts.view_collector_results` -- required to view the Results
  tab on a `CollectionPlan`, which shows the outcome of its most recent
  run.

Grant these permissions via the standard NetBox permission system to the
user or group that should be allowed to mutate NetBox from a detect-only
run, trigger collection runs, or review run results.

`apply_factsreport` and `run_collector` gate the UI views only. The REST
API does not check them: NetBox's token permission class maps every `POST`
to the model's standard `add_*` permission, so the report-level actions
(`apply`, `skip`, `retry`, `unskip`) require `netbox_facts.add_factsreport`
and `CollectionPlan`'s `run` action requires
`netbox_facts.add_collectionplan` -- see the REST endpoints section of
[Facts Reports](../user-guide/facts-reports.md#rest-endpoints) for the
pinned behavior.

## Job timeout vs NAPALM timeout

These are independent:

- `napalm_timeout` (default 60s) bounds a single NAPALM RPC call.
- `job_timeout` (default 1800s / 30 min) bounds the entire RQ job that
  iterates every device in a plan.

If a plan covers many devices, `job_timeout` is the value to raise.

## Stale grace period

A stale sweep believes the run it is part of. One flapping collection -- a
device briefly unreachable mid-run, a transceiver reseated between two
passes -- is otherwise enough to unassign an address or delete a module
that is still there.

With `stale_grace_period_days` set to a positive number, a missing object
goes through two stages instead of one:

1. The first run that does not find it records when it went missing and
   tags it **Orphaned (netbox-facts)**. No `stale` entry is recorded and
   nothing is removed. Every later run that still cannot find it confirms
   the absence.
2. Once it has been missing for the whole period, the sweep proceeds
   exactly as it always did: a detect-only plan records the `stale` entry
   for review, and an applying plan performs the removal.

An object the run finds again loses the tag and its record, so the clock
starts from zero the next time it goes missing.

The default is `0`, which disables the grace period entirely: no records
are written and every sweep behaves exactly as it did before the setting
existed. Each plan can override the setting with its own
`stale_grace_days` field -- blank follows the setting, `0` opts that plan
out of a fleet-wide grace period. See
[Stale grace period](../user-guide/collection-plans.md#stale-grace-period).

The tag is created by a plugin migration and looked up by its slug
(`netbox-facts-orphaned`), so it is safe to recolour or re-describe it.
Filtering a list view on it is the quickest way to see what is on its way
out before anything is removed.

## Report retention

An interval-scheduled plan creates one `FactsReport` per run, so a plan on a
15-minute interval accumulates roughly 35,000 reports per year. Retention is
opt-in: with the default `report_retention_days = 0` nothing is ever deleted
automatically.

Set the value to a positive number of days to enable the **Facts Report
Retention** job. It is registered with NetBox as a system job and runs once
per day from the RQ worker; no cron entry or extra configuration is needed.
The schedule itself is created when the RQ worker starts, so restart the
worker after installing or upgrading the plugin for the job to appear.
Each pass deletes the reports (and their entries, by cascade) that were
created strictly more than `report_retention_days` ago. A report aged exactly
that many days is kept until the next pass.

Two safeguards apply:

- Reports with at least one entry still in the `pending` status are never
  deleted, however old they are, so a detect-only backlog awaiting review
  cannot age out.
- Reports with no recorded creation timestamp are never selected.

The job logs the number of deleted reports and the cutoff timestamp at
`INFO` under the `netbox_facts.retention` logger and in the job's own log.
When retention is disabled the job exits without touching the database.

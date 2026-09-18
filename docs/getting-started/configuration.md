# Configuration

All plugin-wide options live under `PLUGINS_CONFIG["netbox_facts"]` in your
NetBox configuration. The defaults match what `FactsConfig.default_settings`
declares in `netbox_facts/__init__.py`.

## Settings reference

| Setting | Type | Default | Description |
|---|---|---|---|
| `top_level_menu` | bool | `True` | Render the plugin as an **Operational Facts** top-level menu. When `False`, entries appear under **Plugins**. |
| `napalm_username` | str | `""` | Default NAPALM username for device connections. If left empty and not overridden per plan, an empty username is passed to NAPALM and the connection fails per device. |
| `napalm_password` | str | `""` | Default NAPALM password. If left empty and not overridden per plan, an empty password is passed to NAPALM and the connection fails per device. |
| `napalm_timeout` | int | `60` | Connection timeout passed to the NAPALM driver as `optional_args["timeout"]` when the per-plan `napalm_args` does not already set it. |
| `global_napalm_args` | dict | `{}` | Extra NAPALM `optional_args` merged into every plan. The plan's own `napalm_args` overrides matching keys. |
| `valid_interfaces_re` | str | `".*"` | Regex applied to interface names by collectors that walk per-interface tables (ARP, NDP, interfaces, ethernet switching). Interfaces whose name does not match are skipped. |
| `job_timeout` | int | `1800` | Maximum runtime in seconds passed to RQ when enqueuing a `CollectionJobRunner` job. |
| `report_retention_days` | int | `0` | Age in days after which Facts Reports are deleted by the daily retention job. `0` disables pruning and keeps every report forever. Reports holding pending entries are never deleted, whatever their age. |
| `scope_warning_threshold` | int | `500` | Number of devices above which saving a Collection Plan warns that its scope is large. The plan is still saved; `0` disables the warning. See [Device scoping](../user-guide/collection-plans.md#device-scoping). |

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

Each Collection Plan has a **NAPALM arguments** JSON field that is merged on
top of `global_napalm_args`. To override the username and password for a
specific plan, include `username` and `password` keys:

```json
{
    "username": "collector-user",
    "password": "collector-pass"
}
```

These two keys are extracted by the collector before the remainder is
passed to NAPALM as `optional_args`, so they will not interfere with driver
options. See `NapalmCollector.__init__` in
`netbox_facts/helpers/collector.py` for the resolution order.

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

## Job timeout vs NAPALM timeout

These are independent:

- `napalm_timeout` (default 60s) bounds a single NAPALM RPC call.
- `job_timeout` (default 1800s / 30 min) bounds the entire RQ job that
  iterates every device in a plan.

If a plan covers many devices, `job_timeout` is the value to raise.

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

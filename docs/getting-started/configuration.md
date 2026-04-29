# Configuration

All plugin-wide options live under `PLUGINS_CONFIG["netbox_facts"]` in your
NetBox configuration. The defaults match what `FactsConfig.default_settings`
declares in `netbox_facts/__init__.py`.

## Settings reference

| Setting | Type | Default | Description |
|---|---|---|---|
| `top_level_menu` | bool | `True` | Render the plugin as an **Operational Facts** top-level menu. When `False`, entries appear under **Plugins**. |
| `napalm_username` | str | `""` | Default NAPALM username for device connections. Empty disables device login unless overridden per plan. |
| `napalm_password` | str | `""` | Default NAPALM password. Empty disables device login unless overridden per plan. |
| `napalm_timeout` | int | `60` | Connection timeout passed to the NAPALM driver as `optional_args["timeout"]` when the per-plan `napalm_args` does not already set it. |
| `global_napalm_args` | dict | `{}` | Extra NAPALM `optional_args` merged into every plan. The plan's own `napalm_args` overrides matching keys. |
| `valid_interfaces_re` | str | `".*"` | Regex applied to interface names by collectors that walk per-interface tables (ARP, NDP, interfaces, ethernet switching). Interfaces whose name does not match are skipped. |
| `job_timeout` | int | `1800` | Maximum runtime in seconds passed to RQ when enqueuing a `CollectionJobRunner` job. |

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
(`view_*`, `add_*`, `change_*`, `delete_*`) plus a custom permission used
by the apply workflow:

- `netbox_facts.apply_factsreport` -- required to apply or skip pending
  entries on a `FactsReport`.

Grant the permission via the standard NetBox permission system to the user
or group that should be allowed to mutate NetBox from a detect-only run.

## Job timeout vs NAPALM timeout

These are independent:

- `napalm_timeout` (default 60s) bounds a single NAPALM RPC call.
- `job_timeout` (default 1800s / 30 min) bounds the entire RQ job that
  iterates every device in a plan.

If a plan covers many devices, `job_timeout` is the value to raise.

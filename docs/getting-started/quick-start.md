# Quick Start

This walkthrough assumes you have already
[installed and configured](installation.md) the plugin and have at least
one NetBox device with a primary IP that you can reach via NAPALM.

## 1. Create a Collection Plan

Navigate to **Operational Facts -> Facts Collection -> Collection Plans**
and click **Add**.

Required fields:

- **Name**: a human-readable label (e.g. `core-arp-junos`).
- **Collector type**: pick one of the ten types (start with `Inventory` or
  `LLDP` for low-risk verification).
- **NAPALM driver**: e.g. `junos`, `ios`, `eos`, `nxos_ssh`.
- **Devices** (or any of the scoping fields below).

Useful fields:

- **Detect-only**: tick this. It causes the run to produce a
  `FactsReport` instead of mutating NetBox.
- **NAPALM arguments**: JSON. Include `{"username": "...", "password": "..."}`
  if this plan should not use the global credentials.
- **Connection target**: leave on `Primary IP` unless you want OOB
  fallback.
- **Interval (minutes)**: leave blank for an on-demand plan, or set a
  value to schedule recurring runs.

## 2. Scope the plan to devices

Plans select devices through any combination of the M2M fields below. Each
populated field narrows the result (AND across dimensions, OR within):

- `devices` (explicit list)
- `regions`, `site_groups`, `sites`, `locations`
- `device_types`, `roles`, `platforms`
- `tenant_groups`, `tenants`
- `tags`
- `device_status` (one or more `DeviceStatusChoices` values; defaults to
  `active` when none selected)

The resolved queryset is built by `CollectionPlan.get_devices_queryset()`.

## 3. Run the plan

From the plan detail page, click **Run**. The plan transitions to
`queued`, then `working`, and the resulting `Job` log shows the per-device
NAPALM activity. When the job ends, the linked `FactsReport` is visible
under **Operational Facts -> Facts Reports**.

You can also enqueue a run via the API:

```bash
curl -X POST \
  -H "Authorization: Token <token>" \
  https://netbox.example.com/api/plugins/facts/collectionplans/<id>/run/
```

The response is `202 Accepted` with `{"job": <pk>}`.

## 4. Review the report

Open the report. The summary shows entry counts by action:

- **New**: detected on the device, not yet in NetBox.
- **Changed**: detected on the device, differs from NetBox.
- **Confirmed**: detected and matches NetBox.
- **Stale**: in NetBox (and tagged "Automatically Discovered") but not
  detected this run.

Each entry shows `detected_values` (what the device reported) and
`current_values` (what NetBox has, when applicable).

## 5. Apply or skip entries

Tick one or more pending entries and use the **Apply selected** or
**Skip selected** buttons. Apply runs each entry through its per-collector
handler in
[`netbox_facts/helpers/applier.py`](https://github.com/jsenecal/netbox-facts/blob/main/netbox_facts/helpers/applier.py)
inside a per-entry savepoint, so a failure on one entry does not roll back
others.

When every entry is resolved (applied, skipped, or failed) the report
status becomes `Applied` (if any entry was applied) or `Completed`.

## 6. Schedule recurring runs

Edit the plan and set **Interval (minutes)**. The `post_save` signal
calls `CollectionJobRunner.enqueue_once()`, mirroring NetBox's
`DataSource` sync pattern. Disabling the plan or clearing the interval
cancels any pending scheduled job.

See [Scheduling and Jobs](../user-guide/scheduling.md) for details.

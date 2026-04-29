# Scheduling and Jobs

Collection plans run on top of NetBox's `JobRunner` framework. Each plan
gets its own `CollectionJobRunner` (defined in
`netbox_facts/jobs.py`).

## Manual runs

From the plan detail page, click **Run** (or `POST` to
`/api/plugins/facts/collectionplans/<id>/run/`). The view calls
`CollectionPlan.enqueue_collection_job(request)` which:

1. Refuses to enqueue if `status` is already `queued` or `working`
   (`OperationNotSupported` -> HTTP `409`).
2. Picks the user (`run_as` if the requester is a superuser and the field
   is set, otherwise the requester).
3. Calls `CollectionJobRunner.enqueue()` with the plan as `instance` and
   `queue_name=plan.priority` (one of `high`, `default`, `low`).

`enqueue()` injects the plugin's `job_timeout` (default 1800s) when the
caller does not pass one, then sets the plan's status to `queued`.

## Recurring runs

Set the plan's `interval` (in minutes). The `post_save` signal
`handle_collection_job_change` calls
`CollectionJobRunner.enqueue_once(instance=plan, interval=plan.interval, ...)`,
which is the upstream NetBox idiom that ensures only one scheduled job
exists per plan.

Unsetting the interval, or disabling the plan, deletes any pending
scheduled job for that plan.

## Queue priorities

| Priority | RQ queue |
|---|---|
| `high` | `high` |
| `default` | `default` |
| `low` | `low` |

Queues map directly. Configure RQ workers to drain higher-priority queues
first if you mix interactive and bulk plans.

## Job lifecycle

`CollectionJobRunner.run()`:

1. Loads the `CollectionPlan` from `self.job.object_id`.
2. Calls `plan.run(request=request)`, which constructs a `NapalmCollector`
   and iterates devices.
3. In a `finally`, copies the in-memory log onto `self.job.data["log"]`
   so the job results page can display it (even on failure).
4. Links the most recent `FactsReport` for the plan to this job (if not
   already linked).

`CollectionPlan.run()` updates plan status:

- Set `working` at the start.
- Set `completed` and `last_run = now` on success.
- Set `failed` and re-raise on exception.

`NapalmCollector.execute()` updates report status:

- `Applied` (apply mode) or `Pending` (detect-only) on clean finish.
- `Failed` and `error_message` on uncaught exception.

## Stalled detection

If a plan is `working` but no live `Job` exists (e.g. a worker crashed),
`CollectionPlan.check_stalled()` (called from `__init__`) flips its
status to `stalled`. This is a hint to operators: stalled plans can be
re-run safely.

## Job timeouts

Two limits cap how long a run can take:

- `napalm_timeout` (plugin-wide, default 60s) is the per-RPC NAPALM
  timeout. It is injected into `optional_args["timeout"]` before
  connecting.
- `job_timeout` (plugin-wide, default 1800s) is the RQ-level cap on the
  whole job. Long plans (many devices) may need this raised in
  `PLUGINS_CONFIG`.

## Inspecting jobs

Each plan's **Jobs** tab lists every `core.Job` it has produced. Each job
links to its `FactsReport` and shows the per-device log written into
`Job.data["log"]`.

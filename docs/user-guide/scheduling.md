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

## Schedule semantics

A plan carries three scheduling fields. Two of them describe a recurrence
and are mutually exclusive; the third is a start time.

| `interval` | `cron_schedule` | `scheduled_at` | Behavior |
|---|---|---|---|
| blank | blank | blank | Manual runs only. Nothing is scheduled. |
| blank | blank | future time | One single run, at `scheduled_at`. |
| `N` | blank | blank | Runs now, then every `N` minutes. |
| `N` | blank | future time | First run at `scheduled_at`, then every `N` minutes. |
| blank | expression | blank | Runs at each firing of the expression. |
| blank | expression | future time | Runs at the first firing after `scheduled_at`, then at each firing. |
| `N` | expression | -- | Validation error: pick one recurrence. |

`scheduled_at` is a not-before anchor, never a recurrence of its own: a
time already in the past schedules nothing, so a one-time plan does not
fire again on the next save and a recurring plan is not held back by an
anchor it has already passed.

The **Next run** value shown on the plan detail page, on the plan list,
and in the REST API is computed from these three fields plus `last_run`.
A disabled plan has no next run.

### Cron expressions

`cron_schedule` takes a standard five-field expression (minute, hour,
day of month, month, day of week) and is evaluated in NetBox's
configured time zone -- `0 2 * * 1-5` is 02:00 on weekdays where the
maintenance window lives, not 02:00 UTC. The seconds field and the
`@daily`-style nicknames are rejected.

```
0 2 * * 1-5     02:00, Monday through Friday
*/15 * * * *    every 15 minutes
0 */4 * * *     every four hours, on the hour
0 3 1 * *       03:00 on the first of each month
30 22 * * 6     22:30 on Saturdays
```

A cron plan carries no interval on its background job: NetBox reschedules
a recurring job from the interval stored on the job itself, which cannot
express "02:00 on weekdays". Instead `CollectionJobRunner` computes the
next firing after each run (including a failed one) and enqueues the
successor itself, so a cron schedule survives a failing device as well as
an interval schedule does.

### How a schedule reaches the queue

The `post_save` signal `handle_collection_job_change` asks the plan for
the `(schedule_at, interval)` pair its fields describe and passes it to
`CollectionJobRunner.enqueue_once()`, the upstream NetBox idiom that
keeps exactly one scheduled job per plan. Clearing the recurrence, or
disabling the plan, deletes any future-dated job it still has.

An interval plan with no future anchor is deliberately enqueued without a
`schedule_at`: `enqueue_once()` compares the stored schedule against the
requested one, so handing it a fresh "now" on every save would delete the
pending job and start another run each time the plan is edited.

A future-dated job does not set the plan's status to `queued`. The plan
is idle until its slot arrives, so the Run button stays available and a
manual run alongside a schedule is still possible.

## Scheduling through the REST API

`interval`, `cron_schedule`, `scheduled_at`, and `connection_target` are
writable on `/api/plugins/facts/collectionplans/`, so a schedule can be
set up with a `PATCH` instead of the plan edit form:

```
PATCH /api/plugins/facts/collectionplans/12/
{"interval": 1440, "scheduled_at": "2026-01-01T02:00:00Z"}
```

```
PATCH /api/plugins/facts/collectionplans/12/
{"interval": null, "cron_schedule": "0 2 * * 1-5"}
```

`next_run` is read-only and computed, not stored.

`last_run` and `run_as` are read-only. `last_run` is stamped by the
collection job itself. `run_as` is read-only because it is the identity a
*scheduled* run executes under, and `enqueue_once()` takes it verbatim --
unlike a manual run, there is no superuser check at that point. Accepting
it from the API would let anyone who can edit a plan have collections run
under another user's identity, so it is set from the plan edit form only.

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

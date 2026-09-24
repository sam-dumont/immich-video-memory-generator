---
sidebar_position: 2
title: Automated generation
---

# Automated generation

Three ways to get memories without asking for them: `auto run` on a host scheduler, the same
decision inside the web UI process when you run Docker, or an HTTP POST from anything that can make
one.

## auto run

`immich-memories auto run` is the single daily entry point. It scans the library, decides which one
memory is worth making today, and exits. One invocation does exactly one thing: retry a pending
delivery, generate one eligible memory, or return a typed skip or dry-run result.

```bash
immich-memories auto suggest        # what would it generate?
immich-memories auto run            # decide and perform one action
immich-memories auto install --hour 9   # schedule it daily (launchd on macOS, systemd on Linux)
```

Variety rules stop it becoming a monthly-highlight vending machine: only the latest completed month
is eligible, monthly runs are capped at one per calendar month, a category cannot repeat
back-to-back, and no category may take more than two of the last six completed automatic runs. The
nine detectors, their scores and the rest of the rotation rules are on [auto](../being-rewritten/auto.md).

## Docker and the web UI: the built-in timer

`auto install` needs a host scheduler and the binary on the host. In Docker the container's only
process is the web UI, so the timer lives there instead: one config toggle makes the UI process run
the same `auto run` decision once a day, with the same lease, history, delivery retry and
notifications as the CLI.

```yaml
advanced:
  automation:
    enabled: true        # default false
    daily_at: "09:00"    # local wall-clock time of the container (set TZ=)
```

or `IMMICH_MEMORIES_AUTOMATION__ENABLED=true` and `IMMICH_MEMORIES_AUTOMATION__DAILY_AT=09:00` for
compose. Then `docker compose up` is the whole setup.

One automation decision per calendar day: a container that was down at `daily_at` catches up when it
starts, and if the day's run already happened (including a manual `docker exec … auto run`) it waits
for tomorrow. A manual UI or CLI run in progress holds the same lock, so the timer's run is reported
as `skipped` rather than overlapping it. `/health/ready` shows the timer under
`in_process_scheduler`. With `enabled: false`, the default, the timer never runs and `auto install`
stays the route for bare-metal installs.

## Trigger it over HTTP

The server accepts one POST that starts a memory. It takes no parameters and runs exactly the
decision `auto run` would have made, on the same detectors, variety rules, cooldown and history. You
are choosing *when*, not *what*. That makes it the piece Immich Workflows was missing: a workflow
that fires when an album fills up, a cron on another box, a phone shortcut, a Home Assistant
automation.

The endpoint is **not served at all** unless something can authenticate the caller, because this
process holds your Immich API key.

| `auth.enabled` | `server.trigger_token` | `POST /api/trigger` |
|---|---|---|
| off | unset | **404**: the route is not enabled |
| off | set | token required |
| on | unset | logged-in session required (browsers only) |
| on | set | token **or** session |

For headless callers, set a long random token and keep it in the environment:

```bash
export IMMICH_MEMORIES_SERVER__TRIGGER_TOKEN="$(openssl rand -hex 32)"
```

Writing it into `config.yaml` works too, but `server` is not one of the sections that expand a
`${VAR}` reference: put `"${SOMETHING}"` there and the token is those literal characters. Either way
the value is compared in constant time and redacted from logs, `/health` and the config viewer. It
is a shared secret over whatever transport your server already uses, so put it behind the same HTTPS
reverse proxy as the web interface: a token sent over plain HTTP is a token you have published.

```bash
curl -X POST https://memories.example.com/api/trigger \
  -H "x-api-key: $IMMICH_MEMORIES_SERVER__TRIGGER_TOKEN"
```

```json
{
  "status": "accepted",
  "attempt_id": "6f1c2a54-9d0e-4c31-9f6a-2c1d0b7e8a44",
  "status_url": "/api/trigger/6f1c2a54-9d0e-4c31-9f6a-2c1d0b7e8a44"
}
```

`202 Accepted`, not `200 OK`: a generation takes minutes to hours, so the call returns the moment the
run is booked. `Authorization: Bearer <token>` works in place of `x-api-key`. **409 Conflict** means
a run is already going and the body names it, enforced by the same lock the nightly timer uses, so a
trigger cannot make two generations fight over your GPU.

GET the `status_url` for progress. `phase` is live (`discovery` through `complete`) rather than a
flat "running", and when it finishes `state` becomes `completed`, `failed`, `skipped` or `dry_run`,
with `run` carrying `run_id`, `status`, `created_at`, `completed_at`, `last_phase`,
`output_duration_seconds`, `delivery_status` and `immich_asset_id`. The rest of the record holds host
paths and person names, which this contract has no reason to promise.

`skipped` is a normal answer. The trigger runs what `auto run` decides, and `auto run` declines when
the cooldown is still active or no candidate cleared its bar; `reason` says which. Two things to know
before wiring it to Immich Workflows: a workflow that fires on every upload will mostly get
`skipped` back, which is the system working, and a workflow that fires on a trip album does not
generate *that* album, it asks for the best candidate right now. For a specific memory, use the CLI
or the web UI.

Clear `server.trigger_token` to turn it off; with authentication also off the routes go back to
answering 404.

## Kubernetes

`deploy/kubernetes/base/job.yaml` holds a one-off `generate` Job plus monthly and `auto run`
CronJobs. It reads the Immich connection from the `immich-memories-secrets` Secret and shares the
PVCs of the [Kubernetes deployment](../run/kubernetes.md):

```bash
kubectl apply -f deploy/kubernetes/base/job.yaml
```

The Job runs to completion and writes the video to the output volume (`/app/output`), which keeps the
render off your laptop.

## A named memory type on a named date

That is the one thing `auto` cannot express, and the only reason the legacy
[scheduler daemon](../being-rewritten/scheduler.md) still exists. It handles timezone-aware cron and resolves
date parameters from fire time, so `year_in_review` firing in January generates the previous year.

```yaml
scheduler:
  enabled: true
  timezone: "America/New_York"
  schedules:
    - name: "yearly-recap"
      memory_type: "year_in_review"
      cron: "0 9 15 1 *"          # Jan 15 at 9am
      upload_to_immich: true
      album_name: "{year} Memories"
```

```bash
immich-memories scheduler start --foreground
```

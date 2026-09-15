---
sidebar_position: 2
title: Automated Generation
---

# Automated Generation

Three ways to get memories without asking for them, in the order you should try them: `auto run`
on a host scheduler, the same decision inside the web UI process when you run Docker, or the old
scheduler daemon when you need a named memory type on a named date.

## auto run

`immich-memories auto run` is the single daily entry point: it scans the library, decides which
one memory is worth making today, and exits. One invocation does exactly one thing: retry a pending delivery, generate one eligible memory, or
return a typed skip or dry-run result.

```bash
# What would it generate?
immich-memories auto suggest

# Decide and perform one action
immich-memories auto run

# Schedule it daily (launchd on macOS, systemd on Linux)
immich-memories auto install --hour 9
```

Variety rules stop it from becoming a monthly-highlight vending machine: only the latest completed
month is eligible, monthly runs are capped at one per calendar month, a category cannot repeat
back-to-back, and no category may take more than two of the last six completed automatic runs. The
nine detectors, their scores and the rest of the rotation rules are in [auto](../cli/auto.md).

### Docker and the web UI: the built-in timer

`auto install` needs a host scheduler and the binary on the host. In Docker the container's only
process is the web UI, so the timer lives there instead: one config toggle makes the UI process
run the same `auto run` decision once a day, with the same lease, history, delivery retry and
notifications as the CLI.

```yaml
advanced:
  automation:
    enabled: true        # default false
    daily_at: "09:00"    # local wall-clock time of the container (set TZ=)
```

or, for compose, `IMMICH_MEMORIES_AUTOMATION__ENABLED=true` and
`IMMICH_MEMORIES_AUTOMATION__DAILY_AT=09:00`. Then `docker compose up` is the whole setup: a memory
appears on schedule, uploads if `upload_to_immich` is on, and notifies if notifications are on.

- One automation decision per calendar day: a container that was down at `daily_at` catches up
  when it starts; if the day's run already happened (including a manual `docker exec … auto run`),
  it waits for tomorrow.
- A manual UI or CLI run in progress holds the same lock, so the timer's run is reported as
  `skipped` rather than overlapping it.
- `/health/ready` shows the timer under `in_process_scheduler` (`enabled`, `daily_at`, `next_run`,
  `running`, `last_fired_at`, `last_outcome`, `last_reason`).
- The timer never runs when `enabled` is `false` (the default): `auto install` stays the route
  for bare-metal installs.

To fire the same decision on demand instead of on a clock (from an Immich workflow, a cron on
another machine, a phone shortcut), see
[Trigger from Immich or Anything Else](./trigger-endpoint.md).

## Scheduler daemon

:::tip Use the `auto` system instead
The scheduler is for the case `auto` cannot express: a specific memory type on a specific date.
:::

It runs inside immich-memories and handles timezone-aware cron, auto-resolved date parameters and
upload-back, so there is no shell scripting around it.

```yaml
# config.yaml
scheduler:
  enabled: true
  timezone: "America/New_York"
  schedules:
    - name: "yearly-recap"
      memory_type: "year_in_review"
      cron: "0 9 15 1 *"          # Jan 15 at 9am
      upload_to_immich: true
      album_name: "{year} Memories"

    - name: "monthly-highlights"
      memory_type: "monthly_highlights"
      cron: "0 9 1 * *"           # 1st of each month
      duration_minutes: 3

    - name: "on-this-day"
      memory_type: "on_this_day"
      cron: "0 9 * * *"           # Every morning
```

```bash
# Start the daemon (foreground mode required, background mode not yet implemented)
immich-memories scheduler start --foreground

# Check what's scheduled
immich-memories scheduler list
immich-memories scheduler status
```

Date parameters are resolved from fire time: `year_in_review` firing in January generates the
previous year, `monthly_highlights` firing on the 1st generates the previous month, `on_this_day`
uses the current date. Explicit `params` in the schedule override that. Full reference:
[scheduler CLI docs](../cli/scheduler.md).

## Hand-written cron

`auto install` writes the cron, launchd or systemd config for you, so hand-written entries are
mostly a way to get the date arithmetic wrong. The CLI is headless, so any of this works over SSH,
in a container, or in CI.

```bash
# crontab -e: a yearly recap every 1 January at 3 AM
0 3 1 1 * immich-memories generate --person "Emma" --year $(date -d 'last year' +\%Y) --duration 600
```

For everyone in the house at once, loop over the names:

```bash
for person in "Emma" "Lucas" "Sophie"; do
  immich-memories generate --person "$person" --year 2024 --duration 600 \
    --output "/videos/memories/${person}_2024.mp4"
done
```

## Kubernetes

`deploy/kubernetes/base/job.yaml` holds a one-off `generate` Job plus monthly and `auto run`
CronJobs. It reads the Immich connection from the `immich-memories-secrets` Secret and shares the
PVCs of the [Kubernetes deployment](../../deploy/installation/kubernetes.md):

```bash
kubectl apply -f deploy/kubernetes/base/job.yaml
```

The Job runs to completion and writes the video to the output volume (`/app/output`), which keeps
the render off your laptop.

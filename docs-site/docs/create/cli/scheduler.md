---
sidebar_position: 4
title: scheduler
---

# scheduler

:::tip The `auto` system is the easier option
Most users should schedule one daily [`auto run`](./auto.md#auto-run) instead: it detects trips,
birthdays, and highlights automatically. No cron expressions or explicit schedules needed. The
scheduler below is the advanced/legacy cron daemon for Docker/K8s deployments or exact control
over what generates when.
:::

:::caution Background mode not yet implemented
The scheduler daemon currently requires `--foreground` to run. Background (daemonized) mode is planned but not yet implemented. Always pass `--foreground` when starting the scheduler.
:::

## scheduler list

```bash
immich-memories scheduler list
```

Shows all configured schedules: name, memory type, cron expression, enabled/disabled, upload setting, and next run time.

If nothing's configured, you get a hint pointing you to the config file.

## scheduler status

```bash
immich-memories scheduler status
```

Quick overview: is the scheduler enabled, how many schedules are active, when the next job fires.

## scheduler start

```bash
immich-memories scheduler start --foreground
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--foreground` | flag | `false` | Run in foreground (required: background mode is not yet implemented) |

Starts the advanced/legacy scheduler daemon. Needs `scheduler.enabled: true` and at least one
schedule in the config. It is separate from `auto run`; do not run both daily unless you deliberately
want independent automation paths.

Each generation job has a two-hour deadline by default. It covers preparation,
selection, rendering and upload. Set `scheduler.job_timeout_minutes` to a positive
number to change it; an explicit value in an existing config still applies.

This deadline allows a long job to finish; it does not make generation faster. How long a
full year actually takes on this route has not been measured, so two hours is a ceiling
somebody chose, not a forecast. If your jobs are being killed, raise it and measure.

On macOS and Linux, a timed-out generation is stopped together with its child
processes. They get up to five seconds to stop after SIGTERM, followed by SIGKILL
and a bounded wait of up to five more seconds. Scheduler shutdown uses the same
cleanup. Captured output remains available to diagnose the failure. `auto run`
uses the same process cleanup for its generation deadline and interruption.

## Auto-resolved parameters

When a schedule fires, date parameters get resolved automatically from the fire time:

| Memory type | What gets filled in |
|-------------|---------------------|
| `year_in_review` | `year` = previous year |
| `monthly_highlights` | `year` + `month` = previous month |
| `season` | `year` = fire year |
| `on_this_day` | `target_date` = fire date |
| `trip` | `year` = previous year |
| anything else | `year` = fire year |

So a `year_in_review` firing on Jan 15 2025 generates for 2024. A `monthly_highlights` firing on Aug 1 generates for July. You get the idea.

Explicit `params` override these auto-resolved values, but only for the seven things the daemon knows how to put on a command line: `year`, `month`, `target_date`, duration, upload, album and person. Setting `params: { year: 2020 }` on a `year_in_review` schedule always generates for 2020 no matter when it fires. Any other key is merged and then silently dropped.

:::danger Six of these do not currently work
Verified against the code on this branch, not inferred. The root cause of most of it is that the
daemon's command builder emits only `--year`, `--month`, `--target-date`, duration, upload, album
and person, so any memory type needing another flag cannot be driven from here.

- **`on_this_day` fails every time.** The daemon passes `--target-date`, which `generate` does not
  accept. The job exits on a Click usage error. (It would work if the daemon simply omitted the
  flag: `on_this_day` defaults to today.)
- **`season` fails every time.** `--season` is never passed, and `generate` refuses without it.
- **`holiday` fails every time.** `--holiday` is never passed, same refusal.
- **`album` cannot be scheduled.** There is no way for `--from-album` to reach the command.
- **`trip` generates nothing.** The daemon never passes `--all-trips`, and `generate
  --memory-type trip --year N` without it is discovery mode: it prints a table of trips and stops.
- **`duration_minutes` is seconds.** It is handed straight to `--duration`, which takes seconds, so
  `duration_minutes: 3` asks for a three-second film.

Until these are fixed, drive those types from your host's own scheduler (`auto run`, cron,
launchd) and write `duration_minutes` as the number of seconds you want.
:::

## Example config

```yaml
scheduler:
  enabled: true
  timezone: "America/New_York"   # cosmetic: cron is evaluated in UTC, see below
  job_timeout_minutes: 120
  schedules:
    - name: "yearly-recap"
      memory_type: "year_in_review"
      cron: "0 9 15 1 *"          # Jan 15 at 09:00 UTC
      upload_to_immich: true
      album_name: "{year} Memories"

    - name: "monthly-highlights"
      memory_type: "monthly_highlights"
      cron: "0 9 1 * *"           # 1st of each month at 09:00 UTC
      duration_minutes: 3

    - name: "on-this-day"
      memory_type: "on_this_day"
      cron: "0 9 * * *"           # Every day at 09:00 UTC
      person_names: ["Riley"]

    - name: "summer-2024"
      memory_type: "season"
      cron: "0 9 1 10 *"          # Oct 1 at 09:00 UTC
      enabled: false              # Paused, and broken anyway: `season` never reaches the command
      params:
        season: "summer"
        year: 2024
```

Cron format: `minute hour day-of-month month day-of-week`. Standard 5-field cron syntax, nothing fancy, **evaluated in UTC**. `scheduler.timezone` is printed back to you and otherwise unused: it does not shift a single fire time. Write your crons in UTC until that is fixed.

---
sidebar_position: 4
title: scheduler
---

# scheduler

Set-and-forget memory generation. Define schedules in `config.yaml` under `scheduler.schedules` with standard cron expressions, and the daemon handles the rest.

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

Starts the scheduler daemon. Needs `scheduler.enabled: true` and at least one schedule in the config.

## Auto-resolved parameters

When a schedule fires, date parameters get resolved automatically from the fire time:

| Memory type | What gets filled in |
|-------------|---------------------|
| `year_in_review` | `year` = previous year |
| `monthly_highlights` | `year` + `month` = previous month |
| `on_this_day` | `target_date` = fire date |
| `trip` | `year` = previous year (scans GPS data, generates all trips) |

So a `year_in_review` firing on Jan 15 2025 generates for 2024. A `monthly_highlights` firing on Aug 1 generates for July. You get the idea.

Explicit `params` in the schedule config override these auto-resolved values. Setting `params: { year: 2020 }` on a `year_in_review` schedule always generates for 2020 no matter when it fires.

## Example config

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

    - name: "monthly-highlights"
      memory_type: "monthly_highlights"
      cron: "0 9 1 * *"           # 1st of each month at 9am
      duration_minutes: 3

    - name: "on-this-day"
      memory_type: "on_this_day"
      cron: "0 9 * * *"           # Every day at 9am
      person_names: ["Alice"]

    - name: "summer-2024"
      memory_type: "season"
      cron: "0 9 1 10 *"          # Oct 1 at 9am
      enabled: false              # Paused
      params:
        season: "summer"
        year: 2024
```

Cron format: `minute hour day-of-month month day-of-week`. Standard 5-field cron syntax, nothing fancy.

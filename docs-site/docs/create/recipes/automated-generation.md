---
sidebar_position: 2
title: Automated Generation
---

# Automated Generation

Once you know your preferred settings, automate the whole thing. Three paths: smart automation (recommended), the built-in scheduler daemon, or classic cron/scripts.

## Smart Automation (Recommended)

The `auto` system scans your library, detects what's worth turning into a memory video, and generates the best candidate. It runs 8 detectors (monthly, yearly, trips, person spotlights, birthdays, activity bursts, on-this-day, multi-person pairs) and picks the highest-scoring one.

```bash
# See what it would generate
immich-memories auto suggest

# Generate the top candidate
immich-memories auto run

# Set up daily automatic runs (launchd on macOS, systemd on Linux)
immich-memories auto install --hour 9
```

Each run generates one memory, then applies cooldowns so the next run picks something different. Over a week of daily runs, you get a diverse mix: monthlies, birthday videos, trip compilations, year-in-reviews.

See [auto CLI docs](../cli/auto.md) for the full reference including detector details and scoring.

## Built-in Scheduler

:::tip Use smart automation instead
Most users should use the `auto` system above — it figures out what to generate automatically. The scheduler below is for Docker/K8s deployments or when you need exact control over what generates when (specific memory types on specific dates).
:::

The scheduler daemon runs inside immich-memories and handles timezone-aware cron, auto-resolved date parameters, and upload-back. No shell scripting required.

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

Date parameters are auto-resolved from fire time: `year_in_review` firing in January generates for the previous year, `monthly_highlights` firing on the 1st generates for the previous month, `on_this_day` uses the current date. Override with explicit `params` in the schedule config if you need something specific.

Full reference: [scheduler CLI docs](../cli/scheduler.md).

## CLI One-Liner

If you just need a one-off:

```bash
immich-memories generate \
  --person "Emma" \
  --year 2024 \
  --duration 600 \
  --orientation landscape \
  --resolution 1080p
```

## Cron Job (Legacy)

Old-school but works. Consider `auto install` instead — it generates the right cron/launchd/systemd config for you. Generate a yearly memory video every January 1st:

```bash
# crontab -e
0 3 1 1 * immich-memories generate --person "Emma" --year $(date -d 'last year' +\%Y) --duration 600
```

Runs at 3 AM on January 1st. Uses last year as the period so you get a complete year of content.

## Multiple People

Shell script that generates for everyone:

```bash
#!/bin/bash
PEOPLE=("Emma" "Lucas" "Sophie")
YEAR="2024"

for person in "${PEOPLE[@]}"; do
  echo "Generating for $person..."
  immich-memories generate \
    --person "$person" \
    --year "$YEAR" \
    --duration 600 \
    --output "/videos/memories/${person}_${YEAR}.mp4"
done
```

## Kubernetes Batch Job

There's a job manifest in the repo at `deploy/kubernetes/job.yaml`. Customize the configmap with your Immich connection details and submit:

```bash
kubectl apply -f deploy/kubernetes/configmap.yaml
kubectl apply -f deploy/kubernetes/job.yaml
```

The job runs to completion and writes the output video to the configured volume. Good for running generation on a GPU node in your cluster without tying up your local machine.

## Headless Mode

The CLI runs fully headless: no display needed. Works fine in Docker containers, SSH sessions, and CI pipelines. All configuration comes from `config.yaml` and CLI flags.

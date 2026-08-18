---
sidebar_position: 2
title: Automated Generation
---

# Automated Generation

Once you know your preferred settings, automate the whole thing. The recommended path is one daily
smart decision; the scheduler daemon and hand-written cron are advanced/legacy alternatives.

## Smart Automation (Recommended)

The `auto` system scans your library, detects what's worth turning into a memory video, and generates the best candidate. It runs 8 detectors (monthly, yearly, trips, person spotlights, birthdays, activity bursts, on-this-day, multi-person pairs) and picks the highest-scoring one.

```bash
# See what it would generate
immich-memories auto suggest

# The single daily entry point: decide and perform one action
immich-memories auto run

# Set up daily automatic runs (launchd on macOS, systemd on Linux)
immich-memories auto install --hour 9
```

`immich-memories auto run` is the recommended single daily entry point. Each invocation performs
exactly one action: retry one pending delivery, generate one eligible memory, or return a typed
skip/dry-run result. Variety rules keep the outputs from becoming a monthly-highlight vending
machine: only the latest completed month is eligible, monthly runs are capped at one per calendar
month, categories cannot repeat back-to-back, and each category is capped at two of the last six
completed automatic runs.

See [auto CLI docs](../cli/auto.md) for the full reference including detector details and scoring.

### Docker and the web UI: built-in daily timer

`auto install` needs a host scheduler and the binary on the host. In Docker the container's only
process is the web UI, so the timer lives there instead: one config toggle makes the UI process
run the same `auto run` decision once a day — same lease, history, delivery retry, and
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
- The timer never runs when `enabled` is `false` (the default) — `auto install` stays the route
  for bare-metal installs.

## Scheduler daemon (advanced/legacy)

:::tip Use smart automation instead
Most users should use the `auto` system above — it figures out what to generate automatically. The scheduler below is for Docker/K8s deployments or when you need exact control over what generates when (specific memory types on specific dates).
:::

The advanced/legacy scheduler daemon runs inside immich-memories and handles timezone-aware cron,
auto-resolved date parameters, and upload-back. No shell scripting required.

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

There's a job manifest in the repo at `deploy/kubernetes/base/job.yaml` (one-off `generate` Job plus monthly and `auto run` CronJobs). It reads the Immich connection from the `immich-memories-secrets` Secret and shares the PVCs of the [Kubernetes deployment](../../deploy/installation/kubernetes.md):

```bash
kubectl apply -f deploy/kubernetes/base/job.yaml
```

The job runs to completion and writes the output video to the output volume (`/app/output`). Good for running generation in your cluster without tying up your local machine.

## Headless Mode

The CLI runs fully headless: no display needed. Works fine in Docker containers, SSH sessions, and CI pipelines. All configuration comes from `config.yaml` and CLI flags.

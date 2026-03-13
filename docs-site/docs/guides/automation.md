---
sidebar_position: 3
title: Automation
---

# Automation

Once you know your preferred settings, automate the whole thing. Two paths: the built-in scheduler daemon (recommended) or classic cron/scripts.

## Built-in Scheduler (Recommended)

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
# Start the daemon
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
  --period "2024" \
  --duration 10 \
  --orientation landscape \
  --resolution 1080p
```

## Cron Job

Old-school but works. Generate a yearly memory video every January 1st:

```bash
# crontab -e
0 3 1 1 * immich-memories generate --person "Emma" --period "$(date -d 'last year' +%Y)" --duration 10
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
    --period "$YEAR" \
    --duration 10 \
    --output-dir "/videos/memories/${person}_${YEAR}.mp4"
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

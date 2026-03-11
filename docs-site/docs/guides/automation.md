---
sidebar_position: 3
title: Automation
---

# Automation

Once you know your preferred settings, automate the whole thing.

## CLI One-Liner

```bash
immich-memories generate \
  --person "Emma" \
  --period "2024" \
  --duration 10 \
  --orientation landscape \
  --resolution 1080p
```

## Cron Job

Generate a yearly memory video every January 1st:

```bash
# crontab -e
0 3 1 1 * immich-memories generate --person "Emma" --period "$(date -d 'last year' +%Y)" --duration 10
```

Runs at 3 AM on January 1st. Uses last year as the period so you get a complete year's worth of content.

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

The CLI runs fully headless — no display needed. This means it works fine in Docker containers, SSH sessions, and CI pipelines. All configuration comes from `config.yaml` and CLI flags.

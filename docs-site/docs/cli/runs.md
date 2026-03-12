---
sidebar_position: 6
title: runs
---

# runs

Every time you generate a video, immich-memories tracks the run: what settings you used, how long it took, how many clips were processed, errors, system info. The `runs` command lets you browse that history.

## runs list

```bash
immich-memories runs list [OPTIONS]
```

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--limit` | `-n` | int | `20` | Number of runs to show |
| `--person` | `-p` | string | — | Filter by person name |
| `--status` | `-s` | choice | — | `completed`, `failed`, `running`, `cancelled`, `interrupted` |

Examples:

```bash
# Recent runs
immich-memories runs list

# Just the failures
immich-memories runs list --status failed

# Runs for a specific person
immich-memories runs list --person "Emma" --limit 5
```

## runs show

Detailed view of a single run. Shows status, date range, clip counts, output file size, phase-by-phase timing breakdown, and system info (CPU, GPU, RAM, FFmpeg version).

```bash
immich-memories runs show RUN_ID
```

You can use a partial run ID: if it's unambiguous, it'll match:

```bash
immich-memories runs show 20260105_1430
```

## runs stats

Aggregate statistics across all your runs:

```bash
immich-memories runs stats
```

Shows total runs, completion rate, total video generated, total processing time, average clips per run, etc.

## runs delete

Delete a run record and optionally its output video:

```bash
# Delete the run and its output file
immich-memories runs delete 20260105_143052_a7b3

# Delete the record but keep the video
immich-memories runs delete 20260105_143052_a7b3 --keep-output
```

You'll get a confirmation prompt before anything is deleted.

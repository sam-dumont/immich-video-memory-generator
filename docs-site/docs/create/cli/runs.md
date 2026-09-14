---
sidebar_position: 5
title: runs
---

# runs

Every time you generate a video, immich-memories tracks the run: how long it took, how many clips were processed, the model's call count and cost, errors, system info. Render settings are not recorded. The `runs` command lets you browse that history.

## runs list

```bash
immich-memories runs list [OPTIONS]
```

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--limit` | `-n` | int | `20` | Number of runs to show |
| `--person` | `-p` | string | n/a | Filter by person name |
| `--status` | `-s` | choice | n/a | `completed`, `failed`, `running`, `cancelled`, `interrupted` |

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

Detailed view of a single run. Shows status, clip counts, output file size, phase-by-phase timing breakdown, and system info (CPU, GPU, RAM, FFmpeg version). There is a date-range row in the renderer, but nothing currently fills it, so you will not see it.

```bash
immich-memories runs show RUN_ID
```

You can use a partial run ID: if it's unambiguous among the 100 most recent runs, it'll match. Older than that and a unique prefix still reports "Run not found"; use the full id.

```bash
immich-memories runs show 20260105_1430
```

### Model spend

`runs show` reports what the run spent on the LLM: calls, judgment-cache hits,
tokens, wall time, and any thinking calls truncated at the token budget.

It is reported **per run, not per phase**, and the phase table is labelled
"recorded phases only" for the same reason: the run tracker records clip
extraction, assembly and music, all of which happen after selection. Analysis
and selection run before the run row exists and account for most of the model
budget, so a per-phase total would understate the bill.

## runs story

The cut of a run in the order it plays: one line per shot with its timecode, capture day, kind (photo or video), length, the story it was granted to and the reason the editor wrote. A month change prints as a chapter line. It is the same record the web UI's Storyboard tab draws.

```bash
immich-memories runs story              # the most recent completed run
immich-memories runs story 20260913_08  # a run id or a unique prefix
immich-memories runs story ~/.immich-memories/cache/editorial-runs/june/attempts/a1   # an attempt directory
```

The end-of-run block of `generate` prints the first eight shots and this command for the rest.

## runs why

What a run decided about one picture: the passes it survived, the pass that dropped it and the reason, and, when it made the cut, where it plays.

```bash
immich-memories runs why 3f1c9a2e-... --run 20260913_08   # --run defaults to the latest completed run
```

Every run since 0.78 writes its decision log (`selection-trace.private.json`) beside its plan, so this works without `--trace-selection`. Runs made before that answer "left no decision log".

The local database also keeps the run's operational events in `phase_events`, including each
`elapsed_seconds` sample. The sample measures time since the previous event; repeated events
within one phase stay separate. A linked automation attempt keeps those events too, alongside
its earlier discovery events. Older runs have an empty list because their timings were not saved.

Both commands find the run through the run id, which the CLI and the web UI share: a memory cut on the page can be read from the terminal and the other way round.

## runs stats

Aggregate statistics across all your runs:

```bash
immich-memories runs stats
```

Shows total runs, completed and failed as raw counts, total video generated, total and average processing time, and average and total clips processed. No completion rate: divide it yourself if you want one.

## runs delete

Delete a run record and optionally its output video:

```bash
# Delete the run and its output file
immich-memories runs delete 20260105_143052_a7b3

# Delete the record but keep the video
immich-memories runs delete 20260105_143052_a7b3 --keep-output

# Skip the confirmation prompt (scripts)
immich-memories runs delete 20260105_143052_a7b3 --yes
```

You'll get a confirmation prompt before anything is deleted unless you pass `--yes`.

## runs storage

Report where the space went under the output and cache roots, without changing anything. It groups by run status and lists the ten largest directories; it walks directories only, so loose files sitting directly in a root count as nothing:

```bash
immich-memories runs storage
immich-memories runs storage --json   # machine-readable
```

---
sidebar_position: 5
title: runs
---

# runs

Reader: power user.

Every `generate`, from the CLI or the web UI, writes a run row: how long it took, how many clips it processed,
where the title came from, the model's call count and cost when a model was used, errors, system info. Render
settings are not recorded. `runs` reads that history back, and `runs story` and `runs why` are how you find out
what the editor did and why. Every flag
is in the [CLI reference](../../reference/cli-reference.md#runs).

## runs list

```bash
immich-memories runs list                          # the last 20
immich-memories runs list --status failed
immich-memories runs list --person "Emma" --limit 5
```

`--status` takes `completed`, `failed`, `running`, `cancelled` or `interrupted`.

## runs show

```bash
immich-memories runs show 20260105_1430
```

Status, date range, clip counts, **Title From**, output file, duration and size, the phase-by-phase timing,
and the machine it ran on (CPU, GPU, RAM, FFmpeg version).

**Title From** is the source of the opening title: `override` (you typed it), `album`, `occasion`, `model`,
`place` (a trip) or `fallback` (the template). The table is on
[Titles](../titles-maps-music.md#where-the-title-came-from).

A run whose cut was checked against its promises also prints `Cut checks: N broken promise(s)`; the rows are in
the attempt's `derived-decisions/cut-invariants.private.json` (see
[How it chooses](../../how-it-chooses/overview.md)).

A partial run id matches if it is unambiguous among the 100 most recent runs. Older than that, a
unique prefix still reports "Run not found": use the full id.

### Model spend

The **Model** block: calls, judgment-cache hits, tokens, wall time, and any thinking calls truncated at the
token budget. A run that made no model call (every NAS run) prints no such block. It is reported per run, not
per phase: the tracked phases (clip extraction, assembly, music) all come after selection, and selection is
most of the model budget, so a per-phase total would understate the bill.

## runs story

The cut of a run in the order it plays: one line per shot with its timecode, capture day, kind
(photo or video), length, the story it was granted to and the reason the editor wrote. A month
change prints as a chapter line. It is the same record the web UI's Storyboard tab draws.

Timecodes and lengths are the film's, not the plan's: the renderer fits the selected seconds into the
timeline's content budget and the opening card plays before the first picture, and both are applied here. The
header gives the pictures and videos, then about how long the film runs. That last number is an estimate until
the file exists, because smart transitions decide fade or cut at each boundary. `runs show` prints the duration
measured from the render.

```bash
immich-memories runs story              # the most recent completed run
immich-memories runs story 20260913_08  # a run id or a unique prefix
immich-memories runs story ~/.immich-memories/cache/editorial-runs/june/attempts/a1   # an attempt directory
```

The end-of-run block of `generate` prints the first eight shots and this command for the rest.

## runs why

What a run decided about one picture: the passes it survived, the pass that dropped it and the
reason, and, when it made the cut, where it plays.

```bash
immich-memories runs why 3f1c9a2e-... --run 20260913_08   # --run defaults to the latest completed run
```

Every run writes its decision log (`selection-trace.private.json`) beside its plan, so this works without
`--trace-selection`. A run with no log answers "left no decision log".

A picture on the run's check-before-sharing list gets one more line, naming what the
sensitive-content detector read for it and the hold it sits under:

```text
  worth a look before sharing: the exposure head read 0.35, under the 0.5 hold
```

Both commands find the run through the run id, which the CLI and the web UI share: a memory cut on
the page can be read from the terminal and the other way round.

## runs stats

```bash
immich-memories runs stats
```

Total runs, completed and failed as raw counts, total video generated, total and average
processing time, and average and total clips processed. No completion rate.

## runs delete

Removes the run and its output file. `--keep-output` deletes the record only, `--yes` skips the
confirmation prompt for scripts.

```bash
immich-memories runs delete 20260105_143052_a7b3
```

## runs storage

Where the space went under the output and cache roots, changing nothing. It groups by run status
and lists the ten largest directories, and it walks directories only, so loose files sitting
directly in a root count as nothing.

```bash
immich-memories runs storage --json
```

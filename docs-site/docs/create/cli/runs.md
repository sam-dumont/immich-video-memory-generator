---
sidebar_position: 5
title: runs
---

# runs

Every generate writes a run row: how long it took, how many clips it processed, the model's call
count and cost, errors, system info. Render settings are not recorded. `runs` reads that history
back, and `runs story` and `runs why` are how you find out what the editor actually did. Every flag
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

Status, clip counts, output file size, the phase-by-phase timing, and the machine it ran on (CPU,
GPU, RAM, FFmpeg version).

A partial run id matches if it is unambiguous among the 100 most recent runs. Older than that, a
unique prefix still reports "Run not found": use the full id.

### Model spend

Calls, judgment-cache hits, tokens, wall time, and any thinking calls truncated at the token
budget. It is reported **per run, not per phase**: the run tracker only records clip extraction,
assembly and music, which all happen after selection, and selection is most of the model budget. A
per-phase total would understate the bill.

## runs story

The cut of a run in the order it plays: one line per shot with its timecode, capture day, kind
(photo or video), length, the story it was granted to and the reason the editor wrote. A month
change prints as a chapter line. It is the same record the web UI's Storyboard tab draws.

Timecodes and lengths are the film's, not the plan's: the renderer squeezes the selected seconds into the timeline's content budget and the opening title card plays before the first picture, and both are applied here. The header line gives the pictures and video, then about how long the whole film runs. That last number is an estimate while the file does not exist, because smart transitions decide fade or cut at each boundary and the overlap they take moves a second either way. `runs show` prints the duration measured from a finished render.

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

Every run writes its decision log (`selection-trace.private.json`) beside its plan, so this works
without `--trace-selection`. Runs made before 0.78 answer "left no decision log".

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

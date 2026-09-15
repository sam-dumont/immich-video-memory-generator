---
sidebar_position: 1
title: generate
---

# generate

`immich-memories generate` pulls pictures and video from your Immich library, reads the period
as a story, keeps the pictures that carry it, and renders a cut. It prepares missing facts for the
whole period first (previews, pixel facts, detectors, captions when the tier asks for them), so the
first run over a period is the slow one: the second is mostly the render. The audience is always
"family": that is a fixed part of the request, not a setting.

```bash
immich-memories generate [OPTIONS]
```

Every flag, with its default, is in the generated
[CLI reference](../../reference/cli-reference.md#generate), which comes out of the same Click tree
`--help` does and is checked against it on every PR. `immich-memories generate --help` prints the
same thing in your terminal. This page is what the flags do not tell you.

`--resolution` takes the config value; `auto` matches source clips. When `--resolution` is omitted,
the command uses `output.resolution`, 1080p by default. `--quality` changes the effective CRF,
mapped onto each hardware encoder's own scale.

Opening titles name the people or the occasion, never the query that produced them: a holiday
opens with its name and "Through the Years", On This Day with the month and day, a trip with a
template that follows `title_screens.locale` and counts both the first and last day. A wordy title
from a new special-day scan is asked for again rather than cut off mid-sentence, while existing
catalogue entries keep their saved titles. `--title` and `--subtitle` override all of it. See
[titles](./titles.md).

Two root options go before `generate`: `-v` (or `--log-level DEBUG`) for verbose logs, and
`--preset fast` for the CPU-only profile (1080p, H.264, medium quality, static title backgrounds)
on every knob you did not set. See [Health, logs and cache](../../deploy/maintenance/health-logs-cache.md).

## Examples

```bash
# A calendar year
immich-memories generate --year 2024

# One month, one person
immich-memories generate --memory-type person_spotlight --person "Riley" --year 2026 --month 2

# The year ending on a birthday, plus the five before it (birth date read from Immich)
immich-memories generate --year 2025 --birthday --person "Emma" --duration 900

# A child with either adult
immich-memories generate --memory-type multi_person --year 2025 \
  --people-expression '("Alex Smith" OR "Morgan Smith") AND "Riley Smith"'

# Any preset with your own dates
immich-memories generate --memory-type person_spotlight --person "Riley" \
  --start 2025-02-01 --end 2025-03-31

# This day across the years, pinned so the run is repeatable
immich-memories generate --memory-type on_this_day --day 2026-08-31 --years-back 20

# Five Christmases in one cut
immich-memories generate --memory-type holiday --holiday christmas --years-back 5

# A day the catalogue found (see discover-days); the title comes from the catalogue
immich-memories generate --memory-type special_day --day 2016-06-12

# Vertical, 30 seconds, for Reels or Shorts
immich-memories generate --year 2025 --month 8 --short-form 30
```

Three things the examples hide:

- `--people-expression` takes exact library names, binds `AND` tighter than `OR`, and works on
  date-range memories (months, years, seasons). Trips, albums and single-person presets refuse it.
- Moving holidays are computed for each year (Easter, Thanksgiving, Mother's and Father's Day), with
  a window of two days either side. A holiday cut runs 60 seconds unless you pass `--duration`.
- `special_day` refuses to run without `--day`, with a day the catalogue never recorded, or with a
  day that has no title: a day the model could not name is not rendered. `immich-memories days-due`
  lists what the catalogue holds.

## Trips

Set your home in `config.yaml` and the tool finds clusters of GPS-tagged pictures at least
50 km away, spanning at least 2 nights, split when the gap between pictures passes 2 days:

```yaml
trips:
  homebase_latitude: 50.8468
  homebase_longitude: 4.3525
  min_distance_km: 50
  min_duration_days: 2
  max_gap_days: 2
```

```bash
immich-memories generate --memory-type trip --year 2024                  # a table of trips, no video
immich-memories generate --memory-type trip --year 2024 --trip-index 2   # one of them
immich-memories generate --memory-type trip --year 2024 --all-trips      # all of them
```

A trip over New Year is one trip, not two.

## What the terminal shows while it runs

One line per stage, the same record the web UI draws its rows from. A stage that counts its
work (previews, pixel facts, detectors, the reader's requests) turns the spinner into a bar with
an estimate:

```text
⠿ Preparing previews: 352/9814 · ~19m left in this stage ━━━━━━━━━━━   3%
  ⏱ 0:41 elapsed
```

The estimate uses the items completed since this stage's first update. It appears after
another update advances the count and resets when the stage changes. It estimates this pass,
not the whole cut; slow items can change it. The page reads the same saved estimate, including
after a reload. Stages with nothing to count keep the spinner and elapsed time.

If the reader stops answering, the line says so instead of going quiet:

```text
⠿ Waiting for the reader at omlx.local:9999: connection dropped, retry 1 of 3
  ⏱ 0:14 elapsed
```

Three drops, two then four seconds apart, and the last line says what to do: `Gave up on the
reader at omlx.local:9999 after 3 dropped connections: fix the server and cut again`. The run then
fails naming the same endpoint. A model
server that is restarting survives that; one that is off is named within a second.

## What a run leaves behind

Every run ends with the cut in the order it plays, then a pointer:

```text
Memory generated in 42s
  measured this run
    selection                   11s   6 planned from 6 candidates
    generation                  31s
  the cut, in order (6 shots, 0:24)
  June 2024
   0:00  2024-06-08  video     4 s  Lunch in the garden: the table and chairs still out on the lawn
   0:04  2024-06-09  photo     4 s  Lunch in the garden: the cake with the candles still in it
   ...
  why any picture is in or out: immich-memories runs why <asset id> --run 20260913_083421_9dcb
```

The two timings are wall-clock around the calls as they happen. `runs story <run>` prints the
whole cut again later, `runs why <asset id>` says where one picture passed or where it was
dropped and why, and `runs show <run>` has the model spend (calls, cache hits, tokens). See
[runs](./runs.md). The reasons are written for every run; `--trace-selection` only adds a copy of
the funnel at a path you choose.

## Output

`--output` names the file you want, not the path you get. Every run writes into its own folder,
named after the file plus the run id, so a rerun never overwrites an earlier result:

```bash
immich-memories generate --year 2025 --output ~/Videos/summer.mp4
# writes ~/Videos/summer_20260105_143052_a7b3/summer.mp4
```

Without `--output` the file lands in the configured output directory (`~/Videos/Memories/` by
default) as `{person}_{memory-type}_{date}.mp4`. Nothing prunes those folders; `runs delete`
removes a run and its output.

## Upload

```bash
immich-memories generate --year 2024 --upload-to-immich --album "2024 Memories"
```

The album is created if it does not exist. Without `--album` the video is a standalone asset.
The persistent form is `upload.enabled: true` and `upload.album_name` in the config.

## Two ways to skip the video

`--dry-run` is the cheap preview: it discovers the inputs and reports what preparation the
period still needs (which producers are missing, how many pictures have no facts). Nothing is
selected, so there is nothing to trace.

`--no-render` selects for real, with every reading and every gate, and stops at the encode. The
pictures it lists are the pictures it would have shipped, and the run is on record like any
other. Use it to compare settings, or to time selection without paying for an encode you will
delete.

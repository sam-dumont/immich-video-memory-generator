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

The tables below are the flags as `--help` prints them. The [CLI reference](../../reference/cli-reference.md)
is generated from the same source and wins on any disagreement.

## Flags

### Time period

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--year` | `-y` | int | n/a | Calendar year |
| `--start` | n/a | `YYYY-MM-DD` | n/a | Start date. With `--end` it overrides any memory type's own range |
| `--end` | n/a | `YYYY-MM-DD` | n/a | End date (with `--start`) |
| `--period` | n/a | string | n/a | Length from `--start`: `30d`, `2w`, `6m`, `1y` |
| `--month` | n/a | int | n/a | Month 1 to 12 (with `--year`); with `--memory-type trip` it selects the trip by month |
| `--day` | n/a | `YYYY-MM-DD` | today | The day the memory is about. `special_day`: a catalogued day. `on_this_day`: the anniversary to look back from |
| `--years-back` | n/a | int | per type | `on_this_day`: all years (30 max) unless set. `holiday` and `--birthday`: 5 |

Dates are `YYYY-MM-DD`, always. `--birthday` also takes `MM-DD`. Slashed or day-first forms are
refused with an error naming the format, never guessed.

### Memory type

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--memory-type` | n/a | choice | n/a | `year_in_review`, `season`, `person_spotlight`, `multi_person`, `monthly_highlights`, `on_this_day`, `album`, `trip`, `holiday`, `special_day` |
| `--from-album` | n/a | string | n/a | An Immich album (name or id) as the pool. Implies `album`; cannot be combined with a time period or a person |
| `--person` | `-p` | string | n/a | A person from Immich face recognition, repeatable |
| `--person-match` | n/a | `and` / `or` | `and` | With several `--person`: everyone in each picture, or any of them |
| `--people-expression` | n/a | string | n/a | Quoted full names with `AND`, `OR` and parentheses, evaluated per picture. Use instead of `--person` |
| `--birthday` | `-b` | flag or `MM-DD` | n/a | The year ending on a birthday, plus earlier birthdays. Bare flag reads the birth date from Immich |
| `--holiday` | n/a | name or `MM-DD` | n/a | With `holiday`: `new_year`, `valentines`, `easter`, `mothers_day`, `fathers_day`, `halloween`, `thanksgiving`, `christmas_eve`, `christmas`, `new_years_eve`, or any date |
| `--season` | n/a | choice | n/a | `spring`, `summer`, `fall`, `autumn`, `winter` (with `season`) |
| `--hemisphere` | n/a | `north` / `south` | `north` | For the season dates |
| `--event-id` | n/a | string | n/a | An exact catalogue event id (with `special_day` and `--day`) |
| `--trip-index` | n/a | int | n/a | One trip from the discovery table (with `trip`) |
| `--all-trips` | n/a | flag | off | Every detected trip, one video each |
| `--near-date` | n/a | `YYYY-MM-DD` | n/a | The trip closest to a date |
| `--accept-any-provenance` | n/a | flag | off | Keep forwarded and re-encoded media (the messaging-app imports the default filter drops) |

### Output

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--duration` | `-d` | int | per type | Target length in seconds |
| `--short-form` | n/a | `15` / `30` / `60` / `90` | n/a | Sets the duration and makes the video vertical |
| `--orientation` | n/a | choice | `landscape` | `landscape`, `portrait`, `square` |
| `--resolution` | `-r` | choice | config value; `auto` matches source clips | `auto`, `4k`, `1080p`, `720p` |
| `--scale-mode` | `-s` | `blur` / `fit` | config, else `blur` | Blurred background or black bars on an aspect mismatch |
| `--transition` | `-t` | choice | `smart` | `smart` (fades and cuts), `cut`, `crossfade`, `none` |
| `--quality` | `-q` | choice | config | `high`, `medium`, `low` |
| `--format` | n/a | choice | config | `mp4`, `h265`, `prores` |
| `--output` | `-o` | path | config dir | The file name you want; see [Output](#output) |
| `--title` / `--subtitle` | n/a | string | template | Override the title screen text |
| `--llm-title` | n/a | flag | off | Ask the model for the title. `--title` still wins; a failed call falls back to the template |
| `--add-date` | n/a | flag | off | Caption each clip with its date, bottom right, worded relative to the memory's span |
| `--add-place` | n/a | flag | off | Caption a clip with `CITY, COUNTRY` when the place changes, top left |

### Photos and music

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--include-photos` / `--no-photos` | n/a | flag pair | on | Photos as animated clips beside the videos |
| `--photo-duration` | n/a | float | `4.0` | Seconds per photo |
| `--include-live-photos` / `--no-live-photos` | n/a | flag pair | on | Live Photo clips, merged when burst-captured |
| `--music` | `-m` | path or `auto` | config | A file, or `auto` to generate from config |
| `--no-music` | n/a | flag | off | No music at all |
| `--music-volume` | n/a | float | `0.5` | 0.0 to 1.0 |

### Modes

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--dry-run` | n/a | flag | off | Discover the inputs and report what preparation is missing. No selection, no video |
| `--no-render` | n/a | flag | off | Select for real, stop before the encode |
| `--include` | n/a | asset id, repeatable | n/a | Keep this picture in the cut even if the editor would drop it. The same as ticking it on the pool page |
| `--exclude` | n/a | asset id, repeatable | n/a | Leave this picture out. The same as unticking it |
| `--trace-selection` | n/a | path | n/a | Also write the stage-by-stage funnel to a file of your choosing |
| `--privacy-mode` | n/a | flag | off | Blur every frame, scramble the audio, fake the names (for demos) |
| `--upload-to-immich` | n/a | flag | off | Upload the video back to Immich |
| `--album` | n/a | string | n/a | The album to upload into, created if missing |
| `--keep-intermediates` | n/a | flag | off | Keep the work files |
| `--quiet` | n/a | flag | off | No progress display, log lines only |

When `--resolution` is omitted, the command uses `output.resolution` from the config (1080p by
default); pass `--resolution auto` to let the sources choose. `--quality` changes the effective CRF,
mapped onto each hardware encoder's own scale.

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

---
sidebar_position: 1
title: generate
---

# generate

`immich-memories generate` pulls pictures and video from your Immich library, reads the period as a
story, keeps the pictures that carry it, and renders a cut. It prepares missing facts for the whole
period first, so the first run over a period is the slow one: the second is mostly the render. The
audience is always "family", which is part of the request rather than a setting.

The CLI and web UI pass the same timed source clips to the editor, including
short videos and those with unknown duration. The editor checks whether they can
be used. The selection count includes photos when photos are enabled.

Without `--duration` the length comes from the material the period holds, and the run prints what
decided it: see [how long a film runs](../memory-types.mdx#how-long-a-film-runs).

`--duration` budgets the finished film: selection reserves the opening, ending and
dividers, and credits expected crossfade overlap. Smart transitions use stable source
IDs, so rerendering the same cut keeps the same transition choices. A selection with
too little useful material can finish early. Older saved timing plans need replanning
to use the new budget.

```bash
immich-memories generate [OPTIONS]
```

Every flag, with its default, is in the generated
[CLI reference](../../reference/cli-reference.md#generate), or in `generate --help`. This page is
what the flags do not tell you.

`--resolution` takes the config value; `auto` matches source clips. When `--resolution` is omitted,
the command uses `output.resolution`, 1080p by default. `--quality` changes the effective CRF,
mapped onto each hardware encoder's own scale.

`--orientation` defaults to `auto`, which follows the majority orientation of the final kept clips.
Use `landscape`, `portrait` or `square` to set the canvas yourself. Orientation changes rendering
only; it does not change which pictures or video intervals are selected.

Opening titles name the people or the occasion, never the query that produced them. A people or
occasion memory is named by the model as soon as a reader is configured, from the family record and
the facts the run already holds; `--llm-title` extends that to trips, and `--no-llm-title` pins the
template, which is what a comparison run across months wants. `--title` and `--subtitle` override
all of it. See [titles](../titles-and-music.md).

Two root options go before `generate`: `-v` (or `--log-level DEBUG`) for verbose logs, and
`--preset fast` for the CPU-only profile on every knob you did not set.

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

# The same people, forever: no dates, so the window starts at their birth dates
immich-memories generate --memory-type multi_person \
  --people-expression '("Alex Smith" OR "Morgan Smith") AND "Riley Smith"'

# This day across the years, pinned so the run is repeatable
immich-memories generate --memory-type on_this_day --day 2026-08-31 --years-back 20

# Five Christmases in one cut
immich-memories generate --memory-type holiday --holiday christmas --years-back 5

# A day the catalogue found (see discover-days); the title comes from the catalogue
immich-memories generate --memory-type special_day --day 2016-06-12

# A day it never found; the day is the scope and the model writes the title
immich-memories generate --memory-type special_day --day 2021-04-04

# Vertical, 30 seconds, for Reels or Shorts
immich-memories generate --year 2025 --month 8 --short-form 30
```

Three things the examples hide:

- `--people-expression` takes exact library names, binds `AND` tighter than `OR`, and works on
  date-range memories (months, years, seasons). Trips, albums and single-person presets refuse it.
- A person or multi-person memory with no dates at all is not an error. It runs from the first day
  one of its pictures could exist to today, read off the birth dates Immich holds (and `people.yaml`
  where Immich holds none). See [memory types](../memory-types.mdx#a-people-memory-with-no-dates).
  With no birth date on record anywhere it still asks for `--year`, and says why.
- Moving holidays are computed for each year (Easter, Thanksgiving, Mother's and Father's Day), with
  a window of two days either side. A holiday cut runs 60 seconds unless you pass `--duration`.
- `special_day` works on any day, catalogued or not, and refuses only without `--day`. A day with a
  catalogue row is scoped and named by it; a day without one is scoped to itself, named by `--title`
  or by the model from the day's own facts, and never written back to the catalogue.
  `immich-memories days-due` lists what the catalogue holds.

## Trips

With `trips.homebase_latitude` and `trips.homebase_longitude` set, the tool finds clusters of
GPS-tagged pictures at least 50 km away spanning at least 2 nights, split when the gap between
pictures passes 2 days. The thresholds are in the
[config reference](../../reference/config-reference.md#trip-detection).

```bash
immich-memories generate --memory-type trip --year 2024                  # a table of trips, no video
immich-memories generate --memory-type trip --year 2024 --trip-index 2   # one of them
immich-memories generate --memory-type trip --year 2024 --all-trips      # all of them
```

A trip over New Year is one trip, not two.

## What the terminal shows while it runs

One line per stage, the same record the web UI draws its rows from. A stage that counts its work
gets a bar with an estimate for that stage, not for the whole cut:

```text
⠿ Preparing previews: 352/9814 · ~19m left in this stage ━━━━━━━━━━━   3%
  ⏱ 0:41 elapsed
```

A reader that stops answering is named on the line rather than going quiet. Three drops, two then
four seconds apart, and the run fails naming the endpoint.

## What a run leaves behind

Every run ends with the cut in the order it plays, then a pointer:

```text
Memory generated in 42s
  measured this run
    selection                   11s   6 planned from 6 candidates
    generation                  31s

  CHECK 2 pictures to check before sharing · immich-memories runs why <asset id> --run 20260913_083421_9dcb

  the cut, in order (6 shots, 0:24)
  June 2024
   0:00  2024-06-08  video     4 s  Lunch in the garden: the table and chairs still out on the lawn
   0:04  2024-06-09  photo     4 s  Lunch in the garden: the cake with the candles still in it
   ...
  why any picture is in or out: immich-memories runs why <asset id> --run 20260913_083421_9dcb
```

The two timings are wall-clock around the calls as they happen. [`runs`](./runs.md) reads all of it
back later. The reasons are written for every run; `--trace-selection` only adds a copy of the
funnel at a path you choose.

The CHECK line counts the shots in the finished cut that the sensitive-content detector read between
0.2 and 0.5 and that nothing else already holds to family viewing. It is a list, not a gate: no shot
was removed or changed for it. The shots themselves are in `review-before-sharing.private.json` in
the run attempt directory, and `runs why` names one when you ask about it. A run with none prints
`CHECK 0 pictures to check before sharing`, which says the run looked.

## Output

`--output` names the file you want, not the path you get. Every run writes into its own folder,
named after the file plus the run id, so a rerun never overwrites an earlier result:

```bash
immich-memories generate --year 2025 --output ~/Videos/summer.mp4
# writes ~/Videos/summer_20260105_143052_a7b3/summer.mp4
```

Without `--output` the file lands in `output.directory` (`~/Videos/Memories/`) as
`{person}_{memory-type}_{date}.mp4`. Nothing prunes those folders; `runs delete` removes a run and
its output.

`--upload-to-immich --album "2024 Memories"` creates the album if it does not exist; the
persistent form is `upload.enabled: true` and `upload.album_name`.

The memory is filed on the day of its last picture, in the timezone most of its pictures share, so
it sits in your timeline where the memory ends instead of on the day you rendered it. The render
day is what you get when no picture in the cut carries a usable time.

## Two ways to skip the video

`--dry-run` is the cheap preview: it discovers the inputs and reports what preparation the period
still needs. Nothing is selected, so there is nothing to trace.

`--no-render` selects for real, with every reading and every gate, and stops at the encode. The
pictures it lists are the pictures it would have shipped, and the run is on record like any other.
The plan it prints ends with the title and subtitle the film would open on, so a title can be tried
without producing a file. Use it to compare settings, or to time selection without paying for an
encode you will delete.

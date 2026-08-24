---
sidebar_position: 1
title: generate
---

# generate

The main event. `immich-memories generate` pulls videos from your Immich library, analyzes scenes, picks the best moments, and assembles them into a compilation.

## Usage

```bash
immich-memories generate [OPTIONS]
```

## Flags

### Time period

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--year` | `-y` | int | — | Year to generate (calendar year by default) |
| `--start` | — | string | — | Start date (`YYYY-MM-DD` or `DD/MM/YYYY`). Overrides memory type date range when combined with `--end` |
| `--end` | — | string | — | End date (use with `--start`) |
| `--period` | — | string | — | Period from start date (e.g., `6m`, `1y`, `2w`, `30d`) |

### Memory type

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--memory-type` | — | choice | — | `year_in_review`, `season`, `person_spotlight`, `multi_person`, `monthly_highlights`, `on_this_day`, `trip`, `holiday`, `then_and_now` |
| `--holiday` | — | text | — | Holiday name or `MM-DD` (with `--memory-type holiday`) |
| `--from-album` | — | string | — | Generate from an Immich album (name or ID) instead of a date range. See [Album Memories](../memory-types/album-memories). Cannot be combined with any time-period or person flag |
| `--person` | `-p` | string | — | Person name from Immich face recognition (repeatable: `--person "Alice" --person "Bob"`) |
| `--birthday` | `-b` | flag/string | — | Use birthday-based year. Bare flag auto-detects from Immich; or pass `MM/DD` to override |
| `--season` | — | choice | — | `spring`, `summer`, `fall`, `autumn`, `winter` (use with `--memory-type season`) |
| `--month` | — | int | — | Month 1-12 (with `--year`, generates that month; selects trip by month) |
| `--hemisphere` | — | choice | `north` | `north` or `south` (for season date calculation) |
| `--years-back` | — | int | per type | Years to look back. Omitted: all years for `on_this_day` (30-year max), 5 for `holiday`, 10 for `then_and_now` |

### Output

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--duration` | `-d` | int | — | Target duration in seconds |
| `--short-form` | — | choice | — | `15`, `30`, `60`, or `90` — sets the duration and goes vertical |
| `--orientation` | — | choice | `landscape` | `landscape`, `portrait`, or `square` |
| `--resolution` | `-r` | choice | config value; `auto` matches source clips | `auto`, `4k`, `1080p`, or `720p` |
| `--scale-mode` | `-s` | choice | config/`blur` | `blur` (blurred background) or `fit` (black bars) |
| `--transition` | `-t` | choice | `smart` | `smart`, `cut`, `crossfade`, or `none` |
| `--quality` | `-q` | choice | config value | `high`, `medium`, or `low` |
| `--format` | — | choice | config value | `mp4`, `h265`, or `prores` |
| `--output` | `-o`, `-O` | path | auto | Where to write — see [Output](#output); the file lands in a per-run folder beside it |
| `--title` | — | string | — | Override title screen text |
| `--llm-title` | — | flag | off | Ask the LLM for the title instead of using a template |
| `--subtitle` | — | string | — | Override subtitle text |
| `--add-date` | — | flag | — | Caption each clip with its capture date |
| `--add-place` | — | flag | — | Caption each clip with where it was taken |

`--add-date` writes the clip's own capture date in the bottom-right corner,
in bold uppercase using the same Outfit face as the title screens, worded
relative to the memory's span: inside a single-month memory the month is the
video's own premise, so the caption reads `SUNDAY 10`; a memory spanning
months within one year reads `10 AUGUST`; only a multi-year memory spells out
`10 AUGUST 2025`. Day and month names follow the configured locale
(`title_screens.locale`; `auto` follows the host machine's language — deployed
next to Immich, that is the server's locale): a French library says
`DIMANCHE 10`. Size and corner insets scale with the frame and are identical
in portrait and landscape. A dark outline keeps white text readable over
bright content; on HDR output the caption is drawn at HLG graphics white
rather than full white, which would otherwise glare above the picture's own
diffuse white. Title cards and clips without a date are left alone.

`--add-place` adds the location Immich recorded for the clip, as
`CITY, COUNTRY`, in the top-left corner — the date keeps the bottom right.
The place is shown when it *changes*: the first clip in `Nice, France` is
captioned, the clips that stay there are not, and coming home captions the
first clip back. A clip without a location does not reset the run, so EXIF
gaps inside one event stay quiet.

Place names bring characters FFmpeg's text renderer treats as syntax. A colon
would break the filter outright, and an ASCII apostrophe is silently *dropped* —
`L'Aquila` rendered as `LAquila` — so apostrophes are written as the typographic
`’`, which is the correct mark anyway.

### Short-form

`--short-form 30` is the vertical formats in one flag: it sets the duration and
makes the output portrait, which is what Reels, Shorts and TikTok take.

```bash
immich-memories generate --year 2025 --month 8 --short-form 30
```

The preset fills gaps rather than overruling you. An explicit `--duration` wins,
and so does an explicit `--orientation` — square short-form is a real format, so
`--short-form 30 --orientation square` gives you 30 seconds in a square frame.

Titles keep more clearance on a vertical render: 16% of the width on each side
instead of 10%. A title is centred, and the column of action buttons those apps
draw down the right-hand side sits across the middle of the frame — exactly
where a centred title lands. The text shrinks to fit rather than sliding, so it
stays centred and out from under the buttons.

With `--orientation portrait` the caption is inset further from the bottom —
about a sixth of the frame height — to clear the captions, handle and action
rail that Reels, Shorts and Stories draw over the lower part of a 9:16 video.
Landscape output keeps the tighter inset, having no chrome to dodge.

When `--resolution` is omitted, the command uses `output.resolution` from the config (1080p by
default). Pass `--resolution auto` explicitly when you want the source clips to choose the output
tier. `--quality` changes the effective CRF preset; an explicit `output.crf` in config remains the
more precise control. The app passes it directly to software H.264/H.265 and translates it for
Apple VideoToolbox; other hardware backends retain their existing quality policies.

### Preset (root option)

`--preset fast` is a root option — it goes before `generate`: `immich-memories --preset fast generate --month 6`.
It applies the CPU-only/NAS profile for this run (1080p H.264, fast encoder, medium quality,
static title backgrounds, no speech pass, photos ≤25 %, favorites-first analysis) to every knob you
have not set explicitly; the flags below still win. Persistent form: `preset: fast` in
`config.yaml` or `IMMICH_MEMORIES_PRESET=fast` — see the [config reference](../../reference/config-reference.md#preset).

### Analysis

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--refinement-passes` | — | integer | `10` | How many times selection may verify, judge and review before it settles. Three loops run up to this many times, so it is the largest multiplier on a warm run — and on the bill if `llm.base_url` is a paid API. `preset: fast` uses 3 |
| `--analysis-depth` | — | choice | `auto` | `auto` (all manageable cache misses, shortlist large pools), `fast` (favorites first), or `thorough` (every eligible clip). Under `preset: fast`, `auto` runs as `fast` |
| `--include-photos` | — | flag | — | Include photos alongside videos |
| `--photo-duration` | — | float | `4.0` | Seconds per photo clip (use with `--include-photos`) |

### Music

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--music` | `-m` | string | — | Path to audio file, or `auto` to generate from config |
| `--no-music` | — | flag | — | Disable all music (skip files and AI generation) |
| `--music-volume` | — | float | `0.5` | Music volume 0.0-1.0 |

### Modes

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--dry-run` | — | flag | — | Cheap preview: cached analysis only, verify pass skipped, no video |
| `--no-render` | — | flag | — | The real selection — analysis, verify, judge, review — stopping before the encode |
| `--privacy-mode` | — | flag | — | Blur all video and mute speech |
| `--include-live-photos` | — | flag | — | Include Live Photo video clips (merged when burst-captured) |
| `--keep-intermediates` | — | flag | — | Keep intermediate files for debugging |
| `--quiet` | — | flag | — | Suppress interactive progress, emit log lines only |
| `--trace-selection` | — | path | — | Write a stage-by-stage report of how the clips were chosen |

### Upload

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--upload-to-immich` | — | flag | — | Upload generated video back to Immich |
| `--album` | — | string | — | Album name for uploaded video (created if missing) |

### Trip-specific

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--trip-index` | — | int | — | Select a specific trip by index (use with `--memory-type trip`) |
| `--all-trips` | — | flag | — | Generate a video for every detected trip (use with `--memory-type trip`) |
| `--near-date` | — | string | — | Select trip closest to this date (`YYYY-MM-DD`, use with `--memory-type trip`) |

## Examples

### Calendar year

Grab all videos from January 1 to December 31, 2024:

```bash
immich-memories generate --year 2024
```

### Birthday year

Auto-detects the birthday from Immich when `--birthday` is used as a flag:

```bash
immich-memories generate --year 2024 --birthday --person "Emma" --duration 900
```

Or specify manually: `--birthday 07/21`.

### Person spotlight for a single month

Narrow a person spotlight to just February:

```bash
immich-memories generate --memory-type person_spotlight --person "Alice" --year 2026 --month 2
```

### Override any preset with custom dates

`--start/--end` overrides the date range for any memory type:

```bash
immich-memories generate --memory-type person_spotlight --person "Alice" \
  --start 2025-02-01 --end 2025-03-31
```

### Custom date range

Just the summer:

```bash
immich-memories generate --start 2024-06-01 --end 2024-08-31
```

### Period-based

Six months from a start date:

```bash
immich-memories generate --start 2024-01-01 --period 6m
```

### On This Day — all years

Look back across all years with data (not just the last 5):

```bash
immich-memories generate --memory-type on_this_day
```

Or limit to the last 3 years: `--years-back 3`.

### The same holiday, across the years

A holiday is the one date a library reliably has every year, so this spans them
rather than covering a single occasion:

```bash
immich-memories generate --memory-type holiday --holiday christmas --years-back 5
```

Known names: `new_year`, `valentines`, `easter`, `mothers_day`, `fathers_day`,
`halloween`, `thanksgiving`, `christmas_eve`, `christmas`, `new_years_eve`.
Anything else can be given as a date — `--holiday 07-04` — so a household's own
occasion works without being on the list.

Moving holidays are computed, not looked up in a table: Easter, Thanksgiving,
Mother's and Father's Day land on the correct date in each year, including years
nobody has thought about yet. The window is the holiday ±2 days by default, so
Christmas Eve and Boxing Day belong to Christmas.

Without `--duration` this runs 60 seconds. The usual duration scaling reads the
span between the first and last date, which for five Christmases is five years —
a length meant for one continuous stretch, not a handful of days repeated. The
number of years does not change the target; pass `--duration` for a longer cut.

### Then and now

Two whole years, far apart, in one video:

```bash
immich-memories generate --memory-type then_and_now --year 2025 --years-back 10
```

That covers 2015 and 2025. Whole years rather than narrow windows, because the
contrast is the point and a two-day window a decade ago is usually empty.

Without `--duration` this runs 45 seconds — two years side by side, not the ten
that separate them.

### Trip closest to a date

Find and generate the trip closest to a specific date:

```bash
immich-memories generate --memory-type trip --year 2024 --near-date 2024-07-15
```

### With photos

Include photos alongside videos:

```bash
immich-memories generate --year 2024 --include-photos --photo-duration 5.0
```

## Time Period Options

| Method | Flags | What you get |
|--------|-------|-------------|
| Calendar year | `--year 2024` | Jan 1 2024 to Dec 31 2024 |
| Birthday year | `--year 2024 --birthday --person "Emma"` | Birthday to birthday (auto-detected from Immich) |
| Single month | `--year 2024 --month 7` | Jul 1 to Jul 31 2024 |
| Custom range | `--start 2024-06-01 --end 2024-08-31` | Exact start and end dates |
| Period from start | `--start 2024-01-01 --period 6m` | 6 months from the start date |
| Override preset | `--memory-type season --season summer --start 2024-07-01 --end 2024-07-31` | Custom dates with preset scoring |

Date formats: `YYYY-MM-DD`, `DD/MM/YYYY`, or `MM/DD` (for `--birthday` manual override).

Period format: number + unit (`d` days, `w` weeks, `m` months, `y` years). Examples: `90d`, `2w`, `6m`, `1y`.

## Output

`--output` names the file you want, not the path you get. Every run writes into
its own timestamped folder, so a rerun never overwrites an earlier result and a
failed run's intermediates stay contained:

```bash
immich-memories generate --year 2025 --output ~/Videos/summer.mp4
```

writes `~/Videos/summer_20260105_143052_a7b3/summer.mp4`. The folder is the name
you asked for plus the run id (timestamp, then four characters so two runs in the
same second stay apart); the file keeps the name you gave it. The folder sits
where you pointed. Album runs name the file after the album rather than after
`--output`.

Nothing prunes those folders, so reruns accumulate. `immich-memories runs delete`
removes a run's output along with its record.

If you don't pass `--output`, the file lands in your configured output directory (default `~/Videos/Memories/`), inside a per-run folder, with an auto-generated name of the form `{person}_{memory-type}_{date}.mp4` — for example `all_memories_2024.mp4`, `alice_year_in_review_2024.mp4` or `alice_memories_20240207-20250206.mp4`. (The web UI names its files differently, e.g. `alice_2024_memories.mp4`.)

## Upload to Immich

Send the generated video straight back to your Immich library:

```bash
immich-memories generate --year 2024 --upload-to-immich --album "2024 Memories"
```

If an album with that name already exists, the video is added to it. If not, it's created. Without `--album`, the video is uploaded as a standalone asset.

You can also enable this permanently in your config:

```yaml
upload:
  enabled: true
  album_name: "Memories"
```

## Trip Detection

Automatically find trips in your library based on GPS data. Set your home coordinates in config, and the tool finds clusters of videos taken far from home over 2+ days.

```bash
# Discover trips from 2024 (shows a table, doesn't generate)
immich-memories generate --memory-type trip --year 2024

# Generate a specific trip (trip #2 from the table)
immich-memories generate --memory-type trip --year 2024 --trip-index 2

# Generate all detected trips at once
immich-memories generate --memory-type trip --year 2024 --all-trips
```

Without `--trip-index` or `--all-trips`, the command runs in discovery mode: it scans all GPS-tagged videos for the year, filters to those 50+ km from your homebase, groups them by temporal proximity, and shows you what it found. Cross-year trips (like a New Year's trip spanning Dec to Jan) are detected as a single trip.

First, set your home coordinates in `config.yaml`:

```yaml
trips:
  homebase_latitude: 50.8468     # Your home location
  homebase_longitude: 4.3525
  min_distance_km: 50            # How far = "away from home" (default 50km)
  min_duration_days: 2           # Min days to count as a trip
  max_gap_days: 2                # Max gap between videos before splitting trips
```

## Dry run

### Letting the LLM name the memory

By default the CLI titles a memory from a template — "Year in Review 2025",
"March 2025", "On This Day — July 4". `--llm-title` asks the configured model
instead, using the same prompt the web UI uses: the dates, the people, and what
the analyzer said about each selected clip.

```bash
immich-memories generate --year 2025 --llm-title
```

Three things worth knowing:

- **It is off by default, deliberately.** The wizard turns LLM titles on whenever
  a model is configured; the CLI does not, because a default that starts
  inventing titles makes runs before and after it incomparable. If you are
  comparing outputs across a sweep, leave it off or set it for every run.
- **`--title` still wins.** An explicit title is you typing the answer; nothing
  overrules it, and the flag is ignored when both are given.
- **It fails soft.** No model configured, a failed call, or an empty answer all
  fall back to the template. The video is never lost over a title.

It uses `title_llm` if you have configured one, otherwise `llm` — the same
resolution the rest of the pipeline uses.

### Why did selection drop that clip?

`--trace-selection` writes a funnel: every stage of selection, what it received, what it let
through, and how many **favourites** survived each step.

```bash
immich-memories generate --year 2024 --trace-selection ~/selection.txt
```

It writes **two** files — the readable funnel at the path you gave, and the same data as JSON at
the same path with a `.json` suffix, for scripting.

The report looks like this, and the marker is the point:

```
stage                  kept  lost     favorites
favorites first          38     0      38 -> 38
temporal dedup           21    17      38 -> 21
scale to duration         9    12      21 ->  0  <-- all favorites lost here
```

Selection passes a pool through a dozen filters, caps, scalers and LLM judgements. Reading the log
and inferring which one ate your clips is slow and wrong often enough to matter — a real February
started with 38 favourites and shipped none, and finding the stage responsible took several rounds
of guessing. This answers it directly.

:::warning Do not combine this with `--dry-run`
`--dry-run` skips work, and some of the work it skips is selection. Photo scoring falls back to
metadata only — the VLM scorer never runs — so a trace taken under `--dry-run` describes a
different, cheaper pipeline than the one that makes your videos. Trace a real run —
`--no-render` gives you one without the encode. See below.
:::

### Two ways to skip the video

They are not the same, and the difference decides which one you want.

`--dry-run` is the cheap preview. It analyses nothing it has not already
cached and skips the verify pass, so the clips it lists are an approximation
of the real selection. Use it to check that your criteria match the assets you
expect.

`--no-render` runs the pipeline for real — full analysis, the verify pass, the
judge and the review — and stops at the encode. The clips it lists are the
clips it would have shipped. Use it when you care about the selection itself:
tuning scoring, comparing settings, or measuring how long selection takes
without paying several minutes to encode a file you are going to delete.

Use `--dry-run` to see how many videos match your criteria without actually generating anything:

```bash
immich-memories generate --year 2024 --person "Emma" --dry-run
```

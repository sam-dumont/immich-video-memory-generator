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
| `--memory-type` | — | choice | — | `year_in_review`, `season`, `person_spotlight`, `multi_person`, `monthly_highlights`, `on_this_day`, `trip` |
| `--from-album` | — | string | — | Generate from an Immich album (name or ID) instead of a date range. See [Album Memories](../memory-types/album-memories). Cannot be combined with any time-period or person flag |
| `--person` | `-p` | string | — | Person name from Immich face recognition (repeatable: `--person "Alice" --person "Bob"`) |
| `--birthday` | `-b` | flag/string | — | Use birthday-based year. Bare flag auto-detects from Immich; or pass `MM/DD` to override |
| `--season` | — | choice | — | `spring`, `summer`, `fall`, `autumn`, `winter` (use with `--memory-type season`) |
| `--month` | — | int | — | Month 1-12 (with `--year`, generates that month; selects trip by month) |
| `--hemisphere` | — | choice | `north` | `north` or `south` (for season date calculation) |
| `--years-back` | — | int | all | Years to look back for `on_this_day` (default: all years) |

### Output

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--duration` | `-d` | int | — | Target duration in seconds |
| `--orientation` | `-o` | choice | `landscape` | `landscape`, `portrait`, or `square` |
| `--resolution` | `-r` | choice | config value; `auto` matches source clips | `auto`, `4k`, `1080p`, or `720p` |
| `--scale-mode` | `-s` | choice | config/`blur` | `fit`, `fill`, `smart_crop`, or `blur` |
| `--transition` | `-t` | choice | `smart` | `smart`, `cut`, `crossfade`, or `none` |
| `--quality` | `-q` | choice | config value | `high`, `medium`, or `low` |
| `--format` | — | choice | config value | `mp4`, `h265`, or `prores` |
| `--output` | `-O` | path | auto | Output file path |
| `--title` | — | string | — | Override title screen text |
| `--subtitle` | — | string | — | Override subtitle text |
| `--add-date` | — | flag | — | Caption each clip with its capture date |
| `--add-place` | — | flag | — | Caption each clip with where it was taken |

`--add-date` writes the clip's own capture date in the bottom-right corner, as
`5 Jan 2026` — spelled out rather than formatted by locale, so the caption reads
the same wherever it runs. Size and inset scale with the frame, so a 4K memory
and a 720p one look alike. Title cards and clips without a date are left alone.
On HDR output the caption is drawn at HLG graphics white rather than full white,
which would otherwise glare above the picture's own diffuse white.

`--add-place` adds the location Immich recorded for the clip, as `City, Country`.
Clips without one are left alone, so a memory mixing geotagged and untagged
footage captions only what it can. With both flags the caption reads
`Paris, France · 5 Jan 2026` on a single line.

Place names bring characters FFmpeg's text renderer treats as syntax. A colon
would break the filter outright, and an ASCII apostrophe is silently *dropped* —
`L'Aquila` rendered as `LAquila` — so apostrophes are written as the typographic
`’`, which is the correct mark anyway.

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
| `--dry-run` | — | flag | — | Show what would be done, don't generate |
| `--privacy-mode` | — | flag | — | Blur all video and mute speech |
| `--include-live-photos` | — | flag | — | Include Live Photo video clips (merged when burst-captured) |
| `--keep-intermediates` | — | flag | — | Keep intermediate files for debugging |
| `--quiet` | — | flag | — | Suppress interactive progress, emit log lines only |

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

Use `--dry-run` to see how many videos match your criteria without actually generating anything:

```bash
immich-memories generate --year 2024 --person "Emma" --dry-run
```

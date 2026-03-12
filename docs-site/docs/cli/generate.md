---
sidebar_position: 2
title: generate
---

# generate

The main event. `immich-memories generate` pulls videos from your Immich library, analyzes scenes, picks the best moments, and assembles them into a compilation.

## Usage

```bash
immich-memories generate [OPTIONS]
```

## Flags

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--year` | `-y` | int | — | Year to generate (calendar year by default) |
| `--birthday` | `-b` | string | — | Birthday date for year calculation (use with `--year`) |
| `--start` | — | string | — | Start date (`YYYY-MM-DD` or `DD/MM/YYYY`) |
| `--end` | — | string | — | End date (use with `--start`) |
| `--period` | — | string | — | Period from start date (e.g., `6m`, `1y`, `2w`) |
| `--person` | `-p` | string | — | Filter by person name (repeatable for multi-person) |
| `--memory-type` | — | choice | — | Memory type preset (see below) |
| `--season` | — | choice | — | Season: `spring`, `summer`, `fall`, `autumn`, `winter` |
| `--month` | — | int | — | Month 1-12 (use with `monthly_highlights`) |
| `--hemisphere` | — | choice | `north` | `north` or `south` (for season date calculation) |
| `--duration` | `-d` | int | `10` | Target duration in minutes |
| `--orientation` | `-o` | choice | `landscape` | `landscape`, `portrait`, or `square` |
| `--scale-mode` | `-s` | choice | `smart_crop` | `fit`, `fill`, or `smart_crop` |
| `--transition` | `-t` | choice | `crossfade` | `cut`, `crossfade`, or `none` |
| `--output` | `-O` | path | auto | Output file path |
| `--music` | `-m` | path | — | Background music file |
| `--dry-run` | — | flag | — | Show what would be done, don't generate |

## Memory types

The `--memory-type` flag selects a preset that configures date ranges, scoring weights, and title templates:

| Type | Description | Required flags |
|------|-------------|----------------|
| `year_in_review` | Full calendar year compilation | `--year` |
| `season` | Seasonal highlights with boosted motion scoring | `--year`, `--season` |
| `person_spotlight` | Single person focus with boosted face scoring | `--year`, `--person` |
| `multi_person` | Multiple people together | `--year`, `--person` (2+) |
| `monthly_highlights` | Single month, shorter output | `--year`, `--month` |
| `on_this_day` | Same date across multiple past years | (uses today by default) |

You can still use `--start`/`--end`/`--period` without `--memory-type` for manual date ranges.

## Examples

### Calendar year

Grab all videos from January 1 to December 31, 2024:

```bash
immich-memories generate --year 2024
```

### Birthday year

Your kid's birthday is July 21st. Generate their "2nd year" (Jul 21 2024 to Jul 20 2025):

```bash
immich-memories generate --year 2024 --birthday 07/21 --person "Emma" --duration 15
```

### Season highlights

Summer 2024 with boosted motion scoring (catches action shots):

```bash
immich-memories generate --memory-type season --season summer --year 2024
```

### Person spotlight

A year of videos featuring one person, with face scoring cranked up:

```bash
immich-memories generate --memory-type person_spotlight --person "Emma" --year 2024
```

### Multi-person

Videos where both Alice and Bob appear together:

```bash
immich-memories generate --memory-type multi_person --person "Alice" --person "Bob" --year 2024
```

### Monthly highlights

Just July 2024, shorter output (3 min default):

```bash
immich-memories generate --memory-type monthly_highlights --month 7 --year 2024
```

### On This Day

Same date across multiple past years (like Google Photos memories):

```bash
immich-memories generate --memory-type on_this_day
```

### Custom date range

Just the summer (manual, without memory type preset):

```bash
immich-memories generate --start 2024-06-01 --end 2024-08-31
```

### Period-based

Six months from a start date:

```bash
immich-memories generate --start 2024-01-01 --period 6m
```

## Output

If you don't pass `--output`, the file lands in your configured output directory (default `~/Videos/Memories/`) with an auto-generated name like `all_2024_memories.mp4` or `emma_20240207-20250206_memories.mp4`.

## Dry run

Not sure what you'll get? Use `--dry-run` to see how many videos match your criteria without actually generating anything:

```bash
immich-memories generate --year 2024 --person "Emma" --dry-run
```

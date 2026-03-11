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
| `--person` | `-p` | string | — | Filter by person name (from Immich face recognition) |
| `--duration` | `-d` | int | `10` | Target duration in minutes |
| `--orientation` | `-o` | choice | `landscape` | `landscape`, `portrait`, or `square` |
| `--scale-mode` | `-s` | choice | `smart_crop` | `fit`, `fill`, or `smart_crop` |
| `--transition` | `-t` | choice | `crossfade` | `cut`, `crossfade`, or `none` |
| `--output` | `-O` | path | auto | Output file path |
| `--music` | `-m` | path | — | Background music file |
| `--dry-run` | — | flag | — | Show what would be done, don't generate |

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

## Output

If you don't pass `--output`, the file lands in your configured output directory (default `~/Videos/Memories/`) with an auto-generated name like `all_2024_memories.mp4` or `emma_20240207-20250206_memories.mp4`.

## Dry run

Not sure what you'll get? Use `--dry-run` to see how many videos match your criteria without actually generating anything:

```bash
immich-memories generate --year 2024 --person "Emma" --dry-run
```

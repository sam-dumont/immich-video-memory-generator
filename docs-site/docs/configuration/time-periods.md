---
sidebar_position: 3
title: Time Periods
---

# Time Periods

The `generate` command needs to know what time range to pull videos from. There are four ways to specify it.

## Options

| Method | Flags | What you get |
|--------|-------|-------------|
| Calendar year | `--year 2024` | Jan 1 2024 — Dec 31 2024 |
| Birthday year | `--year 2024 --birthday 07/21` | Jul 21 2024 — Jul 20 2025 |
| Custom range | `--start 2024-06-01 --end 2024-08-31` | Exact start and end dates |
| Period from start | `--start 2024-01-01 --period 6m` | 6 months from the start date |

## Priority

If you pass conflicting flags, here's the resolution order:

1. `--start` + `--end` (custom range wins)
2. `--start` + `--period` (period from start)
3. `--year` + `--birthday` (birthday-based year)
4. `--year` alone (calendar year)

## Date formats

Both `--start`, `--end`, and `--birthday` accept:

- `YYYY-MM-DD` (ISO format): `2024-06-15`
- `DD/MM/YYYY`: `15/06/2024`
- `MM/DD` (month/day only, for `--birthday`): `07/21`

## Period format

The `--period` flag takes a number followed by a unit:

| Unit | Meaning | Example |
|------|---------|---------|
| `d` | days | `90d` = 90 days |
| `w` | weeks | `2w` = 2 weeks |
| `m` | months | `6m` = 6 months |
| `y` | years | `1y` = 1 year |

## Examples

```bash
# All of 2024
immich-memories generate --year 2024

# Emma's 2nd year (born Jul 21, 2022)
immich-memories generate --year 2024 --birthday 07/21 --person "Emma"

# Just summer 2024
immich-memories generate --start 2024-06-01 --end 2024-08-31

# First 6 months of 2024
immich-memories generate --start 2024-01-01 --period 6m

# Last 90 days from a specific date
immich-memories generate --start 2024-10-01 --period 90d

# Two weeks of a vacation
immich-memories generate --start 2024-07-15 --period 2w
```

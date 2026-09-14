---
sidebar_position: 6
title: discover-days
---

# discover-days

Scan for occasions such as weddings or birthdays and save them in
`~/.immich-memories/special-days.json`. Automation can propose them on an anniversary; the
Memory page offers them under **Surprise me**.

```bash
immich-memories discover-days --since 2020 --until 2025
```

This uses Immich metadata and the model configured under `llm:`. With a vision model it also
sends sampled thumbnails; otherwise it reasons from dates, places and recognised names.
Configure the [model connection](../../deploy/configuration/config-file.md) first.

## Scope and cost

| Option | Default | What it does |
|---|---|---|
| `--since` | 2007 | First year to scan |
| `--until` | This year | Last year to scan |
| `--per-year` | 6 | Maximum candidate days to ask the model about per year |
| `--also-skip` | unset | Additional holiday name or `MM-DD` to skip |
| `--out` | `~/.immich-memories/special-days.json` | Catalogue path |
| `--rescan` | off | Ignore and replace the existing catalogue |

The scan fetches metadata month by month and resumes by skipping years already in the
catalogue. Rerunning normally will not discover newly imported material in a previously scanned
year; use `--rescan` when you want to rebuild it. A scan that finds nothing does not replace a
nonempty catalogue unless rescan was requested.

With `llm.thinking: true`, a candidate uses a vision pass followed by a text reasoning pass.
Otherwise it uses one pass. A rejected title can add one retry. Increasing `--per-year` increases
the number of model calls; scanning many years can take hours.

## Why a day was included or missed

A candidate needs at least **20 pictures across six distinct clock hours**. Many pictures in
one short burst do not qualify. A five-hour gap ends an occasion, so a wedding continuing past
midnight can stay together. These thresholds deliberately miss some short or sparsely
photographed events. They identify candidates, not proof that an occasion matters.

Detected trips are excluded. Holidays are excluded when the pictures were near home, or lack
coordinates; pictures showing the day was away can still be considered. Both checks use your
`trips` settings, including home coordinates and distance thresholds.

The model can record an event window within a longer day. If it cannot, a dominant location can
supply a window only when trimming removes at least 45 minutes and 15% of the day's span,
leaving at least 30 minutes. An all-day event may have no narrower window.

Titles are checked against supplied places and facts. A rejected title gets one retry, then a
plain fallback if available. A day with no usable title is left out. These checks reduce
unsupported claims; review the catalogue before relying on it.

To inspect the model's per-picture observations, run:

```bash
immich-memories -v discover-days --since 2024 --until 2024 --rescan
```

This rebuilds the catalogue for the named scope. Use a separate `--out` path if you want to
inspect a year without replacing the main file. Verbose logs can contain personal details.

## Find an anniversary or make its video

```bash
immich-memories days-due
immich-memories days-due --on 2026-06-12
immich-memories generate --memory-type special_day --day 2016-06-12
```

`days-due` lists anniversaries within three days of the reference date, including across New
Year, with round anniversaries first. `--catalogue PATH` reads a different file. The optional
clock range is the event window; a value such as `9h` counts distinct clock hours containing
pictures, not elapsed duration.

`auto run` proposes due anniversaries. **Surprise me** offers all catalogued days, due ones
first. Explicit generation takes the date, title and event window from the catalogue; it
refuses a missing day or title. See [Special Days](../memory-types/special-days.mdx).

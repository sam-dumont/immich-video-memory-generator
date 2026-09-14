---
sidebar_position: 4
title: Tips & Best Practices
---

# Tips & Best Practices

## Start with a month

A one-minute monthly memory is quick to review. A shorter target reduces rendering work, but
preparation still covers every eligible picture in the period. Narrow the dates when testing
settings on a large library.

## Prepare once, reuse the work

On a slow machine, prepare a month overnight:

```bash
immich-memories prepare --year 2024 --month 6
```

[`prepare`](../cli/prepare.md) fills missing annotations without selecting or rendering. Later
cuts reuse matching records. New pictures, changed producer versions or changed model inputs can
require more work. Choose the [running mode](../../deploy/self-hosting.md) and configure its
producers before starting a large preparation job.

## Check hardware before rendering

```bash
immich-memories hardware
immich-memories preflight
```

Encoding acceleration is detected automatically when supported. It reduces rendering time;
it does not make a slow caption or reader server faster. `--preset fast` applies a CPU-friendly
profile to settings you have not explicitly set.

## Review the pool and the result

Open **Media pool** before cutting to exclude material you do not want used. After a cut, the
checkboxes show its picks: untick one to exclude it, or tick a dropped picture to request its
inclusion, then **Cut again**. The family-viewing gate can still refuse a requested picture.

In the terminal, inspect selection without making a video:

```bash
immich-memories generate --year 2024 --month 6 --no-render --trace-selection selection.txt
```

Read `selection.txt` or its JSON companion. For a completed render, `runs story` and
`runs why ASSET_ID` explain the latest cut.

Watch the first rendered result before scheduling more. Check names, dates, excerpts, music
volume and whether the length suits the material.

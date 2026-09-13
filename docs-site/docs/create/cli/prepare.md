---
sidebar_label: "prepare"
---

# `prepare`: do the expensive half, and say what it cost

Preparation is the part of a cut that looks at pixels: it fetches a preview for every eligible
picture, measures it, runs the encoder and the six public heads, runs both detectors, and asks the
caption server for a description. Everything after that (grouping, reading, selection, render) is
arithmetic and text by comparison.

Preparation is also **banked per picture**. A picture prepared today is free for every later cut,
forever, until a producer's version changes. That is what makes it worth doing on its own:

```bash
immich-memories prepare --year 2024 --month 6
```

It prepares that month and stops. No selection, no render, no video.

## Why you would want this on a NAS

On a four-core Celeron the producers cost about **1.2 seconds a picture**, an overnight job for a
10,000-picture library, and nothing at all on a rerun. Captions cost about **31 seconds a picture**
on the same box, which is four days.

Before this command the only way to find that out was to start a `generate` and watch it. Now you
can prepare a month, read the rate in your own units, and decide.

```bash
immich-memories prepare --year 2024 --month 6 --library-size 10000
```

```text
ℹ Preparing 1,440 pictures over 1 window(s)
producer        pending   s/picture   share    elapsed
previews           1440      0.0180    1.9%       26 s
pixels             1440      0.1250   13.2%      3 min
public_heads       1440      0.6070   64.0%     15 min
detectors          1440      0.1980   20.9%      5 min
total              1440      0.9480    100%     23 min

At this rate 10,000 pictures would take 2 h 38 min.
✓ 1,440 pictures prepared at 0.9480 s/picture.
```

Reading that table:

- **`s/picture`** is the producer's wall clock divided by the pictures in the scope. It is the
  number to compare between machines and the number the projection uses.
- **`pending`** is the work that producer reported for itself. On a cold scope it equals the
  pictures. On a rerun it drops to whatever was still missing, which is how you see that a second
  pass is cheap. The detector stage counts one unit per detector per picture, so its `pending` can
  be a multiple of the scope.
- **`elapsed`** is real time, so `share` tells you which producer to move to a faster machine.
- **`At this rate …`** projects `--library-size` pictures at the measured total. Use your real
  library size.

## Working through a library a month at a time

```bash
for month in 1 2 3 4 5 6 7 8 9 10 11 12; do
  immich-memories prepare --year 2024 --month "$month"
done
```

Each run is independent and resumable: interrupt one and the next picks up whatever was not
banked. The scope flags are the same vocabulary `generate` uses:

| Flag | What it means |
|---|---|
| `--year 2024` | the calendar year |
| `--year 2024 --month 6` | one month |
| `--start 2024-01-01 --end 2024-06-30` | an exact range |
| `--start 2024-01-01 --period 6m` | a period from a start date |

## What it prepares, and what it skips

`prepare` asks the same source pass `generate` asks, so it prepares exactly the pictures a cut over
that scope would prepare: archived and hidden assets are out, forwarded and re-encoded material is
out, and Live Photo components are handled the same way. It never prepares more than a cut would,
which matters when every picture is a second of CPU.

## Exit codes

- **0**: every producer produced its facts for every picture in the scope.
- **1**: facts are still missing, and the run says which producer and how many. The usual cause is
  a caption server that is not running; the counts tell you whether to fix it or to keep going.

Re-running is cheap and safe, so "run it until it exits 0" is the intended loop.

## Before the first run

Preparation needs the pinned encoder on disk:

```bash
immich-memories models fetch
```

If your caption server runs on another machine, every eligible picture in the scope is sent to it
as a 400 px JPEG tile with no metadata attached. That is the whole of what leaves this box during
preparation, see
[Network & Privacy](../../deploy/configuration/network-and-privacy.md#the-two-picture-seats).

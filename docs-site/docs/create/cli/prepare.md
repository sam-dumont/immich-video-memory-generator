---
sidebar_label: "prepare"
---

# `prepare`: the expensive half, on its own

Preparation is the part of a cut that looks at pixels: a preview for every eligible picture,
its measurements, the encoder and the six context heads, both detectors, and on the `full` tier
a caption. Everything after it (grouping, reading, selection, render) is text and arithmetic.

It is banked per picture. A picture prepared today is free for every later cut until a producer's
version changes, which is why it is worth doing alone:

```bash
immich-memories prepare --year 2024 --month 6
```

That prepares the month and stops. No selection, no video.

## What it costs, in your units

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

`s/picture` is the number to compare between machines and the number the projection uses.
`pending` is what the producer still had to do: the whole scope on a cold run, a handful on a
rerun. `share` says which producer to move to a faster machine (with
[the inference service](../../deploy/installation/inference-service.md) the heads and detectors
can run elsewhere; the table then shows a `remote_facts` row, and a `service s/pic` column beside
the wall clock saying how much of the wait was the classifiers deciding rather than the wire).
On a four-core Celeron the
producers cost about 1.2 s a picture; captions cost about 31 s a picture on the same box, which is
why the `no_captions` tier exists.

Work through a library a month at a time; each run resumes where the last stopped:

```bash
for month in 1 2 3 4 5 6 7 8 9 10 11 12; do
  immich-memories prepare --year 2024 --month "$month"
done
```

The scope flags are the ones `generate` takes: `--year`, `--year --month`, `--start --end`,
`--start --period`. It prepares exactly the pictures a cut over that scope would prepare: no
archived or hidden assets, no forwarded or re-encoded media, Live Photo components handled the
same way.

Exit 0 means every producer finished for every picture. Exit 1 means facts are still missing, and
the run says which producer and how many; a caption server that is not running is the usual
cause. A caption server that is running but answers 401 or 403 says so and names
`advanced.editorial.preparation.caption_api_key`. Rerunning is cheap, so "run it until it exits 0" is the intended loop.

## What leaves your machine

This is the consent step. Preparation is the only stage that sends pixels anywhere, and it sends
them only where you point it:

| Producer | Goes where | What is sent |
|---|---|---|
| previews, pixels | nowhere | your Immich server answers preview requests over your LAN |
| heads, detectors | nowhere by default; the inference service if `advanced.inference.facts_base_url` is set | the preview of each picture that still lacks those facts, once |
| captions (`full` tier) | the caption server at `editorial.preparation.caption_base_url` | a 400 px JPEG tile of every eligible picture in the scope, once, no metadata, plus `caption_api_key` as a bearer token if the server wants one |

Both endpoints default to `localhost`. Nothing asks a second time once you point one elsewhere,
so read [Network & Privacy](../../deploy/configuration/network-and-privacy.md#the-two-picture-seats)
before you do. The reader (the text model) is not part of preparation; what it receives is on the
same page.

Before the first run, the pinned encoder and detector files have to be on disk:

```bash
immich-memories models fetch
```

---
sidebar_position: 4
title: Twins and Near-Duplicates
---

# Twins and Near-Duplicates

You held the shutter down. You imported the same clip twice. You shot the cake from two
steps left. None of that should cost three slots in a two-minute film.

Duplicate checks start with capture time and preview hashes, then use model comparisons
when the model reader is active. Rules mode omits those model checks.

## 1. Bursts, on capture time and pixels

Photos taken within `photos.burst_window_seconds` of each other **and** within
`photos.burst_hash_threshold` bits on an average hash of their Immich previews are one
burst. Only the best frame survives it.

```yaml
photos:
  burst_window_seconds: 300   # 0 compares only identical capture timestamps
  burst_hash_threshold: 8     # hash bits two frames may differ by
```

Both conditions are required. Time alone would collapse a busy minute at a party;
similarity alone would merge the same kitchen photographed a month apart. A photo whose
preview never arrived is kept by this check. A favourite wins its burst; otherwise the
measured quality decides. A zero time window still compares identical timestamps.

This runs after preparation, so it reduces repeated pictures in the cut rather than
saving the earlier caption or detector work.

A Live Photo burst collapses the same way, to one carrier: the favourite if there is one,
otherwise the sharpest, best-exposed frame. Its siblings stay in the unit as members; they
do not come back as separate stills.

## 2. Neighbours, asked as a pair

The model sees two numbered pictures together. A positive answer normally needs a
second check with their display order reversed. For a nominated pair within the internal
10-bit hash limit, the pixel match can supply that second vote. An unreadable answer or
disagreement keeps both pictures. Matching pair decisions can be reused from the cache.

## 3. The final film, over what actually shipped

Once the cut exists, the pictures in it are checked against each other again. A pair is
*nominated* when either signal fires: hashes within 10 bits, or descriptions that read as
the same thing (Jaccard over words of four letters or more, at 0.60).

Nominated pairs are then asked as pairs, by the same question as step 2.

## Which one survives

Ranked, in order: protected carriers, then favourites, then pictures with a known quality
figure, then quality itself, then capture time, then asset id as a tiebreak.

So a favourited copy wins even at a lower resolution: you flagged that one on purpose.

Removals are recorded, not silent: each one writes which picture went, which one kept its
slot, the hamming distance, and which signal nominated the pair.

## What there is no knob for

Remove `analysis.duplicate_hash_threshold` from older configs. The loader ignores it and
logs a warning because the scorer that read it has been retired. The two configurable
thresholds are the burst settings above; later checks use internal limits.

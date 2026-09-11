---
sidebar_position: 4
title: Twins and Near-Duplicates
---

# Twins and Near-Duplicates

You held the shutter down. You imported the same clip twice. You shot the cake from two
steps left. None of that should cost three slots in a two-minute film.

Sameness is decided in three places, cheapest first, and only the last one asks a model.

## 1. Bursts, on capture time and pixels

Photos taken within `photos.burst_window_seconds` of each other **and** within
`photos.burst_hash_threshold` bits on an average hash of their Immich previews are one
burst. Only the best frame survives it.

```yaml
photos:
  burst_window_seconds: 300   # 0 disables burst de-duplication
  burst_hash_threshold: 8     # hash bits two frames may differ by
```

Both conditions are required. Time alone would collapse a busy minute at a party;
similarity alone would merge the same kitchen photographed a month apart. A photo whose
preview never arrived is always kept: redundancy is measured, never assumed.

Measured on one real June library: 64 of 303 photos gone, 21% of the pool, in groups of
up to five. This runs before anything asks a model a question, so every frame it drops is
also a model call saved.

A Live Photo burst collapses the same way, to one carrier: the favourite if there is one,
otherwise the best-scored frame. Its siblings stay in the unit as members; they do not
come back as separate stills.

## 2. Neighbours, asked as a pair

Two pictures side by side, two numbered tiles, judged in **both** arrangements: only the
two orders agreeing counts as one picture. The verdict belongs to the pair, not to
whichever pass presented them, so a verdict bought once is a cache hit everywhere.

Perceptual distance is the second vote here, never the only one. Measured on 653 real
pairs: the model contradicted itself on 39, and only 4 of those were pixel-close; its
uncertainty lives on pixel-*distant* pairs. At a corroboration distance of 10 the rule
reproduced all 653 decisions exactly while removing 30% of the calls. The first changed
decision appears at 12. That 10 is a cap, not a setting: every run recalibrates it on a
sample of your own library and may only lower it.

## 3. The final film, over what actually shipped

Once the cut exists, the pictures in it are checked against each other again. A pair is
*nominated* when either signal fires: hashes within 10 bits, or descriptions that read as
the same thing (Jaccard over words of four letters or more, at 0.60).

That 0.60 is the knee of a measured curve over 1,124,250 real pairs from the cache: 0.60
collapses 33 pairs, 0.55 collapses 74, 0.50 collapses 135. The count triples per step
below it, which is where genuinely different shots start merging. Above it, real
duplicates survive: the same child in the same hallway scored 0.70, differing only on a
t-shirt.

Nominated pairs are then asked as pairs, by the same question as step 2.

## Which one survives

Ranked, in order: protected carriers, then favourites, then pictures with a known quality
figure, then quality itself, then capture time, then asset id as a tiebreak.

So a favourited copy wins even at a lower resolution: you flagged that one on purpose.

Removals are recorded, not silent: each one writes which picture went, which one kept its
slot, the hamming distance, and which signal nominated the pair.

## What there is no knob for

There is no `duplicate_hash_threshold`. The key existed for the retired clip scorer and
the loader now refuses a config file that names it: you get a startup error naming the
key rather than a setting that loads and does nothing. The two thresholds you can still
set are the burst pair above; the other two numbers are measured caps in the code.

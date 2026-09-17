---
date: 2026-09-16
status: implemented — prompt/mood enrichment shipped with a cheap periodicity gate and bounded regenerations
issue: 1007
builds-on: 2026-09-10-story-first-selection.md, the local ACE-Step / Demucs work in #1006
replaces: the "no scoring model chosen" note at the bottom of #1007
---

# Music quality: fix the caption, then gate the take

> The soundtrack was generic because the prompt was generic. The gate is the safety net, not the fix.

## 0. The decision, restated

Issue #1007 opened as "repetitive ticking" on one 180-second control film. The
control matrix (14 renders, 2026-09-15) showed the real story: most films with
generated music were not great, and the ticking was the extreme of a broader
problem. Two fixes shipped together.

1. The **prompt/mood path was the root cause**. It collapsed the text model's
   full music judgment to one word ("calm", "happy") and two styles ("acoustic",
   "future bass"), and used only the first scene's mood for the whole film. That
   is why every upbeat memory sounded like the same metronomic future-bass loop.
2. A **cheap degeneracy gate** now scores each generated full mix and re-rolls a
   metronomic take up to `audio.max_regenerations` times, keeping the best-scored.
   It is a safety net for the case the richer prompt still gets wrong.

## 1. The evidence, measured

Retained pre-mix tracks from the control runs (`mastered_version_0.wav`) were
scored on one numpy feature: the peak of the onset-energy autocorrelation over
its zero lag, i.e. how much of the rhythm sits at a single repeat period.

| source | periodicity |
|---|---|
| curated/bundled library | 0.35-0.61 |
| the "on this day" film (only varied take) | 0.33 |
| failing generated tracks | 0.65-0.98 |
| the trip film (worst tick) | 0.96 |

The two bands do not touch. Periodicity alone separates the metronomic generated
tracks from the curated ones, so the initial two-feature gate (periodicity AND a
crest-factor spikiness) was over-fitted to synthetic metronome-vs-pad fixtures.
Real degenerate tracks are metronomic but "filled" after mastering, so spikiness
added false negatives (the person-spotlight track measured 0.97 periodic but only
2.1 spiky and slipped through). The gate is now periodicity alone, threshold
0.65; spikiness is still computed and logged because it separates a sparse tick
from a dense drone when someone is triaging a catch.

## 2. The prompt/mood root cause

The text model already produces a full judgment: a 15-way primary mood, an energy
level, a tempo, and up to five genre yields. `_music_evidence` kept only the
primary mood, and the caption layer collapsed that 15-way word onto five buckets
and two styles, reading only `scene_moods[0]`. "Dramatic" fell through to
"happy". The changed path:

- `MOOD_PROFILES` now has one profile per mood the reader can name (15), each
  with its own register and production texture, so "mysterious" and "melancholic"
  no longer arrive as "calm" and "nostalgic".
- The full judgment threads from `mood_for_cut` through `GenerationRequest` into
  `build_ace_caption_structured`. Genre yields pick the style through a
  `_GENRE_TO_STYLE` map (jazz, rock, orchestral, piano join acoustic and
  electronic); the stated energy or tempo places the BPM.
- Three new style homes mean the reader's "jazz"/"orchestral"/"rock" suggestions
  stop collapsing to future bass.

## 3. The gate and regeneration

`score_track` decodes to mono float via ffmpeg, frames the RMS envelope, and
autocorrelates the positive onset energy. `MusicPipeline.generate_music_for_video`
takes a `quality_gate`; when present it masters each take, scores it, stops early
on the first un-flagged take, and separates stems only for the winner. Auto mode
passes `score_track`, requests `1 + audio.max_regenerations` takes, and keeps the
best-scored.

## 4. What still needs measuring

The periodicity threshold is calibrated on the retained control tracks, not on a
labelled human-rated set, and a motorik EDM track would false-reject (documented
limit). The regeneration gate re-rolls the same caption, so it cannot fix a
systematically bad prompt; that is now handled at the caption. Before tightening,
measure missed failures and false rejections against the human-rated set #1007
mandates; the verdict (periodicity, spikiness, flagged) is logged per take, which
is the diagnosis-and-reproduce hook the issue asks for.
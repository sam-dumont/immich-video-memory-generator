---
sidebar_position: 0.7
title: Rules mode
---

# Rules mode: editing without a language model

Set `advanced.editorial.reader: rules` (or leave it on `auto` with no `llm.model`) and the
editor runs the same six stages with a rule answering each question a model would answer. Same
stages, same records, same storyboard; the answerer is different. It costs nothing in API fees
and runs on a 4-core NAS.

## What the rules do

| Question | Rule |
|---|---|
| Is this happening memory-worthy? | Remarkable when the day holds at least four times the median photographed day, or when its pictures are away from the usual cities (or more than 10 km from `trips.homebase_*`). Maybe when a favourite, a close family member, or a video is in it. Background otherwise |
| How do days group into stories? | A run of consecutive photographed days is a story; a day splits into two episodes when more than 90 minutes pass and the dominant place changes |
| What is the story called? | Templated from facts: the activity at the place, or the place, or the date. Never retitled, never joined |
| How much does a story weigh? | From the gate: remarkable seeds `minor`, maybe seeds `glimpse`, background gets nothing; three favourites raise a story to `major`. `dominant` exists only for single-occasion products (trip, holiday, album, special day) |
| Which pictures show a moment? | A capture group is a moment; the favourite wins it, then sharpness, then capture order. Two groups whose thumbnails hash alike are one moment |
| Does a picture stand on its own? | From the facts on its line: a favourite stands; a document, a sensitive-content hit, a blurry, dark or blown-out frame is weak; a picture with people, an activity or a real venue stands |
| Who may see it? | Any flag from the detectors (sensitive content, exposure, a child in swimwear, a review flag) keeps a picture at family-only viewing. Nothing clears a flag except you, on the pool page |

Every answer stays inside the vocabulary the model path uses, so the planners downstream do not
know which reader spoke.

## What you lose

- The thesis. A rule reader has no opinion about what the period was, and the page hides the
  quote rather than showing a templated one.
- The reason under a picture is the facts that funded it (`<story>: <n> pictures at <place>`),
  not an editor's sentence.
- Moments merged by content across capture groups; sampled duplicate review; the choice to play
  a Live Photo's motion (a Live carrier renders as a still unless its measured motion already
  earns the clip).
- Clearing a flagged-but-innocent picture for sending: it stays family-only until you clear it.

Measured against the model editor's reference cut over the same periods, the rules reader kept
100 % of the known occasions for a special day, on-this-day and album, 94 % for a person, 86 % for
several people, 67 % for a trip, and between 43 % and 62 % for a month, a season or a year. The
per-type table and the timings are on [Running modes](../../deploy/running-modes.md).

## What it needs

Nothing beyond the app on `tier: metadata_only`. With `tier: no_captions` and
`immich-memories models fetch`, the two detectors and the six context heads give the standing and
audience rules something to read. The Memory page prints one line under the title when a cut was
edited without a model: *Edited without a language model: the reasons under each picture are the
facts, not an editor's sentence.*

Rules are a degraded mode, not an equal-quality alternative. Prefiltered requests (a person, an
album, one event, a trip) survive it well; broad recaps are where the model earns its cost.

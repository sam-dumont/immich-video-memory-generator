# The allocation mechanism: discovery, thesis, and scarcity

Status: proven on the 2026-09-01 validation ladder (occasion door + thesis-first
allocation implemented and tested; scarcity regimes designed, implementation
queued behind the duration sweep). This document is the productization source.

## The chain

Allocation is the last link of a chain, and every earlier link can silently
destroy what the last one is blamed for missing:

```
admission (what the readers SEE)
  → chapter readings (what each period says)
    → global thesis (what the year WAS: the stories, the turning points)
      → allocation (slots implement the thesis)
        → selection (which moments fill the slots)
```

The 2026-09-01 audit found the year's biggest losses happened at the FIRST
link, while every fix before it had been aimed at the last two.

## Link 1 — admission: stars are indicators, never gates

Owner ruling, verbatim: "STARRED AND FAVORITES ARE INDICATORS but may entirely
miss some events."

Measured on a full-year corpus (6,502 eligible assets): description admission
compressed per chapter, and one favourite anywhere in a chapter switched that
chapter to favourites-only. Whole unstarred occasions died before any reader
saw them: a birth week (88 assets, 0 stars), a three-day capital-city stop
ending a week-long hike abroad (~150 assets, 0 stars), a first mass-start race
(106 assets in one day, 0 stars), a four-day work trip abroad (96 assets,
0 stars). The one starred trip of the year (401 stars) sailed through — the
pipeline had quietly equated "starred" with "important" and the owner stars
almost nothing outside trips.

The fix is the **occasion door** (`scripts/probe_description_allocation.py`),
and it needs no model:

- **Mass flag**: a day whose asset count reaches `max(4 × median, p75)` of the
  library's photographed-day masses.
- **Away flag**: two adjacent days whose top EXIF city falls outside the top-12
  dominant cities of the corpus.
- **Blocks**: each flagged day extends one photographed day each side (the
  quiet first day of an occasion rides in with its loud second day), merged
  into runs. Never "any contiguous photographed run containing a flag" — on a
  library that photographs 300+ days a year that degenerates into one
  year-long block.
- **Admission**: up to 12 not-yet-visible moments per block, representatives
  only, reason `occasion:<block-start>`. Stars still compress what they touch;
  their absence licenses nothing.

Effect on the measured year: 306 → 564 admitted moments (+258 representative
assets). Over-admission is safe by design: admission feeds readers, it does
not select. Losing an occasion is the only unacceptable loss
(the acceptance bar), and the door makes that structural.

Tests: `tests/test_description_allocation_occasions.py`.

## Link 3 — the thesis is the importance function

Not a mass formula, not floors, not star counts. The global thesis states what
happened and which moments mattered; every layer below it implements that
ruling. Mass numbers (`turning_points`, `distinct_days`, `available_moments`)
stay in the allocation prompt as EVIDENCE of how much a chapter holds — the
prompt now says explicitly they are never the rule, and neither is dense
photography or star count.

## The validation ladder

Three rungs, run in order, never skipped:

1. **Data**: are the occasions visible in metadata at all? (Query the source
   by day/city/favourite; no model.)
2. **Ceiling read**: a context-clean strong reader gets ONLY the card wall —
   the exact `card_line + people_metadata` text the production readers see —
   and must DISCOVER the year: thesis, every occasion, turning points, plus an
   explicit "uncertain" list. Its misses are DATA gaps. Run it as a fresh
   agent so the answer key cannot leak into the reading.
3. **Production model**: only after rung 2 passes does the production model
   run the same wall. Its misses versus the ceiling read are MODEL/PROMPT
   gaps, cleanly separated from data gaps.

Measured result on the enriched wall: the ceiling read found 8/8
owner-confirmed stories blind, ranked them sanely, recovered stories the
owner had forgotten to list, resolved previously unexplained mass days, and
correctly hedged what the cards cannot establish. The data view is sufficient;
every remaining failure is a model or prompt failure.

## Compression and expansion: capacity picks a regime

Capacity = content seconds ÷ 4 (≈13 slots at 1 minute, ≈39 at 3, ≈65 at 5,
≈140 at 10). Expansion is easy — more slots mean arcs and texture. Compression
must change KIND, not just quantity:

| Regime | Condition | Behaviour |
|---|---|---|
| Abundant | slots ≥ ~2× stories | Stories told as arcs; ordinary texture allowed; current proven behaviour. |
| Tight | slots ≈ 1–2× stories | One emblem tile per story; texture dies first; chronology intact. |
| Scarce | slots < story count | Whole stories die in reverse thesis rank; survivors keep exactly one emblem; the last standing are the lifecycle headline, the peak achievement, the defining trip. |

A one-minute year is not a degraded ten-minute year; it is "the year in
twelve pictures", a legitimate editorial form of its own.

**Floors yield at scarcity.** Mechanical minimums (lifecycle anchors,
away-episode floors, evidence bindings) are backstops sized for abundance. At
scarcity they must become ranked claims that yield in reverse priority
(evidence bindings first, then away floors, then lifecycle anchors — lifecycle
survives longest), with every yielded claim logged. The current code raises
`lifecycle anchors exceed the global moment capacity` when minimums exceed
capacity: that raise site is where the scarcity protocol engages instead of
crashing.

## Standing validation: the duration sweep

Same case, same banked wall, four durations: 60s / 180s / 300s / 600s.
Descriptions are cached, so each duration costs one text phase. Grade the 60s
wall with one question: does it produce the lifecycle headline, the peak
achievement, the defining trip, the season ritual — or an arbitrary set of
tiles? That question is the intelligence layer earning its keep.

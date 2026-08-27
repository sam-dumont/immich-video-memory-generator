---
date: 2026-08-25
status: measurement — the evidence the #764 rebuild rests on
issue: 764
---

# The Funnel, Measured

> **Moved into the repository 2026-08-27**, from §1–§2 of a design note that
> lived in a local scratch directory. The design sketch around it was superseded
> the same week by `docs/designs/2026-08-25-contact-sheet-editing-process.md`,
> but these two sections were not repeated anywhere in the repository — and they
> are the *reason* selection is being rebuilt rather than tuned. Every number is
> read off a real run's decision ledger, not remembered.
>
> Read with `docs/implementation-plans/2026-08-25-existing-selection-rules.md`,
> which records what the old selector got *right* and must not be lost.

## 1. What the pipeline does today

`ClipRefiner.phase_refine()` (clip_refiner.py:619) is the whole of selection.
Nine stages, in this order, all of them arithmetic:

| # | stage | what decides |
|---|---|---|
| 1 | per-day photo cap | `photos_per_day_for(target_clips, active_days)`, then score within day |
| 2 | distribute by date | one slot per period, score within period |
| 3 | fit to Ns | `ClipScaler.scale_to_target_duration` — drops until the runtime fits |
| 4 | same-moment dedup | `keep_per_moment = _clips_per_moment(target, moments)` |
| 5 | same-thing dedup | description similarity |
| 6 | photo ratio cap | `photo_max_ratio`, lowest-scored photos go |
| 7 | duration backfill | relaxation ladder re-admits from the overflow |
| 8 | the favourite wins its moment | substitution, applied to whatever 1–7 produced |
| 9 | llm review | the only judgment; ≤ 20% of the cut per round |

Two stages run *before* this and outside the trace entirely
(`cli/_pipeline_runner.py:444`): `_drop_reencoded_sources` and
`_apply_subject_policy`. The subject policy has no `trace.record` call, so its
kills are invisible to the funnel report — the life-event-photo kill that ended
the old direction happened in a stage the trace cannot see.

## 2. What it costs — June 2023, one run

From `month-2023-06-DECISION-LEDGER.md` + `-FULL-LOG.txt` (same run):

```
source filter          282 photos from excluded sources dropped   ← correct, provenance
unified pool           35 video + 215 photo = 250
source quality         −15
subject policy         −11 (2 animal, 4 object, 5 screen)          ← untraced
                       204 candidates, 100% visually analyzed

per-day photo cap       58 kept   166 lost   favourites 18 → 4
distribute by date      31 kept    27 lost   favourites  4 → 4
fit to 57s              13 kept    18 lost   favourites  4 → 4
same-moment dedup       13          0
same-thing dedup        13          0
photo ratio cap         13          0
duration backfill       14         +1
favourite wins          14          0
llm review              12          2 ← per round, eight-plus rounds
```

Three facts, each sufficient on its own:

**The first arithmetic stage destroys 78% of the owner's own marks.** 18
favourites enter, 4 leave, and the killer is *how many photos that day had*.
Stage 8 — the law — is then applied to a pool that lost fourteen stars at stage
1. `let_the_favourite_win` substitutes within a moment from `all_analyzed`, so
it can restore some; it did not, because a moment whose every member was capped
has no seat to take back.

**The caps delete the best material.** In the later run traced in
`month-2023-06-trace.md`, the photo ratio cap dropped the strongest photograph
of the month (score 0.80, interest 0.85) plus 0.72, 0.67, 0.67; `fit to 57s`
dropped 0.66/0.85 and 0.65/0.80. The cut that shipped contains an object at
score 0.36, interest 0.4. Nothing in the judge can fix that: it never saw
either photograph.

**Judgment is 2 drops out of 14, after 191 of 204 are already gone.**
`_MAX_DROP_RATIO = 0.2` on a 14-clip cut is `max(1, 2)`. The trace shows the
same clip named and capped three rounds running before it finally went.

And the pool shrinks between runs of the same month: 204 → 151 → 141 across the
three June traces on disk. Each run kills what the last one learned.


---
date: 2026-08-27
status: accepted steering — the cut's direction, not yet implemented
issue: 764
audience: Codex, on the branch tonight
supersedes: the model-chosen Structure cut left open by the-annotation-layer.md
---

# Structure: the cut is an allocation, not a model

Written from two read-only passes over the corpus the same evening (opencode;
Fable's consolidation, which this adopts wholesale), then corrected in place by
a third pass (Fable) checking every claim against the tree: the peak claim and
the slot-filling mechanism did not survive their receipts. Everything cited is
verified in the tree, the docs, or the Codex session log.

## The one decision, stated once

**The tentative cut is arithmetic over facts you already have, and it costs
zero calls.** Structure's cut is comparative against *scarcity* — a month ships
10 of 79 moments because there is room for ten — and a tile cannot show a model
how little room there is. That was measured twice (rotation control, then a
rebuild in Cull's proven per-tile shape; both sit on their own chance floor,
and the flat partition truncates 2 times in 3). "Ask for 10" is a magnitude
question wearing a list's clothing. **Stop shaping that prompt; the door is
closed.** The cut is the one place the instrument ladder is free at every rung.

## How to build it (TDD against this)

1. **Fix capacity first, before you look** (mapping hint 12). Capacity = the
   item-per-minute budget × runtime, minus transitions/dividers. It is a ceiling,
   not an instruction to fill every slot and not a top-N rule. The survival ratio
   and the number that actually ship are outputs. Argue every threshold against
   clip capacity, never a percentage.
2. **The invariant, narrowed to the form that survives the arithmetic:**
   *every episode containing a starred asset ships ≥ 1.* Not "every episode
   survives" — June is 58 episodes into 12 slots, so that gate fails every
   correct run. Encode the starred-episode form only, and keep it a continuous
   diagnostic, not a hard assertion, until the owner settles the unit
   (decision queue, below).
3. **Admission is the mechanism, not slot-filling.** The measured core is the
   slice-4 admission tests (`handoff-slice3-to-codex.md` §3): **owner acts** —
   star, filed in an album spanning ≤30 shooting days, album cover — and
   **backward firsts** — "has this ever happened before in the prefix" over
   the four free keys Immich holds: place, person, pair, calendar date. Union,
   no ranking, no N as an input. Measured: June's 58 episodes → 8 occasions in
   1.4 s at zero calls, and every extra test's fire rate is banked
   (`~/.immich-memories-matrix/slice4-metadata-2026-08-27/bench.py` replays
   them: rare pair 18, stranger among well-known 27, album cover 19, birthday
   7, resumption 2, over 799 episodes / 14 months). **Tests admit; N caps.**
   Measured so far, admissions land near or under the ceiling (June 8 vs ~12
   slots), so overflow is the exceptional path — and "the count is an OUTPUT"
   stays true, because the tests set it. A weighted fill of N slots is a top-N
   over episodes: the shape the README's risk section warns "must not become
   the old selector's quota ladder in new words". Do not build it.
4. **Weights decide only the overflow**, when admissions exceed N, over
   rung-1/3 facts, lexicographically — sacrifice from the bottom, never
   average into a score:
   - owner acts survive before backward firsts, and an episode holding a star
     never loses its last frame (the invariant, item 2);
   - the episode contains the memory's subject (who-matters from the
     appearance distribution; a trip weights landscape, a person's memory
     weights their episodes — owner doctrine, #744);
   - presence weight — a `tier`; the family ranks itself out of the histogram;
   - coverage: **a cap per scene, not a quota per period.** Cap ~2 per scene
     (Platt's "the more you have, the less each is worth"; Hurn's heading
     tick-off), favourites exempt. A cap is the anti-domination rule. A quota
     ladder is the old selector that killed 14 of 18 favourites at stage 1.
     They look alike. They are not alike.

   There is NO "reading strength" or "peak present" axis: the peak question
   measured 0 of 12 and sits in the README's dead list, and volume ≠
   significance is paid for (the apartment viewing).
5. **Two lanes for "what's significant", and both can be true at once:**
   - **people-bearing libraries:** the appearance distribution (free, rung 3)
     — a first-appearing cluster, an onset near its own birth date, a tier —
     feeding the tests above (rare pair, stranger, birthday, resumption).
   - **single-camera and non-people libraries:** **description-novelty over
     the prefix** — bank the 400px description per asset *forever* (layer 1 by
     lifetime, instrument rung 6), significance = the novelty of the description over the
     library's prefix. In a single-camera library nothing else finds the
     pregnancy test — measured: the floor there loses exactly that.
     Unmeasured; if you build the allocation, stand this probe beside it as
     its control.
6. **The model never enters the per-memory membership decision.** The one
   pre-cut exception is the ingestion-time 400px description probe above: it
   describes an asset once, banks the words forever, and does not actuate.
   Per memory, the model enters only after the cut — as the O(1) *reader* of an
   episode and as an O(1) *fine-cut validator* over the whole cut ("does this
   hold / carry weight"). Never to choose membership.

## Bias traps you will drift into — this is why the memo exists

- **"Passes are good."** Craft's many passes is repeated *human* binary on the
  same facts. For you it is more LLM calls and more noise. When you reach for
  another pass to "tighten" the cut you are re-introducing the model that was
  proven to fail at the one question that matters. **The cut is arithmetic;
  the model is O(1) review.** The void-probe rule already existed in prose and
  was broken six times this week; it is not a guardrail if it is not checked
  before every probe runs.
- **A stored score.** No. Binary verdicts and a stored *reason*, never a
  stored 1–5. The reason survives a change of question; the score rots.
- **Reuse the model's verdict as the representative.** It can't (measured: 3%
  over random at picking a frame; clustering 46%). The workprint's medoid is a
  **coverage view** — fine where it is, not the selection. And do not
  reintroduce a peak-finder anywhere: the peak question measured **0 of 12**
  and sits in the README's dead list ("there is no peak and no `no_peak`
  verdict"), and `_keep_from_run` (`selection_selects.py`) already states that
  which frame of a same-picture run ships "is not an editorial question. It is
  deliberately not asked" — favourites survive, else the stated arithmetic
  (larger file, then ID). **The only peak instrument in the system is the
  owner's star.** Keep view, sameness and cut apart and there is no problem;
  merge them and you have regressed to the thing you replaced. An earlier
  draft of this memo called the shipped frame "a peak problem" solved by
  Selects — that claim contradicted both the dead list and the code, and one
  grep killed it.
- **"Resolve" the open conflicts to feel done.** Two stay open and are the
  owner's call, not yours to settle silently:
  - *Record mark vs junk cull* — the tree asserts one side, the docs the
    other. Surface it; do not pick.
  - *The coverage invariant unit* — do not write "zero episodes with zero
    survivors" into a gate. It fails on real grouping.
- **Add a person detector / scene classifier / embedding to "help".** No.
  Measured and rejected: a detector answers "is a person present", not "what
  is this of"; Places365 is a scene classifier on a *people* photograph;
  DINOv2 ties the hash for sameness. Closed.

## What you must not change — measured

Keep in Selects: the `reason` in the pair prompt (drop it and 126 pairs flip
same→different; a 1950s editing-room rule turned out to be a mechanistic
finding about how this model computes), the adaptive second vote, the
intersection rule, fail-open. Keep the 400px tile floor, the high token cap,
no confidence filter on transcripts, no exposure-actuates-alone, no distance
band absorbing a pair, no packing multiple pairs per sheet. `ordinary` parsing
and discard is correct — it names 259/261.

## What you built tonight is correct — three fixes before it lands

`build_structure_workprint` — a conserved chronological coverage wall, one
medoid per surviving moment, contact sheets, no cut — is right staging. But:

1. **The medoid is unmeasured at its unit.** The 46% clustering win was
   farthest-first over EPISODES (spread); tonight's code is medoid over
   MOMENTS (centrality), the opposite instinct. The June gate is the right
   response, but its verdict needs a human's eyes on the sheet, not a
   self-declared pass.
2. **Ordering.** It sits after Cull, before Selects, in the old four-pass
   flow. The annotation layer's eleven-stage order puts Selects AFTER the cut,
   inside in-cut moments only. Fine as a tracer; do not fossilize it.
3. **It has no consumer yet.** Wire Structure to read it in the same slice or
   it joins PeriodInsight as a fourth write-only artifact.
4. **Privacy.** The episode scan sends names to the local endpoint, so traces,
   review sheets and the June output carry names. Everything rendered from
   `/private/tmp/immich-structure-june-production` needs matrix-dir discipline
   — and that path is NOT in the matrix dir.

## Product hygiene, before more probing

The gap that should worry you is product, not research. `run_editorial_selection`
has zero production callers; the old selector still ships every memory; the
new path carries four write-only artifacts. Order of work:

1. **Land before probing further.** `feat/764-selects` is 48 local commits with
   no PR. Open the stacked PR. One laptop is a story waiting to happen.
2. **Re-key the cache first** (annotation layer §7, items 1–2: per-asset
   layer-1 keys, drop `ordered_group_ids` from pair requests). Tonight proved
   the cost: one annotation change stranded the whole sheet bank. Everything
   probed before that re-keying is paid twice.
3. **One consumer per artifact, same slice.** PeriodInsight,
   `recurring_threads`, the workprint: wire each into a reader or delete it.
4. **The remaining blockers are a decision queue, not model work** — the owner's
   calls still open after accepting the arithmetic cut direction:
   - the invariant's unit (three options recorded in the annotation layer);
   - record-mark vs junk-cull collision (both rules in the tree; the test
     asserts the losing one);
   - second-camera keep / demote / drop;
   The Structure direction itself is settled: admission-first (tests admit,
   capacity caps, weights break only the overflow), with the
   description-novelty probe standing beside it as the single-camera control.
   The probe remains non-actuating until it has its floor and control.
5. **Make the probe checklist mechanical.** Floor + control, built from the
   production path, banned-instruments list read: all three exist as prose and
   were each violated this week. A template header in every probe script costs
   nothing and turns six void results into zero.

## Guardrail for the whole branch

Every new model call owes a line: *which rung of the ladder answers this
question, and why 1–4 could not.* If you cannot write that line, you are
spending calls on meaning the ladder already pays for.

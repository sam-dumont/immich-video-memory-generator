---
date: 2026-08-27
status: design — owner directed, not yet implemented
issue: 764
supersedes: corpus-wide Selects as built in Tasks 7-9
---

# The Annotation Layer

> The picture has its own value. The editing says what that value is worth for
> *this* memory.

Measured 2026-08-27 against the real library. A year of a small child is 12,072
to 21,458 assets, and Selects as built costs `2 x (frames - moments)` model calls
over the whole corpus — about **13,600 calls, six hours**, and it pays that again
for the next question over the same photos. That is the defect this design fixes.

---

## 0. The bar every threshold is argued against

**Owner-stated, 2026-08-27.** Measured reduction, from the owner's own figures:

| corpus | ships |
|---|---|
| a 300-asset month | **~10 clips** |
| a 2,000-asset year | **~120 clips** |

Three to six percent survives, and transitions, month dividers and animated trip
maps consume part of even that. Roughly **10 to 15 items per minute of footage**.

So the job is not to find the best 120. It is to make sure the 120 that ship
carry the weight — the ones that say "this was great". The corpus is
**over-supplied by an order of magnitude**: the year measured here holds 1,791
favourites for ~120 slots.

### What follows from that

1. **Losing good pictures is acceptable.** Not desirable, but acceptable, because
   another good one stands behind it. A pass that spends hours to avoid dropping
   a frame that had fourteen equals is spending in the wrong place.
2. **Losing an occasion is not acceptable.** The hospital, the birthday, the trip
   — each must keep at least one frame. This is the invariant, and it is the only
   one of its kind.
3. **Emotional weight is the target, not completeness.** Coverage exists so the
   story has its beats, not so every event gets equal airtime.

### The gate assertions this implies

Both computable with no model, both cheap enough to assert on every run:

- **zero moments with zero survivors** — measured on the dense month, satisfied by
  both a model-driven and a hash-only Selects;
- **candidates per shipped slot** — the dense month offered 281 for ~45, an
  over-supply of 6x. A run that falls near 1x has cut too hard somewhere upstream
  and should say so.

**Every threshold in this document is argued against this bar and not against
"never make a wrong cut".** That earlier framing was the source of several
measurements this session that were correct and irrelevant: a band rejected for
7 wrong cuts in 550, a hash-only Selects rejected for dropping 30 frames of 719
while leaving every one of 123 moments represented.

---

## 1. The defect, in one line

**#764 caches per REQUEST. A request contains a sheet, a sheet contains a moment,
and a moment is a function of the date range.** Change the window and every key
misses.

The path being replaced did not have this problem: `asset_scores_v22` is keyed
`(asset_id, model_version)`. Per-asset annotation, banked once, reused by every
memory over those photos. That was not a shortcut taken for speed; it is a unit
that amortises.

The fix is not a bigger cache. It is caching each fact **at the unit it is a fact
about**.

---

## 2. Three layers, three lifetimes

| layer | depends on | lifetime | cost for a year |
|---|---|---|---|
| **1. The picture's own value** | nothing but the picture | **forever** | ~10 min, **no model** |
| **2. Pairwise sameness** | two pictures | **forever** | ~1,050 calls, in-cut only |
| **3. The reading and the weighting** | **the question** | per memory | scales with the scope |

Everything scope-dependent must be small, and it naturally is: a question is
always narrower than a library.

### What this answers

| the same photos, asked differently | layer 1 | layer 2 | layer 3 |
|---|---|---|---|
| "the year 2024" | paid once | paid once | ~190 calls |
| "Emile in 2024" | **reused** | **reused** | fewer, smaller corpus |
| "that trip" | **reused** | **reused** | a handful |

---

## 3. Layer 1 — the picture's own value

One record per asset, keyed by `asset_id` plus the version of whatever produced
each field. Nothing in it may depend on a date range, a moment, or a question.

| field | source | cost | status |
|---|---|---|---|
| perceptual hash | CV on the thumbnail | ~1 ms | `duplicate_hashing.py` exists |
| sharpness, contrast, exposure | `photos/frame_quality.py` | ~2 ms | **exists; #764 does not call it** |
| text boxes, coverage, strings | Immich `GET /api/assets/{id}/ocr` | ~10 ms | **2,016 assets in 22 s** |
| faces, people ids | Immich asset payload | free | already fetched |
| capture instant, GPS, favourite, filename, mime | EXIF | free | already fetched |
| subject / setting label | **open — see §6** | | |

Measured for a year: roughly **ten minutes of I/O and CPU, zero model calls**,
and it is paid once for the library rather than once per memory.

### What falls out of it with no model at all

- **`failed`** — blur below the library's own floor. Measured: the softest
  photograph of 1,725 is genuinely motion-blurred and **Cull banked it as
  `ordinary`**, so the pass under-fires. The floor is tight: unusable at 3.2,
  perfectly good at 5.9, so this is the bottom ~1%, which is what Cooke's rule
  predicts — "if a photo is not clearly bad, it survives by default".
- **Not a document, provably** — 78% of a month has zero OCR boxes. That is a
  proof, not a probability.
- **Burst families** — exact capture instant absorbs 38% of a dense month.

### The one rule that composes them

**Exposure may not actuate alone.** Of 37 blown-highlight photographs, **35 carry
real text** — they are documents, not failures — and the most blown frame in the
month is a designed announcement card, among the most valuable images in the
library. Blur is clean: **0 of the 20 softest carry any text**.

This is mapping hint 11: a record shot is judged on *"is this the only one"*, not
on *"is this good"*. A technical rule may not touch one.

---

## 3b. Sound is the other sense, and it is not free

`analysis/speech_analysis.py` and `analysis/segment_transcription.py` exist and
are wired into `unified_analyzer.py`. **The #764 editorial path references
neither**, the same way it never calls `photos/frame_quality.py`. Three separate
jobs are hiding under "audio", and they do not have the same cost or the same
home.

| job | cost | belongs |
|---|---|---|
| voice activity — where speech IS | cheap | rendering, so a cut never lands mid-sentence |
| spectrogram alignment | cheap, deterministic | rendering, merging a Live Photo burst |
| **transcription — what was SAID** | **expensive** | the episode reading, demand-driven |

**Transcription is the only signal in the pipeline that reaches a different
sense.** A birthday on a contact sheet is people around a table; "happy birthday"
being sung is a birthday. A name called out, a weight read aloud, a "look at the
camera" — none of it is visible. So it is not decoration on the visual reading;
it is the thing that disambiguates a moment the pixels cannot.

It belongs in layer 1 by lifetime — what was said in a video does not depend on
whether the question was the year or the trip, so it is banked per asset and
reused by every memory. It is the one annotation field that is **not** free, so
it is the one field acquired on demand rather than up front:

- gated by voice activity, which is cheap, as the config already does;
- run for moments in contention or in the cut, on the same rule as Selects;
- never run to fill the library speculatively.

Two measured rules carried over, so they are not rediscovered: transcribe a **30
second window** rather than the clip's exact span, because short slices were the
defect and not the model; and **do not filter on confidence** — the best real
transcripts measured 0.41 to 0.55, so a confidence gate is a length filter
pointed the wrong way.

## 4. Layer 2 — pairwise sameness

A verdict about two pictures. It must be keyed by **the two pictures**.

**Today it is not.** `VisualJudgmentIdentity` includes `ordered_group_ids`, which
for a pair request is the moment id and the pair's index within it. Moment ids
hash their membership, so scoping the corpus changes every one of them and every
pair re-asks despite identical pixels. Dropping `ordered_group_ids` from pair
requests makes these verdicts scope-free and permanent.

### The model is required here, and we know what it buys

A hash-only Selects was measured: at the threshold that reproduces the model's
survival rate it keeps 70% of the same frames for **zero calls**. What it drops is
not redundancy — it is the newborn on the hospital scale, the first bath, the
family portrait, the other children meeting him. The hash cannot separate *another
attempt at this picture* from *the next thing that happened*, and no threshold
fixes it: loose enough to reduce is loose enough to merge across moments.

Also measured and rejected: **DINOv2 ViT-S ties the perceptual hash** (286 vs 291
unanimous pairs) at 18 ms/image and 86.6 MB. **Packing 4–6 pairs per request** is
4× cheaper and changes ~20% of decisions.

What survives: the **adaptive second vote** — ask one arrangement, and buy the
second only where the pixels do not corroborate. Measured to reproduce every one
of 653 decisions exactly while removing 30% of calls, with the distance
self-calibrated per library.

---

## 5. Layer 3 — the reading, and the weighting

Scope-dependent by nature, and small because a question is narrow.

- **Episode readings** — what was this block. Language, irreducible, banked by
  asset-set hash. Re-paid when the scope changes, over the scoped corpus.
- **The insight** — one call.
- **Structure, projection, fine cut** — the edit. Four to six calls.
- **The weighting** — *"for a trip, this landscape is more important."* Applied at
  selection over cached facts. **No pixels, no calls.** A landscape is a landscape
  in every memory; only its weight changes.

### Selects becomes demand-driven

Selects runs only inside moments that reach a tentative cut, per the owner's
directive: *"get the rough idea of what we have as moment, get the best pictures,
analyze those and see if it fits… Curation based on moment → entirety."*

**This is not a shortlist, and the distinction is the whole point.** Mapping hint 8
warns against exactly this — Wassermann: *"you can become beholden to the selects
when what you really need to do is just look at the footage."* The defence is
structural: the tentative cut is formed from **moment-level readings over the full
corpus**, never from a metadata ranking, and the revisit loop reopens material.
That is *"refine without ever missing good ones by skipping them entirely at the
start"* — the answer to the measured 3-of-295 circularity.

Revisit triggers, unchanged from the owner's design: refuse-class reading
(mandatory), review-drop (one sibling check steered by the drop reason),
below-cut-median (optional). One revisit per moment per generation, K=2–3.

---

## 6. What is still unknown

| question | status |
|---|---|
| subject/setting label — classifier, or model banked per asset? | **unmeasured.** `setting` is already a closed vocabulary (#483) because free text gave 39 variants, which is a classifier's job. Calibrate against the `setting`/`category` answers the legacy path already banked. |
| does the tentative cut need pixels, or do episode readings suffice? | **unmeasured**, and it decides layer 3's cost |
| how many moments reach the cut on a real year | **unmeasured**; ~150 assumed |
| note vs record, on the ~2.4% OCR shortlist | needs the model; per-asset and banked, so it amortises |
| blur floor across libraries | bracketed to 3.2–5.9 on one month, four sample points |

---

## 6b. Things that look like free wins and are not

Every item here was measured on the real library and rejected. Each looks like an
obvious saving from the outside, which is why they are written down with their
numbers: a reader who finds one of these attractive has almost certainly not seen
the measurement.

**Do not drop the written `reason` from the pair prompt.** The parser reads only
`same`, the reason is 484 of 533 characters, and dropping it halves the call —
1.06s to 0.51s. A 30-pair sample agreed 29 times in 30. Across the 650 pairs
judged under both contracts it agrees **79%**, one-directional: **126 pairs went
from "same" to "different" and 10 the other way**. Two of the flipped pairs,
looked at rather than counted, are a woman holding a newborn in the same chair in
the same pose seconds apart, and the same baby in the same outfit on the same lap.
Both are plainly one picture. **On this model, writing the reason is part of how
the answer is arrived at.** The wire contract is `pair-v3` and it carries the
field. This was shipped as verdict-only and reverted the same day.

**Do not let a distance band absorb a pair with no model call.** Calibrated and
holdout-tested, it made wrong cuts, and the deeper problem is that a band required
to be *unanimous* against a noisy oracle can never open: the counterexample that
killed it at every hash resolution (8, 12, 16, 24) turned out to be a model error
rather than a hash collision. The model must see every pair. What is safe is the
adaptive second vote in §4, where the pixels only decide whether the SECOND
arrangement is worth buying.

**Do not pack several pairs into one request.** Four to six pairs per sheet is
4x cheaper and changes ~20% of decisions. Permutation stability is flat at 83%
across pack sizes, so the loss is the model's own noise rather than scope
contamination — but 20% of decisions is not a trade this pass can make.

**Do not swap the perceptual hash for an embedding.** DINOv2 ViT-S was measured
on the same 656 pairs with the same verdicts: unanimous band 286 against the
hash's 291. A tie, for 18 ms/image and an 86.6 MB model. Both cap at 44%, which
says the ceiling is the pairs and not the signal.

**Do not shrink the tile below 400px.** 400 to 200 quarters the pixel area and
buys 11% of the call, while agreement with the 400px answer falls to 28/30. The
vision encoder resizes to its own grid, so a smaller tile changes what the model
can SEE without changing what it costs.

**Do not cap output tokens tightly.** A 64-token cap looked like the fastest
variant at 0.49s and killed **12 of 30 calls**, truncating mid-JSON. It scored
well by having its failures leave the sample. Generation stops on its own after
~49 characters; leave the cap high.

**Do not let exposure actuate on its own** (§3), and **do not filter transcripts
on confidence** (§3b).

### Where the evidence lives

Probes, banked verdicts and the Places365 artefacts:
`~/.immich-memories-matrix/slice2b-probes-2026-08-27/`. The most valuable file
there is `data/cost/pairs.json` — 656 real pairs judged in both arrangements
under the reason-carrying contract. Nearly every decision on this page was
calibrated against it and it cost roughly 1,300 live model calls to produce.
Write-ups: `docs/implementation-plans/2026-08-27-what-the-pass-costs.md` and
`2026-08-27-visual-analysis-inventory.md`.

## 7. Order of work

1. **Fix the pair cache key** — drop `ordered_group_ids` from Q4 requests. Small,
   testable, and it is what makes "same photos, different question" free.
2. **Build layer 1** as its own stage ahead of everything, per-asset keys, wiring
   the CV and OCR that already exist.
3. **Move Cull's `failed` and the document shortlist onto layer 1.**
4. **Make Selects demand-driven** on in-cut moments.
5. **Then** the weighting, per memory type.

Tasks 4–9 are rewritten against this. The corpus-wide Selects built in Task 7
keeps its measured internals — the intersection rule, the adaptive second vote,
fail-open — and loses only its position in the pipeline.

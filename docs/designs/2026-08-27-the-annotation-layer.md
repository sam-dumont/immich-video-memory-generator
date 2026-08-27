---
date: 2026-08-27
status: design — owner directed, not yet implemented
issue: 764
supersedes: corpus-wide Selects as built in Tasks 7-9
---

# The Annotation Layer

> The picture has its own value. The editing says what that value is worth for
> *this* memory.

Measured 2026-08-27 against the real library, and it pays that cost again for the
next question over the same photos. That is the defect this design fixes.

**The year figure, derived once so it is not quoted three different ways.** Take
the larger measured year, 21,458 assets. Source eligibility and Cull remove ~4%,
leaving ~20,600. Stage A absorbs exact-instant twins at the dense month's measured
38%, leaving **~12,800 frames** across **~3,000 moments**. Adjacent pairs are
therefore `frames − moments` ≈ **9,800**, and the pass costs **1.39 calls per
pair** measured (910 for 653, after the short-circuit and the adaptive second
vote) — about **13,600 calls**.

At the **pair** call cost of ~1.06s that is roughly **four hours of model time**;
at the ~1.6s per call observed end-to-end in a real gate run, about **six**. Both
numbers appear in this corpus and they are the same measurement with and without
per-call overhead. **The sheet call cost of ~12s is a different unit and must not
be multiplied by a pair count** — earlier drafts did exactly that.

---

## 0. The bar every threshold is argued against

**Owner-stated, 2026-08-27.** Measured reduction, from the owner's own figures:

| corpus | ships |
|---|---|
| a 300-asset month | **~10 clips** |
| a 2,000-asset year | **~120 clips** |

**These are ratios, not a claim about library size.** Three to six percent
survives, at roughly **10 to 15 items per minute of footage**, and transitions,
month dividers and animated trip maps consume part of even that. The library
measured throughout this document is far larger than the table's illustration —
one month is 2,016 assets and one year is **12,072 to 21,458** — which only makes
the point sharper: the denominator grows and the numerator does not.

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

   **An occasion is an EPISODE.** The three units, stated once so they are not
   re-derived: an **episode** is a temporal-and-place block — the party, the race,
   the hospital day; a **moment** is a content-homogeneous burst inside one; an
   **occasion** is the human word for an episode and is not a fourth unit. The
   invariant is therefore *zero episodes with zero survivors*, and it is measured
   in episodes.
3. **Emotional weight is the target, not completeness.** Coverage exists so the
   story has its beats, not so every event gets equal airtime.

**What the favourite law becomes at this scale.** One measured year holds 1,791
favourites and ships ~120 clips, so "every favourite ships" is arithmetically
impossible and was never the rule. The rule, hoisted here from the superseded
Pass 2 section because it is the only formulation that scales: **each starred
episode that ships uses a starred representative.** A favourite still wins its
moment against a non-favourite, and Structure may still omit a whole episode with
a named reason — but no pass may ship an episode's non-favourite while that
episode's favourite sits unused.

### The gate assertions this implies

Both computable with no model, both cheap enough to assert on every run:

- **zero EPISODES with zero survivors** — the invariant, in its own unit. The
  earlier draft asserted this over *moments*, which is both the wrong unit and too
  weak to bite: a hash-only Selects satisfies it while dropping the newborn on the
  hospital scale and the first bath, because those frames' moments keep a
  neighbour. Episode-level is the level the invariant is about.
- **candidates per shipped slot** — the dense month offered 281 for ~45, an
  over-supply of 6x. A run near 1x has cut too hard upstream and must say so.

**Neither assertion is sufficient on its own, and this is deliberate.** They are
cheap continuous checks, not a substitute for the owner reading a gate sheet.
Anything that changes *which frame within an episode* ships passes both and still
has to be looked at.

**Every threshold in this document is argued against this bar and not against
"never make a wrong cut".** That earlier framing was the source of several
measurements this session that were correct and irrelevant: a band rejected for
7 wrong cuts in 550, a hash-only Selects rejected for dropping 30 frames of 719
while leaving every one of 123 moments represented.

---

## 0b. The instrument ladder

**Owner doctrine, stated repeatedly and repeatedly under-applied.** Every question
descends this ladder and stops at the first rung that can answer it safely. A
lower rung is not reached because it is better; it is reached because everything
above it has been tried and cannot answer.

| # | instrument | cost | examples |
|---|---|---|---|
| 1 | **metadata Immich already holds** | free | EXIF, GPS, capture time, favourite, filename, mime, dimensions |
| 2 | **what Immich has already derived** | free | face clusters (named or not), OCR text and boxes, person birth dates |
| 3 | **arithmetic over 1 and 2** | free | episodes and moments, exact-instant twins, appearance distribution, **age at capture**, **relationships**, co-occurrence |
| 4 | **classical CV on pixels** | ~ms | blur, exposure, perceptual hash |
| 5 | **a small permissive model** | ~10–30 ms | only where 1–4 cannot answer, and only where measured to work on THIS library |
| 6 | **the language model** | ~1–12 s | only for meaning, and only where the answer changes the outcome |

**Rung 2 is the one that keeps being skipped.** Immich clusters *every* face in
the library whether or not anyone has named it — 3,591 clusters here against 156
names — and it runs OCR over everything. Both were rediscovered late in this
work after being available all along. **Before proposing anything at rung 5 or 6,
check rungs 1–3.**

**Rung 3 is larger than it looks.** Arithmetic over free data yields who matters
(appearance distribution), exactly how old someone is in a given photograph
(`birth_date` + capture time), who a person was born to (onset matching a birth
date, then co-occurrence), and which day is a birthday. None of that needs pixels.

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

### People are the strongest free signal, and the pipeline barely uses them

Immich detects and clusters **every face in the library, whether or not anyone has
named it**. Measured on this library:

| | |
|---|---|
| person clusters Immich holds | **3,591** |
| of those, named by the owner | 156 |
| named **and** carrying a `birthDate` | 19 |
| dense month: assets with a recognised person | **62%** |
| dense month: assets with a person whose **age we know** | **58%** |
| dense month: distinct clusters / face appearances | 71 / 1,755 |

Four separate signals come out of that, all free, all already computed:

1. **Presence** → `category: people`, measured at **98% precision and 77% recall**
   against the language model's own labels. The 23% it misses are people
   photographed from behind or too small for a face — a person *detector* would
   close that gap, and nothing else needs one.
2. **Identity without naming.** A cluster has an ID whether or not it has a name,
   so "the same person again" is answerable across the whole library for free.
3. **Who matters, by appearance distribution.** In the dense month the top three
   clusters carry **1,295 of 1,755 appearances — 74%**, against a long tail of
   21-and-below. The family ranks itself out of the frequency histogram; naming is
   a convenience, not a prerequisite. This is the emergent doctrine applied to
   people: weights come from the library's own distribution, never from a query.
4. **Exact age at capture** wherever a `birthDate` exists — `birthDate` plus the
   capture timestamp, no inference. In the dense month **588 assets contain a
   person aged 0.0 years**.

**That last one changes what the expensive call is asked.** The thesis of that
month — a baby arrived — is derivable from metadata alone: a cluster appearing for
the first time on a known date, aged zero, in 588 photographs. The episode reading
should be *told* that and asked what else was happening, rather than sent to
discover it from pixels. It also removes a measured failure: a sheet prompt once
fabricated a name, a sex and twins, and the durable fix was better data rather
than more instruction. **An age you were handed cannot be hallucinated.**

The signal's strength varies with the material, and that is correct rather than a
weakness: the dense month is 58% age-known, the sparse cycling month 9%. It is
strongest on exactly the months that are about people.

### How `people.yaml` is consumed

The file is written once per library by `immich-memories people scan` and read by
every stage after it. **It is not an input the user must provide** — it
auto-populates from Immich and works unedited; the `confirmed:` blocks exist so a
person *can* correct it, and a refresh copies those through untouched.

| stage | what it reads | what that buys |
|---|---|---|
| **1** annotation | cluster ids on the asset, each one's `tier`, and `birth_date` + capture time | every asset stamped with **who is present and how old they were** — no pixels |
| **2** cheap cull | `tier` | an `inner`-tier face is a reason to keep, never to cut |
| **3** grouping | co-occurrence | who was there is a moment-boundary signal alongside time and place |
| **5** episode reading | who, their age, their relationship | the reading is **told** rather than asked to guess. "A person aged 3 days is in 47 of these" replaces a model inferring a newborn from pixels — and removes a measured failure where a prompt fabricated a name, a sex and twins |
| **7** tentative cut | `tier` + relationship + the memory's subject | **this is the weighting.** A memory about one person weights that person's episodes; a trip weights landscape. Same photo, different worth, no re-analysis |
| titles | relationship + `birth_date` | "their first year" is derivable; a birthday is `birth_date` matching a capture date |
| memory types | onset, span, relationship | Then-and-Now needs the same person across years; a birthday memory needs whose |

**Two contracts that must not be broken.**

*Confirmed beats inferred.* A refresh recomputes every `inferred:` block and copies
every `confirmed:` block through unchanged, and a person somebody annotated is
never dropped even if they fall off the roster. The inference is allowed to be
wrong because the user can overrule it permanently.

*Names never leave the machine.* The file holds real names of real people. It is
local state: never in a commit, an issue, a pull request, a public log, or a
model request beyond the owner's own endpoint. Where this design quotes the file
it quotes **structure and counts**, never names — and that is why every example
here says "a person aged 0.0" rather than who.

### The people graph exists, and what it does not yet infer

**This is already built.** `src/immich_memories/people/` and
`immich-memories people scan` read counts, names, birth dates, month curves and
pairwise co-occurrence out of Immich — *"nothing here looks at a pixel and nothing
here asks you a question"* — and write a hand-editable companion where
**confirmed beats inferred**. Run on this library it produced **78 people**:
13 `inner`, 34 `recurring`, 27 `episodic`, 4 `event`, plus 50 links and an
inferred owner.

Each person carries an evidence block: `count`, `onset`, `first_month`,
`last_month`, `active_months`, `span_years`, `concentration`, `continuity`.

**What it does not infer is the relationship itself.** `LinkKind` is
`tight-dyad | twin | duplicate` — "these two appear together often", never
*couple*, *parent*, *child*, *sibling* or *grandparent*.

That gap is closable from data the file already holds, with no pixels and no
model. Demonstrated on this library:

**A person whose `onset` matches their own `birth_date` was born into the
library.** Of 18 people carrying a birth date, **4 match** — birth years 2013,
2015, 2022 and 2024 — and those are exactly the children. One near-miss at 11
months is a child who entered the library after their first year, so the rule
needs a tolerance rather than an exact match.

**Measured on this library with a 12-month tolerance: 5 children found** — born
2013-11, 2015-05, 2018-05, 2022-02 and 2024-02. Four have an onset in their own
birth month; the 2018 one needs the full twelve, which is why the rule wants a
tolerance rather than equality.

**Parents do not fall out as easily, and the blocker is precise.** Filtering to
"inner tier and present before the child's onset" leaves **11–12 candidates** —
presence is not discrimination. The discriminator must be **co-occurrence with
that specific child**, and `people.yaml` does not carry it: `graph.py` computes
pairwise co-occurrence, uses it to emit `tight-dyad` and `twin` links, and
**discards the counts**. A sampled inner-tier person has `links: []`.

So the raw material is already being computed and thrown away. **Persisting one
number per pair — the count already in memory during the scan — turns twelve
candidate parents into two, at no additional cost and no new API call.** That is
the single change that unlocks the rest:

From that anchor the rest follows arithmetically:

### It infers EVIDENCE, never a relationship

**Inferring relationships is normative, and this design must not be.** Two adults
who appear with a child from their first month might be their parents, or one
parent and a partner, or grandparents raising them, or separated co-parents, or
none of those. Metadata cannot separate those cases and must not pretend to. A
tool that tells someone their family is shaped a particular way, from a face-count
histogram, is wrong in a way no accuracy number redeems.

`editor.py` already carries the right instinct: *"What inference can suggest, and
nothing more. A role the graph cannot propose is a role only the user can name,
and the free-text field is where they do."*

So the graph writes **observations**, and a person names the relationship:

| what is inferred — an observation | what is NOT inferred |
|---|---|
| `onset` ≈ own `birth_date` → **"first appears at their own birth"** | "child" |
| present before that onset, and in N% of that person's photographs | "parent", "mother", "father" |
| a tight dyad also present before the onset | "couple", "spouse" |
| a 50–70 year age gap, `episodic` tier, co-occurring | "grandparent" |
| two people who each first appear at their own birth, sharing the same close adults | "siblings" |

Each observation carries its numbers, so a person reading the file sees *why* it
was written and can disagree with the reasoning rather than only the label.

**The override contract already exists and must be used, not reinvented.**
`confirmed:` holds `role` (free text, deliberately not an enum), `links` and
`notes`; a refresh recomputes every `inferred:` block and copies every
`confirmed:` block through untouched; a person somebody annotated is never
dropped. `people/editor.py` and the `settings_people` UI page are the surfaces.
**Relationship inference must write only into `inferred:`, and every consumer must
prefer `confirmed:`** — so a wrong guess is corrected once and stays corrected.

A consumer that needs a relationship — a title saying "their first year", a
person-centred memory — reads `confirmed.role` if present, and otherwise treats
the observation as what it is: a pattern in the numbers, not a fact about a
family.

**Why it is worth closing.** A relationship is what turns a fact into a story: the
dense month is not "a cluster aged 0.0 appeared in 588 photographs", it is "their
child was born". That is the thesis, the title, and the weighting for every
person-centred memory type — and it is metadata arithmetic, not a model call.

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

## 5b. The runtime order, and what makes the tentative cut

Moving Selects after the cut left a hole the earlier draft did not close:
**Structure must choose moments before anything has chosen frames**, so a moment
is still N frames when it is judged. The hole and the `representative_tiles`
finding are the same problem — both need one frame that stands for a moment, and
the model cannot produce one (0.42 overlap by picture, 0.42 by position, against
a 0.22 random floor).

**It is a coverage question, not a quality one, so clustering answers it.**
Cluster a moment's frames by perceptual distance and take the medoid; a favourite
overrides. Deterministic, free, and position is not an input so it cannot have the
measured failure mode. Mapping hint 5 is the craft's version: headings get ticked
off, and an over-covered heading stops competing.

**Measured, and the margin is not close.** Scoring a representative set by the
mean distance from every frame in the episode to its nearest representative —
low means the wall shows you the episode — on real episodes:

| picks | mean distance to nearest representative |
|---|---|
| the model's | 4.64 |
| **farthest-first clustering** | **2.49** |
| a random k-subset | 4.79 |

**Clustering is 46% better; the model is 3% better than random.** That is the
second independent probe to reach this verdict — the rotation control found the
model tracking neither the picture nor the position (0.42 / 0.42 against a 0.22
floor). Different instruments, same answer.

*n = 5 episodes.* Thin, and it is the effect size rather than the sample that
makes this actionable: enough to build the clustering version and gate it, not
enough to declare the matter closed.

With that, the order is specifiable:

| # | stage | cost | unit |
|---|---|---|---|
| **0** | **the people graph** — clusters, tiers, co-occurrence, birth dates, **relationships** | **free** | **per LIBRARY, refreshed rarely** |
| 1 | annotation — hash, sharpness/exposure, OCR, **who is present + their age + their tier**, EXIF | free | per asset, **forever** |
| 2 | source eligibility, then cheap cull: `failed` by CV, documents routed by OCR | free | per asset |
| 3 | group into episodes and moments | free | time + place |
| 4 | **one representative per moment by clustering** | **free** | per moment |
| 5 | episode readings over the representative wall, **told who is present and how old** | ~190/yr | per episode, banked |
| 6 | the insight | 1 | per question |
| 7 | **Structure — the tentative cut**, reject-only over the representative work print | 1–2 | per question |
| 8 | **Selects inside in-cut moments only** — which frame actually ships | small | per pair, **forever** |
| 9 | projection, revise the thesis once | 0–1 | per question |
| 10 | fine cut over the whole cut | 1–2 | per question |
| 11 | duration fit, then rendering — may not change membership | free | — |

**Stage 0 is per-library, not per-memory.** `immich-memories people scan` already
builds it and it is the only stage whose unit is the whole library rather than a
question or an asset. Everything downstream reads it: stage 1 stamps each asset
with who is present and how old they were, stage 5 tells the reading rather than
asking it to guess, and the weighting in stage 7 uses tier and relationship.

**Stage 7 is the tentative cut.** It is Structure, unchanged in contract —
reject-only, named reasons, no ranking, chronology fixed — but it now runs on
*clustered* representatives instead of on Selects' output, and Selects runs after
it on the survivors. Nothing is skipped at the start: every asset reaches stages
1–4, and stage 7 sees a representative of **every** moment in the corpus.

This supersedes the design-of-record acceptance criterion
`Insight → Cull → Selects → Structure → projection → Fine Cut`.

**What remains open** is narrower than "the tentative cut is unspecified": does
stage 7 need the representative *pixels*, or do the episode readings from stage 5
carry enough? Pixels are the safe assumption and the design's own rule; the
cheaper variant is unmeasured.

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

**Do not put a scene classifier behind `setting`.** Places365 was evaluated and
fails on this material — 60% agreement even in its top confidence bucket, because
it is a *scene* classifier and a family photograph is a *people* photograph with a
room behind it; it has no person concept and grasps at whatever furniture remains
visible (a bedroom read as "hospital_room", a living room as "beauty_salon").
**More importantly the question should not have been asked at that rung at all:**
`setting`'s only consumers store it, parse it, and pass it to the review model as
context, and Immich already returns a named place — Jette, Etterbeek — for 80% of
a month, free. A real place name is better context than a five-value guess.

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

**Do not swap the perceptual hash for an embedding — for PAIR SAMENESS.** DINOv2
ViT-S was measured on the same 656 pairs with the same verdicts: unanimous band
286 against the hash's 291. A tie, for 18 ms/image and an 86.6 MB model. Both cap
at 44%, which says the ceiling is the pairs and not the signal.

**This rule is scoped to near-duplicates and must not be read wider.** A
perceptual hash encodes coarse pixel LAYOUT, so it collapses precisely where the
camera moved — and "same event, different angle" is the open question for
representatives (§6). An embedding is the expected winner there, and DINOv2 was
relicensed to Apache-2.0 in August 2023, so the licence objection no longer
holds. Same model, opposite verdicts, because they are different questions.

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

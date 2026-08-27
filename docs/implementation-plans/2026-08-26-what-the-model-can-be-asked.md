# What the Model Can Be Asked

> Measured 2026-08-26 against the real library and the production path, before
> Task 7 was written. Every number here comes from `prepare_editorial_source`,
> the real atlas, `build_contact_sheets` and `VisualEditorialGateway` at
> temperature 0 — not from a reconstructed request.

Task 7 assumed the model could pick the best frame in a moment. It cannot. This
page records what it can and cannot be asked, so the next pass is designed
around a measured capability rather than an assumed one.

---

## 1. The result

Four question shapes, same moments, same model, same fidelity.

| question | shape | result |
|---|---|---|
| "which single tile is the peak" | superlative over N | **0 of 12** answers follow the picture |
| "which tiles add nothing the others have" | reject-only over N | emits strides — `[2,4,6,8]`; kept-set Jaccard 0.57–0.72 |
| "which tiles are attempts at the same picture" | partition of N | pair Jaccard **0.15**; usually returns all-singletons |
| "is this a document / did this picture fail" | per tile, fixed definition | byte-identical across repeats, precision good (Cull, shipped) |

### How it was tested

The control is **cyclic rotation**. Under rotation by `k` every tile moves by
the same amount and no pixel changes, so a model that is looking at the
photographs names the same *picture* while a model running a positional habit
names the same *position*. Nothing else separates the two.

Rotation matters because the obvious control does not work. Reversal was tried
first and reported 3 of 3 "order-invariant" at n=3 — an artifact, because the
midpoint of three maps to itself under reversal, so "always pick the middle"
scores perfectly. All three answers at n=3 were literally tile 2.

| battle width | same picture under rotation | same position under rotation |
|---|---|---|
| 3 | 0/3 | **3/3 — always tile 2** |
| 4 | 0/3 | 0/3 |
| 6 | 0/3 | 0/3 |
| 8 | 0/3 | 0/3 |

The stated reasons stayed fluent and specific throughout — "Frame 8 captures the
baby's most alert and engaged expression — eyes wide". **The answers parse, carry
grounded-sounding prose, and raise no `!!`.** A Selects pass built on this
question would ship confident nonsense through every gate the project has.

## 1b. Structure's exemption was tested, and it does not hold

**Measured 2026-08-27.** §1 lists reject-only over N as a failing shape, and §6C
then exempts Structure: *"Structure is reject-only, the form that measurably works
on this model."* Those two statements were never reconciled, and Structure and
Fine Cut — the two passes that do all the cutting — are both reject-only over N.

The plausible defence was that Structure's question has an **external referent**
(a thesis: *"which moments does this story need"*) while the failing one points
inward (*"which add nothing the others have"*). Both were probed under cyclic
rotation on four real episodes, 12 tiles each:

| question | overlap by **picture** | overlap by **position** |
|---|---|---|
| anchored to a thesis | **0.12** | 0.33 |
| unanchored | 0.29 | 0.35 |

**Neither reads the photographs, and the anchor does not rescue it.** The raw
answers are the finding, not the averages — unanchored produced
`[1,2,3,4]`, `[2,4,6,8]`, `[1..8]` and `[1..12]`: strides and prefixes, index
patterns with fluent reasons attached.

Anchoring makes the model **more conservative** — shorter lists, several empty —
which is fail-safe and therefore easy to mistake for working. It is not more
accurate.

*n = 4 episodes, 13 comparisons.* Thin, and the raw output is damning independent
of the sample: `[1,2,3,4,5,6,7,8,9,10,11,12]` is not an editorial decision.

**What this does not settle.** Structure as specified also carries a *thesis*, the
prior passes' *reasons*, and grounded annotations — this probe gave it only the
thesis. And a 12-tile episode is not the 3,000-representative work print the
current design asks for, which is a scope problem on top of a shape problem. But
the exemption as written has no support, and **§6C's claim must not be relied on
until a probe supports it.**

## 2. Why, and what it predicts

The four rows differ in one property: whether the question has a referent
**outside the set being compared**.

Cull asks "is this a document?" — the tile is checked against a definition that
exists independently of the other tiles. Every Selects question tried points
only inward: better *than these others*, alike *to these others*. With nothing
outside the comparison to anchor on, the model anchors on position.

This predicts the failures rather than describing them, and it is the rule to
design against: **a pass may ask the model to classify against a fixed
definition. It may not ask the model to rank its inputs against each other.**

It also matches the craft. The tidy tie-breaker checklist — peak of gesture, eye
contact, clean background — appears in *no primary source* in the research;
what practitioners actually say is Soth's "It's impossible to explain why I
chose the final frame… It just felt right." Human editors cannot articulate the
comparison either. The craft's answer is never to ask it directly: duChemin,
"Pick them or don't pick them, but don't rate them"; Thein builds a rank as an
*output* of repeated binary rounds; Gilden marks on a linear sweep and only then
looks at the marked set.

## 3. Fidelity, and the one hint nobody chose

`contact_sheets.sheet_layout` served every pass one density. Measured tile sizes:

| tiles on the page | tile px |
|---|---|
| 8 | 400 |
| 35 | 300 |
| 57 (a real Cull pack) | 210 |
| 120 (the page cap) | 150 |

The answer changed between 150px and 400px in **4 of 4** moments, and did not
change between 400px and 700px in 3 of 3 that parsed. So 400px is where this
model stops gaining, and Task 7's instruction to "pack many complete separated
battles per request" would have run the pass at 150px — 86% less pixel area on
the decision the craft says needs the most.

`sheet_layout` now takes `tile_px`, so a pass states the fidelity its own
question needs instead of inheriting the page's compromise.

**`image_detail` is `"low"` on every editorial request** — the default on
`VisualEditorialRequest`, never overridden by any pass, and it reaches the wire
as `{"detail": "low"}`. `openai_image_detail` exists in config and the editorial
path ignores it. Measured, this endpoint appears to ignore the hint: 400px/low
and 400px/high gave identical answers in 4 of 4. It is a latent defect, not a
live one — but nobody chose it, and a different endpoint would honour it.

## 4. The unit was wrong too

Asked to judge "one attempt at one picture", the model objected on its own:

> "1-3 are one attempt (baby sleeping) and 4-7 are another (man posing with
> empty carrier)"

Content-grounded, unprompted, and correct. Moments are grouped by time and
place, so one moment holds several attempts at several pictures — and "which of
these eight is the peak" is then an incoherent question. Some of the positional
fallback is a rational response to being asked one.

Measured shape of real moments:

| | sparse (Jun 2023) | dense (Feb 2024) |
|---|---|---|
| candidates / moments | 261 / 81 | 1468 / 202 |
| singletons | 38 (47% of moments) | 47 (23%) |
| moments of 6+ | 9, holding **47%** of candidates | 83, holding **82%** |
| longest | 39 frames / 58 min | 73 frames / 11 min |
| chained through a drizzle (>30 min, 6+) | 1 | 0 |

Chaining is **not** the defect. `_belongs_with` compares against the moment's
last member, so it is gap-based single linkage, and median gaps inside the big
moments are 0.0–4.2 seconds — these are real bursts. The defect is that time and
place cannot separate two different things happening in one room inside ten
minutes. Only content can.

The distribution is also bimodal, and that matters for what this product is.
77 of 202 dense moments are singletons or pairs: disjointed life snapshots with
no peak to find. The other 82% of the volume is bursts, where the craft's "one
attempt at one picture" transfers for a mundane reason — you took twelve shots
of the baby because you were trying to get one good one.

## 5. Half the reduction is arithmetic

| | sparse | dense |
|---|---|---|
| exact capture twins absorb | 1 (0%) | **558 (38%)** |
| within 2 seconds absorbs | 66 (25%) | 730 (50%) |
| left needing an eye | 195 (75%) | 738 (50%) |

Not Live Photo pairs — source preparation removes those upstream and zero
rendering families were built. This is two devices photographing the same thing
at the same instant, which the moment model expects by design.

Caveat worth keeping: two devices at one instant are two *vantages*, not one
picture. Collapsing them is a real editorial choice. It is usually right for a
memory video and it will occasionally drop the better angle.

## 6. The pass this implies

**A. Arithmetic absorbs the free half.** Exact capture twins collapse to one by
a stated rule — favourite, then existing objective evidence, then first. Zero
calls, deterministic, traced. Thein's set test is "at a glance, one should not
be mistaken for another"; when two frames are the same instant, which one ships
is not an editorial question.

**B. The model is asked only questions with an external referent** — Cull's
proven shape, per tile, in a small scope. Which question earns its place is not
yet measured and should not be guessed.

**C. Selects marks; it does not reduce to one per moment.** Eisenhardt:
documentary selects keep 25–50%, and "the big cuts happen later, at structure,
not at the item filter." Structure is reject-only, the form that measurably
works on this model.

**D. Where the model actuates, only the intersection of two arrangements
counts.** Thein: "those that overlap are the ones that make it into the final
cut." Disagreement means keep, which matches the project's own asymmetry — a
wrong keep is fixed by a later pass a person can check, a wrong cut is permanent
and invisible.

## 7. Two corrections to existing doctrine

**An empty example is safe for a reject list and wrong for a partition.** Slice 1
learned to show decision lists empty, because a populated example was copied onto
unrelated visuals. That generalises incorrectly. Asked for a partition with
`{"alike": []}` in the example, the model returned an empty list five times out
of six while describing real groups in its prose field — an answer that scores as
success. Empty is the honest default only where empty means "change nothing".
Where empty means "no answer", ask for one entry per tile so a copied example is
a loud invalid answer instead of a silent one.

**A metric that scores "no answer" as agreement will report success.** Two
separate artifacts in one session: reversal at odd widths, where the midpoint is
a fixed point; and Jaccard over empty sets returning 1.0. Both produced clean
tables that meant nothing. Any stability metric needs a case for what an absent
answer scores, written before the numbers are read.

## 8. Probes

Archived beside the slice-1 probes. Each deletes its own judgment cache first, so
a banked answer cannot fake a live gate.

| probe | asks | calls |
|---|---|---|
| `probe_fidelity.py` | does the answer move with tile size and detail hint | 20 |
| `probe_width.py` | order-invariance by battle width (reversal — confounded) | 30 |
| `probe_rotate.py` | picture vs position under rotation | 36 |
| `probe_rejectonly.py` | reject-only stability under rotation | 15 |
| `probe_grouping.py` | sameness partition stability under rotation | 18 |
| `probe_groupshape.py` | real moment sizes and spans | 0 |
| `probe_twins.py` | how much duplication is arithmetic | 0 |

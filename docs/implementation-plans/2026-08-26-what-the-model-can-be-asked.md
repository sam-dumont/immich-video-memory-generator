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

## 1c. Decomposing Structure does not rescue it, and the reason is the anchor

**Measured 2026-08-27**, `slice3-probes-2026-08-27/probe_structure_shape.py`, on
June's 81 moment representatives, temperature 0.

§1b left one defence open: the failing shape returns a **set** over N, and
Cull's working shape returns a **verdict per tile** against a definition. So
Structure was rebuilt in Cull's shape — *for EACH tile, sort it into `carries`,
`supports` or `outside`* — and run against the same pixels under the same cyclic
rotation. Both answers were expanded into one verdict per tile so they score
identically.

| shape | overlap by **picture** | by **position** | chance floor |
|---|---|---|---|
| A — reject-only over the wall | 0.72 | 0.70 | **0.67** |
| B — per-tile sort vs the thesis | 0.90 | 0.89 | **0.88** |

**Both sit on their own chance floor, and B's higher number is the artifact.**
B scores 0.90 by saying `outside` to about one tile in twelve; a verdict that
lopsided agrees with itself by luck 88% of the time. A shape that beats its floor
by 0.02 has measured nothing. Printing the floor beside every number is what
stopped this being reported as a success.

### The oracle says it is neither the shape nor the scope

The same question was then asked with **one tile per call and no wall at all** —
no other pictures, no positions, nothing to compare against, every confound
removed. It answered `carries` for **31 of 36 moments**.

That is the finding. The model is not anchoring on position here and it is not
losing the set; asked about one photograph in isolation it says the story needs
it. **A month that must reach ~10 clips from 79 live moments cannot be cut by a
question that keeps 86% of them.**

### Why: a thesis fitted to the corpus cannot exclude the corpus

June's thesis, synthesised by Pass 0 from this exact material:

> *A period of active cycling training and racing, interspersed with domestic
> life involving cats, home maintenance, and leisure activities.*

A frying pan is domestic life. A landscape is a leisure activity. **Every moment
matches by construction**, because the criterion was derived from the moments.
Pass 0's insight is a *description* of the period, and Structure was specified to
use it as a *selection criterion*. Those are different objects and the design
does not distinguish them.

This generalises past Structure: **any pass anchored on an artefact synthesised
from its own inputs is anchored on nothing.** The anchor has to come from outside
the material.

### An anchor from outside the material does not rescue it either

The obvious repair is an anchor that is **not** synthesised from the corpus. The
owner has stated one twice — *"would you show this to someone else? A memory is
something you show people."* It depends on nothing but the photograph.

Asked one tile per call over the same 36 representatives, with `false` shown as
the example default so a copied answer would cut rather than keep:

| anchor | keeps |
|---|---|
| the thesis, fitted to the corpus | 86% |
| **shareability, external by construction** | **100%** |

**36 of 36.** Every reason is fluent, specific and correctly grounded in visible
pixels — and among the pictures it would show someone are **a washing machine**
and **a Bosch product label**:

> *"The image clearly shows a white front-loading washing machine in what appears
> to be a laundry room…"*
> *"The image clearly displays a product label with specific details like the
> manufacturer (Robert Bosch…)"*

### What this settles

**No per-item question makes this cut, with any anchor.** Three were measured —
inward-pointing, corpus-fitted, and externally anchored — across two shapes and
three scopes, and the failure is the same each time.

The reason is that *"should this ship?"* is **inherently comparative and the
comparison is against scarcity, not against the other tiles**. A month ships ~10
of 79 moments because there is room for ten, not because 69 photographs are bad.
One tile cannot show a model how little room there is, so it correctly answers a
question nobody asked: *is this a real photograph of something?* Yes — including
the washing machine.

This is a stronger statement than §1b's. §1b said reject-only over N is not a
shape this model can execute. §1c says **Structure's cut is not a question this
model can be asked**, and no prompt, anchor, decomposition or scope changes that.

**What it does not settle:** where the cut comes from instead. §0 of
`docs/designs/2026-08-27-the-annotation-layer.md` states the bar — losing a good
picture is acceptable, losing an occasion is not — which is a *coverage* claim
and a *weight* claim, neither of which is a taste judgement. Nothing in the
corpus specifies the mechanism, and it must not become the old selector's quota
ladder wearing new words.

### A complete partition does not scale, for a reason Cull avoids by accident

The same shape B was run over a 36-tile wall. One rotation of three returned a
complete answer; the other two ran into prose — *"Let's go tile by tile"* — and
truncated at 4,654 and 5,361 characters with no JSON.

| tiles | answer length |
|---|---|
| 12 | 101–111 characters |
| 36 | 4,654–8,555 characters, 2 of 3 truncated |

The schema is identical at both widths, so this is not schema size. **A complete
partition has no empty default**: every tile must be named, so the model
enumerates. Cull stays compact over 110 tiles precisely because its normal answer
is *nothing to report* — empty lists. That property was designed in for honesty
and turns out to be the reason it is affordable.

So even a working per-tile Structure could not use a 120-tile page. At 36 it
truncates; at 12 it would cost 250 calls for a year against Structure's specified
"1–2".

### `ordinary` is not a hidden Structure signal

Checked at the same time, from banked answers, **zero model calls**: Cull's
discarded `ordinary` bucket names **259 of 261 candidates (99.2%)**, and 77 of 79
live moments are wholly ordinary. It is the complement bucket — the prompt says
*"Most tiles are ordinary"* — and it exists to keep `notes` clean, which it does.
Cutting on it leaves 2 moments of 79 and takes 4 favourites. **Not a free win.**

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

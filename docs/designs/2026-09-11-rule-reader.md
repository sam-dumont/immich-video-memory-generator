# The rule reader: editing without a language model

Status: all ten standard products now have a measured rules path (September 12, 2026).
See [the capability matrix](../research/2026-09-12-capability-matrix.md) for 48 successful
selection runs, 36 contact sheets and the quality losses. February deployment measurements
remain in [the phase 5 review](../research/2026-09-12-phase5-readiness.md).

The implementation uses `RuleEpisodeReader` and `rule_period` for the two upstream readings,
with `RuleStructureReader` on the existing planner ports. It reuses weight floors,
capture-group inventory, mechanical picks, admission and timing. It does not add the
proposed seven-delegation `ModelReader` abstraction. `NoModelJudge` rejects accidental
model-only calls; rules bypass semantic banks. Plans identify `rules-v1`, with an empty
thesis and Live Photos kept as stills. Custom semantic subjects require a model reader.

The album route now treats the owner's album choice as curation, after source, audience and
technical gates. The broader design below still contains proposals: adjacent-away extension,
about-candidate nomination, dominant journey weighting and cross-group hash collapse have
not been added to this implementation. Rules currently group consecutive photographed days;
long runs can skew allocation. UI redesign remains outside this work.

The ten-route matrix does **not** establish the declared 100% occasion-recall and zero-audience-
regression bar: reference inventories are incomplete, and some rules picks were refused by
the reference model. Those are explicit review questions, not passing quality results. The
following description of the blank-model crash is historical; blank models now select rules
when `editorial.reader` is `auto`.

A self-hoster who will not send pictures to a hosted model and cannot run a 30B text model at
home is not a fringe case. Today they get a crash: `build_editorial_planner` raises
`editorial runtime needs a nonblank LLM model` and the product is over. This note scopes a
deterministic reader that takes the text model's seat and leaves everything else — the planner,
the allocation, the timing, the carriers, the render, the attempt tree, the story view, the
matrix driver — exactly as it is.

The cheap encoders stay. DINOv2 plus the six public heads, the two CPU detectors, the pixel
facts and the 500M captioner are all still fact producers; they see each picture once and bank
it. The seat being removed is the text model that reads those facts. "Less smart", as the owner
put it, not "less honest".

## 1. The seam

Four of the eight judgments the route makes **already have a deterministic implementation in the
tree**, used today as the failure path. The rule reader promotes those to first class and writes
four new ones.

| Judgment | Asked today at | Rule reader supplies | Already deterministic? |
| --- | --- | --- | --- |
| Episode reading (`what_happened`, representatives, cull) | `episode_reader_factory` → `TextEpisodeReadResult` | one `EpisodeEditorialEvidence` per projection, templated meaning, no cull decisions | partly — `representative_for` already has a rule fallback |
| Period account | `period_reader` → `TextPeriodInsightResult` | empty thesis, grounding from the same episodes, its own `PeriodInsightIdentity` | no |
| Memory-worthy gate | `judge_worthiness` (`editorial_block_votes`) | `{happening: (2\|1\|0, reason)}` | no — the occasion door is designed, not in the tree |
| Story grouping | `_group_chunks` (`editorial_story_grouping`) | day episodes → stories, thesis, about-candidates | no |
| Story weights | `_ask_both_orders` (`editorial_story_weighing`) | empty answers; the floors settle it | **yes** — `_floor_one` + `GATE_WEIGHT` |
| Moment inventory | `inventory_event` (`editorial_moment_inventory`) | capture groups as moments | **yes** — `_capture_group_moments` |
| Moment pick | `pick_story_moments` (`editorial_story_pick_contract`) | favourites, then spread | **yes** — `_repeat_pick` |
| Standing gate | `judge_standing` (`editorial_block_votes`) | `{asset: (2\|1\|0, why)}` | no |
| Audience | `check_audience` (`editorial_shareability`) | verdict from flags and heads | **yes** for layer 1 (`never_auto`) |

**Unchanged and reused as-is**: the source model and its three gates, preparation, event
families, the annotation lines, the moment wall and cards, `PartitionedSlots.allocate`,
`_spaced`/`_spread`, `StandingGate.stands`, `CarrierAdmission` and its three re-grant passes,
occasion integrity (`_keep_occasions`), `trim_to_timing_budget`, the timing binding, the
duplicate pass, the attempt tree, the render projection, the Memory page.

**The mechanism.** `StructurePlannerPorts` gains one field, `reader: EditorialReader`, defaulting
to `ModelReader(ports.judge)` — seven one-line delegations to the functions that exist today, so
the model route does not change by a byte. Seven call sites move from `f(judge, …)` to
`reader.x(…)`: `judge_worthiness`, `_group_chunks`, `_ask_both_orders`, `inventory_event`,
`judge_standing`, `pick_story_moments`, `check_audience`. The two upstream readings keep the
`Callable` seats they already have. `StructureJudge` stays; it is a transport, not a reading
contract.

Rejected: answering `StructureJudge.ask(stage, prompt)` directly. The stage string does not carry
the scope of the ask (which happenings are in this block, which day is being inventoried), so a
rule answer would have to parse its own prompt back out of a JSON payload. That is brittle
exactly where the product must not be.

## 2. The rules, one per contract

Every output stays inside the existing vocabulary. Nothing new is invented for the planner to
read.

### Memory-worthy gate → tier 0 remarkable / 1 maybe / 2 background

The occasion door from [the allocation mechanism](2026-09-01-the-allocation-mechanism.md),
measured on a 6,502-asset year, implemented at last. Per happening:

| Rule | Fires | Facts used |
| --- | --- | --- |
| Mass | day count ≥ `max(4 × median, p75)` of photographed-day masses in the window | Immich `taken` |
| Away | dominant EXIF city outside the window's top-12 cities, or centroid > 10 km from `trips.homebase_*` when set | `exif_info` city/lat/lon |
| Favourite present | ≥ 1 star anywhere in the happening | Immich `favourite` |
| Close family present | a kinship word on the line (the `_FAMILY_WORD` set already in `editorial_story_weighing`) | `asset_people` + people file |
| Recorded | a video, or a Live burst that beats a still | `motion_bursts`, candidate duration |
| Required partition | the only happening in an `intent` partition marked required | `EditorialIntent.partitions` |

Mass or away → remarkable (2 votes). Any other rule → maybe (1). Nothing → background (0).
Favourites are indicators here, never gates: their absence licenses nothing, and the star only
ever raises. `reason` is the rule that fired, ≤ 12 words, in facts: *"four times an ordinary day;
outside the usual cities"*.

Adjacent away days extend one photographed day each side and merge into runs, so a quiet first
day rides in with its loud second. Never "any contiguous photographed run containing a flag" — on
a library that photographs 300 days a year that degenerates into one year-long block.

### Story grouping → day episodes, stories, thesis

A day episode is a run of capture groups inside one day, or crossing midnight within six hours —
the rule `_still_open` already encodes, applied instead of offered. A day splits when the gap
between consecutive groups exceeds 90 minutes *and* the dominant place changes, so a morning at
home and an afternoon out are two episodes.

A story is a run of consecutive days: `consecutive_runs()` already exists and already says the
right thing — *"a holiday or a hospital stay is one run; a month is many"*. A run whose days are
all away-flagged stays one story across a one-day gap.

Titles are templated from facts, in this order: `<activity head> at <place>`, `<place>`, then the
date. Accounts are the first observation of each capture group, joined, ≤ 60 words — the same
`fragment_fact` headline the model path banks.

**The thesis is empty.** The thesis is a judgment about what the period was. A rule reader does
not have one, and a templated one ("a month at home and two days away") is a sentence nobody
wrote. The about-candidate is nominated deterministically — the away run holding the most days —
and the existing `_central_stories` guard confirms it only when it is also the period's largest
story by moments, so a single posing afternoon cannot take half a month.

### Weights → dominant / major / minor / glimpse / none

The seat is `_ask_both_orders`; the rule reader returns empty answers, no joins, no retitles.
Everything after it is the existing code and stays: `_settle_weights` leaves the weights blank,
`_floor_one` seeds each from `GATE_WEIGHT` (remarkable → minor, maybe → glimpse, background →
none), then the floors raise (≥ 3 favourites → major; present-in-this-memory → minor), the
strangers-only rule caps at glimpse, and the one-moment ceiling applies. `dominant` exists only
for single-partition products — trip, holiday, album, special day — and only through the
confirmed about-candidate. A month or a year gets no dominant story.

Stories are never joined and never retitled by rules. A rule reader has no grounds to rename what
it just named.

### Moment inventory → depicted moments

`_capture_group_moments` verbatim: the capture group is the moment, the favourite wins it, the
alternatives order by sharpness then life then capture order. One addition, because it is what
the model was actually doing: two capture groups collapse into one depicted moment when their
perceptual thumbnail hashes land in the same bucket. `ports.thumbnail_hash` is already a
deterministic hash over the cached preview, so a burst split across two groups does not buy two
slots. `content` is the primary's caption, 60 words.

### Pick → which moments tell the story

`_repeat_pick` verbatim: favourites first, then `_spread` across the story's span for the
remainder, then `_spaced` compatibility against what is already committed. There is no
`why_fewer` and no `unused_slots` — a shortfall is arithmetic, not a decision.

### Standing gate → 2 stands / 1 borderline / 0 weak, reject-only

From the facts on the annotation line, which the heads and pixel facts already put there:

| Reading | Score | Facts |
| --- | --- | --- |
| favourite | 2 | Immich `favourite` |
| `document=…`, `nsfw=yes` | 0 | `flags`, `head_facts` (doc_docling, nsfw_marqo) |
| `SOFT (blurry)`, `DARK`, `BLOWN OUT` | 0 | `pixel_facts` |
| `people=none` + `activity=other` + `venue=home`, unstarred | 0 | `head_facts` |
| `people=none` + `location=indoor`, unstarred | 1 | `head_facts` |
| people ≥ one, or activity ≠ other, or venue ∈ {nature, urban, event-venue, sports, water} | 2 | `head_facts` |
| otherwise | 1 | — |

`StandingGate.stands()` — which already weighs these scores against the story weight, the
`life()` test and story thinness — is untouched. `why` names the fact that decided it.

### Audience → share / family_only / do_not_show

Layer 1 is already deterministic and stays: a `never_auto` flag from any source excludes, a Live
burst with one flagged member is excluded whole, only an owner `cleared` flag lifts it. Layer 2
becomes rules, still tighten-only: `nsfw=yes`, `exposure` other than none, `swim=yes` together
with `children=yes`, or a `review` flag → `family_only`; everything else → `share`. A `sendable`
export keeps only `share`. No body observation, no exposure sub-ask; those need a vision model
that is not there. This is strictly more conservative than the model check on what it would have
cleared, and identical on what it refuses.

## 3. What degrades, and how the UI says so

| Today | With rules |
| --- | --- |
| The thesis: what the period was | nothing; the period carries no stated argument |
| `why` under a carrier: the editor's sentence | the facts that funded it — `"<story title>: <n> pictures at <place>"` |
| Story titles, retitles, joins | templated from place, activity and date; never retitled |
| Depicted moments merged across capture groups by content | perceptual-hash buckets only |
| A flagged-but-innocent picture cleared for sending | stays `family_only`; the owner clears it on the pool page |
| Sampled-pair duplicates, picture facts, story motion | absent; `final_duplicate_review.status = "unavailable"` |
| Attached Live material sampled and tightened | absent; a Live carrier renders as a still unless its measured residual already earns the clip |

The six stage labels do not change — the stages are the same, only the answerer differs. The
Memory page prints one line under the title: **"Edited without a language model — the reasons
under each picture are the facts, not an editor's sentence."** It hides the thesis quote when it
is empty rather than showing an empty one.

## 4. Config and surfaces

- `advanced.editorial.reader: auto | model | rules`, default `auto`. `auto` resolves to `rules`
  when `llm.model` is blank and `model` otherwise. That is the whole fix for the crash.
- No new `generate` flag. Which reader runs is a property of the deployment, not of a request,
  and the matrix would stop being comparable if it could vary per route.
- The web UI shows the resolved reader read-only in the brief page's Advanced section, with the
  one-line consequence. Not a toggle.
- The attempt records it: `plan["reader"]`, `status.private.json["reader"]`, and every
  derived-decision file carries `"producer": "rules-v1"`.
- **Rule answers never enter the semantic banks.** `_judge_model_identity` already returns `None`
  when the judge has no `config.llm`, which disables bank reuse; the rule reader makes that
  explicit instead of accidental, so a library that later gains a text model re-reads from
  scratch rather than inheriting rule answers as though a model had given them.
- The matrix report gains a `reader` column and each reference entry records the reader that
  produced it. A rules run against a model reference is a cross-reader diff and is labelled so.

## 5. Evaluation — the bar, declared before building

Run `scripts/matrix_routes.py` over the ten approved routes with `editorial.reader: rules`,
against the accepted model run as reference. Five of the six metrics are already computed by
`matrix_routes_report.compare`.

| Metric | Source | Pass |
| --- | --- | --- |
| Occasion recall — every story the model run weighed dominant/major/minor has ≥ 1 carrier | `plan["story"]["episodes"]` × carriers | **100 % on 10/10 routes — hard** |
| Audience regressions — a carrier the model run refused appearing in the rules run | `shareability` log | **0 — hard** |
| Favourite retention | `favourites_kept / reference_favourites` | ≥ 80 % per route |
| Carrier agreement | `1 − removed / len(reference)` | ≥ 40 %, informational |
| Shortfall | `duration_realization.status` | no route worse than the model run |
| Cost | `llm_metrics.llm_calls` | exactly 0 |

Then the owner grades the ten selection sheets the way matrix sheets are always graded.

Carrier agreement is expected to be low and that is not a failure. Losing good pictures is fine;
losing an occasion is not. If occasion recall or audience regressions fail, the rule reader does
not ship — no partial credit, no "fix it in the next slice".

## 6. The spike

**Slice: one route — monthly highlights, 60 s.** Smallest scope, most banked history, a run in
minutes rather than hours.

Files added:

- `analysis/editorial_reader.py` — the `EditorialReader` protocol and `ModelReader`, seven
  delegations to the functions that exist today. Behaviour-neutral by construction; this is the
  seat and nothing else.
- `analysis/editorial_rule_reader.py` — worthiness, grouping, weights.
- `analysis/editorial_rule_picks.py` — inventory, pick, standing, audience.

Files edited, one call site each: `editorial_structure_planner.py`,
`editorial_story_grouping.py`, `editorial_story_weighing.py`, `editorial_story_planner.py`,
`editorial_story_carriers.py` (two), `editorial_shareability.py`. Plus `editorial_runtime.py`
(`auto` resolution and the two upstream reader seats) and `config_models_editorial.py` (the key).

Tests: the route run is the proof. Unit tests only where a threshold is load-bearing — the
occasion door's mass and away flags, and the weight floors under an empty answer — plus one
end-to-end test that a planning run with `llm.model = ""` produces carriers instead of raising.

**Effort: 4 days.** One for the seat and `ModelReader` (mechanical, and it must not move a single
decision); one and a half for the memory-worthy gate and the grouping, since the occasion door
has to be written from the design rather than ported; half for standing and audience; one for the
route run and reading what came out.

Not in the spike: the UI line, the matrix `reader` column, the resolved-reader output, the other
nine routes, the away-block extension across adjacent days (the spike uses the per-day flag
only), and any rule touching captions — captions come from the 500M model and stay.

Sequencing: after the scorer removal. The files differ, but a rules run is only readable against
a reference produced by the shipped route, not by a route mid-removal.

## 7. Open questions

1. **Ship an empty thesis, or template one?** Recommend empty. A templated thesis is a sentence
   nobody wrote, and the Memory page can hide the line.
2. **`auto`, or make the user opt in?** Recommend `auto`. A self-hoster with no text model gets a
   crash today; getting a film instead is the entire point, and the resolved reader is visible
   everywhere it matters.
3. **May rule answers enter the shared banks?** Recommend no — separate producer key, so a
   library that later gains a text model re-reads rather than inheriting.
4. **Does `dominant` exist without a model?** Recommend yes, narrowly: single-partition products
   only, from the away run with the most days, confirmed by the existing largest-story guard.
5. **Live Photo motion without the sampled-pair and attached ports?** Recommend leaning on the
   measured residual, which already decides still-versus-clip; what is lost is the attached
   audience tightening, so an unsampled Live carrier renders as a still. That costs seconds, not
   occasions.

## Where this came from

- [Story-first selection](2026-09-10-story-first-selection.md) — the shipped route these
  contracts belong to.
- [The allocation mechanism](2026-09-01-the-allocation-mechanism.md) — the occasion door, its
  measured thresholds, and the scarcity regimes.
- [The triage engine](../research/2026-08-31-triage-heads-architecture.md) — the head set and its
  class vocabularies, which are what the standing and audience rules read.

# Story-first selection: the shipped design

Status: shipped 2026-09-10 as the only selection route. `immich-memories generate`, the web
UI's Memory page and scheduled runs all go `generate` → `build_smart_pipeline(editorial_context)`
→ `run_editorial_source` → structure planner → story planner. The legacy scorer
(`SmartPipeline.run_selection`, `ClipRefiner`, the review loop) stays in the tree, unreached,
until the removal phase deletes it with its config keys. This note is the map of what shipped;
the reasoning that got here is in the notes cited at the end.

## What it replaced, and why

The old route scored every clip — faces 35 % of the weight, motion, stability, audio, an
optional vision-model bonus — shortlisted by a density budget, then ran refine → verify → judge
→ review in a loop bounded by `max_refinement_passes` (10). Measured on one month: 204
candidates reached selection, arithmetic removed 191 of them, and the only judgment in the
pipeline was then allowed to drop two clips out of fourteen per round. It took eight rounds of
drop-and-refill to remove what the first round had already named. On a full year the biggest
losses happened earlier still: one favourite anywhere in a chapter switched that chapter to
favourites-only, and whole unstarred occasions died before any reader saw them. Every fix before
that audit had been aimed at the last link of the chain.

Four rulings came out of it, and the code below is their implementation:

- **Selection is editing, not scoring.** Nothing is ranked against a bar. The period is read,
  its stories are weighed in words, and every picture in the cut carries the reason it is there.
- **Stories are weighed, not days.** The unit of weight is the multi-day story — a holiday, a
  week-long stay, an afternoon — never a calendar day, a place band, or a mass formula.
- **Favourites are indicators, never gates.** They carry the owner's judgment inside a story and
  lead the pick within a moment; their absence licenses nothing.
- **Always chronological.** The editorial levers are inclusion and dwell time, never order.

Gone with the scorer: `--refinement-passes`, `--analysis-depth`, the four analysis sub-phases
(clustering → filtering → analyzing → refining) and the whole-cut review. `analysis.max_refinement_passes`
and the `content_analysis` scorer keys still parse and do nothing on this route.

## The source model

Before any judgment is asked, selection has one canonical description of what the request
admitted (`selection_source.py`, `editorial_source.py`): every eligible source with its
provenance, duration, Live companion and — for what was left out — its exclusion reason. The
window is fetched whole, without person filtering; people conditions are applied afterwards as
an expression (`--people-expression`, or repeated `--person` with `--person-match and|or`).
Three deterministic gates run here and nowhere else: forwarded re-encodes without camera EXIF
(`source_quality.py`, widened by `--accept-any-provenance`), files a memory must never use by
name alone (`source_filter.py`), and the screen/document gate over the detector facts
(`editorial_source_gate.py`). Known screens and documents remain evidence for the readers; they
never carry a scene. Event families (`editorial_event_families.py`) group the wall
deterministically over normalised facts.

## The annotation store and the banks

`<cache>/annotations.sqlite`, created 0600, holds every fact the readers see, keyed by the
producer that made it (`store/editorial_preparation.py`):

| Table | Fact | Producer |
| --- | --- | --- |
| `descriptions`, `description_fields` | a compact caption and its fields, from a 400 px tile | `editorial.description_model` (a public 500M vision model, 140 tokens, temperature 0) |
| `head_facts` | six public context heads over a frozen DINOv2-small embedding: people, children, activity, location, venue, swim | `editorial.head_versions` |
| `flags` | two CPU detectors: sensitive content, document/figure | `det-v1`, run in their own interpreter |
| `pixel_facts` | pixel measurements at one fixed JPEG recipe | `editorial.pixel_producer_key` |
| `asset_people`, `motion_bursts` | who Immich says is in it; Live Photo burst structure | Immich metadata |

Preparation (`editorial_preparation*.py`) is the first stage of every run: it produces what is
missing, reuses what is complete, and a producer that is not available stops the run with a
count per producer instead of narrowing the input silently. Two invalid caption completions
become a recorded `caption unavailable`, bound to the preview and the exact request; timeouts
stay incomplete work for the next run. The pixel rule: **pictures are seen once, by the cheap
model, and banked.** The text model is text-only everywhere — it reads annotation lines, never
images.

The banks live in the same file and beside it, every one keyed by the exact request so a changed
prompt is a different key, never a stale answer:

- `editorial_episode_readings` (group, producer, evidence key) and `editorial_period_insights`
  (producer, evidence key) — the two readings below;
- `editorial_verdicts` — the cull's durable buckets per asset and pass version;
- `judgments`, `visual_judgments` and their `*_completion_failures` twins — banked text and
  visual answers, with the memory of an answer that could not be completed so the same
  exhausted ask is not repeated;
- `<cache>/structure-banks/<case>/memory-worthy.private.json` and
  `picture-stands.private.json` — the two vote banks of the structure planner;
  `thumbnail-hashes.sqlite` and `demanded-motion.sqlite` beside them.

## The six stages

These are the strings the run reports (`attempt.stage(...)` then `on_stage`), and what the
Memory page shows beside its active phase row:

1. **Preparing source metadata** — the source model above, then preparation, which reports
   `Preparing captions|public_heads|detectors: n/N` as it goes.
2. **Reading event evidence** — `text_episode_reader.py`. Temporal source groups are reading
   envelopes; the reader pages through their annotation lines and connects them into lived
   episodes, keeping every source reference before any picture budget exists. The cull
   (`selection_cull.py`, `cull_answer.py`) is asked inside each episode's scope and answers in
   two reject-only buckets; nothing it leaves alone is pre-deselected.
3. **Reading the period account** — `text_period_insight.py`, `period_insight.py`. The paged
   account of the period ends in a thesis: what the period was, which stories mattered. A
   whole-answer repair is bounded: an answer outside the schema gets one repair ask, not a loop.
4. **Building editorial cards** — `moment_cards.py`, `editorial_moment_wall.py`. One
   deterministic card per moment over the banked annotations and the episode meaning, rendered
   into the moment wall the planner reads: TSV, 256 characters per row, identifiers replaced.
5. **Editing the memory** — the two planners below.
6. **Validating selected source timing** — `processing/editorial_timing.py`. The chosen
   intervals are bound to their sources, still timing is preserved at the render boundary, and
   the duration is realised honestly: requested seconds, the content budget after titles and
   transitions, what was selected, the shortfall, and a status of `near_target` or
   `editorial_shortfall`. A short film is reported short, not padded.

## The story reading and its weights

`editorial_story_reading.py` and its split (`_grouping`, `_weighing`, `_replies`) produce a
duration-independent account of the stories a memory's sources support. The grouping connects
day episodes into stories; the weighing then asks the model, over one compact table, to weigh
each story **in its own words**: `dominant`, `major`, `minor`, `glimpse`, `none`
(`editorial_story_replies.WEIGHTS`). The model can only name story keys — it cannot explode or
fold the grouping — and it may join two adjacent stories. Words map to roles: dominant and
major are central, minor supports, glimpse is texture, none is incidental.

Two properties of the ask are load-bearing:

- **Judgments that matter are asked in two orders.** The memory-worthy gate and the standing gate
  (`editorial_block_votes.py`) vote in blocks of twelve, once in source order and once in a
  hashed order, and a picture's score is how many orders named it (2, 1 or 0). This is the
  judgment shape that held across the validation matrix; a single-order ask did not.
- **Repairs are bounded and stay inside the vocabulary.** A reply that is incomplete or uses a
  word outside the vocabulary gets at most two repair asks (`editorial_story_weight_contract.py`),
  each of which can only answer with the output vocabulary. A weight still missing after that is
  audited (`editorial_story_weight_audit.py`) and reported — never invented, never defaulted to
  "minor".

## The two planners

**The structure planner** (`plan_structure`, `editorial_structure_planner.py`) reads the captured
wall into the happenings and playable units of the period (`editorial_structure_material.py`),
then runs the memory-worthy gate: every happening read as `remarkable`, `maybe` or
`background`, per happening, in two orders (v44). The audience gate
(`editorial_structure_audience.py`) decides who may see each candidate — `family` by default, a
home video is for the household; `sendable` is the explicit stricter export — banked by
evidence key and tightened again once attached Live material has been sampled. It writes the
plan (`plan.private.json`), the contract it planned against (`contract.private.txt`) and a
human-readable selection sheet.

**The story planner** (`select_story_first`, `editorial_story_planner.py`) is the selection
itself. The order is the owner's:

1. **Words to slots** (`editorial_story_slots.py`) — the only arithmetic in the route. A story's
   weight becomes a number of pictures, capped by the moments the story actually holds; where a
   product limits how much one calendar partition may carry (a year's months), that capacity is
   reserved in the same order. Depth per weight class, never per day.
2. **A funded story is inventoried over its whole span** (`editorial_moment_inventory.py`) — the
   depicted-moment inventory is read only where a slot lands, which is where the reading budget
   went from "too slow" to affordable.
3. **The standing gate rejects before the pick** (`editorial_story_carriers.StandingGate`) —
   "does each picture stand by itself?", reject-only, two orders. A favourite lowers the bar; a
   texture slot raises it.
4. **The pick** (`editorial_story_shortlist.py`, `editorial_story_pick_contract.py`) — the model
   chooses which moments tell the story from a shortlist that names each source truthfully
   (video with its length, live photo, still); the favourite or the most-photographed moment
   leads.
5. **Carrier admission** — one picture per chosen moment is admitted if it is free, in context,
   spaced from what is already committed and allowed for the audience. Freed slots are
   re-granted across stories in up to three further passes, never to variants. An occasion whose
   every candidate failed still shows once.
6. **The audience chain and the final duplicate pass** close the cut — sampled-pair confirmation
   over conserved pixels, then duplicate discovery over the material that will actually be
   displayed (`editorial_final_sampled_duplicates.py`).

Nothing is refilled with variants: when the moments run out, the film is shorter, and the
duration line says so. The typed intent (`editorial_intent.py`) states what can be checked for
each product — which partitions of the scope must be represented, what supporting texture is
admissible, and when to abstain with `insufficient_material` instead of inflating what is at
hand — and `validate_intent` reports every violation class.

## Carriers

A carrier is one picture holding one interval in the cut, with: `kind` (`still`, `live-still`,
`live-motion`, `video`; the last two render as motion), `seconds`, `taken`, its story episode,
weight and role, its `standing`, and `why` — the one line the editor wrote for it, which the
Memory page prints under the thumbnail. Motion is measured only for chosen Live carriers
(`editorial_motion_facts.py`); attached Live material is demanded for the final checks without
widening the selection or granting audience clearance (`editorial_final_attached.py`); a video's
sequence evidence is literal and source-bound (`editorial_story_motion.py`). Owner edits on the
pool page are projected back onto the bound render plan, not re-planned.

## Durable attempts

Every run writes `<cache>/editorial-runs/<key>/attempts/<id>/`
(`operations/editorial_attempt.py`; the id is a UTC timestamp plus twelve hex characters, the
directory is 0700) and points `<key>/latest-attempt.private.json` at it. The attempt holds a
`.lease` taken with `flock`, which the OS releases even after a crash, so a reader can tell an
interrupted run from a slow live one without guessing from its age or a PID: `status.private.json`
says `running`, the lease can be taken, the answer is `interrupted`. The status record carries
the stage, the request, and on exit the outcome, the carrier count and the duration
realisation. Beside it: the source snapshot, the preparation report, the source gate's
exclusions, the plan, the contract, the selection sheet, the render projection, every text call
(`calls/`, `pre-planner-calls/`), every derived decision (`derived-decisions/`: the
memory-worthy gate, the period story, the story selection, each shortlist pass, the timing
trim, the audience bank), the story-motion observations and the sampled-pair sheets. Semantic
banks are shared across attempts; attempt artifacts never are.

That is what makes a cut survive its page: the UI arms a key, the worker writes under it, the
page polls the pointer every second, a reload lands on the same phase rows, and a cut that
finished while nobody was looking is read back from its plan. Cancellation stops at the next
stage boundary and is recorded as such.

## Memory types on this route

Ten types are offered on both surfaces, in the CLI's order: year in review, season, person
spotlight, multi-person, monthly highlights, on this day, album (`--memory-type album` with
`--from-album`), trip, holiday, surprise me (`special_day`). `then_and_now`, offered on neither
surface since the cutover, was retired with the legacy selector.

## Where this came from

- [The allocation mechanism](2026-09-01-the-allocation-mechanism.md) — the admission audit, the
  thesis as the importance function, the scarcity regimes.
- [The triage engine](../research/2026-08-31-triage-heads-architecture.md) — frozen encoder,
  task heads, teacher conclusions; why the heads nominate and never veto.
- [The annotation store](../research/2026-09-01-annotation-store-design.md) — facts per asset,
  decisions in banks, one store per library.
- [The reading budget](../research/2026-09-02-reading-budget-research.md) — where the reading
  passes spent their time and why the inventory is read only where a slot lands.
- [The motion describer](../research/2026-09-02-motion-describer-design.md) — queued; motion
  facts today are measured, not described.

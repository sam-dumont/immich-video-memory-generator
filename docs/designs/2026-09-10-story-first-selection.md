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
| `motion_lines` | one sentence about what happens in a video, from three keyframes read by byte range | `motion-line-v1` on the caption server, keyed by the picture's source metadata |

Preparation (`editorial_preparation*.py`) is the first stage of every run: it produces what is
missing, reuses what is complete, and a producer that is not available stops the run with a
count per producer instead of narrowing the input silently. A source the server itself cannot
serve is the other case: Immich answering 404 for a preview is its settled answer about that one
asset, and no rerun of any producer changes it, so that source leaves the film by name through
`evidence_exclusions` — counted once in the run log, listed in `preparation.private.json`, and
rejected in the `source-eligibility` pass — while a producer outage still stops the run. Two
invalid caption completions become a recorded `caption unavailable`, bound to the preview and the
exact request; timeouts stay incomplete work for the next run. The pixel rule: **pictures are
seen once, by the cheap model, and banked.** The text model is text-only everywhere — it reads
annotation lines, never images.

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
   `Preparing captions|public_heads|detectors|motion: n/N` as it goes.
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
duration-independent account of the stories a memory's sources support. Its rows are the banked
90-minute episode readings of stage 2, not the captions behind them, and one page is one calendar
month, cut into parts only at a day boundary when a month is too large for one request. No page
carries anything from the page before it, so a month's prompt is a pure function of that month's
rows: the pages read in parallel, the judgment bank answers a month it has already read for free,
and one changed asset invalidates one month instead of every page after it. The grouping connects
day episodes into stories; the weighing then asks the model, over one compact table, to weigh
each story **in its own words**: `dominant`, `major`, `minor`, `glimpse`, `none`
(`editorial_story_replies.WEIGHTS`). The model can only name story keys — it cannot explode or
fold the grouping — and it may join two adjacent stories. Words map to roles: dominant and
major are central, minor supports, glimpse is texture, none is incidental.

**Trips carry their own weight.** Before the weighing, `editorial_story_trips.py` runs the app's
trip detection (`trip_detection.detect_trips`) over the film's pool with the configured `trips` home
base, distance, duration and gap, naming each trip from its pictures' EXIF places and never over the
network. Every day episode holding one of a trip's pictures, or falling on a trip day with no
position at all, leaves whatever story the reader filed it in and joins that trip's story; what a
straddling story keeps outside the trip stays a story of its own. The trip row the weighing reads
carries `trip: N days away, first -> last, stops: place (days); ...`, and the same line opens the
story's purpose, which the pick reads, with the owner's 09-01 instruction to cover the whole span if
the pictures allow. The strangers-only ceiling does not apply to a trip. Without a configured home
base there are no trip stories, and `derived-decisions/trip-stories.private.json` records why; a
trip film skips detection, because it already is the journey.

**A recurring activity is one thread, in the film's context.** The grouping still rejects a story
whose days are not consecutive (`_broken_spans`), because a reader that folds gapped days together
usually folds unrelated ones. The cost was the other case: the same activity at the same place on
separate days came back as one story per day and one picture each. After the weighing,
`editorial_story_threads.py` nominates weighed stories of one place and one era as a group when the
reader's own words link them. Near the home base (the structure planner's 10 km test, by majority
of a story's pictures) only an activity links: the same activity phrase, or a shared title word the
film uses there more than at any other place. Away from home, the reader's own name for them (one
`split_from` title, or the same title) links too. The film's home place (the place most near-home
stories happened at, or the most common place without a home base) never holds a thread: the first
real run asked about 42 home stories the reader had filed as one "early home life" and joined 28
days of it. Names from the annotation lines, kinship words, English function words, times of day
and container nouns ("moments", "life", "stay", "session") never link, and a place alone never
does. One banked question per group (`recurring-activity-v3`) carries the film's dates
and contract and asks which stories are one recurring activity and which are steps worth showing
apart; each confirmed group, split again into its linked parts, becomes one story with the weight
of its heaviest member, placed where its first member was. An unreadable answer keeps them apart.
Eras follow the product contract: a film longer than `ERA_THRESHOLD_DAYS` is read per calendar
year and keeps one thread per year. The rules reader, trip films and subject memories ask nothing;
`story-threads.private.json` records the nominations, the answers and the folds.

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
   reserved in the same order. Depth per weight class, never per day. Stories are funded by weight
   word; inside a word a detected trip first, then the gate's word and the story's moments, then
   the reader's own order (`story.priorities`, the order the grouping named its stories in), then
   the first day (`funding_order`). The reader's order breaks ties rather than leading: a period
   the reader filed as one story and the day rule split sits at the top of that list, and leading
   with it pushed a weekend away out of a 90 s year. A trip weighed dominant or
   major reserves `round(slots / 2 * sqrt(trip days / film days))` pictures (at least one), counted
   in photographed days, and takes them at its own turn in the presence pass, before the stories
   after it take their first. A trip that is the whole film would get the dominant cap; the square
   root is the curve that meets the owner's 09-04 calibration, a ten-day trip in a five-minute year
   at about five, where a pro-rata share gives two.
2. **A funded story is inventoried over the capture groups it can spend a slot on**
   (`editorial_moment_inventory.py`) — the depicted-moment inventory is read only where a slot
   lands, and inside a funded story only over the groups its own shortlist keeps (whole groups,
   so a nearby competitor still has one); stars exempt nothing, because a shortlist that only
   just covers its grant is exactly where the inventory finds a further moment inside a group the
   story already holds. That is where the reading budget went from "too slow" to affordable.
   Because that shortlist is the whole funnel, motion competes inside it: a moment that plays is
   sampled before an equivalent still, and when the cap fills anyway the shortlist reaches for as
   many more playable moments as the story has slots, appended so no favourite is displaced and
   the spread already chosen is untouched. Inside a group, the picture that plays takes the frame
   unless a favourite claims it, because a video carries no sharpness measurement and lost every
   other tie to a still (#1066).
3. **The standing gate rejects before the pick** (`editorial_story_carriers.StandingGate`) —
   "does each picture stand by itself?", reject-only, two orders. A favourite lowers the bar; a
   texture slot raises it. A moving row says what it is (video with its source length, or a Live
   Photo whose motion plays), whether anyone speaks in it, and what happens across it, from the
   motion sentence preparation banked; the criterion says to judge that rather than whether one
   frame would make a good photograph. Its bank key carries the caption seat that wrote those
   sentences, the way a cull verdict carries the reading that produced it (#1064). A moving clip
   needs at least one standing approval. Playing motion cannot override two weak votes, even
   inside an important story; worthwhile scenery and action absent from the still remain eligible.
4. **The pick** (`editorial_story_shortlist.py`, `editorial_story_pick_contract.py`; a valid
   shortfall whose sentence the reader forgot is asked once more and then taken as the choice it
   is, rather than ending the film) — the model
   chooses which moments tell the story from a shortlist that names each source truthfully
   (video with its length, a live photo that plays or is shown as a still, still), favourites
   marked there. Moments that play (a true video, or a Live Photo above the motion discriminant)
   lead its rows, each carries its banked motion line whatever the grant (the plain facts when
   the tier has no caption server), and the
   contract says to choose the moving record over a still of the same moment: this is a video
   product. Where the two orders split, a moment that plays takes the slot before a still. It is
   asked whatever the owner starred; only a story offering a single moment its
   grant reaches has nothing to ask. The star wins the frame of the moment the pick chooses, never its story's
   slot. A large pick is cut into pages by request size and by how many labels one answer would
   have to name (`editorial_story_pick_pages.MAX_LABELS_PER_ASK`), because the counting is what a
   30B fails: asked for at most 51 and then at most 65, it returned 53 and 101. A reply that
   overruns its page anyway is repaired once, and if it overruns again the reader's own order is
   cut to that page's grant and recorded as a trim with its reason, the way the timing trim
   records a dropped carrier. A reply that does not parse, or that names a row nobody offered,
   still ends the attempt: it leaves no order to cut.
5. **Carrier admission** — one picture per chosen moment is admitted if it is free, in context
   and spaced from what is already committed, and, when its story already holds a picture, if it
   does not look like the frames around it (`editorial_story_lookalike.py`: its own moment's, and
   the kept frame just before and just after it in capture time). That check reuses the final review's
   visual repetition question through the same port: a pair of one capture family inside the
   90-minute window is asked as `episode-similarity-v1` and shares its memo with the final review,
   any other pair of one story as `story-similarity-v1`, whose premise says the two may be days
   apart. It has its own bound of twice the film's slots rather than a share of the final review's
   (that one is over the finished film, replacements and video samples included); a pair both ask
   is one request. A refusal frees the slot and buys one more pass, so the story's next distinct
   moment or the next story in funding order takes it; a favourite is never refused against a
   picture the owner did not star; what nothing else can fill is readmitted, so the check never
   causes a shortfall on its own.
   A film still short after the passes and the occasion keep then spends its free slots as depth
   inside the moments its funded stories show (`editorial_story_depth.py`), in funding order:
   depicted moments the inventory found and no pick took (the five-minute spacing inside a capture
   group had kept them out), alternating between capture groups, then further members of the chosen
   moments up to three frames per moment. Each is admitted only when the same question confirms it
   differs from the kept frames of its moment and its neighbours in capture time; an unasked pair
   (no visual port, or the bound spent) adds nothing, and a refused variant is never readmitted. Freed slots are re-granted across stories in up to
   three further passes, never to variants. An occasion whose every candidate failed may still
   show through a weak still, but rejected motion is never forced back in to fill it. The audience
   is not asked here: the gate reads the cut, not every candidate.
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
widening the selection or granting audience clearance (`editorial_final_attached.py`); what a
video shows reaches the pick as the caption server's banked sentence
(`editorial_preparation_motion.py`), so the reader never sees a video frame. Owner edits on the
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

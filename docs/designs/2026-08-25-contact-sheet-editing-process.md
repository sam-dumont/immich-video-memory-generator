---
date: 2026-08-25
status: owner approved
issue: 764
rejected_experiment_pr: 768
decision: contact sheets are the working surface of every editorial pass
---

# Contact-Sheet Editing Process Design

## Decision

Rebuild smart selection in the order a human editor needs information:

```text
source eligibility
    ↓
Pass 0: Insight
    ↓
Pass 1: Cull + record-shot lane
    ↓
Pass 2: Selects
    ↓
Pass 3: Structure
    ↓
projection checkpoint
    ↓
Pass 4: Fine cut
    ↓
rendering
```

Every editorial pass works from pixels on a purpose-built contact sheet. Text is grounded
annotation, not a substitute for seeing the material. Each pass banks its decision and reasons so
later passes can use them without inventing the missing context again.

This keeps the editing process described in the craft research: binary passes, a wall of material,
selects before assembly, a revisable thesis, explicit sacrifices, and a fine cut that judges the
whole. It changes the implementation order and contracts, not the underlying editorial method.

PR #768 is retained as rejected experimental evidence. Its June sheet is a migration gate, not an
accepted selection, and its branch is not the base for this replacement. We do not tune the current
Structure prompt until it happens to pass one month.

## Why the current draft regressed

The accepted Slice 2 gate at `80e3037` had 11 clips in 61 seconds. It was too cycling-heavy, but it
kept the Brussels Tour setup, live action, and medal as a readable personal sequence. The current
draft at `9ee0f61` has 8 clips in 47 seconds. It keeps a dull selfie, keeps both professional races,
and reduces the owner's Brussels Tour to the medal. That is worse.

The regression starts at `27618ef`. The latest commit did not cause it; its PNG and trace are
byte-identical. The actual failure is the contract around Structure:

- Structure sees time, place, counts, favourite count, a duration estimate, and two score-selected
  descriptions truncated to 120 characters.
- It does not see the pixels, the period insight, the relationship between frames, record-shot
  status, or the previous pass's editorial reasoning.
- A global priority order is later converted into a mechanical tail release. In June that killed
  18 of 42 moments without another visual judgement.
- Structure intent does not survive downstream. The fine cut can treat the Brussels Tour action as
  redundant with the medal because it never receives “effort before payoff.”
- A separate numeric judge can remove an asset without adding that loss to the editorial trace.

Prompting harder cannot restore evidence that the model never receives. The fix is to make each
pass see the right sheet and hand its decision to the next one.

## Goals

1. Emulate a recognisable human editing process instead of producing a score-ranked slideshow.
2. Make pixels the primary evidence at every editorial decision.
3. Preserve record shots and favourites without turning either into an unconditional topic quota.
4. Let repetition and variety emerge from visual roles in this particular period.
5. Keep every rejection attributable to one pass, one explicit decision, and one reason.
6. Make the duration envelope an editorial constraint, never an excuse for a mechanical tail cut.
7. Give the owner a judgeable contact sheet after every migration slice.
8. Use one selection flow from CLI, UI, automation, and dry-run.
9. Extract as many decisions as safely possible from each visual request and avoid per-item calls.

## Non-goals

- No cycling, concert, pet, selfie, person, or location quotas.
- No fixed “two events per topic” rule.
- No CLIP database or semantic retrieval system.
- No new scalar quality score pretending to be an editor.
- No reordering the final chronology.
- No deletion of source media.
- No upload of private contact sheets, descriptions, names, or locations to public issues or PRs.
- No wholesale removal of the old safety net before its replacement passes the real-library gates.

## The editorial object model

The passes exchange explicit editorial records. These names describe contracts, not a required
module layout.

```text
PeriodInsight
  thesis: str | None
  evidence: list[InsightEvidence]
  tensions: list[str]
  recurring_threads: list[str]
  unavailable_reason: str | None
  revision: int

EpisodeReading
  episode_id: str
  visual_summary: str
  representative_asset_ids: list[str]

CullDecision
  asset_id: str
  decision: reject | survive
  reason: str

RecordShotMark
  asset_id: str
  function: str
  evidence: str

MomentGroup
  moment_id: str
  episode_id: str
  candidate_asset_ids: list[str]

MomentSelect
  moment_id: str
  episode_id: str
  status: selected | no_peak | unresolved
  representative_asset_id: str | None
  alternate_asset_ids: list[str]
  record_asset_ids: list[str]
  selection_reason: str
  unresolved_reason: str | None
  shippable_duration: float

StructureDecision
  moment_id: str
  decision: keep | reject | unresolved
  reason: str
  intended_contribution: str | None
```

The thesis is prose, provisional, and allowed to be absent. Narrative contributions are model
observations for this run, not an enum of approved story roles. Otherwise “five live shows” soon
becomes another hard-coded diversity problem with fancier labels.

Every record is immutable and carries the exact input asset IDs, pass version, model identity,
contact-sheet identity, and decision provenance. Later passes add records rather than mutating an
earlier pass's reading. They may challenge an interpretation when the complete cut contradicts it.

## Contact sheets are the working surface

The repository already has the useful machinery in `moment_reading.py`: `tile_sheet`, `sheets_of`,
wide layouts, numbering, thumbnail caching, and time/place grouping. Keep and generalise it.
Current measurements show 215 photos can be represented in 14 model calls in roughly 22 seconds.
The existing ceiling of 120 tiles and 2100 pixels keeps sheets readable and requests bounded.

Each sheet must:

- show large enough pixels to judge gesture, action, expression, composition, and duplication;
- preserve chronological numbering even when split across pages;
- map every tile number to one stable asset or moment ID;
- include only grounded metadata available from the library or earlier passes;
- carry a hash in the trace so the decision can be reproduced;
- be stored locally and never attached to a public GitHub conversation.

“The model sees the sheet” has a literal meaning: the exact sheet image bytes are attached to the
model request. Saving a PNG, mentioning its path, or tracing its hash while sending only text fails
the pass contract. Request-level tests compare the attached image hashes with the traced hashes.

A photograph contributes one tile. When motion matters, a video contributes one locally composed
tile containing a small chronological filmstrip from its proposed segment plus grounded duration
and audio annotations. The filmstrip changes pixels inside the tile; it does not add a model call.
A pass without temporal evidence may judge visual membership, but it cannot claim to have judged
motion, audio, or pacing.

## Minimum-call request planning

Passes are logical decision and trace boundaries; they are not synonymous with model calls. A
single packed request may produce separately validated outputs for independent passes, while a
single difficult pass may need a bounded continuation.

The unit of work is a packed sheet request, never one asset or one moment. The request planner
minimises calls subject to three hard limits: provider payload limits, conservation of every tile,
and measured visual judgement quality.

- Build thumbnails and video filmstrips locally once, then reuse that visual atlas in derived
  sheets. Do not ask a model to describe the same pixels again for each pass.
- Pack as many complete episode or moment groups as remain legible on one sheet. One Pass 2 request
  returns decisions for many visually separated moment battles.
- Attach multiple sheet pages to one request when the configured provider supports it and a probe
  proves that numbering and decision accuracy remain reliable.
- Combine independent questions over the exact same visual evidence. The episode scan may return
  the Pass 0 `EpisodeReading` plus separately namespaced Pass 1 record marks and clear-unusable
  rejects in one response. This is safe because Cull is deliberately independent of the thesis.
- Parse and validate those namespaces independently. Record IDs and model Cull-reject IDs must be
  disjoint. If an asset appears in both, its rejection is invalid and fail-open while the record
  mark remains. Failure in one namespace never discards a valid sibling namespace.
- Do not combine dependent questions when the earlier answer changes the later question's sheet.
  Period insight must exist before Structure, and the structured cut must exist before projection.
- Projection and Fine Cut may share one ordered response over the same rough-cut sheet. If
  projection revises the insight, discard the provisional Fine Cut answer, replay Structure once,
  and judge the changed sheet. This optimisation ships only if a probe matches the separate-call
  control.
- Make continuation calls conditional. Structure asks for more sacrifices only when the named cut
  is genuinely over budget. Projection replays Structure only when the insight materially changes.
- Cache complete namespaced outputs by visual evidence and schema version. A later pass consumes
  the banked result; it does not re-query it for convenience.

The trace records planned calls, actual calls, cache hits, attached sheet count, and tile count per
request. Any batching change is probed against conservation and decision-quality fixtures before
build. “One enormous call” is not an optimisation if the model starts skipping tiles.

The normal uncached budget is therefore expressed as:

```text
episode scan packs (Pass 0 readings + independent Pass 1 decisions)
+ 1 period insight synthesis
+ packed Pass 2 battle requests
+ 1 Structure request
+ 1 combined projection + Fine Cut request
+ conditional Structure continuation, replay, and post-replay Fine Cut only when required
```

Each implementation slice reports this call count on the synthetic and June gates. A change that
adds calls must show a visual-decision failure that the extra request fixes.

Grounded annotations may include timestamp, place, favourite or record status, tagged people,
actual duration, audio/activity findings, episode reading, and provisional insight. Generated
descriptions can support the pixels. They cannot replace them.

### Pass 0 sheet: see the period

Pass 0 uses a hierarchical wall because one unreadably dense month sheet is not useful:

1. Build chronological sheets for each episode or day cluster from the full source-eligible corpus,
   before editorial subject filtering.
2. Ask for a short `EpisodeReading` and representative tile IDs grounded in what is visible.
3. Build a period sheet from those episode readings and representative tiles.
4. Ask what this period appears to be about, what repeats, and where the contrasts are.

The representative tiles are an explicit, reasoned model decision over the complete episode sheet,
never a score pick. They make the period sheet legible; they do not cull the episode. The period
request receives every episode reading and its selected tiles, while the trace proves page and
episode conservation. Every source asset still reaches Pass 1.

The output is a provisional `PeriodInsight`, not a command to force every frame into a theme. If
the model cannot form a credible thesis, `thesis` is `None`. The later passes make an honest
highlight cut and the trace says that insight was unavailable. They do not invent a story.

### Pass 1 sheet: cull only clear failures

Pass 1 sees the complete chronological episode sheet. Its response may share the earlier episode
scan request, but its schema and trace remain a separate pass contract. Record-shot detection runs
first as a separate lane over that same sheet. A pregnancy test, finish-line proof, score board,
ticket, sign, document, or other evidentiary frame may be visually weak and still carry the story.
Pass 1 names that function before an aesthetic comparison can erase it. Record shots are seeded
into Structure and Fine Cut so the edit composes around them.

Cull then asks only: “Which non-record items are clearly unusable?” It is a reject-only pass. Blur,
accidental captures, and unusable exposure can leave here. Subject class, ordinary repetition,
weakness relative to another frame, and relevance to the thesis are not Cull reasons. Legacy
subject quotas do not run. Screenshots, documents, and near-duplicates wait for their visible
function or comparison to be understood. Ambiguous material survives. A parser failure,
truncation, refusal, or missing model answer kills nothing and adds a loud `!!` warning.

If Cull rejects more than 75% of an otherwise source-eligible corpus, emit `!! possible over-cull`.
This is a diagnostic, not a quota: the system never restores items by score to satisfy a ratio.

A record shot remains in the candidate cut whenever its occasion remains. Rejecting the last record
shot or starred representative of an occasion requires an explicit protected-occasion decision
explaining why the occasion itself is outside this edit. Visual quality, repetition, and duration
alone are invalid reasons.

Subject policy belongs at this boundary and must run identically in every public entry point. This
means inferred subject categories become evidence for record handling and later decisions, not the
legacy class-based quotas. Explicit hard owner exclusions are source scope and apply before Pass 0.
This is why the pregnancy test cannot appear in a Structure-only sheet today: it has already died
upstream under the old policy.

### Pass 2 sheet: choose the peak frame in each moment

Pass 2 uses packed battle sheets containing as many complete, visually separated `MomentGroup`s as
remain judgeable. It compares neighbours directly and returns one namespaced decision per group in
the request. Its question is: “Does this moment reach a peak; if so, which visual expresses it, and
what useful alternate remains?”

A favourite wins its moment automatically. If several favourites collide, only those favourites
compete in the visual battle. This is battle semantics, not global immunity: the whole occasion can
still be omitted by Structure through the protected-occasion decision. “Favourites intact” means
each starred occasion that ships uses a starred representative; it does not mean every starred
asset must ship.

Without a favourite, the model may choose one representative and bank bounded alternates for a
specific reason such as a better establishing view. It may instead return `no_peak` with a reason;
a situation is not forced to produce a keeper merely because its answer parsed. Scalar score does
not silently choose the winner. The chosen representative contributes its actual shippable
duration to later planning.

Record shots remain a sidecar lane and do not compete for the aesthetic representative slot. This
prevents a visually strong favourite from hiding the evidentiary frame in the same moment.

An unreadable answer leaves the moment unresolved. A mechanical preview frame may be used to keep
a diagnostic render running, but it is marked non-editorial, cannot be banked as a valid select,
and puts `!!` on the gate sheet.

### Pass 3 sheet: make the rough cut

Pass 3 sees a chronological work-print sheet with one selected representative per moment, visible
record shots, the provisional insight, and the prior passes' short reasons. Its question is:
“Which moments does this particular story need?”

Structure's actuating answer is reject-only. Every valid rejection names one moment and a reason;
unnamed moments survive. Kept contributions are useful non-actuating annotations and may remain
unresolved without deleting anything. A rejected moment explains why that moment's contribution is
unnecessary. If it removes the final surviving moment from an episode, the reason must additionally
justify losing the whole occasion. Chronology is fixed, but importance need not be confused with
chronology.

Structure runs even when all moments already fit. Fitting the envelope does not make a cut
coherent.

If the first rough cut is over budget, the model sees the surviving work print again and is asked
to name the smallest additional sacrifice set, with a reason for every member. The set applies
atomically. It returns no rank, and runtime never takes a prefix or tail based on duration. Only IDs
explicitly named by the model may leave. There is no global rank followed by a tail release, no
invented fallback ordering, no per-moment cap, and no `_shipped_est` adjustment based on
hypothetical future deduplication.

The rough cut should normally land within 10% of the content target. If the model stops before the
envelope is met or a sacrifice continuation fails, no additional editorial membership changes. The
valid overlong cut returns with `!! unresolved envelope`. A mechanically narrowed preview may be
generated as a separate diagnostic artifact, but it cannot replace or mutate the
`StructureDecision` and is never valid gate evidence. A failed request never destroys an otherwise
valid cut, but the owner is not shown a warning-free fake.

This is where repetition resolves emergently. If a month contains five live shows, two may survive
because one establishes a friend's first original song and another closes on a festival crowd. If
three shows make three distinct contributions, three can survive. If they all do the same job, one
may be enough. The same logic applies to professional races: two separate races are not useful
merely because they are separate events if both provide the same “cycling happened” beat.

### Projection checkpoint: test the thesis against the cut

After the first structured cut, project the provisional insight against the surviving work-print,
the banked insight evidence, and the rejected-moment ledger. The model may confirm it, revise it
once, or discard it. If no provisional insight exists, skip this question. A revised insight
triggers at most one Structure replay over the complete Pass 2 work-print, including moments the
first Structure answer rejected. Earlier Structure decisions are evidence, not vetoes.

This bounded checkpoint models a normal editorial fact: the assembly teaches the editor what the
film is about. It avoids both extremes—locking an early thesis forever or looping until the model
manufactures a justification for every choice.

The revision and the before/after evidence are banked in the trace. When the combined-request probe
passes, this checkpoint and the provisional Fine Cut share one ordered response over the same
sheet. Any revision invalidates that provisional fine answer.

### Pass 4 sheet: judge the complete cut

Pass 4 sees the complete chronological candidate cut as a contact sheet, not isolated clips. It
also receives the current insight and each Structure keep reason. Its question is: “Does every
visual belong, and does the set have enough air and progression?”

It partitions the cut into keep and reject with a reason for every rejection. It may disagree with
Structure after seeing the whole. It cannot replenish the timeline with score-ranked material or
run an open-ended stabilisation loop.

Fine Cut may inspect one banked alternate from the same moment only when it rejects the current
representative for a reason another representative could answer. The reason must name the hole,
and the replacement gets one visual judgement. If the moment or occasion itself was rejected, no
sibling may return. Reopening an occasion requires the single bounded Projection/Structure replay.
This is bounded repair, not a second selection system.

Fine Cut aims for the requested duration within a few percent when the material supports it. A
shorter strong cut beats filler. If a downstream duration absorber, deduper, or score judge changes
the editorial membership, the run warns and the gate is invalid.

## Rendering comes last

The editorial system selects visuals and their intended durations before deciding how to render
them. A Live Photo remains one visual with a still or motion rendering option. It is not a second
editorial candidate simply because it has a video component.

The renderer may trim, animate, or choose the Live Photo motion component inside the selected
visual's contract. It may not change the membership of the cut.

## Banking and cache identity

Editorial calls are expensive enough to cache and important enough not to reuse against the wrong
pixels. The existing judgement cache keys text prompts but does not fully identify visual inputs.
Image-bearing decisions use a stronger key containing:

- pass name and prompt/schema version;
- model and thinking configuration;
- exact ordered asset or moment IDs;
- source-analysis and thumbnail-render versions;
- contact-sheet layout version and sheet content hash;
- grounded annotations supplied to the model;
- upstream insight and decision versions relevant to this pass.

Any change to the visual evidence invalidates the decision. Cache files remain safe to delete. A
cache hit records the original decision provenance and the current reuse in the trace.

Banked insight and decisions can support generation, titles, and later local discovery, but private
content stays in the user's local state. Public test fixtures use synthetic descriptions and
generated or licensed pixels.

## Failure semantics

Rejection is fail-open throughout the pipeline:

- A failed cull rejects nothing.
- A failed Selects answer chooses no editorial winner.
- A failed Structure continuation keeps the valid cut and marks the envelope unresolved.
- A failed Fine Cut rejects nothing further.
- Truncated output can only act on complete, explicitly named decisions.

Fallbacks used to produce a diagnostic preview are labelled mechanical and add `!!`. They are not
valid gate evidence. The standing rule remains: never record an owner verdict on a sheet whose
trace carries `!!`.

Optional model failure may still produce an honest legacy highlight video during migration, but
the run must say which path was used. Once the replacement is complete, the product fallback is a
deterministic highlight cut with no claimed thesis—not a hidden score-based imitation of the visual
editor. This fallback is a separate output contract; it never mutates or masquerades as the failed
editorial decision.

## One trace, no missing bodies

The JSON trace is the complete record. For every pass it contains:

- input and output IDs in chronological order;
- contact-sheet reference and hash;
- prompt/schema/model/cache provenance;
- insight used at that point;
- all keep, reject, unresolved, and replacement reasons;
- actual duration before and after;
- warnings and any mechanical fallback;
- a conservation check proving every input has one fate.

Markdown may stay readable by showing a sample, but it must say `showing 12 of N; full list in
JSON`. Display truncation must never look like missing selection accounting.

No numeric judge or downstream filter may remove an asset without adding a named trace decision.

## One public selection path

CLI generation, UI generation, automation, dry-run, and tests call one shared selection flow. The
flow owns, in order:

1. source eligibility: date range, library scope, supported media type, and explicit hard owner
   exclusions only;
2. contact-sheet construction;
3. Pass 0, then Pass 1 including subject policy, then Passes 2–4 and their handoffs;
4. final editorial membership and duration;
5. the complete trace.

Entry points may select configuration and presentation. They may not skip source eligibility or
editorial passes. Technical quality, re-encode findings, and inferred subject evidence are supplied
to Cull; no separate source-quality stage removes an editorial candidate before Pass 0. This closes
the current gap where the CLI and UI can feed different material to the same named selection
feature.

## Migration plan and slice gates

Build replacements in dependency order. Update issue #764 as the design of record; do not open a
competing architecture issue. Keep PR #768 available as rejected evidence while Pass 3 is rebuilt
on the real upstream contracts.

The replacement uses a two-level branch model. `feature/764-editorial-selection` is a long-lived
integration trunk created from `origin/main`. Every implementation slice branches from and opens a
pull request back into that integration trunk. Published integration history is merged forward from
`main`, never rebased underneath active slice branches. The existing selector remains untouched on
`main` until a final, owner-approved cutover pull request merges the complete integration trunk.

### Slice A: Pass 0 visual insight

- Generalise the existing contact-sheet builder.
- Add hierarchical episode and period sheets.
- Add the packed request planner, `PeriodInsight`, visual cache identity, trace support, and honest
  no-insight behaviour.
- Gate on a judgeable period wall and banked result, not final clip selection.

### Slice B: Pass 1 cull and record-shot lane

- Run subject policy and visual reject-only cull from the full episode sheets.
- Detect and preserve record-shot function.
- Prove parser refusal and truncation reject nothing.
- The pregnancy test becomes judgeable here, not in Structure.

### Slice C: Pass 2 selects

- Build packed visual battle sheets with complete, separated moment groups.
- Implement favourite auto-win and bounded alternates.
- Remove score as the silent representative chooser.
- Gate on whether each retained moment has the right peak frame.

### Slice D: rework Pass 3 Structure

- Replace the text table with the chronological selected work print.
- Consume Pass 0–2 records and actual shippable durations.
- Add the bounded projection checkpoint.
- Remove global rank/tail release, `_shipped_est`, per-moment caps, and already-fits bypass.
- Preserve the safe parts of #768: reject-only parsing, truncation-safe action, chronological IDs,
  reason ledger, and refusal to invent a fallback order.

### Slice E: Pass 4 visual fine cut

- Show the whole candidate cut with Structure intent.
- Replace score deduplication and stabilise/replenish loops with one visual partition and bounded
  reason-led re-mining.
- Prove downstream rendering cannot change membership silently.

### Slice F: delete superseded paths

- Route CLI, UI, automation, and dry-run through the same orchestration.
- Delete the old arithmetic funnel, duplicate subject-policy path, legacy caps, text-only Structure
  table, silent numeric judge, and downstream membership absorbers only after their replacements
  pass the real gates.
- Update public documentation and configuration migration notes.

Stacked builds remain allowed inside the feature trunk. Each slice blocks its merge into the
feature trunk until the owner accepts its contact sheet. Deletions remain last so a rejected
experimental pass does not leave the product without a working fallback.

## Test strategy

Implementation uses vertical red-green-refactor slices through public interfaces.

### Contract and unit tests

- stable chronological tile numbering across multiple sheets;
- cache invalidation when pixels, order, annotations, model, or pass version changes;
- minimum-call packing without skipped tiles or cross-group decisions;
- video filmstrips are locally composed tiles and do not add requests;
- complete parsers and reject-only fail-open behaviour under refusal and truncation;
- explicit handoffs for insight, record shots, representatives, Structure intent, and revisions;
- favourite wins its moment while the occasion remains rejectable with a reason;
- no unnamed rejection during envelope convergence;
- projection can revise once and cannot loop;
- conservation accounting for every pass;
- identical source policy and pass order through CLI, UI, automation, and dry-run.

### Synthetic generalisation controls

The tests must punish hard-coded topic rules:

- A matrix of periods contains repeated topics under different duration envelopes and with one,
  two, and three genuinely distinct contributions. Decisions follow the visible contributions and
  insight, not topic labels or a fixed survivor count.
- Five repeated activities from another domain use different evidence and can reach a different
  answer.
- Swapping topic labels while preserving evidence preserves the decision shape.
- A record shot with low aesthetic quality survives the cull and is composed into the story.

### Real-library migration gates

Use local, private contact sheets for June plus the previously accepted April and August controls.
Never attach them to GitHub.

The June gate is successful when:

- the result is chronological and warning-free;
- every rejected asset has a visible fate in the JSON trace;
- the generic selfie does not survive merely because it scored well;
- the kitten selfie may survive if it earns a role;
- two professional races do not both survive when they perform the same job;
- the Brussels Tour can retain setup, live effort, and payoff when that progression earns the time;
- the pregnancy test ships through the record-shot lane; this is a private regression fixture, not
  a product-level object quota;
- all shipped favourite occasions use a favourite representative;
- the duration lands near target without filler or a mechanical tail cut.

These are observations from this month, not product rules. A different period is allowed to reach a
different answer for visible reasons.

Each slice also runs the full repository gate and critique process. The final migration requires
`make ci`, `make critique`, warning-free traces, and owner acceptance of the contact sheets.

## Acceptance criteria

- Every editorial pass judges pixels on a purpose-built contact sheet.
- The runtime order is Insight → Cull/record → Selects → Structure → projection → Fine Cut.
- Later passes receive the decisions and reasons that earlier passes learned.
- No scalar score, rank tail, cap, deduper, or renderer silently changes editorial membership.
- Repetition resolves from the material's contribution to this cut, not a topic quota.
- Refusal and truncation cannot cause unnamed losses.
- The JSON trace accounts for every candidate; Markdown truncation is explicitly display-only.
- CLI, UI, automation, and dry-run execute the same source and editorial contracts.
- June is at least as strong as the accepted pre-regression sheet before the replacement Structure
  slice can merge into the feature trunk.
- The owner approved this architecture before implementation planning and production-code changes.

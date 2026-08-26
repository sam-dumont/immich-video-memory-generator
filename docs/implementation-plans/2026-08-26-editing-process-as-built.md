# The Editing Process, As Built

> **Living record, updated while building.** Not a plan — a description of what the
> code actually does and what it actually costs, measured on the real library.
> `2026-08-25-contact-sheet-editing-process.md` is the plan; this is the outcome.
> Written so the user-facing documentation can be assembled from it at the end.

Every measurement below comes from real library months, never synthetic fixtures.

---

## 1. Cost and latency

### Model calls per memory

| stage | calls | notes |
|---|---|---|
| Source preparation | **0** | metadata only — no model, no downloads |
| Pass 0 — episode scan | **one per pack** | one call per contact sheet |
| Pass 0 — period synthesis | **1** | one wall over the episode representatives |
| Pass 1 — Cull | **0** | reparses Pass 0's banked answer as a second namespace |

Measured on two real months of very different shape:

| month | fetched | candidates | episodes | packs | **total calls** |
|---|---|---|---|---|---|
| a sparse month | 729 | 261 | 58 | 3 | **4** |
| a dense month | 2016 | 1468 | 101 | 15 | **16** |

Cost scales with contact sheets, not with assets: a corpus 5.6× larger costs 4×
the calls. A pack is tile-capped at ~120, so a dense month's packs hold 4–14
episodes while a sparse, scattered month packed as many as 36 into one.

**Cull is free.** It is a logical pass, not a request: Pass 0 and Pass 1 ride in one
fused envelope over identical evidence. This is the single most important cost
property of the design and it must survive every later change.

### What drives the pack count

A pack is one contact sheet, and the packer fills it until the largest *valid*
response would no longer fit the output budget (`fused_episode_response_fits`).
Raising tiles-per-pack lowers the call count and has a hard quality limit — see §3.
Episode count per pack matters less than first measured. Probed across packs of 1,
4, 6, 7, 8, 9, 11 and 14 episodes at ~110 tiles each, three repeats: **23 of 24 runs
parsed and every one carried `cull_rejects`.** There is no cliff. (An earlier probe
that appeared to show one at 8 episodes was starving the model on the dataclass
default of 500 output tokens instead of production's 4000.)

The observed failure is at the extreme: one 36-episode pack returned a complete,
valid answer with `cull_rejects` simply absent, and one 14-episode run ran away to
15,586 characters. Both are tail behaviour, not a threshold.

### Measured wall-clock

| run | scope | calls | time |
|---|---|---|---|
| one day | 9 candidates, 1 pack | 2 | ~12 s |
| sparse month | 261 candidates, 3 packs | 4 | **49 s** |
| dense month | 1468 candidates, 15 packs | 16 | **189 s (3.2 min)** |

**~12 s per call**, flat across pack sizes. A month is a few minutes, and the
cost is dominated by the number of sheets, not by their contents.

Thumbnails are fetched once per asset and reused across passes; the atlas is built
once and every pass is handed the same encoded JPEG bytes.

### Cacheability

Answers are banked against `VisualJudgmentIdentity`: the exact sheet bytes plus the
exact question. Measured on the sparse month:

| | cold bank | warm bank |
|---|---|---|
| wall time | 49 s | **10 s** |
| cache hits | 0 of 3 | **3 of 3** |
| model calls | 3 | **0** |

Three tiers, in order of how much they cost:

1. **An unchanged month re-runs free.** Pack identities are byte-identical across
   runs, so every call is a hit and the decisions replay exactly.
2. **One asset changed re-asks about one pack.** Removing a real candidate mid-month
   left 2 of 3 pack identities intact — packs break on episode boundaries, so a
   change stays local instead of cascading down the month.
3. **Any change to the question re-asks the whole library.** Model, prompt version,
   pass version, schema version, render version, layout version, image detail,
   thinking, and the request limits are all in the key. The v3 → v4 bump stranded
   every banked answer.

The coarseness is inherent: a holistic pass judges in context, so the unit of cache
has to be the sheet, not the asset. The practical consequence is that **prompt tuning
is expensive once a library is banked** — settle a contract on small probes before a
full-library bank exists.

### Requirements this sets

- **Cost must scale with contact sheets, never with assets.** 4 calls for a sparse
  month and 16 for one 5.6× larger is the shape to preserve; anything that adds a
  call per asset, per moment, or per episode is out.
- **A pass that needs its own request must justify it against a fused namespace.**
  Fusion is only valid over identical evidence with independent namespaces.
- **No pass may re-download or re-encode pixels.** One atlas, hashed, reused.

---

## 2. The process, end to end

### Source preparation — no model

Runs before any pass and removes only **facts about the file**, never judgements.
Every exclusion is recorded with a named reason, so the account still answers for
the whole fetch.

| removed | why | sparse month |
|---|---|---|
| Live Photo motion components | the video half is part of a photograph, not a second visual | **186** |
| not shot on this camera | forwarded and downloaded material is not theirs to remember | **282** |
| out of date/library scope | not asked for | — |
| owner exclusions | the owner said no | — |

**729 fetched → 261 candidates.** A star overrides provenance, as it overrides every
other hard gate.

Candidates are then grouped, chronologically and by place, into **episodes** (58)
and **moments** (81). Groups conserve every candidate exactly once.

### Pass 0 — Insight

Reads each episode pack and returns, per episode, a `visual_summary` and
`representative_tiles`. Those representatives are composed into one **period wall**,
which is read once more to form the month's thesis.

Observation only: Pass 0 changes no membership.

### Pass 1 — Cull

**Removes the junk, removes the failed pictures, protects the favourites.** It never
chooses between similar frames — a month held near-duplicate runs of 8 to 35, and
deciding among those belongs to Selects and Structure.

Asked inside **each episode's own scope**, two lists per episode:

| bucket | means |
|---|---|
| `notes` | taken as a note rather than as a memory — a screen, a document, an object photographed to record what it is |
| `failed` | the picture did not come out — obstructed, smeared, unreadable |

Protections and fail-safes:

- a **favourite** named by Cull is kept, with a warning;
- **unavailable pixels** cannot actuate a decision;
- refusal, timeout, truncation, or a tile this pack never showed **rejects nothing**
  and raises a loud `!!`;
- a tile filed under the wrong episode is still applied and warned — bookkeeping
  must not void a pack of correct judgements, which it did on a real month;
- more than 75% rejected raises `!! possible over-cull` — a diagnostic, never a quota;
- any `!!` marks the review sheet **invalid for an owner verdict**.

### The review sheet

Every pass renders an owner-judgeable contact sheet: each candidate in chronological
order, banded with its fate and its reason, favourites badged, warnings in a red
banner across the top. **A sheet carrying `!!` is not judgeable** and no verdict may
be taken from it.

---

## 3. Constraints learned by measurement

These are not preferences. Each cost a real failure.

**Scope decides reliability.** A flat question over a 57-tile pack parsed 1 run in 3
and gave 55 of 57 tiles the same label. The identical question asked per episode
parsed 3 in 3 with identical output. A small model cannot search a large flat set; it
answers reliably inside a named small scope. This caps how large a pack can usefully
get, independently of the token budget.

**Show the shape, never describe it.** Prompts embed a complete example envelope
built from the same keys the parser demands, and a test parses that very example.
Prose schemas drift from strict parsers silently, and fixtures written from the
parser cannot catch it.

**Everything in an example is instruction** — values and whitespace included. Shown
`"ticket"`, the model labelled a pregnancy test a ticket. Shown a populated reject,
it copied that defect onto seven unrelated visuals. Decision lists are shown empty.

**Ask for less.** Three measured rounds: each added paragraph made the answer worse.
Never restate in prose what the parser already enforces.

**Bounded prose is fitted, not fatal.** A reason running past its bound is trimmed;
the decision it explains survives.

**Decide greedily, always.** Every call in the project was sampling at the
transport default of 0.3, because nothing ever passed a temperature. That, not the
prompt and not the model, was the instability: four repeats of one real pack gave
2107, 1917, 2253 and 2448 characters, and the fourth named all 105 tiles in the
pack. At 0.0 all four were byte-identical. The default is now 0.0 everywhere.

This is what makes the rest measurable. Before it, comparing two prompts was
comparing two samples, and a banked answer was not what re-asking would return.

**Some assets have no preview at all.** A dense month raised 23
`!! Pass 0 visual unavailable` warnings where the preview endpoint answered 404.
Those visuals cannot be judged and correctly actuate nothing.

**Failing open is only safe if it is loud.** Every fail-open path writes `!!`, and
the sheet says INVALID. Two of three early runs looked clean by their decision counts
alone.

---

## 4. Open, not yet built

Passes 2–4 (Selects, Structure, Fine Cut) and the surface convergence. The
near-duplicate reduction — the largest single quality lever on the sheet — lives
there, not in Cull.

Rules still owed, from `2026-08-25-existing-selection-rules.md`: a period's last
voice, temporal coverage with adaptive granularity, and the quarter-of-the-cut
proportion cap.

Known open behaviour: the period thesis is all-or-nothing on episode readings, so 14
unreadable episodes out of 101 cost the thesis for the other 87. That is the same
asymmetry corrected everywhere else in this pass and has not been corrected here.

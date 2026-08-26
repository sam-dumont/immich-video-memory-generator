# The Editing Process, As Built

> **Living record, updated while building.** Not a plan — a description of what the
> code actually does and what it actually costs, measured on the real library.
> `2026-08-25-contact-sheet-editing-process.md` is the plan; this is the outcome.
> Written so the user-facing documentation can be assembled from it at the end.

Corpus for every measurement below: **June 2023**, a real library month.

---

## 1. Cost and latency

### Model calls per memory

| stage | calls | notes |
|---|---|---|
| Source preparation | **0** | metadata only — no model, no downloads |
| Pass 0 — episode scan | **one per pack** | June: **6 packs → 6 calls** for 261 candidates |
| Pass 0 — period synthesis | **1** | one wall over the episode representatives |
| Pass 1 — Cull | **0** | reparses Pass 0's banked answer as a second namespace |
| **Total, one month** | **7** | |

**Cull is free.** It is a logical pass, not a request: Pass 0 and Pass 1 ride in one
fused envelope over identical evidence. This is the single most important cost
property of the design and it must survive every later change.

### What drives the pack count

A pack is one contact sheet, and the packer fills it until the largest *valid*
response would no longer fit the output budget (`fused_episode_response_fits`).
Measured on June: **6 packs for 261 candidates ≈ 43 tiles per call**, largest pack
57 tiles.

Raising tiles-per-pack lowers call count and raises latency per call. It also has a
hard quality limit — see §3.

### Measured wall-clock

| run | scope | calls | time |
|---|---|---|---|
| one day | 9 candidates, 1 pack | 2 | ~12 s |
| full month | 261 candidates, 6 packs | 7 | a few minutes |

Thumbnails are fetched once per asset and reused across passes; the atlas is built
once and every pass is handed the same encoded JPEG bytes.

### Requirements this sets

- **A month must not cost more than ~10 model calls.** Anything that adds a call
  per asset, per moment, or per episode is out.
- **A pass that needs its own request must justify it against a fused namespace.**
  Fusion is only valid over identical evidence with independent namespaces.
- **No pass may re-download or re-encode pixels.** One atlas, hashed, reused.

---

## 2. The process, end to end

### Source preparation — no model

Runs before any pass and removes only **facts about the file**, never judgements.
Every exclusion is recorded with a named reason, so the account still answers for
the whole fetch.

| removed | why | June |
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
- refusal, timeout, truncation, an unknown tile, or a tile named under the wrong
  episode **rejects nothing** and raises a loud `!!`;
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

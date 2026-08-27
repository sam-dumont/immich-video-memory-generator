---
date: 2026-08-27
status: index — read this first
issue: 764
---

# Selection: what to read, in what order, and what is dead

The selection documents are **a stack of patches, not a set**. Reading them in
filename order gives the wrong answer twice. This page is the ledger.

## Read in this order

1. **`research/2026-08-25-the-funnel-measured.md`** — why the old selector is
   being replaced. Two pages, all measured. Start here or nothing else has a
   motive.
2. **`research/2026-08-25-editing-craft-research.md` §Mapping hints** — twelve
   numbered lines tying each craft practice to a pipeline concept. The rest of
   the corpus cites these **by number**, so read them before anything cites one.
3. **`designs/2026-08-25-contact-sheet-editing-process.md`** — the design of
   record. **Three of its sections are dead; see the table below.**
4. **`implementation-plans/2026-08-26-what-the-model-can-be-asked.md`** — the
   measurement that killed the original Pass 2. Wins over the design wherever
   they touch.
5. **`implementation-plans/2026-08-27-visual-analysis-inventory.md`** — every
   visual question in the tree, and which instrument answers it.
6. **`designs/2026-08-27-the-annotation-layer.md`** — **the current shape.**
   Where it disagrees with (3) about caching or when Selects runs, it wins.
   Its §6b is the single most useful page here.
7. **`implementation-plans/2026-08-26-editing-process-as-built.md`** — the only
   description of what actually runs today.

## Status ledger

| document | status | authority |
|---|---|---|
| `research/…editing-craft-research.md` (+ supplement) | source material | the justification, never overruled — but see the caveat below |
| `research/…the-funnel-measured.md` | measurement | why the rebuild exists |
| `designs/…contact-sheet-editing-process.md` | **design of record, partly dead** | everything except the sections below |
| `designs/…the-annotation-layer.md` | **current shape**, not implemented | caching, pass ordering, the acceptance bar |
| `implementation-plans/…contact-sheet-editing-process.md` | the task plan | Tasks 0–15; see dead-gate note |
| `implementation-plans/…what-the-model-can-be-asked.md` | measurement | what the model can be asked |
| `implementation-plans/…what-the-pass-costs.md` | measurement, **two conclusions reversed inline** | cost only; read its header |
| `implementation-plans/…visual-analysis-inventory.md` | current | instrument selection, licences |
| `implementation-plans/…every-pixels-to-model-call.md` | **SUPERSEDED — do not act on** | none |
| `implementation-plans/…editing-process-as-built.md` | current | what runs today |
| `implementation-plans/…existing-selection-rules.md` | current | rules recovered from the old selector |

## What is dead, and what still points at it

The design's **Pass 2 section** was killed by measurement (superseding note
inline). Its consequences were not all removed, so these are stale and must not
be built:

- `MomentSelect.status = selected | no_peak | unresolved` and
  `representative_asset_id` in the object model — there is no peak and no
  `no_peak` verdict.
- **Slice C's gate**, "gate on whether each retained moment has the right peak
  frame" — that question was measured at 0 of 12.
- **Task 8's slice gate**, "the Pass 2 sheet exposes complete moment battles…
  bounded alternates" — a gate for a pass that no longer exists.
- **Task 11 (Fine Cut)** consuming "one banked alternate per eligible moment" —
  Task 7 removed ranked alternates. Fine Cut's repair needs redefining against
  what Selects actually produces.
- The design's **acceptance criterion** stating the runtime order as
  `Insight → Cull → Selects → Structure → projection → Fine Cut`. The annotation
  layer moves Selects to **after** a tentative cut. **The new order is not yet
  written down anywhere, because the tentative cut is not yet specified.**

## Two rules that genuinely conflict, unresolved

**Record marks vs junk culls.** `existing-selection-rules.md` §D says the junk
labels *override* a record mark, "because a mark argues about how a picture LOOKS
and has no standing over what it IS" — a rule that exists because the record lane
was measured shielding tweet screenshots and an advert. The design and Task 6 say
the reverse and a test asserts it: on collision the rejection is invalid and the
record mark stands. **Both are in the tree. The test asserts the losing one.** Not
yet decided; decide before Task 6 is revisited.

## Caveat on the craft research

It is scrupulous about its own reliability — it debunks the Capa D-Day darkroom
myth, flags the tie-breaker checklist as folklore in no primary source, marks one
quote UNVERIFIED. **The documents citing it are less careful about which findings
are structural and which are contingent on a human timescale.** In particular:
"documentary selects keep 25–50%" is one editor's remark about feature
documentaries and has been promoted to a gate assertion on a family photo library
with nothing measuring whether the ratio transfers. And several cited
practitioners separate their passes by *weeks* — Hurn's cooling-off gap is quoted
at length — to justify a process that runs in three minutes. Treat ratios and
timescales as untested; treat the instrument-follows-the-question mapping as the
part that predicted our measurements.

## Not covered here

`docs/` describes **one stage**. The end-to-end product story lives in
`docs-site/docs/create/pipeline/` and describes the selector this work replaces.
Neither says which is live: today the old selector ships and this rebuild is on
`feat/764-selects`.

---
date: 2026-08-27
status: audit — every visual model call in the plan and the tree
issue: 764
---

# Every Pixels-to-Model Call

> # SUPERSEDED — DO NOT ACT ON THIS PAGE
>
> Replaced 2026-08-27 by `2026-08-27-visual-analysis-inventory.md`, which sweeps
> all of `src/` rather than only the #764 editorial path, and finds that nineteen
> call sites ask **seven** distinct questions.
>
> **Its §6 recommendation — "ship the measured hash band, 291/291 unanimity" — is
> FALSE and must not be followed.** Cross-validated against current verdicts the
> band collapses to zero on both months, at every hash resolution; the
> counterexample is a model error rather than a hash collision. The 291/291 was
> calibrated against a prompt that has since been replaced.
>
> Kept only as a record of how the inventory was arrived at. Every conclusion in
> it that still holds is restated in the inventory, which is the page to read.

> Audited 2026-08-27 against the implementation on `feat/764-selects` and Tasks
> 0–15 of the implementation plan. The goal set by the owner: carry as much
> visual inspection as possible on one or two permissively-licensed models, keep
> the language model only where meaning is genuinely required, and know what is
> lost before doing it.

The doctrine this serves is in the design: **the model is a last resort.** This
page is the inventory that makes it actionable.

---

## 1. The result, first

There are **six** places where pixels reach a language model — three built,
three planned. They split cleanly, and the split is not where intuition puts it:

| | calls for a year of ~21,500 assets |
|---|---|
| scale with the **corpus** | ~18,400 |
| scale with the **cut** | ~5 |

**Every call that scales with the corpus asks a question a permissive model can
answer. Every call that needs meaning is O(1) in library size.** Nothing that
scales needs language, and nothing that needs language scales.

That is the whole finding. The affordability problem and the "must it be an LLM"
problem have the same answer.

---

## 2. The inventory

### Built

#### A. Episode scan — `episode_scan_request.py`
One fused request per pack, scaling with the corpus. It asks five things at once
and they do not have the same answer.

| sub-question | what it needs | replaceable by |
|---|---|---|
| `visual_summary` — describe this episode | **language** | nothing. Keep. |
| `representative_tiles` — which tiles make a wall legible | ranking over N | **clustering.** See §3. |
| `notes` — screen or document | reading text | **OCR** + metadata |
| `failed` — obstructed, smeared, unreadable | technical failure | **classical CV** |
| `ordinary` — the residual | nothing | nothing |

#### B. Period insight — `period_insight.py`
One request per period. Thesis, tensions, recurring threads over the
representative wall. **Language, irreducibly.** One call. Keep.

#### C. Pair sameness — `selection_selects.py`
Two requests per adjacent pair, scaling with the corpus and the dominant cost.
Already measured: perceptual distance decides 44% of pairs with 291/291
unanimity. An embedding distance should decide more. **Partly replaceable.**

### Planned

#### D. Structure, Task 9 — "which moments does this story need?"
Reject-only over one chronological work-print sheet, with the insight and prior
reasons. **Language.** One call, plus a conditional continuation. Keep.

#### E. Projection, Task 10 — confirm, revise once, or discard the thesis
**Language.** One call, conditional. Keep.

#### F. Fine cut, Task 11 — "does every visual belong, and does the set have
enough air and progression?"
Judges the whole cut as one sheet. **Language, and the most editorial question in
the pipeline.** One to two calls. Keep.

---

## 3. The finding that was not expected

`representative_tiles` asks the model to pick, from an episode's tiles, the ones
that best stand for it. The design calls this "an explicit, reasoned model
decision over the complete episode sheet, never a score pick".

**It is a superlative over N, which is the shape measured at 0 of 12.** The same
question that made Task 7 unbuildable is already shipping inside Pass 0 — it just
never had a control run against it, because a representative that reads plausibly
looks like it worked.

It is also the sub-question with the best algorithmic answer. "Which tiles make
this wall legible" is a **coverage** question, not a quality one: cluster the
episode's frames and take one per cluster. That is what a person means by a
representative set, it is what a clustering algorithm computes directly, and it
cannot anchor on tile position because position is not an input.

**This should be probed under rotation before anything else on this page.** If it
follows position, Pass 0's wall has been built from an arbitrary sample of every
episode since Slice 1 — and the period thesis rests on that wall.

---

## 4. The two models

| model | licence | job |
|---|---|---|
| **DINOv2 ViT-S** (21M) | Apache 2.0 | pair sameness (C), representative clustering (A) |
| **an OCR model** (RapidOCR / PaddleOCR) | Apache 2.0 | `notes` — screen and document (A) |

Plus classical CV that needs no model and no licence: Laplacian variance for
blur, histogram clipping for exposure, motion energy for accidental capture —
covering `failed` (A). OpenCV is already a dependency, and `SourceEvidence`
already declares `blur` and `exposure` fields that **reach no candidate today**.

The runtime is **ONNX Runtime, already in `pyproject.toml`**, whose execution
providers — CUDA, ROCm, OpenVINO, CoreML, DirectML, ArmNN, CPU — cover the
documented support matrix exactly, and are what Immich's own ML service runs on.

Ruled out: **DINOv3** (restrictive licence requiring personal information and
approval). **CLIP**, including Immich's, is retrieval-shaped — it scores an image
against text you supply and cannot say what an image contains. **RAM++** licence
unconfirmed.

---

## 5. What is lost, and how to know

Nothing here ships on this page's say-so. Each replacement follows the stated
order: the banked model answers are the ground truth the cheap path is
calibrated against, a band that **acts** needs unanimity rather than correlation,
and the residual is stated out loud.

| replacement | ground truth already banked | expected loss |
|---|---|---|
| perceptual/embedding distance for pairs | 656 dense + 174 sparse judged pairs | the ambiguous band still needs the model; measured, 54% of pairs at hash fidelity |
| clustering for representatives | none — **probe first** | unknown, and the current answer may be worse than the replacement |
| CV for `failed` | 0 rejects banked on two months | **unknown, and suspicious** — see below |
| OCR for `notes` | 9 + 4 rejects banked on two months | small sample, needs a labelled pass |

**Cull is barely firing.** Across two real months it banked 13 `notes` and **zero**
`failed`. Before replacing that with CV, find out whether the material is genuinely
clean or the pass is under-firing. Replacing a pass that is not doing anything
measures the wrong thing, and a CV replacement that also finds nothing would look
like a success.

---

## 6. Order of work

1. **Probe `representative_tiles` under rotation.** It is the cheapest probe on
   this page and the only one that could invalidate work already merged.
2. **Ship the measured hash band** in Selects: zero calls, 291/291 unanimity,
   cross-validated on the sparse month first.
3. **Wire the dead `SourceEvidence` fields** — blur and exposure, no model, no
   licence, already declared.
4. **Then** bring in DINOv2 and measure it against the banked pair verdicts
   before it decides anything.
5. **Then** OCR for `notes`, once Cull's under-firing is understood.

Passes B, D, E and F are not touched. They are five calls for a year, and they
are the ones doing the editing.

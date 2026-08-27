---
date: 2026-08-27
status: inventory — every visual analysis in the tree and the plan
issue: 764
---

# Every Visual Analysis, and What Should Answer It

> Complete sweep of `src/` on `feat/764-selects` plus Tasks 0–15 and the
> roadmap. Supersedes `2026-08-27-every-pixels-to-model-call.md`, which covered
> only the #764 editorial path and was written one call at a time.

The system asks pixels **seven distinct questions**. They are asked in nineteen
places, which is why this looked like nineteen problems. It is seven.

---

## 1. The seven questions

| # | question | asked in | scales with | answer |
|---|---|---|---|---|
| **Q1** | What is in this image? | 4 places | corpus | **split** — see §3 |
| **Q2** | Is it technically broken? | 5 places | corpus | **CV** — already in tree |
| **Q3** | Is it a note, screen or document? | 4 places | corpus | **OCR** — free from Immich |
| **Q4** | Are these the same thing? | 5 places | corpus | **hash** — needs recalibration |
| **Q5** | What was this day / episode / period? | 5 places | episodes | **language** |
| **Q6** | Does this cut work? | 4 places | the cut (O(1)) | **language** |
| **Q7** | What mood is this, for music? | 1 place | the cut (O(1)) | **language**, or colour stats |

**Q5 and Q6 are the edit.** Everything else is measurement wearing an editor's
coat.

---

## 2. Where each is asked

### The #764 editorial path (being built)

| site | question(s) | calls for a year (~21.5k assets) |
|---|---|---|
| `episode_scan_request.py` → `visual_summary` | Q5 | ~400 (per episode) |
| `episode_scan_request.py` → `representative_tiles` | Q4 | fused, free |
| `episode_scan_request.py` → `notes` | Q3 | fused, free |
| `episode_scan_request.py` → `failed` | Q2 | fused, free |
| `period_insight.py` | Q5 | 1 |
| `selection_selects.py` (pair) | Q4 | **~18,000** |
| Structure (Task 9) | Q6 | 1–2 |
| Projection (Task 10) | Q5 | 0–1 |
| Fine cut (Task 11) | Q6 | 1–2 |

### The legacy selector (still in tree, scheduled for deletion in Task 14)

| site | question(s) |
|---|---|
| `llm_response_parser.py` `CONTENT_ANALYSIS_PROMPT` | Q1 + Q2 + Q3 — per clip |
| `photos/scoring.py` `_PHOTO_ANALYSIS_PROMPT` | Q1 + Q2 + Q3 — per photo |
| `moment_reading.py` `SHEET_PROMPT` | Q5 |
| `special_day.py` `_PROMPT`, `_LOOK_PROMPT` | Q5 |
| `special_day_title.py` `_RETITLE_PROMPT` | Q5 |
| `selection_review.py` `_PROMPT` | Q6 |

### Adjacent

| site | question |
|---|---|
| `audio/mood_analyzer.py` | Q7 |

### Already answered without a model

| module | what it measures | used by |
|---|---|---|
| `photos/frame_quality.py` | sharpness, contrast, exposure | **legacy photo pipeline only** |
| `analysis/duplicate_hashing.py` | perceptual hash, hamming | legacy dedup |
| `analysis/face_scoring.py` | faces (Apple Vision, OpenCV cascade) | legacy scoring |
| `analysis/thumbnail_clustering.py` | thumbnail clusters | legacy |
| `analysis/scenes.py` | video scene cuts | video segmentation |

**The measurement machinery already exists and the new editorial path ignores
all of it.** `SourceEvidence` declares `blur`, `exposure` and `similarity` and
reaches no candidate. `frame_quality.measure()` is imported only by
`photo_pipeline.py`. Nothing in #764 calls either.

---

## 3. What answers each question

### Q1 — "what is in this image?"
Splits into three, and only one needs language.

- **A closed label** (category, setting, activities) — a classifier. Places365
  for setting, ImageNet-class for objects. Note `SETTING_VALUES` is already a
  closed vocabulary, adopted in #483 *because* free text produced 39 variants
  including both "indoor" and "indoors". That is a classifier's job, being done
  by a language model.
- **Subjects / faces** — face detection is already in the tree, and Immich has
  recognition with names.
- **Free description** — language. Needed for Q5's grounding, and only there.

### Q2 — "is it technically broken?"
**Classical CV, and `frame_quality.py` already computes it.**

Measured 2026-08-27 on the dense month: the softest photograph (Laplacian
variance 3.2 of 1,725) is a genuinely motion-blurred frame that **Cull banked as
`ordinary`**. Cull is under-firing, not the material being clean.

**Exposure must never actuate alone.** Of 37 blown-highlight photographs, **35
carry real text** — they are documents, not failures, and the most blown frame
in the month is a designed announcement card that is among the most valuable
images in the library. Blur is clean: of the 20 softest photographs, **0** carry
text. So blur may actuate; exposure requires an OCR gate first.

### Q3 — "is it a note, screen or document?"
**Immich's own OCR**, `GET /api/assets/{id}/ocr` — already computed, public,
documented. 2,016 assets answered in **22 seconds**.

**78% have zero text boxes**, which is a proof of not-a-document rather than a
probability. The ~2.4% above 15% coverage are the candidates, verified on pixels
as a photographed newspaper article and a downloaded logo. Known false positive:
a photograph *containing* text — a shop sign, a slogan. So coverage routes the
question; it does not close it. File extension is a free second signal (`.PNG`
from an iPhone is a screenshot).

### Q4 — "are these the same thing?"
**Perceptual hash**, and *not* an embedding.

Measured on 656 real pairs: the hash's unanimous band is 291 pairs, DINOv2
ViT-S's is 286. A tie — and the hash is microseconds against 18 ms/image and an
86.6 MB model. **DINOv2 does not earn its place for this question.** That both
cap at 44% says the ceiling is the pairs, not the signal.

**Open, and blocking:** cross-validated on `pair-v2` verdicts, the unanimous
band collapses to **zero on both months** — there is a pair at hamming distance
0 the model calls different. The 291/291 was calibrated against `pair-v1`, the
prompt we then replaced. An 8×8 dhash is 64 bits of low-frequency structure and
distinct pictures can collide; `hash_size=16` is untested and is the next probe.

**Representatives are the same question.** `representative_tiles` asks a
superlative over N — measured at 0.42 overlap by picture and 0.42 by position
against a 0.22 random floor. It is close to arbitrary, it ships today, and the
period thesis is built on the wall it produces. Clustering answers it directly,
deterministically, and cannot anchor on position because position is not an
input.

### Q5 — "what was this day / episode / period?"
**Language, irreducibly.** But it scales with *episodes*, not assets — roughly
400 calls for a year rather than 18,000. Six sites ask it today (episode scan,
period insight, moment reading, special day ×2, projection) and they are close
to the same question asked at different altitudes.

### Q6 — "does this cut work?"
**Language, irreducibly, and O(1).** Structure, projection, fine cut, legacy
review. Four to six calls for a year. This is the editing.

### Q7 — "what mood, for music?"
One call over keyframes. A colour-statistics fallback is plausible but unmeasured
and it is one call — leave it.

---

## 4. What this changes about cost

For a year of ~21,500 assets:

| | calls before | calls after |
|---|---|---|
| Q4 pairs | ~18,000 | the ambiguous band only — **unknown until the band is recalibrated** |
| Q2 failed | fused | 0 |
| Q3 notes | fused | 0 |
| Q1 closed labels | per-asset in legacy | 0 |
| Q5 episodes | ~400 | ~400 |
| Q6 the cut | ~5 | ~5 |

Q4 is the entire problem. Q2, Q3 and Q1's closed half go to zero. Q5 and Q6 are
already affordable and are the passes that do the editing.

---

## 5. Order of work

1. **Recalibrate the Q4 band** on current verdicts at `hash_size=16`. It blocks
   the only change that matters for cost, and the number previously reported for
   it was wrong.
2. **Probe representatives against clustering** — Q4 again, and it may already
   be broken in merged code.
3. **Wire `frame_quality.measure()` into the editorial path** for Q2, blur only,
   no exposure without the OCR gate. The code exists; nothing calls it.
4. **Wire Immich OCR** for Q3, as a router: below threshold is a proof, above it
   is a candidate.
5. **Then** consider a classifier for Q1's closed labels, calibrated against the
   banked `setting` and `category` answers the legacy path already produced.

Nothing here touches Q5 or Q6.

---

## 6. What is measured and what is not

| claim | status |
|---|---|
| OCR clears 78% of a month, 22s | **measured**, verified on pixels |
| blur finds a failure Cull called ordinary | **measured**, verified on pixels |
| exposure alone is 95% wrong | **measured** (35 of 37) |
| DINOv2 ties the hash on pairs | **measured** (286 vs 291) |
| representatives are near-random | **measured**, n=6 episodes — thin |
| hash band survives cross-validation | **measured and FALSE** |
| `hash_size=16` fixes the band | **unmeasured** |
| a classifier can do Q1's closed labels | **unmeasured** |
| clustering beats the model at representatives | **unmeasured** |

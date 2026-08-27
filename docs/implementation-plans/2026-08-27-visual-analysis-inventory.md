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

## 1b. The craft drew this line first

This split was not invented for the pipeline. Ming Thein sorts his own reject
list by **what the display can support** — on the camera back, "Clearly out of
focus / Incorrectly exposed / Compositional failures… / Clearly meaningless / no
obvious subject", but "**I'll leave duplicates or near-duplicates of good shots;
you can't judge fine detail or critical focus off the back of a camera screen**",
and only at full screen "Not critically sharp / … / Compositionally weaker than
the rest of the set". Mapping hint 2: *a pass may only ask what its viewing
conditions can answer.*

| question | Thein's list | instrument | measured |
|---|---|---|---|
| Q2 technically broken | camera back | thumbnail + CV | works; found a failure Cull missed |
| Q3 note or document | camera back | Immich OCR | 78% cleared in 22s |
| Q4 same thing | **full screen** | 400px tiles, model where pixels cannot decide | answer moves 150→400px, stops above |
| Q5 / Q6 | the editor, the wall | language | — |

The fidelity measurement and the craft agree without being made to. Q4 was
measured to need 400px tiles and to stop improving above them; Thein says
near-duplicate collapse is the one thing the small screen cannot do. Q2 runs on
a thumbnail; Thein judges exposure and gross focus on a phone.

Three consequences, each stated in the craft and each inverted by the build:

1. **The first pass is meant to be the cheap one.** Hurn marks a whole contact
   sheet in white in one sitting; Cooke's rule is that anything not clearly bad
   survives by default. Junk cull is binary, fast, per-item. Giving it to a
   language model made the craft's cheapest pass the pipeline's most expensive.
2. **Coverage is not quality** (mapping hint 5: headings get ticked off, an
   over-covered heading stops competing). "Which tiles make this wall legible"
   asks what an episode contains — clustering, not a superlative over N.
3. **A record shot is judged on a different axis** (mapping hint 11: "is this the
   only one", not "is this good"), so a technical rule may not touch it. Hence
   the OCR gate on exposure.

## 2. Where each is asked

### The #764 editorial path (being built)

| site | question(s) | calls for a year (~21.5k assets) |
|---|---|---|
| `episode_scan_request.py` → `visual_summary` | Q5 | **~190** (per PACK, not per episode) |
| `episode_scan_request.py` → `representative_tiles` | Q4 | fused, free |
| `episode_scan_request.py` → `notes` | Q3 | fused, free |
| `episode_scan_request.py` → `failed` | Q2 | fused, free |
| `period_insight.py` | Q5 | 1 |
| `selection_selects.py` (pair) | Q4 | **~13,600** (derivation: annotation-layer design §1) |
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

#### The evidence that Q1's closed half is a classifier's job

Two numbers from the live cache, 2026-08-27, and neither was known when the
inventory was first written.

**The closed vocabulary still leaks.** `video_segments` holds 4,690 answers with
a `setting`. `SETTING_VALUES` has exactly five members and the prompt states
them. The answers include `water` (83), `kitchen` (20), `park` (11), `beach`
(11) and `living room` (10) — about **3% off-vocabulary**, years after #483
closed the vocabulary precisely to stop this. A classifier with five output
neurons cannot emit "living room"; the failure is not reduced, it is
structurally impossible.

**And the per-photo half barely runs.** `asset_scores` holds 8,854 photos and
**8,755 have no category at all** — 99 were ever looked at, **1.1%**. That is the
3-of-295 shortlisting defect sitting in the data: the model only ever sees what
metadata already chose, so content can only confirm metadata. A classifier at
~15 ms an image makes the question affordable for every asset rather than for
one in ninety.

**State of the prototype:** Places365 ResNet18 downloaded and exported to ONNX
(45.5 MB weights in an external `.onnx.data`; export needs torchvision +
onnxscript, both build-time only). The 365-to-5 mapping is written with explicit
lists and **14 scenes flagged ambiguous** rather than silently assigned —
`corridor`, `lobby`, `porch`, `park`, `farm`, `campsite` and similar. Mapped
distribution: 151 outdoor_urban, 136 indoor_public, 53 outdoor_nature, 20
indoor_home, 5 vehicle.

**Calibration is blocked on frames, not on labels.** The 4,690 labelled segments
do not have their `keyframe_path` persisted, so the frame each label was assigned
to must be re-derived from `asset_id` + `start_time` before the classifier can be
scored against them.

**Places365 answers `setting`, not `category`.** Subject — people / animal /
landscape / object / screen — needs other sources, and ImageNet-1k has no generic
"person" class. `people` comes from face detection, `screen` from OCR coverage,
`landscape` from Places365's outdoor-natural scenes with no faces; `animal` and
`object` are the open gap. Before filling it, check whether anything in the new
design still reads `category`: its main consumer is `subject_policy.py`, the
quota system the design abolishes.

Probes and artefacts: `~/.immich-memories-matrix/slice2b-probes-2026-08-27/`.

### Q2 — "is it technically broken?"
**Classical CV, and `frame_quality.py` already computes it.**

Measured 2026-08-27 on the dense month: the softest photograph (Laplacian
variance 3.2 of 1,725) is a genuinely motion-blurred frame that **Cull banked as
`ordinary`**. Cull is under-firing, not the material being clean.

**How much it under-fires, precisely, because two documents look like they
disagree.** They count different things and both are right.
`2026-08-26-editing-process-as-built.md` reports 19 and 64 removals across the two
months — that is *every* removal at that stage, including source eligibility and
subject policy. The banked **model** verdicts are 9 and 8 `notes` and **zero**
`failed`: that is what Cull itself decided. So Cull removes about eight documents
a month and no failures at all, while OCR alone finds ~49 document candidates and
CV finds real motion blur.

**But "under-firing" is a smaller claim than it sounds**, and the earlier draft
overstated it. The blur floor is tight — a frame is unusable at Laplacian variance
3.2 and perfectly good at 5.9 — so the true `failed` bucket is the bottom ~1% of
photographs, roughly a dozen a month, not hundreds. That is what Cooke's rule
predicts: anything not *clearly* bad survives by default. Cull is missing a dozen
frames a month, not failing wholesale.

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
~190 calls for a year rather than 13,600 — cost scales with PACKS, and a pack holds 4–14 episodes. Scaled from the measured dense month: 2,016 assets took 16 calls. Six sites ask it today (episode scan,
period insight, moment reading, special day ×2, projection) and they are close
to the same question asked at different altitudes.

### Q6 — "does this cut work?"
**Language, irreducibly, and O(1).** Structure, projection, fine cut, legacy
review. Four to six calls for a year. This is the editing.

### Q7 — "what mood, for music?"
One call over keyframes. A colour-statistics fallback is plausible but unmeasured
and it is one call — leave it.

---

## 3c. Licence landmines, and what is actually usable

Surveyed 2026-08-27 against primary sources. **Verify before adopting** — several
are subtle, and one is the first thing an engineer would reach for.

### The constraint is TIME, not bytes

An earlier draft of this page carried a "<100 MB" target. That was the wrong
constraint and it would have excluded usable models: the Docker image is already
several GB, so a 200 MB model is not a problem in itself.

**The real bar is throughput: ~20,000 images in minutes on a weak NAS CPU with no
GPU.** That means roughly 10–30 ms per image. Size matters only where it predicts
latency, and as a one-time first-run download. A 46 MB ResNet18 at ~10 ms clears a
year in about three minutes; a ViT-base at ~200 ms does not clear it at all.

### Deployment reality, corrected 2026-08-27

Three corrections from a third survey, the first **verified against the installed
runtime**.

**The execution-provider list in this corpus was wrong.** ONNX Runtime 1.28
reports `ROCMExecutionProvider` as **not in the build** — the AMD route is
**MIGraphX** — and `ArmNNExecutionProvider` is absent too. Verified with
`ort.get_all_providers()`. Earlier text here claimed both. The real list this
build knows: **CUDA, MIGraphX, OpenVINO, CoreML, DirectML, CPU.**

**"Runs on ONNX Runtime" is not the same as "runs on every provider."**
No upstream project publishes a validation matrix covering the same graph across
every EP. Distinguish:

- **official ONNX** — the publisher ships the file;
- **export required** — plausible, but *not production-qualified* until the
  exported graph is tested on each target EP.

**Places365 and DINOv2 are both export-required** — the exports used here were
made locally and have been run only on CPU/CoreML. Portability is assumed, not
demonstrated.

**And the CPU numbers here are desktop numbers.** The first NAS-class evidence
found, from OpenCV Zoo's own benchmarks:

| model | i7-12700K | **Raspberry Pi 4** | 20,000 images on the Pi |
|---|---|---|---|
| NanoDet-m-plus 416 (3.62 MB, Apache-2.0, **official ONNX**) | 41 ms | **215 ms** | **~72 min** |
| MediaPipe person detector (11.4 MB) | 7.7 ms | 106 ms | ~35 min |
| YOLOX-S (34.2 MB) | 79 ms | **1,614 ms** | **~9 hours** |

**This is the number that decides the architecture**, and it points where the
annotation layer already points: none of this can be a query-time pass over
20,000 assets on weak hardware. It is **ingestion-time metadata**, computed once
per asset and stored, so a query becomes a database operation. That conclusion
was reached independently by an external survey and matches
`docs/designs/2026-08-27-the-annotation-layer.md` §0b.

### Excluded

| what | why |
|---|---|
| **`pyiqa` / IQA-PyTorch** — MUSIQ, TOPIQ, NIMA, HyperIQA, DBCNN | **PolyForm Noncommercial + NTU S-Lab.** The standard image-quality toolbox and the obvious `pip install` for anything quality-related. **Do not add it.** |
| **LayoutLMv3** and every RVL-CDIP fine-tune of it | weights CC-BY-**NC**-SA 4.0 |
| **MobileCLIP / MobileCLIP2** | Apple Sample Code License (`apple-amlr`), non-standard — despite being the right size and speed |
| **DINOv3** | gated, registration-required |
| **Ultralytics YOLO** (v5/v8/11) **and YOLOv9** | AGPL-3.0 and GPL-3.0 respectively (`Xenova/yolov9-c_all` verified GPL-3.0). If detection is ever needed: YOLOX, RT-DETR, RF-DETR, RTMDet — all Apache-2.0 |
| **`microsoft/dit-base-finetuned-rvlcdip`** | no licence tag on the weights — absent, not merely unread |

### Usable

| job | model | licence | size | fits the time bar? |
|---|---|---|---|---|
| ~~scene / `setting`~~ | ~~Places365 ResNet18~~ | MIT code, CC-BY weights | ~46 MB | **REJECTED — see below** |
| ~~subject / `category`~~ | ~~SSD-MobileNet / Open Images~~ | Apache-2.0 | ~20–30 MB | **DECLINED at this size — see below** |
| event clustering | **DINOv2 ViT-S** | **Apache-2.0** since Aug 2023 | ~86 MB | ~18 ms measured — yes |
| eyes-closed / group photo | **MediaPipe / BlazeFace** landmarks + eye-aspect-ratio | Apache-2.0 | <1 MB | yes |
| richer multi-label subject | **Tencent ML-Images ResNet-101**, 11k labels | BSD-3 code + weights | ~170 MB | **unmeasured** — size is fine, latency is the question |

**ImageNet-21k carries a data-provenance caveat** separate from timm's Apache-2.0
code licence: image-net.org states the data is for non-commercial research. That
argues for Open Images over timm-21k for subject classification.

### Two of these were then evaluated and rejected — this table is a shortlist, not a plan

**Places365 was measured and fails on this material** (60% agreement even at high
confidence; no person concept, and a family photograph is a people photograph
with a room behind it). More importantly `setting` has no consumer that acts on
it, and Immich already returns a real place name for 80% of a month. **Do not
build it.**

**The person detector was measured and declined** at 29.5 MB / 16 ms — it does
not separate `people` from `object` at all. It may be worth re-pricing against
NanoDet-Plus (<2 MB claimed); see Phase M. **Note the scope carefully:** the
rejection is about `category` (what a picture is mainly *of*). Using a detector
for *presence* — closing the 23% of people that face detection misses — is a
different question and is **not** covered by that rejection.

The authority on what to build is Phase M in the implementation plan and §6b of
the annotation-layer design, not this shortlist.

### Two open questions this settles

**No permissively-licensed aesthetic model is trustworthy on family photos.** The
two that exist — LAION's predictor (MIT) and idealo's NIMA (Apache-2.0) — are
trained on AVA photo-contest scores or, for LAION, documented as reflecting one
individual's taste with a measured demographic skew; idealo's own issue tracker
reports predictions "often totally inadequate" on general images. Independent
support for the standing rule against a scalar quality score standing in for an
editor. Classical CV for technical quality; nothing learned for taste.

**Nothing off the shelf separates a photographed DOCUMENT from a photo that
CONTAINS text.** RVL-CDIP models classify scanned-document *type* from clean
grayscale scans and do not transfer to a photograph of a receipt on a table. This
matches the measurement here — the birth announcement sits at 18.8% OCR coverage,
above any threshold that catches receipts. **OCR coverage routes the question; it
does not close it.**

### The gap with no answer yet

There is **no small, permissively-licensed, ONNX-native zero-shot classifier**.
SigLIP 2 is Apache-2.0 and would adapt to any taxonomy without retraining, but is
ViT-base scale and will not clear the time bar on a NAS CPU. The one model at the
right speed, MobileCLIP, is licence-blocked. So: fixed-taxonomy CNNs on the
CPU-only tier, and zero-shot only if a GPU tier is ever exposed — where size
stops mattering entirely.

### Three more surveys, and the four leads they add

Surveyed 2026-08-27 by three independent external models. **Most of it restates
this section** — the `pyiqa` trap, MobileCLIP, DINOv3, Ultralytics, the missing
DiT weights licence, AVA-trained aesthetics, the ROCm/MIGraphX correction and the
Pi-class latencies were all already here, which is worth knowing: three readers
converged on the conclusions this page already reached, including
*precompute at ingestion, never at query time*.

**Nothing below is measured, and none of the licences below has been verified
against a primary source.** They are leads, recorded so they are not re-derived,
and each names what would have to be true.

| lead | why it is new | verify before adopting |
|---|---|---|
| **A binary photo/document classifier** — `vlad-m-dev/mobilenet_v3_small_onnx_photo_doc`, claimed MIT, native ONNX, INT8 <15 MB | attacks the gap this page records as closed to us: *nothing separates a photographed document from a photo containing text*. A binary doc/photo head is a **different shape** to the RVL-CDIP type-classifiers that fail here, and it is small enough for the time bar | the licence; and whether it survives our own counterexample — the birth announcement at 18.8% OCR coverage. Trained on Italian documents and Japanese photos per its own card, so **the transfer is the question** |
| **SPAQ-trained technical quality** — 11,125 *smartphone* photographs, annotated per attribute (sharpness, brightness, contrast, graininess) | answers the exact objection this page raises against every learned quality model: AVA and TID2013 are contest and synthetic-distortion data. SPAQ is neither. It is the first candidate whose **training domain matches a phone library** | whether a permissively-licensed ONNX checkpoint actually exists — the survey that proposed it said "implementation dependent", which is not a model. Also whether it beats the classical CV already in `photos/frame_quality.py`, which is the only bar that matters |
| **Intel `open-closed-eye-0001`** — claimed Apache-2.0, **46 KB**, <1 ms on a 32x32 eye crop | eyes-closed is currently listed here as landmarks + eye-aspect-ratio. A dedicated 46 KB head is smaller than the arithmetic it would replace | it ships as OpenVINO IR, so an ONNX conversion is on us. It does **not** remove the landmark step — it needs the crop, so it is an addition to face detection, not a substitute |
| **SSCD is a trap for event clustering** (MIT, and it looks perfect for the job) | its training objective **repels** distinct originals so copy detection stays precise. Two photographs of one cake from opposite sides are exactly what it is built to push apart | nothing — this is a do-not-propose, in the same class as `/api/duplicates`. Recorded so the next reader does not find it attractive |

**Video shot boundaries are a question this page does not ask.** TransNetV2 (MIT)
and PySceneDetect (BSD-3) both cut a long video into shots before a frame is
sampled from it. The seven questions above are all about stills; sampling a
filmstrip across a cut is a defect none of them names. Out of scope for #764,
but it belongs on the list.

**One correction that costs nothing to accept.** ImageNet-1k's lack of a person
class is not an oversight to work around — 2,702 of the `person` synsets were
**removed** in the 2019-2021 audit, leaving three (`scuba diver`, `bridegroom`,
`ballplayer`). It will not come back, so no ImageNet-1k head will ever answer
`category`.

## 4. What this changes about cost

For a year of ~21,500 assets:

| | calls before | calls after |
|---|---|---|
| Q4 pairs | ~13,600 | in-cut moments only — **unknown until the tentative cut is specified** |
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


## One picture, two files: the shape a real library has

Measured 2026-08-27 against a real library, while testing whether Immich's
duplicate detection could serve as an event-similarity signal. It cannot, but
the groups describe the corpus, and that turned out to matter more.

`GET /api/duplicates` returns 4,136 groups. **None** are byte-identical --
Immich rejects those at upload -- so every group is two *different files* the
embeddings found alike. Split by what they actually are:

| | share of groups |
|---|---|
| one shot stored twice at two resolutions | 61% |
| consecutive shutter presses (burst) | 19% |
| neither | 20% |

The 61% is the important one, and it is structural rather than accidental. A
shared album carries no originals, so a library that uses one for curation holds
the full-size original AND a downscaled copy of the same shot. Both carry the
same capture instant, so **Stage A already groups them** -- no duplicate API
needed, and nothing new on the instrument ladder.

Two consequences for selection:

- **Which file survives is a quality decision.** 533 of 2,847 exact-instant
  groups kept the smaller copy under ID ordering, at 0.26x the pixels of the
  best available (median). Fixed: pixel count now leads `_keeping_order`.
- **A star belongs to the picture, not the file.** The star and the resolution
  can sit on different assets, because the album is both where stars get set and
  where the small copies come from. Measured here, they coincide -- all 1,099
  starred groups have the star on the largest file, none on a smaller one -- so
  this is a closed trap, not a repaired loss. The kept frame now takes the star
  from any frame absorbed into it, which satisfies the favourite law and lets
  pixels decide the file.

**The standing check for any future absorbing rule:** when two assets are folded
together, ask separately which *picture* wins (editorial) and which *file*
carries it (arithmetic). Conflating them is what put a UUID in charge of
resolution.

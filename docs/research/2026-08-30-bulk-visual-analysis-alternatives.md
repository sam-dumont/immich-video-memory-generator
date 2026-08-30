---
date: 2026-08-30
status: research — external survey of non-LLM instruments for the bulk vision layer
issue: 764
---

# Bulk Visual Analysis: What Can Leave the 27B

> **How to read the numbers.** Three provenance classes, marked inline:
> - **[M5 Max, 2026-08-30]** — run on this machine today (macOS 26.5.1,
>   onnxruntime 1.29.0, CPU unless stated). Reproducible here.
> - **[repo]** — this project's own prior measurements, from
>   `docs/implementation-plans/2026-08-27-what-the-pass-costs.md`,
>   `2026-08-27-visual-analysis-inventory.md` and
>   `docs/designs/2026-08-27-the-annotation-layer.md`.
> - **[cited]** — literature or vendor figures, with source and date in §9.
> - **[EST]** — my inference. Never presented as measured.
>
> Nothing here has been implemented. This is an instrument survey, written to
> be argued with. §8 lists what is already measured and rejected — read it
> before proposing anything.

---

## 0. Verdict

**Roughly 60–70% of *cold* vision calls can leave the 27B — but almost none of
it by swapping in a small VLM for the description cards.** The exit is classes
2, 4 and 5 moving to cheap CV plus one distilled pairwise head trained on
verdicts this pipeline has already banked. Class 1 stays generative.

Three framing points outrank every model choice below.

**1. The serving layer is the only untouched lossless lever.**
`what-the-pass-costs.md` §1 states "the endpoint is local, so concurrency is not
the lever — parallelising the gateway would queue, not overlap." That premise is
contradicted by the stack actually in use: oMLX ships continuous batching via
mlx-lm's `BatchGenerator` with `--max-concurrent-requests` defaulting to 8, and
mlx-vlm's server documents that *"image requests are prefilled individually with
their own vision embeddings, then join the shared decoding batch"* **[cited]**.
Decode is weight-bandwidth-bound, so batching amortises the model read. Against
that, Stage B measured 1,381 calls at 0.51 s isolated (≈12 min of model time)
inside 47 min of wall clock **[repo]** — about 60% of the bill is not the model
and is explicitly "not yet separated". That gap is the cheapest thing on this
page and costs no quality.

**2. Every model swap expires the banked corpus.** A prompt bump already took a
threshold from 291/291 to zero **[repo]**. Swapping the model does the same to
every banked verdict and every threshold calibrated on it. This makes lossless
levers (batching, prefix/vision caching, byte-identical speculative decoding)
strictly better risk-adjusted than any replacement — and it is the main reason
class 1 is ranked last, not first.

**3. This is a cold-start problem, not a steady-state one.** Bill 1 (vision
ingestion) banks at asset/pair/moment lifetime and amortises to zero on warm
runs **[repo]**. Everything below is about first-run and onboarding, which is
exactly the ordinary-user complaint.

---

## 1. The five call classes and where the money is

| # | Class | Scales with | Calls/year | Why it costs |
|---|---|---|---|---|
| 2 | Pairwise same-picture / better-of-two | corpus | **~13,600** **[repo]** | 1.19 s median per pair at 400px **[repo]**; ~6 h cold for a year |
| 1 | Fused description cards | corpus (~21.5k assets) | bulk | Autoregressive decode of a full reasoned card |
| 3 | Final-wall visual audit | the cut | tens–hundreds | Multi-image prompts that run away |
| 4 | Live-Photo motion classification | reservoir | ~774 of 1,171 **[repo]** | One model call for a 1-bit answer |
| 5 | Cull-stage quality triage | corpus | bulk | A binary junk test given to a language model |

Classes 2, 4 and 5 pay a 27B autoregressive decode for what is a classification.
That is where the 100×–1000× reductions live. Class 1 genuinely needs generation,
so it can only ever win a single-digit multiple from a smaller model.

---

## 2. Per-class recommendations

| # | Task | Top 1–2 candidates | Evidence | Latency | Judgment lost vs 27B | Effort | Servable via omlx-style HF pull today |
|---|---|---|---|---|---|---|---|
| 2 | Pairwise sameness / better-of-two | **Margin-ranking or logistic head on frozen DINOv2 ViT-S/B embeddings** + pHash distance + Δt + ΔGPS | SSCD Table 2: same descriptors, DINO ViT-B/16 **32.2 → 53.8 μAP (+67%)** purely from calibrating the similarity **[cited]**. ISC2021 identical data: descriptor-only 0.6354 vs pairwise-stage-allowed **0.8329** **[cited]**. Burst ranker: 0.47 MB, 13 ms/frame, 64.1% top-1 **[cited]** | 10–26 ms/image embed; head ≈0 | Cannot write the pair `reason`; needs an escalation band | Medium — labels already banked | n/a (embedding, not served) — DINOv2 Apache-2.0 ungated; SigLIP 2 Apache-2.0 with official ONNX |
| 4 | Live-Photo subject motion | **Sparse LK + `estimateAffinePartial2D` RANSAC (4-DOF) → residual DIS flow** | DIS at VGA **0.93 ms** (preset 1) / **2.35 ms** (preset 2) per pair **[cited]**. Residual-flow formulation is canonical **[cited]** | ~3–8 ms/pair **[EST]**; 6–8 pairs at 320×240 → **25–65 ms/clip** **[EST]** | Nothing it was doing well | Low — OpenCV already a dependency | n/a — OpenCV Apache-2.0 |
| 5 | Cull triage (blur / exposure / junk) | **Already in the tree**: `photos/frame_quality.py` + Immich OCR | 35 of 37 blown-highlight frames carry real text (documents, not failures); 0 of the 20 softest carry text **[repo]**. OCR cleared 2,016 assets in 22 s **[repo]** | single-digit ms | Nothing | **Lowest — wiring, not building** | n/a — in-repo |
| 3 | Final-wall audit | **Split it**: per-frame flags → CV; set-level judgment → LLM on ≤12 tiles | MMIU: GPT-4o **55.7%** on multi-image **[cited]**. Position bias confirmed at CVPR 2025 **[cited]**, matching this project's own tile-position anchoring finding **[repo]** | ~5–14 ms/frame | Nothing — the 200-tile sheet was already unreliable | Low | Apple Vision fast path + MediaPipe portable lane |
| 1 | Fused description cards | **Qwen3-VL-4B-Instruct** (in-family); optional LoRA distil on banked cards | Decode ~10× vs the incumbent **[EST]**, cross-referenced from two M4 Max measurements (Qwen3-4B 159 tok/s; Qwen3.6-27B-8bit 16.4 tok/s) **[cited]**. Caption distillation transfers: ShareGPT4V +222.8 MME on LLaVA-7B **[cited]** | ~0.5–1 s/card **[EST]** | **Unquantified.** See §2.1 | **High — re-banks everything** | **Yes** — Apache-2.0; mlx-community hosts 2B/4B/8B/30B-A3B/235B-A22B; GGUF + vLLM |

### 2.1 Class 1 is the weakest lane, not the strongest

A field test of **41 MLX VLMs on an M5 Max 128 GB generating per-image
structured catalogue metadata** — very nearly this task on this hardware —
returned **10 clean / 17 with caveats / 13 unusable / 1 crash** **[cited]**.

| Verdict | Models |
|---|---|
| Clean | Qwen3.5-9B, Qwen3.5-35B-A3B, Gemma-4-26B/31B, **Ministral-3-3B** (3.8 s end-to-end, 187 tok/s, 7.8 GB), LFM2.5-VL-450M/1.6B (<3 s, 1–4 GB) |
| **Unusable** | **Qwen3-VL-2B-Thinking** (burned the full 1000-token budget reasoning, emitted no fields), FastVLM-0.5B, PaliGemma 2, Qwen2-VL-2B — "ignored the structured format or degenerated into repetition" |
| Crash | SmolVLM2-2.2B (current mlx-vlm regression) |

Three consequences:

- **Qwen3.5-4B and -2B were not in the test set.** The recommended size is
  untested; the 9B and 35B-A3B above it were clean. **Ministral-3-3B** enters as
  a proven dark horse with the only real end-to-end per-image latency figure in
  the corpus.
- **Do not pair a Thinking variant with structured output.** mlx-vlm's
  `json_schema` + `enable_thinking` has a broken history (the forced closing
  think token is rejected by the JSON grammar) **[cited]**, and the field test
  shows thinking models exhausting their budget without emitting fields.
- **A grammar guarantees the envelope, never the facts.** One sub-1B model
  passed as format-clean while *"parroting the hints verbatim rather than
  describing the image"* **[cited]**. Under a schema that failure becomes
  valid-but-empty output, invisible to the parser. This is the concrete form of
  the risk behind the project's `reason`-field finding: a small model can
  satisfy the contract without doing the work.

Structured output itself is production-grade here — mlx-vlm added `json_schema`
via Outlines (2026-01-20), moved the server to llguidance (2026-04-29), and
supports multimodal requests. Format compliance is essentially size-independent
(llguidance ~96% on function-call-shaped schemas at 1B); *content* accuracy is
not (best measured value accuracy on images 67.2%, with an 8B floor) **[cited]**.
Keep schemas flat: string/enum/number leaves, no `$ref`, `minItems` or `pattern`.

### 2.2 The class-2 head: build it this way

The prior in-house negative — DINOv2 ViT-S ties a perceptual hash, 286 vs 291
unanimous **[repo]** — tested an *unsupervised distance threshold*, not a
supervised head. Those are different instruments, and the evidence says the
decision rule is what was limiting.

- **Feature construction.** SBERT's ablation over frozen pair embeddings:
  `(u, v, |u−v|)` scores **80.78** vs `|u−v|` alone at **69.78** — and the
  three-way concatenation *wins* the ablation, beating the four-way **[cited]**.
- **Use symmetric features.** Sameness is a symmetric relation; `[a, b, |a−b|]`
  is not, so it leaks ordering. Use `[|a−b|, a⊙b, (a+b)/2]`.
- **PCA to 128-d first.** Raw 384-d embeddings give 1,154 features on 13,600
  samples (~12 samples/feature) and will overfit. PCA'd: ~386 features,
  ~35 samples/feature.
- **Plain regularised logistic regression, not a metric-learning loss.** The
  ECCV 2020 *Metric Learning Reality Check* found a decade of exotic losses gave
  "marginal at best" gains over tuned baselines **[cited]**. The win comes from
  having a trained decision rule at all.
- **Add the free non-visual features** — time delta, GPS delta, burst grouping.
  For "same event" these are often more discriminative than pixels.

**The measurement trap — this invalidates the obvious experiment.** A head
trained on the 13,600 banked 27B verdicts and scored on a held-out split of them
measures *agreement with the 27B*, not correctness. That is **not comparable to
the 286/291 baseline**, which was scored against unanimous ground truth.
Reporting one against the other manufactures a win. Build a **separate
human-labelled holdout of a few hundred pairs** first. This also interacts with
the project's "ground truth has a version" finding: if the pair prompt bumped
mid-collection, the head is learning two label distributions.

**Label volume is not the constraint.** ~1,000 of 11,000 labels recovers 95% of
the achievable gap on a comparable probe task **[cited]**; LAION's original
aesthetic model was a linear head on 5,000 rating pairs **[cited]**. 13,600 is
roughly 13× the "95% of the gap" point.

**Sizing the cascade.** Hold out ~500 verdicts as a calibration set, pick the
abstention band on predicted probability to hit a target agreement rate, and
measure the fraction of pairs decided without the 27B — that fraction *is* the
saving. The closest published shape is Trust-or-Escalate: **>80% human agreement
with <43.5% reliance on the strong model** **[cited]**. Note there is still no
clean *vision* cascade number in the literature; all figures are text-domain, so
the mechanism transfers but the magnitude does not.

### 2.3 The class-4 recipe, and its dominant failure mode

```
per frame pair (downscale to 320x240 or 640x360, grayscale):
  1. pts0 = cv2.goodFeaturesToTrack(g0, maxCorners=300, qualityLevel=0.01, minDistance=8)
  2. pts1, st, _ = cv2.calcOpticalFlowPyrLK(g0, g1, pts0, None)
  3. M, inliers = cv2.estimateAffinePartial2D(pts0[st], pts1[st],
                     method=cv2.RANSAC, ransacReprojThreshold=3.0)   # 4-DOF
  4. flow = dis.calc(g0, g1)                 # PRESET_FAST / ULTRAFAST
     residual = flow - flow_implied_by(M)
  5. report BOTH: residual_fraction, largest_connected_component / frame_area
```

- **Use 4-DOF similarity, not an 8-DOF homography.** A full homography has
  enough freedom to partially explain away real subject motion when the subject
  fills the frame **[EST, but a direct implication of the failure mode]**.
- **Express the threshold as a fraction of frame width**, not pixels, so it
  survives a resolution change. The existing residual ≥1.5 rule is presumably in
  pixels and will not transfer.
- **Parallax is the dominant false positive.** Live Photos are close-range, and
  a single global transform cannot explain parallax — Google reports their
  IMU-plus-visual approach wins *"especially for close-range photography where
  parallax introduces depth variations"* **[cited]**. Mitigate by requiring the
  residual to be spatially clustered (parallax is diffuse and depth-correlated;
  subject motion is a compact blob) and temporally persistent across ≥3 pairs.
- **Steal Frigate's `lightning_threshold`** (0.8): if too much of the frame
  changed, that was the camera, not the subject **[cited]**.
- Reject MoViNet (TF/TFLite only, no ONNX or Core ML) and X3D-XS (low FLOPs,
  poor hardware efficiency) **[cited]**. No published work classifies Apple Live
  Photos specifically — this would be the first characterisation.

---

## 3. Top three bets, ranked

**1. Distil the pair verdict into a ranking head (class 2).** It is the whole
cost problem — ~13,600 calls/year, ~6 h cold — the labels are already banked,
and the prior negative tested the wrong instrument. Keep the 27B as the
escalation tier for the undecided band. Build the human-labelled holdout first
or the result is unmeasurable.

**2. Instrument the serving layer before changing any model.** Confirm oMLX is
actually batching, then close the unexplained ~60% of wall clock. A drafter for
the exact incumbent model exists (`z-lab/Qwen3.6-27B-DFlash`) with byte-identical
output — 3.38× on 8-bit **text** decode, M4 Max **[cited]** — but ViSpec reports
text-only drafters give **<1.5×** on image-conditioned decode **[cited]**, so
measure acceptance on real card prompts before banking it. mlx-vlm's vision
feature cache (11×) and vllm-mlx's content-hash image prefix cache (6.7–13.1×)
**[cited]** directly answer the standing infra question about the wall being
re-prefilled 4–5× per memory.

**3. Wire the CV already in the tree, then add the macOS fast path.**
`frame_quality.py`, `duplicate_hashing`, `face_scoring` and Immich OCR are all
present and unread by the current editorial path **[repo]**. Then add Apple
Vision: `CalculateImageAestheticsScoresRequest` (macOS 15.0+) returns
`overallScore`, which Apple documents as incorporating *"aesthetic score, failure
score, and utility labels"*, plus `isUtility` — *"images that are not necessarily
of poor image quality, but may not have memorable or exciting content"*
**[cited]**. That is classes 3 and 5 in one call, and `pyobjc-framework-Vision`
is already a declared darwin dependency with a working `VNImageRequestHandler`
wrapper and an OpenCV fallback **[repo]**.

---

## 4. What must stay on the big VLM

- **Q5 and Q6 — the reading and the cut.** They scale with episodes and the cut,
  not the corpus, so they were never the bill.
- **The pair `reason` text.** Dropping it flipped 126 pairs one way and 10 the
  other **[repo]** — writing it is how this model arrives at the answer. A
  verdict-only head must therefore *escalate*, never *replace*.
- **Event understanding and the final set-level judgment.** Set-level redundancy
  and shape cannot be read off per-image scores.
- **"Is this the only picture of this occasion."** A relational judgment no
  per-image instrument can make.

---

## 5. Instrument shortlist: latency, licence, portability

Measured on this machine today unless marked otherwise.

| Instrument | Licence | Size | Latency | Portable (CPU/CUDA/Mac) | Verdict |
|---|---|---|---|---|---|
| **MediaPipe FaceLandmarker `0.10.35`** — 478 landmarks + 52 blendshapes | Apache-2.0, package *and* all 3 model cards | 3.58 MiB | **3.75 ms/image all-in** **[M5 Max, 2026-08-30]**; blendshapes add ~0.01 ms; flat in input resolution (same at 160px and 1024px) | Yes, with caveats (§6) | **Ship it** — the eyes/expression decider |
| **YuNet** (`cv2.FaceDetectorYN`, already in OpenCV) | MIT | **227 KB** | **5.47 ms**, 11/11 faces **[M5 Max, 2026-08-30]** | Yes | **Ship as the detector** |
| **Apple Vision one-pass** — aesthetics + `isUtility` + 1303-class classifier + 768-d feature print + face capture quality | OS API, no weights to ship | — | **14.2 ms/image total** **[M5 Max, 2026-08-30]**, macOS 15+ | **macOS only** — fast path behind a portable fallback | Ship as accelerator |
| **ARNIQA** | **Apache-2.0 code *and* weights**; in torchmetrics | ~28 MB | ~30–50 ms @224px on M1 Pro CPU **[EST]** from 0.485 s @1080×800 **[cited]**; ~3–5 ms via self-built Core ML **[EST]** | Yes (stock ResNet-50, ONNX-trivial) | Only licence-clean learned IQA; KonIQ SRCC unpublished — self-measure |
| **DINOv2 ViT-S/B** | **Apache-2.0, ungated** | 86.6 MB (ViT-S) | 18–26 ms/image **[repo/cited]** | Yes (no official ONNX; stock ViT) | Primary embedding for "same event, different angle" |
| **SigLIP 2** | Apache-2.0 | base 0.4B | — | Yes — **official ONNX** (`onnx-community/siglip2-*-ONNX`) | Best ONNX story; behind DINOv2 on instance retrieval |
| **pHash / dHash** (`imagehash`) | BSD | trivial | 18–26 s per 10,200 images **[cited]** | Yes | Exact near-dup is **solved**: precision 1.000 / recall 1.000 at threshold 0 **[cited]** |
| **PDQ** (`pdqhash`) | permissive | 256-bit | 7 ms/image via C++ bindings vs 262 ms pure Python **[cited]** | Yes | Optional upgrade — the free quality score is genuinely new information |
| eDifFIQA(T) (OpenCV Zoo) | CC-BY-4.0 | 7.27 MB | **1.70 ms** **[M5 Max, 2026-08-30]** | Yes | Sharpness term only — **not** an eyes proxy (§5.2) |
| **InsightFace `buffalo_l`** | code MIT, **weights non-commercial research only** | 288 MB | 62.5 ms full **[M5 Max, 2026-08-30]** | — | **Licence-incompatible with this MIT repo** |
| OFIQ (ISO/IEC 29794-5 reference impl.) | MIT code | 402 MB models | ~397 ms + 7.6 s init **[cited]** | — | **Spec source, not a runtime** |
| SER-FIQ / CR-FIQA | CC-BY-NC(-SA) 4.0 | — | — | — | Non-commercial — no |
| pyiqa (IQA-PyTorch) | **PolyForm Noncommercial 1.0.0** | — | — | — | **Blocked.** And its `nima`, `nima-vgg16-ava`, `cnniqa`, `dbcnn`, `hyperiqa`, `metaiqa`, `wadiqam_nr` checkpoints are retrained by the toolbox, so **the weights are NC too** |
| aesthetic-predictor-v2-5 | **AGPL-3.0** | — | — | — | Blocked for an MIT project |
| LAION aesthetics v2 | MIT/Apache-2.0 | 3.7 MB head + CLIP | — | Yes | **Actively harmful here** — see §5.3 |
| MobileCLIP2 | **`apple-amlr`, research-only** | — | headline 1.5 ms is iPhone ANE, not CPU | — | Blocked |
| DINOv3 | bespoke DINOv3 License | — | — | Yes | Commercial OK **but** requires a visible "Built with DINOv3" credit and a gated download; distilled ViT-S retrieval scores unpublished |
| MoViNet / X3D-XS | — | — | — | TF-only / poor efficiency | Reject for class 4 |

NAS multiplier for everything portable: **~15–25× slower** than this machine
**[cited]**. **No learned IQA model fits a 10–50 ms budget on a NAS** — classical
per-region CV is the portable answer, and any learned scorer is a Mac-only
accelerator behind it.

### 5.1 Blendshapes beat the Eye Aspect Ratio, because of smiling

A broadly-smiling portrait measured **EAR 0.197 / 0.212** — the classic 0.2
threshold calls it *eyes closed* **[M5 Max, 2026-08-30]**. Blendshapes separate
the cases: `eyeSquint` 0.74 + `mouthSmile` 0.96 is a squinting smile (a keeper);
`eyeBlink` 0.6 with no smile is a mid-blink (a weak frame). For a family library
where the good frames are the ones people are laughing in, EAR rejects exactly
what you want. The 2016 EAR paper concedes this itself ("thresholding fails when
a subject smiles") and uses t=0.2, not the 0.25/0.3 seen in blog posts **[cited]**.

**The 0.5 blink threshold has production provenance.** Chromium's shipping
FaceGaze accessibility code uses 0.5 on `eyeBlinkLeft/Right`, with the rationale
in a code comment **[cited]**. Calibration reproduced it: open-eye max **0.269**,
closed-eye min **0.554**, n=10 (6 closed-eye, 4 open-eye controls, public-domain
sample portraits) **[M5 Max, 2026-08-30]** — corroboration, not proof.

**Abstain below inter-ocular distance ≈25 px.** The failure is biased toward
reporting "eyes open", the dangerous direction for a culler. An 18 px-IOD face
gave blinkL 0.688 vs blinkR 0.274 on the same pair of eyes
**[M5 Max, 2026-08-30]**; the 2016 paper reports "major confusion" below IOD
20 px **[cited]**. Gate the judgment rather than guessing.

Worth stealing from the FIQA world: **OFIQ's ISO-standardised EyesOpen
normalisation** — `min(eye apertures) / T-metric(chin → eye-centre midpoint)`,
sigmoid x0=0.02. About ten lines over MediaPipe's landmarks, and pose-normalised
better than EAR because inter-ocular distance shrinks with yaw while the T-metric
does not. NIST scored this definition at MAE 0.01, top tier **[cited]**.

A property unique to a *personal* library: per-person open-eye baselines are
available because the identity is already known — which is what 2026
personalised-threshold work found to be necessary **[cited]**.

### 5.2 Face image quality assessment is the wrong instrument — proven

eDifFIQA(T), the only shippable face-quality model, scored a **closed-eye photo
0.745 and an open-eye photo 0.443** **[M5 Max, 2026-08-30]**. FIQA answers "will
a recogniser match this face", not "is this a keeper". Do not use it as an eyes
or keeper proxy.

### 5.3 Scalar aesthetic and quality scores fail at the comparison that matters

- **Within-tier discrimination collapses.** TuningIQA measures annotator↔MOS
  PLCC **0.85 overall → 0.24 inside a single quality tier**, and shows a
  fine-grained same-scene pair where **MUSIQ (69.42 vs 72.36) and LIQE (3.55 vs
  3.69) both invert** the human preference **[cited]**. That is precisely the
  near-duplicate comparison.
- **LAION-aesthetics would actively hurt a family library.** A 2026 audit found
  landscapes, cityscapes and portraits make up 73% of its top-rated images
  against 39% of the data, and that theme-relative AVA votes were conflated with
  an absolute scale **[cited]**. It prefers a pretty landscape over a picture of
  a child. Its AVA SRCC is also weak (0.665).
- **Q-Align and DeQA-Score are 7B VLMs** (15.4 GB VRAM, 0.22 s on a V100) —
  300×+ over budget, whatever their scores **[cited]**.
- Classical NSS metrics also blow the budget at full resolution: piqe 112 ms,
  brisque 133 ms, niqe 215 ms on M1 Pro CPU **[cited]**. A raw Laplacian on a
  512px downscale is the only single-digit-ms option.

### 5.4 The blur discriminant is spatial, not global

Intentional shallow depth of field, missed focus and shake are not separable by
one global number, but they are separable by *region* — and the face boxes
`face_scoring.py` already produces make it two extra Laplacian calls:

| Face crop | Background | Reading |
|---|---|---|
| sharp | soft | intentional shallow DoF — **keep** |
| soft | sharp | missed focus — **weak** |
| soft | soft | shake / motion — **weak** |

Motion blur is additionally **anisotropic** (gradient-orientation or FFT ridge
directionality); defocus is isotropic. Note that variance-of-Laplacian is
content-dependent and noise-inflated, so it is valid **only within same-scene
comparisons** — which is exactly the burst case. CPBD, S3 and an FFT high-band
ratio are cheap upgrades if a scene-independent number is ever needed.

---

## 6. Integration traps

Each verified on this machine today; none previously reported upstream.

| Trap | Detail | Action |
|---|---|---|
| **MediaPipe 1.0.x hard-aborts on macOS arm64** | `Check failed: service_ Service is unavailable` in `DrishtiMetalHelper`, even with `Delegate.CPU`. Not catchable — kills the process | **Pin `mediapipe==0.10.35` exactly** |
| **The legacy `mp.solutions` API is gone** | Removed around 0.10.31; every `mp.solutions.face_mesh` tutorial online is dead code | Use `mediapipe.tasks.python.vision.FaceLandmarker` |
| **MediaPipe's bundled BlazeFace is selfie-optimised** | Missed 3 of 11 ordinary photos, including two large frontal faces — consistent with its own model card | **YuNet detects → crop with ~50% margin → FaceLandmarker on the crop** (measured to recover the misses) |
| **`mp.Image` aborts on non-contiguous arrays** | Regression since 0.10.30 | Wrap in `np.ascontiguousarray` |
| **MediaPipe wheels are manylinux_2_28, no sdist** | `py3-none` (works on Python 3.13/3.14) but **Alpine and older-glibc NAS images cannot install it** | Portable lane needs a fallback path for those images |
| **InsightFace weights are non-commercial** | "ALL models… non-commercial research purposes only" | Do not add for quality. Identity only if that decision is already made elsewhere |
| **llama.cpp fails open on grammar errors** | Grammar parse error returns 200 OK with *unconstrained* output | **Assert on the response, not the flag** — structurally the same trap as the documented fail-open review pass |
| **JSON mode collapses lexical diversity** | −0.22 bits answer surprisal (p=.0002); bracket-delimited output *gains* 0.13 bits **[cited]** | Matters because description novelty is measured downstream — a grammar could depress that metric with no change in real redundancy. Cheap A/B first |
| **A grammar cannot catch an empty answer** | See §2.1 | Add **content canaries**: a planted-hint test (does output repeat prompt text?) and an image-swap test (does the description change when the photo does?) |
| ComfyUI's landmarker port | Clean pure-PyTorch, but **GPL-3.0** | Reference only |

---

## 7. Production-precedent convergence

Every shipped on-device curation system converged on the same shape, and it is
not "score each photo and take the top N":

| System | What it actually does |
|---|---|
| **Google Top Shot** (2018) | MobileNet-based SSD trained by **knowledge distillation**, human raters supplying **preference** labels. Scores *interpretable attributes* — eye openness, smiling, expression, blur, subject-motion saliency, gyro/OIS global motion, 3A — not a scalar MOS. A GAM over those attributes picks the frame |
| **Google Clips** (2018) | Quality head is a **linear** model trained on **>50 million pairwise choices** across 1,000+ videos, with a **floating** threshold. Two-mode power escalation (1 fps → 15 fps when quality crosses a bar) — a cascade |
| **Apple Photos / Vision** | Computes 26 scalar quality scores of which only the aggregate is exposed; **explicitly forbids thresholding face capture quality** (rank within a burst only); and deliberately contaminates its one public score with **failure and utility** terms rather than shipping pure aesthetics |

Three independent teams, one doctrine: **many cheap interpretable signals,
combined by a simple model trained on comparisons, with no absolute quality
bar.** That is the same conclusion the IQA, embeddings and Apple research lanes
reached separately — and it matches this project's own craft findings (repeat
the binary rather than refine a score; coverage is not quality; a record shot is
judged on a different axis).

---

## 8. Already measured and rejected — do not re-propose

From this project's own measurements **[repo]**:

- **Places365 behind `setting`** — 60% agreement even in its top confidence
  bucket. It is a *scene* classifier and a family photograph is a *people*
  photograph with a room behind it.
- **A person detector for `category`** — closes 42–67% of the gap face detection
  misses but does not separate people from object at all (42% v 36%), because an
  object photograph usually contains the hands holding it.
- **Dropping the pair `reason`** — halves the call, flips 126 pairs one way and
  10 the other. Reverted the same day.
- **Shrinking the 400px tile** — quartering the pixel area buys 11% of the call
  and moves the answer.
- **A distance band absorbing a pair with no model call** — made wrong cuts at
  every hash resolution.
- **Packing several pairs into one request** — 4× cheaper, changes ~20% of
  decisions.
- **An embedding replacing the hash for pair sameness** — DINOv2 ViT-S: 286
  unanimous vs the hash's 291. **Scoped to near-duplicates only**; for "same
  event, different angle" the hash provably collapses (pHash recall **0.016** on
  viewpoint change **[cited]**) and an embedding is the expected winner.
- **Capping output tokens tightly** — a 64-token cap killed 12 of 30 calls by
  truncating mid-JSON, and scored well only because its failures left the sample.

New in this survey: **exact near-duplicate detection is finished** — every hash
hits precision 1.000 / recall 1.000 at threshold 0 **[cited]**, which is why the
DINOv2 comparison tied. Both were at ceiling. No model swap changes it.

---

## 9. Sources

**Repo measurements** — `docs/implementation-plans/2026-08-27-what-the-pass-costs.md`,
`docs/implementation-plans/2026-08-27-visual-analysis-inventory.md`,
`docs/designs/2026-08-27-the-annotation-layer.md`.

**Serving and acceleration**
- vllm-mlx, arXiv 2601.19139 — image cache 6.7–13.1×, continuous batching 3.7–4.3×
- mlx-vlm 0.6.17 (2026-08-26, MIT) — vision feature caching 11×, DFlash/EAGLE-3, LoRA/QLoRA trainer; oMLX (Apache-2.0)
- Block Diffusion on Apple Silicon, HF blog 2026-05-31 — Qwen3.6-27B 8-bit 16.4 → 55.3 tok/s, byte-identical, **text only**
- ViSpec, arXiv 2509.15235 (2025-09-17) — vanilla speculative decoding gives <1.5× on VLMs
- Apple, *Exploring LLMs with MLX and the M5 GPU*, 2025-11-19 — TTFT 3.33–4.06× vs M4; generation 1.19–1.27×
- Kroeger et al., *Fast Optical Flow using Dense Inverse Search*, arXiv 1603.03590 (ECCV 2016) — VGA 0.93 ms / 2.35 ms per pair

**Multi-image and structured output**
- MMIU, arXiv 2408.02718 (2024-08-05) — GPT-4o 55.7%
- *Identifying and Mitigating Position Bias of Multi-image VLMs*, arXiv 2503.13792 (CVPR 2025 Oral)
- *More Images, More Problems?*, arXiv 2601.07812 (2026-01-13)
- JSONSchemaBench, arXiv 2501.10868 (2025-01-18); Structured Output Benchmark, arXiv 2604.25359 (2026-04-28)
- mlx-vlm issues #1904 (2026-08-14) and #1826 (2026-08-09) — the 41-model M5 Max field test
- JSON-mode lexical diversity, arXiv 2607.18476

**Embeddings, dedup and distillation**
- SSCD, arXiv 2202.10261 (CVPR 2022) — Table 2 score-normalisation gains
- ISC2021 results, arXiv 2202.04007; DrivenData winners write-up 2021-12-09
- Sentence-BERT, arXiv 1908.10084 (EMNLP 2019) — Table 6 concatenation ablation
- *A Metric Learning Reality Check*, arXiv 2003.08505 (ECCV 2020)
- *On the rankability of visual embeddings*, arXiv 2507.03683 (NeurIPS 2025)
- ShareGPT4V, arXiv 2311.12793 (ECCV 2024); Recap-DataComp-1B, arXiv 2406.08478 (ICML 2025)
- imagededup benchmarks — UKBench pHash recall 0.016; exact-dup precision/recall 1.000
- PDQ, arXiv 2212.08035; SigLIP 2, arXiv 2502.14786 (Apache-2.0, 2025-02-20); DINOv2 (Apache-2.0); DINOv3 licence (2025-08-13)
- FrugalGPT, arXiv 2305.05176; Trust-or-Escalate, arXiv 2407.18370; early-abstention cascades, arXiv 2502.09054

**Quality, faces and aesthetics**
- pyiqa / IQA-PyTorch — model zoo, efficiency CSVs, PolyForm-NC licence
- TuningIQA, arXiv 2508.17965 (2025-08-25) — PLCC 0.85 → 0.24 within tier
- *Real-time Burst Photo Selection*, arXiv 1803.07212 (2018-03-20) — 0.47 MB, 13 ms, 64.1% / 86.2%
- ARNIQA (WACV 2024, Apache-2.0); LAION aesthetics bias audit, arXiv 2601.09896
- Soukupová & Čech, *Real-Time Eye Blink Detection using Facial Landmarks*, CVWW 2016
- Chromium FaceGaze `gesture_detector.ts` — the 0.5 blink threshold and its rationale
- OFIQ / ISO-IEC 29794-5; NIST IR 8485 (2026-08) — EyesOpen MAE 0.01
- Personalised blink thresholds, arXiv 2604.22479

**Apple and Google on-device**
- Apple developer docs — `CalculateImageAestheticsScoresRequest` and
  `ImageAestheticsScoresObservation` (macOS 15.0+, iOS 18.0+);
  `GenerateImageFeaturePrintRequest`; coremltools ResNet-50 = 1.63 ms (iPhone 15 Pro)
- Google, *Top Shot on Pixel 3*, 2018-12-20
- Google, *Automatic Photography with Google Clips*, 2018-05-11
- Google, *Behind the Motion Photos Technology in Pixel 2*, 2018-03-13
- Frigate motion-detection docs — `lightning_threshold`

---

## 10. Open questions worth a probe

1. **Is the local endpoint actually batching?** One uncontended run at
   concurrency 1 vs 8 settles §0 point 1. The `1 <= concurrency <= 8` validation
   cap is also worth revisiting against oMLX's configurable maximum.
2. **DFlash acceptance rate on image-conditioned prompts** — 30 minutes, and it
   decides whether the byte-identical 3.38× survives contact with vision tokens.
3. **Qwen3.5-4B on the structured card task** — the recommended size was absent
   from the 41-model field test. Also worth benchmarking Ministral-3-3B, which
   has the only real end-to-end number.
4. **ARNIQA's KonIQ-10k SRCC** — unpublished anywhere; must be self-measured
   before it can be trusted as a bar.
5. **What motion metadata iOS actually writes into the Live Photo container** —
   if gyro data is present, class 4 becomes Google's hybrid approach rather than
   a purely visual one.
6. **Whether a 4-bit or MTP variant of the incumbent preserves the banked
   verdicts** — `mlx-community` hosts MTP builds of the 27B, and MTP is
   self-speculative (the draft head sees image tokens), which sidesteps ViSpec's
   objection. But it is a quantisation change, so it expires calibration.

---

## Addendum — owner ruling 2026-08-30 (same day): cross-platform first

The owner ruled after reading this report: **the portable stack is the shipped stack — no
per-platform implementations.** Apple Vision's one-pass (14.2 ms measured above) is kept as a
reference benchmark only, not a code path. Where this report leaned on Apple Vision, the
portable substitutes are: aesthetics → a LAION-aesthetics-class linear head on the embeddings
this pipeline already banks; screenshot/utility → small portable classifier or the in-tree
OCR + frame_quality signals; feature print → the banked DINOv2/SigLIP embeddings themselves.
Everything else recommended here (YuNet, MediaPipe 0.10.35, eDifFIQA(T) ONNX, classical-CV
motion) was already cross-platform. Deployment constraint restated: mediapipe wheels are
manylinux_2_28 — Debian-based Docker images only, never Alpine.

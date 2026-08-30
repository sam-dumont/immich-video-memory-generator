---
date: 2026-08-30
status: research — distillation feasibility; five parallel streams, all closed
issue: none-yet
---

# Distilling the card model

Can the card job leave the incumbent ~27B (`Qwen/Qwen3.8-27B`, served via omlx/MLX) by
*training a student on its own banked outputs*, rather than by shopping for an
off-the-shelf replacement?

Companion to two same-day notes that answer the adjacent questions:

- [2026-08-30-small-vision-models-landscape.md](2026-08-30-small-vision-models-landscape.md) — can an
  existing ≤10B model take the job as-is (shortlist, exclusions, threshold literature).
- [2026-08-30-bulk-visual-analysis-alternatives.md](2026-08-30-bulk-visual-analysis-alternatives.md) — the
  five call classes, and which ones want an instrument rather than a model.
- [2026-08-30-fast-lane-licensing.md](2026-08-30-fast-lane-licensing.md) — licence verdict for the
  frozen-encoder lane.

This note assumes those and does not repeat them. Its subject is **response-based knowledge
distillation**: fine-tune a small student on the teacher's own temp-0 JSON, then ship the student.

Every number is tagged `[cited arXiv-id/date]` (a figure from a dated source),
`[measured]` (observed first-hand during this investigation, hardware named), or
`[EST]` (arithmetic, not observed).

---

## Verdict

| Task | Verdict | Student | Why |
|---|---|---|---|
| **Fused CARDS / asset descriptions** | **Distil.** Highest confidence of the three. | Qwen3-VL-2B-Instruct, LoRA r=8 | A published 2026 paper ran this exact task shape and beat a 9B general model with a 2B student `[cited 2608.22070/2026-08-22]` |
| **Episode-scan / cull triage** | **Distil, but not as a VLM.** Reshape to per-tile classification on frozen embeddings. | frozen SigLIP2 / DINOv2 + head | Tile sheets are where small VLMs break — cross-image leakage, position bias, middle-content neglect. Closed-vocabulary decisions belong on heads. |
| **Per-frame audit flags** | **Distil, same head recipe. Do this one first.** | frozen encoder + multi-label head | Lowest risk and lowest cost; validates the label-banking, training and shipping machinery on a task where being wrong is cheap. |
| **Editorial judgment** | **Stays on the 27B by doctrine.** Not researched, not challenged. | — | — |

The boundary between the head family and the generative family is **field shape**, not task name:

| Field shape | Frozen encoder + head | Evidence |
|---|---|---|
| Closed-vocabulary, single-label (scene type, activity class, keep/park, blur, eyes-closed) | **Works** — +12 to +27 points over prompting the same encoder; 5–20 examples per class to reach parity | CLIP linear-probe suite; frozen DINOv2 probe generalised as well as fully fine-tuned baselines four orders of magnitude larger `[cited 2607.02559/2026-07]` |
| Open-vocabulary, multi-label, long tail (free-form attributes, hedged relations) | **Fails on the tail** — frozen CLIP 47.53 mAP vs 69.03 fine-tuned on VAW; OVAD tail classes 9.5 AP vs 58.6 head | `[cited 2301.09506/2023-01]` |

So the hedged people/relations/activity fields were never head material. Splitting the card
across heads applies to the closed fields only.

---

## 1. The precedents: this experiment is already published

Two 2026 papers make this much less speculative than it looked.

**SoulGard-VL-2B** `[cited 2608.22070/2026-08-22]` is the same task shape: a **Qwen3-VL-2B student
emitting structured JSON scene fields, LoRA r=8, trained on teacher labels**. Its main run at 26K
JSON targets took the 2B student from **53.72% → 83.63%**, beating Qwen3.5-VL-9B at **75.08%**.

**i1** `[cited 2606.11289/2026-06-09]` (Princeton, captions released MIT) used **Qwen3-VL-30B-A3B as
its teacher captioner** across 12 corpora and ran an explicit ablation of Qwen2-VL-2B /
Qwen2.5-VL-3B / Qwen3-VL-2B / Qwen3-VL-4B *as synthetic captioners*. That is this pipeline,
published, permissively licensed. Caveat: their "publicly available" is not licence-clean —
ImageNet's non-commercial terms and RedCaps' NC clause are both in their mix.

Supporting distillation evidence at this scale:

| Work | Teacher → student | Result |
|---|---|---|
| LLaVA-KD `[cited 2410.16236/2024-10-21]` | 7B MLLM → Qwen2.5-**0.5B**, ~1.2M | VQAv2 74.8→77.7, TextVQA 49.2→52.0; the distillation stages *alone* worth +3.2pp |
| VLsI `[cited 2412.01822/2024-12-02]` | large VLM → 2B / 7B | +11.0% (2B), +17.4% (7B) vs GPT-4V across 10 benchmarks |
| RubiCap `[cited 2603.09160/2026-03-10]` | — | 7B captioner matches Qwen2.5-VL-32B on CaptionQA; 3B surpasses its own 7B. Also the warning: *"supervised distillation often yields limited output diversity and weak generalization"* |
| VAREX `[cited 2603.15118/2026-03-16]` | — | extraction-specific fine-tuning at **2B yields +81pp** on structured extraction |

---

## 2. Data scale — 15–25k, not 60k

SoulGard published the curve nobody else did, on the exact task shape `[cited 2608.22070/2026-08-22]`:

| Teacher-labelled samples | Avg accuracy |
|---|---|
| 2K | 70.92% |
| **8K** | **80.64%** |
| 10K | 80.62% |

**2K→8K buys +9.7pp. 8K→10K buys −0.02pp.** Flat by 8K.

Convergent evidence from the subset-selection literature:

- Random 20% of LLaVA-665K (133K) reaches **95.8%** of full performance `[cited 2501.00654/2025-01]`
- 10% of data reaches **102.1% / 103.7%** of full-data fine-tuning `[cited 2510.03880/2025-10]`
- 10% of LLaVA-665K reaches **100.2%** of the full visual instruction tune `[cited 2504.21850/2025-04]`
- ~15% ≈ full, beating it on 4 of 8 benchmarks `[cited 2403.09559/2024-03]`
- ShareGPT4V: 100K teacher captions in SFT = +2.5 MMBench; the 1.2M scale-up on top = +2.0 more.
  **12× more captions bought ~2 points.** Swapping caption *quality* at identical count = +1.9 `[cited 2311.12793/2023-11]`
- Format is cheap: 3,688 samples on a 0.77B model → **99.8% valid JSON**, but material accuracy
  63.0% against category accuracy 94.6% `[cited 2605.09827/2026-05]`. That spread is the
  format/judgment split.

**Plan: 3–5k pilot → 15–25k main run. Above 50k expect under 1–2 points.** The owner's ~12k banked
descriptions are already in the right range — but see §4 and §11 for why they should be the
*eval* set, not the training set.

---

## 3. The two corrections

### 3.1 Never train on 100% teacher output

Recap-DataComp-1B's mixing ablation, where `p` is the probability of using the **original human**
caption rather than the synthetic one `[cited 2406.08478/2024-06]`:

| p | ImageNet-1K zero-shot | COCO I→T |
|---|---|---|
| 0.0 (all synthetic) | **36.0** | 53.0 |
| 0.5 | 67.2 | **61.9** |
| 1.0 (no synthetic) | 69.7 | 57.3 |

All-synthetic **collapsed** ImageNet zero-shot from 69.7 → 36.0. The peak is 50/50.

**Blend roughly half human-written supervision into the mix.** Localized Narratives is the free
answer: human-written, CC BY 4.0, and already annotated on the *same* Open Images photographs, so
it costs nothing extra to source.

Related: FineVision `[cited 2510.17269/2025-10-20]` (CC-BY-4.0, 24M samples) delivered +12.7pp over
`the_cauldron` at nanoVLM scale, and its conclusion argues against aggressive filtering —
*"preserving breadth rather than aggressive filtering yields the best downstream generalization."*

### 3.2 LoRA r=8, not r=16 — the list-degeneration receipt

The generic consensus across ten shipped configs (Unsloth notebooks, HF cookbooks, LLaMA-Factory,
Roboflow maestro, Extract-0) is r=16, α=32, lr 2e-4, 1–3 epochs. Rank 16 was found *"a consistent
sweet spot"* across 10 datasets `[cited 2508.12512/2025-08-17]`.

**That consensus does not survive contact with this schema.** High-capacity LoRA on dense
repeated-list fields produced **duplicate rate 0.080, max repeat sequence 23**; the interference
was explicitly **"structure-bound"**, hitting repeated lists only, and was fixed by an
object-level repeat-stop (duplicate rate → 0.000) `[cited 2606.14507/2026-06]`.

The card's hedged people/relations/activity arrays are exactly that shape. SoulGard shipped **r=8**
on the same task shape `[cited 2608.22070/2026-08-22]`. **Use r=8, α=16, and add a duplicate-rate
check to the eval gates** (§7).

### 3.3 Freeze the vision tower; do not freeze the language model

Freezing *both* backbones in a LLaVA-style stack costs **9.2 points** (60.3 frozen vs 69.5 with
LoRA) `[cited 2405.02246/2024-05-03]`. In `mlx_vlm.lora` terms: leave `--train-vision` off, but let
LoRA reach the language model rather than the projector alone.

Two independent lines converge on this. From the privacy side, **head tuning was the
highest-leaking configuration measured** — exposure 10.78 and MIA recall 81.6%, against 1.42 and
19.2% for full fine-tuning — attributed to *"the number of parameters and the **location** of the
parameters, right at the last layer"* `[cited 2205.12506/2022-05-25]`. Projector-only training is
structurally head-like. Avoid it for both reasons.

The freeze-vs-unfreeze disagreement in the literature resolves on data volume: Prismatic (freeze,
single-stage) ran ~665K; Cambrian-1 (unfreeze) ran 1.2M–5M. **At 15–25k the Prismatic regime
applies unambiguously.** And per MM1 `[cited 2403.09611/2024-03-14]`, *"the vision-language
connector design is of comparatively negligible importance"* — do not spend a day on projector
architecture.

---

## 4. Corpus verdict

The naive plan trained on the owner's own library. Two independent findings make that the wrong
corpus for weights that get redistributed — a generalisation argument (§4.1) and a privacy
argument (§8). The corpus below replaces it.

### 4.1 Open Images V7 is the answer

| Property | Value |
|---|---|
| Licence purity, 68,313-row metadata slice | **100.0% CC BY 2.0**; zero blank `Author`, zero blank `AuthorProfileURL` `[measured]` |
| Personal-photo yield (machine labels ≥0.5, 72,892-image sample, extrapolated to 9.01M) | **20.3% ≈ 1.83M**; intersected with `Person`, **13.1% ≈ 1.18M** `[measured]` |
| Flickr link rot (n=200 HEAD) | **86.0% alive** `[measured]` |
| **Licence drift** (n=150 landing pages scraped 2026-08-30) | of 132 reachable: **93.2% still CC BY 2.0; ~3.8% moved to All Rights Reserved or NonCommercial** `[measured]` |
| **Zero-link-rot path** | CVDF `s3://open-images-dataset` covers only ~18% of personal-filter IDs `[measured]` — but that is **~330k personal-photo-like CC-BY images, ~100 GB** at 303 KB mean object size `[measured]` |

330k is 13–20× the revised training budget, with **no Flickr fetching at all**. The dataset ships
its own filter vocabulary among 20,932 classes — `Snapshot`, `Party`, `Birthday`, `Birthday cake`,
`Family`, `Toddler`, `Pet`, `Vacation`, `Picnic`, `Barbecue`, `Baby shower`. Corroborating the
domain: **9.6% of titles are raw camera filenames** (`IMG_0186.jpg`, `DSC00504`, `HPIM2646.JPG`)
`[measured]`.

Google's own disclaimer is the reason for the drift measurement: *"we make no representations or
warranties regarding the licence status of each image and you should verify the licence for each
image yourself."* CC grants are irrevocable, so a later relicence does not retract the grant for a
copy taken while it was CC BY — but proving it becomes your problem. **Snapshot the licence string
with a retrieval timestamp and a content hash at download time.**

Residue to filter: ~2.6% of authors are institutional/archival (Biodiversity Heritage Library,
Boston Public Library, war-photo archives) `[measured]`.

### 4.2 Megalith-CC0 — the zero-obligation secondary

2,385,784 rows, **CC0 only**, MIT metadata, persisted to an independent public S3 bucket under the
AWS Open Data Registry. Sampled recaptions: **58.2% mention a person, 32.4% have indoor cues,
12.6% hit personal-life keywords** `[measured]` — 6.7× more personal than PD12M, at **zero
attribution obligation**. Its own card is honest about residue: 5–7% may have minor edits, 1–2%
may have copyright concerns despite the metadata, 1–2% non-photographs.

### 4.3 The exclusion list, with reasons

| Corpus | Reason |
|---|---|
| **COCO / COCO Captions** | **~70% NonCommercial, ~33% NoDerivatives, only ~16% plain CC BY** — computed from COCO's own `licenses` array across 164,073 images in two splits `[measured]`. COCO hosts pixels it does not own. |
| **YFCC100M** | 68.2% NonCommercial `[cited 1503.01817/2015-03]`. Usable only if filtered to the 17.2M plain-CC-BY slice. |
| **PD12M / PD-Extended** | Licence-perfect (59.8% CC0 / 40.1% PD Mark `[measured]`) but **fails on content: 1.88% personal-life keywords** `[measured]`. Museum and nature archive material; the paper's own subtitle is *"A Highly Aesthetic Image-Text Dataset"*, and aesthetic filtering is the opposite of what a family-photo model wants. |
| **LAION-5B / Re-LAION-5B** | CSAM findings 2023-12; re-released 2024-08-30 with 2,236 links removed; **all major LAION repos are `gated: auto` on HF as of 2026-08-30** `[measured]`, and the re-release is named *"research-safe"*. Reputational hazard for a family-photo app regardless of the legal read. |
| **RedCaps** | Closest thing to a consumer-photo corpus and explicitly off-limits: *"non-commercial research"* only, and *"should not be used for any tasks that involve identifying features related to people"* — which would directly forbid the hedged people/relations fields. |
| **VIST / SIND, MemexQA, CUFED / ML-CUFED** | Right domain (Flickr albums of personal events) but NC **and** ND contamination; MemexQA's terms say *"You will NOT distribute the above images."* Useful as eval only. |
| **CC12M / Conceptual Captions, PixelProse, Recap-DataComp, DataComp/CommonPool** | No image licence granted. DataComp additionally documented as a consent hazard: ≥122M samples show a copyright notice, 60% of the top-50 domains prohibit scraping in ToS `[cited 2511.08637/2025-11-10]`. |
| **ImageNet, Places365** | Non-commercial research and education only; both now gated `[measured]`. |
| **EPIC-KITCHENS** | CC BY-NC 4.0. |
| **Wikimedia WIT** | Person-primary images excluded by design — wrong content. |
| **CC BY-SA generally** | Legally probably fine, but CommonCanvas shipped SA weights anyway and CC's own conservative guidance says to. Cheap risk to buy out when the goal is permissive weights. |

**Corpus policy: CC0 and CC-BY only. Exclude NC, ND, and SA.** The NC exclusion is not close —
shipping permissive weights grants every downstream user the right to commercialise, and that
right cannot be granted over an NC-limited upstream.

---

## 5. Methods considered

| Method | Data needed | Effort | Quality evidence | Anchoring / phantom-fill risk |
|---|---|---|---|---|
| **Response-based KD** (SFT/LoRA on teacher JSON) — *chosen* | 15–25k | Low | +81pp at 2B on structured extraction `[cited 2603.15118/2026-03]`; 2B beats 9B on this task shape `[cited 2608.22070/2026-08]` | **Anchoring solved structurally** — no in-context examples survive distillation; adaptation *"reduces prompt sensitivity, producing more consistent behavior"* `[cited 2603.13306/2026-03]`, PAFT +7% generalisation `[cited 2502.12859/2025-02]`. **Phantom-fill inherited and amplified** — see below |
| **Frozen encoder + classification head** — *chosen for triage/audit* | 1–10k per field | Very low | frozen DINOv2 + linear probe generalised as well as fully fine-tuned baselines four orders larger, 10–20 labels/class `[cited 2607.02559/2026-07]` | Structurally cannot phantom-fill — closed vocabulary, no generation |
| Frozen encoder + tiny trained decoder | 100k+ | High | ShareGPT4V trained its captioner from 100K teacher captions then scaled to 1.2M `[cited 2311.12793/2023-11]` | Free generation; same exposure, less capacity to resist |
| Feature / logit distillation | + teacher forward passes | High | helps most when an intermediate teacher bridges the capacity gap `[cited 2605.10641/2026-05]` | no specific evidence |
| On-policy distillation | rollouts + teacher as grader | Very high | closes 63% of the gap vanilla, 97% scheduled `[cited 2608.24987/2026-08]` | *"limiting its performance to that of the teacher"* `[cited 2608.24696/2026-08]` |
| Preference / rejection-aware (DPO) | ranked pairs | Medium-high | continuous DPO raised non-hallucination caption rate **48.3% → 77.9%** at 7B `[cited 2504.13123/2025-04]` | **the only measured fix for phantom-fill that needs no architecture surgery** |

**The phantom-fill warning, stated plainly.** Plain SFT on a detail-rich corpus lengthens captions
but *"over forty percent still name absent objects"* `[cited 2608.12746/2026-08-13]`. LVLMs
*"hallucinate more frequently on images seen during training"* `[cited 2508.04567/2025-08-06]`.
And in sequence-level KD specifically — this exact recipe — students *"memorize more than baseline
models… and show increased hallucination rates"*, with the authors' own conclusion:
*"students inherit both their teachers' superior performance and their fault modes, thereby
requiring active monitoring"* `[cited 2502.01491/2025-02-03]`.

Expect to fail the phantom-fill gate on the first pass and need a DPO round. Budget for it.

---

## 6. The consolidated recipe

| Step | Decision |
|---|---|
| **Corpus** | Open Images V7, personal-life label filter ∩ `Person`, minus institutional authors → pull the ~330k on `s3://open-images-dataset`. Top up from Megalith-CC0. Exclude NC, ND, SA, and COCO/YFCC/RedCaps/VIST/CUFED/LAION/ImageNet/DataComp |
| **Teacher** | Qwen3.8-27B (Apache-2.0, no distillation clause). **Downscale to ~512px long edge first** — resolution is 3–4× the run's wall clock; measured 21.7 s/image at 1024px on an M4 Max, 2.8 s with cached vision embeddings |
| **Labels** | Strip proper nouns at generation time. **Blend ~50% Localized Narratives** (human, CC BY 4.0, same images) |
| **Scale** | 3–5k pilot → **15–25k** main run. Not 60k |
| **Student** | Qwen3-VL-2B-Instruct (Apache-2.0). **LoRA r=8**, α=16, lr 2e-4, ≤3 epochs, vision tower frozen, LoRA reaching the LLM — not projector-only |
| **Venue** | M5 Max locally (~1–3h), or ~$1–4 on a RunPod Community 4090 |
| **Ship** | Weights Apache-2.0, app MIT, GGUF + MLX (not ONNX), `sources.parquet` beside the weights, ungated repo |
| **Gates** | Field F1 vs the ~95% teacher self-agreement ceiling · `FP_fields/predicted_fields` ≤ teacher · **duplicate rate on list fields = 0** · canary exposure single-digit at 1× and 5× |

### Cost and venue detail

Labelling `[EST]`: at ~$0.00046/image via a hosted Qwen3-VL-32B endpoint, 25k images ≈ **$12**.
Locally, downscaled to 512px, `[EST]` 6–10 s/image → 25k in **40–70 h**, i.e. two or three
overnight runs, free.

Training, FLOPs model `4.5 × N × T`, ~440 tokens/sample (a 400px tile is only **144–169 visual
tokens** on Qwen3-VL: `patch_size 16`, `spatial_merge_size 2`, one token per 32×32px `[measured]`
from the model config) `[EST]`:

| Job | RunPod Community 4090 @ $0.34/hr | A100 80GB @ $1.19/hr | M5 Max local |
|---|---|---|---|
| 2B, 12k, 3 epochs | **$0.87** | $1.61 | 1.2–2.4 h |
| 2B, 25k, 3 epochs | **~$1.80** | ~$3.35 | 2.5–5 h |
| 2B, 50k, 3 epochs | $3.60 | $6.70 | 5–10 h |

Costs converge across GPU tiers because $/FLOP is roughly constant — an H100 is ~6× faster at ~8×
the price. Pick on convenience. Prices verified 2026-08-30 `[measured]`.

### Two operational landmines

**`mlx-vlm` shipped a bug where images were discarded entirely during LoRA training** — the run
completed, the loss curve looked plausible, and the model learned nothing visual (PR #823, fixed
2026-03-15). A user's measurement: *before fix, 23 trained tokens/iter with loss stuck at 0.43;
after, 1,076 tokens/iter and loss 22.1 → 3.2.* Two related training bugs are open as of
2026-08-30. **Verify trained-tokens/iteration ≈ 440 × batch before trusting any local run.**

**HF TRL's `SFTTrainer` defaults `max_length` to 1024**, and the docs warn that *"truncating may
remove image tokens."* Set `max_length=None` on that path.

### Free levers not yet spent

- A **Perceiver resampler cuts 729 visual tokens to 64 with no measured loss**
  `[cited 2405.02246/2024-05-03]` — the cheapest remaining throughput move.
- [TinyLLaVA Factory](https://github.com/TinyLLaVA/TinyLLaVA_Factory) (Apache-2.0, last push
  2026-07-23) ships Frozen / Full / Partial / LoRA / QLoRA recipes side by side — the turnkey
  harness for the §3.3 ablation. Licence trap inside its zoo: the Phi-2 (MIT) and Qwen2.5-0.5B
  (Apache-2.0) rows are clean; StableLM-2 (`license: other`) and OpenELM (`apple-amlr`) are not,
  and an Apache tag on a composite checkpoint does not cleanse the base LLM.

---

## 7. Eval gates

Four gates decide whether the student ships. All four are cheap; two of them are corrections to
the obvious choices.

1. **Field-level micro-F1 vs the teacher**, scored against the **~95% teacher self-agreement
   ceiling** — not against 100%. The ceiling is the honest denominator.
2. **Phantom-fill: `hallucination_rate = FP_fields / predicted_fields` ≤ the teacher's rate.**
   This must be computed by hand. Donut's canonical `cal_f1` **pools FP and FN** into
   `TP/(TP+(FP+FN)/2)` and structurally cannot report a hallucination rate; the widely used
   `docext` KIE metric is worse, iterating ground-truth fields only, so **hallucinated extra
   fields are invisible to it**. There is no standard image→JSON hallucination metric as of
   2026-08.
3. **Duplicate rate on list-shaped fields = 0** — the §3.2 failure mode, invisible to F1.
4. **Canary exposure single-digit at 1× and 5×** — see §8.

Report Donut nTED (`max(0, 1 − TED(pred,gt)/TED(∅,gt))`) alongside micro-F1 for structural credit.

**Do not gate on JSON parse-validity.** Parse-validity runs **93.4–100% across all models** and
does not discriminate; the real finding is that *"only those with at least 12B parameters produce
enough valid **extraction leaves**"* `[cited 2602.12203/2026-02-12]`. Measure leaf validity.

**Held-out size: 300–500, paired.** The whole KIE field evaluates on small sets — FUNSD 50 forms,
CORD 100, SROIE 347 — and field-level scoring multiplies effective n (300 docs × ~20 fields =
6,000 decisions). Detecting a 3-point difference *unpaired* needs ~969 items
`[cited 2411.00640/2024-11-01]`, so **A/B successive fine-tunes on the same held-out items**;
pairing removes between-item variance and is the only affordable way to tell v2 from v1.

---

## 8. Privacy: what actually leaks

The instinct — "training on the owner's own library and shipping the weights leaks the library" —
is correct, but the mechanism most people reach for is not the one that bites.

**Verbatim caption regurgitation: LOW.** Each photo yields one unique caption, so the 1×-insertion
regime applies, where data *"inserted just once is rarely memorized"* `[cited 2202.07646/2022-02-15]`.
At ≤3 epochs the model sits on the flat part of the measured fine-tuning curve: summarisation-style
memorisation runs 14.12% at epoch 1, 14.32% at epoch 3, then knees to **22.33% at epoch 5**
`[cited 2310.06714/2023-10-10]`. Even in the worst task measured, **verbatim** regurgitation is ~4%.

**A correction worth recording.** The headline figure often quoted here — 94.83% membership-inference
success against captioning models `[cited 2209.06997/2022-09-15]` — comes from **LSTM captioners
trained from scratch on 3,000 pairs**, a maximally overfit regime that does not describe a LoRA'd
2B pretrained VLM. And on a balanced 6,000-image benchmark, state-of-the-art VLM MIA
*"approached chance-level"*, with the authors attributing prior results to *"detecting
distributional bias introduced during dataset construction rather than… true membership status"*
`[cited 2510.16295/2025-10-18]`. **Treat any VLM-MIA AUC above ~0.6 as unreplicated.**

**Name leakage is the real risk, and it is a different mechanism.** A recurring personal name is
not a 1× sequence — across 15–25k captions it is a 10³-scale duplicate, and duplication drives
regeneration *superlinearly*: *"a sequence present 10 times in the training data is on average
generated ~1000 times more often than a sequence present only once"* `[cited 2202.06539/2022-02-14]`.
Deduplication stops helping past ~408 repeats `[cited 2202.07646/2022-02-15]`.

So the realistic failure is not recitation. It is: **show the shipped model any photo of a
frequently-photographed person, and it writes their name.** That is a *learned capability*, not
memorisation — and **deduplication, epoch caps and low-rank LoRA do not touch it.** Only stripping
proper nouns before training does.

### Mitigation stack, by risk-reduced ÷ effort

1. **Strip proper nouns at caption-generation time** — constrain the teacher prompt so it never
   emits personal names, street names, house numbers or school names; NER-scrub as a second pass;
   then grep the corpus for known strings. *The only mitigation that addresses the real risk.*
2. **Epochs ≤ 3.** Free, and it is the difference between the flat segment and the knee.
3. **Collapse near-duplicate photos before captioning** (perceptual hash, one keeper per burst) —
   buys a measured 3–10× `[cited 2107.06499/2021-07-14]`.
4. **LoRA is a mild mitigation, not a defence.** Measured exposures are *not monotone* in adapter
   size `[cited 2205.12506/2022-05-25]`; see §3.3 for why projector-only is the worst choice.
5. **Skip DP-SGD.** Best-known 2025 pipelines recover only **63% of non-private performance** at
   ε∈{4,6} `[cited 2511.14936/2025-11-18]`; captioning-specific, ε=1.3 costs ROUGE-L
   0.245→0.147. It would not remove the name→face association anyway.

### The verification, runnable in an afternoon

Secret-sharer canaries `[cited 1802.08232/2018-02-22]`, `exposure = log₂|R| − log₂ rank`. Mint ~50
canary captions with a 6-digit secret (|R|≈10⁶, extraction threshold ≈20), inject at
**1×, 5×, 20×, 100×**, train the real recipe. **Pass: 1× and 5× groups in single digits.**
Calibration: a 50× canary over 20 epochs peaked at exposure 14.54 and was still not extractable.

Add adversarial prompting with the **longest** prefix the model accepts — discoverability rises
from 33% to 65% between 50 and 450 tokens of context `[cited 2202.07646/2022-02-15]`.

Publishing those numbers on the model card is not merely diligence: EDPB Opinion 28/2024 ¶55 asks
for *"structured testing against: (i) attribute and membership inference; … (iii) regurgitation of
training data; (iv) model inversion"*, and ¶58(e) asks first for *"the ratio between the amount of
training data, and the number of parameters in the model"* — which for a 2B model on 25k captions
is the metric that cuts against the release. Disclose it anyway.

**On face-blurring:** the ImageNet result — *"overall recognition accuracy drops only slightly
(≤1.0%)"* `[cited 2103.06191/2021-03]` — **does not transfer**. That measured object recognition,
where the face is incidental. Here the model's job is to describe who is present and what they are
doing together. Nobody has measured that cost. Treat blurring as an option for a
privacy-hardened variant only, and expect it to hurt the people/relations fields.

---

## 9. Legal

### 9.1 The licence chain is clean

Verified 2026-08-30 `[measured]` via the HF API and raw LICENSE files. **The whole Qwen3-VL family
is Apache-2.0 across all 40 repos**, and the GitHub LICENSE is plain Apache-2.0 with no addendum:
no output clause, no distillation clause, no naming requirement. Apache-2.0 §2 grants rights over
*the Work*; model outputs are not the Work.

What that dodged, for contrast:

| Family | Clause |
|---|---|
| **Tongyi Qianwen Licence** (Qwen1/Qwen2 era) | *"You can not use the Materials or any output therefrom to improve any other large language model"* — would have killed this plan outright |
| **Gemma ≤3** | "Model Derivatives" expressly includes *"synthetic data Outputs by Gemma for training that model"*, plus downstream pass-through |
| Llama 3.1+ | must prefix the derived model's name with "Llama" |
| Hosted OpenAI / Anthropic APIs | ToS prohibit training competing models on outputs |

⚠️ **The Qwen3.8 family is now mixed.** `Qwen3.8-27B` (2026-08-05) is Apache-2.0, but
`Qwen3.8-Flash-Next` (2026-08-24) and `Qwen3.8-2.4T-A95B` ship under **Qwen Community Licence
1.0** `[measured]`. Re-check the licence tag on any future teacher upgrade rather than assuming
family uniformity.

**Blockers for a permissive release:**

| Blocked | Why |
|---|---|
| Qwen2.5-VL-**3B** | Qwen Research Licence — *"FOR NON-COMMERCIAL PURPOSES ONLY."* The 7B/32B/72B siblings are Apache-2.0; the 3B alone is not, and at least one community GGUF of it is mis-tagged apache-2.0 `[measured]` |
| PaliGemma 2 | Gemma Terms + manual gating |
| Moondream 3 / 3.1 | BSL 1.1 — not an open-source licence, cannot be sublicensed. Moondream **2** is Apache-2.0 and fine |
| DINOv3 | Custom Meta licence + gating; commercial use is granted but with mandatory *"Built with DINOv3"* display, pass-through, and a no-reverse-engineering clause — **usable in a product, not relicensable**. DINOv2 relicensed CC-BY-NC → Apache-2.0 on 2023-08-31 and is fine |

Newly viable: **Gemma 4 flipped to Apache-2.0**, ungated, multimodal (E2B 2.3B effective, E4B
4.5B) `[measured]`.

### 9.2 Weights are probably not a copy — the best authority

**Getty Images v Stability AI [2025] EWHC 2863 (Ch), 4 Nov 2025, ¶600:**

> "…is an AI model which derives or results from a training process involving the exposure of
> model weights to infringing copies itself an infringing copy? **In my judgment, it is not.** …
> **the model weights are not themselves an infringing copy and they do not store an infringing
> copy.** They are purely the product of the patterns and features which they have learnt."

Full trial record. Permission to appeal granted 16 Dec 2025; no Court of Appeal judgment as of
2026-08-30. The US Copyright Office's pre-publication Part 3 (2025-05-09) frames it compatibly:
liability *"turns on whether the model has retained or memorized substantial protectable
expression."*

Creative Commons' own guidance (2025-05-28) says attribution is *"triggered only upon public
sharing of the original work or an adaptation of it"*, that *"reusers engaged in text and data
mining do not have to adhere to the marking and attribution requirements"*, and that a link to the
dataset can satisfy attribution. CC Signals — announced 2025-06-25, reset 2026-04-23 — exists
precisely because the current licences do not reach training; nothing has shipped as of 2026-08-30.

### 9.3 The two behavioural rules from Beaulier

*Beaulier v. Meta Platforms*, N.D. Cal., order granting motion to dismiss **2026-08-26** — the one
case squarely about CC-BY attribution stripped during dataset ingestion. It was dismissed on
intent, and the reasoning yields two rules:

1. **Never build a step that targets licence metadata.** The dismissal turned on *"uniform
   transformation that incidentally sheds CMI… discards everything that is not part of the
   training signal — including, but not specifically targeting, CMI."* Let metadata fall off as an
   ordinary consequence of resize and encode; keep it in a parallel table. Cases that survived
   (*NYT v. Microsoft*, *Concord Music v. Anthropic*) involved defendants who **chose tools for
   their CMI-stripping effectiveness**.
2. **Never publish a caption dataset with the creator/licence columns dropped.** Count II died on
   *"This allegation concerns internal use, not distribution."* Publishing weights is very likely
   not distribution; publishing a stripped dataset is the one act that would land inside
   §1202(b)(3).

The live money in this area is NonCommercial, not attribution: *S.A. Jamendo v. NVIDIA* (FAC
2026-07-21) alleges training on a CC BY-NC-SA corpus and seeks ≥EUR 17.8M; the word "attribution"
appears once in 98 pages.

### 9.4 GDPR — build the takedown path first

**A copyright licence is not a legal basis.** EDPB Opinion 28/2024 (adopted 2024-12-18):
*"the mere fact that personal data is publicly accessible does not imply that 'the data subject
has manifestly made such data public'."* It also holds that models trained on personal data
*"cannot, in all cases, be considered anonymous"* (¶34), and that weights may retain information
*"'absorbed' in the parameters of the model"* (¶31). Legitimate interest is the route, via the
documented three-step test.

EU AI Act Art. 5(1)(e) (applicable 2025-02-02) prohibits creating or expanding facial-recognition
databases through untargeted scraping. A scene-description model is not that — but the hedged
people/relations fields are close enough to the neighbourhood that the model card should say so
explicitly, and **no face-embedding or identity-matching capability should ship with the weights.**

**The withdrawal precedent is the operative lesson: every dataset that has ever been pulled was
one about people** — 80 Million Tiny Images (withdrawn 2020-06-29, *"we ask the community to
refrain from using it… and also delete any existing copies"*), DukeMTMC (2019-05), MS-Celeb-1M
(2019-04, and copies persist regardless — you cannot un-ship a dataset), LAION-5B (2023-12).
**Build the takedown path against the manifest before publishing, not after.**

### 9.5 EU AI Act GPAI — out of scope, comply cheaply anyway

Chapter V has applied since 2025-08-02 and the 2026 Digital Omnibus (in force 2026-07-27) did not
delay it. The indicative GPAI threshold is **10²³ FLOP**; this job is `[EST]` ~10¹⁸–10¹⁹ via the
Commission's own `C ≈ 6·P·D` — **four to five orders of magnitude below**. As a fine-tuner one
would also need to exceed ⅓ of the base model's training compute; this is ~10⁴× below that.

Art. 53(2) exempts free-and-open-source releases from (a) and (b), leaving **(c) a copyright
policy and (d) a training-content summary** — a few paragraphs each, and the FOSS carve-out does
*not* relieve them. Publish both and the classification question becomes moot.

🔴 **Do not gate the HuggingFace repo.** Commission Guidelines ¶84: access requiring *"the
collection or otherwise processing of personal data should be treated in same manner as
monetisation strategies."* HF gating collects username and email — **gating would arguably forfeit
the open-source exemption.** Also skip `extra_gated_eu_disallowed`. Hosting itself is safe:
Recital 103 says making components available through open repositories *"should not, in itself,
constitute a monetisation."*

---

## 10. Shipping form

**Artifacts: safetensors + GGUF + mmproj, plus MLX quants. Not ONNX.**

### Why ONNX was demoted for this student

It was the obvious portable target and it does not work for a *fine-tuned* VLM:

- **Mainline `optimum` exports zero VLMs.** `optimum-onnx` `main` has 166 `OnnxConfig` classes and
  not one VLM — no Qwen2/2.5/3-VL, no Idefics3/SmolVLM, no Florence-2, no PaliGemma `[measured]`.
  Both native-support requests were closed `not_planned` by stale-bot (optimum#2376 closed
  2026-03-25; optimum#2431 closed 2026-07-15) `[measured]`.
- Every VLM ONNX repo on the Hub is a hand-built one-off. `onnx-community/Qwen3-VL-2B-Instruct-ONNX`
  exists (created 2026-03-01, updated 2026-04-20) with fp32/fp16/q4/q4f16 plus ORT-GenAI
  int4-rtn-block-32 builds for CPU **and** CUDA — but **176 downloads and no model card**
  `[measured]`. Unproven.
- **You cannot export your own.** transformers.js#1622 (opened 2026-03-31, still open with zero
  comments as of 2026-08-30) documents five failed routes for a LoRA-merged SmolVLM that is
  architecturally identical to its base — including patching weights into the published base
  export, which ORT ignores because the HF conversions have fused `SimplifiedLayerNormFusion`
  nodes with weights embedded in the graph `[measured]`.

**The replacement: llama.cpp + GGUF, with MLX as the Mac-native fast path.** Qwen3-VL support
merged 2025-10-30 (PR #16780) `[measured]` — note it is *not* listed in `docs/multimodal.md`,
which is stale and should not be read as the support matrix. One MIT binary on macOS, CUDA and
CPU; GBNF `json_schema` composes with images on the same request; Qwen ships official GGUF +
mmproj under Apache-2.0.

🔴 **Pin llama.cpp ≥ v0.3.0.** Issue #27313 (2026-08-18): llama-server **silently dropped the
second of two adjacent images with identical pixel dimensions** — no warning, request succeeded,
model answered confidently from truncated input. That is exactly a uniform-400px-tile batch. Fixed
by PR #27348, merged 2026-08-19 `[measured]`.

**Do not quantize for latency on Apple Silicon.** Measured on an M5 Max (18-core, 128 GB, macOS
26.5.1, ORT 1.29.0, batch 1, median of 15 runs) `[measured]`: SigLIP2-base-384 int8 is **2.2×
slower** than fp32 (181 vs 83 ms) and q4 is 2.5× slower (205 ms); DINOv2-base int8 is 1.5× slower
on CPU and 18× slower on CoreML. Cause is `ConvInteger`/`MatMulInteger` dynamic quantisation with
no optimised ARM64 kernel path. MLX reaches the same conclusion independently — it deliberately
never quantises the vision tower. **Quantisation here is a disk decision, not a latency one.**

### The head models ship differently — and this is where ONNX wins

SigLIP and DINOv2 have **native first-party `OnnxConfig` classes** in `optimum-onnx`, so
`optimum-cli export onnx` handles them with no custom config `[measured]`. A trained MLP head is
`Gemm`+`Relu`. Measured on the same M5 Max `[measured]`:

| Model | Precision | px | CPU EP | CoreML EP |
|---|---|---|---|---|
| **DINOv2-base** | fp32 | 392 | 107–112 ms | **15.2–15.8 ms** |
| DINOv2-base | fp32 | 224 | 136 ms | **6.0 ms** |
| SigLIP2-base-p16-384 vision | fp32 | 384 | **82.8 ms** | compile fail |
| SigLIP2-so400m-p16-384 vision | fp32 | 384 | 326 ms | compile fail |

**15.2 ms/tile ≈ 66 tiles/s** — a 5–20× margin over any generative path, and the one configuration
where the CoreML EP actually compiles. (Every SigLIP-family graph fails with *"has unbounded
dimension which is not supported"*; CoreML requires constant `B` in MatMul/Gemm, which attention's
Q·Kᵀ violates but a frozen head satisfies.)

### Release form

- **Weights under Apache-2.0; the app stays MIT.** Apache §4 permits relicensing a derivative "as
  a whole", but relabelling Apache-derived weights as bare MIT while dropping the licence copy is
  the one move that creates an argument.
- **`base_model:` names the student's initialisation, not the teacher; the teacher is named in
  prose** with sample counts — the DeepSeek-R1-Distill convention. There is no `distilled_from:`
  metadata field; the AI Act training-content template is ahead of the HF schema here.
- **Ship `sources.parquet` beside the weights**: per image the source URL, creator,
  licence name, licence URL, **retrieval timestamp** and content hash. One line on the model card
  linking to it. This is CommonCatalog's mechanism, it maps onto CC's own "link to a resource"
  language, and for Open Images it is a `SELECT` over metadata that is already complete
  (zero blank author fields `[measured]`).
- **Ungated repo** (§9.5). Runtime download from HF rather than bundling in the wheel — legally
  identical, but it keeps the Apache-2.0 weights out of the MIT sdist and avoids explaining the
  split to every downstream packager. Pin a full 40-character revision SHA.
- Model card carries: parameter-to-data ratio, dedup policy, epoch count, the name-scrubbing
  pipeline, canary exposures, MIA AUCs, who ran the tests and when (§8), and an out-of-scope
  statement that the model is **not for identification of persons**.

---

## 11. The untested assumption

**No published work measures web-image → personal/consumer-photo transfer for VLM captioning.**
Five distinct search formulations returned nothing. This is the single thing that could invalidate
the plan: the corpus in §4 is Flickr-sourced public photography, and the deployment domain is
private camera rolls. Open Images' 20.3% personal-photo yield and its raw-camera-filename titles
argue the gap is small, but that is an argument, not a measurement.

**It is cheap to de-risk, and the owner is uniquely equipped to do it.** The ~12k banked
descriptions and ~22,769 banked visual judgments are already temp-0 teacher output on exactly the
target distribution. Hold out **300–500 of them, hand-correct the cards, and use that as the
primary eval set** (§7). Do not trust a public-corpus benchmark for this question — DOCCI
(15k, CC BY 4.0, faces blurred) and ImageInWords are good public description benchmarks, but they
do not answer the domain-transfer question.

This also resolves the tension the reframing created: the banked corpus stops being training data
(where it leaks, §8) and becomes the one asset that answers the question no paper does.

---

## 12. Sources

Distillation and data scale — [2608.22070](https://arxiv.org/abs/2608.22070) (2026-08-22, SoulGard-VL-2B) ·
[2606.11289](https://arxiv.org/abs/2606.11289) (2026-06-09, i1) ·
[2603.15118](https://arxiv.org/abs/2603.15118) (2026-03-16, VAREX) ·
[2603.09160](https://arxiv.org/abs/2603.09160) (2026-03-10, RubiCap) ·
[2410.16236](https://arxiv.org/abs/2410.16236) (2024-10-21, LLaVA-KD) ·
[2412.01822](https://arxiv.org/abs/2412.01822) (2024-12-02, VLsI) ·
[2311.12793](https://arxiv.org/abs/2311.12793) (2023-11-21, ShareGPT4V) ·
[2406.08478](https://arxiv.org/abs/2406.08478) (2024-06, Recap-DataComp-1B) ·
[2510.17269](https://arxiv.org/abs/2510.17269) (2025-10-20, FineVision) ·
[2501.00654](https://arxiv.org/abs/2501.00654) (2025-01, ICONS) ·
[2510.03880](https://arxiv.org/abs/2510.03880) (2025-10-04, Q-Selector) ·
[2504.21850](https://arxiv.org/abs/2504.21850) (2025-04-30, COMPACT) ·
[2403.09559](https://arxiv.org/abs/2403.09559) (2024-03, TIVE) ·
[2605.09827](https://arxiv.org/abs/2605.09827) (2026-05, Fashion Florence)

Training config and architecture — [2606.14507](https://arxiv.org/abs/2606.14507) (2026-06, list degeneration) ·
[2508.12512](https://arxiv.org/abs/2508.12512) (2025-08-17, LangVision-LoRA-NAS) ·
[2405.02246](https://arxiv.org/abs/2405.02246) (2024-05-03, Idefics2) ·
[2403.09611](https://arxiv.org/abs/2403.09611) (2024-03-14, MM1) ·
[2301.09506](https://arxiv.org/abs/2301.09506) (2023-01-23, OvarNet) ·
[2607.02559](https://arxiv.org/abs/2607.02559) (2026-07, frozen DINOv2 probe)

Hallucination and anchoring — [2502.01491](https://arxiv.org/abs/2502.01491) (2025-02-03, memorization inheritance in SeqKD) ·
[2608.12746](https://arxiv.org/abs/2608.12746) (2026-08-13, DSCC) ·
[2508.04567](https://arxiv.org/abs/2508.04567) (2025-08-06, Obliviate) ·
[2504.13123](https://arxiv.org/abs/2504.13123) (2025-04-17, low-hallucination captions) ·
[2502.12859](https://arxiv.org/abs/2502.12859) (2025-02-18, PAFT) ·
[2603.13306](https://arxiv.org/abs/2603.13306) (2026-03-03, compact VLM benchmark) ·
[2503.13792](https://arxiv.org/abs/2503.13792) (2025-03-18, multi-image position bias)

Privacy and memorization — [2202.07646](https://arxiv.org/abs/2202.07646) (2022-02-15, quantifying memorization) ·
[2202.06539](https://arxiv.org/abs/2202.06539) (2022-02-14, dedup mitigates privacy risk) ·
[2107.06499](https://arxiv.org/abs/2107.06499) (2021-07-14, dedup) ·
[2310.06714](https://arxiv.org/abs/2310.06714) (2023-10-10, memorization in fine-tuned LMs) ·
[2205.12506](https://arxiv.org/abs/2205.12506) (2022-05-25, memorization by fine-tuning method) ·
[2209.06997](https://arxiv.org/abs/2209.06997) (2022-09-15, M⁴I) ·
[2510.16295](https://arxiv.org/abs/2510.16295) (2025-10-18, OpenLVLM-MIA) ·
[1802.08232](https://arxiv.org/abs/1802.08232) (2018-02-22, The Secret Sharer) ·
[2511.14936](https://arxiv.org/abs/2511.14936) (2025-11-18, DP utility cost) ·
[2103.06191](https://arxiv.org/abs/2103.06191) (2021-03, face-blurred ImageNet)

Evaluation — [2111.15664](https://arxiv.org/abs/2111.15664) (2021-11-30, Donut) ·
[2602.12203](https://arxiv.org/abs/2602.12203) (2026-02-12, ExStrucTiny) ·
[2411.00640](https://arxiv.org/abs/2411.00640) (2024-11-01, Adding Error Bars to Evals) ·
[2501.10868](https://arxiv.org/abs/2501.10868) (2025-01-18, JSONSchemaBench) ·
[2503.05488](https://arxiv.org/abs/2503.05488) (2025-03-26, KIEval)

Corpora and law — [1811.00982](https://arxiv.org/abs/1811.00982) (2018-11-02, Open Images V4) ·
[1503.01817](https://arxiv.org/abs/1503.01817) (2015-03, YFCC100M) ·
[2410.23144](https://arxiv.org/abs/2410.23144) (2024, PD12M) ·
[2310.16825](https://arxiv.org/abs/2310.16825) (2023-10, CommonCanvas) ·
[2511.08637](https://arxiv.org/abs/2511.08637) (2025-11-10, DataComp consent) ·
Getty Images v Stability AI [2025] EWHC 2863 (Ch), 2025-11-04, ¶600 ·
Beaulier v. Meta Platforms, N.D. Cal., MTD granted 2026-08-26 ·
EDPB Opinion 28/2024, adopted 2024-12-18 ·
Commission GPAI Guidelines, 2025-07-18 ·
Creative Commons, "Using CC-licensed Works for AI Training", 2025-05-28

Licence and portability facts marked `[measured]` were verified against the HuggingFace API,
raw LICENSE files, GitHub issue/PR state, and first-hand ONNX Runtime benchmarks on
Apple M5 Max hardware, all on 2026-08-30.

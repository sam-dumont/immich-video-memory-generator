# The motion describer — feasibility and design

Status: QUEUED design. Not tomorrow's job. Written 2026-09-02 against read-only inspection of the
installed toolchain, the judgment bank, and the app's live-photo path. No inference was run.

Goal: a local "what happens in this clip" describer, distilled from the 30B teacher onto
SmolVLM2-500M-Video-Instruct, matching the recipe that shipped the still describer
(0.604 F1 vs verified truth; `scripts/distill/train_local_mlx.py`, mlx-vlm 0.6.17,
completion-only loss, 512px processor, batch 8, seq 384).

---

## 1. Feasibility verdict

**Venue: mlx-vlm 0.6.17, frames-as-images. No library change, no new venue, no wait.**

The fallback is not a fallback. Frames-as-images *is* SmolVLM2's native video representation.

### Evidence: video == frames + timestamp text

`transformers/models/smolvlm/processing_smolvlm.py:186` `replace_video_token()` expands a
`<video>` placeholder into plain text plus per-frame image blocks:

```
DEFAULT_VIDEO_INTRO   = "You are provided the following series of {frame_count} frames from a {video_duration} [H:MM:SS] video.\n"
FRAME_TIMESTAMP_MESSAGE = "\nFrame from {timestamp}:"     # then the normal image prompt string
```

There is no video encoder and no temporal module. A video is N images with a text scaffold. So the
difference between "native video training" and "N frames as N images" is **the scaffold string**,
which is pure data prep.

### Evidence: what mlx-vlm 0.6.17 can and cannot do

| Question | Answer | Source |
|---|---|---|
| Does the trainer dataset accept `videos`? | **No.** `VisionDataset.process()` reads only `images`/`image` and `audio`/`audios`. | `mlx_vlm/trainer/datasets.py:90-96` |
| Does the *inference* path accept videos? | Yes — `prepare_inputs(..., videos=...)` loads via cv2 `load_video()`. The trainer never passes it. | `mlx_vlm/utils.py:1795`, `:1935`, `:2059-2117` |
| Does the smolvlm message formatter emit video parts? | **No.** `smolvlm` is absent from the video-format model list (qwen2_vl…minimax_m3_vl); it routes to `LIST_WITH_IMAGE_FIRST`. | `mlx_vlm/prompt_utils.py:77`, `:292-306` |
| Is smolvlm multi-image capable? | **Yes.** Not in `SINGLE_IMAGE_ONLY_MODELS`; `_format_list_with_image` emits `num_images` image parts. | `prompt_utils.py:121-129`, `:378-405` |
| Does the *model* take multiple images? | **Yes, required 5-D.** `batch_size, num_images, num_channels, height, width = pixel_values.shape` | `mlx_vlm/models/idefics3/idefics3.py:100` (smolvlm subclasses Idefics3) |
| Is the qwen3vl collator bug shared? | **No, twice over.** (a) It is fixed: [#1726](https://github.com/Blaizzy/mlx-vlm/issues/1726) → [PR #1744](https://github.com/Blaizzy/mlx-vlm/pull/1744), merged 2026-07-27, shipped in 0.6.8; we run 0.6.17. (b) It never applied — smolvlm produces no `image_grid_thw`/`video_grid_thw`, the ragged key that path collates. | `trainer/sft_trainer.py:365-368` |
| Is SmolVLM LoRA training itself sound in 0.6.17? | **Yes, but only recently.** [#1830](https://github.com/Blaizzy/mlx-vlm/issues/1830) (three cascading SmolVLM crashes: connector 3D→2D reshape, truncation desyncing image tokens from `pixel_values`, cache under grad checkpointing) → [PR #1852](https://github.com/Blaizzy/mlx-vlm/pull/1852) merged 2026-08-14, plus [PR #1922](https://github.com/Blaizzy/mlx-vlm/pull/1922) "Fix SmolVLM split-image token expansion" 2026-08-16. Both precede 0.6.17. **Pin the version.** | upstream |

**Constraint that follows: N must be identical for every record.** `_collate_arrays`
(`sft_trainer.py:32-49`) tries `mx.stack`; on shape mismatch it falls back to
`mx.concatenate(axis=0)`, which for `(N_i, C, H, W)` yields a 4-D array and then fails the 5-D
unpack at `idefics3.py:100`. Variable N crashes loudly rather than silently — acceptable, but the
dataset builder must pad/trim to a fixed N.

### Two hazards found by reading, to gate against

1. **Silent record drop.** `iterate_batches` skips any record whose image tokens do not fit
   `max_seq_length`, with a `logging.warning` only (`sft_trainer.py:278-303`). This guard exists
   *because* truncation used to silently desync image tokens from `pixel_values` — bug 2 of
   [#1830](https://github.com/Blaizzy/mlx-vlm/issues/1830). It converts corruption into a drop, which
   is better and still invisible. Set N and seq so nothing is dropped, and **assert the
   trained-example count equals the dataset count**. This repo has been bitten by silent absorbers
   before.
2. **All-black frames are treated as padding.** `real_images_mask = (pixel_values == 0.0).sum(...)`
   at `idefics3.py:107` drops all-zero images. Night clips, fades and letterboxed frames are a real
   part of this library — normalise/clamp so no frame is exactly zero, and probe it.

### Alternative venues — evaluated, not chosen

Every venue bottoms out in the same `processor.apply_chat_template(...)` expansion; we do it
explicitly.

- **plain transformers + peft** — the genuine second-best. HF's own
  [`SmolVLM2_Video_FT.ipynb`](https://github.com/huggingface/smollm/blob/main/vision/finetuning/SmolVLM2_Video_FT.ipynb)
  (linked from the [SmolVLM2 blog](https://huggingface.co/blog/smolvlm2)) is a `transformers.Trainer`
  plus a 15-line collator, and is the reference implementation for this exact task on this exact
  checkpoint. Cost: a second toolchain and a CUDA box, to reach a wire format mlx-vlm already
  accepts. Note it defaults `USE_LORA = False` — HF advise full FT for the 500M; remember that if
  LoRA underfits.
- **axolotl** (`scripts/distill/axolotl_smolvlm2_lora.yaml` exists). SmolVLM2 is first-class
  supported with a shipped LoRA example, but the
  [multimodal docs](https://docs.axolotl.ai/docs/multimodal.html) are titled **BETA** and the video
  section warns verbatim *"This is not well tested at the moment."* Zero SmolVLM2 video test
  coverage. Plus a concrete default-config failure: axolotl sets `chat_template` from the tokenizer,
  but `SmolVLMProcessor.apply_chat_template` only swaps in the video-capable template when
  `chat_template is None`, and neither SmolVLM2 checkpoint ships a template with a `video` branch —
  so `<video>` never appears and `validate_inputs` raises a count mismatch. We would be the testers.
  Rejected.
- **Waiting for mlx-vlm video training** — pointless, and nobody is building it: no open issue or PR
  requests it.

---

## 2. Frame-sampling spec

### Token budget — measured, not estimated

SmolVLM2-500M: `image_seq_len = 64` (`processor_config.json`; independently `((512//16)**2)/(4**2) = 64`
from `vision_config.image_size=512, patch_size=16, scale_factor=4`), 512px longest edge with
`do_image_splitting=True` and tile 512 → one global image, no split. Measured cost:
**67 input tokens per frame** (64 image + 3 specials). Chat-template overhead 11 tokens.

This matches the model's native video path exactly: `replace_video_token` builds each frame with
`_prompt_single_image`, i.e. **no splitting**, one global tile at 512px — the checkpoint's
`video_sampling` block declares `{"fps": 1, "max_frames": 64, "video_size": {"longest_edge": 512}}`.
Our 512/512 processor cap reproduces the video-mode geometry per frame. Adding the timestamp
scaffold (§8 risk 3) costs ~6 more tokens per frame, i.e. **~73/frame**; the tables below are
without it, so subtract one frame from each row if the ablation says to keep it.

Measured full-record lengths (prompt + N frames + target + template), via
`mlx_vlm.prompt_utils.apply_chat_template` and the real processor:

| frames | verbose prompt (168 tok) + long target (71 tok) | lean prompt (59 tok) + short target (54 tok) |
|---:|---:|---:|
| 1 | 316 | 190 |
| 2 | 383 | 257 |
| 3 | 450 | **324** |
| 4 | 517 | 391 |
| 5 | 584 | 458 |
| 6 | 651 | 525 |
| 8 | 785 | 659 |
| 12 | 1053 | 927 |

Max frames that fit:

| max_seq_length | lean prompt | verbose (current teacher prompt) |
|---:|---:|---:|
| **384** (shipped) | **3** | 2 |
| 512 | 5 | 3 |
| 768 | 8 | 6 |
| 1024 | 12 | 10 |

Two consequences. First, **3 frames at seq 384 is free** — it is the shipped config untouched, and
it happens to equal the app's existing `FILMSTRIP_FRAME_COUNT = 3`
(`src/immich_memories/analysis/visual_atlas.py:16`). Second, the prompt must be lean: the current
teacher motion prompt costs 168 tokens and alone drops the budget from 3 frames to 2. Trimming the
prompt buys a whole frame — consistent with the project's less-direction-for-small-models doctrine.

Raising resolution is the expensive lever, not raising frame count: at size=1024/tile=512 a single
image costs 368–501 tokens instead of 67. Keep 512px.

### Three-way input taxonomy

Measured durations from the app's own cache (`~/.immich-memories/cache.db`, `video_analysis`,
n=3,445 analysed assets): median **4.0s**; 2–4s 1,025; 4–8s 1,726; 8–15s 214; 15–30s 262; 30–60s
145; 60s+ 73. **80% are under 8 seconds.** The tail is thin but real (max 2,363s).

| Class | Population | Duration | Frames | Sampling rule | Seq |
|---|---|---|---|---|---|
| **A. Single live component** | 21,523 components | 3.0s (1.5s for Pixel, `live_photo_merger.py:33,46`) | **3** | `even_timestamps(D, 3)` = D/4, D/2, 3D/4 — avoids first/last frame | 384 |
| **B. Stitch (premerged burst)** | app's primary motion artifact | ~4–15s, k=2–5 segments, no cap | **2 per segment, cap 8** | **segment-boundary-aware**: within each segment span sample at 1/3 and 2/3; never sample across a cut | 768 |
| **C. Regular video** | ~3.4k analysed | median 4.0s, p80 <8s | **3** short (<8s), **6** medium (8–30s), **8** long (>30s) | uniform for <8s; scene-bounded via existing `SceneDetector` for >30s | 384 / 768 |

**Why class B cannot use uniform fps.** A stitch is a concatenation of independently trimmed
components (`build_merge_command` → `trim` + `concat`, `live_photo_merger.py:552,592`). Uniform
sampling over the merged timeline will land two frames inside one long segment and none inside a
short one, and the describer then narrates one continuous shot. Per-segment sampling makes the
sequence legible as a sequence ("the child jumps, then everyone laughs") rather than as a single
take with an inexplicable jump cut.

**Blocker for class B — segment boundaries are not persisted.** This is the finding that sequences
the slices:

- `MotionRendering.trim_points` / `shutter_timestamps` (`analysis/motion_rendering.py`) and
  `VideoClipInfo.live_burst_trim_points` (`api/models.py:395-398`) carry the *pre-alignment* trims.
- `_try_merge_burst` overwrites them with spectrogram-derived trims
  (`generate_downloads.py:291`, `valid_trims = video_trims`) and **never writes them back**.
- The merged file `{output_dir}/.live_merges/{still_id}_merged.mp4` gets no sidecar, no chapter
  markers, no metadata — and `_cleanup_temp_dirs` rmtree's the whole directory at end of run
  (`generate_clips.py:192`).
- The editorial layer keeps only a **sha256** of the manifest
  (`live_photo_rendering_family_id`, `editorial_contracts.py:100`). A hash is not invertible.

So boundaries in merged-file time are recoverable only by cumulative-summing trims that may be
stale. **The fix is one sidecar write at the single choke point where the true trims exist:**
`_try_merge_burst` (`generate_downloads.py:265-294`). Small, but it is app work, and it must land
before class B is trainable.

### Tooling — all of it already exists

`processing/frame_sampling.py` has `even_timestamps(duration, count)`, `sample_frames(...)` and
`sample_segment_frames(video, *, start_time, end_time, count, width, cache_dir, render_version)` —
content-addressed on `resolve():size:mtime_ns:start:end:count:width:version`, and exactly the
per-segment primitive class B needs. `visual_atlas._filmstrip_for()` already calls it at
`count=3, width=360`. `analysis/scenes.py::SceneDetector.detect()` covers class C's long tail, and
`analysis/contact_sheets.py` (`tile_sheet` / `build_contact_sheets`) is the `sheet_hashes` producer
reused for the truth strips in §5.

Nothing new needs building for sampling. The dataset record is the shipped shape with a longer list:

```python
{"messages": [{"role": "user", "content": [{"type":"image","image":p} for p in frames]
                                          + [{"type":"text","text": prompt}]},
              {"role": "assistant", "content": [{"type":"text","text": target}]}],
 "images": [p1, p2, p3]}                      # N constant across the whole dataset
```

`VisionDataset.process()` reads `images` directly (`datasets.py:90`), and `assemble_blend.py:139-161`
already emits this shape with a one-element list.

---

## 3. Teacher labeling plan

### The prompt exists and is recoverable

`src/immich_memories/analysis/selection_descriptions.py:104-112` holds `_MOTION_PROMPT` verbatim
(pass version `asset-motion-description-v2`, render version
`visual-atlas-v1/contact-sheet-v1/asset-motion-400px`). Note what it reveals: **the teacher already
consumes motion as a single 3-frame contact-sheet filmstrip**, not as video — "this one numbered
chronological filmstrip".

That is a gift and a trap. Gift: the labeling harness is built. Trap: the student will be fed N
separate frames, not one strip. Feed the teacher the **same N separate frames** the student will
see, or the student is being taught to imitate answers derived from a different stimulus.

### Schema for the student target

The 168-token teacher prompt is for a 30B. The student target should be leaner (see §2):

```json
{"schema_version":"asset-motion-description-v2",
 "description":"what happens across the frames",
 "setting":"where this is, or insufficient evidence",
 "motion_contribution":"meaningful or still_sufficient"}
```

Drop `motion_reason` from the *student* target: it costs 17 tokens of budget and the project has
measured that a discarded reasoning field costs quality — so keep it in the **teacher** call (where
writing the reason is the thinking) and strip it when building the student record. Rules, expressed
as structure rather than instruction: one line, no double quotes or backslashes, motion verbs only
for change visible *between* frames, `insufficient evidence` rather than a guess.

### Volume and cost

The still describer needed ~3k. Assume the same.

| Item | Number |
|---|---|
| Target labeled clips | 3,000 |
| Teacher calls, unpacked, at 2.5s | 7,500s = **2.1 h** |
| Teacher calls, packed 4 (validated for descriptions only, `DESCRIPTION_PACK_SIZE = 4`) | 750 calls, ~7s each = **1.5 h** |
| Frame extraction, 3,000 × 3 frames × ~0.1s ffmpeg seek | 900s = **15 min**, cached thereafter |
| Verified-truth set (§4) | 200 clips |

Packing saves little here and adds a cross-contamination risk the project has already measured on
pair verdicts. **Run unpacked.**

---

## 4. What the bank already holds

`~/.immich-memories-matrix/pairhead-2026-08-30/judgments-blanked-selects.db`, table
`visual_judgments` (15,555 rows total).

| pass_version | rows | model_identity |
|---|---:|---|
| `asset-motion-description-v1` | 1,744 | `scottlowry/Qwen3.8-27B-oQ4e-mtp` |
| `asset-motion-description-packed-v2` | 624 | `scottlowry/Qwen3.8-27B-oQ4e-mtp` |
| `asset-motion-description-v2` | 51 | `scottlowry/Qwen3.8-27B-oQ4e-mtp` |
| **total motion** | **2,419** | **100% 27B — zero 30B rows** |

Class balance (v1): `still_sufficient` 1,262 / `meaningful` 482 — **72/28**. A student trained on
this as-is will learn to say "nothing happens", which is precisely the failure mode a motion
describer must not have. Rebalance or stratify.

Schema drift: 1,744 of 1,795 non-packed rows have **no `setting` key** despite the v1 shape
declaring one — the same prose-schema-vs-strict-parser drift the project has hit before. Mean
description length 268 chars (min 108, max 617), i.e. well past the 240-char still-describer bar.

**Verdict: regenerate under the 30B. Do not seed training targets from the bank.** Three reasons,
in order: (a) lineage — every row is 27B-era and the project treats teacher lineage strictly;
(b) stimulus mismatch — these are answers to a *single composited filmstrip*, not to N frames;
(c) the schema is drifted and the classes are skewed.

**But keep the 2,419 rows** for two free jobs that need no regeneration:
1. **Prompt regression** — diff a new 30B answer against the 27B answer on the same clip to see what
   the teacher upgrade actually changed.
2. **Sampling frame for the truth set** — the `meaningful` 482 are a ready-made pool of clips where
   something demonstrably happens, worth oversampling into the verified-truth set.

---

## 5. Truth protocol

Mirrors the still describer: teacher labels are training signal, **not** truth. Truth is
hand-verified.

- **Size: 200 clips** (within the 150–300 band). Stratified: 70 class A, 70 class B, 60 class C;
  within each, 50/50 `meaningful` / `still_sufficient` by teacher label so the gate cannot be won by
  a constant answer.
- **Artifact for the judge**: a frame-strip contact sheet per clip — the exact N frames the model
  saw, in order, numbered, via `contact_sheets.tile_sheet` at `tile_px=400`. Viewable as an image,
  so an Opus judge sees the same stimulus the student did.
- **Protocol**: Opus judges field-by-field against the strip, then a human spot audit. Field
  agreement scored with the same `token_f1 >= 0.5` arithmetic `eval_gates.py:107,119` already uses,
  so the number means what it means for the still describer.
- **The set is versioned with the prompt.** Banked thresholds expire on a prompt bump; a rebuilt
  prompt needs a rescored truth set, not a reused number.

---

## 6. Eval gates

Gates 1–4 are the shipped four (`scripts/distill/eval_gates.py:350-388`), unchanged:

1. **Field micro-F1 vs teacher ceiling** — `micro_f1 / TEACHER_SELF_AGREEMENT (0.95) >= --min-f1`.
2. **Phantom-fill FP/predicted ≤ teacher** — vacuous without `--teacher-rate` scored on the *same*
   hand-corrected holdout. Report unmeasured rather than inventing a bar.
3. **Duplicate rate on list fields == 0.**
4. **Canary exposure (1x, 5x) < 10.0.**

Motion-specific additions:

5. **Static-verb assertion rate.** The teacher's known weakness is posture-verbs-from-stills. Build
   a **fixed-point control**: N copies of *one* frame presented as an N-frame clip. The only correct
   answer contains no motion verb and `motion_contribution = still_sufficient`. Measure the rate at
   which the student asserts a motion verb anyway. Target: **≤ teacher's rate on the same control**,
   and report both. This control is free to build and cannot be gamed — the fixed point is known.
6. **Motion-contribution balance.** Predicted `meaningful` rate must fall within ±10pp of the truth
   set's rate. Catches the 72/28 collapse-to-majority failure directly.
7. **Order sensitivity.** Re-run the holdout with frames **reversed**. A model that reads temporal
   order must change its answer on clips labeled `meaningful`; one that ignores order will not.
   Target: answer changes on ≥50% of `meaningful` clips, and on <10% of `still_sufficient` ones. If
   the reversed and forward answers agree everywhere, the model is captioning a collage, not
   describing motion — and every other gate can still be green while that is true.
8. **Parroting/place probes** — as shipped.

Gate 7 is the one that decides whether this project is worth doing at all. Run it first.

---

## 7. Smallest first slice

**Class A only: single live-photo components, 3 frames, seq 384, batch 8.**

Why class A and not the stitch, despite stitches being the app's primary motion artifact:

1. **Zero config change.** 3 frames at seq 384 lands at 324 tokens — inside the shipped, validated
   recipe. Class B needs seq 768, which is an unvalidated memory/throughput regime.
2. **Class B is blocked on app work.** Segment boundaries are not persisted (§2). Building the
   sidecar in `_try_merge_burst` is correct and small, but it is a separate PR against `src/`, and
   this slice must not depend on it.
3. **Volume.** 21,523 components, no merge step, no alignment, no cleanup race.
4. **Free prompt regression.** The bank's 2,419 rows are 3-frame filmstrips of exactly this class.
5. **Gate 7 is answerable on class A alone.** A 3s live photo either shows change between its three
   frames or does not. If the model cannot tell forward from reversed there, no amount of stitch
   sophistication rescues it.

Class B is slice 2 and inherits everything except the sampler and the seq length.

### Sequence and machine time

Sequenced **after** tonight's label run and tomorrow's pipeline work. Nothing here touches ports
9999/8091 or the GPU until step 3.

| # | Step | Machine time | Blocking? |
|---|---|---|---|
| 0 | Gate 7 dry-run on the *teacher*: 60 clips forward + reversed. If the 30B is order-blind on its own filmstrips, stop. | 60 × 2 × 2.5s = **5 min** | yes — kill switch |
| 1 | Sample 3 frames × 3,000 class-A clips via `sample_segment_frames` | **15 min**, cached | no |
| 2 | Build the 200-clip truth set: strips + Opus judging + human spot audit | ~30 min machine, human-bound | parallel |
| 3 | Teacher-label 3,000 clips on the 30B, unpacked, N separate frames | **2.1 h** | needs the box |
| 4 | Assemble dataset (extend `assemble_blend.py` to an N-element `images` list); assert trained-example count == dataset count | minutes | no |
| 5 | LoRA train. Reference: the shipped still run was **891s (14.9 min)** — 1,000 iters, 3 epochs, batch 8, seq 384, rank 8, grad checkpointing, 60 GB cap. 3 frames tripled the image tokens, so budget **35–50 min**. | **~45 min** | GPU |
| 6 | Predict holdout + run gates 1–8 | **20 min** | GPU |

**Total machine time ≈ 3.5 h**, dominated by teacher labeling. Everything before step 3 is free and
can be done while the box is busy.

---

## 8. Open questions and risks, ranked

| # | Risk | Cheapest resolving experiment |
|---|---|---|
| 1 | **The 500M cannot represent temporal order at all** — it may pool frames into a bag of images. Every other risk is moot if so. | Gate 7 on the *base* checkpoint, zero-shot, 60 clips forward vs reversed. ~10 min, no training. **Do this first.** |
| 2 | **Teacher is order-blind too** — it labeled from a composited strip, and its 72/28 skew toward `still_sufficient` may be the tell. | Step 0 above: 60 clips forward + reversed on the 30B. 5 min. |
| 3 | **The missing timestamp scaffold matters.** SmolVLM2's video path emits `"You are provided the following series of {N} frames from a {H:MM:SS} video.\n"` then `"\nFrame from MM:SS:"` before each frame (`processing_smolvlm.py:26-32,186-209`). Omitting it puts the record off-distribution; including it costs ~6 tokens/frame. | Ablation at dataset-build time: two variants, train both, compare on the same holdout. +45 min. Cheap because training is 45 min. mlx-vlm's smolvlm processor has no `replace_video_token`, so the scaffold must be written as literal text either way. |
| 4 | **Class imbalance collapses the student to "nothing happens".** 72/28 in the bank. | Stratify the 3,000 to 50/50 at sampling time using teacher labels. Free. |
| 5 | **All-black frames silently dropped** as padding (`idefics3.py:107`). Night and fade clips are real here. | Probe: one all-black frame in a 3-frame record, check `pixel_values` survives. 5 min, CPU. |
| 6 | **Silent record drops** past seq 384 (`sft_trainer.py:278-303`). | Assert trained-example count == dataset count in the run manifest. Free. |
| 7 | **Class B boundaries unrecoverable** — blocks slice 2, not slice 1. | Sidecar write in `_try_merge_burst` (`generate_downloads.py:265-294`), one choke point. Separate PR. |
| 8 | **Regular-video long tail** (73 assets >60s, max 2,363s) has no sane fixed N. | Exclude >60s from slice 1 and 2. Revisit with `SceneDetector` later. |

**Top open question:** *does the 500M base checkpoint distinguish a clip from its reverse?* It is
answerable in ten minutes with no training and no labeling, and it is the difference between this
being a distillation project and being an expensive way to caption the middle frame. Nothing else in
this document should be started before it is answered.

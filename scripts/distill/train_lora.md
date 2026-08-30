# Stage E — train the student

Two venues. **Path (a) local MLX exists and works** — that is a change from what the research
note assumed, and it is the cheaper path. Path (b) is the fallback when a bug in path (a) bites.

All facts below verified 2026-08-30 against source, the GitHub API and PyPI. Anything not
verifiable that day is marked `TODO-verify`.

---

## The recipe, before either venue

From docs/research §3.2 and §3.3, and these are the numbers to type:

| Knob | Value | Why |
|---|---|---|
| Student | `Qwen3-VL-2B-Instruct` (Apache-2.0) | §6 |
| LoRA rank | **8**, not 16 | §3.2 — rank 16 on dense repeated-list fields produced duplicate rate 0.080, max repeat run 23; SoulGard shipped r=8 on this task shape |
| LoRA alpha | 16 | §3.2 |
| Learning rate | 2e-4 | §6 |
| Epochs | **≤ 3** | §8 — memorisation is flat at 14.1% (epoch 1) to 14.3% (epoch 3), then knees to 22.3% at epoch 5 |
| Vision tower | **frozen** | §3.3 |
| LoRA reach | the **language model**, not the projector alone | §3.3 — freezing both backbones costs 9.2 points, and projector-only training is structurally head-like, the highest-leaking configuration measured (exposure 10.78, MIA recall 81.6%) |

---

## Path (a) — local, MLX, free

### Status: shipping

- **`mlx-vlm` 0.6.17**, uploaded 2026-08-26 (verified: `https://pypi.org/pypi/mlx-vlm/json`,
  `info.version`).
- The trainer is **`python -m mlx_vlm.lora`** — `mlx_vlm/lora.py` has a `__main__` guard. There is
  **no console script** for it; `[project.scripts]` covers only chat/convert/generate/server.
- **Qwen3-VL is trainable.** `mlx_vlm/models/qwen3_vl/` exists, and the denylist in
  `mlx_vlm/trainer/utils.py` is `{"gemma3n", "qwen3_omni"}` — Qwen3-VL is not in it.
- Its defaults are already the §3.2 recipe: `--lora-rank 8`, `--lora-alpha 16`.
- **LoRA is applied to the language model only** (`find_all_linear_names(model.language_model)`),
  and `--train-vision` is left off. That is exactly §3.3, with no configuration needed.

### 🔴 Correction to the research note

docs/research §6 says the image-dropping bug was fixed by **PR #823, merged 2026-03-15**.
**PR #823 was never merged** — it was closed 10 minutes after opening, unmerged
(`"merged": false, "merged_at": null`), with the maintainer asking for a repro issue instead.

The fix that actually landed is **PR #826, "Fix preprocessing for image input for trainer",
merged 2026-04-21**. The offending ternary is gone from `mlx_vlm/trainer/datasets.py` on main.
Cite #826, not #823. The verification step below is unchanged and still mandatory.

### Open training bugs as of 2026-08-30

| Issue | Impact on this run |
|---|---|
| **#1726** — multi-image records crash the Qwen3-VL collator (`image_grid_thw` collated to `(1,N,3)` instead of `(N,3)`), confirmed on current main | **Avoided by construction.** `assemble_blend.py` emits exactly one image per record, and a test pins it. |
| #824 — "LoRA training broken for Qwen3.5 VLM", still open | Reported against mlx-vlm 0.4.0; its "Bug 1" code path no longer exists on main and the requested retest was never posted. Stale-but-open. `TODO-verify` on 0.6.17 if you see corrupted generation. |

### Commands

```bash
uv pip install "mlx-vlm[train]"

DATA=~/.immich-memories-distill/validation/dataset

python -m mlx_vlm.lora \
  --model-path mlx-community/Qwen3-VL-2B-Instruct-bf16 \
  --dataset "$DATA" \
  --split train \
  --lora-rank 8 \
  --lora-alpha 16 \
  --learning-rate 2e-4 \
  --epochs 3 \
  --batch-size 4 \
  --max-seq-length 2048 \
  --steps-per-report 10 \
  --steps-per-eval 200 \
  --steps-per-save 100 \
  --output-path "$DATA/../adapters/adapters.safetensors"
```

`--epochs` overrides `--iters`. Do not pass `--train-vision` (it fully unfreezes the vision stack,
which is the opposite of §3.3). Do not pass `--full-finetune`.

Resume an interrupted run with `--adapter-path <the saved adapters dir>`.

### 🔴 The verification that makes or breaks the run

The failure mode this guards against is silent: the run completes, the loss curve looks plausible,
and the model learned nothing visual. The measured signature was *23 trained tokens/iter with loss
stuck at 0.43*; after the fix, *1,076 tokens/iter and loss 22.1 → 3.2*.

**After the first `--steps-per-report` line, check trained tokens/iteration ≈ 440 × batch size.**
At `--batch-size 4` that is **≈ 1,760 tokens/iter**. A three-digit number means images are being
dropped — stop, do not spend the night, and switch to path (b).

(440 tokens/sample is §6's estimate: a 400px tile is only 144–169 visual tokens on Qwen3-VL —
`patch_size 16`, `spatial_merge_size 2`, one token per 32×32 px.)

### Two doc/source drifts worth knowing

`LORA.MD` (uppercase `.MD`; `LORA.md` is a 404) says `--val-batches` defaults to 25; the source
says **4**. And `--train-mode {sft,orpo}`, `--beta`, `--eps` exist in source but appear nowhere in
the docs — relevant if you get to the DPO round §5 says to budget for.

### Dataset format

`load_dataset(args.dataset, split=args.split)`, so a local directory works. It wants an `images`
column and a `messages` column, and `assemble_blend.py --format messages` emits exactly the shape
`LORA.MD` documents for Qwen:

```json
{"images": ["/abs/path.jpg"],
 "messages": [{"role": "user", "content": [{"type": "image", "image": "/abs/path.jpg"},
                                            {"type": "text", "text": "<the production request>"}]},
              {"role": "assistant", "content": [{"type": "text", "text": "<the JSON target>"}]}]}
```

---

## Path (b) — rented 4090

§6 costs it at **$0.87 for 2B / 12k / 3 epochs** on a RunPod Community 4090 at $0.34/hr, ~$1.80 at
25k. Costs converge across GPU tiers because $/FLOP is roughly constant — pick on convenience.

### b1. axolotl (config written for you: `axolotl_qwen3vl_lora.yaml`)

Qwen3-VL is documented in `docs/multimodal.qmd`, under a **"MultiModal (BETA)"** header that warns
support "doesn't have full feature parity". There is **no** `examples/qwen3-vl/` directory —
the config here is the documented Qwen3-VL snippet merged onto `examples/qwen2_5-vl/lora-7b.yaml`.

The vision tower is frozen two ways at once, belt and braces: the `lora_target_modules` regex is
scoped to `model.language_model.layers`, and `freeze_mm_modules: true` is a real schema field
(`src/axolotl/utils/schemas/config.py`).

```bash
# on the pod
pip install "axolotl[flash-attn]"
# upload the dataset dir and the config
rsync -av ~/.immich-memories-distill/validation/dataset/ pod:/workspace/data/
scp scripts/distill/axolotl_qwen3vl_lora.yaml pod:/workspace/
# train
axolotl train /workspace/axolotl_qwen3vl_lora.yaml
# bring the adapter home
rsync -av pod:/workspace/out/ ~/.immich-memories-distill/adapters/
```

### b2. LLaMA-Factory (the better-trodden path if axolotl's BETA bites)

Qwen3-VL is in the supported-models table with template **`qwen3_vl`** (and a `qwen3_vl_nothink`
variant for instruct checkpoints), and it ships `examples/train_lora/qwen3vl_lora_sft.yaml` at
`lora_rank: 8` already. Register the dataset in `data/dataset_info.json`, then:

```bash
llamafactory-cli train examples/train_lora/qwen3vl_lora_sft.yaml \
  model_name_or_path=Qwen/Qwen3-VL-2B-Instruct \
  template=qwen3_vl_nothink \
  lora_rank=8 lora_alpha=16 learning_rate=2e-4 num_train_epochs=3
```

### b3. Unsloth

Supports Qwen3-VL — verified from `unsloth/models/vision.py` (which lists `qwen3_vl` /
`qwen3_vl_moe`), a shipped default config, and `Qwen3_VL_(8B)-Vision.ipynb`. The README never
names it. Note its shipped default is `finetune_vision_layers: true`; **set it to `False`** for
§3.3.

### 🔴 If you use HF TRL's `SFTTrainer` directly

`max_length` defaults to **1024** and the docs warn that *"truncating may remove image tokens."*
Set `max_length=None`.

---

## After training

1. Merge or keep the adapter, then generate predictions over
   `dataset/validation.jsonl` into a JSONL with `image_id` + the model's JSON.
2. Run stage D:

```bash
uv run --with pyarrow scripts/distill/eval_gates.py \
  --holdout   ~/.immich-memories-distill/validation/dataset/validation.jsonl \
  --predictions student_predictions.jsonl \
  --canaries  ~/.immich-memories-distill/validation/labels.parquet
```

Expect to fail the phantom-fill gate on the first pass and need a DPO round (§5). Budget for it.

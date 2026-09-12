---
date: 2026-08-31
status: design — not implemented, no code in src/ yet
issue: none-yet
builds-on: 2026-08-30-fast-lane-licensing.md, 2026-08-30-bulk-visual-analysis-alternatives.md, 2026-08-30-card-model-distillation.md, implementation-plans/2026-08-27-visual-analysis-inventory.md, designs/2026-08-31-the-per-asset-index.md
replaces: the "train a bigger VLM student" direction (owner ruling, 2026-08-31 evening)
---

# The triage engine — frozen encoder, task heads, teacher conclusions

> Nominate on every asset. Cut on none.

## 0. The decision, restated

A frozen permissive vision encoder plus small task-specific heads, trained on the
27B teacher's **forced-choice conclusions** rather than on its prose. It runs over
every asset and emits per-asset facts. It **nominates assets for attention; it
never cuts.**

Four measurements decided it:

- **Prose is the lossy channel.** The 27B's own descriptions preserve activity
  classification at 90% when a text judge reads the conclusion back out; a
  distilled 0.5B VLM at 75%. Location and people survive at 88–97%.
- **The base model is not the value.** An un-finetuned 500M VLM scores 0/400. The
  0.5B teacher-only adapter measured 0.632 holdout / 0.493 on-library.
- **A foreign taxonomy does not transfer.** Places365 ResNet18 was built and
  measured on this material: 60% agreement even at high confidence. Already on
  the do-not-re-propose list.
- **The field-shape rule** — *"the boundary between the head family and the
  generative family is field shape, not task name."* Closed-vocabulary
  single-label fields on a frozen encoder measure **+12 to +27 points over
  prompting the same encoder**, reach parity at **5–20 examples per class**, and
  a frozen DINOv2 probe *"generalised as well as fully fine-tuned baselines four
  orders of magnitude larger."* Open-vocabulary multi-label fields fail on the
  tail (frozen CLIP 47.53 mAP vs 69.03 fine-tuned on VAW; OVAD tail 9.5 AP vs
  58.6 head).

Every head in §2 is closed-vocabulary and single-label. That is the entry
condition, not a coincidence.

---

## 1. Encoder

**DINOv2 ViT-S/14 — `facebook/dinov2-small`, pinned by 40-char SHA, 224×224,
exported with `optimum-cli export onnx`.** The export artifact's sha256 is what
the cache keys on, not the upstream repo.

| | value |
|---|---|
| licence | Apache-2.0 — **pin ≥ 2023-08-31** (CC-BY-NC-4.0 before commit `81b2b64`) |
| params / weights | ~21 M, **86.6 MB** fp32 |
| input | 224×224, resize-short-256 + center-crop-224, ImageNet mean/std |
| tokens out | 1 CLS + 256 patch (14 px patches, 16×16 grid), **384-d** |
| compute | ~6 GFLOPs/image (ViT-B/14 ~23) |
| export | **first-party `OnnxConfig` in `optimum-onnx`** — no custom config needed |

**1. CoreML compiles it; it does not compile SigLIP.** Measured, M5 Max, ORT
1.29.0, batch 1, median of 15 runs:

| model | px | CPU EP | CoreML EP |
|---|---|---|---|
| DINOv2-base fp32 | 392 | 107–112 ms | **15.2–15.8 ms** |
| DINOv2-base fp32 | 224 | 136 ms | **6.0 ms** |
| SigLIP2-base-p16-384 fp32 | 384 | 82.8 ms | **compile fail** |
| SigLIP2-so400m-p16-384 fp32 | 384 | 326 ms | **compile fail** |

Every SigLIP-family graph fails with *"has unbounded dimension which is not
supported"* — CoreML wants a constant `B` in MatMul/Gemm, which attention's Q·Kᵀ
violates *but a frozen head satisfies*. One portable stack means one graph that
runs on every provider we target; SigLIP2 has not got one today.

**2. It wins on instance discrimination**, which is the job. Pairwise control
set: DINOv2 ViT-B **0.985 AUC**, ViT-S **0.976**, SigLIP-2 base/16-224 **0.968**
(`residual-instruments-report.md`). The bulk-vision survey concurs — *"best ONNX
story; behind DINOv2 on instance retrieval."*

**3. Zero-shot is a capability we are banned from using.** SigLIP2's advantage is
text alignment; we have labels and a standing ban on retrieval-as-editorial-basis.

**4. The owner already named it.** The bulk-vision addendum resolves the
feature-print job to *"the banked DINOv2/SigLIP embeddings themselves"* and
aesthetics to *"a LAION-aesthetics-class linear head on the embeddings this
pipeline already banks."* This design is that sentence, built.

**Why ViT-S and not ViT-B.** On M-series they cost the same, measured: **19.16
ms/image for ViT-S/14 vs 18.23 for ViT-B/14** on identical assets and loop
(`embed_meta.json` / `embed_meta_vitb14.json`) — a 4× FLOP increase cost nothing,
so the clock was on JPEG decode. It is not free where the forward pass *is* the
clock: DINOv2-base on ORT CPU at 224 px is **136 ms/image on an 18-core M5 Max**.
ViT-S is roughly a quarter of that; ViT-B is not a candidate on a NAS at any
price. Nine thousandths of AUC does not buy the CPU tier back.

**Preprocessing.** `preprocess_version = "dinov2-s14-224-crop-v1"` — resize short
side 256 (bicubic), center-crop 224, `/255`, ImageNet mean/std, CHW float32.
Byte-for-byte `probe_pairhead_embed.py:load_and_transform`, so the 6,841
embeddings already on disk stay a valid starting corpus. Input is the Immich
preview JPEG (~400 px), never the original; `Image.draft()` halves decode again.

One measured oddity carried forward rather than explained away: DINOv2-base fp32
on **CPU** is slower at 224 px (136 ms) than at 392 px (107–112 ms), while CoreML
behaves normally. Slice 0 re-measures it on ViT-S before 224 is locked in.

---

## 2. Heads

**One shared frozen trunk. Five independent heads. No multi-task head.** With a
frozen trunk there is no gradient sharing to exploit, so multi-task buys nothing
and costs the thing that matters: independent versioning. A retrain of `activity`
must not orphan `location`'s cached outputs.

### Input: pooled token pack, then PCA

CLS alone is not enough — `people` needs spatial structure.

```
pack = [ CLS | mean(256 patches) | mean(Q1) | mean(Q2) | mean(Q3) | mean(Q4) ]
     = 6 × 384 = 2,304 dims   →   PCA-256   →   stored fp16
```

Quadrants are the 8×8 patch quarters of the 16×16 grid. Storage is why this is
the design and not an optimization: the full 257-token pack is **197 KB/asset**
(19.7 GB at 100k) against **4.6 KB** for the pooled pack — and 512 B after PCA.

PCA is not cosmetic. The bulk-vision arithmetic: raw 384-d embeddings gave 1,154
pair features on 13,600 samples (~12 samples/feature) and "will overfit"; PCA-128
fixed it at ~35. Here it is worse — 2,304 features against ~11k labels is **4.8
samples/feature**. PCA-256 brings it to ~43. The PCA matrix is part of
`layout_version`, not a per-head object.

Consequence, stated plainly: **a head that consumes only the pack can be retrained
from cache; a head that needs full patch tokens forces a re-embed.** v1 heads are
pack-only for exactly that reason. Attention pooling over full tokens stays as a
slice-1 escalation if `people` misses its gate — but the layout must change
*before* anything is cached at scale.

`layout_version = "cls+mean+quad2x2/pca256/fp16"`.

### Architecture per head

1. **Linear probe** — multinomial logistic regression on the 256-d pack, L2, `C`
   by 5-fold CV. The in-tree proven shape: the pairwise head is logistic
   regression over 128-d PCA'd DINOv2 features at 0.906 accuracy / 0.958 AUC
   against a 0.876 raw-cosine and 0.589 trivial baseline. The *Metric Learning
   Reality Check* verdict applies — a decade of exotic losses gave "marginal at
   best" gains; *"the win comes from having a trained decision rule at all."*
2. **Shallow MLP** — `LayerNorm(256) → Linear(256→256) → GELU → Dropout(0.2) →
   Linear(256→K)`, ~70 k params/head, exports as `Gemm`+`Relu`.

**Rule: ship the MLP only if it beats the linear probe by ≥2 points of
accuracy-on-covered on the eval truth.** Otherwise ship the probe.

Cheap non-visual side features get one ablation each: adding 5 time-delta buckets
+ 2 hash-hamming features moved the pairwise head 0.906 → 0.919. The per-asset
analogue is hour-of-day and EXIF flash/exposure — free from Immich, never
anything the owner can edit.

### "Undetermined" — explicit class **and** calibrated band

Both, ORed, because they are different failure modes and only one is learnable.
The teacher's `undetermined` is *semantic* — the picture genuinely does not show
whether there are children — which is a real fact a head can learn and a
threshold cannot express. The head being unsure is a separate thing, and an
argmax with no floor is the softmax accident.

```
p = softmax(logits)                  # K decidable classes + "undetermined"
label = argmax over decidable classes
emit label   iff  p[label] ≥ τ_conf  AND  p[undetermined] < τ_und
else emit "undetermined", covered = 0
```

Calibration: temperature scaling on ~500 held-out calibration items for K > 2;
isotonic for binary `children` (`CalibratedClassifierCV(FrozenEstimator(base),
method="isotonic")` is already in `probe_pairhead_cascade.py`). Thresholds are
**asymmetric per class** where measurement says so: on the pairwise head a
symmetric threshold gave 54.8% coverage at 98% agreement, while `t_same=0.945 /
t_diff=0.10` gave 58.5% at 97.6% with the error direction moved where it did
least harm. Every head ships a coverage/accuracy curve (`cascade_curve()`).

### The v1 head set

| head | classes | prose number to beat | tail risk |
|---|---|---|---|
| `location` | indoor, outdoor, undetermined | 97% | none |
| `venue` | home, nature, urban, event-venue, sports, water, undetermined | — | low |
| `people` | none, one, two, small-group, crowd, undetermined | 95% | ordinal — risk 2 |
| `children` | yes, no, undetermined | — | none |
| `activity` | eating-drinking, sport-active, performing, sightseeing, playing, celebration, posing, working, animal-nature, other | 90% | **highest** |

`activity` sits closest to the field-shape boundary: 10 classes, a real tail, and
`other` doing the job `undetermined` does elsewhere. The `notes` bucket
conflation was measured — a model reads a catch-all as a default. Slice 0 counts
the `other` rate before any head is trained.

---

## 3. Training

### Label generation — forced choice, with a costless escape

One request per asset, all five questions, strict JSON, enumerated options. Not
derived from prose.

- **Teacher**: `scottlowry/Qwen3.8-27B-oQ4e-mtp` — Apache-2.0, verified free of
  distillation and output clauses; `teacher_label.py` already guards the pin.
  (The family is now mixed — `Qwen3.8-Flash-Next` and `Qwen3.8-2.4T-A95B` ship
  under Qwen Community Licence 1.0. Do not drift.)
- **Temperature 0.0.** Measured: at 0.3, four repeats of one pack gave four
  different answers; at 0.0 all four were byte-identical. A banked answer produced
  at 0.3 is a lie.
- **512 px long edge** — resolution is 3–4× the wall clock.
- **Reuse `teacher_label.py`** — resumable write-ahead JSONL, proper-noun scrub,
  envelope validation. Change the prompt, not the harness.

**`undetermined` must be a costless first-class schema escape, not prose
permission.** This is the one thing that can silently poison the whole label set.
PhantomFill, 13 models: in free text GPT-5.5 correctly reports no data 98% of the
time; given a **required JSON field** it invents an answer **40/40**, and required
fields drive fabrication to **100% in ten of thirteen models**. Direct instruction
not to infer is overridden by the schema in four of six. The measured fix is a
guaranteed-reachable, costless escape: five open-weight models spent it **0/203**
on fabrication-carrying fields and 12 times on the one field where escaping
conceded nothing. So `{"anyOf": [{"enum": [...]}, {"const": "undetermined"}]}`,
with the constraint in **schema key names, not prose** (*"Qwen models tend to
benefit more from schema-level instructions"*).

**Assert the constraint is live.** `response_format`, `json_schema`,
`guided_json` and `grammar` appear **zero times** under `src/` today; omlx treats
`xgrammar` as optional and silently degrades `response_format` to prompt
injection logged at `logger.info`; llama.cpp returns 200 OK with unconstrained
output on a grammar parse error. Send a schema the model's natural output
violates and verify it is refused — assert on the response, never on the flag.

**Two content canaries per 1,000 labels.** A grammar guarantees the envelope,
never the facts: one sub-1B model passed as format-clean while *"parroting the
hints verbatim rather than describing the image."* Plant a hint that contradicts
the image; and re-ask the same prompt on a different image and require the
answers to differ.

### Volume — smaller than instinct says

Measured on exactly this task shape (structured JSON scene fields from teacher
labels): **2K → 70.92% · 8K → 80.64% · 10K → 80.62%.** 2K→8K buys +9.7 points;
8K→10K buys −0.02. The pairwise analogue agrees from the other side: ~1,000 of
11,000 labels recovered 95% of the achievable gap.

| pool | n | role |
|---|---|---|
| pilot, owner library | 1,000 | slice 0 — marginals, `other` rate, cost/call |
| main, owner library | 8,000 | train + cal + test (deployment domain) |
| main, CC BY 2.0 public corpus | 3,000 | train + portability check; the only publishable slice |
| stratified top-up | ≤2,000 | mined by the pilot head, paid only where the tail is thin |
| 22,769 banked visual judgments | — | **not labels** — asset inventory and stratification only |
| ~12,000 banked descriptions | — | **not labels** — re-imports the loss this design avoids |
| 400 public + 300 owner verified cards | 700 | **eval only, forever** |

**"Never train on 100% teacher output" does not transfer here.** The
Recap-DataComp ablation (all-synthetic collapsed ImageNet zero-shot 69.7 → 36.0;
50/50 peaked at 67.2) is about a *generative* caption distribution collapsing. A
classification head has no output distribution to collapse, only K logits. The
blend rule governs the describer student. Do not apply it here by analogy.

### Cost

Two provenances, both recorded because they disagree. **Measured, in-tree**:
stage B ran 1,381 teacher calls at 0.51 s isolated model time (~12 min) inside
**47 min wall clock** — ~2.0 s/call, and ~60% of the bill is serving overhead,
not the model. **Estimated, research doc**: 6–10 s/image at 512 px for a *full*
card, i.e. 25k in 40–70 h.

The forced-choice card is ≤80 completion tokens against the full card's ~500 and
the workload is ~93% prefill, so it should sit near the measured end. Budget
**5–9 h for 12k labels at concurrency 2, locally, free — and measure the first
200 before buying the rest.** Hosted is priced at **~$0.00046/image**, so the
3,000-image public corpus is ~$1.40; the owner library stays local for privacy.
Preflight rules stand: never compete with the local server.

### Splits and the leakage rule

**Group by day/moment, never by asset.** Two frames of one burst, one in train and
one in test, makes every number optimistic. Transfer the pairwise probe's
connected-components split and its programmatic assert (`verify_no_leakage()`
raises if an id appears in two splits); group key `(moment_id if known else
capture_day)`; 70/10/20, seed 42.

The 700 verified cards go on a `never_train` list checked at dataset-build time; a
build that finds an overlap **fails loudly** rather than dropping the row. They
carry `description` and `setting`, not the five head labels, so they need hand
annotation for the five fields — risk 3, the only unpurchasable input here.

**Score them paired.** Detecting a 3-point difference *unpaired* needs ~969 items;
the same 300–500 items scored under both versions removes between-item variance
and makes a small truth set viable. Every head version is evaluated on the
identical item list and the gate reads the paired delta.

### Class imbalance

1. **Stratified top-up labeling** — the pilot head mines candidates for rare
   classes and the teacher is paid only for those (`probe_pairhead_flywheel.py`).
2. **Class-balanced loss weights**, inverse-frequency, capped at 10×.
3. **Never duplicate rows** — it inflates the calibration split and the thresholds
   come out fitted to a distribution that does not exist.

Hard rule: **balancing touches the training split only; calibration and test stay
at natural frequency.**

### Target metrics

Accuracy-on-covered on the hand-verified owner truth:

| head | accuracy bar | at coverage ≥ |
|---|---|---|
| `location` | 97% | 85% |
| `people` | 95% | 80% |
| `activity` | 90% | 70% |
| `children` | 95% | 85% |
| `venue` | 88% (provisional — owner sets it) | 70% |

**The ceiling caveat decides what the gate can be.** The teacher self-agrees at
~95% (343 of 6,384 pairs conflicted across repeats) — healthy, roughly 2.5× more
self-consistent than published text judges, but still a ceiling. Any
agreement-with-teacher number above 95% is fitting label noise. **Teacher
agreement is a training signal and a regression tripwire; it cannot certify a 97%
head.** The acceptance truth is the hand-verified set — which is what makes risk
3 load-bearing.

---

## 4. Embedding cache

Its own SQLite file, `~/.immich-memories/cache/embeddings.db`, for the reason
`judgment_cache.py` gives for its own: the analysis cache carries a
`SCHEMA_VERSION` real users' stored analysis keys off, and bumping it for a
derived cache would invalidate everybody's work for an unrelated feature. This
file can be deleted at any time and costs only the compute it saved. Connections
via `ThreadOwnedConnections`; every failure quiet, costing only compute.

```sql
CREATE TABLE IF NOT EXISTS encoder_registry (
    encoder_key        TEXT PRIMARY KEY,   -- sha256 of the four fields below
    encoder_id         TEXT NOT NULL,      -- "facebook/dinov2-small@<40-char sha>"
    weights_sha256     TEXT NOT NULL,      -- of the exported .onnx bytes
    preprocess_version TEXT NOT NULL,      -- "dinov2-s14-224-crop-v1"
    layout_version     TEXT NOT NULL,      -- "cls+mean+quad2x2/pca256/fp16"
    registered_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS embeddings (
    asset_id       TEXT NOT NULL,
    encoder_key    TEXT NOT NULL,
    vector         BLOB NOT NULL,          -- fp16, 256 dims = 512 bytes
    preview_sha256 TEXT NOT NULL,          -- the exact bytes embedded
    source_updated TEXT NOT NULL,          -- Immich updatedAt at derivation
    embedded_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (asset_id, encoder_key)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS head_facts (
    asset_id     TEXT NOT NULL,
    head_name    TEXT NOT NULL,
    head_version TEXT NOT NULL,            -- "location-v1"
    encoder_key  TEXT NOT NULL,
    label        TEXT NOT NULL,            -- includes "undetermined"
    confidence   REAL NOT NULL,
    covered      INTEGER NOT NULL,         -- 0/1: cleared the abstention band
    decided_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (asset_id, head_name, head_version, encoder_key)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS head_facts_lookup ON head_facts (head_name, head_version, label);
```

During slices 0–3 only, the pre-PCA 2,304-d pack is kept in a parallel staging
table so the PCA can be refit without re-embedding. It is dropped when
`layout_version` freezes.

**Key composition.** `encoder_key = sha256(encoder_id ‖ weights_sha256 ‖
preprocess_version ‖ layout_version)`; `head_facts` keys on that **plus**
`head_version`. That split is the point: **a head retrain bumps `head_version`
only** — embeddings untouched, retraining reads from cache, re-deciding 100k
assets is five small matmuls per asset. Only a change to checkpoint, export,
preprocessing or layout forces a re-embed, and each is a deliberate act with a name.

| assets | embeddings | head_facts | total | (+ staging table) |
|---|---|---|---|---|
| 10,000 | 5.9 MB | 4.5 MB | **~10 MB** | ~58 MB |
| 100,000 | 59 MB | 45 MB | **~104 MB** | ~575 MB |

**Invalidation — three rules, no TTLs**, from the index's ownership classes:
(1) pixel-derived, forever at `(asset_id, encoder_key)`; (2) `source_updated` or
`preview_sha256` mismatch re-queues the asset — the one case where "pixels do not
change" is false; (3) a new `encoder_key` or `head_version` orphans old rows by
design, and orphans are dropped only by an explicit `cache prune`, so a rollback
is free until the owner asks for the space.

Read path is fail-open and write-back: ask the cache, compute misses inline, write
back. An empty cache is the fully-cold case, never an error.

---

## 5. Runtime

**ONNX Runtime, and only ONNX Runtime.** `CPUExecutionProvider` everywhere,
`CoreMLExecutionProvider` where it compiles. One graph, one code path, a provider
list.

The owner's ruling is explicit — *"the portable stack is the shipped stack — no
per-platform implementations."* So there is **no torch in `src/`**. torch lives
in `scripts/` for training and offline label work only. An "optional torch fast
lane" would be a second implementation.

- **It is already the pattern here.** `speech/fireredvad.py` runs a vendored
  2.4 MB ONNX graph on `onnxruntime>=1.28` with the stated rationale "no runtime
  download, no torch". Prebuilt wheels for macOS arm64 and manylinux; nothing
  compiles at install time. Debian-based images only, never Alpine.
- **torch is not a core dependency** — it is in the `audio-ml`/`demucs` extras.
  Requiring it here puts ~200 MB of unused runtime in the base image.
- **The export is first-party.** DINOv2 and SigLIP have native `OnnxConfig`
  classes in `optimum-onnx`; a trained head is `Gemm`+`Relu`. This is exactly
  where the distillation research says ONNX wins, against VLM students where
  *"mainline `optimum` exports zero VLMs"* (166 configs, not one a VLM).
- **MLX is Apple-only** — reference implementation at most.

### Do not quantize for latency

Measured, counter-intuitive, and in the design so it is not rediscovered in slice
4. On Apple Silicon, ORT 1.29.0: SigLIP2-base-384 **int8 is 2.2× slower than
fp32** (181 vs 83 ms), q4 2.5× slower (205 ms); DINOv2-base **int8 is 1.5× slower
on CPU and 18× slower on CoreML**. Cause: `ConvInteger`/`MatMulInteger` dynamic
quantisation with **no optimised ARM64 kernel path**. MLX reaches the same
conclusion independently and never quantises the vision tower.
*Quantisation here is a disk decision, not a latency one.* int8 stays on the
table only for x86-64 with VNNI, where the kernel path exists — unmeasured, so
it is an experiment, not a plan.

### Batch pipeline

Decode is the bottleneck on the accelerated path, measured (ViT-S 19.16 vs ViT-B
18.23 ms/image on the identical loop). So: producer/consumer, not one loop.

- N decode workers in a process pool: preview JPEG → `Image.draft()` DCT-scaled
  decode → resize/crop/normalize → shared-memory float32 CHW.
- One ORT session, `intra_op_num_threads = cores − decode_workers`, batch 32,
  fixed shape `[32,3,224,224]` plus a `[1,…]` tail graph so the last partial batch
  does not re-trigger optimization. Every ORT number above is batch 1; batching is
  an unmeasured win, not a claimed one.
- Heads run in the same batch — five small matmuls over 256 dims, well under
  0.1 ms/image.

### Expected cost, and the bar it is actually held to

| target | config | expected ms/image | 100k assets |
|---|---|---|---|
| M-series, CoreML EP | ViT-S fp32, batched | **3–6** (base measured 6.0 at batch 1) | 5–10 min |
| M-series, CPU EP | ViT-S fp32 | **30–40** (base measured 136 ÷ ~4) | 50–70 min |
| 4-core Debian, x86-64 | ViT-S fp32 | **60–150** | 1.7–4.2 h |

**The bar changed shape.** The inventory's 10–30 ms/image bar was written for a
*query-time* pass over 20,000 assets. The per-asset index moves this to
*ingestion time*, once per asset, ahead of need, yielding to everything. The
requirement is therefore **completion, not latency**: a 4-core NAS finishing a
100k library in one to four background hours is the product working. The ≤10 ms
target survives only where it belongs — on M-series, where a generation can
afford to embed inline. Two consequences: the CPU tier's gate is *"a 10k-asset
library finishes in under 30 minutes on 4 cores"*, not a per-image number; and
quantisation stops being load-bearing, which is fortunate given the measurements
above.

Contention follows the index's ladder-mode-3 rule: the ahead-of-need runner yields
to a running generation, a training run, or a busy machine.

---

## 6. Integration

```
src/immich_memories/triage/
├── __init__.py         # public re-exports only (the one place a shim is allowed)
├── preprocess.py       # decode + resize/crop/normalize; PREPROCESS_VERSION
├── encoder.py          # DinoEmbedder: ORT session, EP selection, batch forward
├── pooling.py          # token pack + PCA projection; LAYOUT_VERSION
├── heads.py            # TriageHead: load npz weights, forward, calibrated decide()
├── head_registry.py    # the five heads: vocabulary, version, thresholds
├── engine.py           # TriageEngine: composes embedder + heads + store
├── contracts.py        # Protocols (below)
└── bundled/            # dinov2_vits14.onnx, pca.npz, heads-v1.npz, LICENSE files
src/immich_memories/cache/embedding_cache.py   # EmbeddingCache + HeadFactStore
```

A new package, not a file in `analysis/`: this is per-asset perception, not clip
analysis, and `analysis/` is already ~60 files. `bundled/` follows
`speech/bundled_models/` — weights vendored, no first-run download,
`THIRD_PARTY_NOTICES` extended **in the same PR**, with the format-conversion flag
Apache-2.0 §4(b) wants on an exported graph.

```python
class AssetImageSource(Protocol):
    def preview_bytes(self, asset_id: str) -> bytes | None: ...

class EmbeddingStore(Protocol):
    def vectors_for(self, asset_ids: Sequence[str], encoder_key: str) -> dict[str, np.ndarray]: ...
    def remember_vectors(self, rows: Sequence[EmbeddingRow]) -> None: ...
    def facts_for(self, asset_ids: Sequence[str], head: str, version: str) -> dict[str, HeadFact]: ...
    def remember_facts(self, rows: Sequence[HeadFact]) -> None: ...

class TriageObserver(Protocol):
    def on_batch(self, done: int, total: int, ms_per_image: float) -> None: ...
```

`EmbeddingStore` is one interface so the knowledge-store migration to Postgres
stays mechanical. The engine never talks to Immich; the thumbnail cache
implements `AssetImageSource`.

New Tier 2 config section `triage` (add to `_TIER2_SECTIONS`; model in a new
`config_models_triage.py`). Every option has a sane default, and
`enabled: false` must be byte-identical to today.

```yaml
advanced:
  triage:
    enabled: true          # fail-open: disabling costs time, not correctness
    encoder: dinov2-s14-224
    providers: auto        # auto | cpu | coreml
    batch_size: 32
    decode_workers: 4
    heads: [location, venue, people, children, activity]
    index_ahead: false     # ladder mode 3; off until the owner turns it on
```

**Where it runs.** Ladder **mode 1½** — not free (an encoder pass) but not a model
call either. Cheap enough to run on every asset a run already downloads, which is
where it wires in first: beside `ThumbnailPrefetcher` / `generate_downloads.py`.

**Who reads it** (named consumers, all already banked): index query 4 (warm
generation) returns head facts with the rest of layer 1; free-text threads use
`children=yes` + `venue=nature` as a structural filter *inside* an already-scoped
corpus (the CLIP ban holds — a head emits a fact, it does not answer a query);
surprise-me reads an `activity` histogram over 18 years; structure and allocation
steer variety across `venue`/`activity`.

**Not** Cull, **not** Selects, **not** the final cut. As a code rule: `head_facts`
may be read by anything that *adds* to a candidate set and by nothing that
*subtracts* from one. That is the owner's hard rule made mechanical.

**The other two models.** The **0.5B describer** is unchanged — still owns prose,
still runs on a shortlist. It answers Q5's grounding; the heads answer Q1's closed
half, which is the field-shape rule restated. Today Q1's per-photo half barely
runs: **8,755 of 8,854 photos have no category at all (1.1% coverage)**, because
the model only ever sees what metadata already chose. A head at a few ms makes the
question affordable on every asset — that *is* the coverage-first argument. The
**27B** is unchanged, still owns judgment and every cut; its new second job is
offline label production, never the hot path. The **pairwise head** is the same
shape, encoder and cache — head #6 in a later slice.

---

## 7. Eval gates

Per head, on the hand-verified truth, scored **paired** across versions:

1. **Accuracy-on-covered** ≥ the head's bar at ≥ its coverage bar, on the 300
   owner-library cards re-annotated for that field.
2. **Domain gap** — the same numbers on the 400 public-corpus cards within **8
   points** of the owner-library numbers. Wider means the head learned the library,
   not the question. Precedent: the 0.5B student's 0.632 holdout vs 0.493
   on-library, and that 14-point gap is what killed it. This is also the one thing
   nobody has published — *"no published work measures web-image →
   personal/consumer-photo transfer"* — so it is ours to make.
3. **Abstention honesty** — ECE ≤ 0.05 on the covered set, and the head's
   `undetermined` rate no more than 1.5× the teacher's own. A head that abstains
   its way to a high number has not passed.
4. **EP parity** — CoreML vs CPU: pooled-pack cosine ≥ 0.9999 and 100%
   head-decision agreement over 1,000 assets. Not optional: DINOv2 is an
   export-required graph and nobody publishes a per-EP validation matrix.
5. **Completion** — a 10k library indexes in <30 min on a 4-core Debian container;
   ≤10 ms/image amortized on M-series with CoreML. Both over ≥5,000 assets, decode
   included.

**Two gates from the distillation program are removed by construction, and that is
a real architectural win worth naming.** Gate 2 there — `hallucination_rate =
FP_fields / predicted_fields` — exists because a generative student inherits and
amplifies the teacher's phantom-fill, and because *"there is no standard
image→JSON hallucination metric as of 2026-08"*; a closed-vocabulary head
*"structurally cannot phantom-fill"*, so the budgeted DPO round does not exist
here. Gate 4 (canary exposure) also does not apply — no text is generated, so no
proper noun can be regurgitated. **But the labeling run is still generative**, so
the proper-noun scrub stays on: the leak surface moved from the model to the
corpus, it did not disappear.

**Standing harness**: `scripts/triage/eval_heads.py` → one row per head version in
`metrics.jsonl` in the matrix dir, the shape the existing probes use, plus a `make
triage-eval` target. A version that regresses any gate is not promoted in
`head_registry.py`. The truth sets never move — the gate file is the ratchet.
Reports carry counts and metrics only, no asset ids. **Shadow mode** is how a
version earns promotion without a wall: run `v_next` beside `v_current` over a
real run's assets, record disagreements only, change no behavior — cheap, because
both read the same cached embeddings.

---

## 8. Build plan

**Slice 0 — pilot + CPU measurement (no `src/` code).** Two things in parallel,
both cheap, both able to kill the design. (a) 1,000 owner-library assets,
forced-choice 5-field call, temp 0.0, 512 px, costless schema escape,
live-constraint assertion, both canaries → s/call, five marginals, the `other`
rate, teacher self-agreement over 100 repeats. (b) `optimum-cli export onnx` on
`facebook/dinov2-small`, then 500 previews on macOS CoreML, macOS CPU, and
`docker run --cpus=4 debian` at fp32 and int8 → ms/image on all three, and
whether the 224-vs-392 CPU inversion reproduces on ViT-S.
**STOP** — the owner confirms the five vocabularies before the main labeling run,
and reads the Debian number before anything assumes a CPU tier.

**Slice 1 — one head, end to end.** `location`: highest prose number to beat
(97%), three classes, cheapest to hand-verify. Embed the 6,841 matrix assets +
pilot set; fit PCA-256; train probe and MLP, pick by the ≥2-point rule; fit the
band on ~500 calibration items; emit the curve; hand-verify on the 300 owner
cards and score paired. One number: **accuracy-on-covered ≥97% at ≥85%
coverage.** Still `scripts/` only, so a miss costs a script.
**STOP** — if it misses, the encoder or the pooling is wrong and the other four
heads are not worth training.

**Slice 2 — cache and engine.** `cache/embedding_cache.py` + the `triage/`
package with one head, wired into the download path behind `triage.enabled`,
default **off**. Three properties, each a test: a warm rerun computes zero
embeddings; a `head_version` bump re-decides without re-embedding; a deleted DB
costs time only. EP parity gate lands here.

**Slice 3 — the remaining four heads.** 8k owner + 3k public labels, stratified
top-up for the tail, four heads trained and gated, four fields hand-verified on
both truth sets. **STOP** — the owner sets the `venue` bar.

**Slice 4 — the CPU tier.** Completion gate, provider selection,
`triage.enabled: true` by default. **STOP** if a 10k library does not index in 30
min; the fallback ladder, cheapest first, is more `decode_workers`, then 168 px
(144 patch tokens, ~0.55× FLOPs — but that changes `preprocess_version`, i.e. a
full re-embed), then int8 on x86-64 only if slice 0 measured a win there.

**Slice 5 — the ahead-of-need runner.** Ladder mode 3, the contention rule, a
`triage index` CLI. After the per-asset index store exists.

**Slice 6 — fold in the pairwise head** onto the same trunk, cache and gates. The
6,384-pair bank is its regression set, with the survey's warning attached: a head
scored on held-out *teacher* verdicts measures agreement with the 27B, not
correctness, and is not comparable to the 286/291 unanimous-ground-truth
baseline. It needs its own few-hundred human-labelled pair holdout first.

---

## 9. Risks, ranked, with the cheapest experiment for each

1. **The 4-core CPU tier does not fit even as background work.** DINOv2-base
   measured 136 ms/image on ORT CPU on an *18-core M5 Max*; a 4-core x86 container
   running ViT-S is genuinely uncertain. → Slice 0's Debian measurement, 500
   previews, fp32 and int8, half a day, no labels needed. Second experiment if it
   misses: the same run at 168 px.
2. **The pooled pack is too coarse for `people`** — 2×2 quadrants may not separate
   `two` from `small-group`, and the class is ordinal, which a flat softmax
   ignores. → On the slice-1 embedding set, train the people head four ways —
   CLS-only, pooled pack, pooled pack with a cumulative-logit loss, and full 256
   tokens with attention pooling — on 2,000 labels. If full tokens win by >5
   points the layout changes *before* anything is cached at scale.
3. **The truth sets do not carry the head labels.** ~3,500 hand annotations is the
   only unpurchasable input. → Annotate `location` on 100 owner cards, time it,
   extrapolate. Over 4 h for the full job, cut the eval truth to 150 + 150 and say
   so in the gate — the paired-scoring discipline is what makes a smaller set viable.
4. **Forced-choice labels may be worse than prose-derived labels** for these five
   fields — untested, and PhantomFill says a required field is exactly where a
   model invents. → On 200 assets get both: a forced-choice answer with the
   costless escape, and a prose description text-judged into the same vocabulary.
   Score both against hand truth. If prose wins, the label generator changes; the
   architecture does not.
5. **`other` swallows `activity`.** The `notes` bucket conflation is the
   precedent, and `activity` sits closest to the field-shape boundary where frozen
   heads start failing on the tail. → Count the `other` rate in slice 0. Above
   25%, the vocabulary is wrong before a head is trained.
6. **The teacher's own labels are unstable at the vocabulary edges.** ~95%
   self-agreement is the average; the hard region is worse. → Re-ask 100 pilot
   assets in slice 0 and record per-field self-agreement. A field below 90% cannot
   support a 95% head and must be re-worded or dropped.
7. **Center-crop loses the frame edges**, where extra people stand. → 1,000
   assets, squash-to-224 vs crop-224, people head only. It is a
   `preprocess_version` decision, so it must be settled before slice 2.
8. **Registers.** Plain DINOv2 has known artifact tokens that pollute mean
   pooling. → Embed 1,000 assets with `facebook/dinov2-with-registers-small` and
   re-run the slice-1 head. ≥1 point justifies the pin change; both are Apache-2.0
   and post-2023-08-31.
9. **Pixels change without `updatedAt` changing.** → No experiment;
   `preview_sha256` is 32 bytes and closes it.

### Open questions for the owner

- **Does `venue` earn a head at all?** No prose number to beat, and no named
  consumer today except variety steering — `location` + `activity` may cover it.
  Ruling before slice 3 saves a field's share of the labeling run *and* ~700 hand
  annotations.
- **What certifies a head — hand truth or teacher agreement?** The measured 95%
  teacher ceiling says teacher agreement cannot certify a 97% head, which makes
  ~3,500 hand annotations load-bearing. The alternative is lowering every bar to
  the teacher ceiling and spot-checking. This is the one place the design spends
  the owner's own hours, so it is the owner's call.

## Owner rulings — 2026-08-31, same day

1. **`venue` is dropped from v1.** No named consumer; most of its signal is
   recoverable from `location` + `activity`. Add it the day a consumer names
   itself — a labeling run plus a training script, not a redesign. (Correction
   to the open question above: venue did have a battery baseline — student 83%,
   teacher 91% — the missing thing is the customer, not the number.)
2. **Certification truth is judge-built, not owner-hours.** The pixels-first
   judge protocol that built the two verified truth sets on 2026-08-31 (Opus
   judges under a claim-by-claim protocol, independence from the model being
   scored, Fable spot-audit — 6/6 audits clean over 700 images) is the
   certification path for every head. The owner rules only on taste/ambiguity
   classes. Teacher agreement remains disqualified for certification (95%
   self-agreement ceiling).

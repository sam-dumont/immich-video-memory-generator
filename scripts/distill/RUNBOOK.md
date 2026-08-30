# Distilling the card model — operator runbook

Executable form of `docs/research/2026-08-30-card-model-distillation.md`. Read that note for *why*;
this file is *what to type*. Four stages, one command each, all resumable, all safe to Ctrl-C.

**Teacher: Qwen3.8-27B** (`scottlowry/Qwen3.8-27B-oQ4e-mtp`) on the local omlx server. Apache-2.0,
and §9.1 verified it carries no distillation clause and no output clause — the whole plan rests on
that. `teacher_label.py` refuses to start if omlx is serving something else, so a night is never
spent labelling with the wrong model.

Nothing is written into the repo. Everything lands under `~/.immich-memories-distill/`.

---

## Prerequisites

```bash
# omlx serving the 27B on http://localhost:9999/v1
curl -s http://localhost:9999/v1/models | head

# the API key is read from ~/.immich-memories/config.yaml (llm.api_key or advanced.llm.api_key)
# or from $OMLX_API_KEY. Nothing else needs configuring.
```

Every command below is prefixed `uv run --with pyarrow` — that adds parquet to the project venv
without touching `pyproject.toml`. The scripts import the app's own prompt builders, so they must
run in the project environment, not an isolated one.

---

## Tonight: the pilot, in one sequence

Copy-paste the block. Stages A and B are the long ones; both resume if interrupted.

```bash
cd ~/Code/perso/immich-video-memory-generator

# A — corpus (~15 min metadata, ~20 min images, ~1 GB)
uv run --with pyarrow scripts/distill/pull_corpus.py --split validation --count 3000

# B — teacher labels (5–8 h; this is the overnight one)
uv run --with pyarrow scripts/distill/teacher_label.py --split validation --concurrency 2

# C — blend + SFT dataset (~2 min)
uv run --with pyarrow scripts/distill/assemble_blend.py --split validation --human-ratio 0.5
```

Then read **Stage E** (`train_lora.md`) over coffee and pick a venue.

Before launching B for real, smoke it with ten images and read them:

```bash
uv run --with pyarrow scripts/distill/teacher_label.py --split validation --limit 10 --no-canaries
head -3 ~/.immich-memories-distill/validation/labels.jsonl | python3 -m json.tool
```

---

## Pilot vs main run

| | Pilot (`--split validation`) | Middle (`--split test`) | Main (`--split train`) |
|---|---|---|---|
| Candidate pool | **3,580 `[measured]`** — see below | ~10.8k `[EST]` at the same 8.6% yield | ~330k on the CVDF mirror (§4.1 `[measured]`) |
| `--count` | 3000 | 8000 | 15000–20000 (§2: flat by 8k; above 50k expect under 1–2 points) |
| Metadata download | **46 MB `[measured]`** | ~135 MB | **7.8 GB** (machine labels 7.18 GB + image metadata 638 MB) |
| Localized Narratives | 10 MB | 31 MB | 138 MB |
| Images on disk | **~1.5 GB** @ 502 KB mean `[measured]` | ~4 GB | ~6 GB @ 303 KB mean (§4.1) |
| **Total disk** | **~1.6 GB** | **~4.2 GB** | **~14 GB** |
| Labelling wall clock | 5–8 h — one overnight | 13–22 h | 35–55 h — two or three overnights |
| CVDF mirror coverage | **100%** (8/8 `[measured]`) | ~100% expected | ~18% of personal-filter ids — the walk skips absent ids and keeps going |
| Cost | £0 | £0 | £0 local, or ~$12 on a hosted endpoint (§6) |

**The pilot pool, measured 2026-08-30 by running stage A:** of 41,620 validation images, **9,264**
carry a personal-life label and **8,691** carry `Person`; **3,669** carry both (8.8%). The licence
gate then rejects **89 (2.4%)** as NC/ND/blank/institutional — which lands almost exactly on §4.1's
measured ~2.6% institutional residue — leaving **3,580 candidates**.

So `--count 3000` uses 84% of the validation pool. That fits, but there is no headroom: if you want
5k without the 7.8 GB train download, use `--split test`.

Training is the cheap part either way: **$0.87–$1.80** on a RunPod Community 4090, or 1.2–5 h free
on Apple silicon.

Run the pilot end-to-end before spending 7.8 GB and three nights on the main run. Its whole job is
to catch a broken gate cheaply.

---

## The two human-in-the-loop points

Everything else is unattended. These two are not, and neither can be automated away.

### 1. Training venue — after stage C, before stage E

`train_lora.md` has both paths costed and both sets of commands. **Path (a), local MLX, works** —
`mlx-vlm` 0.6.17 ships `python -m mlx_vlm.lora`, Qwen3-VL is trainable, and its defaults are
already r=8 / α=16 with LoRA on the language model only, which is the §3.2/§3.3 recipe with no
configuration. Take path (b), a rented 4090, if you hit the open collator bug or want the run off
your laptop.

### 2. The hand-corrected holdout — before stage D means anything

This is §11, and it is the single most valuable hour in the whole project.

**No published work measures web-image → personal-photo transfer for VLM captioning.** Five search
formulations returned nothing. The corpus is Flickr public photography; the deployment domain is
private camera rolls. That gap is the one thing that could invalidate the plan, and the owner is
uniquely equipped to close it: the ~12k banked descriptions and ~22,769 banked visual judgments are
already temp-0 teacher output on exactly the target distribution.

Hold out **300–500** of them, **hand-correct the cards**, and score against that. Format:

```json
{"image_id": "…", "fields": {"description": "…", "setting": "…"}}
```

Two reasons this is not optional:

- **Gate 2 is vacuous without it.** Measured against the teacher's own labels, the teacher's
  phantom-fill rate is 0 by construction. Only hand-corrected truth gives the gate a real bar.
- **A public-corpus benchmark cannot answer the domain question.** DOCCI and ImageInWords are good
  description benchmarks and they do not measure this.

This also resolves the corpus tension: the banked library stops being training data (where it
leaks, §8) and becomes the one asset that answers the question no paper does.

---

## Go / no-go gates between stages

### A → B

```bash
python3 -c "
import sys,json; sys.path.insert(0,'scripts/distill')
from distill_common import read_parquet
r=read_parquet('$HOME/.immich-memories-distill/validation/manifest.parquet')
print(len(r),'rows'); print(r[0])
print('blank licence:', sum(1 for x in r if not x['license_url']))
print('blank author :', sum(1 for x in r if not x['author']))"
```

- ✅ row count ≥ your `--count`, or the script told you the mirror ran out
- ✅ **zero** blank `license_url` / `author` — those columns are the §9.3 obligation, not decoration
- ✅ open three JPEGs and confirm they look like snapshots, not archive scans
- ❌ if candidates < 2× `--count`, widen the vocabulary or move to `--split train`

### B → C

- ✅ error rate under 5% (the progress line prints it)
- ✅ 20 canary rows present, four repeat rates. **Watch the canary cost line.** Each canary is
  repeated by its injection rate at assembly, so 20 canaries at {1,5,20,100} expand to **630
  training samples** — 3% of a 20k run but **17% of a 3k pilot**. On the pilot either accept it
  (the pilot's job is to prove the machinery, not the quality) or pass `--canary-count 8`.
- ✅ **read ten labels.** Redactions should be non-zero on people-photos and near-zero on scenery.
  All-`[name]` prose means the scrub is over-firing; zero redactions across ten people-photos means
  the teacher is being asked the wrong question.

```bash
python3 -c "
import sys,collections; sys.path.insert(0,'scripts/distill')
from distill_common import read_parquet
r=read_parquet('$HOME/.immich-memories-distill/validation/labels.parquet')
print(collections.Counter(x['status'] for x in r))
print('canaries', sum(1 for x in r if x['is_canary']))
print('median redactions', sorted(x['redactions'] for x in r)[len(r)//2])"
```

### C → E

The stage prints the realised blend. Check `dataset_card.md`:

- ✅ human share **0.45–0.55** — §3.1's peak. All-synthetic collapsed ImageNet zero-shot 69.7 → 36.0
- ✅ `sources.parquet` written, creator and licence columns populated
- ✅ one image per record (mlx-vlm issue #1726 crashes on multi-image records; a test pins this)

### E → D

🔴 **The one check that matters during training.** `mlx-vlm` shipped a bug where images were
discarded entirely: the run completed, the loss looked plausible, the model learned nothing visual.
Signature was 23 trained tokens/iter with loss stuck at 0.43; after the fix, 1,076 tokens/iter and
loss 22.1 → 3.2.

**Verify trained tokens/iteration ≈ 440 × batch size** (≈1,760 at `--batch-size 4`) on the first
report line. A three-digit number means stop.

*(Correction to the research note: it credits PR #823, merged 2026-03-15. #823 was never merged —
closed unmerged 10 minutes after opening. The real fix is **PR #826, merged 2026-04-21**.)*

### D → ship

```bash
uv run --with pyarrow scripts/distill/eval_gates.py \
  --holdout    ~/corrected_holdout.jsonl \
  --predictions student_predictions.jsonl \
  --canaries   ~/.immich-memories-distill/validation/labels.parquet \
  --teacher-rate 0.07
```

Four gates, all must pass:

1. **Field micro-F1** against the ~95% teacher self-agreement ceiling — the honest denominator
2. **FP_fields / predicted_fields ≤ teacher.** Computed by hand here because Donut's `cal_f1` pools
   FP and FN and structurally cannot report it, and `docext` iterates ground truth only, so
   hallucinated extra fields are invisible to it
3. **Duplicate rate on list fields = 0** — §3.2's structure-bound failure, invisible to F1
4. **Canary exposure single-digit at 1× and 5×**

nTED and leaf validity print alongside. **Do not gate on JSON parse-validity** — it runs 93.4–100%
across all models and does not discriminate.

Expect to fail gate 2 on the first pass and need a DPO round (§5). That is the predicted outcome,
not a surprise.

---

## Verified endpoints

All HTTP-HEAD checked **2026-08-30**. Sizes are `content-length`.

| Endpoint | Status | Size |
|---|---|---|
| `storage.googleapis.com/openimages/v7/oidv7-class-descriptions.csv` | ✅ 200 | 501 KB |
| `…/openimages/v5/validation-annotations-machine-imagelabels.csv` | ✅ 200 | 30.7 MB |
| `…/openimages/v5/test-annotations-machine-imagelabels.csv` | ✅ 200 | 89.9 MB |
| `…/openimages/v5/train-annotations-machine-imagelabels.csv` | ✅ 200 | 7.18 GB |
| `…/openimages/v7/oidv7-train-annotations-machine-imagelabels.csv` | ✅ 200 | 7.35 GB |
| `…/openimages/v7/oidv7-val-annotations-human-imagelabels.csv` | ✅ 200 | 28.4 MB |
| `…/openimages/v7/oidv7-train-annotations-human-imagelabels.csv` | ✅ 200 | 2.74 GB |
| `…/openimages/2018_04/validation/validation-images-with-rotation.csv` | ✅ 200 | 15.2 MB |
| `…/openimages/2018_04/test/test-images-with-rotation.csv` | ✅ 200 | 45.2 MB |
| `…/openimages/2018_04/train/train-images-boxable-with-rotation.csv` | ✅ 200 | 638 MB |
| `…/openimages/2018_04/image_ids_and_rotation.csv` (full 9.01M) | ✅ 200 | 3.35 GB |
| `…/localized-narratives/annotations/open_images_validation_captions.jsonl` | ✅ 200 | 10.1 MB |
| `…/localized-narratives/annotations/open_images_test_captions.jsonl` | ✅ 200 | 31.1 MB |
| `…/localized-narratives/annotations/open_images_train_v6_captions.jsonl` | ✅ 200 | 138 MB |
| `open-images-dataset.s3.amazonaws.com/{split}/{id}.jpg` | ✅ 200 | ~303 KB mean |
| `pypi.org/pypi/mlx-vlm/json` → 0.6.17, 2026-08-26 | ✅ 200 | — |

**Dead — do not use:**

| Endpoint | Status |
|---|---|
| `…/openimages/v6/oidv6-train-annotations-machine-imagelabels.csv` | ❌ **403** |
| `…/localized-narratives/…/open_images_train_v6_captions-00000-of-00010.jsonl` | ❌ **404** — the train captions file is **not** sharded; the sharded name applies only to the trace-carrying `*_localized_narratives-*.jsonl` |

**Two notes on choices made here.** The captions-only Localized Narratives files are 10 MB /
138 MB against 1.1 GB / 16 GB for the trace-carrying variants — same captions, 100× less to move.
And `train-images-boxable-with-rotation.csv` (638 MB) is preferred over `image_ids_and_rotation.csv`
(3.35 GB): the boxable subset is where `Person` boxes live, which is where the ∩ `Person` filter is
looking anyway. Point `IMAGE_METADATA_URLS["train"]` at the full file if you want the other 7M.

### TODO-verify

- **mlx-vlm issue #824** ("LoRA training broken for Qwen3.5 VLM") is still open but was reported
  against 0.4.0; its "Bug 1" code path no longer exists on main and the requested retest was never
  posted. Unknown whether it reproduces on 0.6.17.
- **Adapter load-back at inference** for Qwen3-VL under mlx-vlm — claimed broken in a closed PR
  thread, never evidenced. Verify by generating one prediction before trusting a whole eval run.
- **Licence drift.** §4.1 measured 93.2% of 132 reachable landing pages still CC BY 2.0, ~3.8%
  moved to ARR or NC. The manifest snapshots the licence string, a retrieval timestamp and a
  content hash at download time, which is the defence; it is not a re-verification.
- **RunPod pricing** ($0.34/hr Community 4090) was verified 2026-08-30 and moves.

---

## Legal rules the scripts enforce (§9)

Three behavioural rules, all mechanised so they cannot be forgotten:

1. **Never build a step that targets licence metadata.** *Beaulier v. Meta* (MTD granted
   2026-08-26) turned on "uniform transformation that incidentally sheds CMI". Resize and encode
   drop it as an ordinary consequence; the parallel table keeps it. `write_parquet` takes an
   explicit column list and `sources.parquet` carries creator, creator URL, licence name, licence
   URL, retrieval timestamp and content hash.
2. **Never publish a caption dataset with the creator/licence columns dropped.** Count II died on
   "internal use, not distribution" — publishing weights is very likely not distribution;
   publishing a stripped dataset is the one act that lands inside §1202(b)(3).
3. **CC0 and CC-BY only. NC, ND and SA excluded.** Not close: shipping permissive weights grants
   every downstream user the right to commercialise, and that right cannot be granted over an
   NC-limited upstream. The filter drops anything that is not plain CC BY 2.0, and a test pins it.

At ship time (§10): weights Apache-2.0, app stays MIT, GGUF + MLX (not ONNX), `sources.parquet`
beside the weights, **ungated repo** — HF gating collects username and email, and Commission
Guidelines ¶84 treats that like a monetisation strategy, arguably forfeiting the open-source
exemption. **Build the takedown path against the manifest before publishing, not after**: every
dataset ever withdrawn was one about people.

---

## Known limitations

- **The proper-noun scrub is a regex, not NER.** It redacts mid-sentence capitalised tokens outside
  a small place-generic allowlist. It cannot see a lowercase name, it keeps a name that opens a
  sentence, and it will redact a legitimate mid-sentence brand. Over-redaction is the cheap
  direction. Gate 4 exists because this is not trustworthy alone.
- **The institutional-author filter is a substring heuristic** over author and title (~2.6% of rows
  per §4.1). It will drop a person surnamed "Church". Recall is unmeasured.
- **Canary exposure needs ranks the serving stack may not expose.** Without `--canary-ranks`, gate 4
  falls back to an extraction probe and reports `FAIL (unmeasured)` rather than claiming a pass.
- **nTED is a flat-dict approximation** of Donut's tree edit distance — exact for this one-level
  schema, approximate for any nested one.
- **Gate 2 against teacher labels is vacuous.** See human-in-the-loop point 2.

---

## Files

| File | Stage |
|---|---|
| `pull_corpus.py` | A — licence-clean corpus from Open Images V7 + CVDF mirror |
| `teacher_label.py` | B — Qwen3.8-27B labels, scrubbed, canaried, resumable |
| `assemble_blend.py` | C — 50/50 human blend, SFT JSONL, dataset card, `sources.parquet` |
| `train_lora.md` | E — both venues, exact commands, the token-count check |
| `axolotl_qwen3vl_lora.yaml` | E — the path (b) config |
| `eval_gates.py` | D — the four §7 gates |
| `distill_common.py` | shared: paths, parquet, licence filter, deterministic sampling |

Tests: `tests/test_distill_pipeline.py` — `uv run --with pyarrow python -m pytest tests/test_distill_pipeline.py`

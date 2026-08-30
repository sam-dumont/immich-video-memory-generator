# Distilling the card model — operator runbook

Executable form of `docs/research/2026-08-30-card-model-distillation.md`. Read that note for *why*;
this file is *what to type*. Four stages, one command each, all resumable, all safe to Ctrl-C.

---

# FOR THE EXECUTING AGENT

**You can run this whole pilot from this file alone. You do not need any prior conversation.**

**Working directory:** `/Users/sam/Code/perso/immich-video-memory-generator`
**Data directory (the only place outside the repo you may write):** `~/.immich-memories-distill/`

**What this pipeline is.** It builds a licence-clean corpus of public personal-style photographs,
labels them with the owner's local 27B vision model, and blends those labels 50/50 with
human-written captions to make a training set for a much smaller student model. The point is to
move the app's bulk "describe this photo" job off a 27B onto a 2B without losing quality.

## Hard rules

1. **Never print, echo, log or paste an API key or the contents of `~/.immich-memories/config.yaml`.**
   Read the key into a shell variable and use it; never `echo` it. If you must show a command that
   uses it, show `$OMLX_KEY`, never its value.
2. **Write nothing outside `~/.immich-memories-distill/` and the repo's `scripts/distill/`
   outputs.** Do not modify any file in `src/`, `tests/`, or anywhere else in the repo. This task is
   run-only. If a script looks wrong, stop and report — do not patch it.
3. **Never compete for the local model server.** Stage B saturates it. See the preflight below.
4. **Never publish anything.** No `git push`, no HuggingFace upload, no dataset or weight release,
   no PR. That is an owner-only decision — see AUTONOMY BOUNDARIES.
5. **Do not change the teacher model pin.** `teacher_label.py` guards it, but if the guard fires,
   stop and report rather than passing `--model` to work around it.
6. Prefix every command with `uv run --with pyarrow`. That adds parquet to the project venv without
   touching `pyproject.toml`. The scripts import the app's own prompt builders, so they must run in
   the project environment, not an isolated one.

## Preflight — run this before stage B, every time

Stage B is a 5–8 hour saturating job. Two things must be true first.

**(a) Is the local server idle?** The owner runs other pipelines against the same box.

```bash
PID=$(pgrep -x omlx-server | head -1); echo "omlx-server pid=${PID:-none}"
ps -o %cpu=,etime= -p "$PID"; sleep 5; ps -o %cpu=,etime= -p "$PID"
pgrep -fl 'teacher_label.py|probe_smart_edit|probe_pairhead|probe_description' | grep -v pgrep
```

- Sustained **CPU > 20% across both samples** = the server is busy. **Wait or stop. Never compete.**
- Any hit from the second command = another pipeline is already running. **Stop and report.**
- `pid=none` = the server is not running. Stop and report; do not try to start it.

**(b) Is the pinned teacher the one being served?**

```bash
OMLX_KEY=$(python3 -c "import yaml,pathlib;d=yaml.safe_load((pathlib.Path.home()/'.immich-memories/config.yaml').read_text());print((d.get('llm') or {}).get('api_key') or ((d.get('advanced') or {}).get('llm') or {}).get('api_key'))")
curl -s -m 10 http://localhost:9999/v1/models -H "Authorization: Bearer $OMLX_KEY" | python3 -m json.tool | grep '"id"'
```

Expected: **`scottlowry/Qwen3.8-27B-oQ4e-mtp`**. Anything else → **stop and report**. Do not work
around it with `--model`. (`teacher_label.py` also refuses to start, so this check is belt and
braces — but check it yourself so you fail in seconds rather than after a download.)

Do not echo `$OMLX_KEY`. The command above never prints it.

---

# AUTONOMY BOUNDARIES

## You MAY proceed autonomously when

Run stage B's 10-item preview first, then this verification. **Proceed to the full stage B run only
if every criterion holds.**

```bash
uv run --with pyarrow scripts/distill/teacher_label.py --split validation --limit 10 --no-canaries

uv run --with pyarrow python - <<'PY'
import json, re, sys, statistics
from pathlib import Path
sys.path.insert(0, "scripts/distill")
from teacher_label import SCRUB_ALLOWLIST
rows = [json.loads(l) for l in (Path.home()/".immich-memories-distill/validation/labels.jsonl").read_text().splitlines() if l.strip()]
rows = [r for r in rows if not r.get("is_canary")][:10]
ok      = [r for r in rows if r.get("status") == "ok"]
parsed  = [r for r in ok if r.get("text")]
setting = [r for r in ok if "setting" in r]
word    = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z']{2,}\b")
leaks   = [w for r in ok for w in word.findall(" " + r.get("text","")) if w.casefold() not in SCRUB_ALLOWLIST]
lat     = [r.get("latency_s", 0) for r in ok] or [0]
print(f"status ok     : {len(ok)}/10        need >= 9")
print(f"parsed text   : {len(parsed)}/10        need >= 9")
print(f"setting key   : {len(setting)}/10        need >= 9")
print(f"proper nouns  : {len(leaks)} {leaks[:5]}   need == 0")
print(f"median latency: {statistics.median(lat):.1f}s   need <= 25.0")
print(f"median redact : {statistics.median([r.get('redactions',0) for r in ok] or [0])}")
for r in ok[:3]: print("  SAMPLE:", r.get("text","")[:110])
PY
```

| Criterion | Threshold | Why this number |
|---|---|---|
| status ok | **≥ 9/10** | one transient HTTP error is normal; two means the server or the request is wrong |
| parsed text | **≥ 9/10** | §7 measured parse-validity at 93.4–100% across all models — below 9/10 is a broken request, not a weak model |
| `setting` key present | **≥ 9/10** | the mid-flight schema field; if it is missing the student learns the wrong shape |
| proper nouns after scrub | **== 0** | the scrub is deterministic, so any survivor is a bug, and §8 says name-stripping is the *only* mitigation that addresses the real leak |
| median latency | **≤ 25.0 s** | 3000 images ÷ concurrency 2 × 25 s = 10.4 h. Above this the overnight budget is blown and the plan needs revisiting, not more patience |

All five hold → **read the three sample lines to confirm they describe photographs**, then run the
full stage B and continue through stage C unattended. Any criterion fails → **stop and report**.

## You MUST stop and report to the owner for

- **Any criterion above failing**, or any stage's verification failing its sanity bounds.
- **Any gate FAIL in stage D.** Report the gate table; do not tune thresholds to make it pass.
- **A training-venue change.** The default is the local `mlx_vlm.lora` path — taking the default
  needs no stop. Renting a GPU, or switching to axolotl/LLaMA-Factory/Unsloth, does.
- **The teacher pin not matching**, or the server being busy.
- 🔴 **THE TEACHER CHOICE, after stage B0.** Whether to label 25k images with the local 27B or pay
  a hosted provider is a cost, calendar and quality trade-off. Run B0, report the numbers, stop.
- 🔴 **ANYTHING PUBLISH-SHAPED — hard stop, owner only.** Pushing a branch, opening a PR, uploading
  weights or a dataset to HuggingFace, or releasing anything anywhere. Not a judgement call. Even
  if a gate passes and everything looks finished, publishing is the owner's decision alone.

---

# THE PILOT — stage by stage

Total: **~1.6 GB disk, ~6–9 h wall clock**, almost all of it stage B.

**Order: A → B0 → (owner decides the teacher) → B → C → E → D.** B0 is a 200-image probe that runs
*before* the big labelling spend and ends in a STOP. Do not skip it and do not run B first.

## Stage A — build the corpus

```bash
cd /Users/sam/Code/perso/immich-video-memory-generator
uv run --with pyarrow scripts/distill/pull_corpus.py --split validation --count 3000
```

**Budget:** ~15 min metadata + ~20 min images. 46 MB metadata + ~1.5 GB images.

**Expected artifacts**, under `~/.immich-memories-distill/`:

| Path | Sanity bound |
|---|---|
| `metadata/oidv7-class-descriptions.csv` | 0.5 MB |
| `metadata/validation-machine-imagelabels.csv` | 30.7 MB |
| `metadata/validation-images-with-rotation.csv` | 15.2 MB |
| `validation/candidates.json` | **3,400–3,700 entries** (live smoke: 3,580) |
| `validation/images/*.jpg` | **3,000 files**, ~502 KB mean |
| `validation/manifest.parquet` | **3,000 rows** |
| `validation/downloads.jsonl` | ≥3,000 lines |

Expected console lines (live smoke, 2026-08-30): `labels: 9264 personal-life, 8691 Person, 3669
both` then `licences: 3580 kept, 89 rejected`. The 2.4% rejection rate should land near §4.1's
measured ~2.6% institutional residue.

**Verification — run and evaluate:**

```bash
uv run --with pyarrow python - <<'PY'
import sys; from pathlib import Path
sys.path.insert(0, "scripts/distill")
from distill_common import read_parquet
r = read_parquet(Path.home()/".immich-memories-distill/validation/manifest.parquet")
print("rows              :", len(r), "  need 2900-3000")
print("blank licence_url :", sum(1 for x in r if not x["license_url"]), "  need 0")
print("blank author      :", sum(1 for x in r if not x["author"]), "  need 0")
print("non CC BY 2.0     :", sum(1 for x in r if x["license_name"] != "CC BY 2.0"), "  need 0")
print("mean KB           :", round(sum(x["bytes"] for x in r)/len(r)/1024))
print("sample title      :", r[0]["title"])
PY
```

Blank licence or author is a **hard fail** — those columns are the §9.3 legal obligation, not
decoration. Also open two or three JPEGs and confirm they look like snapshots, not archive scans.

**Resume:** rerun the identical command. Metadata resumes by byte range, the candidate scan is
cached in `candidates.json`, and an image already on disk is never refetched. A larger `--count`
walks further down the same deterministic order, so it tops up rather than reshuffling.

| Failure signature | Remedy |
|---|---|
| `no candidates survived the filter` | wrong `--confidence` or `--labels`; rerun with defaults |
| `the Person class did not resolve` | `oidv7-class-descriptions.csv` truncated — delete it and rerun |
| Downloads stall at ~84% of `--count` | the validation pool is only 3,580; lower `--count` or use `--split test` |
| `httpx.HTTPError` on individual ids | harmless, logged and retried next run |

## Stage B0 — teacher gap probe (run this BEFORE the big spend)

**Purpose.** Stage B costs 5–8 h on the pilot and two or three nights on the main run. Before
spending that, find out on **200 images** whether a hosted teacher would give materially different
labels from the pinned local 27B. Cheap question, expensive answer if skipped.

**This stage ends in a STOP.** Teacher choice is the owner's call.

### The candidate catalog — verified 2026-08-30

Only the two hosted providers the repo already speaks to. OpenAI and Anthropic are **out of scope**:
their terms forbid using outputs to train a competing model, which disqualifies them for a student
whose weights get published.

| Provider | Model | Vision | vs the 27B | Price / 25k images | Output-terms verdict |
|---|---|---|---|---|---|
| melious | `qwen3-vl-235b-a22b-instruct` | ✅ | **bigger**, 235B MoE, *purpose-built VL* | **€9.50–12.00** | ✅ **no clause; Apache-2.0 weights** |
| melious | `qwen3.5-397b-a17b` | ✅ | **bigger**, 397B MoE, same family as the 27B | €21.75–29.25 | ✅ **no clause; Apache-2.0 weights** |
| melious | `qwen3.8-27b` | ✅ | *the baseline* (27B dense) | €14.50–19.50 | ✅ no clause; Apache-2.0 |
| melious | `qwen2.5-vl-72b-instruct` | ✅ | older, smaller | €6.25–9.38 | ✅ no clause; Apache-2.0 |
| melious | `kimi-k3` | ✅ | much bigger, 2.8T | €89–124 | ✅ no clause, but weight licence is `other` |
| melious | `qwen3.8-max` | ❌ **text-only** | 2.4T but no vision | — | — |
| melious | `glm-5.3` | ❌ **text-only** | melious serves **no** GLM vision model | — | — |
| z.ai | `glm-5.3-flash` | ✅ | bigger, 320B/18B-active, 1M ctx, newest | $1.97–2.91 promo | 🔴 **BLOCKED via API** |
| z.ai | `glm-4.6v` | ✅ | comparable | $7.50–11.25 | 🔴 **BLOCKED via API** |
| self-host | `zai-org/GLM-5.3-Flash` | ✅ | bigger | your compute | ✅ **MIT weights — unrestricted** |

**melious: no output-terms clause exists.** Its Terms of Use (https://melious.ai/legal/terms,
effective 2026-04-03) were swept for `train`, `training`, `distill`, `competing`, `derive` and
their German equivalents — **none appear.** There is no separate AUP (`/legal/aup` is a 404). It
serves open-weight models only and publishes the upstream HF repo per model, so the fallback
authority is the weight licence, which for the whole Qwen line is **Apache-2.0**. Clean on both
layers. It also states, in your favour:

> §01(4): "User content (in particular chat inputs, uploaded data or AI-generated outputs) is not
> used for these purposes" [of model training].

🔴 **z.ai is BLOCKED for this project, via the API.** Terms of Use
(https://docs.z.ai/legal-agreement/terms-of-use.md, last updated 2026-04-14), Section III.4(f),
verbatim including its broken grammar:

> "You may not use Z.ai to develop, train, or enhance any algorithms, models, or technologies that
> directly or indirectly compete with us is prohibited."

Plus III.4(g), "Any other usage that may harm our interests is strictly forbidden." A photo-curation
model plausibly does not compete with z.ai — but "directly or indirectly" next to that catch-all is
too vague to bet a **published** model on. Private internal use is arguable; distribution is not.

✅ **But the GLM vision weights are MIT.** `zai-org/GLM-4.6V`, `zai-org/GLM-5.3-Flash`,
`zai-org/GLM-4.5V` and `zai-org/GLM-OCR` are all `license: MIT` on HuggingFace. **The chain rule:**
an API ToS is a contract that binds you only when you call the API. Self-host those weights and
III.4(f) never attaches — the outputs are unrestricted, including for training and distributing a
student. (The 744B text flagship `zai-org/GLM-5.3` is *not* MIT — `license: other`.)

**Recommended B0 candidate: `qwen3-vl-235b-a22b-instruct` on melious.** It is a purpose-built
vision-language flagship rather than a general model with an encoder bolted on, it is the cheapest
capable option on the clean list at ~€10, it is Apache-2.0, and the repo already knows it — the
model id appears in `probe_smart_edit_matrix.py`'s currency map. `qwen3.5-397b-a17b` is the
scale-over-specialisation alternative at ~2.5× the price and the same clean terms.

**Unverified, and it matters:** *neither provider publishes an image→token formula.* The prices
above assume 300–800 image tokens plus ~250 in / 150 out per image. Treat them as an order of
magnitude, not a quote — and note z.ai's promo pricing ends 2026-09-09.

### B0.1 — label 200 images with the pinned local teacher

```bash
uv run --with pyarrow scripts/distill/teacher_label.py \
  --split validation --limit 200 --no-canaries --concurrency 2
```

### B0.2 — label the SAME 200 with the hosted candidate

Keys come from environment variables **by name**. Never inline a key, never echo one.

```bash
# melious (OpenAI wire) — set MELIOUS_AI_BASE_URL and MELIOUS_AI_KEY in your shell first
uv run --with pyarrow scripts/distill/teacher_label.py \
  --split validation --limit 200 --no-canaries --concurrency 4 \
  --provider melious --model qwen3-vl-235b-a22b-instruct --label-tag melious
```

That is the recommended candidate. For the scale alternative, swap in
`--model qwen3.5-397b-a17b --label-tag qwen35`.

🔴 **Do not run the z.ai lane for this project.** `--provider zai` exists and works, but z.ai's
Section III.4(f) blocks using its outputs to train a model that gets distributed. The script prints
a warning and requires `--accept-provider-terms` to proceed, which you should not pass without the
owner saying so in writing. If the owner wants a GLM teacher, the clean route is **self-hosting the
MIT weights** (`zai-org/GLM-4.6V`) and pointing `--provider melious --base-url` at that server, or
using `--wire`/`--base-url` against a local endpoint.

`--label-tag` writes `labels-melious.parquet` alongside `labels.parquet`, so the probe can never
clobber the pinned run. Pass the tag **without a leading dash** — argparse would eat `-melious` as
a flag.

⚠️ **The wire split is real.** `--provider melious` speaks OpenAI `/chat/completions` with a
`Bearer` header and a `data:` image URL. `--provider zai` speaks Anthropic `/v1/messages` with an
`x-api-key` header and a base64 `source` block, because that is what the repo's z.ai credentials
are (see `scripts/probe_editorial_provider_replay.py`). `--wire` overrides the default if a
provider's account is the other dialect.

### B0.3 — compare

```bash
uv run --with pyarrow scripts/distill/gap_probe.py --split validation --b melious
```

Writes `gap_probe.md` (the summary) and `gap_probe.jsonl` (side-by-side, one row per image). It
scores field agreement with the **same arithmetic §7 gate 1 uses**, so the number means the same
thing in both places.

### The decision rule

> **The local 27B stays unless the hosted teacher beats it beyond the ~95% noise floor.**

Agreement **≥ 0.95** means the two teachers are indistinguishable on this evidence — keep the free
local one. Below that they genuinely differ, and *agreement is not quality*: read
`gap_probe.jsonl` and judge the disagreements by eye before recommending anything.

**A hosted teacher also wins ties on speed, if the owner opts to pay.** At ~1.2 s/call a hosted
endpoint labels 25k images in **~8 h**; the local 27B takes roughly **two nights**. That is a
cost-and-calendar decision, not a quality one, and it belongs to the owner.

### What to report

Paste the `gap_probe.md` table, the agreement number, the verdict line, and **three example
disagreements** you read yourself. Then stop.

## Stage B — teacher labels (the overnight one)

**Run the preflight above first.** Then the 10-item preview and the autonomy check, then:

```bash
uv run --with pyarrow scripts/distill/teacher_label.py \
  --split validation --concurrency 2 --canary-count 5
```

`--canary-count 5` is the pilot default. 20 canaries at repeats {1,5,20,100} expand to 630 training
samples — 3% of a 20k run but **17% of a 3k pilot**. Five keeps the canary block near 5% while
still covering all four repeat rates.

**Budget:** 5–8 h at concurrency 2. ~5 MB of labels.

| Path | Sanity bound |
|---|---|
| `validation/labels.jsonl` | **≥3,005 lines** (3,000 + 5 canaries), append-only |
| `validation/labels.parquet` | **~3,005 rows** |

**Verification — run and evaluate:**

```bash
uv run --with pyarrow python - <<'PY'
import sys, collections, statistics; from pathlib import Path
sys.path.insert(0, "scripts/distill")
from distill_common import read_parquet
r = read_parquet(Path.home()/".immich-memories-distill/validation/labels.parquet")
st = collections.Counter(x["status"] for x in r)
ok = [x for x in r if x["status"] == "ok"]
print("statuses       :", dict(st))
print("error rate     :", f"{st['error']/max(1,len(r)):.3f}", "  need < 0.05")
print("canaries       :", sum(1 for x in r if x["is_canary"]), "  need 5")
print("median redact  :", statistics.median([x["redactions"] for x in ok] or [0]))
print("median latency :", round(statistics.median([x["latency_s"] for x in ok] or [0]), 1))
print("empty text     :", sum(1 for x in ok if not x["text"]), "  need 0")
PY
```

**Read ten labels.** Redactions should be non-zero on people-photos and near-zero on scenery. All
`[name]` prose means the scrub is over-firing; zero redactions across ten people-photos means the
teacher is being asked the wrong question. Either way, stop and report.

**Resume:** rerun the identical command. Every completed label is in the append-only WAL and is
never re-requested; Ctrl-C is clean and loses at most the rows in flight.

| Failure signature | Remedy |
|---|---|
| `STOP: omlx is serving …` | teacher pin mismatch — **stop and report**, do not pass `--model` |
| `no manifest at …` | stage A did not finish; rerun stage A |
| Error rate climbing past 5% | server is loaded or OOM — stop, wait, rerun (it resumes) |
| Latency > 25 s/image | budget is blown — stop and report |
| Progress line stops advancing | Ctrl-C and rerun; it resumes from the WAL |

## Stage C — blend and assemble

```bash
uv run --with pyarrow scripts/distill/assemble_blend.py --split validation --human-ratio 0.5
```

**Budget:** ~2 min, +10 MB (Localized Narratives captions).

| Path | Sanity bound |
|---|---|
| `validation/dataset/train.jsonl` | **~2,730 lines** (3,000 − 400 holdout + ~130 canary repeats) |
| `validation/dataset/validation.jsonl` | **400 lines** |
| `validation/dataset/sources.parquet` | **3,000 rows** |
| `validation/dataset/dataset_card.md` | human share **0.45–0.55** |

**Verification — run and evaluate:**

```bash
grep -E "Teacher JSON|Human caption|Canary|realised" ~/.immich-memories-distill/validation/dataset/dataset_card.md
uv run --with pyarrow python - <<'PY'
import json; from pathlib import Path
d = Path.home()/".immich-memories-distill/validation/dataset"
tr = [json.loads(l) for l in (d/"train.jsonl").read_text().splitlines()]
va = [json.loads(l) for l in (d/"validation.jsonl").read_text().splitlines()]
print("train / holdout :", len(tr), "/", len(va), "  holdout need 400")
print("one image each  :", all(len(s["images"]) == 1 for s in tr+va), "  need True")
print("roles           :", tr[0]["messages"][0]["role"], tr[0]["messages"][1]["role"])
print("target parses   :", bool(json.loads(tr[0]["messages"][1]["content"][0]["text"])))
PY
```

Human share outside 0.45–0.55 is a fail: §3.1's ablation collapsed ImageNet zero-shot 69.7 → 36.0
on an all-synthetic mix, and the peak is 50/50. `one image each` must be True — mlx-vlm issue
**#1726** crashes the Qwen3-VL collator on multi-image records.

**Resume:** this stage is idempotent; just rerun it.

| Failure signature | Remedy |
|---|---|
| `missing …labels.parquet` | stage B did not finish |
| `narratives: 0 of N` | captions file truncated — delete `metadata/*-localized-narratives-captions.jsonl` and rerun |
| Human share ≈ 0 | Localized Narratives did not match the ids; check the narratives line |

## Stage E — train

**Read `scripts/distill/train_lora.md` and follow path (a), the local MLX venue.** That is the
default and needs no stop. It trains the 2B, then the challenger — see below.

🔴 **The one check during training:** verify trained tokens/iteration ≈ 440 × batch size (≈1,760 at
`--batch-size 4`) on the first report line. mlx-vlm shipped a bug where images were dropped
entirely: the run completed, the loss looked plausible, the model learned nothing visual. A
three-digit number means stop. (The 440 constant is Qwen3-VL-specific; `train_lora.md` gives the
relative form of the check for the challenger.)

## The challenger lane

One overnight queue trains **both** sizes on the **identical blend** — same `train.jsonl`, same
holdout, same four gates — so model size is the only variable.

| | Primary | Challenger |
|---|---|---|
| Model | `mlx-community/Qwen3-VL-2B-Instruct-bf16` | `mlx-community/SmolVLM2-500M-Video-Instruct-mlx` |
| Params | 2B | 0.5B |
| Licence | Apache-2.0 | Apache-2.0, ungated |
| Config (CUDA) | `axolotl_qwen3vl_lora.yaml` | `axolotl_smolvlm2_lora.yaml` |

Both are chat-shaped and emit the same JSON envelope they are trained on, so **`eval_gates.py`
needs no change** — score each student against the same holdout and compare the gate tables.

**Decision rule: ship the smallest student that passes all four gates within the teacher
self-agreement noise floor.**

Concretely: the ceiling is ~95% teacher self-agreement (§7 gate 1), so treat two students as tied
when their ceiling-adjusted micro-F1 differs by **less than 0.05**. If the 0.5B passes all four
gates and is within 0.05 of the 2B, **ship the 0.5B**. If the 0.5B fails any gate, ship the 2B.
**Escalate to a 4B student only if the 2B itself fails a gate** — do not reach for more parameters
to buy margin the gates do not ask for.

Report both gate tables to the owner regardless of which wins.

## Stage D — the gates

```bash
uv run --with pyarrow scripts/distill/eval_gates.py \
  --holdout     ~/.immich-memories-distill/validation/dataset/validation.jsonl \
  --predictions predictions_qwen3vl2b.jsonl \
  --canaries    ~/.immich-memories-distill/validation/labels.parquet
```

Exit code 0 = all gates pass, 1 = at least one failed. Four gates:

1. **Field micro-F1** against the ~95% teacher self-agreement ceiling — the honest denominator
2. **FP_fields / predicted_fields ≤ teacher.** Computed by hand here because Donut's `cal_f1` pools
   FP and FN and structurally cannot report it, and `docext` iterates ground truth only, so
   hallucinated extra fields are invisible to it
3. **Duplicate rate on list fields = 0** — §3.2's structure-bound failure, invisible to F1
4. **Canary exposure single-digit at 1× and 5×**

nTED and leaf validity print alongside. **Do not gate on JSON parse-validity** — it runs 93.4–100%
across all models and does not discriminate.

Two gates cannot pass on the pilot alone, by construction, and that is expected, not a bug:

- **Gate 2 needs `--teacher-rate`**, which needs the hand-corrected holdout (human-in-the-loop 2).
- **Gate 4 needs `--canary-ranks`**; without it, it reports `FAIL (unmeasured)` rather than
  claiming a pass.

Report them as unmeasured. Do not invent a `--teacher-rate` to make gate 2 go green.

Expect to fail the phantom-fill gate on the first real pass and need a DPO round (§5).

---

# DONE LOOKS LIKE

**Artifacts, all under `~/.immich-memories-distill/validation/`:**

```
manifest.parquet          3000 rows, zero blank licence/author
labels.parquet            ~3005 rows, error rate < 5%, 5 canaries
labels.jsonl              the append-only WAL
dataset/train.jsonl       ~2730 samples, human share 0.45-0.55
dataset/validation.jsonl  400 held-out samples
dataset/sources.parquet   3000 rows, creator + licence columns populated
dataset/dataset_card.md   counts, sources, licences
```

**Report template — fill this in for the owner:**

```markdown
## Distillation pilot — run report

Ran: <date>  ·  Host: <machine>  ·  Teacher: scottlowry/Qwen3.8-27B-oQ4e-mtp

| Stage | Wall clock | Key output | Verification |
|---|---|---|---|
| A pull_corpus   | <t> | <n> images, <n> candidates | licence/author blanks: <n> |
| B0 gap_probe    | <t> | local vs <provider>/<model> on 200 | agreement <x> vs floor 0.95 → <verdict> |
| B teacher_label | <t> | <n> labels, <n> errors (<pct>) | median latency <s>s, median redactions <n> |
| C assemble      | <t> | <n> train / <n> holdout | human share <x> |
| E train (2B)    | <t> | adapters at <path> | tokens/iter <n> (need ~1760) |
| E train (0.5B)  | <t> | adapters at <path> | tokens/iter <n> |

### Gate table

| Gate | 2B | 0.5B |
|---|---|---|
| 1 micro-F1 (ceiling-adjusted) | <x> | <x> |
| 2 FP/predicted vs teacher | <x> | <x> |
| 3 duplicate rate | <x> | <x> |
| 4 canary exposure 1x/5x | <x> | <x> |

Decision rule says: ship <2B|0.5B|neither>, because <...>

### Blocked on the owner
- [ ] **teacher choice** (B0 numbers above; local 27B is the default)
- [ ] hand-corrected holdout (gate 2 has no bar without it)
- [ ] canary ranks (gate 4 unmeasured without them)
- [ ] anything publish-shaped

### Anything surprising
<...>
```

---
---

# REFERENCE (human-readable)

Everything below is background. The agent sections above are sufficient to run the pilot.

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
| HF `HuggingFaceTB/SmolVLM2-500M-Video-Instruct` → apache-2.0, ungated | ✅ 200 | 1.51M downloads |
| HF `mlx-community/SmolVLM2-500M-Video-Instruct-mlx` → apache-2.0, ungated | ✅ 200 | — |

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
- **SmolVLM2's HF-side module prefix** (`model.text_model` vs `model.language_model`) is
  transformers-version-dependent and was not verifiable today. `axolotl_smolvlm2_lora.yaml` uses a
  prefix-independent suffix list plus `freeze_mm_modules: true` to sidestep it.
- **Licence drift.** §4.1 measured 93.2% of 132 reachable landing pages still CC BY 2.0, ~3.8%
  moved to ARR or NC. The manifest snapshots the licence string, a retrieval timestamp and a
  content hash at download time, which is the defence; it is not a re-verification.
- **RunPod pricing** ($0.34/hr Community 4090) was verified 2026-08-30 and moves.

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

## Files

| File | Stage |
|---|---|
| `pull_corpus.py` | A — licence-clean corpus from Open Images V7 + CVDF mirror |
| `gap_probe.py` | B0 — local vs hosted teacher agreement, side-by-side, ends in a STOP |
| `teacher_label.py` | B — Qwen3.8-27B labels, scrubbed, canaried, resumable |
| `assemble_blend.py` | C — 50/50 human blend, SFT JSONL, dataset card, `sources.parquet` |
| `train_lora.md` | E — both venues, both students, exact commands, the token-count check |
| `axolotl_qwen3vl_lora.yaml` | E — path (b) config, primary student |
| `axolotl_smolvlm2_lora.yaml` | E — path (b) config, challenger |
| `eval_gates.py` | D — the four §7 gates |
| `distill_common.py` | shared: paths, parquet, licence filter, deterministic sampling |

Tests: `tests/test_distill_pipeline.py` — `uv run --with pyarrow python -m pytest tests/test_distill_pipeline.py`

# Phase 5: inference as a service, the way immich does it

Written against `docs/phase5-nas-first` (`151bcc64`), the tip of the story-first stack. Follows
[`2026-09-11-launch-readiness.md`](2026-09-11-launch-readiness.md), which found that a stranger
cannot complete a first cut. This plan decides *where* that machinery runs. The framing is the
owner's, and it discards the earlier "is a NAS CPU fast enough?" question:

> We built the onnx for its ability to run on Linux like immich does for their own ml models. It
> needs to be able to do the same and Linux must be a first class citizen, we can't release to
> users saying too bad it's only for Mac.

So **Linux is the shipping path, Apple/MLX is a convenience, and inference is a service with
hardware variants** — modelled on `immich-machine-learning`, which the owner already runs in
Kubernetes with a GPU. Three services, not one app:

| # | Service | Holds | Optional? |
|---|---|---|---|
| 1 | **main** | the app: Immich reads, selection, the store, the UI, the render | no — it is the product |
| 2 | **ML** | encoder + heads, the two detectors, the captioner | yes — see the reduced tiers (§5.A) |
| 3 | **encoder** | FFmpeg assembly on a GPU, when the main box has none | yes — topologies 1 and 2 never need it |

The reader (the 17 GB text/vision model) stays out of all three. It is a general-purpose VLM
people already run on Ollama, vLLM, oMLX or a hosted endpoint. Wrapping a model server we did not
write is not a service boundary, it is a fork.

**Everything below §1 rests on measurements taken on 2026-09-11**, on an Apple M5 Max and on the
owner's Synology DS423+, with `scripts/benchmark_preparation.py`. Where an earlier draft of this
plan guessed, the guess has been replaced; where a measurement contradicted it, the contradiction
is stated.

---

## 1. What preparation costs

### 1.1 How these numbers were taken

`scripts/benchmark_preparation.py` calls the product's own producer functions on real JPEG bytes
and times each one separately. One command re-takes every number:

```bash
# every producer; --encoder defaults to the path TriageConfig.encoder uses
uv run python scripts/benchmark_preparation.py --repeat 5 --provider cpu --json bench.json

# the captioner, against any endpoint advertising the alias
uv run python scripts/benchmark_preparation.py --stages caption \
    --caption-base-url http://host:8092/v1

# the torch-free detector, and the proof it decides the same
uv run python scripts/export_marqo_onnx.py --out /tmp/nsfw-marqo-384.onnx
uv run python scripts/benchmark_preparation.py --stages nsfw_marqo nsfw_marqo_onnx \
    --marqo-onnx /tmp/nsfw-marqo-384.onnx
```

- **Pictures**: the five distinct photographs already in `tests/fixtures/hdr_samples/` (MIT, from
  `NMoroney/Awesome-Gain-Maps`), 600×600 to 1599×1066. Real photographic content, already public,
  no library asset involved. Two of the fixtures are byte-identical; the harness drops duplicates,
  because a duplicate looks like a second picture to a loop and like a cache hit to a captioner.
- **Cold and warm are separated.** Every stage runs one discarded pass before the measured cycles,
  and model load is timed apart from steady state.
- **The caption stage reports the cold pass only.** This is not fastidiousness: `llama-server`
  caches image embeddings, so the second cycle over the same pictures came back at 6.0 s/picture
  against 30.9 s cold. A benchmark that averaged those would have reported a captioner five times
  faster than it is, and the tier table below would have been wrong.
- **The NAS is a shared box.** Its other containers push the load average to 5–7, and an
  identical measurement taken under load and quiesced differed by up to 2×. Every NAS number here
  is from a quiesced run (load average ≤ 2.3 at start); the numbers that moved are called out.
- **The two numbers the tiers turn on were re-taken hours later, and reproduced.** Machine A's
  producer table came back within 3 % overall, no stage off by more than 10 %. The NAS captioner,
  re-measured on a quiesced box after restarting the server to clear its cache, came back at
  **30.93 s/picture** against 30.9. The CoreML penalty in §1.6 was measured twice on purpose.
  The one figure not re-taken is the MLX caption: its endpoint was no longer running, and it is
  the owner's own service to start. It was measured in the same window as the producer table
  that did reproduce.

### 1.2 The two machines

| | A | B |
|---|---|---|
| Machine | Apple M5 Max, 128 GB | Synology DS423+ |
| CPU | 18 cores | Intel Celeron J4125, 4 cores @ 2.0 GHz |
| SIMD | NEON | **SSSE3, SSE4.1, SSE4.2 — and nothing above it.** No AVX, no AVX2, no F16C, no FMA |
| OS | macOS | Linux 4.4.302, Docker 24.0.2 |
| Runtime | project venv | `python:3.12-slim` container, `torch --index-url .../whl/cpu` |

Machine B is the magnifying glass. It is also the honest floor: a DS423+ is the NAS a self-hoster
most often already owns.

### 1.3 The per-picture table

Seconds per picture, warm, model already loaded.

**Read the scope labels before comparing anything.** Machine A's column is **tuned**
(`provider=cpu`, batch 8) because its shipped default is the slower CoreML path (§1.6); machine
B's column is **as shipped** (batch 32, ORT `cpu_count-1` = 3 threads, torch 6 threads) because
§1.8 found nothing to tune there. Every total row names its own scope, and a tuned number is
never set beside an untuned one without saying so.

| producer | A: M5 Max (tuned) | B: J4125 (as shipped) | B ÷ A |
|---|---:|---:|---:|
| preview reuse check (`Image.verify`) | 0.0000 | 0.0003 | — |
| pixel facts (`pixel-facts-v1`) | 0.0096 | 0.0549 | 5.7× |
| thumbnail aHash (OpenCV) | 0.0066 | 0.0195 | 3.0× |
| caption tile (400 px q90) | 0.0088 | 0.0497 | 5.6× |
| DINOv2 preprocess | 0.0071 | 0.0410 | 5.8× |
| **DINOv2 embed (ONNX Runtime)** | **0.0110** | **0.5259** | **48×** |
| six public heads | 0.0000 | 0.0175 | — |
| **`nsfw_marqo` (timm/torch)** | **0.0139** | **0.3962** | **29×** |
| **`doc_docling` (ONNX Runtime)** | **0.0059** | **0.1266** | **21×** |
| **sum: all producers, no caption** | **0.0629** | **1.2315** | **20×** |
| caption — graded MLX, serial | 0.171 | n/a (Apple-only) | |
| caption — graded MLX, concurrency 4 | 0.117 | n/a | |
| caption — SmolVLM2-500M Q8\_0 GGUF, `llama.cpp` | n/a | 30.9 | |
| **sum: all producers + caption** | **0.180** | **32.1** | *not a ratio — see below* |

Peak RSS for all producers in one process: 671 MB (A), 570 MB (B). Memory is not the constraint on
a 4 GB container; time is.

**The like-for-like comparison, same scope on both sides.** The two machines run different
captioners, so the only honest machine-to-machine ratio is the one that leaves the captioner out
of both columns — and it is the same 20× the per-stage column already shows:

| all producers, no caption, 10,793 pictures | A: M5 Max (tuned) | B: J4125 (as shipped) | B ÷ A |
|---|---:|---:|---:|
| per picture | 0.0629 s | 1.2315 s | 20× |
| **first pass** | **11 min** | **3 h 41 min** | **20×** |

Adding each machine's own captioner is a separate question with a separate answer (§1.4), and
32.1 against 0.180 is *not* a machine ratio: it is MLX on Apple silicon against `llama.cpp` on a
CPU with no AVX, which is two variables at once.

Two shapes hide in that table, and they are the whole story:

1. **Python-and-Pillow work is only 3–6× slower on the Celeron.** Decode, resize, hash, Laplacian
   variance — a slow core is just a slow core.
2. **Neural work is 20–50× slower.** That is not clock speed. A J4125 has no AVX of any kind, so
   every GEMM falls back to scalar or SSE4.2 kernels while the M5 Max runs wide NEON. The gap
   between these two shapes is the single most useful thing measured here, because it says
   exactly which seats can move to a small box and which cannot.
3. **The captioner is worse than either, but by an amount nobody should quote as a ratio.** 30.9 s
   against 0.171 s is `llama.cpp` Q8\_0 on a no-AVX CPU against MLX 4-bit on Apple silicon —
   different runtime, different quantisation, different silicon. What is safe to say is the
   absolute number, and it is 30.9 s.

### 1.4 What one library costs

At the 10,793 candidates one scope of the owner's library reported, and per 1,000 pictures for
arithmetic on any other library. Preparation is banked per picture, so these are **first-pass**
costs; a rerun over prepared pictures pays only the preview reuse check.

Every row names its machine, its scope and whether it is tuned, so no two rows can be read as a
ratio unless they say the same thing on both counts.

| machine | scope | settings | per 1,000 | 10,793 | pictures/day at 24 h |
|---|---|---|---:|---:|---:|
| A — M5 Max | producers only | tuned | 1.0 min | **11 min** | 1,373,000 |
| A — M5 Max | producers + MLX caption | tuned | 3.0 min | **32 min** | 480,000 |
| A — M5 Max | producers + MLX caption | **as shipped today** | 4.3 min | **46 min** | 336,000 |
| B — J4125 | producers only | as shipped | 21 min | **3 h 41 min** | 70,000 |
| B — J4125 | producers + GGUF caption | as shipped | 8 h 55 min | **96 h ≈ 4 days** | 2,690 |

The last two rows are the decision, and they differ in one variable only — the captioner, on one
machine. Encoder, heads and both detectors on a DS423+ finish a whole library **overnight**.
Adding captions turns that into four days. Nothing else in this plan matters as much as that one
line.

And the steady state is not the first pass. A family that adds 50 pictures a day needs 25 minutes
a day of NAS captioning, which is nothing. **The first pass is the problem, not the rate.**

### 1.5 The caption verdict

**`llama.cpp` runs on a CPU with no AVX at all, and the product's graded wire fits it unchanged.**
No source build was needed. The prebuilt `ghcr.io/ggml-org/llama.cpp:server` image (build 10902,
commit `df03399b8`) ships fifteen CPU backend variants — including `libggml-cpu-sse42.so` — and
selects one at runtime. Its own banner on the DS423+:

```
system_info: n_threads = 4 (n_threads_batch = 4) / 4 | CPU : SSE3 = 1 | SSSE3 = 1 |
             LLAMAFILE = 1 | OPENMP = 1 | REPACK = 1 |
```

No AVX line at all. Serving `ggml-org/SmolVLM2-500M-Video-Instruct-GGUF` Q8\_0 (437 MB) with its
Q8\_0 `mmproj` projector (109 MB) and `--alias smolvlm2-500m-base-public`:

- `GET /v1/models` advertises the alias, so `check_provider` is satisfied;
- `response_format: {"type": "json_schema", …}` is honoured — grammar-constrained decoding cut a
  description at exactly the schema's 120 characters;
- the non-standard `repetition_penalty` field in our payload is accepted, not rejected with a 400;
- every response validated against `validate_envelope` on the first attempt.

So `editorial_description_wire.request_payload` does not change one byte, and **the Linux caption
path exists.** What it costs is the problem: **30.9 s per picture**, of which ~25 s is prompt
evaluation (the vision tower — 188 prompt tokens) and ~5 s is generation (~50 completion tokens).
Two consequences follow directly. `check_provider` sends three synthetic control tiles before any
library picture, so every run starts with 93 s of warm-up. And because five sixths of the cost is
the image encoder, **quantising the text weights attacks the small half**, which the next section
measures rather than assumes.

Output quality is comparable to MLX and differs in wording, as expected from a different numeric
path. From the public fixtures, same picture, same prompt:

| | description | setting |
|---|---|---|
| MLX (graded) | "A bustling night-time street filled with neon signs and people going about their day." | `cityscape` |
| GGUF Q8\_0 | "A bustling night scene in Japan's neon-lit streets." | `insufficient evidence` |

The descriptions are peers. The `setting` field is not: across the fixtures GGUF hedged to
`insufficient evidence` where MLX named a scene type. `setting` feeds editorial context, so that
difference is exactly what W11's grading has to judge — it is a behaviour change, not noise.

### 1.6 The knobs, measured

| knob | effect | verdict |
|---|---|---|
| **ORT provider on macOS** (`auto` → CoreML, today's default) | DINOv2 embed **0.0110 s on the CPU EP vs 0.0885 s on CoreML**, and **0.0121 vs 0.0766** on a second run hours later: **6–8× worse**, both times. Session load 0.08 s vs 0.68–0.76 s, and **peak RSS 314 MB vs 2,856 MB** — 9× the memory for 6× the latency. CoreML claimed 274 of 513 nodes and got slower at every batch size (1 → 0.068, 16 → 0.094 s) while the CPU EP got faster (1 → 0.020, 16 → 0.012 s). Head labels identical either way. | **Fix it.** This is a live pessimisation on every Mac today, and the only finding here measured three times on purpose. |
| caption concurrency, machine A (MLX) | 1 → 0.173, 2 → 0.127, 4 → 0.117 s/picture | The config default of 4 is right here: 1.5×. |
| caption concurrency, machine B (`llama-server`) | 1 → 30.9, 4 → **34.2** s/picture, p90 41 s | **Harmful.** `llama-server` runs one slot by default, so concurrency only queues, then pays for the queueing. The default of 4 must become a per-endpoint setting. |
| caption quantisation, machine B | Q8\_0 **30.9 s**, Q4\_K\_M **61.5 s**, f16 562 s for a single request | **Q4 is twice as slow as Q8, and worse text.** Without AVX2/F16C the K-quant unpack costs more than it saves, while `REPACK` favours Q8\_0. Q4 also degraded the output ("a simple, yet effective, way to create a grid of circles in a single line of code"). Smaller is not faster here. |
| batch size | Machine A CPU EP: 1 → 0.0199, 16 → 0.0119 (1.7×). Machine B: 1 → 0.577, 16 → 0.504 (13 %). | Worth having on a fast machine; nearly inert on a slow one. Today's default of 32 is fine. |
| ORT thread count, machine B | 1 → 1.399, 2 → 0.721, **3 → 0.522**, 4 → 0.531 s/picture | The encoder's existing `min(8, cpu_count-1)` already picks 3. **No win available.** |
| torch thread count for Marqo, machine B | 3 → 0.417, 4 → 0.461, 6 → 0.404, and 0.396 in the full run | The hardcoded `6` happens to be fine on four cores. **No win available**, which is not the same as "well chosen" — see W2. |
| `nsfw_marqo` through ONNX instead of torch | Machine B 0.469 s (ONNX) vs 0.440 s (torch) at matched threads. Machine A 0.0187 vs 0.0139. | **No speed win. Do it anyway** — for what it removes, see §5.2. |
| int8 dynamic quantisation of the encoder | Machine B 0.542 s vs 0.713 s — 24 % off the largest line item. Machine A: **worse** (86 ms vs 64 ms). Pooled packs drift by up to 3.08 absolute. | **Refuse.** A quantised encoder is a different artifact: new digest, new `encoder_key`, and the six heads are bound to the key they were trained on. 24 % on one machine does not buy a re-graded bundle. §3 says why this rule exists. |
| detector process start-up, machine B | `import numpy` 2.4 s, `import cv2` 3.0 s, `import onnxruntime` 2.9 s, `import torch` 6.4 s, `import timm` **11.1 s** (container start included) | A CLI run pays ~11 s before its first detector picture. Real, but 11 s against a 3 h 42 min first pass: **do not sell the service on start-up amortisation.** |

### 1.7 The trap in the knob numbers

The first thread and batch sweeps on the NAS said 2 ORT threads beat 3 (1.26 vs 1.28 s/picture)
and that batch size barely mattered. Both were artefacts:

- they ran while the box was loaded, and the same configuration re-measured quiesced came back at
  0.53 s/picture — the load, not the knob, was doing the talking;
- with five distinct pictures, "batch 8" and "batch 32" both mean *one batch of five*. The sweep
  was comparing a setting with itself.

Re-measured quiesced, over the same real pictures repeated to fill 32 rows — identical GEMM work,
enough rows for batching to mean something — 3 threads beats 2 by 1.38× and the whole batch curve
is flat (§1.8). Both of the original conclusions were wrong, and the second was wrong about a
comparison that was never made. The lesson is general and belongs in the harness, not in a
footnote: **a benchmark on a shared box measures the box, and a sweep needs more rows than
settings.**

### 1.8 Threads and batch on four slow cores

*(DINOv2-small, ONNX Runtime, machine B, quiesced; see `scripts/benchmark_preparation.py`.)*

| ORT threads (batch 8) | s/picture | | batch (3 threads) | s/picture |
|---:|---:|---|---:|---:|
| 1 | 1.399 | | 1 | 0.577 |
| 2 | 0.721 | | 2 | 0.533 |
| **3** | **0.522** | | 8 | 0.530 |
| 4 | 0.531 | | **16** | **0.504** |

**Both knobs are already set correctly by the product, and neither is a lever.** Threads 1 → 2
is worth 1.94×, 2 → 3 another 1.38×, and 3 → 4 nothing at all — the encoder's existing
`max(1, min(8, cpu_count - 1))` lands on 3 of its own accord. Batch size spans 13 % across a
32× range, and the shipped default of 32 is within 1 % of the best.

That is the real finding about tuning a Celeron: **there is nothing to tune.** The 48× gap to an
M5 Max in §1.3 is the instruction set, not a setting, and no amount of knob-turning reaches it.
The levers that did move something are all elsewhere — the provider on macOS, the caption quant,
the caption concurrency, and removing torch from the image.

### 1.9 Work done more than once

One JPEG decode to RGB costs **0.0036 s** on machine A and **0.0192 s** on machine B. Preparation
decodes each preview **six times**: pixel facts, thumbnail hash, caption tile, DINOv2 preprocess,
and once per detector inside the worker process (`_open_previews` re-opens the file per head).

Hoisting five of those six saves 0.017 s/picture on machine A — **27 % of the non-caption
producer cost** — and 0.096 s on machine B, which is 8 % of its non-caption cost and invisible
next to its captioner. The direction is the opposite of intuition: *shared decoding is a fast
machine's optimisation.* On a slow machine the models swamp it.

---

## 2. The four topologies

| # | Topology | Main | ML | Encoder | Reader | Needs | First pass, 10,793 pictures |
|---|---|---|---|---|---|---|---|
| 1 | **Laptop, all-in-one** | laptop | same host | none (VideoToolbox/NVENC in-process) | same host | Apple Silicon 32 GB+ (the only graded configuration) or amd64 + 24 GB GPU | **32 min** measured on an M5 Max, captions included |
| 2 | **NAS, all-in-one** | NAS | same host, `cpu` variant | none (software x264/x265) | hosted, or a reduced tier | a DS423+ is a J4125: 4 cores, SSE4.2, no AVX | **3 h 42 min without captions**; 4 days with them (§5.A) |
| 3 | **NAS + offload** | NAS | GPU box or k8s, `cuda` variant | same GPU box | anywhere | a reachable Service, plus the NetworkPolicy fix in W9 | unmeasured — no CUDA host available to this pass |
| 4 | **Hosted seats** | anywhere | our own image behind a URL | optional | any OpenAI-compatible endpoint | an explicit consent gate (§5.5) | reader at 2026-09 prices: EUR 0.02–0.12 per render |

Quick Sync, VAAPI and NVENC decode, scale and encode. **They do not run inference.** That is true
on every page in the docs today and must stay true. OpenVINO could in principle use the UHD 600 in
a DS423+ for the ONNX seats; on a 12-EU Gemini Lake iGPU it is not worth the engineering, and this
is the one time this plan says so.

---

## 3. What we copy from immich-ml, and what we do not

Read from the owner's checkout: `machine-learning/Dockerfile`, `docker/hwaccel.ml.yml`,
`machine-learning/app/{main,config,schemas}.py`, `app/models/base.py`, `app/sessions/ort.py`,
`start.sh`, and their `docker-compose.yaml` (`immich_remote_ml`, port 3003, `model-cache:/cache`).
Reference material, cited not copied — their licence is theirs.

| Their choice | Us | Why |
|---|---|---|
| `ARG DEVICE=cpu`, `builder-${DEVICE}` → `prod-${DEVICE}` → `FROM prod-${DEVICE} AS prod` | **copy** | One Dockerfile, N images, no drift between variants. Exactly the shape we need. |
| Image tag suffixes (`:v1.122.2`, `:v1.122.2-cuda`) | **copy** | Users already read immich tags this way. Ours: `:X.Y.Z`, `:X.Y.Z-cuda`. |
| `hwaccel.ml.yml` with `extends:` per backend, `cpu: {}` and a `deploy.resources.reservations.devices` nvidia entry | **copy** | Their file is the documented way to attach a GPU in compose. Ours is `docker/hwaccel.inference.yml`. |
| Dependency groups selected by `--with ${DEVICE}` | **copy** (as uv extras) | This is what keeps 2.5 GB of CUDA wheels out of the CPU image — see §5.2, which now has a measured number for what the CPU image costs instead. |
| Runtime model download into a `/cache` volume; nothing baked | **mostly copy** — see §5.3 | Solves launch-readiness 4.1: the service fetches and verifies the encoder, the app never has to. |
| ORT provider list resolved from `ort.get_available_providers()`, in a declared preference order | **copy, with a measured order** | Our `_create_session` hardcodes CoreML-or-CPU and raises on anything else — and §1.6 shows CoreML is the *slower* of the two for this graph. Preference order is a measurement, not a ranking of brand names. |
| `model_ttl: 300` idle unload | **copy the unload** | A NAS cannot hold the captioner resident next to a render. |
| Idle suicide: `os.kill(os.getpid(), SIGINT)` after the TTL | **do not copy** | A restart loop on a NAS is worse than a resident 200 MB process. Unload the weights, keep the process. |
| `/ping` → `pong`, `HEALTHCHECK CMD python3 healthcheck.py` | **copy** | Six lines, works everywhere, no curl in a slim image. |
| pydantic-settings with an env prefix (`MACHINE_LEARNING_`) | **copy** | Ours: `IMMICH_MEMORIES_INFERENCE_`. |
| gunicorn + a custom uvicorn worker, thread pool in front of blocking ORT | **copy** | asyncio in front of ORT is a bottleneck; they wrote the comment, we get it free. |
| `PipelineRequest` with a `depends` graph and two `asyncio.gather` waves | **do not copy** | Our three pixel producers are independent. A DAG for three leaves is over-structure. |
| multipart `entries` JSON + `image` file part | **do not copy** | Our caption client already posts base64 JSON. One wire format in the repo, not two. |
| ARM NN stage: `/dev/mali0`, `libmali.so`, per-chipset firmware; and ROCm | **do not carry** | Per-SoC driver binding we cannot test, and no AMD hardware to test on. |
| OpenVINO stage: Intel compute-runtime `.deb`s, `/dev/dri`, cgroup rule `c 189:* rmw` | **not at launch** | See §2 on UHD 600. Revisit if an Arc-class iGPU user asks. |
| `MACHINE_LEARNING_WORKERS > 1` + mimalloc `LD_PRELOAD`; `clean_name()` slugs and `immich-app/*` HF repos | **do not copy** | One worker and our own batching; our artifacts are digest-pinned, not name-resolved. |

---

## 4. The service contract, and the key-stability rule

One image, one port. **8092**, because `caption_base_url` already defaults to
`http://localhost:8092/v1` and that string is in the docs. Two path namespaces:

| Endpoint | Question it answers |
|---|---|
| `GET /ping` | are you up |
| `GET /health` | which producers are loaded, at which versions, and the `encoder_key` you would return |
| `POST /facts` | here is one picture — what do the frozen classifiers say about it |
| `GET /v1/models`, `POST /v1/chat/completions` | **unchanged** caption wire, alias `smolvlm2-500m-base-public` |

`POST /facts` takes `{"image": "<base64 jpeg>", "producers": ["heads", "nsfw_marqo", "doc_docling"]}`
and returns each producer's facts, its `version`, and the `encoder_key`. The caption wire does not
change one byte: `editorial_description_wire.request_payload` is graded, its `request_sha256` is
banked as evidence, and §1.5 confirms `llama-server` accepts it as-is. Leave it alone.

> **Every producer key is content-addressed over the artifact, never over where it ran.**

`encoder_key` is already `sha256(encoder_id + weights_sha256 + preprocess_version + layout_version)`
(`triage/encoder.py:35`). It stays exactly that — computed on the service, returned on the wire,
stored by the client as returned. No URL, hostname, device or execution-provider name enters any
key, so the same picture through the `cpu` and `cuda` containers lands on one bank row.

**Device is operational, never identity — and there is now evidence for the "operational" half and
a caveat on it.** The same encoder run through the CoreML and CPU execution providers gave pooled
packs differing by up to 5.5e-3, and *identical* labels on all six heads across the fixtures. So
provider selection does not re-key, and must not. But note what that measurement also says: facts
are **label-identical, not byte-identical**, across providers. W4 and W8 below have their exit
tests corrected accordingly — an exit test that demands byte-identical confidences across devices
is one nobody can pass.

**Quantization *is* identity**, and §1.6 is the proof of why the rule matters: an int8 encoder
moved pooled packs by 3.08 absolute. It is a different artifact and must carry a different key.
The same applies to a GGUF captioner (§5.4). **The counter-example is already in the tree**:
`text_model_identity` (`analysis/llm_text_identity.py:18`) hashes `resolved.base_url` into every
text bank's producer key, so the same model at two URLs misses every bank — a known defect, not
fixed here (§8), and **nothing in the inference service may imitate it.**

---

## 5. Decisions

### 5.1 The service boundary

**Recommendation: the ML service holds the three pixel seats — encoder+heads, the two detectors and
the captioner — in one image on one port. The reader stays outside.**

Those three share a cache volume, a device variant, a lifecycle and an idle-unload policy;
splitting them into three containers triples a NAS owner's ops surface to save nothing. The
captioner keeps its own `/v1` namespace because its wire is graded and its client is already
written — a second dialect inside one process, not a second service. The client side gains
`inference.facts_base_url`; blank keeps today's in-process path, so topology 1 needs no container.

One correction from §1.6: the captioner needs its own **concurrency** setting, not the shared
`caption_concurrency: 4`. Four concurrent requests are worth 1.5× against an MLX server and
**cost 11 % against a single-slot `llama-server`**. The service knows which backend it runs; the
client should not have to.

### 5.2 Device variants

**Recommendation: ship `cpu` (linux/amd64 + linux/arm64) and `cuda` (linux/amd64) at launch.
`openvino`, `armnn` and `rocm` do not ship.**

The `cpu` variant must not contain a CUDA wheel. Today the `editorial` extra on linux/amd64 is
**3.05 GB**, of which 2.5 GB is fifteen `nvidia-*` wheels dragged in by torch, for two detectors
that hardcode `CPUExecutionProvider` and `torch.set_num_threads(6)`. Measured on the DS423+: a
`python:3.12-slim` image with numpy, Pillow, OpenCV, ONNX Runtime and pydantic is **700 MB**;
adding CPU-only torch, torchvision and timm takes it to **1.62 GB**. So the torch family costs
**920 MB in the CPU image** and 2.5 GB in the CUDA one, to run one 5.6 M-parameter classifier.

**Be clear about what that argument is and is not.** Torch runs perfectly well on a CPU with no
AVX — established separately, 50 matmuls of 256x256 in 0.18 s on the J4125 — and §1.3 times the
torch detector at 0.396 s a picture there, a real number from a working model. So dropping torch
(W3b) is an argument about **image size, install fragility and start-up**, never about whether the
Celeron can run it. Nothing here rests on a portability fear, and the measurement in §1.6 is what
settles the speed half: there isn't one.

**This split has a live bug in it, found while building that image.** The `editorial` extra
declares `onnxruntime`, `torch`, `timm` and `huggingface-hub` — and *not* `torchvision`, which
timm needs. Install torch from `download.pytorch.org/whl/cpu` and let pip resolve timm's
`torchvision` from PyPI, which is what the documented Linux install does, and you get a
torchvision built against a different torch. `nsfw_marqo` then dies at load with:

```
RuntimeError: operator torchvision::nms does not exist
```

Not a slow detector — a missing one, on the exact install path a Linux user is told to follow.
`torchvision` must be declared in the extra and pinned to the same index as torch. That is W0,
and it is a bug fix, not a size optimisation.

### 5.3 Model delivery

**Recommendation: bake what is ours and tiny, fetch what is large, verify everything.**

| Artifact | Size | Delivery |
|---|---|---|
| Head bundle `public-6heads-v3.npz` | 2.2 MB | **baked** — already in the wheel, ours, MIT |
| DINOv2-small ONNX export | 88 MB | **fetched** into `/cache`, digest-verified against `478164cd…` |
| Marqo snapshot | 21 MB | **fetched** at its pinned revision — or replaced by our own 22.5 MB ONNX export (W3b) |
| Docling ONNX | 16 MB | **fetched** at its pinned revision |
| Caption weights, GGUF Q8\_0 + `mmproj` Q8\_0 | 437 MB + 109 MB | **fetched** into `/cache`, digest-verified |

An earlier draft of this plan put the two detector snapshots at "~400 MB". They are **37 MB
together**. The captioner is the only large fetch, and only on tiers that have one.

### 5.4 The captioner on Linux — answered

The only graded caption weights are `mlx-community/SmolVLM2-500M-Video-Instruct-mlx@fa57db46`.
MLX is Apple-only.

**Recommendation: serve the same SmolVLM2-500M generation as GGUF under `llama.cpp`'s
`llama-server`, inside our image, keeping the alias. This is now measured, not proposed (§1.5).**

- The prebuilt image runs on a CPU with **no AVX of any kind**; no source build, no custom cmake
  flags, no per-CPU image. That was the open risk and it is closed.
- json-schema-constrained output, the alias, and our exact request body all work unchanged.
- Q8\_0 is the quant to ship. Q4\_K\_M is **2× slower and worse**, f16 is 18× slower.
- ONNX Runtime GenAI would need our own multimodal export of a model it does not support. No.
- vLLM needs a GPU and a 2 GB dependency tree. Topology 3 only; no help to a NAS.
- A third-party hosted captioner is **impossible**, not merely undesirable: the contract pins an
  alias no commercial provider will advertise. The hosted caption story is our own image behind a
  URL (§5.5), not someone else's API.

**The re-grading cost, unchanged from the earlier draft and now with a reason to care.**
`DESCRIPTION_MODEL` is `smolvlm2-500m-base-public@envelope-v3-compact`, which encodes no
quantization, so two backends would write into one bank and nothing would notice. So: the existing
token is **grandfathered to mean MLX `fa57db46`**, the owner re-captions nothing, and the GGUF
backend gets its own token carrying the quant and the weights digest. §1.5 shows the two backends
agree on `description` and diverge on `setting`, which is precisely the kind of drift a shared
token would bury.

**What it does not change: on a J4125 this captioner is 30.9 s a picture.** Proving Linux captions
work does not make them affordable on a NAS. That is §5.A.

### 5.5 Hosted inference

**Recommendation: the reader may be hosted third-party. The pixel seats may only be hosted on our
own image. Both sit behind one explicit per-destination consent gate.**

What leaves the machine differs sharply by seat. Captions send 400 px tiles (median **41 KB**
measured) and pixel facts send preview-derived tensors, both without metadata; the reader gets
800 px tiles **and** annotation lines carrying real people's names and place names — and nothing
about editing a `base_url` tells a user that. So the gate is a first-run opt-in per destination
host naming the seats going off-box and what each sends, not a config field that quietly starts
uploading a family album. The run records the host, which seats went remote, the asset count and
the consent version, beside the existing model attestation. No names, ids or album titles in that
record.

### 5.6 Kubernetes

**Recommendation: mirror the owner's immich-ml deployment — the ML service is its own Deployment
with its own PVC and its own device reservation.**

- `base/` gains `inference-deployment.yaml`, `inference-service.yaml` and `inference-pvc.yaml` (the
  model cache, RWO). CPU by default, boots on any cluster.
- `overlays/gpu/` today patches the **app** pod with `runtimeClassName: nvidia` and
  `nvidia.com/gpu: 1`, for NVENC. That stays and stops being the only GPU story: a new
  `overlays/inference-gpu/` patches the inference Deployment the same way and moves it to the
  `-cuda` tag. The two are independent — that is topology 3.
- **`networkpolicy.yaml` is a live bug.** Egress allows 53, 80, 443, 2283 and 11434; port 8092, our
  own documented caption default, is blocked by our own shipped policy. Add egress to the inference
  Service and ingress on the inference pod selecting the app pod's labels. (`base/kustomization.yaml`
  also still pins `newTag: "0.70.0"`.)

### 5.7 "Prepare elsewhere", and whether `prepare` is in scope

The measured portability finding stands and is not re-derived: the annotation store holds no paths
and travels, captions and head facts transfer unconditionally, **but** the reader's endpoint URL is
hashed into every text bank key (§4), the thumbnail cache and three sidecar SQLite files must
travel too, and there is no prepare-only entrypoint — preparation runs inside `generate`
(`editorial_runtime.py:480`).

**Recommendation: a minimal `prepare` command is in scope; store portability is not.** With an
inference service, "prepare elsewhere" mostly stops being something you do by moving files — you
point at a GPU service instead, which is topology 3 and a better answer. What remains is exercising
preparation without rendering, and §1 already shows why that matters: on a NAS, preparation is a
multi-hour job that a user must be able to start, watch and resume without asking for a film.
`immich-memories prepare --scope <period>` runs the preparation stage, prints seconds per picture
per producer, and stops. S, not L.

---

### 5.A The reduced tiers — and which one a NAS actually gets

The earlier draft offered one reduced tier: metadata only, no ONNX and no LLM at all. §1.4 says
that was one tier too few, and that the interesting line is drawn in a different place.

| Tier | What runs | First pass on a DS423+ | What the reader sees |
|---|---|---|---|
| **Full** | pixels, encoder + six heads, both detectors, captions | **4 days** | everything |
| **No captions** ← *the NAS tier* | pixels, encoder + six heads, both detectors | **3 h 42 min** | facts instead of sentences; the audience gate keeps all its evidence |
| **Metadata only** | pixels and Immich metadata; no ONNX, no LLM | minutes | facts only, family-viewing only |

**Recommendation: a DS423+-class NAS ships the no-captions tier by default, not metadata-only.**

That is the measured line. Encoder, heads and detectors cost 1.23 s a picture and finish a library
overnight; the captioner costs 25× the rest of the pipeline put together. Cutting captions removes
96 % of the cost and keeps everything the audience gate reads — `nsfw_marqo`, `swim`, `children`,
`exposure`, `doc_docling`. Cutting the models as well removes another 4 % and costs the gate its
evidence. One of those trades is obviously right and the other is obviously wrong.

**Captions on the NAS become an opt-in backfill, not a blocker.** They are banked per picture and
the pipeline already tolerates their absence. So: offer them as a background job with the rate
stated in the user's own units — *"about 2,700 pictures a day on this machine; your library will
take four days"* — and let the first cut proceed without them. That is a supportable feature. A
progress bar that sits for four days before anyone sees a video is not.

**What the no-captions tier loses, in what the user sees.** No descriptions, so every reason under
a picture becomes a fact rather than a sentence — the rule reader's form already does this:
`"<story title>: <n> pictures at <place>"`. No thesis. Weight badges survive: weights come from
counts and spread, not from the model. One line under the title: *"Edited without descriptions —
picture content was classified, not read."*

**What the metadata-only tier loses, and why it still exists.** Everything above, plus all six
heads and both detectors — and with them **sensitive-content and document detection.** It survives
as the tier for a machine that cannot run ONNX at all, or a user who refuses to. Verified in the
tree, it still has: dates, GPS, favourites, albums, EXIF, asset type, duration and Immich's own
face recognition; the visibility gate (`source_filter.not_on_the_timeline`); the provenance filter
that drops forwarded and re-encoded material by EXIF camera make, filename pattern and short side
(`source_filter.from_the_camera_roll:197`) — a screenshot filter without a model; the pixel facts
and their self-calibrating blur cut from `pixel_facts_thresholds.sharpness_p10`; near-duplicate and
burst buckets from `editorial_thumbnail_hashes`; live still-vs-clip from `editorial_motion_facts`;
and the whole deterministic downstream.

**Recommendation on sharing and export, unchanged.** The standing rule is that the gate may only
ever tighten. The metadata-only tier must therefore **default every unit to `family_only` and
refuse a `sendable` export outright** — not "share, because nothing objected", which inverts the
gate. The no-captions tier keeps `nsfw_marqo` and the exposure evidence, so it keeps the normal
gate. This is the sharpest practical reason to prefer it.

**Does the occasion door still work?** Yes in both, and this is why they are defensible. Occasion
discovery was designed on metadata mass and away-from-home blocks, with stars as indicators and
never admission gates — exactly the machinery that survives. The acceptance bar (losing good
pictures is fine, losing an occasion is not) stays **testable** here.

**Ship them?** Yes, as named tiers and never silent fallbacks, stated on the Memory page.

---

### 5.B The GPU encoder service

> "if main service has a gpu, use it, if not, we offload to a gpu"

The in-process half already exists and is good: `processing/hardware.py` probes by **actually
encoding a 64×64 `testsrc` frame** (`_probe_ffmpeg_encode:123`) rather than trusting
`ffmpeg -encoders`, which advertises NVENC on every GPU-less Docker host (#343), and the NVENC,
QSV, VAAPI and VideoToolbox paths are all there. The service is only the *remote* case of a
decision the code already makes.

**Material — recommendation: the main service ships the clips it already downloaded; the encoder
service never talks to Immich and holds no credential.** The alternative, an encoder pulling with
its own API key, means a second credential with access to the whole library plus a duplicate of the
visibility gate that lives in the app. Transfer does not save it: a render's material is roughly
200–800 MB, which on a 1 Gbit LAN is 2–7 seconds against minutes of encode.

**Contract: a render plan in, a film out.** Not a filter graph. `VideoAssembler` builds its graph at
run time and FFmpeg majors differ across hosts — Homebrew and macOS CI ship 9 (no
`-filter_complex_script`), Ubuntu 24.04 ships 6 (no `-/filter_complex`) — so a graph built on one
and run on the other is a latent break. The plan carries clips, timings, titles, audio and target
codec; **the service builds its own graph with its own FFmpeg** and reports its major in `/health`.
Upload to Immich stays with the main service: writes belong to whoever holds the credential.

**Fallback.** `render.encoder_base_url` blank means local — today's behaviour unchanged. Configured
but unreachable, or failing, **falls back to local software encoding with a warning**.

**Key stability? No**, and saying so matters. Nothing about a film is banked, so there is no
producer key to protect. Its discipline is pinning the FFmpeg major per image and recording which
encoder produced each film.

**Still honestly optional, and still undecided on evidence.** This pass measured preparation, not
rendering: the DS423+ render timing was not taken, and §9 says so. Two things are known and worth
recording for whoever takes it: the NAS exposes `/dev/dri/renderD128`, so a container with a modern
FFmpeg may reach the UHD 600's *media* engine even though the host's own FFmpeg 4.1.9 has no VAAPI
encoders; and inference has no claim on that engine either way (§2). Last built, first cut.

---

## 6. Work items

Reordered so that the items §1 showed to be bugs come before the items §1 showed to be nice.

| # | Item | Size | Exit test |
|---|---|---|---|
| **W0** | **Declare `torchvision` in the `editorial` extra, from the same index as torch.** §5.2: without it `nsfw_marqo` dies with `torchvision::nms does not exist` on the documented Linux install. | XS | A clean `linux/amd64` container installing the extra from the CPU index loads `Marqo` and decides a picture. |
| **W1** | **Provider selection by measurement, not by name.** `triage/encoder.py` `_create_session` picks CoreML whenever it is available; §1.6 measures CoreML at 6–8× the CPU EP for this graph and at 9× its resident memory. Add a `provider` field to `TriageConfig` (it has none), default to the CPU EP on macOS, resolve CUDA from `ort.get_available_providers()` where present. | S | On a Mac the default session is `CPUExecutionProvider`; on a CUDA host, `CUDAExecutionProvider`; same input → same `encoder_key` and the same head labels on all three. |
| **W2** | **Thread counts stop being constants.** `torch.set_num_threads(6)`, `intra_op_num_threads = 6` and `OMP_NUM_THREADS=6` are hardcoded for a machine nobody has, while `_create_session` derives its own from `os.cpu_count()`. One setting, honoured by every seat. **Honest ranking: §1.8 found no win on either machine measured** — 6 is harmless on 4 cores and the encoder's derived 3 is already optimal. This is hygiene and a lever for hosts nobody has tried, not a speed-up. | S | Setting it changes what the sweep in §1.8 measures; leaving it unset reproduces today's numbers. |
| **W3** | Split the `editorial` extra by device: CPU torch index for `cpu`, CUDA wheels only for `cuda`. | S | `docker buildx build --platform linux/amd64` produces a `cpu` image with zero `nvidia-*` wheels. Measured target: the non-torch base is 700 MB and the full CPU image 1.62 GB (§5.2). |
| **W3b** | **Export the Marqo detector to ONNX and drop the torch family.** `scripts/export_marqo_onnx.py` already does it: a 22.5 MB single-file graph, every label agreeing with torch across the fixtures (max probability delta 1.19e-7 with timm's transform, 1.01e-3 with the torch-free numpy transform in the same script). | M | Label agreement on a held-out sample; the `nsfw_marqo` fact version bumps and re-derives. **Justified by size and dependency hygiene, not speed** — §1.6 measured ONNX at 0.469 s vs torch at 0.440 s on the NAS. What it buys is 920 MB, 11 s of start-up, and W0's whole class of bug. |
| **W4** | The service: `services/inference/` with `/ping`, `/health`, `/facts`, pydantic-settings under `IMMICH_MEMORIES_INFERENCE_`, a thread pool in front of ORT, idle unload without idle suicide. | M | `/facts` on a fixed picture returns **the same labels and versions** as today's in-process path, and the same `encoder_key`. (Not byte-identical confidences — §4 explains why that test cannot be passed across providers.) |
| **W5** | `docker/Dockerfile.inference` with `ARG DEVICE`, `builder-${DEVICE}` / `prod-${DEVICE}`, CUDA runtime base for `cuda`. | M | Both variants build; `:X.Y.Z` and `:X.Y.Z-cuda` publish; the cuda image loads a CUDA EP session. |
| **W6** | `docker/hwaccel.inference.yml` (`cpu: {}`, `cuda:` reservation) and an inference service in `docker-compose.yml`. | S | `docker compose up` on a GPU host with the `cuda` extends block gives a service whose `/health` names a CUDA provider. |
| **W7** | Model cache + runtime fetch inside the service: encoder digest-verified, detector snapshots at pinned revisions, optional boot preload. Share the module with `models fetch`. | M | Cold container, empty volume, one `docker compose up`: `/health` reports every producer loaded and the encoder digest matches. Closes launch-readiness 4.1 and 4.2. |
| **W8** | Client side: `inference.facts_base_url`, blank = in-process. Store the `encoder_key` and versions the service returned, verbatim. | M | The same library prepared in-process and via the service produces the same labels in the same bank rows. |
| **W9** | Kubernetes: inference Deployment + Service + PVC in `base/`, `overlays/inference-gpu/`, **NetworkPolicy egress to the inference Service and ingress from the app pod**, kustomize tag bump. | M | `kubectl apply -k base` on a CPU cluster completes a cut; `-k overlays/inference-gpu` runs the same cut with the ML pod on a GPU node. |
| **W10** | **The captioner in the service**: bundled `llama-server` child, GGUF **Q8\_0** fetched to the cache, `--alias smolvlm2-500m-base-public`, json-schema output, proxied under `/v1`. New bank token for the GGUF artifact; the MLX token grandfathered. A per-backend concurrency setting (§5.1). | L | `check_provider` passes all three schema controls unchanged; a GGUF-captioned asset and an MLX-captioned asset occupy distinct bank rows. Most of the risk is already retired by §1.5. |
| **W11** | Grade the GGUF captioner: one route, owner's eyes, against the approved plan. §1.5 flags `setting` as the field most likely to have moved. | M | Either it is approved and becomes the Linux default, or Linux ships the no-captions tier and the docs say why. No third outcome. |
| **W12** | `immich-memories prepare --scope <period>`: run preparation, print seconds per picture per producer, stop. | S | On a cold scope it prepares and exits 0 without rendering; its numbers agree with §1.3. |
| **W13** | Consent gate for off-box destinations: per-host opt-in naming the seats and their payloads; record host, seats, asset count, consent version. | M | Pointing the reader at a remote host without consent refuses with a message naming what would be sent. The record contains no names, ids or album titles. |
| **W14** | **The reduced tiers** (§5.A): tolerate every absent producer; the no-captions tier keeps the normal audience gate, the metadata-only tier defaults to `family_only` and refuses `sendable`; fact-shaped reasons; the Memory-page line; captions offered as a rate-stated background backfill. | M | With no captions, a cut completes and no unit loses its gate evidence. With no encoder, no detectors and no captions either, a cut still completes; every approved occasion from the graded routes keeps at least one picture; no unit is marked `share`. |
| **W15** | **One decode per picture.** Hoist the JPEG decode out of the six producers that each repeat it (§1.9) and pass the decoded image down. | M | The producers' facts are unchanged; the non-caption per-picture cost drops by 27 % on machine A. Worth doing *after* W0–W3b, because it saves nothing a slow box would notice. |
| **W16** | **GPU encoder service** (§5.B): plan in, film out, own FFmpeg, `/health` reports its major; local fallback on absence or failure. | L | Same plan rendered locally and remotely gives films of the same duration, structure and titles; killing the service mid-render still produces a film locally. |

Order: W0, W1, W2 → W3, W3b → W4, W5, W6 → W7 → W8 → W9 → W10 → W11 → W12, W13 → W14 → W15 → W16.
W14 can be built in parallel by anyone; it touches none of the service work.

W0, W1 and W2 are three days of work between them and they are the only items in this table that
make the product faster for people who already have it. Everything after W3b makes it *deployable*,
which is the point of the phase — but the ordering should not pretend otherwise.

---

## 7. The validation ladder

Everything is developed and validated locally before any NAS or cluster reaches it.

**Rung 0 — this Mac. Correctness, plus the one timing that is now a regression test.**

```
make dev && make ci
make test-integration-assembly
uv run python scripts/benchmark_preparation.py --repeat 5 --provider cpu
uv run immich-memories prepare --scope <period>      # W12
```

A run where `dino_embed` comes back near 0.09 s/picture instead of 0.011 means W1 regressed and
CoreML is back.

**Rung 1 — local `linux/amd64` containers. Correctness of the real shipping path. Under emulation
every timing is meaningless and is not recorded.**

```
docker buildx build --platform linux/amd64 -f docker/Dockerfile.inference \
  --build-arg DEVICE=cpu -t immich-memories-inference:cpu .
docker compose up -d immich-memories-inference
curl -s localhost:8092/health
curl -s localhost:8092/v1/models
```
Then the same cut with `inference.facts_base_url=http://localhost:8092` and with it blank: the bank
rows must carry the same labels (W8's exit test).

**Rung 2 — the owner's GPU box and Kubernetes. The `cuda` variant and topology 3.**

```
docker buildx build --platform linux/amd64 -f docker/Dockerfile.inference \
  --build-arg DEVICE=cuda -t immich-memories-inference:cuda .
kubectl apply -k deploy/kubernetes/overlays/inference-gpu
kubectl -n immich-memories exec deploy/immich-memories -- \
  curl -s http://immich-memories-inference:8092/health
```
Measured here: seconds per picture on a GPU — the one column §1.3 is missing — and whether the
NetworkPolicy actually lets the app pod through (it does not today).

**Rung 3 — the DS423+. Now a regression run, not an experiment.**

The open questions this rung used to carry are answered: torch runs, ONNX Runtime runs,
`llama.cpp` runs, and §1.3 has the numbers. What remains is confirming them from the product's own
entrypoint rather than from the harness, and holding them:

```
docker compose up -d                                  # cpu variant, all-in-one
docker run --rm -v …:/bench prepbench python scripts/benchmark_preparation.py \
    --encoder /bench/dinov2-small-478164cd.onnx --provider cpu --repeat 3
time immich-memories prepare --scope <one month>      # cold, empty cache volume
time immich-memories prepare --scope <same month>     # warm — should be preview checks only
/usr/bin/time -v immich-memories generate --scope <same month>
```

Hold these, quiesced, within 20 %: producers **1.23 s/picture**, of which DINOv2 embed is
**0.53** and `nsfw_marqo` **0.40**; captions **30.9 s/picture**; peak RSS **570 MB**. Take the load average before and after; §1.7 is what happens when nobody does.
Still to take here, and owed to W16: how long a three-minute H.265 render takes in software.

---

## 8. What transfers to every deployment

The owner's framing was that anything learned on the NAS applies to the main project. Five of these
seven have nothing to do with a NAS at all.

1. **The Mac's default execution provider is the slowest one available.** `provider="auto"` picks
   CoreML, which is 6–8× slower than the CPU EP for DINOv2-small, takes 9× longer to load, and
   holds **2,856 MB resident against 314 MB** — on the only graded configuration the product has.
   Every Mac user pays it today. It is W1 and it is the single largest measured win in this
   document for existing users. The memory half also travels: an accelerator that quietly wants
   2.8 GB is the difference between fitting a 4 GB container and not.
2. **Hardware accelerators are a measurement, not a ranking.** CoreML lost. A CUDA EP might win.
   The rule that falls out: a provider list is ordered by what was timed on that graph, and
   `/health` says which one answered — never "use the fancy one if it exists".
3. **Every "torch + timm" install on Linux is one index away from broken.** `torchvision::nms does
   not exist` is not a NAS problem; it is what the documented CPU install does on any Linux box.
   An extra that names torch and timm and not torchvision is incomplete.
4. **A constant that happens to be harmless is still a constant.** `6` appears three times in the
   producers, chosen for no machine in particular; measured, it costs nothing on four cores and
   nothing obvious on eighteen. The finding is the inverse of what was expected, and it is worth
   recording precisely because it demotes W2 below W0 and W1. Make it a setting because nobody
   chose the number, not because it is slow.
5. **The bottleneck moves with the machine, and so should the optimisation.** On an M5 Max the
   Python-and-Pillow work is **51 %** of the non-caption cost and the models are 49 %, so hoisting
   the six redundant JPEG decodes (W15) takes 27 % off it. On a J4125 that split is **13 % against
   87 %**, and the same change is worth 8 %.
   Optimising for the slow box would have meant doing the wrong work on the fast one.
6. **Smaller quantisation is not faster.** Q4\_K\_M is 2× slower than Q8\_0 on a CPU without AVX2,
   and worse at the task. Int8 weights made the encoder slower on Apple Silicon. Quantisation is a
   trade against a specific instruction set, and it has to be measured on the target.
7. **A benchmark on a shared box measures the box.** Two knob sweeps here reversed sign between a
   loaded and a quiesced run, and a caption number was five times too good because the server
   cached image embeddings and the harness fed it the same pictures twice. Both are in the harness
   now: duplicates are dropped, and the caption stage refuses to report anything but its cold pass.

---

## 9. What this pass did not measure

- **Preview fetch over the network.** The harness times the reuse check a rerun pays (0.0003 s on
  the NAS) and the decode that follows, not the first HTTP GET from Immich. That is bandwidth-bound,
  depends on someone else's host, and doing it properly would mean pulling family previews through
  a benchmark. It is also the one stage a slow CPU does not make worse.
- **Anything on a GPU.** No CUDA host was available, so topology 3's column in §2 is empty and
  W5's exit test is unverified.
- **The DS423+ render.** §5.B still rests on judgement. `/dev/dri/renderD128` exists on that box,
  so the answer may be better than "software x265 on four Celeron cores", but nobody has timed it.
- **Whether the GGUF captions are good enough.** §1.5 shows they are peers on `description` and
  divergent on `setting`. That is a grading question with an owner's eyes on it (W11), not a
  benchmark.
- **Batch size on more than five pictures, through the product path.** The fixture set has five
  distinct photographs. §1.8's sweep fills 32 rows by repeating them — identical arithmetic, which
  is what a throughput measurement needs — but a real 10,000-picture batching curve was not taken.
- **Cold model fetch.** All model files were already local or pulled before timing, so §5.3's
  delivery numbers are sizes, not download times.

---

## 10. What phase 5 does not do

- **Fix `text_model_identity`'s endpoint hashing.** A real defect (§4), but fixing it re-keys the
  owner's entire text bank; it belongs with the hosted-reader work.
- **Store portability.** Moving a prepared library between machines stays unsupported; topology 3
  is the better answer to "prepare elsewhere".
- **Ship the reader as a service.** Somebody else's model server, and always will be.
- **`openvino`, `armnn`, `rocm` variants**, and **retraining any head** — the bundle is graded and
  bound to its encoder key. §3 says why on the variants, once each.
- **Quantise the encoder.** §1.6 measured the prize at 24 % on one machine and negative on the
  other, against a new artifact, a new key and a re-graded bundle. Named and refused, so nobody
  re-proposes it.
- **A smaller local reader for the NAS tier.** Still tempting, still wrong: that tier gets a hosted
  reader, the rule reader, or a reduced tier, not an ungraded model handed to the users least able
  to judge the output.
- **GPU inference in the main image.** Its job is the app and the render; models are services.

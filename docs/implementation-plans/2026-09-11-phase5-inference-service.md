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
| 2 | **ML** | encoder + heads, the two detectors, the captioner | yes — see the metadata-only tier (§4.A) |
| 3 | **encoder** | FFmpeg assembly on a GPU, when the main box has none | yes — topologies 1 and 2 never need it |

The reader (the 17 GB text/vision model) stays out of all three. It is a general-purpose VLM
people already run on Ollama, vLLM, oMLX or a hosted endpoint. Wrapping a model server we did not
write is not a service boundary, it is a fork.

---

## 1. The four topologies

| # | Topology | Main | ML | Encoder | Reader | Needs | Cost |
|---|---|---|---|---|---|---|---|
| 1 | **Laptop, all-in-one** | laptop | same host | none (VideoToolbox/NVENC in-process) | same host | Apple Silicon 32 GB+ (the only graded configuration) or amd64 + 24 GB GPU | one machine; the reader is the whole bill |
| 2 | **NAS, all-in-one** | NAS | same host, `cpu` variant | none (software x264/x265) | hosted, or the metadata-only tier | a DS423+ is a J4125: 4 cores, **SSE4.2, no AVX2**, Quick Sync UHD 600 | **unmeasured** — rung 3 of §6 decides whether this is honest |
| 3 | **NAS + offload** | NAS | GPU box or k8s, `cuda` variant | same GPU box | anywhere | a reachable Service, plus the NetworkPolicy fix in §5 W10 | two machines; the NAS downloads, plans and stores |
| 4 | **Hosted seats** | anywhere | our own image behind a URL | optional | any OpenAI-compatible endpoint | an explicit consent gate (§4.5) | reader at 2026-09 prices: EUR 0.02–0.12 per render (746k+42k tokens for a year, 111k+18k for a month) |

Quick Sync, VAAPI and NVENC decode, scale and encode. **They do not run inference.** That is true
on every page in the docs today and must stay true. OpenVINO could in principle use the UHD 600 in
a DS423+ for the ONNX seats; on a 12-EU Gemini Lake iGPU it is not worth the engineering, and this
is the one time this plan says so.

---

## 2. What we copy from immich-ml, and what we do not

Read from the owner's checkout: `machine-learning/Dockerfile`, `docker/hwaccel.ml.yml`,
`machine-learning/app/{main,config,schemas}.py`, `app/models/base.py`, `app/sessions/ort.py`,
`start.sh`, and their `docker-compose.yaml` (`immich_remote_ml`, port 3003, `model-cache:/cache`).
Reference material, cited not copied — their licence is theirs.

| Their choice | Us | Why |
|---|---|---|
| `ARG DEVICE=cpu`, `builder-${DEVICE}` → `prod-${DEVICE}` → `FROM prod-${DEVICE} AS prod` | **copy** | One Dockerfile, N images, no drift between variants. Exactly the shape we need. |
| Image tag suffixes (`:v1.122.2`, `:v1.122.2-cuda`) | **copy** | Users already read immich tags this way. Ours: `:X.Y.Z`, `:X.Y.Z-cuda`. |
| `hwaccel.ml.yml` with `extends:` per backend, `cpu: {}` and a `deploy.resources.reservations.devices` nvidia entry | **copy** | Their file is the documented way to attach a GPU in compose. Ours is `docker/hwaccel.inference.yml`. |
| Dependency groups selected by `--with ${DEVICE}` | **copy** (as uv extras) | This is what keeps 2.5 GB of CUDA wheels out of the CPU image. |
| Runtime model download into a `/cache` volume; nothing baked | **mostly copy** — see §4.3 | Solves launch-readiness 4.1: the service fetches and verifies the encoder, the app never has to. |
| ORT provider list resolved from `ort.get_available_providers()`, in a declared preference order | **copy** | Our `triage/encoder.py` `_create_session` hardcodes CoreML-or-CPU and raises `ValueError` on anything else. It has no CUDA path at all. |
| `model_ttl: 300` idle unload | **copy the unload** | A NAS cannot hold the captioner resident next to a render. |
| Idle suicide: `os.kill(os.getpid(), SIGINT)` after the TTL | **do not copy** | A restart loop on a NAS is worse than a resident 200 MB process. Unload the weights, keep the process. |
| `/ping` → `pong`, `HEALTHCHECK CMD python3 healthcheck.py` | **copy** | Six lines, works everywhere, no curl in a slim image. |
| pydantic-settings with an env prefix (`MACHINE_LEARNING_`) | **copy** | Ours: `IMMICH_MEMORIES_INFERENCE_`. |
| gunicorn + a custom uvicorn worker, thread pool in front of blocking ORT | **copy** | asyncio in front of ORT is a bottleneck; they wrote the comment, we get it free. |
| `PipelineRequest` with a `depends` graph and two `asyncio.gather` waves | **do not copy** | Our three pixel producers are independent. A DAG for three leaves is over-structure. |
| multipart `entries` JSON + `image` file part | **do not copy** | Our caption client already posts base64 JSON. One wire format in the repo, not two. |
| ARM NN stage: `/dev/mali0`, `libmali.so`, per-chipset firmware; and ROCm | **do not carry** | Per-SoC driver binding we cannot test, and no AMD hardware to test on. |
| OpenVINO stage: Intel compute-runtime `.deb`s, `/dev/dri`, cgroup rule `c 189:* rmw` | **not at launch** | See §1 on UHD 600. Revisit if an Arc-class iGPU user asks. |
| `MACHINE_LEARNING_WORKERS > 1` + mimalloc `LD_PRELOAD`; `clean_name()` slugs and `immich-app/*` HF repos | **do not copy** | One worker and our own batching; our artifacts are digest-pinned, not name-resolved. |

---

## 3. The service contract, and the key-stability rule

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
banked as evidence, and `check_provider` sends three synthetic colour tiles through the schema
before any library preview leaves the machine. Leave it alone.

### The rule

> **Every producer key is content-addressed over the artifact, never over where it ran.**

`encoder_key` is already `sha256(encoder_id + weights_sha256 + preprocess_version + layout_version)`
(`triage/encoder.py:35`). It stays exactly that — computed on the service, returned on the wire,
stored by the client as returned. No URL, hostname, device or execution-provider name enters any
key, so the same picture through the `cpu` and `cuda` containers lands on one bank row: same
artifact, same question.

Corollaries. **Device is operational, never identity** — provider selection is config and does not
re-key. **Quantization *is* identity** — a GGUF captioner is a different artifact from the MLX one
and gets its own bank token (§4.4); `DESCRIPTION_MODEL` carries no such token today, which is how
two backends would silently write into one corpus. **The counter-example is already in the tree**:
`text_model_identity` (`analysis/llm_text_identity.py:18`) hashes `resolved.base_url` into every
text bank's producer key, so the same model at two URLs misses every bank — a known defect, not
fixed here (§7), and **nothing in the inference service may imitate it.** Finally, the service
refuses any producer whose artifact digest is not the pinned one; the encoder already does
(`RuntimeError` on anything but `478164cd…`) and the bundle's encoder-key binding does the rest.

---

## 4. Decisions

### 4.1 The service boundary

**Recommendation: the ML service holds the three pixel seats — encoder+heads, the two detectors and
the captioner — in one image on one port. The reader stays outside.**

Those three share a cache volume, a device variant, a lifecycle and an idle-unload policy;
splitting them into three containers triples a NAS owner's ops surface to save nothing. The
captioner keeps its own `/v1` namespace because its wire is graded and its client is already
written — a second dialect inside one process, not a second service. The client side gains
`inference.facts_base_url`; blank keeps today's in-process path, so topology 1 needs no container.

### 4.2 Device variants

**Recommendation: ship `cpu` (linux/amd64 + linux/arm64) and `cuda` (linux/amd64) at launch.
`openvino`, `armnn` and `rocm` do not ship.**

The `cpu` variant must not contain a CUDA wheel. Today the `editorial` extra on linux/amd64 is
**3.05 GB**, of which 2.5 GB is fifteen `nvidia-*` wheels dragged in by torch, for two detectors
that hardcode `CPUExecutionProvider` and `torch.set_num_threads(6)`. That is the largest cheap win
here: `onnxruntime` + `torch --index-url .../whl/cpu` for `cpu`, `onnxruntime-gpu` + CUDA torch for
`cuda`, selected the way immich selects `--with ${DEVICE}`. Better still, see W2b — the Marqo
detector is a timm ViT and should export to ONNX, which drops torch from the image entirely.

### 4.3 Model delivery

**Recommendation: bake what is ours and tiny, fetch what is large, verify everything.**

| Artifact | Size | Delivery |
|---|---|---|
| Head bundle `public-6heads-v3.npz` | 2.2 MB | **baked** — already in the wheel, ours, MIT |
| DINOv2-small ONNX export | 88 MB | **fetched** into `/cache`, digest-verified against `478164cd…` |
| Marqo + Docling snapshots | ~400 MB | **fetched** into `/cache` at their pinned revisions |
| Caption weights (GGUF) | ~0.4–0.9 GB | **fetched** into `/cache`, digest-verified |

This is immich's model: a `model-cache:/cache` volume populated at first use, with an optional boot
preload (their `MACHINE_LEARNING_PRELOAD__*`). It closes launch-readiness 4.1 without the app ever
touching a model file — **the fetch becomes the service's job.** `immich-memories models fetch`
from `feat/install-bootstrap` survives as the no-service laptop entrypoint; both call one module.

### 4.4 The captioner on Linux — the one that decides whether Linux is real

The only graded caption weights are `mlx-community/SmolVLM2-500M-Video-Instruct-mlx@fa57db46`.
MLX is Apple-only. A Linux user has no captioner at all today.

**Recommendation: serve the same SmolVLM2-500M generation as GGUF under `llama.cpp`'s
`llama-server`, inside our image, keeping the alias.**

- `llama-server` gives an OpenAI-compatible `/v1/chat/completions` with images, `/v1/models`,
  `--alias smolvlm2-500m-base-public` and json-schema-constrained output — the wire we already send
  fits it — and builds for CPU (amd64 and arm64), CUDA and Metal: the same variant matrix.
- ONNX Runtime GenAI would need our own multimodal export of a model it does not support. No.
- vLLM needs a GPU and a 2 GB dependency tree. Topology 3 only; no help to a NAS.
- A third-party hosted captioner is **impossible**, not merely undesirable: the contract pins an
  alias no commercial provider will advertise. The hosted caption story is our own image behind a
  URL (§4.5), not someone else's API.

**The re-grading cost, stated honestly.** A GGUF quant is a different numeric path from MLX 4-bit,
so descriptions will differ — and today `DESCRIPTION_MODEL` is
`smolvlm2-500m-base-public@envelope-v3-compact`, which encodes no quantization, so two backends
would write into one bank and nothing would notice. So: the existing token is **grandfathered to
mean MLX `fa57db46`** and the owner re-captions nothing; the GGUF backend gets its own token
carrying the quant and the weights digest, so different artifact means different rows; and before
it becomes the Linux default **one route is graded against the approved plan by the owner's eyes**,
same as every other grading. If it degrades, Linux ships the metadata-only tier (§4.A) rather than
an ungraded captioner, and we say so.

### 4.5 Hosted inference

**Recommendation: the reader may be hosted third-party. The pixel seats may only be hosted on our
own image. Both sit behind one explicit per-destination consent gate.**

What leaves the machine differs sharply by seat. Captions send 400 px tiles and pixel facts send
preview-derived tensors, both without metadata; the reader gets 800 px tiles **and** annotation
lines carrying real people's names and place names — and nothing about editing a `base_url` tells a
user that. So the gate is a first-run opt-in per destination host naming the seats going off-box
and what each sends, not a config field that quietly starts uploading a family album. The run
records the host, which seats went remote, the asset count and the consent version, beside the
existing model attestation. No names, ids or album titles in that record.

### 4.6 Kubernetes

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

### 4.7 "Prepare elsewhere", and whether `prepare` is in scope

The measured portability finding stands and is not re-derived: the annotation store holds no paths
and travels, captions and head facts transfer unconditionally, **but** the reader's endpoint URL is
hashed into every text bank key (§3), the thumbnail cache and three sidecar SQLite files must
travel too, and there is no prepare-only entrypoint — preparation runs inside `generate`
(`editorial_runtime.py:480`).

**Recommendation: a minimal `prepare` command is in scope; store portability is not.** With an
inference service, "prepare elsewhere" mostly stops being something you do by moving files — you
point at a GPU service instead, which is topology 3 and a better answer. What remains is exercising
preparation without rendering: §6 cannot produce per-picture timings without it and launch-readiness
4.16 owes those numbers. So `immich-memories prepare --scope <period>` runs the preparation stage,
prints seconds per picture per producer, and stops. S, not L.

---

### 4.A The metadata-only tier — no ONNX, no LLM at all

> "what can we do WITHOUT any ONNX or LLM — okay you did not follow the basic requirements but we
> can still do something."

This sits strictly **below** the rule reader
([`docs/designs/2026-09-11-rule-reader.md`](../designs/2026-09-11-rule-reader.md)), which still
needs the encoder, the heads and the detectors — its layer-2 audience rules read `nsfw=yes`,
`swim=yes` with `children=yes`, and `exposure`, all of which are model facts.

**What survives, verified in the tree:**

| Signal | Where | Model? |
|---|---|---|
| Dates, GPS, favourites, albums, EXIF, asset type, duration, **and people** | Immich API — the faces are Immich's own recognition, server-side | not ours |
| Visibility gate (archived / hidden / locked) | `source_filter.not_on_the_timeline` | no |
| Provenance filter — drops forwarded and re-encoded material by EXIF camera make, filename pattern and short side. **A screenshot filter without a model.** | `source_filter.from_the_camera_roll:197` | no |
| Sharpness, brightness, contrast, dark/bright fraction, dimensions, orientation — and a self-calibrating blur cut from `pixel_facts_thresholds.sharpness_p10`, a library percentile rather than a fixed bar | `editorial_preparation_pixels.pixel_facts`, banked under the `pixel-facts-v1` producer — PIL plus a numpy Laplacian variance | **no** |
| Near-duplicate and burst buckets | `editorial_thumbnail_hashes`, aHash over previews via OpenCV | no |
| Live still-vs-clip; burst grouping, "beats a still", minimum duration | `editorial_motion_facts` (`median-flow-v1-12frames-320x240` Farneback optical flow on a playback preview) and `motion_bursts` | no |
| Allocation, chronology, carrier mechanics, duplicate buckets, templated titles, assembly, music | the deterministic downstream the rule-reader design already reuses unchanged | no |

**What is lost, in what the user sees.** No descriptions, so every reason under a picture becomes a
fact rather than a sentence — the rule reader's form already does this:
`"<story title>: <n> pictures at <place>"`. No activity, venue, people, children, swim or location
heads. And, the one that matters: **no sensitive-content or document detection.** The audience
gate's layer 2 reads captions classified by the reader, plus `nsfw_marqo`. With neither it has
owner flags and metadata, and nothing else.

**Recommendation on sharing and export.** The standing rule is that the gate may only ever tighten.
A tier with almost no evidence must therefore **default every unit to `family_only` and refuse a
`sendable` export outright** — not "share, because nothing objected", which inverts the gate. The
existing per-picture `cleared` flag on the pool page stays the only way up, and `sendable` unlocks
only when every included picture carries an owner clearance. The tier produces a family video, not
a shareable one, and says so.

**The story view.** No thesis (hidden, not shown empty). Weight badges survive — weights come from
counts and spread, not from the model. Reasons are facts. One line under the title, extending the
rule reader's: *"Edited from metadata only — no descriptions and no content checks. Family viewing."*

**Does the occasion door still work?** Yes, and this is why the tier is defensible. Occasion
discovery was designed on metadata mass and away-from-home blocks, with stars as indicators and
never admission gates — exactly the machinery that survives. The acceptance bar (losing good
pictures is fine, losing an occasion is not) stays **testable** here, which is what separates this
from a toy.

**Ship it?** Yes, as a named tier and never a silent fallback: metadata-only, family_only, no
sendable export, stated on the Memory page. It is also the only tier that runs on a J4125 today,
which makes topology 2 real regardless of how rung 3 turns out.

---

### 4.B The GPU encoder service

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
but unreachable, or failing, **falls back to local software encoding with a warning**: the plan is
already computed and throwing it away over a network blip is not acceptable. That is how topologies
1 and 2 work with no encoder service at all.

**Key stability? No**, and saying so matters. Nothing about a film is banked, so there is no
producer key to protect. Its discipline is pinning the FFmpeg major per image and recording which
encoder produced each film — NVENC and libx265 give different bytes from the same plan, and nothing
compares them.

**Honestly optional**: last built, first cut. It ships only if rung 3 shows a DS423+ cannot encode
a three-minute H.265 memory in a time anyone will wait for — likely, and exactly the measurement
that should decide it.

---

## 5. Work items

| # | Item | Size | Exit test |
|---|---|---|---|
| **W1** | Split the `editorial` extra by device: CPU torch index for `cpu`, CUDA wheels only for `cuda`. | S | `docker buildx build --platform linux/amd64` produces a `cpu` image with zero `nvidia-*` wheels; the extra is under 700 MB. |
| **W2** | Real provider selection in `triage/encoder.py` `_create_session`: resolve from `ort.get_available_providers()` against a declared preference order including CUDA; add a `provider` field to `TriageConfig`, which has none today. | S | On a CUDA host the session reports `CUDAExecutionProvider`; on a Mac, CoreML; on a NAS, CPU. Same input → identical `encoder_key`. |
| **W2b** | Export the Marqo detector (a timm ViT) to ONNX and compare against the torch path on a sample. If it agrees, drop torch and timm from the inference image. | M | Label agreement on a held-out sample; the `nsfw_marqo` fact version bumps and re-derives. If it does not agree, this item dies here and torch stays. |
| **W3** | The service: `services/inference/` with `/ping`, `/health`, `/facts`, pydantic-settings under `IMMICH_MEMORIES_INFERENCE_`, a thread pool in front of ORT, idle unload without idle suicide. | M | `/facts` on a fixed picture returns byte-identical facts to today's in-process path, and the same `encoder_key`. |
| **W4** | `docker/Dockerfile.inference` with `ARG DEVICE`, `builder-${DEVICE}` / `prod-${DEVICE}`, CUDA runtime base for `cuda`. | M | Both variants build; `:X.Y.Z` and `:X.Y.Z-cuda` publish; the cuda image loads a CUDA EP session. |
| **W5** | `docker/hwaccel.inference.yml` (`cpu: {}`, `cuda:` reservation) and an inference service in `docker-compose.yml`. | S | `docker compose up` on a GPU host with the `cuda` extends block gives a service whose `/health` names a CUDA provider. |
| **W6** | Model cache + runtime fetch inside the service: encoder digest-verified, detector snapshots at pinned revisions, optional boot preload. Share the module with `models fetch`. | M | Cold container, empty volume, one `docker compose up`: `/health` reports every producer loaded and the encoder digest matches. Closes launch-readiness 4.1 and 4.2. |
| **W7** | The captioner in the service: bundled `llama-server` child, GGUF fetched to the cache, `--alias smolvlm2-500m-base-public`, json-schema output, proxied under `/v1`. New bank token for the GGUF artifact; the MLX token grandfathered. | L | `check_provider` passes all three schema controls unchanged; a GGUF-captioned asset and an MLX-captioned asset occupy distinct bank rows. |
| **W8** | Grade the GGUF captioner: one route, owner's eyes, against the approved plan. | M | Either it is approved and becomes the Linux default, or Linux ships the metadata-only tier and the docs say why. No third outcome. |
| **W9** | Client side: `inference.facts_base_url`, blank = in-process. Store the `encoder_key` and versions the service returned, verbatim. | M | The same library prepared in-process and via the service produces identical bank rows. |
| **W10** | Kubernetes: inference Deployment + Service + PVC in `base/`, `overlays/inference-gpu/`, **NetworkPolicy egress to the inference Service and ingress from the app pod**, kustomize tag bump. | M | `kubectl apply -k base` on a CPU cluster completes a cut; `-k overlays/inference-gpu` runs the same cut with the ML pod on a GPU node. |
| **W11** | `immich-memories prepare --scope <period>`: run preparation, print seconds per picture per producer, stop. | S | On a cold scope it prepares and exits 0 without rendering; the numbers feed §6 and launch-readiness 4.16. |
| **W12** | Consent gate for off-box destinations: per-host opt-in naming the seats and their payloads; record host, seats, asset count, consent version. | M | Pointing the reader at a remote host without consent refuses with a message naming what would be sent. The record contains no names, ids or album titles. |
| **W13** | **Metadata-only tier** (§4.A): tolerate every absent producer, fact-shaped reasons, `family_only` default, `sendable` refused, the Memory-page line. | M | With no encoder, no detectors, no captions and no reader, a cut completes; every approved occasion from the graded routes keeps at least one picture; no unit is marked `share`. |
| **W14** | **GPU encoder service** (§4.B): plan in, film out, own FFmpeg, `/health` reports its major; local fallback on absence or failure. | L | Same plan rendered locally and remotely gives films of the same duration, structure and titles; killing the service mid-render still produces a film locally. |

Order: W1, W2 → W2b, W3, W4, W5 → W6 → W9 → W10 → W7 → W8 → W11, W12 → W13 → W14.
W13 can be built in parallel by anyone; it touches none of the service work.

---

## 6. The validation ladder

Everything is developed and validated locally before any NAS or cluster reaches it.

**Rung 0 — this Mac. Correctness only; Apple timings are not transferable and every number here is
labelled so.**

```
make dev && make ci
make test-integration-assembly
uv run immich-memories prepare --scope <period>      # W11
```

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
rows must be identical (W9's exit test).

**Rung 2 — the owner's GPU box and Kubernetes. The `cuda` variant and topology 3.**

```
docker buildx build --platform linux/amd64 -f docker/Dockerfile.inference \
  --build-arg DEVICE=cuda -t immich-memories-inference:cuda .
kubectl apply -k deploy/kubernetes/overlays/inference-gpu
kubectl -n immich-memories exec deploy/immich-memories -- \
  curl -s http://immich-memories-inference:8092/health
```
Measured here: seconds per picture on a GPU, and whether the NetworkPolicy actually lets the app
pod through (it does not today).

**Rung 3 — the DS423+. The only timings that decide whether topology 2 is honest.**

A J4125: 4 cores, **SSE4.2, no AVX2**, Quick Sync UHD 600 (media only). Record, cold and warm:

- seconds per picture for captions, for encoder+heads, for the detectors;
- peak RSS per producer;
- whether a month-scope memory completes at all, and in how long;
- and, for W14, how long a three-minute H.265 render takes in software.

```
docker compose up -d                                  # cpu variant, all-in-one
time immich-memories prepare --scope <one month>      # cold, empty cache volume
time immich-memories prepare --scope <same month>     # warm
/usr/bin/time -v immich-memories generate --scope <same month>
```

The first thing rung 3 must answer is not a timing: **does the stack start at all.** PyTorch wheels
assume AVX on x86; a Gemini Lake CPU has SSE4.2 and no AVX of any kind, so `import torch` may
simply die with an illegal instruction. That is W2b's real justification, and it must be checked
before any of the numbers above are attempted.

---

## 7. What phase 5 does not do

- **Fix `text_model_identity`'s endpoint hashing.** A real defect (§3), but fixing it re-keys the
  owner's entire text bank; it belongs with the hosted-reader work. Phase 5's job is not to repeat it.
- **Store portability.** Moving a prepared library between machines stays unsupported; topology 3
  is the better answer to "prepare elsewhere".
- **Ship the reader as a service.** Somebody else's model server, and always will be.
- **`openvino`, `armnn`, `rocm` variants**, and **retraining any head** — the bundle is graded and
  bound to its encoder key. §2 says why on the variants, once each.
- **A smaller local reader for the NAS tier.** Still tempting, still wrong: that tier gets a hosted
  reader, the rule reader, or the metadata-only tier, not an ungraded model handed to the users
  least able to judge the output.
- **GPU inference in the main image.** Its job is the app and the render; models are services. That
  was true before this plan, and this plan is what makes it affordable.

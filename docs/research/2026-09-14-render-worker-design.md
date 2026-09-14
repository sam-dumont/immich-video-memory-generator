---
date: 2026-09-14
status: design, no code on any branch
issue: "#931"
builds-on: services/inference (the facts split that shipped), docs/research/2026-09-01-annotation-store-design.md (#871)
measured-from: setup matrix run of 2026-09-14, library `demo`, month 2024-06
---

# The render worker

A second service beside the inference service, built from the app image, that takes a finished
plan and gives back a finished mp4. The NAS decides what the film is; the GPU box encodes it.

The facts split already exists: `advanced.inference.facts_base_url` moves the encoder, the six
heads and the two detectors onto a CUDA box, and the app banks the same rows it would have
derived itself. This is the same move for the render, and it is the larger half of the bill.

## 1. The numbers to beat

Setup matrix, 2026-09-14, library `demo`, June 2024, one 60 s monthly memory per cell, image
`0.87.16`. Music generation off everywhere, so every render second below is decode, scale,
titles and encode. Single observations.

| cell | where the render ran | render | finished film | film / render |
| --- | --- | ---: | ---: | ---: |
| `mac-local` | M5 Max, VideoToolbox | 49 s | 56 s | 1.14 |
| `mac-rules` | M5 Max, VideoToolbox | 50 s | 55 s | 1.10 |
| `k8s-rules-service` | cluster pod, CPU | 364 s | 58 s | 0.16 |
| `k8s-hosted-melious` | cluster pod, CPU | 387 s | 58 s | 0.15 |
| `k8s-rules-local` | cluster pod, CPU | 406 s | 58 s | 0.14 |
| `nas-hosted-melious` | DS423+, 4 cores, 4 GB, CPU | 1,582 s | 54 s | 0.03 |
| `nas-rules-service` | DS423+, 4 cores, 4 GB, CPU | 1,606 s | 54 s | 0.03 |
| `nas-rules-local` | DS423+, 4 cores, 4 GB, CPU | 1,734 s | 54 s | 0.03 |

Read the last column as "seconds of film per second of work". The NAS produces a film about 30
times slower than it plays. Selection on that same NAS took 1 s, and the picture facts already
come off the inference service in two of the three NAS rows: 1,606 of 1,607 seconds are the
render. The borrowed GPU is already doing the cheap half.

Two other measured numbers set the budget. The inference service warmed in 14.38 s on the same
run, which is what a cold sibling service costs. An isolated T1000 8 GB pod did a synthetic 720p
H.264 NVENC encode at 5.6 times realtime (phase 5 review, 2026-09-12), so the encode alone of a
54 s film is about 10 s on that card and everything above it is decode, scale and titles.

**The bar, on the same demo month:**

| | today | required | stretch |
| --- | ---: | ---: | ---: |
| Worker's own render seconds, cluster | 364 s (CPU) | ≤ 120 s | ≤ 60 s |
| NAS cell, submit to validated mp4 on the NAS | 1,606 s | ≤ 180 s | ≤ 120 s |
| Output size at the same resolution and codec | x264 baseline | ≤ 2.5 × baseline | ≤ 1.5 × |

The third row exists because VideoToolbox already spends 8 times the bits of x265 for the same
picture, and VAAPI, QSV and NVENC get no quality arguments at all today. A render that is 13
times faster and 3 times fatter is not obviously a win, so `size_bytes` gets published beside
`render_s` in every matrix cell, and a cell that fails the size bar fails the cell.

## 2. The boundary

### What a render needs, today, by name

Nothing here is invented. Every item is something `generate_memory` already receives.

| What | The real type | Where it comes from now |
| --- | --- | --- |
| The certified timing binding | `dict` from `bind_editorial_timeline`: `policy`, `timeline`, `source_ids`, `sha256` | `PipelineResult.stats["editorial_render_timing"]` |
| The render projection | `editorial-source-rendering-v1`: `selected_ids`, `adjustments`, `allow_live_motion`, `intervals` | `<attempt>/render-projection.private.json` |
| The cut itself | `tuple[EditorialSelection, ...]`: asset id, start, end, `render_mode`, `render_frame_seconds` | `PipelineResult.editorial_selections` |
| The sources | `VideoClipInfo` per selected asset, plus the Live Photo companion ids | `assets_to_clips` |
| Output settings | `OutputCanvas`, plus the `EncodingRequest` inputs: codec, container, resolution, quality, crf, `hdr_mode`, `codec_policy`, scale mode, transition and its duration | `config.output`, `config.defaults`, CLI overrides |
| Title inputs | `TitleScreenSettings`: resolved title and subtitle text, the three durations from `TimelinePlan`, locale, style mode, dividers, memory type, orientation, resolution, fps | `_build_title_settings` after `resolve_cli_title` |
| Music | the chosen track's bytes, `music_volume`, `music_mute_windows` and the stem paths when they exist | `MusicSelection.path`, a local file |
| The audience gate's trims | `certified_content_intervals: dict[str, tuple[float, float]]` | the preparation gate |
| Immich | `base_url`, `api_key`, `timeout`, API version policy | the `SyncClientConnection` triple |

Two of those deserve a sentence.

`certified_content_intervals` travels or the worker renders material the gate cut. It is the
only field in the list whose absence is silently wrong rather than loudly wrong, so the worker
refuses a job envelope that omits the key entirely, and accepts an explicitly empty map.

The music is a local file on the app box: bundled tracks ship in the package, generated ones are
written into the run directory. It rides in the envelope as base64, the way `/facts` carries a
picture. Generating music on the worker is not in this issue.

### What comes back

| What | The real type |
| --- | --- |
| The film | `video/mp4` bytes, streamed once |
| The plan the worker actually resolved | `EncodingPlan` as JSON: codec, encoder, `encoder_args`, `target_transfer`, `tone_map_to_sdr`, `pixel_format`, container, crf, `codec_substituted_from` |
| ffprobe facts | `OutputProbe`: codec, container, `duration_seconds`, `size_bytes`, `pixel_format`, `color_transfer`, `color_primaries`, width, height, `decoded_frames` |
| The run record's render block | `OutputProbe.render_metrics(plan)`, verbatim |
| The timing block | `{download, assembly, music, total}` as `_log_phase_timing` already spells it, plus `queued_seconds` and the encoder name |
| Warnings | the music warning and the duration warning, as strings |

**The app does not send an `EncodingPlan`.** `_build_assembly_settings` resolves one against
`detect_hardware_acceleration()` on whatever box it runs on, so a NAS would plan libx264 and a
worker would then be validated against a plan it never used. The job carries the *request*, the
worker resolves the plan on its own hardware, validates its own file with
`validate_output(staged, its_plan)`, and returns the plan it used. The app rehydrates that plan
and calls `publish_validated_output(staged, final, plan)` unchanged on the bytes it received.
Same function, same three gates (media shape, encoding identity, colour metadata), run twice on
two machines. `codec_requested` in the run record is how a substitution on the worker stays
visible.

This does mean the app validates against a yardstick the worker declared. A worker that lies
about its plan validates its own lie. That is acceptable for a service you deploy yourself on
your own LAN and it is written down here so nobody discovers it later.

### The admission gate already exists

`prepare_certified_timeline(params)` raises when the timing policy differs from the one bound
into the plan, and again when the clip list differs from `binding["source_ids"]`. The worker
calls it on receipt, before it fetches a single byte. A job whose envelope drifted from its own
binding is a 409, not a film.

## 3. The job API

Port 8093. FastAPI, one uvicorn worker, the same shape as the inference service.

| Route | Question |
| --- | --- |
| `GET /ping` | are you up |
| `GET /health` | which encoder would this box actually open, which title backend, how much scratch is free, is a job running |
| `POST /render` | here is a job |
| `GET /render/{job_id}` | where is it |
| `GET /render/{job_id}/result` | give me the film |
| `DELETE /render/{job_id}` | stop |

`/health` runs the real chain, not a probe. The existing defect is precisely that a probe
initialises a device and the real render does not, and `scripts/verify_hardware_encode.py`
already encodes through `ClipEncoder`'s own command to prove the opposite. `/health` reuses that
path on a two-second synthetic clip at boot, caches the answer, and reports
`encoder: h264_nvenc` only when that clip came out. A worker that cannot prove NVENC says so on
`/health` and the app's preflight row turns red before anyone waits 27 minutes to find out.

### Idempotency

The key is `(memory_key, plan_digest)`. `plan_digest` is `binding["sha256"]`, which
`bind_editorial_timeline` already computes over the policy, the timeline and the source ids.
Nothing new gets hashed.

- Same key, job still running: `200` with the running job. Not a second render.
- Same key, job finished and the result still held: `200`, and the result is fetchable again.
- Different key while a job runs: `409`, body names the running job id and its key.

One job at a time is the whole concurrency model. A card encoding one film has nothing left for
a second, and a queue that pretends otherwise just moves the waiting somewhere less visible.

### Progress

The app polls `GET /render/{job_id}` once a second. The body carries two records the app
already knows how to draw:

- `phase_event`: `PhaseEvent.to_dict()`, so `phase`, `label`, `current`, `total`, `message`,
  `elapsed_seconds`, with `phase` in `render` / `music` / `complete`.
- `stage_update`: `StageUpdate.as_record()`, so `phase`, `label`, `done`, `total`, `verb`.

The app feeds the first into its existing `phase_callback` and the second into `announce_stage`.
The CLI's Rich bar and the web's `memory_run.py` poller then read what they read today, out of
the same attempt directory, with no second code path. `RunTracker.record_phase_event` is
forward-only, so a worker that restarts and replays an earlier phase is ignored rather than
rewinding the bar.

No SSE and no websocket. The web surface already polls the attempt tree on a timer, and a stream
buys a reconnect state machine for a bar that moves once a second.

### Size limits

| Setting | Default | Why |
| --- | --- | --- |
| `MAX_JOB_BYTES` | 64 MiB | the envelope including the base64 track; the inference service caps one picture at 16 MiB |
| `MAX_SELECTIONS` | 400 | a 10-minute film at the shortest supported carrier |
| `RESULT_TTL_SECONDS` | 3600 | how long a finished film stays fetchable |
| `JOB_TIMEOUT_SECONDS` | 3600 | `output_contract` alone allows a 15-minute full decode |

An oversized envelope is `413` with the ceiling in `detail`, same as `/facts`.

### Auth

**A shared token, required, no anonymous mode.** Header `x-render-token`, compared in constant
time. The worker refuses to start without `IMMICH_MEMORIES_RENDER_WORKER_TOKEN` set.

This diverges from the inference service, which checks no credential at all, and the divergence
is the point: that service takes a picture and returns an opinion, this one takes your Immich
API key. A service that will read your whole library on request should not answer an
unauthenticated caller, even on a LAN. The token costs one line in `.env`.

The bind default stays loopback in code (`127.0.0.1`) and `0.0.0.0` in the image, exactly as
`InferenceSettings` does it, and the docs repeat the sentence the inference page already carries:
do not give it a routable address.

The Immich key is the uncomfortable part. Today there is one credential shape, a full
`x-api-key`, and no scoped key anywhere in the codebase. Until there is:

- the key lives in memory for the life of the job and is never written to the job record,
- every error passes through `sanitize_error_message` before it reaches a response or a log,
- the worker's NetworkPolicy allows egress to Immich and DNS, and nothing else.

The real fix is a scoped, read-only, short-lived key, and the seam for it already exists:
`SyncClientConnection` (`base_url`, `api_key`, `timeout`) and `build_sync_client_factory` are the
only things downstream of the credential. That work is owed before this worker is ever reachable
from outside a LAN, and it is not in this issue.

## 4. The worker

Same app image, `ghcr.io/sam-dumont/immich-video-memory-generator`, different command. The image
already carries ffmpeg, the VA-API drivers, the bundled fonts and the title kernels, and
`overlays/gpu` already claims that image works on an NVIDIA node with
`NVIDIA_DRIVER_CAPABILITIES=compute,video,utility`.

Service source lives at `services/render/immich_memories_render/`, on `PYTHONPATH`, not a
distribution, mirroring `services/inference`. No second image is published: `docker/Dockerfile`
gains one `COPY services/render /app/services/render` and one `ENV PYTHONPATH`, which is exactly
what `Dockerfile.inference` already does for its own service source. One image means the worker
can never diverge from the renderer the app ships.

- One job at a time, one uvicorn worker, `replicas: 1`, `strategy: Recreate`.
- NVENC for the encode, the GPU title kernels for the titles. `init_kernels()` may legitimately
  return `CPU`, which `/health` reports as `titles: cpu` rather than pretending.
- Scratch is a real budget: `SCRATCH_GB`, default 20, which is twice what
  `cache.video_cache_max_size_gb` already treats as an acceptable footprint for downloaded
  clips. The worker refuses a job when free space under the scratch root is below the budget,
  with the number in `detail`.
- Cleanup: a job's scratch directory goes when its result is fetched, or at `RESULT_TTL_SECONDS`,
  whichever comes first. Everything under the scratch root is swept at boot, because a killed
  pod leaves clips behind.
- Readiness stays `/ping`, not "am I free". A busy worker that fails readiness is removed from
  its Service, and the app's poll of a job it just submitted then fails. Busy is a fact of the
  job API, never of the endpoint.

### Settings, and the underscore trap

The worker's own settings take the prefix `IMMICH_MEMORIES_RENDER_WORKER_` (single underscore
inside), not `IMMICH_MEMORIES_RENDER_`. The app's own section is `render`, whose environment form
is `IMMICH_MEMORIES_RENDER__WORKER_BASE_URL` with two underscores. On the longer prefix, no two
variables differ by a single underscore while meaning opposite things. The inference service has
that hazard today and pays for it with a four-line warning in its Deployment.

| Variable | Default |
| --- | --- |
| `IMMICH_MEMORIES_RENDER_WORKER_HOST` / `_PORT` | `127.0.0.1` / `8093` |
| `IMMICH_MEMORIES_RENDER_WORKER_TOKEN` | none, and the process refuses to start |
| `IMMICH_MEMORIES_RENDER_WORKER_SCRATCH_DIR` | `/scratch` |
| `IMMICH_MEMORIES_RENDER_WORKER_SCRATCH_GB` | `20` |
| `IMMICH_MEMORIES_RENDER_WORKER_MAX_JOB_BYTES` | `67108864` |
| `IMMICH_MEMORIES_RENDER_WORKER_RESULT_TTL_SECONDS` | `3600` |
| `IMMICH_MEMORIES_RENDER_WORKER_JOB_TIMEOUT_SECONDS` | `3600` |

### Compose

Profile `render`, exactly as `inference` does it, on the published `docker-compose.yml`:

```yaml
  immich-memories-render:
    # The same image and the same tag as the app service above. /health refuses
    # a job from an app on another version, so keep the two lines in step.
    image: ghcr.io/sam-dumont/immich-video-memory-generator:latest
    container_name: immich-memories-render
    profiles:
      - render
    command: ["python", "-m", "immich_memories_render"]
    ports:
      - "127.0.0.1:8093:8093"
    volumes:
      - immich-memories-render-scratch:/scratch
    environment:
      IMMICH_MEMORIES_RENDER_WORKER_TOKEN: "${RENDER_WORKER_TOKEN}"
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 8G
          cpus: "8"
        # ── For a GPU: uncomment. NVENC needs the video capability. ────────
        # reservations:
        #   devices:
        #     - driver: nvidia
        #       count: 1
        #       capabilities: [gpu, video]
```

No `extends:`, for the reason #882 already established: compose resolves an `extends:` when the
file loads whatever profiles are on, and a downloaded `docker-compose.yml` then fails to parse.
The GPU reservation ships commented inside the service, like the inference one.

8 GB and 8 CPUs rather than the inference service's 4 and 4, because the compose header already
says encoding wants 4 to 8 GB and 4+ cores, and the worker does nothing else.

### Kubernetes

Three directories, mirroring `overlays/inference*` down to the naming:

```
deploy/kubernetes/overlays/render/        deployment.yaml, service.yaml, networkpolicy.yaml, kustomization.yaml
deploy/kubernetes/overlays/render-cuda/   deployment-cuda.yaml, kustomization.yaml
deploy/kubernetes/overlays/render-lan/    service-lan.yaml, kustomization.yaml
```

- `overlays/render` does not pull in `../../base`. Base refuses to build without a hand-made
  `base/secret.yaml`, and this worker takes its Immich credentials in the job, never from a
  Secret of its own. Same reasoning the inference overlay writes down.
- Deployment `immich-memories-render`, container `render`, ClusterIP Service named `render` on
  8093, so the app's setting reads `worker_base_url: http://render:8093`.
- Pod `securityContext` identical to the inference one: non-root, 1000/1000, `fsGroup: 1000`,
  `RuntimeDefault`. Container: no privilege escalation, `readOnlyRootFilesystem: true`, all
  capabilities dropped. `TMPDIR=/scratch` for the same reason `HF_HOME` moves there on the
  inference pod: a read-only root filesystem has nowhere to put a temporary file, and ffmpeg
  writes plenty.
- Scratch is an `emptyDir` with `sizeLimit: 20Gi`, not a PVC. Clips are re-fetchable from Immich
  and nothing here is worth surviving a pod.
- Probes on `/ping`, same delays and thresholds as the inference pod.
- `render-cuda` is the one patch: `runtimeClassName: nvidia`, one `nvidia.com/gpu`, the node
  selector on `nvidia.com/gpu.present=true`, the `nvidia.com/gpu` toleration, and
  **`NVIDIA_DRIVER_CAPABILITIES: "compute,video,utility"`**. The inference overlay sets
  `compute,utility`: copying that verbatim gives a worker with no NVENC and a `/health` that
  correctly reports libx264 after you have already scheduled it on a card. It sits in a sibling
  directory for the reason kustomize gives: an overlay nested inside its own base reads as a
  cycle.
- `render-lan` adds a second Service of type LoadBalancer, additively, so a NAS outside the
  cluster has an address and no in-cluster caller starts riding an external one. Identical to
  `inference-lan`, and the setup matrix applies and deletes it the same way.

## 5. The app side

One new Tier 2 section, `render`, flat on `Config` at runtime as `config.render`:

```yaml
advanced:
  render:
    worker_base_url: ""        # blank: render in this process, as today
    worker_token: "${RENDER_WORKER_TOKEN}"
    timeout_seconds: 3600      # one job, submit to result
    fallback_to_local: false   # when the worker cannot be reached, do NOT encode here
```

`worker_token` joins the short list of fields `config_loader` expands `${VAR}` for, the one
`llm.api_key` is already on, so a token never lands on disk in a pinned config.

`fallback_to_local` defaults to **false**, where the facts equivalent defaults to true. The two
fallbacks are not the same trade. A facts fallback costs preparation minutes and produces
byte-identical rows, because a fact's identity is the artifact that produced it and never the
machine that ran it. A render fallback costs 26 minutes on the box that was configured to avoid
exactly that, and produces a different encode: a different encoder, different `encoder_args`, a
different file size. Silently substituting a 27-minute x264 render for a 90-second NVENC one is
not a fallback. Anyone who configured a worker wants the worker, so the surprising behaviour is
the opt-in. The NAS profile then sets nothing here, and the matrix pins what it already pins.

**Preflight** gets the row described in S3. It is the first remote-service row of its kind: there
is no inference row in `preflight.py` today, which is its own small debt, and the caption
endpoint check is the template both should follow.

**The CLI**, when the worker is down before a render starts: `generate` refuses with the endpoint
named and the reason, and exits non-zero. The plan is already banked in the attempt, so the run
is resumable once the worker is back and nothing is re-read or re-decided.

**The CLI**, when the worker dies mid-render: the poll fails, the attempt records `failed` with
the endpoint in the detail, and the film is not half-written because
`publish_validated_output` only replaces the final path from a validated sibling. With
`fallback_to_local: true` the local render starts from the same certified binding, so the film is
the same cut and only the encode differs, and the run summary says which encoder produced it.

**The web**, same two cases: the Memory page's cut-in-progress view already polls the attempt
tree and already has a Cancel, so a failed poll shows the existing failure state with the
endpoint in the detail. Cancel sends `DELETE /render/{job_id}` before it releases the attempt
lease, so a cancelled tab does not leave a card encoding for another twenty minutes.

## 6. How this sits on the Postgres program (#871)

The store is coming: one PostgreSQL database with pgvector, settings, people, run history and
the operational tables, with per-attempt records staying as files indexed by `pipeline_runs`.
Slice P3 is the one that owns operational tables. When it lands, this handoff should become a
row rather than an HTTP submit, and the point of this section is to say what to build now so
that move is a transport swap and not a redesign.

**Build now:**

1. **A job record shaped like a row.** One JSON document per job under the scratch root, with
   exactly the columns a `render_jobs` table wants: `job_id`, `memory_key`, `plan_digest`,
   `status`, `phase`, `submitted_at`, `started_at`, `finished_at`, `worker_id`, `error`,
   `render_metrics`, `probe`. No field that only makes sense as a file.
2. **A `RenderJobStore` Protocol with three methods**: `claim`, `update`, `read`. One
   file-backed implementation now, a Postgres one in P3. The FastAPI routes talk to the
   Protocol, never to the filesystem.
3. **Identity that is already durable.** `(memory_key, plan_digest)` are facts of the attempt,
   both computed before any transport exists. A queue keyed on them needs no migration.
4. **Status as a forward-only enum**: `queued`, `running`, `succeeded`, `failed`, `cancelled`.
   `OperationalPhase.order` already proves the pattern works for phases.
5. **A self-contained envelope.** The worker never reads the app's attempt directory. Everything
   it needs is in the job. This is the single decision that makes a shared store optional rather
   than required, and it is why the HTTP path keeps working for a NAS that is not in the cluster.

**Do not build now:** a shared volume between app and worker, multi-worker claims, retries with
backoff, a dead-letter table. When P3 lands, `POST /render` inserts a `queued` row, the worker
loop claims with `SELECT ... FOR UPDATE SKIP LOCKED`, and the HTTP surface stays as the path for
callers outside the cluster. Attempts and outputs are already shared by then, so the job row
joins `pipeline_runs` on the run id and the film's `render_metrics` land where every other
render's do.

## 7. What it does not do

- **No footage through the NAS.** The worker fetches originals and playback renditions from
  Immich itself with the credentials in the job. The only bytes that cross the NAS are the job
  envelope up and the finished mp4 down.
- **No multi-tenant.** One token, one job at a time, no per-caller isolation, no quota. Two
  households wanting one GPU box run two workers.
- **No music generation.** ACE-Step and MusicGen stay where they are. The app resolves the track
  and sends it. Moving generation onto a GPU is its own issue and its own measurement.
- **No picture facts.** That is the inference service, and it already works.
- **No delivery.** The app still uploads to Immich, because the app holds the album choice and
  the run record.
- **No film storage.** One fetch, then the scratch directory goes. The worker is not an archive.
- **No cross-version tolerance.** Worker and app must report the same version on `/health` or
  the job is refused. A renderer and a planner that disagree about `editorial-timing-zero-overlap-v1`
  produce a film nobody asked for.

## 8. Slices

Each one is a PR of at most 300 lines with its test, in order.

**S1: the job API and an in-process worker behind a flag.** `RenderWorkerConfig` in
`config_models_render.py` (`worker_base_url`, `worker_token`, `timeout_seconds`,
`fallback_to_local`, an `enabled` property, and the same URL validator `InferenceConfig` uses,
which rejects credentials, query and fragment). `services/render/immich_memories_render/` with
the six routes, the `RenderJobStore` Protocol, its file implementation, and a runner that calls
the existing render path in process.
*Test:* `TestClient(app)` submits, polls to `succeeded` and fetches bytes that
`probe_output` accepts; the same envelope submitted twice returns one job id; an envelope whose
`source_ids` disagree with its binding is refused by `prepare_certified_timeline` with the
message that already exists. Mirrors `tests/test_remote_facts_service_parity.py`, which mounts
the service app at the configured base URL.

**S2: the container and the compose profile.** `command:` override on the app image, the `render`
profile, the scratch volume, the commented GPU reservation, `RENDER_WORKER_TOKEN` in both
`.env.example` files, and the `/health` encoder proof wired to
`scripts/verify_hardware_encode.py`'s path.
*Test:* `tests/test_render_worker_image_contract.py` on the model of
`test_inference_image_contract.py`, plus `make compose-check`, which already proves the published
file parses alone in an empty directory.

**S3: the overlays and preflight.** The three overlay directories. `check_render_worker(config)`
in `preflight.py`, modelled on `check_caption_endpoint`: `SKIPPED` when `worker_base_url` is
blank, `ERROR` naming the endpoint when unreachable, `ERROR` when the token is refused, `ERROR`
when `/health` reports a software encoder on a worker you deployed for NVENC, `OK` with the
encoder name in the message. Added to `run_preflight_checks` and to the hand-maintained bullet
list in the `preflight` command's docstring.
*Test:* a kustomize build assertion per overlay (the CUDA one asserts
`NVIDIA_DRIVER_CAPABILITIES` contains `video`), plus the four preflight outcomes against a
stubbed transport, on the model of `tests/test_preflight.py`.

**S4: progress, and what happens when the worker dies.** The client in
`processing/remote_render.py` (mirroring `analysis/remote_facts.py`: `httpx.Client`,
`trust_env=False`, a bounded `detail` echo, a `RemoteRenderError`). Progress feeding
`phase_callback` and `announce_stage`. The fallback rule. Cancel from both surfaces wired to
`DELETE`.
*Test:* the worker stops answering at 50 %; with `fallback_to_local: false` the cut fails naming
the endpoint and the attempt records `failed`; with it true, the local render runs from the same
plan and its film still passes `validate_output`. The progress test asserts the CLI and the web
read the same two records, and that a replayed earlier phase does not rewind the bar.

**S5: docs and matrix cells.** The running-modes table gains a "where the render runs" row with
the section 1 numbers. A `docs-site/docs/deploy/installation/render-worker.md` page on the model
of the inference-service page. Config reference entries. Two matrix cells,
`nas-rules-gpu-render` and `nas-hosted-gpu-render`, with `render_overlay: true` and
`render.fallback_to_local: false` pinned for the reason every service cell pins the facts one:
with the fallback on, an unreachable worker is answered by the local encoder and the row reports
a time for a setup that never ran. `render_overlay_steps()` in `setup_matrix_plan.py` beside
`inference_overlay_steps()`, applying and deleting `render-lan` the same way.
*Test:* `make docs-config-check`, `make docs-voice`, `make docs-build`, and the
`tests/test_setup_matrix_plan.py` extension that asserts the new steps and the pinned keys.

## 9. What could make this wrong

- **NVENC on this image is unproven end to end.** Every k8s cell in the 2026-09-14 run encoded on
  CPU. `overlays/gpu` claims the app image renders on a card, and the only measured GPU encode is
  a synthetic two-second 720p clip in an isolated pod. S2's `/health` proof exists because of
  that gap, and S2 is not done until a real 54 s film comes out of the worker.
- **The GPU title kernels are a separate risk from NVENC.** `init_kernels()` returning `CPU` on a
  GPU node is a legal outcome, and the kernel backend probe runs in a child interpreter precisely
  because a non-AVX CPU used to SIGILL inside the native load. A worker with NVENC and CPU titles
  is still a large win, and `/health` must let you tell the two apart.
- **The size bar may fail.** NVENC gets no quality arguments today. If the worker is 13 times
  faster and 3 times fatter, the honest answer is to fix rate control before shipping the cells,
  not to publish the speedup alone.
- **The Immich key crosses the LAN in a job body.** Mitigated, not solved, until scoped keys
  exist. Section 3 says where that work goes.

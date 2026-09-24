# Render worker

One GPU lane, an authenticated job API, and direct Immich downloads. The worker
runs the existing extraction, title and assembly code. It does no selection,
model captioning, music generation or Immich upload. The CLI and web UI use it
when `render.worker_base_url` is configured.

Use the same application revision on the worker and its submitting client.
Install this package into the app environment with
`uv pip install --no-deps -e services/render-worker`, then run
`python -m immich_memories_render_worker`.

NVENC and the CUDA title kernels are preferred, not required. Measured on one
cluster node, same cut, same reader, only the encoder differing: NVENC finished
in 219 s against 258 s for libx264. The card is worth about 1.65x on the encode
stage and about 15% of the whole render, because fetching the originals is
roughly half of it. So a worker that cannot open NVENC renders the film anyway,
about 15% slower, and reports the degradation on `/health` and in the job
record. A dropped selected asset is still a failed job, never a different film.

## Deploy and connect

The app image includes the worker. Use the same versioned image for both roles;
the client refuses a different app version before submitting footage.

For Docker with the NVIDIA Container Toolkit installed, copy `compose.yaml` to
an empty directory. Set these variables in that directory's `.env`:

```dotenv
IMMICH_MEMORIES_IMAGE=ghcr.io/sam-dumont/immich-video-memory-generator:YOUR_APP_TAG
IMMICH_URL=https://photos.example.com
RENDER_WORKER_TOKEN=replace-with-a-random-shared-token
RENDER_BIND_ADDRESS=127.0.0.1
```

Generate a token with `openssl rand -hex 32`, then run `docker compose up -d`.
For a NAS connecting across your private LAN, set `RENDER_BIND_ADDRESS` to the
worker's LAN address. The loopback default is for a reverse proxy on the worker host.

For Kubernetes, copy `kubernetes.yaml`, replace its image with the app's versioned
image, and create the `immich-memories-render-worker` Secret in the same namespace.
It needs two keys: `token` (the shared worker token) and `immich-url` (the same URL
configured on the app). Apply the manifest. It requests one NVIDIA device and
uses the `nvidia` runtime class; use the runtime class provided by your cluster.
The Service is internal. A NAS outside the cluster needs your private ingress
or load balancer pointing to its port 8093.

On the app, add:

```yaml
render:
  worker_base_url: https://render.example.com
  worker_token: ${RENDER_WORKER_TOKEN}
  timeout_seconds: 3600
  fallback_to_local: false
```

Pass the shared token to the app process as `RENDER_WORKER_TOKEN`. Generation
then follows the normal CLI or UI flow. The worker receives the selected cut
and the app's own Immich API key (`immich.api_key`, not a narrower one), downloads
its sources and returns the base film. The app
checks the received bytes, canvas, cut duration and audio windows before music
and upload. A worker failure fails the run unless `fallback_to_local` is enabled.
That option renders the same selection locally and reports the fallback.
Run `immich-memories preflight -v` to check worker reachability, version, CUDA
title support and available encoders before generating.

Manual video cuts, still holds and stitched Live carriers are supported.
Output is H.264 or H.265 MP4. MOV and ProRes require local rendering. Selection
and speech-safe cuts happen before the handoff; orientation changes the canvas.

## Settings

Environment prefix: `IMMICH_MEMORIES_RENDER_WORKER_`. Not
`IMMICH_MEMORIES_RENDER_`: the app's own `render` section produces
`IMMICH_MEMORIES_RENDER__WORKER_TOKEN`, and on the shorter prefix the two
variables differ by one underscore while meaning opposite things.

| Suffix | Default | Meaning |
| --- | --- | --- |
| `TOKEN` | required | Shared worker bearer token |
| `IMMICH_URL` | required | The only accepted source server URL |
| `DIRECTORY` | required | Private scratch storage owned by this worker |
| `HOST` | `127.0.0.1` | Bind address |
| `PORT` | `8093` | HTTP port |
| `MAX_JOBS` | `4` | Queued, running and unretrieved jobs combined; maximum 32 |
| `RETENTION_SECONDS` | `3600` | Terminal job lifetime; 60 to 86400 seconds |
| `JOB_TIMEOUT_SECONDS` | `3600` | A render past this is abandoned and its scratch released |

### What the worker holds

Every job carries the app's full Immich API key: the same key the app uses, with the
same permissions. There is no separate, narrower key yet. The worker uses it only to
download the selected originals from the one Immich URL it was started with, keeps it
in memory for the job and redacts it from messages, but anyone who controls the worker
process can read it. So run the worker where you would run the app. If the app does not
upload back to Immich, give it an Immich key with read and download permissions only;
that is then all the worker holds too.

Use a trusted network or a TLS reverse proxy. Every operation below requires
`Authorization: Bearer <worker token>`. The per-job Immich key belongs in the
request body, never a URL. Access logs are disabled by the entry point.

## Job API, version 1

`GET /health` returns `ready`, `accelerated`, `titles`, `encoders`, `worker_id`,
`started_at`, `app_version` and `contract_version`. `accelerated` is true only
when the title kernels report CUDA and NVENC opened; a deployment that asked for a card and reads false has a
misconfigured `NVIDIA_DRIVER_CAPABILITIES`.

`POST /jobs` accepts this shape and returns HTTP 202 with a status record:

```json
{
  "version": 1,
  "memory_key": "example-cut",
  "immich": {"url": "https://photos.example.com", "api_key": "<the app's Immich key>"},
  "plan": {
    "clips": [{
      "asset_id": "00000000-0000-4000-8000-000000000002",
      "start": 0, "end": 3, "render_mode": "motion"
    }],
    "transition": "crossfade",
    "transition_duration": 0.5
  },
  "memory": {"target_duration_seconds": 60, "memory_type": "monthly_highlights"},
  "titles": {"enabled": true, "title": "A day out", "locale": "en"},
  "timing": {"policy": {}, "timeline": {}, "source_ids": [], "sha256": ""},
  "certified_content_intervals": {},
  "output": {"codec": "h264", "resolution": "1080p", "orientation": "landscape", "crf": 23}
}
```

`timing` is `bind_editorial_timeline`'s output verbatim: the timing policy, the
frozen `TimelinePlan`, the ordered source ids and their digest. The worker
re-checks the digest, the source ids and the policy it can rebuild from the
envelope, and answers 409 when any of the three drifted, before it fetches a
byte. The binding is also what carries the title, ending and divider durations,
so the worker re-derives none of them.

`certified_content_intervals` has no default. An envelope that omits the key is
refused. For a merged Live carrier, send its `editorial_live_manifest` unchanged
in the clip's `live` field: version, canonical source material and selected interval.
That interval must match both the clip's start/end and its entry in
`certified_content_intervals`. Missing or changed trims return 409 before rendering.

The cut is ordered, with at most 500 distinct assets and one hour of source
intervals. `still` renders a photo, or a video's `render_frame_seconds`.
Moving Live Photos can use a video asset directly or a certified Live carrier.
The worker restores all source segments, including their trim points and shutter times.
Output is MP4, H.264 or H.265, at 720p, 1080p or 4K, landscape, portrait or square.
`hdr_mode`, `codec_policy` and `quality` use the app's output settings; omitted HDR
mode retains the original SDR default. Source audio is preserved; adding a
soundtrack belongs to the submitting app, which is why the result carries
`music_mute_windows`.

Clips also carry `rotation_override`, `audio_categories` and `llm_emotion`.
The `titles` object accepts the full title configuration; `memory` accepts
`person_name` and `preset_params`. `options` carries `scale_mode`, date/place
overlays, `privacy_mode` and `photo_duration`. These preserve the submitted film
and source-audio decisions when the worker rebuilds generation parameters.

A job is named after `(memory_key, plan_digest, render_attempt)`. The app creates
a fresh `render_attempt` UUID for each deliberate render, so changing output
settings or rendering again after a download works. Repeating the same request
keeps its job id and does not render twice. Without `render_attempt`, identity
uses the memory key and timing digest alone. Re-submitting a cut whose job failed
starts a fresh render. Changed content, including a changed Immich key, while
that job is live returns 409. Full capacity returns 429. A mismatched Immich URL
returns 422.

`GET /jobs/{job_id}` returns the job record: `job_id`, `memory_key`,
`plan_digest`, `worker_id`, `state`, `phase`, `progress`, `message`, `error`,
`submitted_at`, `started_at`, `finished_at`, and once a film exists `encoder`,
`encoding_plan`, `probe`, `render_metrics`, `clips`, `music_mute_windows` and
`degradations`. States are `queued`, `running`, `ready`, `failed` and
`consumed`. Messages and failures redact the Immich key. Feed `encoding_plan`
back into `publish_validated_output` to re-run the same three gates on the bytes
you received.

A job id this worker never saw answers 404 with `reason: unknown`; one whose
result expired answers 404 with `reason: expired`; one whose render died with a
previous process answers `failed` and says so. Both 404 bodies carry
`worker_started_at`.

`GET /jobs/{job_id}/output` atomically claims a ready film and streams it once.
A premature request returns 409; a second claim returns 410. A disconnected
first download is still consumed. Before readiness, the central output contract
decodes the full MP4 and checks codec, pixel format, container and colour
metadata.

## Storage and checks

Live job status is in memory; every transition is also written as one JSON
record per job under the scratch root, shaped like the row a `render_jobs` table
wants. That record is what lets a restarted worker answer "that render died with
its process" instead of the bare 404 an expired job and a stranger's id also
return. The store exposes atomic admission, update, claim, expiry and deadline
operations so a PostgreSQL implementation can replace it without changing the
wire contract. No database tables are introduced by this slice.

Each process owns a private scratch session, and every session a previous
process could not clean up is swept at boot, because a hard kill never runs
cleanup. A ten-second sweep removes expired terminal jobs, their films, and any
render past `JOB_TIMEOUT_SECONDS`. A wedged FFmpeg still holds the single lane
until the process restarts; the deadline frees admission and tells the caller,
it does not kill the subprocess. The process retains at most 128 status records,
including consumed and failed jobs, until their expiry.

From the repository root:

```sh
make -C services/render-worker dev
make -C services/render-worker test
make -C services/render-worker integration
make -C services/render-worker ci
```

The integration check uses a local HTTP source and real FFmpeg on a CPU host:
one case renders a film end to end and asserts the software encoder is reported
as a degradation, the other drops a selected asset and asserts the job fails
naming it. The Live integration case also renders two certified source clips,
checks the exact five-second title/content/ending timeline and decodes the retained
audio. The app-to-worker variant passed on a real NVIDIA T1000 with CUDA titles
and `h264_nvenc` on 2026-09-15. Explicit local fallback preserves that same cut
and audio window when the worker cannot be reached.

The same day's real Synology-to-T1000 replay kept all 15 clips from a saved
February cut: a 55-second, 1920×1080, 10-bit PQ H.265 film with audio. The worker
used `hevc_nvenc` and finished its job in 435 seconds; the NAS completed retrieval,
full decode validation and run finalization in 566 seconds overall. Local
fallback was disabled. No model calls were made. This is frozen-cut render
evidence; fresh preparation and reader measurements remain separate in #873.

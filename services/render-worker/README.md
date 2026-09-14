# Render worker (S1)

One GPU lane, an authenticated job API, and direct Immich downloads. The worker
runs the existing extraction, title and assembly code. It does no selection,
captioning, music generation or Immich upload. App-side handoff and deployment
profiles are later slices of #931.

Use the same application revision on the worker and its submitting client.
Install this package into the app environment with
`uv pip install --no-deps -e services/render-worker`, then run
`python -m immich_memories_render_worker`. FFmpeg must support NVENC and the
host must expose NVIDIA CUDA to the title kernels.

## Settings

Environment prefix: `IMMICH_MEMORIES_RENDER_`.

| Suffix | Default | Meaning |
| --- | --- | --- |
| `TOKEN` | required | Shared worker bearer token |
| `IMMICH_URL` | required | The only accepted source server URL |
| `DIRECTORY` | required | Private scratch storage owned by this worker |
| `HOST` | `127.0.0.1` | Bind address |
| `PORT` | `8093` | HTTP port |
| `MAX_JOBS` | `4` | Queued, running and unretrieved jobs combined; maximum 32 |
| `RETENTION_SECONDS` | `3600` | Terminal job lifetime; 60 to 86400 seconds |

Use a trusted network or a TLS reverse proxy. Every operation below requires
`Authorization: Bearer <worker token>`. The per-job Immich key belongs in the
request body, never a URL. Access logs are disabled by the entry point.

## Job API, version 1

`GET /health` returns `ready`, `titles` and `encoders`. Readiness requires CUDA
for titles and a working NVENC encoder. Each job checks its requested encoder
again. A software fallback is a failed job, never a successful GPU render.

`POST /jobs` accepts this shape and returns HTTP 202 with a status record:

```json
{
  "version": 1,
  "request_id": "00000000-0000-4000-8000-000000000001",
  "memory_key": "example-cut",
  "immich": {"url": "https://photos.example.com", "api_key": "<scoped key>"},
  "plan": {
    "clips": [{
      "asset_id": "00000000-0000-4000-8000-000000000002",
      "start": 0, "end": 3, "render_mode": "motion"
    }],
    "title": "A day out",
    "subtitle": "",
    "transition": "crossfade",
    "transition_duration": 0.5
  },
  "output": {"codec": "h264", "resolution": "1080p", "orientation": "landscape", "crf": 23}
}
```

The cut is ordered, with at most 500 distinct assets and one hour of source
intervals. `still` renders a photo, or a video's `render_frame_seconds`.
Moving Live Photos use their selected video asset in S1; merged Live bursts
need a later contract version. Titles are optional. Output is SDR MP4, H.264 or
H.265, at 720p, 1080p or 4K, landscape or portrait. Source audio is preserved;
adding a soundtrack belongs to the submitting app.

Retry the same `request_id` and identical body to get the existing job. Changed
content, including a changed scoped key, returns 409. Full capacity returns
429; unavailable GPU capability returns 503. A mismatched Immich URL returns
422. The worker does not persist the body or scoped key.

`GET /jobs/{job_id}` returns `job_id`, `memory_key`, `state`, `phase`, `progress`,
`message` and `error`. States are `queued`, `running`, `ready`, `failed` and
`consumed`. Messages and failures redact the scoped key.

`GET /jobs/{job_id}/output` atomically claims a ready film and streams it once.
A premature request returns 409; a second claim returns 410. A disconnected
first download is still consumed. Submit a new request ID to render again.
Before readiness, the central output contract decodes the full MP4 and checks
codec, pixel format, container and colour metadata.

## Storage and checks

S1 has in-memory status. Restarting loses job IDs; the client must submit again.
The store exposes atomic admission, update, claim and expiry operations so a
PostgreSQL implementation can replace it without changing the wire contract.
No database tables are introduced by this slice.

Each process owns a private scratch session. Shutdown removes it. A ten-second
sweep removes expired terminal jobs and their films. A hard kill can leave an
old session directory behind; remove those only while the worker is stopped.
The process retains at most 128 status records, including consumed and failed
jobs, until their expiry.

From the repository root:

```sh
make -C services/render-worker dev
make -C services/render-worker test
make -C services/render-worker integration
make -C services/render-worker ci
```

The integration check uses a local HTTP source and real FFmpeg on a CPU host,
then verifies that software fallback is withheld. Successful CUDA/NVENC output
still requires validation on an NVIDIA host before deployment.

# Launch Quality Follow-ups Design

Date: 2026-08-13

## Goal

Close the six quality gaps found during the real-library launch smoke test without changing the
daily smart-automation product model: one entry point runs each day, decides what should be made,
and either produces one varied memory or records why it skipped.

The changes must keep Immich v2 and v3 support automatic at runtime and explicit in the manual.
They must not enable upload or the scheduler during verification.

## 1. One output canvas and one photo transform

The photo renderer, title renderer, and final assembler will consume one resolved output canvas.
Resolution and orientation will be resolved once from the command/configuration and source clips,
then carried through `GenerationParams` into every downstream phase.

An explicit command resolution is authoritative. A 1080p landscape request resolves to
1920x1080 even when the application default is 4K or most selected photos are portrait. Automatic
resolution continues to inspect selected source clips, but it also resolves once before photo
rendering.

Still photos keep their native aspect ratio in a fixed window. A 4:3 photo inside a 16:9 video is
aspect-fitted once, with blurred fill around the unused canvas and one subtle Ken Burns movement
inside the fixed window. The final assembler receives an already matching clip and must not apply a
second orientation correction, aspect crop, or blur-background transform.

## 2. Content-analysis provider health and circuit breaker

OpenAI-compatible content analysis will no longer report itself as unconditionally available. At
the start of an analysis run, the application will make one bounded capability check using the
configured server and model. It will distinguish these useful states:

- endpoint unreachable;
- authentication rejected;
- chat-completions route missing;
- configured model missing or rejected;
- ready.

An optional provider failure does not fail video generation. It emits one clear warning, sets the
semantic-analysis weight to zero for the remainder of that run, and continues with motion, audio,
quality, favorite, face, and metadata signals. A permanent 4xx response encountered after a
successful probe opens the same run-level circuit breaker. Transient server errors may be retried
only within the existing bounded request policy.

Photo and video semantic scoring will share this provider instance and health state so a known-bad
provider is not independently retried for each photo.

The configured local state observed during review is explicit: the configuration names
`Qwen3-VL-8B-Instruct-MLX-4bit` at `http://localhost:9999/v1`; no server was listening on
2026-08-13, while the earlier France run reached a server that returned HTTP 404.

## 3. Honest final-duration budget

`--duration` means final playable video duration in both the CLI and web UI. The completed file may
exceed the requested duration by at most one second to allow frame and audio rounding.

At least 80% of the requested duration is reserved for actual photos and videos. Title material may
use at most 20%. The title planner uses this priority order when the configured cards do not fit:

1. preserve the opening title;
2. preserve an ending only when it fits the remaining title budget;
3. add chronological or location dividers only from the remaining title budget;
4. drop low-priority dividers before reducing content;
5. shorten or omit the ending for short targets rather than violating the 80% content floor.

The same title plan is used for budgeting and insertion. Month estimates exclude the first month,
because the opening title already introduces it. Trip estimates count location cards triggered by
the same 30 km rule used by insertion. Crossfade overlaps are calculated from the actual planned
timeline instead of a separate approximation.

After selection, content clips are scaled to the computed content budget without the existing 10%
overrun. After assembly, the generated duration is validated. An over-budget artifact is a
generation error rather than a silently misleading success.

Sparse libraries may legitimately produce a shorter video; the application does not duplicate or
stretch weak material merely to hit the target.

## 4. Useful `generate --dry-run`

`generate --dry-run` becomes a read-only planning run. It connects to Immich, resolves the API
version and named people, resolves dates or detected trips, fetches candidate metadata, runs the
normal cached/lightweight analysis and selection path, and prints:

- resolved memory type and date range;
- candidate counts by video, Live Photo, and photo;
- selected counts and estimated content duration;
- planned title cards and estimated final duration;
- resolved canvas, music policy, output path, and upload intent.

It does not render photos or titles, download full-resolution generation media, invoke FFmpeg for
final output, generate music, write an output video, upload to Immich, or send notifications.

Trip dry-runs use the same trip choice and geofilter as real generation. If required discovery or
selection fails, the command exits non-zero rather than printing a false success.

## 5. Optional PANNs semantics

PANNs stays an optional `audio-ml` extra because Torch and the model add substantial install size
and startup cost. When `audio_content.enabled` and `use_panns` are true, preflight and run startup
will report one of:

- semantic PANNs audio classification ready;
- PANNs unavailable, using energy-only fallback;
- audio-content analysis disabled.

The fallback remains non-fatal and runs without Torch. Documentation will explain that energy-only
analysis can find loud/quiet structure but cannot reliably label laughter, babies, speech, or music.
The documented installation commands remain `uv sync --extra audio-ml` and
`pip install immich-memories[audio-ml]` and will be verified against project metadata.

## 6. Quiet, visible notification health

Notification delivery remains best-effort and can never turn a completed video into a failed run.
Thumbnail attachments become opt-in because attachments consume more provider quota than text.

Delivery state is persisted alongside automation operational state without storing notification
URLs or credentials. It records the last attempt time, last success time, last failure category,
and a sanitized message. A failed delivery opens a 24-hour cooldown for normal success
notifications so repeated manual or automated runs do not hammer a quota-limited provider. Failure
notifications remain allowed once per cooldown window. `auto test-notification` explicitly bypasses
the cooldown because it is a user-requested diagnostic.

`preflight`, `auto status`, and the detailed health endpoint expose notification state as optional
health. A quota or transport failure is a warning, not a readiness failure.

## Error handling and privacy

All provider and notification diagnostics pass through the existing sanitization boundary. API
keys, Immich credentials, Apprise URLs, embedded notification credentials, and full remote response
bodies must not appear in logs, state, CLI output, or health responses.

No change in this batch enables upload, installs/loads the scheduler, or changes the daily variety
policy. The existing `MagicMock/` directory is user-owned and remains untouched.

## Verification

Each behavior is developed as a vertical red-green-refactor slice. Focused tests cover:

- explicit and automatic canvas resolution, including 4:3 into 16:9 without a second transform;
- provider capability states and one-run circuit breaking for video and photo scoring;
- 60-second and short timelines, 80/20 allocation, month and trip dividers, and one-second tolerance;
- read-only dry-run discovery/selection for people and trips;
- PANNs installed, absent, and energy-fallback reporting;
- notification cooldown, opt-in attachments, persisted sanitized health, and test bypass.

After focused tests, run the repository's lint/type/test gates. Then run read-only preflight and
dry-run checks against the owner's Immich v3.1.0 library. Generate a small local 720p or 1080p
smoke artifact with upload disabled to verify real duration and photo geometry before considering
the batch complete.

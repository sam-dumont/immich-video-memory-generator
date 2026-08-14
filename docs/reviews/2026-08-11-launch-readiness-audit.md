---
date: 2026-08-13
branch: codex/launch-hardening
scope: launch readiness, automation, performance, Immich v2/v3 compatibility, and user flow
verdict: code and real-library launch gates pass; scheduler activation remains an intentional deployment step
---

# Launch Readiness Audit — 2026-08-11

## Executive verdict

The original review found the application close to a public beta but unsafe for an unattended
stable launch. The blockers were contract failures around automation, Immich v3, output encoding,
versioning, and browser E2E—not a need to rewrite the video engine or compile Python with Cython.

Those P0 blockers and the approved P1 performance/operations work are now closed. The complete
launch gate passes. This checkout is suitable for promotion to a public beta. Unattended operation
still requires the owner to intentionally load the preserved daily scheduler; this review does not
silently turn it back on.

The most urgent problem was operational: the installed daily automation could report success
without producing a new video, retry bad candidates every day, and generate files without a
clear status surface. The installed LaunchAgent was unloaded on 2026-08-11 with the owner's
approval. Its plist remains on disk and nothing was deleted.

## Remediation status — 2026-08-11

- **P0.1 smart automation: fixed and independently approved.** The single daily entry point
  now returns typed outcomes, verifies the exact causal run and output, holds a process lease,
  preserves custom-config provenance, records UTC-aware durable history, and reports bounded,
  fully redacted failures. Schema upgrades are serialized across processes. The final review
  passed 149 focused tests plus deterministic and real-process migration diagnostics. Key final
  commits: `a0549cf`, `fc24d30`, `7af199f`, and `d4777b5`.
- **P0.2 variety: fixed and independently approved.** Automatic monthly work is limited to the
  latest completed month, hard category cadence/rotation rules are enforced without relaxation,
  and status/dry-run output explains rejections. The full automation controller gate passed
  318 tests with 16 live tests deselected.
- **P0.3 Immich v2/v3: fixed and independently approved.** Code and documentation now explicitly
  support both majors with automatic runtime detection by default. Duration normalization,
  version-selected uploads, offset-aware search dates, structured and transport-error redaction,
  all-client policy propagation, preflight resolution, and the read-only config check are closed
  through `144c38f`; public compatibility docs landed in `59e97b2`. The final compatibility gate
  passed 465 tests plus Ruff, format, mypy, and import contracts. Live read-only
  `immich-memories config test` passed with both default `auto` detection and an explicit `v3`
  override. Key final commits: `33745c6`, `5e46078`, `822a870`, `cecdf7a`, `c6cea3b`, `ee389a0`,
  `144c38f`, and `59e97b2`.
- **P0.4 output contract: fixed and independently approved.** One immutable encoding plan now
  controls final assembly, clips, titles, optional music, container choice, HDR policy, and
  hardware fallback. FFprobe validates codec, container, streams, dimensions, duration, and
  decodability before the artifact is published. Optional music or delivery failure cannot erase
  a valid video.
- **P0.5 version identity: fixed and independently approved.** Hatch VCS owns runtime and build
  identity. CLI, Python, health, wheel metadata, and Docker build metadata consume the same value.
  The final local package reported `0.37.2.dev113` on CLI, Python, and wheel metadata.
- **P0.6 required E2E: fixed and independently approved.** The required browser gate uses a local
  fake Immich v3 service, has no environment-based skip, drives Chromium through the real UI,
  renders a real video, and validates the output probe. CI and `make launch-check` require it.
- **P0.7 artifact delivery lifecycle: fixed and independently approved.** Artifact completion is
  durable before optional delivery. Failed uploads remain pending, the next daily run retries the
  oldest pending artifact before new generation, and the UI uses the same authoritative run.
- **P0 release gate: passed.** See the exact command evidence below. Documentation review also
  closed the one reported stable-JSON example omission.
- **P1 performance: fixed, measured, and independently reviewed.** Cache maintenance now scans
  once per batch, downloads prefetch through three bounded worker-owned clients, analyzers live for
  one batch rather than one clip, unchanged media metadata shares one ffprobe result per run, and
  unchanged trip-discovery inputs reuse a validated, freshness-checked trailing-year snapshot
  instead of paginating through the same assets every day. The deterministic structural gate
  passed 70 tests. Controlled medians changed from
  0.776/4.214/12.474 seconds to 0.688/4.151/13.002 seconds for minimal/typical/heavy workloads.
  The heavy +4.2% result remains a regression watch item, not a claimed improvement.
- **P1 profiling/CI: fixed and independently approved.** Controlled cold/warm profiles found
  FFmpeg/pipe I/O and native NumPy/OpenCV work, not a material pure-Python hotspot. The recorded
  decision is `no Cython`. Benchmark alerts require ten comparable histories, and both the gate
  and benchmark name bind comparisons to workload and environment identity.
- **P1 operational flow: fixed and independently approved.** CLI, UI, run history, and automation
  share discovery/download/analysis/selection/render/music/delivery/complete phases. Failed
  uploads stay retryable. `runs storage` is read-only. Legacy exact scheduling warns operators to
  use one daily `auto run` entry point.
- **P1 deployment guidance: fixed.** An unauthenticated non-loopback UI bind prints a startup
  warning. README, user, Docker, Kubernetes, Terraform, and authentication docs state that the UI
  is single-user, single-replica and must not be exposed with authentication disabled.

The LaunchAgent remains unloaded by explicit owner instruction. Completion does not reactivate
it; loading the job is a separate action that was not requested.

## Final P0 + P1 gate evidence — 2026-08-12

| Check | Result |
|---|---|
| Full pytest suite | 4,232 passed, 7 skipped, 658 deselected, 20 warnings |
| Required hermetic browser E2E | 24 passed, 0 skipped; Chromium rendered and probed a real video |
| Ruff | Clean; 510 Python files already formatted |
| Mypy | Clean across 244 source files |
| Structural performance gate | 70 passed |
| Trip-cache automation/API neighborhood | 293 passed, 16 deselected |
| Operational phase/storage gate | 13 passed |
| Docusaurus production build | Passed |
| Package build | `0.37.2.dev138` wheel and sdist built; Twine accepted both |
| Installed LaunchAgent | Not loaded; `launchctl print` exited 113 with “Could not find service” |

## Real-library launch validation — 2026-08-13

Four requested memories were generated against the owner's real Immich library with notifications
disabled and delivery not requested. All music was generated locally through ACE-Step 1.5 using
`acestep-v15-xl-turbo` on MPS/MLX with the 4B planner. Local Demucs produced four stems for ducking.
Hosted ACE-Step support remains available but was not used by these runs.

This validation found and closed two additional launch blockers:

1. The selector scaled to the duration target and then applied temporal deduplication, the
   non-favorite cap, and the photo cap. Those filters could remove most of the chosen timeline,
   but the optimizer never reconsidered unused candidates. A final backfill pass now consumes
   valid leftovers while preserving temporal uniqueness, same-day limits, non-favorite limits,
   and the remaining duration. It uses unused motion clips first. If strict photo balance still
   leaves a large hole, it may relax the photo cap from 50% to at most 70% rather than publishing
   a badly underfilled memory.
2. FFmpeg 8.1 single-pass `loudnorm` can emit NaN samples for short silent clips, which makes the
   AAC encoder abort with `-22 Invalid argument`. The audio graph now replaces only non-finite
   normalized samples with digital silence and restores a stable 48 kHz transition format.

Real selection evidence demonstrates the intended fallback behavior:

- Emile yearly fell from 185 seconds to 83.4 seconds after temporal and photo filters, then added
  16 unused clips and recovered to 185.3 seconds of a 187.5-second content budget.
- Somme fell to 93.1 seconds after filters. Strict 50% photos could not fill the remaining gap, so
  the selector used all valid motion, relaxed photos to the 70% ceiling, added 11 leftovers, and
  recovered to 156.1 seconds of a 157.5-second content budget.

| Memory | Final artifact | Duration | Probe/decode result |
|---|---|---:|---|
| Emile — June 2026 | `emile-june-2026-xl-music.mp4` | 59.917s | 1280×720 H.264 + stereo AAC; full decode passed |
| Emile — July 2026 | `emile-july-2026-xl-music.mp4` | 62.633s | 1280×720 H.264 + stereo AAC; full decode passed |
| Emile — 2026 yearly | `emile-yearly-2026-180s-xl-music.mp4` | 181.333s | 1280×720 H.264 + stereo AAC; full decode passed |
| Somme — July 25 to August 5 | `trip_somme,_france_2026-07-25.mp4` | 155.533s | 1280×720 H.264 + stereo AAC; full decode passed |

The final post-fix repository gate passed 4,334 tests with 7 skipped and 662 deselected. Ruff and
`git diff --check` were clean. The LaunchAgent remained unloaded throughout this validation.

The plist is still present at
`/Users/sam/Library/LaunchAgents/com.immich-memories.auto.plist`. No scheduler was loaded and no
user videos, run directories, caches, database rows, or generated output were deleted.

## Safety action already taken

- Unloaded job: `gui/501/com.immich-memories.auto`
- Preserved plist: `/Users/sam/Library/LaunchAgents/com.immich-memories.auto.plist`
- Verified state: `launchctl print` reports that the service is not loaded
- Deleted files: none
- Automatic reactivation: not performed; loading requires a separate explicit owner request

## Evidence baseline

### Quality gates

| Check | Result |
|---|---|
| Default fresh test suite | 3,469 passed, 20 skipped, 602 deselected, 22 warnings in 49.32s |
| Scheduler-focused tests | 67 passed in 0.35s |
| Ruff | Clean; 463 files formatted |
| Mypy | Clean across 224 source files |
| Import contracts | 2 kept, 0 broken |
| Audit-time tracked diff | Empty |

Pre-existing untracked directories `.playwright-mcp/` and `MagicMock/` were not touched.

### Performance baseline

Host used for the audit: Apple M5 Max, 40-core GPU, 128 GB RAM.

| Synthetic workload | Fresh-host result |
|---|---:|
| 2 × 3-second clips at 720p | 0.8s |
| 5 × 5-second clips at 1080p | 3.7s |
| 8 × 10-second clips at 1080p | 10.4s |

A real scheduled 4K memory spent roughly 80–180 seconds extracting and 190–350 seconds
assembling. That result points at FFmpeg work, I/O, repeated metadata/cache work, and process
orchestration—not pure Python arithmetic.

The current performance fixture is unreliable for duration comparisons:
`tests/integration/assembly/conftest.py` omits duration from generated fixture filenames, so
different benchmark scenarios can reuse a clip with the wrong duration.

### Installed automation history

The active scheduling mechanism was an operating-system LaunchAgent, not the built-in
`scheduler` section. It invoked:

```text
.venv/bin/immich-memories auto run --quiet --cooldown 24
```

at 03:00 daily.

Observed history:

| Observation | Count |
|---|---:|
| Attempts | 96 |
| Hard failures | 79 |
| Exit-zero/success log entries | 17 |
| False trip successes within those entries | 12 |
| Actual completed database runs | 5 |
| Cooldown skips | 7 |

Failed scheduled work since May left 58 videos using about 4.02 GiB across 62 run
directories. The five real successes account for about 0.23 GiB. The entire
`~/Videos/Memories` tree was about 57 GB, but that includes older demos and debug output;
it must not all be attributed to the scheduler.

## P0 — launch blockers

### P0.1 — Smart automation correctness — closed

Confirmed root causes:

1. `automation/runner.py::_build_generate_command()` sends a trip candidate using `--start`
   and `--end`.
2. `cli/_trip_generation.py` selects trips by index, month, near-date, or all-trips. The
   supplied start/end range is not used to select the detected trip.
3. No selected trip returns normally. The subprocess exits zero even though nothing was
   generated.
4. `AutoRunner.run_one()` then returns the most recent completed output from the database,
   which may belong to an older run.
5. Because no new memory key was completed, the same trip is eligible again the next day.

Additional automation defects:

- `on_this_day` has no explicit command mapping. An unknown candidate can fall through to a
  generic `generate` invocation.
- Failure logging captures stdout and stderr but reports only the last 500 characters of
  stderr. The useful exception is commonly lost.
- `check_immich()` catches raw `httpx` errors, while the API client wraps them as
  `ImmichAPIError`. A normal outage can therefore escape as a traceback.
- Generated runs do not receive `source=auto`; `auto history` is empty and cooldown uses
  unrelated manual/test runs.
- `None` represents cooldown, no candidate, dry run, and failure. The caller cannot tell a
  healthy no-op from broken generation.
- A subprocess exit code is treated as proof of a new output.
- The production database contains obvious fixture/benchmark rows: 507 completed, 78 failed,
  and 66 stale `running` records at audit time. Automation uses this database for dedup,
  scoring, and cooldown.
- The current candidate scorer limits list display but does not enforce cross-run variety.
  Monthly and activity-burst backlogs can win on consecutive days.

Closed result:

- One daily `auto run` entry point decides what, if anything, should happen.
- At most one generation candidate is attempted per invocation.
- Outcomes are explicit: skipped, dry-run, completed, or failed.
- Failure is nonzero and stops the invocation.
- Success requires a new matching run, matching memory key, and a validated output file.
- Only successful `source=auto` history affects automation cooldown and variety.
- A failed delivery retries the existing artifact before any new render.
- The legacy exact-time scheduler is deprecated and cannot activate implicitly.

### P0.2 — Automation variety controls — closed

The visible monthly flood is explained by two candidate sources:

- `MonthlyDetector` proposes up to six unfinished months.
- `ActivityBurstDetector` can emit additional past months as `monthly_highlights`.

Completing one candidate exposes the next monthly candidate on the following day. A score
penalty cannot guarantee variety when the backlog is large.

Closed result:

- Automatic regular monthly reviews consider only the most recently completed month.
- Older missing months are manual backfills.
- Regular monthly review runs at most once per calendar month.
- The same category cannot win twice in a row.
- A category can occupy at most two of the last six successful automatic generations.
- If all candidates violate variety rules, the day is skipped; constraints are not relaxed.
- Activity bursts have their own category and cadence.
- Person rotation accounts for both category and person identity.
- Dry-run and status output explain rejected candidates and applied variety rules.

### P0.3 — Immich v2/v3 compatibility — closed

The app supports Immich v2 and v3 explicitly in the code and documentation, with automatic
runtime selection by default.

Confirmed v3 breaks addressed by the compatibility layer:

- `AssetResponseDto.duration` is now a nullable integer in milliseconds. V2 strings and v3
  integers are normalized to seconds at the API boundary.
- V3 `POST /assets` no longer accepts `deviceAssetId` or `deviceId`. Upload fields are now
  selected from the resolved server major before bytes are sent.
- V3 validation errors are structured and correlation ID moved to the
  `X-Correlation-ID` response header.
- `/server/version` returns major, minor, and patch components. The validated server model now
  uses those fields and caches the resolved major per client.

Closed result:

```yaml
immich:
  api_version: auto  # auto | v2 | v3
```

- `auto` detects and caches the server major version.
- Explicit `v2`/`v3` overrides exist as manual troubleshooting escape hatches for unusual
  deployments and bypass detection by forcing the selected contract.
- Unsupported majors fail preflight.
- API models accept v2 duration strings and v3 millisecond integers, normalizing both to
  seconds at the boundary.
- Upload payloads are selected before the request; there is no risky upload-and-retry
  version detection.
- Search date bounds carry a UTC offset accepted by v2 and required by v3.
- V3 structured errors and correlation IDs are preserved in sanitized exceptions/logs.
- `immich-memories config test` provides a read-only authentication and compatibility check and
  reports the resolved API contract.

Final verification evidence:

- The complete compatibility gate passed 465 tests plus Ruff, format, mypy, and import contracts.
- Live read-only `immich-memories config test` passed in default `auto` mode and with an explicit
  `v3` override.
- Transport failures and diagnostics are redacted through `ee389a0` and `144c38f`.
- The public v2/v3 contract and mutation-sensitive docs tests landed in `59e97b2` and its follow-up.

Official references:

- [Immich v3.0 breaking changes](https://github.com/immich-app/immich/discussions/29439)
- [Immich v3.1.0 OpenAPI specification](https://raw.githubusercontent.com/immich-app/immich/v3.1.0/open-api/immich-openapi-specs.json)
- [Immich v3.1.0 release](https://github.com/immich-app/immich/discussions/30359)

### P0.4 — Output codec and hardware settings — closed

The audited user configuration requested 4K H.264. Produced files were 4K HEVC.

Root cause:

- `_build_assembly_settings()` stores `output_codec`.
- `AssemblyEngine` ignores it and calls the HDR/GPU resolver.
- On macOS that resolver selects `hevc_videotoolbox`.
- Final assembly also ignores `hardware.enabled`.
- `AssemblySettings.preserve_hdr` defaults to true and is not derived from output config.

Closed result:

- Codec selection is authoritative: H.264, H.265, and ProRes produce their requested codec.
- Disabling hardware uses the matching software encoder.
- Missing hardware falls back within the same codec.
- H.264 and ProRes are SDR; H.265 may preserve HDR when configured. Explicit HDR with H.264 or
  ProRes is rejected before rendering.
- Container/codec combinations are validated before rendering.
- `ffprobe` validates the final codec, container, streams, resolution, and duration.

### P0.5 — Version reporting — closed

Observed versions:

- Git/tag line: around `v0.37.2`
- `immich-memories --version`: `0.2.0`
- Editable distribution metadata: `0.36.6.dev5`
- Docker argument/label and semantic-release configuration: `0.2.0`

Closed result: Git-derived build metadata is the single source for CLI, UI, health,
Python package, Docker metadata, and releases.

### P0.6 — Browser E2E launch gate — closed

Confirmed gaps:

- `E2E_TEST_UPDATE_NOTES.md` says a selector must be fixed and 50+ screenshots recaptured.
- The broken selector remains in `tests/e2e/test_screenshots.py` and is hidden by
  `except: pass`.
- Server startup failures and missing core controls can become skips.
- The full-generation browser test is excluded from the normal `make e2e` path.
- PR CI excludes integration/E2E; private post-merge jobs do not run browser E2E.
- The UI music path creates a tracker without starting a run, leading to swallowed foreign
  key warnings and missing phase statistics.

Closed result:

- Deterministic browser smoke runs in required CI without a live personal Immich server.
- Startup and primary controls fail when absent.
- Only explicitly external live-server suites may skip for missing environment.
- A tiny full flow validates an actual output artifact and the configured codec.
- Screenshot maintenance no longer hides selector failures.

## P1 — closed after P0

### P1.1 — Performance work should target measured I/O and FFmpeg costs

Closed changes:

- Cache maintenance scans once at batch start rather than after every download.
- Three worker-owned clients overlap network downloads; FFmpeg extraction stays sequential.
- Analysis services reset per video and close once per batch; full GC is not forced per clip.
- One per-run probe cache supplies duration, resolution, codec, HDR, audio, rotation, and frame
  rate for unchanged media.
- Trip discovery hashes the server, API-key scope, and monthly bucket counts without persisting
  either secret. A cache hit is reused only after a one-result `updatedAfter` query confirms that
  Immich metadata has not changed. Both rolling-window boundaries are validated against a seven-day
  coverage horizon. Corruption, expiry, a freshness error, or a failed write falls back to the full
  Immich query. The final independent review found no Critical or Important issues.
- Controlled profiles and exact before/after medians are saved in the performance review.
- Cython was rejected because the measured pure-Python wrappers remain below 5% end-to-end.

### P1.2 — The built-in exact scheduler is a second, weaker product

Confirmed defects in the legacy daemon:

- Configured timezone is ignored; scheduling is UTC.
- Jobs sharing a start time can be skipped.
- Long jobs miss later schedules.
- No durable queue, catch-up, lease, or retry model exists.
- Status reflects configuration rather than daemon liveness.
- Album templates can be passed literally.
- Docker starts only the UI and does not supervise this daemon.

Closed decision: do not build a second scheduling platform. The legacy commands remain for one
compatibility release but print a deprecation warning. Setup documentation uses external timers
that invoke the canonical `auto run` command.

### P1.3 — The main flow hides expensive work and operational state

The four-page UI is coherent, but Step 2 hides discovery, analysis, selection, and refinement
behind a broad loading state. In-process session state also means restarts lose the workflow
and multiple UI replicas are unsafe.

Closed improvements:

- One shared operational phase model now drives CLI, UI, automation, and run status.
- Failures retain their actual phase; a failed delivery retains the validated output and becomes
  the next automatic retry before new rendering.
- Automation status exposes the last attempt/phase, decision, failure, pending delivery, cooldown,
  category history, and candidate rejections.
- `runs storage` classifies configured output/cache roots without deleting or changing anything.

### P1.4 — Deployment exposure guidance — closed

P0 fixed the Docker home/mount mismatch, made optional dependency selection fail closed, and split
process liveness from dependency readiness. P1 adds a startup warning for non-loopback binds with
authentication disabled. Every main deployment entry point now says the UI is single-user,
single-replica and explains that a shared secret or volume does not make multiple replicas safe.

## Read-only storage audit — 2026-08-12

The final storage report inspected only configured roots and did not change the database or delete,
rename, or upload anything.

| Area | Bytes | Approximate size | Files |
|---|---:|---:|---:|
| Total classified storage | 6,700,673,589 | 6.24 GiB | 3,357 |
| Preview cache | 5,455,153,242 | 5.08 GiB | 267 |
| Thumbnails | 546,883,272 | 0.51 GiB | 2,729 |
| Video cache | 534,583,336 | 0.50 GiB | 6 |
| `photos_test` | 164,053,739 | 0.15 GiB | 355 |

The report found four unknown cache directories and one empty orphan output directory:
`/Users/sam/Videos/Memories/everyone_june_2024_memories_20260811_205324_8b49`. It remains in place.
The configured output root also contains a 75,780-byte `.DS_Store`; there are no material loose
video files there. Cleanup remains a separate owner-approved operation.

## Approved product direction

The owner approved the following direction during review:

1. Make smart automation the canonical workflow.
2. Wake it daily and let the application decide what it should do.
3. Attempt at most one generation candidate per day.
4. Stop and report on candidate failure.
5. Enforce hard variety limits, especially for monthly reviews.
6. Support Immich v2 and v3, defaulting to automatic detection.
7. Be explicit about supported majors and overrides in documentation/manuals.
8. Complete every P0 before beginning P1 optimization or polish.

The implementation design is saved in
[`docs/superpowers/specs/2026-08-11-launch-hardening-design.md`](../superpowers/specs/2026-08-11-launch-hardening-design.md).

## Existing data policy

This audit did not delete, move, or rewrite generated videos, run directories, caches, or
database rows. Cleanup requires a backup, a concrete manifest, and separate owner approval.

## CI promotion addendum — 2026-08-12

### Cognitive-complexity gate

The checked-in snapshot was created with `complexipy` 5.2.0, but the Make target installed the
latest release on every run. Version 7.0.0 changed scoring and made the existing watermark
non-comparable. The gate also discarded the analyzer exit code, so an installation or analyzer
failure was incorrectly reported as “all functions under threshold.”

Closed contract:

- Pin `complexipy==5.2.0` until a separate, reviewed analyzer migration regenerates the baseline.
- Fail closed when the analyzer process exits non-zero.
- Keep snapshot-watermark parsing because 5.2.0 reports new violations in output while exiting
  zero.
- Use only the basename exclusion supported by 5.2.0. The previous path/glob exclusions did not
  work; CLI and Taichi debt remains visible in the snapshot.

Every branch-introduced or increased function above 15 was reviewed before refreshing the
watermark. The launch branch keeps these as explicit post-launch refactoring debt; none is hidden
by an exclusion.

| Function | 5.2 score | Review disposition |
|---|---:|---|
| `BirthdayDetector.detect` | 17 | Accept bounded birthday eligibility/candidate construction orchestration. |
| `AutoRunner.suggest` | 21 | Accept one Immich discovery transaction plus detector/ranking orchestration. |
| `AutoRunner._run_one_under_lease` | 25 | Accept durable retry/cooldown/generation failure state machine. |
| `register_config_commands` | 52 (from 44) | Accept existing Click registration closure; growth is v2/v3 configuration reporting. |
| `register_generate_commands` | 188 (from 177) | Accept existing Click registration closure; growth wires automation metadata into generation. |
| `_generate_memory_inner` | 21 | Accept top-level operational phase and run-tracker orchestration. |
| `_extract_clips` | 25 (from 21) | Accept download-prefetch, photo/video, probe, and skip boundary in the existing loop. |
| `_download_burst_clips` | 16 | Accept cache-batch and direct-download compatibility boundary. |
| `ClipEncoder.encode_single_clip` | 18 | Accept same-codec hardware-to-software retry state machine. |
| `resolve_encoding_plan` | 21 | Accept validated codec/HDR/hardware policy resolution as one atomic decision. |
| `_detect_hdr_type` | 17 | Accept cached-probe and direct-ffprobe compatibility path. |
| `_frame_rate` | 17 | Accept rational/decimal/invalid ffprobe fallback parsing. |
| `streaming_assemble_full` | 16 | Accept full-assembly resource and progress orchestration. |
| `assemble_streaming` | 24 | Accept streaming encoder setup, fallback, and final validation boundary. |
| `TitleInserter._pre_render_first_clip` | 16 | Accept first-title rendering and clip insertion boundary. |
| `TitleInserter._pre_render_last_clip` | 16 | Accept ending-title rendering and clip insertion boundary. |
| `configured_secret_values` | 23 | Accept exhaustive Pydantic/container traversal required for redaction. |
| `RunDatabase.update_run_status` | 18 | Accept optional-field SQL update construction with parameterized values. |
| `RunDatabase.update_operational_phase` | 21 | Accept monotonic pipeline/automation phase mirroring in one transaction. |

Behavioral evidence before snapshot refresh:

- Automation/calendar/delivery: 196 passed.
- Generation/download/encoding/HDR/probe: 307 passed.
- Streaming/title/security/tracking: 87 passed.
- Analyzer contract: pin, fail-closed status handling, and supported exclusions passed.

### CI-only launch repairs

The remaining failures on the older PR head were reproduced and closed on the final branch:

- The hermetic `launch-check` job now materializes Git LFS fixtures before running Ultra HDR tests.
- The Make prerequisite contract parser ignores GNU Make target-specific variable assignments and
  combines real prerequisite declarations.
- The duplication gate pins `jscpd@5.0.14` and removes the deleted `--gitignore` flag; jscpd 5
  respects `.gitignore` by default.
- Focused contract and Ultra HDR coverage passed: 24 tests.
- The duplication gate passed at 3.34%, below the 5% limit.
- Commit `1dc0c3f` passed every configured pre-commit policy gate, including diff coverage.

The next published matrix run exposed one cross-platform test-oracle bug rather than a product
failure. Python 3.11 and 3.12 on loaded macOS ARM runners completed the six-download prefetch in
0.393 and 0.462 seconds, missing a hard 0.350-second ceiling; both runs still observed all three
workers active concurrently, preserved result order, closed every client, and passed the other
4,238 tests. Python 3.13 on the same runner class and all Ubuntu versions passed.

The repaired contract removes only the environment-dependent wall-clock ceiling. It now verifies
parallelism through both the fake downloader's locked active-operation count and the coordinator's
worker count, while retaining the order, result, and cleanup assertions. All 11 focused download
coordinator tests pass.

### Dependency and benchmark evidence

The first published CI run exposed a reproducibility bug in the local dependency audit. The old
target audited `uv pip freeze`, so an already-updated local virtualenv could pass while CI installed
known-vulnerable versions from `uv.lock`. GitHub correctly failed the Security Scan with 107
fixable advisory rows across a much smaller repeated package set.

Closed contract:

- `make pip-audit` exports `--frozen --extra dev` from the committed lock instead of inspecting the
  caller's ambient environment.
- Direct dependency floors cover NiceGUI 3.12.0, Pillow 12.3.0, Pydantic Settings 2.14.2,
  Click 8.3.3, Authlib 1.6.12, and pytest 9.0.3.
- Transitive security floors remain UV constraints rather than fake application dependencies.
- The refreshed resolution includes aiohttp 3.14.3, NiceGUI 3.16.0, Pillow 12.3.0,
  cryptography 50.0.0, Starlette 1.6.0, GitPython 3.1.59, and yt-dlp 2026.7.4.
- NiceGUI 3.16 cancellation is normalized to `CancelledError` for background callbacks that
  promise a result; ordinary worker failures still propagate unchanged.

The frozen-lock audit now reports `No known vulnerabilities found`. Compatibility verification
passed 166 focused UI/auth/scene/build tests, Ruff, mypy across 245 source files, the full suite
(4,239 passed, 7 skipped, 658 deselected), package/Twine checks, the Docusaurus production build,
and all 24 Fake-Immich/Chromium launch E2E tests.

`make benchmark-assembly` passed all four tests in 69.65 seconds:

| Scenario | Clips/profile | Wall time | Peak child RSS |
|---|---|---:|---:|
| Minimal | 2 × 720p/3s | 0.7s | 415 MB |
| Typical | 5 × 1080p/5s | 3.9s | 961 MB |
| Heavy | 8 × 1080p/10s | 12.0s | 964 MB |

The generated `tests/perf-results.json` and `tests/benchmark-assembly.json` were inspected and then
restored to their exact pre-run SHA-256 hashes. The benchmark left no source or fixture changes.

### CI-equivalent local gate

The complete local gate ran on `74c8ab7193c93789313138410ca16c3398c46248` before publishing the
branch:

- Source/policy: Ruff, format, mypy, file length, Xenon, complexipy, Vulture, Bandit, refurb,
  deptry, import-linter, jscpd, critique, and pip-audit all passed.
- Commit messages passed Commitizen validation for `origin/main..HEAD`.
- Semgrep ran 777 rules over 249 tracked files with zero findings.
- The focused Docker, benchmark, and workflow contracts passed 72 tests.
- The hermetic launch gate passed 4,235 tests with 7 skips and 658 deliberate deselections.
- The launch package built as `0.37.2.dev142`; every existing and new wheel/sdist passed Twine.
- The Docusaurus production build passed.
- Fake-Immich and Chromium launch E2E passed 24 tests, including a real rendered video.
- The exact coverage job passed the same 4,235 tests at 71.99% branch coverage (minimum 65%).
- `diff-cover-ci` followed repository policy and skipped changed-line scoring because this launch
  refactor changes 10,901 source lines, above the 1,000-line safety threshold.

After removing only the generated Bandit/JUnit reports, the index and tracked worktree were clean;
the owner's pre-existing untracked `MagicMock/` directory remained untouched. The installed
LaunchAgent remained unloaded. Cross-platform Python and Docker-image variance is left for the
required GitHub matrix in the next promotion step.

## Real-library launch smoke test — 2026-08-12

The verified P1 worktree was exercised against the owner's live Immich 3.1.0 library with
`immich.api_version: auto`. Auto-detection resolved v3, both upload controls were disabled, and the
installed LaunchAgent remained unloaded. Three local 1080p H.264/AAC videos completed in sequence:

| Flow | Source/selection evidence | Wall time | Final artifact |
|---|---|---:|---|
| Emile Dumont, 2026 person spotlight | 378 videos, 212 Live Photos, 2,096 photos; 16 final clips | 2m45s | 73.27s, 21 MB |
| Sam Dumont, 2026 person spotlight + automatic ACE-Step music | 71 videos, 61 Live Photos, 444 photos; 13 final clips | 2m12s | 69.83s, 11 MB |
| Somme, France trip, 2026-07-25 to 2026-08-05 | 823 detected trip assets; 66 videos, 29 Live Photos, 728 geofiltered photos; 21 final clips | 6m31s | 91.65s, 35 MB |

`ffprobe` confirmed all three artifacts are playable MP4 files with 1920x1080 `yuv420p` H.264 video
and AAC audio. Eight-frame contact sheets were inspected for each output. The sampled frames had no
obvious corruption or unintended black content; portrait sources were fitted with the expected
blurred side fill, chronological interstitials were legible, and the France samples matched the
detected beach/trip content. All three runs are recorded as completed in the run database. Nothing
was uploaded to Immich.

The trip timing split was 166.8s analysis and 224.6s generation. Within generation, downloads used
107.4s and title/final assembly used 109.7s. This confirms that Cython is not the useful next
optimization: current latency is dominated by remote media I/O, FFmpeg/Taichi work, and avoidable
configuration/provider behavior.

### Live findings and ranked quality follow-ups

1. **P1 — propagate per-command resolution into photo animation.** The trip was requested at
   1080p and correctly assembled at 1920x1080, but photo intermediates were rendered at the
   configured 4K portrait size (2160x3840). This adds substantial HEIC/HDR animation and scaling
   work before the final downscale.
2. **P1 — fail fast or circuit-break unavailable content-analysis providers.** The configured
   OpenAI-compatible endpoint at `localhost:9999` returned HTTP 404 for chat completions. Scoring
   correctly fell back to non-LLM signals, but the pipeline retried the known-bad endpoint for many
   segment and photo candidates. Preflight reported this only as a warning. Either correct/disable
   that provider in deployment config or suppress it for the remainder of a run after a permanent
   4xx response.
3. **P1 — make target-duration semantics honest.** The two 60-second person requests produced
   73.27s and 69.83s artifacts because title/interstitial/ending material is added outside the
   requested content budget. Either budget title time inside the requested duration or label the
   option as content duration and report the estimated final duration.
4. **P2 — make `generate --dry-run` perform selection.** It currently returns immediately after
   displaying parameters, before connecting to Immich, resolving people, or counting matching
   assets. The command is safe but does not validate the proposed generation.
5. **P2 — restore optional semantic audio analysis where desired.** `panns_inference` is absent,
   so laughter/speech protection uses the less accurate energy-based fallback. This does not block
   launch; document the quality/installation tradeoff before making the dependency heavier.
6. **P2 — make notifications non-noisy under provider quotas.** The completed France run received
   an ntfy `429` daily-quota response after artifact finalization. Correct behavior was preserved,
   but notification health/configuration should be visible before an unattended rollout.

### Launch-quality follow-up closure — 2026-08-13

All six ranked findings above are closed on `codex/p1-contributor-followups`. The implementation
also closes two inconsistencies found only while planning against the real library: a 720p command
was still applying the 4K selection quality gate, and the density diagnostic could print a
negative raw budget even though the calculator used a valid positive budget.

| Finding | Closed result | Commit |
|---|---|---|
| One photo/video canvas; no stacked crop/scale treatment | One immutable canvas is shared by selection, photo animation, title rendering, dry-run preview, and final assembly. Aspect-fit photo treatment is applied once. | `11977b5`, `e6ded66` |
| Unavailable content-analysis model | Provider capability is probed once; permanent failures open a run-scoped circuit and use local scoring without candidate-by-candidate retries. | `0bbd72f` |
| Titles made a nominal 60-second memory substantially longer | One strict timeline plan reserves no more than 20% for titles and at least 80% for content before selection. | `bc420e6` |
| `generate --dry-run` did not validate the proposed memory | Dry-run now performs real discovery, cached/metadata analysis, selection, and timeline/canvas planning, then stops before media writes and delivery. | `cf4af1a` |
| Optional semantic audio status was opaque | Preflight and documentation explicitly report PANNs availability and the energy-based fallback. | `5a11fb7` |
| Notification quota failures were noisy | Schema v15 stores sanitized notification health; transport failures open a 24-hour cooldown, success closes it, and health surfaces warn without failing readiness. | `731b91b` |

The final local gate after the live-planning corrections passed:

| Check | Result |
|---|---|
| Full default pytest suite | 4,311 passed, 7 skipped, 660 deliberately deselected, 7 warnings in 219.61s |
| Focused canvas/timeline/dry-run/pipeline tests | 199 passed, 1 skipped |
| Ruff | Clean; 525 files already formatted |
| Mypy | Clean across 252 source files |
| Commit hooks | Ruff, format, mypy, secrets, size, modernization, complexity, dead-code, security, dependency, architecture, and duplication gates passed |

Read-only preflight initially connected to Immich 3.1.0 and resolved `api_version: auto` to v3.
Authentication, Apple M5 Max hardware acceleration, and notification configuration passed.
Preflight warned—without failing readiness—that the configured
`Qwen3-VL-8B-Instruct-MLX-4bit` model was unavailable and PANNs was absent, so local scoring and
energy-based audio analysis would be used. The preflight applied the expected schema-v15 database
migration. Upload remained disabled.

Three real-library dry-runs then exercised the launch flows without writing media, uploading, or
sending notifications:

| Flow | Discovery and selection | Planned output |
|---|---|---|
| Emile Dumont, 2026 person spotlight | 378 videos, 212 Live Photos, 2,096 photos; 12 photos selected; 47.0s content + 11.5s titles | 58.5s, 1280x720 |
| Sam Dumont, 2026 person spotlight | 71 videos, 61 Live Photos, 444 photos; 11 photos selected; 44.0s content + 11.5s titles | 55.5s, 1280x720 |
| Somme, France trip near 2026-07-28 | Four trips detected; the 2026-07-25 to 2026-08-05 Somme trip had 823 assets; 2 videos + 9 photos selected; 45.4s content + 11.5s titles | 56.9s, 1280x720 |

After the Immich host became reachable again, preflight authenticated against 3.1.0 and the three
post-fix flows rendered successfully in sequence. Each command requested a 60-second, 720p
landscape memory with photos and Live Photos enabled, medium quality, no music, no upload flag, and
notifications disabled by environment override.

| Flow | Selected clips | Wall time | Verified artifact |
|---|---:|---:|---|
| Emile Dumont person spotlight | 12 | 99.0s | 51.766667s, 6,746,310 bytes |
| Sam Dumont person spotlight | 10 | 61.7s | 54.333333s, 6,089,594 bytes |
| Somme, France trip | 11 | 76.9s | 49.333333s, 8,805,018 bytes |

FFprobe confirmed that all three are playable MP4 artifacts with 1280x720 H.264 `yuv420p` video
and AAC audio. The Emile and Sam flows exercised content-backed opening titles and bounded month
dividers. The trip flow exercised GPS selection, the animated map intro, real video segments, and
portrait photos. Contact sheets for all three were inspected. The frames were coherent and
uncorrupted; portrait/4:3 photos stayed in one centered aspect-fit window with one blurred side
fill. A two-frame comparison inside one Emile photo confirmed a single gentle motion treatment,
not stacked Ken Burns/crop effects. The apparent black final cell in the trip contact-sheet grid
was an unused tile: a direct extraction at 48 seconds showed the intentional blurred ending.

The live logs also confirmed the acceptance-only fixes: 720p runs applied a 540px quality floor
instead of the former 4K-derived threshold, density diagnostics reported positive effective raw
budgets, and one unavailable-model probe opened the run-scoped photo-analysis circuit. Every run
reported `Delivery not requested`; no artifact was uploaded to Immich and no notification was
sent.

Artifacts:

- `/Users/sam/Videos/Memories/launch-smoke-emile-2026-20260813_20260813_130744_1277/launch-smoke-emile-2026-20260813.mp4`
- `/Users/sam/Videos/Memories/launch-smoke-sam-2026-20260813_20260813_131040_5bd2/launch-smoke-sam-2026-20260813.mp4`
- `/Users/sam/Videos/Memories/trip_somme,_france_2026-07-25_20260813_131227_9489/trip_somme,_france_2026-07-25.mp4`

The installed LaunchAgent is still unloaded (`launchctl print` exits 113: service not found). It
was not modified or activated. The owner's untracked `MagicMock/` directory was not touched.

### Daily automation activation checkpoint

Smart discovery is healthy today and produced a varied candidate list: July monthly highlights,
the Somme trip, yearly reviews, person spotlights, on-this-day, multi-person memories, and another
trip. July monthly highlights scored first and Somme scored second. No successful automatic history
exists yet, so cooldown and recent-category variety state are intentionally empty.

The macOS LaunchAgent is installed but inactive. It points to the root checkout's virtualenv rather
than the verified launch/P1 worktree. Archived scheduler logs also show Immich connection timeouts
at 03:00, while interactive checks currently succeed. Do not load this preserved job as-is. After
the two PRs are merged and the final package is installed, reinstall the single daily smart entry
point from that stable executable, run one foreground `auto run --dry-run`, then load it only after
the three local artifacts have been reviewed by the owner.

### All-or-none month-divider closure — 2026-08-13

The follow-up Emile review found a real coherence defect in the strict title budget: the opening
was followed by April and May cards, while later selected months such as June and July had no
divider. The pre-selection planner had room for only two cards inside its 20% title allowance, and
the renderer then reapplied a per-month clip-count threshold. Both layers were doing what they were
written to do; together they produced a partial chronology.

Chronological month flows now have two explicit planning stages:

- Before selection, the plan reserves only the opening and ending and gives the remaining base
  target to content.
- After selection, the finalized immutable plan counts every selected month after the first,
  including one-clip months, and chooses the complete set or zero cards.
- A 60-second request has a conservative 70-second soft maximum. Selected content is not trimmed a
  second time to pay for cards, and transition overlap is not guessed during planning.
- Dry-run, rendering, logging, and final-duration validation consume the same finalized decision.
- Trip location cards and year dividers retain their prior capped behavior; the successful Somme
  flow was not changed.

Implementation commits are `06d7aca`, `832b33d`, and `52d8fc1`. The focused gates passed 182
unit/CLI tests and 133 CLI/assembly integration tests, plus Ruff and format checks. The normal full
repository gate passed 4,318 tests with 7 skips, 662 deliberate deselections, and 7 warnings in
109.85 seconds.

A broader `pytest -m "not e2e"` diagnostic was stopped at 4% after two unrelated live-library
failures in `TestPipelineOutput.test_has_video_stream` and `test_has_audio_stream`. Both use the
older direct `SmartPipeline.run()` test helper for a 15-second June 2025 request and fail before
timeline/title code: `ClipScaler` reduces seven protected candidates to zero selected clips. The
changed-area suites and the normal full gate remain green; this direct-pipeline edge case is a
separate follow-up rather than hidden evidence.

The live Emile dry-run used 720p landscape, medium quality, photos and Live Photos, fast analysis,
`--no-music`, notifications disabled, no upload flag, and a 60-second target. It selected 13 photos
for 51.0 seconds of content and reported:

```text
Month dividers: all 3 selected month changes
Title cards: 5 (13.5s)
Estimated final duration: 64.5s
Music: disabled
Upload: disabled
```

The corresponding real render selected 13 clips (11 photos and 2 video/Live Photo clips) for about
52.2 seconds of content. Its final plan reported all five selected month changes, a 69.7-second raw
estimate, and a 70.0-second soft maximum. Retained title files and a 1fps final-output contact sheet
confirmed the opening followed by every selected change: April, May, June, July, and August. No
partial prefix remained.

Final artifact:

`/Users/sam/Videos/Memories/launch-smoke-emile-month-dividers-20260813_20260813_162637_7ce7/launch-smoke-emile-month-dividers-20260813.mp4`

FFprobe and a full decode pass confirmed a 60.966667-second, 8,590,766-byte MP4 with 1280x720 H.264
`yuv420p` video and 48 kHz stereo AAC audio. A retained 4:3 photo inspected at 0.5 and 3.5 seconds
kept one centered aspect-fit window, one blurred side fill, and one gentle motion treatment; there
was no stacked Ken Burns/crop effect. The run completed with delivery status `not_requested`.
Music generation, notifications, and Immich upload were all disabled for the smoke. Production
automatic-music configuration was not changed.

## ACE-Step 1.5 direct-library benchmark — 2026-08-13

ACE-Step v0.1.8 (`dce6214`) was installed locally in the launch worktree as an inference-only
package and exercised directly through the Python library. This is not a subprocess or a local
HTTP server. The runtime used native MLX for the DiT/VAE and the 4B language model on an Apple M5
Max with 128 GB unified memory. The existing hosted REST implementation, including health checks,
authentication, polling, and downloads, remains available under `mode: "api"`. In `mode: "lib"`,
the configured ACE API remains the automatic fallback when the local package is absent.

The upstream full dependency set cannot be installed beside this app today: ACE-Step's Gradio UI
requires Starlette `<1`, while immich-memories requires Starlette `>=1.3.1`. The reproducible local
setup therefore pins v0.1.8 with `--no-deps` and installs its inference-only dependencies. This is
documented in the Audio & Music manual; an exact `uv sync` will remove the manual package, while
`uv sync --inexact` preserves it.

### Measurements

All media renders used the owner's live Immich 3.1.0 library with API auto-detection. Uploads and
notifications were disabled, and the macOS scheduler stayed unloaded.

| Workload | Result | Wall time | Relevant music time |
|---|---:|---:|---:|
| ACE base, cached cold process, 10-second WAV | 48 kHz stereo PCM | 27.887s | includes ~22s model initialization |
| ACE base, second 10-second WAV in same process | 48 kHz stereo PCM | 5.420s | warm model |
| ACE v0.1.8 XL-turbo + 4B LM, cached cold process, 10-second WAV | 48 kHz stereo PCM | 26.783s | native MLX; includes initialization |
| ACE v0.1.8 XL-turbo + 4B LM, second WAV in same process | 48 kHz stereo PCM | 2.707s | production-model warm result |
| Emile 2026, local ACE base + local Demucs | 64.967s, 14 clips, 10,204,559 bytes | 140.3s | 72.9s generation/stems/mix |
| Sam 2026, local ACE base + local Demucs | 61.333s, 11 clips, 7,560,058 bytes | 133.6s | 66.0s generation/stems/mix |
| Somme trip, no-music control | 56.333s, 13 clips, 9,495,535 bytes | 72.0s | disabled |

The very first ACE run took about 6m41s because it downloaded model weights. That one-time network
cost is deliberately excluded from cached performance. The warm result is the important automation
number: keeping one ACE backend alive across a daily batch saves roughly 22 seconds of model setup
per additional soundtrack on this machine.

The first benchmark used the 2B `base` DiT and was not the historical production-model comparison.
ACE-Step v0.1.6 introduced XL and changed its Gradio default to the 4B `xl-turbo`; the app's local
config and manual had since drifted back to the ambiguous name `base`. A corrected v0.1.8 run used
the full `acestep-v15-xl-turbo` checkpoint plus the separate 4B LM planner. Both WAVs decoded fully
at exactly 10 seconds, 48 kHz stereo. Cold time was essentially unchanged from 2B base, while the
warm 8-step XL run was 2.707 seconds instead of 5.420 seconds. The downloaded XL checkpoint occupies
about 19GB in the local Hugging Face layout, despite upstream's roughly 9GB bf16 weight estimate.

The corrected Emile render spent about 9.1s in analysis, 25.3s downloading selected media, 30.4s
assembling video, and 72.9s generating/separating/mixing music. Its ACE portion generated an
80-second base-model track at 50 steps: roughly 22s initialization and 40s generation, followed by
about 4.2s of local Demucs separation. Sam showed the same shape: 11.5s analysis, 35.8s downloads,
18.3s assembly, and 66.0s music. This again rules out Cython as a useful first optimization; the
time is in network I/O, FFmpeg, and model lifecycle.

### Root-cause fix found by the benchmark

The first new Emile run generated a valid ACE WAV, then discarded it. TorchAudio 2.10 now routes
audio loading through TorchCodec, which was not installed. The optional Demucs stage raised
`ImportError`, while the pipeline's optional-stem boundary caught only `RuntimeError` and
`OSError`. The final video therefore kept only source audio despite successful music generation.

Local Demucs now reads and writes audio through the already-installed SoundFile backend, avoiding
TorchCodec entirely. Its optional boundary also catches `ImportError`, so a missing stem decoder
keeps the valid full mix instead of throwing it away. The retained corrected runs contain the ACE
WAV plus vocals, drums, bass, and other stems; their final AAC tracks are non-silent and peak near
-7.5 dB.

The benchmark also found that saved per-phase timing started only after ACE generation and Demucs,
immediately before the final mix. The tracker now starts the music phase before resolution so future
run metadata covers the real model work. The coarse pipeline logs above were used for these numbers
because the completed artifacts predate that correction.

Final verification exposed and closed two more process-boundary defects. System-info capture called
`ti.init()` independently from the title subsystem; that reset Taichi's process-wide runtime while
the title module retained compiled Metal kernel references. A later globe dispatch then failed with
stale GPU objects. System capture now uses the title subsystem's idempotent Taichi lifecycle. The
minimal benchmark → tracked generation → globe/GPU reproduction changed from 14 failures to 35
passes. The profiling harness also inherited its deliberately forbidden `TMPDIR` into Apple's Git
launcher, which created `xcrun_db`; Git's temporary cache is now confined to an auto-cleaned folder
inside the caller-approved profile output.

The combined audio, delivery, profiler, Taichi, globe, and GPU regression set passed 368 tests. The
fresh normal repository gate then passed 4,326 tests with 7 skips, 662 deliberate deselections, and
7 warnings in 58.51 seconds. Ruff, formatting, and whitespace checks were clean.
Mypy reported no issues across 252 source files.

### Visual and flow review

FFprobe and full decode checks passed for all three videos. Emile and Sam are 1280x720 H.264 with
stereo AAC and completed without warnings. Their opening title covers the first selected month, and
every later selected month has a divider. Contact-sheet inspection found one centered aspect-fit
photo window with one blurred fill for portrait/4:3 media; no stacked or multi-Ken-Burns treatment
was visible.

Artifacts:

- `/Users/sam/Videos/Memories/demo-emile-ace-v15-base-fixed-20260813_20260813_182825_d9cc/demo-emile-ace-v15-base-fixed-20260813.mp4`
- `/Users/sam/Videos/Memories/demo-sam-ace-v15-base-20260813_20260813_183124_95cd/demo-sam-ace-v15-base-20260813.mp4`
- `/Users/sam/Videos/Memories/trip_somme,_france_2026-07-25_20260813_183406_47f2/trip_somme,_france_2026-07-25.mp4`

### ACE-Step release assessment

The correct baseline is [v0.1.6](https://github.com/ace-step/ACE-Step-1.5/releases/tag/v0.1.6)
with an XL model, normally `acestep-v15-xl-turbo`, not the 2B base model used in the first local
benchmark. Against that baseline:

- [v0.1.7](https://github.com/ace-step/ACE-Step-1.5/releases/tag/v0.1.7) adds DCW to every sampler,
  including XL and native MLX; shared `ACESTEP_CHECKPOINTS_DIR`; lower-memory, auto-tuned MLX VAE
  chunks; community VAE selection; correct `infer_steps` handling and forwarded handler kwargs for
  `xl_turbo`; and LM CFG prompt fixes. DCW is the meaningful XL-turbo quality change and costs
  negligible compute.
- [v0.1.8](https://github.com/ace-step/ACE-Step-1.5/releases/tag/v0.1.8) adds Retake controlled
  variations, Flow-Edit, better repainting on MLX, a fix for MLX DiT static buffers across threads,
  and official Docker images for hosted servers. Retake and the MLX thread fix are immediately
  relevant to smart automation; Flow-Edit/repaint belong to a later editing workflow.
- v0.1.7 also created a trap: `GenerationParams.dcw_enabled` defaults to true. v0.1.8 corrected the
  Gradio default for non-turbo models, but not direct-library, CLI, or REST callers. On Apple Silicon
  this can garble `xl-sft`/`xl-base`; the upstream fix is still open as
  [PR #1282](https://github.com/ace-step/ACE-Step-1.5/pull/1282). Production should remain
  `xl-turbo` with DCW enabled. Non-turbo XL must explicitly disable DCW until that fix ships.

Recommended order:

1. Restore `acestep-v15-xl-turbo` + 4B LM as the documented production profile.
2. Keep one loaded backend across automatic batch jobs. The corrected XL benchmark saves 24.076
   seconds between the first and second 10-second soundtrack.
3. Expose v0.1.8 Retake for cheap, controlled alternatives from one soundtrack.
4. A/B the community VAE before changing the default. Do not silently change production sound.
5. Test `xl-sft` only after adding an explicit DCW-off parameter; `xl-base` adds no value to the
   current text-to-music-only flow.

The active local configuration was migrated on 2026-08-13 from the ambiguous 2B
`model_variant: base` + 4B LM combination to `model_variant: "acestep-v15-xl-turbo"` + 4B LM.
Library mode, API fallback URL, one-version generation, Demucs, and all unrelated settings were
preserved. The executable `scripts/validate_local_audio.py --quality high` profile now exercises
the same production combination.

### Other local music models

The best next provider is
[Stable Audio 3](https://github.com/Stability-AI/stable-audio-3), not the old community MusicGen
MLX ports. Its official [pure-MLX runtime](https://github.com/Stability-AI/stable-audio-3/blob/main/optimized/mlx/README.md)
runs without PyTorch at inference time. `small-music` is a 433M model supporting up to 120 seconds;
`medium` is 1.4B and supports longer, higher-quality generation. Both support text-to-audio,
audio-to-audio, negative prompting, and inpainting. Add it as an opt-in provider after accepting and
documenting the Stability Community and T5Gemma model terms. `small-music` is the sensible fast
preview/fallback candidate; `medium` is the quality A/B candidate.

[HeartMuLa](https://github.com/HeartMuLa/heartlib) is Apache-2.0 and attractive for full songs with
lyrics and multilingual tags, but the official 3B runtime is CUDA-oriented and around real-time
speed. Its MLX implementation is community-maintained, so it is lower priority for short
instrumental photo memories. Community MLX ports of Meta MusicGen are also a poor launch default:
they are less mature and inherit non-commercial model terms.

### Remaining benchmark findings

1. **P1 — reuse the ACE runtime across a smart-automation batch.** The production XL benchmark
   measured a 24.076-second first-to-warm saving on this Mac.
2. **P1 — make trip duration planning match the rendered card set.** The Somme dry-run estimated
   64.9 seconds, but the actual artifact is 56.3 seconds because only the map intro and ending were
   rendered and transition overlap was not represented accurately. This misses the owner's
   preferred roughly 65-second result.
3. **P2 — correct saved target-duration metadata.** These 65-second CLI requests are recorded as
   target 60 even though the actual generation parameters received 65.
4. **P2 — avoid unnecessary 96 kHz AAC for ACE-mixed output.** The generated WAV is 48 kHz, but
   Emile and Sam were published with 96 kHz audio. It works, but doubles audio sample work for no
   obvious benefit.
5. **P2 — make `--quiet` quiet.** Candidate analysis and HTTP pagination still flood the console;
   this is an observability problem, not a measured performance bottleneck.

## HDR publication and duration-budget correction — 2026-08-13

The reported HDR failure was reproduced. The media pipeline detected HLG correctly, but the live
configuration selected `output.codec: h264`. Codec selection is authoritative and H.264 is an SDR
publication path, so `hdr_mode: auto` correctly tone-mapped the final result even though several
intermediate clips retained HDR metadata. The live configuration now uses `codec: h265` with
`hdr_mode: auto`. H.264 remains available as the explicit compatibility/SDR choice. A new warning
states exactly why detected HDR is being tone-mapped when H.264 is selected, and both the manual
and configuration reference document the contract. Mixed SDR intermediates are expected: final
assembly normalizes clips, photos, and titles into one 10-bit HLG timeline before blending.

The same real renders closed the earlier target-duration findings. Three separate accounting bugs
were involved:

1. Selection budgeted `content + titles = target`, but each fade removes transition overlap.
2. Trip planning reserved location-card time from the candidate pool instead of the final selected
   locations, so rejected locations consumed phantom title seconds.
3. Backfill required an exact fit and stopped when the remaining hole was smaller than every
   leftover clip, even when a safe clip could be trimmed.

Timeline planning now uses `content + titles - expected transition overlap = target`, finalizes
trip dividers from selected clips, and permits one constraint-safe backfill clip up to two seconds
over the remaining content gap for the normal final trim. Yearly month dividers remain all-or-none;
the opening covers the first selected month and all later selected months receive dividers when the
complete set fits. Dry-run, preview, generation parameters, and final rendering consume the same
finalized timeline decision.

Real-library evidence, with upload and notifications disabled:

- Emile June monthly: 60.917s, HEVC Main 10, `yuv420p10le`, BT.2020/HLG, AAC; full decode passed.
- Emile July monthly: 63.633s, same HDR contract, AAC; full decode passed.
- Emile yearly with all seven subsequent month dividers and ACE music: 179.300s, same HDR
  contract, AAC; full decode passed. The 0.700s delta is below one configured transition.
- Somme trip with 26 content clips, one selected location card, and ACE music: 150.683s, same HDR
  contract, AAC; full decode passed. The selector backfilled ten eligible leftovers, changing the
  previously short 130.533s result into a duration-correct publication.

Validated artifacts:

- `/Users/sam/Videos/Memories/emile-june-2026-hdr-xl-music_20260813_214159_9195/emile-june-2026-hdr-xl-music.mp4`
- `/Users/sam/Videos/Memories/emile-july-2026-hdr-xl-music_20260813_214357_32c8/emile-july-2026-hdr-xl-music.mp4`
- `/Users/sam/Videos/Memories/emile-yearly-2026-180s-hdr-xl-music-final_20260813_221122_8893/emile-yearly-2026-180s-hdr-xl-music-final.mp4`
- `/Users/sam/Videos/Memories/trip_somme,_france_2026-07-25_20260813_221557_c814/trip_somme,_france_2026-07-25.mp4`

The local Qwen warning was resolved on 2026-08-14. The authenticated `/v1/models` endpoint showed
that the old `Qwen3-VL-8B-Instruct-MLX-4bit` identifier had been removed. Qwen 3.5 VL 9B restored
scoring first, then the local A/B probe confirmed that native vision in
`Huihui-Qwen3.6-35B-A3B-abliterated-oQ4e-mtp` accepts the application's real `image_url` payload.
On the same photo and prompt, Qwen 3.5 VL 9B returned in 2.441s and Qwen 3.6 35B-A3B returned in
2.142s; both responses were parseable. The active local configuration now selects the 35B-A3B
model. The full provider preflight reports 5 OK and 0 skipped.

That live probe exposed a separate cache bug: 1,632 existing photo scores had no model identity,
and video semantic analysis had no model field at all. Changing models therefore reused old scores
and made a working VLM look as if it had no effect. Photo cache reads now require the exact model
already supported by `asset_scores.model_version`. Video analysis schema v16 adds the same identity
to each analysis run. Null or different model IDs are misses; successful semantic results replace
the row naturally; provider/download/parse failures still fall back to metadata but are never
stamped as model-authored scores. Existing rows and generated media were not deleted.

The first normal-cache Qwen 3.6 generation rejected six unversioned video analyses and refreshed
all 18 shortlisted photos. The database now contains six video analyses and 18 photo scores stamped
with the exact 35B-A3B model ID. Photo scores span 0.546–0.772. The resulting 11-clip Emile July
selection covers fountains, a ride, drawing, family contact, a close-up, and the beach rather than
collapsing into one repeated activity. It is 59.967s HEVC Main 10 HLG HDR with stereo AAC and passes
a full decode:

- `/Users/sam/Videos/Memories/emile-july-2026-qwen36-model-aware_20260814_082903_bb73/emile-july-2026-qwen36-model-aware.mp4`

The final cache review also closed two failure-mode holes. Video analysis now carries an explicit
confidence signal through the unified analyzer and stamps the configured model only when at least
one result meets `content_analysis.min_confidence`; neutral defaults from a failed provider call no
longer count as semantic success. Photo responses must contain finite numeric `interest` and
`quality` values in the 0–1 range. HTTP 200 responses with missing or malformed scores fall back to
metadata without creating a semantic cache row. Metadata-only runs also bypass thumbnail and
download I/O entirely. An independent follow-up review found no remaining critical or important
issues in this model-cache scope.

Final verification for this correction:

- Full repository suite after model-aware caching: 4,355 passed, 7 skipped, 662 deliberate
  deselections, 7 warnings.
- Focused HDR/timeline/backfill/docs suite: 311 passed.
- Real FFmpeg and CLI integration group: 60 passed, 1 skipped.
- Post-format affected regression set: 222 passed, 54 deselected.
- Mypy: no issues in 252 source files.
- Ruff lint: passed; Ruff format: all 526 Python files clean.
- Docusaurus production build and `git diff --check`: passed.

## HDR title continuity and publication quality — 2026-08-14

The visible exposure jump between a content-backed opening title and its first clip was a real
regression. The title pipeline treated every Taichi frame as SDR. For an HLG content background it
therefore decoded HLG to SDR, composited the title, then expanded the result back to HLG, while the
following content clip remained HLG throughout. The double transfer changed contrast, highlights,
and saturation at the cut.

The content-backed path now restores the previously working transfer-preserving design. HLG/PQ
background frames remain 16-bit in their existing transfer; the raw title input is tagged with the
matching BT.2020 transfer metadata; and the title encoder skips transfer conversion when the frame
transfer already matches the output plan. Synthetic gradients, maps, PIL fallbacks, and other SDR
title sources still use the high-precision SDR-to-HDR conversion. Regression tests assert both
branches explicitly.

The separate report that iPhone video looked like an SDR-to-HDR conversion was checked against the
exact selected marker-scene source (`e0cb27a5-6e4b-4ad0-8fc9-0a3db858c108`). The original is HEVC
Main 10, `yuv420p10le`, TV range, BT.2020/HLG, and also carries Dolby Vision RPU metadata. It was
correctly classified as HLG and never entered the SDR-to-HDR conversion branch. The historic
raw-pipe range fix remains active: decoded 10-bit YUV stays TV range, and the encoder input is
explicitly tagged before metadata is lost at the rawvideo boundary. No arbitrary exposure or
saturation correction was added.

The Apple VideoToolbox quality mapping is also explicit now. The configured CRF is translated into
VideoToolbox's quality scale and `quality` preset disables speed priority; CRF 18 resolves to
`-q:v 75 -prio_speed 0`. Effective hardware-to-software fallback is reported through final run
metadata rather than leaving the requested encoder recorded after a runtime fallback.

Real local-library validation with Qwen selection and local ACE-Step music completed successfully:

- `/Users/sam/Videos/Memories/emile-july-2026-4k-hdr-title-fixed_20260814_132025_566b/emile-july-2026-4k-hdr-title-fixed.mp4`
- 60.467s, 3840×2160 at 60 fps, HEVC Main 10, `yuv420p10le`, TV range, BT.2020/HLG.
- 1,169,050,248 bytes; video bitrate 154,460,436 bit/s; stereo AAC soundtrack generated locally
  through the ACE-Step MLX backend.
- The opening title and first content frame were extracted through the same deterministic HLG-to-SDR
  inspection transform; the prior title/content exposure jump is absent.
- Full repository suite: 4,395 passed, 7 skipped, 662 deliberate deselections.
- Frozen-tree title/HDR regression set: 201 passed.
- Mypy: no issues in 254 source files; Ruff lint and format: all 529 Python files clean.
- Docusaurus production build and `git diff --check`: passed.

## Realistic trip Auto duration and lossless selection — 2026-08-14

The Step 2 Somme flow exposed two separate bugs that amplified each other. A 12-day trip requested
seven minutes because the CLI and documentation still used 35 seconds per calendar day. The UI then
showed 61 clips but preselected 32 using raw source duration, and downstream selection filtered that
already-reduced set again. Density shortlists, photo VLM shortlists, the two-photos/day limit, photo
ratio, non-favorite ratio, and temporal spacing could all permanently discard otherwise valid media
before the optimizer knew it was short.

The new contract is explicit:

- Step 2 checked media is authoritative. All discovered videos and Live Photos start checked; checked
  photos join the same pool.
- Trip Auto uses `30 + 10 × active days`, bounded to 60–300 seconds for dense media. A seven-day trip
  starts at 100 seconds and a 12-day trip at 150 seconds.
- Media capacity is computed from usable excerpts, not raw source length. At most four photos and 30
  seconds of diverse capacity count per active day. Sparse trips may resolve below 60 seconds.
- UI, CLI, and scheduled trip automation resolve the same media-aware duration. A manual duration is
  still exact.
- Fast analysis bounds expensive work only. Every hard-eligible leftover video keeps a cached or
  metadata segment; every checked photo keeps a metadata score after the distributed VLM shortlist is
  merged back.
- Final selection relaxes preferences in order: strict, 70% photo ratio, additional non-favorites,
  temporal spacing, unrestricted photo ratio, then a bounded two-second fit overrun. It never relaxes
  explicit deselection, duplicate removal, minimum usable duration, or HDR-only mode.
- The completion card now separates eligible media, videos deeply analyzed, and clips planned.

Hermetic regression evidence uses the reported 12-day shape: 61 video/Live Photo clips, 48 photos,
19 favorites, and one lower-quality true duplicate. Auto resolves to 150 seconds; deduplication keeps
60 video candidates; the expensive shortlist is smaller than 60; every remaining video receives a
fallback; all 48 photos join the unified pool; and the planned content lands within one configured
average clip of the shared timeline budget.

Final verification for this correction:

- Focused Auto/selection/timeline suite: 203 passed, 6 deliberate integration deselections.
- Full repository suite: 4,429 passed, 7 skipped, 662 deliberate integration deselections.
- Mypy: no issues in 256 source files.
- Ruff lint and format: all 535 Python files clean.
- Docusaurus production build and `git diff --check`: passed.

## Model-aware analysis modes and visible cache reuse — 2026-08-14

The Somme review run showed 35 clips in the analysis phase even though 41 clips survived hard
eligibility. This was not an LLM outage. The density budget shortlisted 35 clips, Fast mode sent
only favorites through unified/LLM analysis, and the remaining clips received local scoring or
metadata fallbacks. The UI exposed two overlapping controls—Analysis Depth and “Analyze all
videos”—whose combinations did not match their labels. Compatible cached semantic results were
also reused internally without being projected back onto every review clip, so valid results could
look like missing analysis.

The analysis contract is now explicit:

- **Auto** is the default. It counts fresh semantic work, not total media. When at most 60 eligible
  clips lack analysis from the exact configured model, every eligible clip goes through the deep
  analysis path. Larger miss pools use the time-balanced density shortlist, but every shortlisted
  clip gets LLM analysis.
- **Fast** always uses the shortlist and reserves LLM analysis for favorites. It no longer silently
  changes itself to Thorough.
- **Thorough** bypasses the shortlist and sends every eligible clip through deep analysis.
- The separate “Analyze all videos” checkbox was removed from the UI. The legacy configuration
  field remains accepted for compatibility with older callers.
- Cache results are reusable only when they have segments and, with content analysis enabled, their
  model identity exactly matches the configured model. Null, unknown, or different model identities
  are stale misses and restart analysis.
- Compatible cached segments now populate the in-memory clip's description, emotion, setting,
  activities, subjects, interest, quality, and audio tags in every path—including shortlist
  leftovers. Step 2 automatically shows them as “Current analysis” before a new run, and the cached
  segment remains available to title, music, and final selection logic.

Regression coverage records the reported 41-clip trip, a 100-clip large library, a 100-clip library
with only 50 current-model misses, unconditional Thorough behavior, stale-model rejection, and
pre-run UI cache hydration. The User Guide, CLI reference, pipeline manual, local-LLM setup, and
documentation animation now use the same three definitions.

Final verification for this correction:

- Focused cache/mode/UI/CLI regression set: 143 passed, 67 deliberate deselections.
- Somme-shaped 60-clip integration and affected regression set: 130 passed.
- Full repository suite: 4,430 passed, 7 skipped, 662 deliberate integration deselections.
- Mypy: no issues in 257 source files.
- Ruff lint, `git diff --check`, and the Docusaurus production build: passed.

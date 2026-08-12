---
date: 2026-08-11
branch: codex/launch-hardening
scope: launch readiness, automation, performance, Immich v2/v3 compatibility, and user flow
verdict: all identified P0 launch blockers are closed; P1 performance and operations work remains
---

# Launch Readiness Audit — 2026-08-11

## Executive verdict

The original review found the application close to a public beta but unsafe for an unattended
stable launch. The blockers were contract failures around automation, Immich v3, output encoding,
versioning, and browser E2E—not a need to rewrite the video engine or compile Python with Cython.

Those P0 blockers are now closed and independently reviewed. The complete launch-candidate gate
passes. P1 still contains measured performance work and operational-flow cleanup, so this is a
launch candidate rather than an excuse to skip the remaining work.

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

The LaunchAgent remains unloaded by explicit owner instruction. P0 completion does not reactivate
it; loading the job is a separate action that was not requested.

## Final P0 gate evidence — 2026-08-12

| Check | Result |
|---|---|
| Full pytest suite | 4,084 passed, 7 skipped, 658 deselected, 20 warnings |
| Required hermetic browser E2E | 24 passed, 0 skipped; Chromium rendered and probed a real video |
| Ruff | Clean; 497 Python files already formatted |
| Mypy | Clean across 237 source files |
| Import contracts | 2 kept, 0 broken across 328 files and 2,033 dependencies |
| Documentation contracts | 39 passed |
| Docusaurus production build | Passed |
| Package build | Wheel and sdist built; Twine accepted both |
| Version consistency | CLI, Python, and wheel metadata all `0.37.2.dev113` |
| Installed LaunchAgent | Not loaded; `launchctl print` exited 113 with “Could not find service” |

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

## P1 — important after P0

### P1.1 — Performance work should target measured I/O and FFmpeg costs

Confirmed opportunities:

- `ClipAnalyzer.phase_analyze()` is sequential and forces `gc.collect()` per clip.
- `VideoDownloadCache.download_or_get()` recursively scans for eviction after every
  download, making a batch cost roughly downloads × cache files.
- Clip extraction is sequential.
- Analysis/probe results and heavyweight analyzers can be reused more aggressively.
- Trip detection can fetch about 13,151 assets through many paginated requests every day.
- Title insertion and assembly need profiling for avoidable re-encodes.

Plan: fix the benchmark, capture cold/warm baselines, then add bounded concurrency, batch
cache maintenance, metadata reuse, analyzer reuse, and trip-input caching. Cython is out of
scope unless a later profile finds material pure-Python CPU time.

### P1.2 — The built-in exact scheduler is a second, weaker product

Confirmed defects in the legacy daemon:

- Configured timezone is ignored; scheduling is UTC.
- Jobs sharing a start time can be skipped.
- Long jobs miss later schedules.
- No durable queue, catch-up, lease, or retry model exists.
- Status reflects configuration rather than daemon liveness.
- Album templates can be passed literally.
- Docker starts only the UI and does not supervise this daemon.

Decision: do not build a second scheduling platform. Keep it for one compatibility release
with a deprecation warning, remove it from setup documentation, then delete it. External
timers invoke the canonical `auto run` command.

### P1.3 — The main flow hides expensive work and operational state

The four-page UI is coherent, but Step 2 hides discovery, analysis, selection, and refinement
behind a broad loading state. In-process session state also means restarts lose the workflow
and multiple UI replicas are unsafe.

Required improvements:

- Show `connect → discover → choose → download → analyze → assemble → validate → deliver`.
- Show counts, elapsed time, cache hits, chosen candidate/reason, and output path.
- Add automation status: last wake, decision, success, failure, pending delivery, cooldown,
  category history, and output directory usage.
- Document the single-user/single-replica UI model.

### P1.4 — Deployment exposure guidance remains

P0 fixed the Docker home/mount mismatch, made optional dependency selection fail closed, and split
process liveness from dependency readiness. The remaining P1 work is narrower: keep the
`0.0.0.0` plus disabled-auth exposure warning prominent in deployment entry points, and document
the single-user/single-replica UI model alongside it.

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

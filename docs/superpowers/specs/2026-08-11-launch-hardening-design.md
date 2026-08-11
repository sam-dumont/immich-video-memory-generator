---
date: 2026-08-11
status: approved
priority_order: P0 before P1
assessment: ../../reviews/2026-08-11-launch-readiness-audit.md
decision: canonical smart automation
immich_support: v2 and v3
---

# Launch Hardening Design

## Context

The launch-readiness audit found a strong core pipeline surrounded by several broken
operational contracts. The app can render good videos, but automation can report false
success, repeat monthly candidates, lose its actual exception, ignore output codec settings,
and break against Immich v3.

This design keeps the existing product architecture. It does not introduce a general-purpose
job queue or a Cython build. It makes `auto run` the single scheduling entry point, hardens
the boundaries around it, and defers measured performance work until all P0 contracts pass.

See the full evidence and priority list in
[`docs/reviews/2026-08-11-launch-readiness-audit.md`](../../reviews/2026-08-11-launch-readiness-audit.md).

## Goals

1. Make unattended daily automation bounded, truthful, and observable.
2. Prevent repeated categories and monthly backlog floods.
3. Support Immich v2 and v3 through one normalized API boundary.
4. Make output settings and application versions authoritative.
5. Establish deterministic browser E2E as a launch gate.
6. Isolate production state from tests and benchmarks.
7. Improve measured end-to-end speed after correctness is locked down.

## Non-goals

- Building a multi-worker distributed job queue.
- Repairing the legacy exact-time scheduler into a second scheduling product.
- Supporting more than one generation candidate per daily invocation.
- Automatically deleting existing database rows, caches, or output files.
- Adding Cython before profiling shows meaningful pure-Python CPU cost.
- Supporting multiple concurrent UI replicas in this release.

## 1. Canonical automation architecture

### External timer contract

LaunchAgent, systemd timer, cron, and container scheduler examples invoke one command:

```text
immich-memories auto run
```

The external timer answers when to wake the app. The app decides what should happen.

The installed LaunchAgent remains unloaded until P0 verification passes. The legacy exact
scheduler remains callable for one compatibility release, but prints a deprecation warning
and is removed from normal setup documentation.

### Daily decision order

`auto run` performs these actions in order:

1. Run preflight, including Immich version resolution.
2. Recover or report stale automation attempts.
3. Retry one pending delivery if present, then stop for the day.
4. Check cooldown using completed `source=auto` generations only.
5. Fetch the minimum detector inputs.
6. Produce typed candidates from enabled detectors.
7. Apply deduplication, eligibility, cadence, and variety gates.
8. Rank the remaining candidates.
9. Select at most one candidate.
10. Convert it to an exhaustive typed generation request.
11. Generate, validate, and persist the exact output.
12. Attempt configured delivery and persist its independent result.
13. Report a typed terminal outcome.

### Terminal outcomes

The runner returns an `AutoRunResult`, not `Path | None`:

| Outcome | CLI exit | Definition |
|---|---:|---|
| `skipped` | 0 | Cooldown, variety gate, or no eligible candidate |
| `dry_run` | 0 | Decision reported without mutation |
| `completed` | 0 | Matching generation completed and output validated |
| `failed` | nonzero | Preflight, detection, generation, validation, or required delivery failed |

`completed` requires all of the following:

- A run created after the automation attempt started.
- `source=auto`.
- The selected candidate's memory key.
- Terminal generation status `completed`.
- A path that exists.
- Successful artifact validation.

A subprocess return code alone is never proof of success. An old database output is never
returned as the result of a new attempt.

### Automation attempts and deliveries

Add additive database records for orchestration state:

```text
automation_attempts
  id, started_at, finished_at, outcome, reason,
  candidate_category, memory_type, memory_key, run_id, error

deliveries
  id, run_id, target, status, attempts,
  created_at, updated_at, last_error, correlation_id
```

`pipeline_runs` remains the source for generation/phase details. `automation_attempts`
records wake-ups and no-op decisions. `deliveries` makes upload retry durable without
re-rendering.

Database migrations are additive. Existing user data is backed up before any separate cleanup
operation; the application never silently deletes historical rows.

### Candidate-to-generation mapping

Every candidate category has an explicit adapter to a typed `GenerationRequest`. The adapter
is exhaustive and rejects unknown categories before starting generation.

Trip generation uses the candidate's exact detected date range when selecting the trip.
`on_this_day`, birthdays, person spotlights, multi-person memories, monthly reviews, yearly
reviews, trips, and activity bursts all receive explicit test cases.

The generated command receives source and memory-key context so the resulting pipeline run is
traceable. Failure reporting retains bounded stdout and stderr tails plus the structured run
error. Sensitive configuration remains sanitized.

## 2. Variety and cadence

### Candidate identity

Candidates gain a `category` separate from their rendering `memory_type`. This prevents an
activity burst rendered with the monthly preset from being counted as a regular monthly
review.

Examples:

| Category | Rendering memory type |
|---|---|
| `monthly_review` | `monthly_highlights` |
| `activity_burst` | `monthly_highlights` |
| `birthday` | `person_spotlight` |
| `person_spotlight` | `person_spotlight` |
| `trip` | `trip` |

### Hard guardrails

- Automatic regular monthly reviews consider only the most recently completed month.
- Older unfinished months remain available through manual generation.
- A regular monthly review may complete at most once per calendar month.
- The same category cannot be selected after the previous successful auto generation.
- A category may account for at most two of the previous six successful auto generations.
- Person categories also consider normalized person identity for repetition.
- Time-sensitive events receive a ranking boost but do not bypass the one-attempt limit.
- If all candidates are blocked, the runner returns `skipped`; it never weakens constraints.

Only completed `source=auto` generations influence the rotation. Manual generations and
fixture data do not.

### Explainability

`auto suggest`, `auto run --dry-run`, `auto status`, and the UI show:

- Candidate category and memory type.
- Base and adjusted score.
- Reason for eligibility.
- Rejected candidates and the rejecting cadence/variety rule.
- Recent successful category rotation.

## 3. Immich v2/v3 compatibility boundary

### Configuration

```yaml
immich:
  url: https://photos.example.com
  api_key: ${IMMICH_API_KEY}
  api_version: auto  # auto, v2, or v3
```

`api_version` defaults to `auto`. Documentation and manuals explicitly state that Immich v2
and v3 are supported and describe when an override is appropriate.

### Version resolution

Create a small API-generation enum/value object (`v2` or `v3`) owned by `ImmichClient`.

- Correct `ServerInfo` to parse `major`, `minor`, `patch`, and optional prerelease data.
- `auto` calls `/server/version` once per client session and caches the resolved major.
- Explicit overrides select behavior, while preflight still reports a detectable mismatch.
- A mismatch or unsupported major fails before asset discovery or upload.
- CLI, UI, preflight, and readiness output show configured and detected values.

### Duration normalization

The API `Asset` model accepts `str | int | None` for raw duration:

- V2 strings parse as hours/minutes/seconds.
- V3 integers are milliseconds and divide by 1,000.
- Null remains unknown.
- Invalid values fail safely and include asset context in diagnostics.

Every downstream component continues to consume seconds. It does not branch on Immich major.

### Upload schemas

The resolved major is injected into upload behavior before the request is built.

V2 multipart data includes legacy `deviceAssetId` and `deviceId` fields plus timestamps.
V3 omits removed device fields and uses the v3 asset-media multipart schema, including the
filename where supported.

There is no speculative v2 upload followed by a v3 retry. Upload responses, including
duplicate responses, are normalized into a typed result.

### Error normalization

`ImmichAPIError` gains structured details and optional correlation ID.

- Legacy string/list messages remain readable.
- V3 Zod validation structures are flattened into concise field errors.
- `X-Correlation-ID` is read from response headers.
- Logs include status, endpoint, safe details, and correlation ID.
- API keys and sensitive URLs stay sanitized.

### Contract coverage

Committed fixtures derived from official v2.6.3 and v3.1.0 schemas cover:

- Server version responses.
- Asset duration variants.
- Asset search responses with removed/optional fields.
- Exact upload multipart fields.
- Created and duplicate upload results.
- Legacy and structured error bodies.
- Every endpoint called by the application.

## 4. Render and delivery state

A valid generated artifact is persisted before retryable delivery work.

### Core generation

Core failures—discovery, extraction, assembly, or validation—mark generation failed and make
`auto run` nonzero. No other candidate is attempted that day.

### Optional enhancements

An optional music-generation failure falls back to the valid base video and records a visible
warning. It does not cause a duplicate render tomorrow.

### Delivery

When upload is enabled, an upload failure creates/updates a pending delivery and returns a
visible failure. The next daily invocation retries that artifact before considering new work.
Successful delivery clears the pending state. Correlation ID and error details survive in
history.

## 5. Authoritative encoding plan

Create one encoding-plan resolver used by assembly, title insertion, photo animation, and
single-clip paths. Inputs include codec, container, quality/CRF, hardware policy, HDR policy,
and detected capabilities. The result contains exact FFmpeg encoder and pixel-format args.

Rules:

- `h264` uses a hardware H.264 encoder when enabled/available, otherwise `libx264`.
- `h265` uses a hardware HEVC encoder when enabled/available, otherwise `libx265`.
- `prores` uses a supported ProRes encoder and compatible MOV container.
- Hardware fallback never changes the requested codec.
- H.264 output tone-maps HDR input to SDR.
- HDR preservation is explicit and valid only for supported codec/container combinations.
- Invalid combinations fail preflight before expensive work.
- `hardware.enabled=false` and backend `none` prevent hardware encoding.

The output config gains an explicit HDR policy with a safe SDR default. Existing H.264
configuration therefore produces H.264 rather than the current implicit HEVC behavior.

After assembly, `ffprobe` validates:

- Video codec.
- Container.
- Width/height and orientation.
- Expected audio/video streams.
- Nonzero duration close to the tracked output duration.
- HDR/color metadata when preservation is requested.

Validation failure marks the core generation failed.

## 6. Single version source

Git-derived Hatch/VCS build metadata is authoritative.

- Package builds continue using dynamic versioning.
- Runtime version access comes from generated package metadata, not a hard-coded constant.
- CLI, UI, health/readiness, and logs import the same helper.
- Semantic release calculates/tags the release but does not maintain a duplicate version
  literal.
- Docker builds receive the calculated package version as OCI metadata and verify that it
  matches the installed runtime package.
- Development checkouts report an explicit `.dev`/local version.

A test compares all runtime surfaces. Release CI compares the tag, wheel metadata, CLI, and
Docker label.

## 7. Test and production-state isolation

All test tiers use temporary directories for configuration, cache, database, outputs,
downloads, and intermediate files.

An autouse test guard fails if a test resolves the normal user database or output directory.
Benchmarks receive unique fixture identities containing all behavioral parameters, including
duration.

Stale production `running` rows are reported by status/doctor tooling. Existing rows and
files are not silently cleaned. A later cleanup operation requires:

1. Database backup.
2. Exact file/row manifest.
3. Size and recovery report.
4. Separate owner approval.

## 8. Required launch E2E

Add a deterministic local fake Immich service with representative v2 and v3 responses and
tiny synthetic media. Required CI does not depend on private credentials or a personal
server.

The browser smoke covers:

1. Application startup and readiness.
2. Configuration/preflight with v2 and v3.
3. Preset/date selection.
4. Discovery and visible analysis phases.
5. Clip review and generation options.
6. Tiny full generation.
7. Success state and output link.
8. `ffprobe` verification of the requested codec.
9. Upload payload contract against the fake service.

Missing startup, core controls, or output fails the test. Only suites explicitly marked as
live-external may skip when their environment is absent. Screenshot selectors do not use
blanket exception swallowing.

## 9. P1 performance design

P1 begins only when all P0 acceptance criteria pass.

### Benchmark first

- Correct fixture cache keys.
- Record cold-cache and warm-cache timings.
- Record CPU, peak memory, downloaded bytes, cache scans, and FFmpeg subprocess counts.
- Keep small 720p/1080p synthetic cases plus a representative 4K case.

### Optimization order

1. Run cache size/age eviction once per batch rather than per download.
2. Reuse probe metadata and analysis cache entries.
3. Replace per-clip forced garbage collection with bounded batch cleanup.
4. Add bounded download/extraction concurrency with deterministic ordering.
5. Reuse heavyweight analyzers/models within one run.
6. Cache trip detector inputs and avoid daily full-year refetches when unchanged.
7. Profile title/assembly stages for avoidable intermediate encodes.

Concurrency defaults are conservative and resource-aware. The process count remains bounded
to avoid 4K memory pressure and disk contention. Each optimization requires benchmark evidence
and full output-quality verification. Cython remains out of scope absent a profile proving a
material Python hotspot.

## 10. P1 flow and operations

CLI and UI expose the same phase model:

```text
connect → discover → choose → download → analyze → assemble → validate → deliver
```

Each phase can report current item, counts, elapsed time, cache hits, and warnings.

`auto status` and the UI show:

- External schedule installed/active state where detectable.
- Last wake and decision.
- Rejected candidates and variety rules.
- Last success and failure.
- Pending delivery.
- Cooldown and recent category history.
- Output directory usage and recent files.

Docker/config cleanup includes the correct non-root home path, deterministic optional
dependencies, and separate liveness/readiness endpoints. Documentation states the
single-user/single-replica UI limitation and warns before exposing an unauthenticated bind.

## 11. Documentation contract

The main README, user guide, configuration reference, automation manual, Docker guide, and
migration/release notes must agree on:

- Smart `auto run` as the canonical daily workflow.
- The one-candidate limit and variety behavior.
- Immich v2/v3 support.
- `api_version: auto | v2 | v3`, with `auto` as default.
- How configured and detected versions appear in preflight/status.
- Pending delivery behavior.
- Output codec/HDR rules.
- Legacy scheduler deprecation.
- Existing output/database cleanup being manual and reviewable.

## 12. Implementation order

P0 is implemented and verified in this order:

1. Test/benchmark state isolation and regression harnesses.
2. Typed automation outcomes, exact candidate mapping, run identity, and error capture.
3. Variety/cadence rules.
4. Immich v2/v3 boundary and endpoint contract fixtures.
5. Render/delivery persistence and upload retry.
6. Authoritative encoding plan and final artifact validation.
7. Single version source.
8. Required browser E2E and documentation updates.

Only then:

9. P1 benchmark-driven performance work.
10. P1 status/UI/Docker/readiness work.
11. Legacy scheduler removal after its compatibility window.

Every bug fix and behavior change follows test-first RED → GREEN → REFACTOR. Each P0 slice
lands with focused tests before proceeding to the next slice.

## 13. Acceptance criteria

P0 is complete when:

- One daily command makes at most one generation attempt.
- Healthy no-op and failure produce different typed outcomes and exit codes.
- Every detector category has an explicit tested generation mapping.
- Trip candidates generate their exact detected trip.
- No stale output can satisfy a new automation attempt.
- Monthly auto-generation cannot backfill repeatedly.
- Variety limits are enforced from successful auto history.
- V2 and v3 fixtures pass for durations, errors, endpoints, and uploads.
- Explicit API-version mismatch and unsupported major fail preflight.
- Requested H.264/H.265/ProRes and hardware policy match the probed artifact.
- All user-facing version surfaces agree.
- Tests cannot write to production database/output paths.
- Required browser smoke fails on missing core state and validates a real output.
- Full unit/integration/type/lint/import checks pass.
- Documentation explicitly reflects the approved behavior.

P1 is complete when:

- Corrected cold/warm benchmarks and before/after evidence are committed.
- Accepted optimizations improve their measured bottleneck without quality regressions.
- Automation status exposes last decision, result, pending delivery, variety, and disk use.
- Docker paths/dependencies/readiness are correct and documented.
- The primary UI exposes meaningful phase progress.

## 14. Rollout and recovery

1. Keep the currently installed LaunchAgent unloaded during implementation.
2. Run deterministic test and fake-server verification.
3. Run a manual `auto run --dry-run` against the real configured Immich server.
4. Run one supervised generation without upload.
5. Validate the artifact and database identity.
6. Run one supervised v3 upload and confirm Immich asset/album behavior.
7. Reinstall/reactivate the daily schedule only with owner approval.
8. Observe at least one daily no-op/generation cycle and check status/history.

Rollback is operationally simple: unload the external timer. Generated artifacts and database
migrations are additive and remain inspectable. Existing data cleanup is a separate operation.

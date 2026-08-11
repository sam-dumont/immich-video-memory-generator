# P1 Performance and Operational Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce measured end-to-end latency and make long-running work understandable without compromising P0 correctness.

**Architecture:** Establish reproducible cold/warm benchmarks first. Replace per-item maintenance with batch lifecycles, overlap only network downloads with bounded independent clients, and reuse probe/analyzer services where state can be reset safely. Emit one shared operational phase model to CLI/UI/status. Use profiles to decide whether native compilation is justified.

**Tech Stack:** Python stdlib profiling/concurrency, pytest, FFmpeg/ffprobe, SQLite, NiceGUI, JSON benchmark artifacts, GitHub Actions benchmark workflow.

## Global Constraints

- Start only after every P0 plan is green.
- Benchmark fixtures encode resolution, duration, codec, and frame rate in their cache key.
- Benchmarks and profiles use pytest temporary roots, never the user's cache/database/output.
- Compare median of at least three measured repetitions after one warm-up.
- Optimize one bottleneck at a time and retain before/after evidence.
- Download concurrency is bounded and uses one client/event loop per worker.
- FFmpeg analysis/extraction concurrency remains one unless a separate measured task proves more is safe.
- Cache maintenance scans at most once at batch start and once at batch end.
- No destructive storage cleanup is introduced or run in this plan.
- Cython is added only if profile evidence meets the explicit Task 7 threshold.
- The installed LaunchAgent remains unloaded.
- Every behavior change follows RED → GREEN → REFACTOR.

---

## File structure

- Modify `tests/integration/assembly/conftest.py`: collision-free media fixtures.
- Modify `tests/integration/assembly/perf_utils.py`: environment/input metadata and medians.
- Create `tests/performance/test_benchmark_contract.py`.
- Create `scripts/profile_pipeline.py`.
- Create `docs/reviews/2026-08-11-performance-baseline.md` and later append measured decisions.
- Modify `src/immich_memories/cache/video_cache.py`: batch maintenance and manifest.
- Create `src/immich_memories/processing/download_coordinator.py`: bounded prefetch.
- Modify `src/immich_memories/generate_clips.py`: prefetch then sequential extraction.
- Modify `src/immich_memories/analysis/clip_analyzer.py` and `preview_builder.py`: shared cache/services and batch GC.
- Create `src/immich_memories/processing/probe_cache.py`: per-run probe reuse.
- Modify `src/immich_memories/processing/ffmpeg_prober.py`, `clip_probing.py`, and consumers.
- Create `src/immich_memories/operations/phases.py` and `storage_report.py`.
- Modify CLI/UI/automation/tracking paths to report phases and storage.
- Modify scheduler CLI to warn that explicit cron scheduling is legacy/advanced.

### Task 1: Repair benchmark identity and capture the baseline

**Files:**
- Modify: `tests/integration/assembly/conftest.py`
- Modify: `tests/integration/assembly/perf_utils.py`
- Modify: `tests/integration/assembly/test_perf_assembly.py`
- Modify: `tests/integration/pipeline/test_perf_pipeline.py`
- Create: `tests/performance/test_benchmark_contract.py`
- Create: `docs/reviews/2026-08-11-performance-baseline.md`

**Interfaces:**
- Extends: `PerfResult` with input duration, codec, frame rate, warm/cold mode, Python version, platform, CPU, and git revision.
- Produces: fixture filename `perf_clip_{resolution}_{duration}s_{fps}fps_{codec}_{index}.mp4`.
- Produces: benchmark summary containing median and individual repetitions.

- [ ] **Step 1: Write fixture-key and benchmark-schema tests**

```python
def test_media_fixture_key_changes_with_duration(fixtures_dir) -> None:
    five = make_n_clips(fixtures_dir, 1, "640x360", duration=5, fps=30, codec="h264")
    ten = make_n_clips(fixtures_dir, 1, "640x360", duration=10, fps=30, codec="h264")
    assert five[0] != ten[0]
    assert probe_duration(five[0]) == pytest.approx(5, abs=0.2)
    assert probe_duration(ten[0]) == pytest.approx(10, abs=0.2)

def test_perf_result_records_reproduction_inputs() -> None:
    payload = PerfResult(
        scenario="contract",
        python_peak_mb=1.0,
        wall_seconds=2.0,
        cpu_user_seconds=1.0,
        cpu_sys_seconds=0.2,
        clip_count=1,
        resolution="640x360",
        input_duration_seconds=5.0,
        codec="h264",
        frame_rate=30.0,
        cache_mode="cold",
        python_version="3.13.5",
        platform="darwin-arm64",
        cpu="test-cpu",
        git_revision="abc1234",
    ).to_dict()
    assert REQUIRED_REPRODUCTION_KEYS <= payload.keys()
```

- [ ] **Step 2: Run the contract tests**

Run: `uv run pytest tests/performance/test_benchmark_contract.py -q`

Expected: FAIL because duration is missing from filenames and result metadata is incomplete.

- [ ] **Step 3: Implement collision-free fixtures and repeat summaries**

Hash the full FFmpeg source/encoder argument tuple into fixture metadata as a second guard.
Before reusing a fixture, ffprobe it and regenerate if codec, dimensions, frame rate, or duration
does not match. Record one warm-up and three measured repetitions; save raw repetitions plus
median, never just the fastest result.

- [ ] **Step 4: Capture the pre-optimization baseline**

Run:

```bash
uv run pytest tests/integration/assembly/test_perf_assembly.py -q -m integration
uv run pytest tests/integration/pipeline/test_perf_pipeline.py -q -m integration
```

Expected: PASS and JSON includes all reproduction fields. Write the exact commands, machine
fingerprint, git revision, scenario medians, and existing audit observations to
`docs/reviews/2026-08-11-performance-baseline.md`. Include the previously observed real 4K
ranges: roughly 80–180 seconds extraction and 190–350 seconds assembly, clearly labeled as
historical observations rather than the new controlled baseline.

- [ ] **Step 5: Commit benchmark integrity and baseline**

```bash
git add tests/integration/assembly/conftest.py tests/integration/assembly/perf_utils.py tests/integration/assembly/test_perf_assembly.py tests/integration/pipeline/test_perf_pipeline.py tests/performance/test_benchmark_contract.py
git add -f docs/reviews/2026-08-11-performance-baseline.md
git commit -m "test: establish reproducible performance baseline"
```

### Task 2: Scan and evict the video cache once per batch

**Files:**
- Modify: `src/immich_memories/cache/video_cache.py`
- Modify: `src/immich_memories/generate.py`
- Modify: `src/immich_memories/analysis/smart_pipeline.py`
- Modify: `src/immich_memories/analysis/clip_analyzer.py`
- Modify: `tests/test_video_cache.py`
- Modify: `tests/test_pipeline_efficiency.py`

**Interfaces:**
- Produces: `VideoDownloadCache.begin_batch() -> CacheBatch`.
- Produces: `CacheBatch.download_or_get(client, asset)` and `finish()`.
- Removes: automatic `evict_if_over_limit()` from each successful download.

- [ ] **Step 1: Write scan-count and lifecycle tests**

```python
def test_batch_of_twenty_downloads_scans_cache_once(cache, assets, client, scan_spy) -> None:
    with cache.begin_batch() as batch:
        for asset in assets[:20]:
            batch.download_or_get(client, asset)
    assert scan_spy.call_count == 1

def test_download_or_get_never_runs_global_eviction(cache, asset, client) -> None:
    with patch.object(cache, "evict_if_over_limit") as evict:
        cache.download_or_get(client, asset)
    evict.assert_not_called()
```

- [ ] **Step 2: Run cache/efficiency tests**

Run: `uv run pytest tests/test_video_cache.py tests/test_pipeline_efficiency.py -q`

Expected: FAIL because every cache miss recursively scans the full cache after download.

- [ ] **Step 3: Implement explicit batch ownership**

At batch start, remove expired files and snapshot size/mtime metadata once. Downloads update the
in-memory manifest. At finish, evict oldest entries from the manifest until under the size cap;
do not `rglob` again unless an external mutation invalidated the manifest. `finish()` runs in
`__exit__` even after an item failure.

Create one cache in `SmartPipeline` and inject its active batch into `ClipAnalyzer` and
`PreviewBuilder`; stop constructing a new `VideoDownloadCache` per clip. Generation owns one
batch around download/extraction.

- [ ] **Step 4: Run cache, pipeline, and generation tests**

Run: `uv run pytest tests/test_video_cache.py tests/test_pipeline_efficiency.py tests/test_clip_analyzer.py tests/test_generate.py -q`

Expected: PASS and scan-count assertions stay constant as asset count grows.

- [ ] **Step 5: Commit batch cache maintenance**

```bash
git add src/immich_memories/cache/video_cache.py src/immich_memories/generate.py src/immich_memories/analysis/smart_pipeline.py src/immich_memories/analysis/clip_analyzer.py src/immich_memories/analysis/preview_builder.py tests/test_video_cache.py tests/test_pipeline_efficiency.py tests/test_clip_analyzer.py tests/test_generate.py
git commit -m "perf: maintain video cache once per batch"
```

### Task 3: Prefetch downloads with bounded independent clients

**Files:**
- Create: `src/immich_memories/processing/download_coordinator.py`
- Modify: `src/immich_memories/config_models.py`
- Modify: `src/immich_memories/generate_clips.py`
- Modify: `src/immich_memories/generate.py`
- Create: `tests/test_download_coordinator.py`
- Modify: `tests/test_generate_downloads.py`

**Interfaces:**
- Extends: `AnalysisConfig.download_workers: int = Field(default=3, ge=1, le=8)`.
- Produces: `DownloadCoordinator(client_factory, cache_batch, max_workers)`.
- Produces: `prefetch(assets, progress) -> dict[str, DownloadResult]`.

- [ ] **Step 1: Write ordering, bound, isolation, and speed tests**

Use a fake download that waits 100 ms and records active worker count:

```python
def test_prefetch_is_bounded_and_faster_than_serial(coordinator, six_assets) -> None:
    started = monotonic()
    results = coordinator.prefetch(six_assets)
    elapsed = monotonic() - started
    assert coordinator.max_observed_workers == 3
    assert elapsed < 0.35
    assert list(results) == [asset.id for asset in six_assets]
```

Assert each worker constructs/closes its own `SyncImmichClient`, duplicate live-photo IDs
download once, one failure is returned per asset without cancelling successes, and `workers=1`
is serial.

- [ ] **Step 2: Run coordinator tests**

Run: `uv run pytest tests/test_download_coordinator.py tests/test_generate_downloads.py -q`

Expected: FAIL because generation downloads and extracts each item in one sequential loop.

- [ ] **Step 3: Implement download-only concurrency**

Use `ThreadPoolExecutor(max_workers=download_workers)`. The factory captures Immich URL, key,
API policy, and timeout, and returns a new sync client per worker. Submit unique download IDs,
collect results in input order, and close each client in its worker. Never share the existing
persistent event loop/client across threads.

Split `_extract_clips()` into a prefetch stage and sequential render/extract stage. Photos do
not enter video prefetch. Keep all FFmpeg extraction sequential.

- [ ] **Step 4: Run download and generation suites**

Run: `uv run pytest tests/test_download_coordinator.py tests/test_generate_downloads.py tests/test_generate.py tests/test_api_client.py -q`

Expected: PASS.

- [ ] **Step 5: Commit bounded prefetch**

```bash
git add src/immich_memories/processing/download_coordinator.py src/immich_memories/config_models.py src/immich_memories/generate_clips.py src/immich_memories/generate.py tests/test_download_coordinator.py tests/test_generate_downloads.py tests/test_generate.py tests/test_api_client.py
git commit -m "perf: prefetch videos with bounded clients"
```

### Task 4: Remove per-clip full GC and reuse resettable analysis services

**Files:**
- Modify: `src/immich_memories/analysis/clip_analyzer.py`
- Modify: `src/immich_memories/analysis/preview_builder.py`
- Modify: `src/immich_memories/analysis/unified_analyzer.py`
- Modify: `src/immich_memories/analysis/scoring.py`
- Modify: `tests/test_clip_analyzer.py`
- Modify: `tests/test_unified_analyzer.py`
- Modify: `tests/test_pipeline_efficiency.py`

**Interfaces:**
- Produces: `UnifiedSegmentAnalyzer.reset_for_video()`.
- Produces: `ClipAnalyzer.close()` for one batch-level teardown.
- Keeps: one content analyzer, audio analyzer, scene scorer, and capture cache per batch.

- [ ] **Step 1: Write resource-count and cache-reset tests**

Analyze ten clips with factories instrumented. Assert content/audio/scorer/analyzer construction
is once per pipeline, `reset_for_video()` is called between clips, no segment/cut-point state
crosses clips, and `gc.collect()` is called at most once during final teardown.

- [ ] **Step 2: Run analysis efficiency tests**

Run: `uv run pytest tests/test_clip_analyzer.py tests/test_unified_analyzer.py tests/test_pipeline_efficiency.py -q`

Expected: FAIL because `UnifiedSegmentAnalyzer` and `SceneScorer` are built per clip and multiple
full GC calls run per item.

- [ ] **Step 3: Add explicit reset/close lifecycles**

Move service creation to `ClipAnalyzer.__init__` lazily. `reset_for_video()` releases the current
capture and clears per-video lists/maps but preserves immutable configuration and reusable model
objects. Call it in a `finally` for each clip. Run final model cleanup and a single `gc.collect()`
in `close()` after the analysis phase.

Remove `gc.collect()` from the main per-clip loop, `_cleanup_analyzer()`, preview legacy analysis,
and `_analyze_clip_with_preview()` finalizer. Preserve explicit native capture release.

- [ ] **Step 4: Run analysis and memory-regression tests**

Run: `uv run pytest tests/test_clip_analyzer.py tests/test_unified_analyzer.py tests/test_pipeline_efficiency.py tests/test_scoring.py -q`

Expected: PASS; a two-video state-leak regression proves the second result contains no first-video
segments.

- [ ] **Step 5: Commit batch analysis services**

```bash
git add src/immich_memories/analysis/clip_analyzer.py src/immich_memories/analysis/preview_builder.py src/immich_memories/analysis/unified_analyzer.py src/immich_memories/analysis/scoring.py tests/test_clip_analyzer.py tests/test_unified_analyzer.py tests/test_pipeline_efficiency.py tests/test_scoring.py
git commit -m "perf: reuse analyzers across a clip batch"
```

### Task 5: Reuse ffprobe results within each run

**Files:**
- Create: `src/immich_memories/processing/probe_cache.py`
- Modify: `src/immich_memories/processing/clip_probing.py`
- Modify: `src/immich_memories/processing/ffmpeg_prober.py`
- Modify: `src/immich_memories/processing/assembly_engine.py`
- Modify: `src/immich_memories/generate_clips.py`
- Create: `tests/test_probe_cache.py`
- Modify: `tests/test_processing_coverage.py`

**Interfaces:**
- Produces: `ProbeKey(path, size, mtime_ns)`.
- Produces: `ProbeCache.get(path) -> VideoProbe` and `invalidate(path)`.
- Injects: one `ProbeCache` per generation/analysis run.

- [ ] **Step 1: Write subprocess-count and invalidation tests**

Probe the same unchanged file for duration, resolution, codec, HDR, and audio stream. Assert one
JSON ffprobe process. Change file size/mtime and assert a second process. Replace a staged file
atomically and assert the final path is re-probed.

- [ ] **Step 2: Run probe tests**

Run: `uv run pytest tests/test_probe_cache.py tests/test_processing_coverage.py -q`

Expected: FAIL because the code has several independent ffprobe helpers and subprocesses.

- [ ] **Step 3: Normalize one comprehensive probe model**

Run one JSON ffprobe command for video/audio streams, format duration/size, color metadata,
rotation, dimensions, and frame rate. Adapt existing public helpers to read `VideoProbe` so
callers remain stable. Inject the per-run cache; do not add a process-global cache.

- [ ] **Step 4: Run processing and output-contract tests**

Run: `uv run pytest tests/test_probe_cache.py tests/test_processing_coverage.py tests/test_output_contract.py tests/test_assembler_unit.py -q`

Expected: PASS.

- [ ] **Step 5: Commit per-run probe reuse**

```bash
git add src/immich_memories/processing/probe_cache.py src/immich_memories/processing/clip_probing.py src/immich_memories/processing/ffmpeg_prober.py src/immich_memories/processing/assembly_engine.py src/immich_memories/generate_clips.py tests/test_probe_cache.py tests/test_processing_coverage.py tests/test_output_contract.py tests/test_assembler_unit.py
git commit -m "perf: reuse media probes within each run"
```

### Task 6: Expose one operational phase model and read-only storage report

**Files:**
- Create: `src/immich_memories/operations/__init__.py`
- Create: `src/immich_memories/operations/phases.py`
- Create: `src/immich_memories/operations/storage_report.py`
- Modify: `src/immich_memories/generate.py`
- Modify: `src/immich_memories/analysis/progress.py`
- Modify: `src/immich_memories/tracking/run_tracker.py`
- Modify: `src/immich_memories/automation/state_store.py`
- Modify: `src/immich_memories/cli/auto_cmd.py`
- Modify: `src/immich_memories/cli/runs.py`
- Modify: `src/immich_memories/cli/scheduler_cmd.py`
- Modify: `src/immich_memories/ui/pages/step2_loading.py`
- Modify: `src/immich_memories/ui/pages/_step4_generate.py`
- Create: `tests/test_operational_phases.py`
- Create: `tests/test_storage_report.py`
- Modify: `tests/test_auto_status.py`
- Modify: `tests/test_scheduler.py`

**Interfaces:**
- Produces: `OperationalPhase.DISCOVERY`, `.DOWNLOAD`, `.ANALYSIS`, `.SELECTION`, `.RENDER`, `.MUSIC`, `.DELIVERY`, `.COMPLETE`.
- Produces: `PhaseEvent(phase, current, total, message, elapsed_seconds)`.
- Produces: read-only `build_storage_report(config, db) -> StorageReport`.
- Produces: `runs storage --json` and human table; no delete option.

- [ ] **Step 1: Write phase-order and storage-classification tests**

Assert generation emits monotonically ordered phases, cached work may emit a zero-item phase but
cannot omit its label, and failures retain the last phase in run/automation status. Build a temp
output tree containing completed, failed, running, pending-delivery, orphaned, and unknown
directories; assert byte/file counts for each class and no path is modified.

- [ ] **Step 2: Run operational tests**

Run: `uv run pytest tests/test_operational_phases.py tests/test_storage_report.py tests/test_auto_status.py tests/test_scheduler.py -q`

Expected: FAIL because analysis/generation use unrelated phase names and no storage audit exists.

- [ ] **Step 3: Implement adapters around one shared phase model**

Keep internal smart-analysis subphases as detail, but map them to `ANALYSIS`/`SELECTION` for the
outer lifecycle. Store the last outer phase on runs and automation attempts using additive
schema migration 12. CLI logs and UI labels consume the same event message.

`build_storage_report()` walks only configured cache/output roots, joins run metadata by exact
output path, and returns counts/bytes plus the ten largest directories. It performs no unlink,
rename, database update, or scheduler action.

- [ ] **Step 4: Warn on legacy explicit scheduler commands**

Every `scheduler` subcommand prints one concise stderr warning that `auto run` is the recommended
daily entry point and links to `immich-memories auto --help`. Keep command behavior for advanced
explicit cron users; do not remove configuration models in this release.

- [ ] **Step 5: Run operation, UI, CLI, and scheduler tests**

Run: `uv run pytest tests/test_operational_phases.py tests/test_storage_report.py tests/test_auto_status.py tests/test_scheduler.py tests/test_ui_state.py tests/test_progress.py -q`

Expected: PASS.

- [ ] **Step 6: Commit observable flow and audit**

```bash
git add src/immich_memories/operations src/immich_memories/generate.py src/immich_memories/analysis/progress.py src/immich_memories/tracking/run_tracker.py src/immich_memories/automation/state_store.py src/immich_memories/cli/auto_cmd.py src/immich_memories/cli/runs.py src/immich_memories/cli/scheduler_cmd.py src/immich_memories/ui/pages/step2_loading.py src/immich_memories/ui/pages/_step4_generate.py tests/test_operational_phases.py tests/test_storage_report.py tests/test_auto_status.py tests/test_scheduler.py tests/test_ui_state.py tests/test_progress.py
git commit -m "feat: expose pipeline phases and storage status"
```

### Task 7: Profile after structural fixes and make the Cython decision

**Files:**
- Create: `scripts/profile_pipeline.py`
- Modify: `docs/reviews/2026-08-11-performance-baseline.md`
- Modify: `.github/workflows/benchmark.yml`
- Modify: `tests/performance/test_benchmark_contract.py`

**Interfaces:**
- Produces: isolated `cProfile` files and top-50 cumulative/self-time text under a caller-provided directory.
- Produces: documented native-code decision with measured percentages.

- [ ] **Step 1: Write profile-script safety tests**

Invoke `scripts/profile_pipeline.py --help` and an isolated tiny scenario. Assert it requires an
explicit `--output-dir`, refuses a path outside the provided pytest temp root in the test hook,
records command/config/git revision, and writes no user state.

- [ ] **Step 2: Run the safety test**

Run: `uv run pytest tests/performance/test_benchmark_contract.py -q`

Expected: FAIL because the profile script does not exist.

- [ ] **Step 3: Implement and run cold/warm profiles**

Use stdlib `cProfile`/`pstats`, not a new dependency. Profile controlled 720p assembly and
analysis, then one representative real-library run only when explicitly configured. Store raw
profiles under `/tmp/immich-memories-profile-20260811` and copy only summarized tables and
environment metadata into the review document.

Run:

```bash
uv run python scripts/profile_pipeline.py --scenario controlled-cold --repetitions 3 --output-dir /tmp/immich-memories-profile-20260811
uv run python scripts/profile_pipeline.py --scenario controlled-warm --repetitions 3 --output-dir /tmp/immich-memories-profile-20260811
```

- [ ] **Step 4: Apply the native-code threshold**

Add Cython only if all conditions are true:

1. one pure-Python function consumes at least 15% of end-to-end wall time in both controlled and
   representative profiles;
2. its inputs cross the Python/native boundary no more than once per batch;
3. an algorithmic, cache, NumPy/OpenCV, or concurrency fix has been measured and rejected;
4. a prototype shows at least 20% end-to-end median improvement without changing outputs;
5. wheels can be built for supported Python/platform targets in CI.

If any condition is false, write `Decision: no Cython` with the measured reason. Do not add
Cython to dependencies or build configuration.

- [ ] **Step 5: Put benchmark metadata checks in CI**

Update the benchmark workflow so uploaded JSON is rejected when reproduction fields or three
repetitions are missing. Keep performance regressions advisory until ten comparable baseline
runs exist; correctness gates remain blocking.

- [ ] **Step 6: Commit profiling evidence and decision**

```bash
git add scripts/profile_pipeline.py .github/workflows/benchmark.yml tests/performance/test_benchmark_contract.py
git add -f docs/reviews/2026-08-11-performance-baseline.md
git commit -m "perf: document profile-backed optimization decision"
```

### Task 8: Re-run benchmarks and verify P1

**Files:**
- Modify: `docs/reviews/2026-08-11-performance-baseline.md` with final before/after table.

**Interfaces:**
- Produces: reviewed performance evidence and green full gate.

- [ ] **Step 1: Run the same baseline commands**

```bash
uv run pytest tests/integration/assembly/test_perf_assembly.py -q -m integration
uv run pytest tests/integration/pipeline/test_perf_pipeline.py -q -m integration
```

Expected: PASS. Add before/after medians, percentage change, cache scan counts, probe subprocess
counts, and peak memory. Do not compare runs with different fixture/environment metadata.

- [ ] **Step 2: Require deterministic structural improvements**

Run:

```bash
uv run pytest tests/test_pipeline_efficiency.py tests/test_download_coordinator.py tests/test_probe_cache.py tests/test_video_cache.py -q
```

Expected: one cache batch scans once regardless of item count; unchanged media probes once per
run; six 100-ms downloads with three workers complete under 350 ms; GC is not forced per clip.

- [ ] **Step 3: Run the complete launch gate**

```bash
make launch-check
uv run pytest tests/test_operational_phases.py tests/test_storage_report.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit the final measured table**

```bash
git add -f docs/reviews/2026-08-11-performance-baseline.md
git commit -m "docs: record launch performance results"
```

- [ ] **Step 5: Keep external state unchanged**

Confirm `runs storage --json` performs no writes and report its output to the user. Do not delete
the previously identified 58 videos/4.02 GiB, modify production fixture-polluted rows, or load
the preserved LaunchAgent without separate approval.

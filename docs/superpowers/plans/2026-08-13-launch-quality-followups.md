# Launch Quality Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the six approved launch-quality fixes so generated memories use one canvas,
degrade cleanly when optional providers are absent, honor final runtime, support useful dry-runs,
report optional audio semantics, and avoid notification quota noise.

**Architecture:** Resolve immutable decisions before expensive generation: one `OutputCanvas` feeds
photo/title/final rendering and one `TimelinePlan` feeds selection and title insertion. Optional
providers expose bounded health results and shared run-level state. Notification delivery health is
stored in SQLite and projected through the existing preflight, automation status, and health
surfaces.

**Tech Stack:** Python 3.11+, Pydantic 2, Click, httpx, OpenCV, FFmpeg/ffprobe, SQLite, Apprise,
pytest, Ruff, mypy.

## Global Constraints

- `--duration` is final playable duration; output may exceed it by at most 1.0 second.
- Actual photo/video content occupies at least 80%; titles occupy at most 20%.
- A 4:3 still in a 16:9 output is aspect-fitted with one blurred fill and one Ken Burns transform.
- Immich v2/v3 selection remains automatic at runtime and explicit in documentation.
- PANNs remains optional through the existing `audio-ml` extra.
- Optional LLM, PANNs, and notification failures never fail an otherwise valid generation.
- Provider URLs, notification URLs, API keys, and response bodies are not persisted or exposed.
- Upload and scheduler activation remain disabled during verification.
- The user-owned untracked `MagicMock/` directory remains untouched.

---

### Task 1: Resolve one output canvas for every renderer

**Files:**
- Create: `src/immich_memories/processing/output_canvas.py`
- Modify: `src/immich_memories/generate.py`
- Modify: `src/immich_memories/generate_settings.py`
- Modify: `src/immich_memories/generate_photos.py`
- Modify: `src/immich_memories/cli/_pipeline_runner.py`
- Modify: `src/immich_memories/cli/generate.py`
- Modify: `src/immich_memories/cli/_trip_generation.py`
- Test: `tests/test_output_canvas.py`
- Test: `tests/test_generate.py`
- Test: `tests/test_unified_budget.py`
- Test: `tests/integration/photos/test_photo_renderer.py`

**Interfaces:**
- Produces: `OutputCanvas(width: int, height: int, orientation: str)`.
- Produces: `resolve_output_canvas(*, resolution: str | None, orientation: str | None,
  configured_resolution: tuple[int, int], clips: Sequence[VideoClipInfo]) -> OutputCanvas`.
- Extends: `GenerationParams.output_orientation: str | None` and
  `GenerationParams.output_canvas: OutputCanvas | None`.
- Consumed by: `_build_assembly_settings()` and `_detect_photo_resolution()`.

- [ ] **Step 1: Add a failing explicit-canvas test**

```python
def test_explicit_landscape_canvas_overrides_config_and_portrait_majority():
    clips = [_clip(width=1080, height=1920), _clip(width=1080, height=1920)]
    canvas = resolve_output_canvas(
        resolution="1080p",
        orientation="landscape",
        configured_resolution=(3840, 2160),
        clips=clips,
    )
    assert canvas == OutputCanvas(1920, 1080, "landscape")
```

- [ ] **Step 2: Run the test and verify RED**

Run: `.venv/bin/pytest tests/test_output_canvas.py -q`

Expected: import failure because `output_canvas` does not exist.

- [ ] **Step 3: Implement the pure canvas resolver**

```python
@dataclass(frozen=True, slots=True)
class OutputCanvas:
    width: int
    height: int
    orientation: str


def resolve_output_canvas(*, resolution, orientation, configured_resolution, clips):
    tier = resolution or _tier_for(configured_resolution)
    if resolution == "auto":
        tier = _tier_for(configured_resolution)
    effective_orientation = orientation or _dominant_orientation(clips)
    width, height = RESOLUTIONS[tier]
    return _orient(width, height, effective_orientation)
```

Explicit `landscape`, `portrait`, and `square` are authoritative. With no orientation, source
majority determines landscape/portrait; a tie uses the configured canvas orientation.

- [ ] **Step 4: Run the focused resolver tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_output_canvas.py -q`

Expected: all canvas cases pass.

- [ ] **Step 5: Add a failing pipeline propagation test**

```python
def test_photo_and_assembly_consume_the_same_explicit_canvas(params):
    params.output_resolution = "1080p"
    params.output_orientation = "landscape"
    settings = _build_assembly_settings(params, [])
    assert _detect_photo_resolution(params) == (1920, 1080)
    assert settings.target_resolution == (1920, 1080)
```

- [ ] **Step 6: Run the propagation test and verify RED**

Run: `.venv/bin/pytest tests/test_generate.py tests/test_unified_budget.py -q`

Expected: photo resolution still resolves from the configured 4K/source-majority path.

- [ ] **Step 7: Thread the resolved canvas through generation**

Resolve `GenerationParams.output_canvas` once before extraction. Pass CLI `orientation` through
`run_pipeline_and_generate()` and trip generation. Replace `_detect_photo_resolution()`'s
independent majority calculation and `_build_assembly_settings()`'s separate resolution map with
the stored canvas. For `resolution="auto"`, resolve once from selected clips before any photo is
rendered.

- [ ] **Step 8: Add a 4:3 renderer regression test**

```python
def test_four_by_three_photo_has_fixed_aspect_fit_window():
    source = np.full((300, 400, 3), 0.5, dtype=np.float32)
    first, last = list(
        render_ken_burns_streaming(
            source, 1920, 1080, KenBurnsParams(fps=1, duration=2, zoom_end=1.08)
        )
    )
    assert first.shape == (1080, 1920, 3)
    assert last.shape == (1080, 1920, 3)
    assert np.var(first[:, :240]) > 0
```

The test catches a return to full-canvas 16:9 cropping or black padding while existing renderer
tests protect the fixed window across animation frames.

- [ ] **Step 9: Run Task 1 tests and commit**

Run: `.venv/bin/pytest tests/test_output_canvas.py tests/test_generate.py tests/test_unified_budget.py tests/integration/photos/test_photo_renderer.py -q`

Commit: `fix: resolve one canvas for photo and video rendering`

---

### Task 2: Probe content-analysis once and circuit-break permanent failures

**Files:**
- Create: `src/immich_memories/analysis/provider_health.py`
- Modify: `src/immich_memories/analysis/llm_response_parser.py`
- Modify: `src/immich_memories/analysis/_content_providers.py`
- Modify: `src/immich_memories/analysis/content_analyzer.py`
- Modify: `src/immich_memories/analysis/clip_analyzer.py`
- Modify: `src/immich_memories/photos/scoring.py`
- Modify: `src/immich_memories/preflight.py`
- Test: `tests/test_content_analyzer.py`
- Test: `tests/test_preflight.py`
- Test: `tests/test_photo_scoring.py`

**Interfaces:**
- Produces: `ProviderState` enum with `READY`, `UNREACHABLE`, `AUTH_FAILED`, `ROUTE_MISSING`,
  `MODEL_MISSING`, and `DISABLED`.
- Produces: `ProviderHealth(state, message)` with `available` property.
- Produces: `ProviderCircuit`, a run-scoped state object shared by every analyzer instance.
- Adds: `ContentAnalyzer.check_health() -> ProviderHealth` and `ContentAnalyzer.disable(reason)`.
- Adds: `ContentAnalyzer.available` run-level circuit state.
- Changes: photo scoring accepts a shared `ContentAnalyzer | None` rather than creating raw
  per-photo httpx requests.

- [ ] **Step 1: Add failing provider-state tests**

```python
@pytest.mark.parametrize(
    ("status", "body", "state"),
    [
        (401, {}, ProviderState.AUTH_FAILED),
        (404, {}, ProviderState.ROUTE_MISSING),
        (404, {"error": {"message": "model not found"}}, ProviderState.MODEL_MISSING),
    ],
)
def test_openai_health_classifies_permanent_failures(status, body, state):
    analyzer = OpenAICompatibleContentAnalyzer(model="gone", base_url="http://llm/v1")
    analyzer._client = _mock_client(status, body)
    assert analyzer.check_health().state is state
```

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/test_content_analyzer.py -q`

Expected: `check_health` and `ProviderState` are missing.

- [ ] **Step 3: Implement bounded health checks and circuit state**

The OpenAI-compatible check first requests `/models` when available, then sends a one-token
`/chat/completions` request only when model availability cannot be established. Connection errors
become `UNREACHABLE`; 401/403 become `AUTH_FAILED`; a model-specific 404 body becomes
`MODEL_MISSING`; other 404 responses become `ROUTE_MISSING`. Diagnostic messages contain status
and configured model name, never response bodies or credentials.

`analyze_segment()` returns neutral `ContentAnalysis(confidence=0.0)` without extracting frames or
issuing HTTP once disabled. Permanent 4xx responses call `disable()` immediately and log once.

- [ ] **Step 4: Run provider tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_content_analyzer.py -q`

- [ ] **Step 5: Add a failing shared photo/video circuit test**

```python
def test_permanent_provider_failure_is_not_retried_for_photos(tmp_path):
    analyzer = _failing_shared_analyzer(status_code=404)
    first = score_photo_with_llm(tmp_path / "a.jpg", 0.5, photo_config, app_config, analyzer)
    second = score_photo_with_llm(tmp_path / "b.jpg", 0.5, photo_config, app_config, analyzer)
    assert first == second == 0.5
    assert analyzer.request_count == 1
```

- [ ] **Step 6: Inject one analyzer into video and photo scoring**

`run_pipeline_and_generate()` creates one analyzer plus `ProviderCircuit`, probes it, and injects it
into `SmartPipeline`/`ClipAnalyzer`. A non-ready result sets its scoring weight to zero.
`_merge_photos_into_pool()` and `score_photos()` accept the same analyzer before the runner closes
it, so photo scoring observes any circuit opened during video analysis. Remove `_query_photo_llm()`'s
independent raw httpx request. `SmartPipeline` does not own an injected analyzer; the runner closes
it after video and photo selection.

- [ ] **Step 7: Make preflight use the same classifier**

`check_llm()` returns `WARNING` for optional unreachable/route/model failures and `ERROR` for
authentication rejection. Its message names the state and configured model without echoing the
remote body.

- [ ] **Step 8: Run Task 2 tests and commit**

Run: `.venv/bin/pytest tests/test_content_analyzer.py tests/test_preflight.py tests/test_photo_scoring.py tests/test_pipeline_orchestration_coverage.py -q`

Commit: `fix: circuit-break unavailable content analysis`

---

### Task 3: Make requested duration a hard final timeline budget

**Files:**
- Create: `src/immich_memories/processing/timeline_budget.py`
- Modify: `src/immich_memories/processing/assembly_config.py`
- Modify: `src/immich_memories/processing/title_inserter.py`
- Modify: `src/immich_memories/analysis/clip_scaler.py`
- Modify: `src/immich_memories/analysis/clip_refiner.py`
- Modify: `src/immich_memories/cli/_pipeline_runner.py`
- Modify: `src/immich_memories/generate.py`
- Modify: `src/immich_memories/generate_settings.py`
- Test: `tests/test_timeline_budget.py`
- Test: `tests/test_clip_scaler.py`
- Test: `tests/test_titles.py`
- Test: `tests/test_generate.py`

**Interfaces:**
- Produces: `TimelinePlan(target_duration, content_budget, title_budget, title_duration,
  ending_duration, divider_duration, max_dividers)`.
- Produces: `plan_timeline(clips, title_settings, target_duration, memory_type) -> TimelinePlan`.
- Adds: `TitleScreenSettings.max_dividers: int | None`.
- Adds: `GenerationParams.timeline_plan: TimelinePlan | None`.
- Adds: `ClipScaler.scale_to_target_duration(..., max_overrun_seconds: float = 0.0)`.

- [ ] **Step 1: Add failing literal budget tests**

```python
def test_sixty_second_memory_keeps_eighty_percent_content():
    plan = plan_timeline(_monthly_clips(3), _titles(3.5, 2.0, 7.0), 60.0, "monthly")
    assert plan.content_budget >= 48.0
    assert plan.title_budget <= 12.0
    assert plan.title_duration == 3.5
    assert plan.ending_duration == 7.0
    assert plan.max_dividers == 0


def test_first_month_is_not_counted_as_a_divider():
    plan = plan_timeline(_monthly_clips(2), _titles(3.5, 2.0, 0.0), 60.0, "monthly")
    assert plan.max_dividers == 1
```

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/test_timeline_budget.py -q`

Expected: planner module is missing.

- [ ] **Step 3: Implement the pure title/content planner**

Calculate `max_title_budget = target_duration * 0.20`. Fit opening first, ending second, and
chronological dividers third. For short targets, shorten opening to the title budget; include a
shortened ending only when at least 2.0 seconds remain. Count month dividers after the first
eligible month. For trips, count named location changes over 30 km. Set
`content_budget = target_duration - title_budget`, which guarantees at least 80% content.

- [ ] **Step 4: Run planner tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_timeline_budget.py -q`

- [ ] **Step 5: Add a failing strict-scaler test**

```python
def test_strict_scaler_never_keeps_more_than_content_budget():
    selected = scaler.scale_to_target_duration(clips, 48.0, max_overrun_seconds=0.0)
    assert sum(c.end_time - c.start_time for c in selected) <= 48.0
```

- [ ] **Step 6: Make scaler tolerance explicit and strict for final planning**

Replace the hard-coded `target * 1.10` with `target + max_overrun_seconds`. If protected clips
alone exceed a strict budget, keep the best temporally distributed subset that fits; a protection
preference cannot violate the hard final contract. Existing callers pass their intended tolerance
explicitly.

- [ ] **Step 7: Apply the plan before generation and during title insertion**

After `SmartPipeline.run_selection()`, build the plan from selected asset dates/GPS and strictly
rescale selection to `content_budget`. Store the plan on `GenerationParams`. `_build_title_settings`
uses its durations and divider cap. Month/year/location insertion stops after `max_dividers`; when
ending duration is zero, `show_ending_screen` is false.

- [ ] **Step 8: Add a failing final-artifact tolerance test**

```python
def test_generation_rejects_artifact_more_than_one_second_over_target(params, monkeypatch):
    params.target_duration_seconds = 60.0
    monkeypatch.setattr(generate, "_probe_file_duration", lambda _: 61.1)
    with pytest.raises(GenerationError, match="duration budget"):
        generate_memory(params)
```

- [ ] **Step 9: Validate final duration after all post-processing**

Probe the completed artifact after music and container finalization. Raise `GenerationError` when
duration exceeds `target_duration_seconds + 1.0`. Record actual duration only after validation.

- [ ] **Step 10: Run Task 3 tests and commit**

Run: `.venv/bin/pytest tests/test_timeline_budget.py tests/test_clip_scaler.py tests/test_titles.py tests/test_generate.py tests/test_unified_budget.py -q`

Commit: `fix: budget titles inside requested duration`

---

### Task 4: Make `generate --dry-run` execute discovery and selection

**Files:**
- Create: `src/immich_memories/cli/_generation_preview.py`
- Modify: `src/immich_memories/cli/_pipeline_runner.py`
- Modify: `src/immich_memories/cli/generate.py`
- Modify: `src/immich_memories/cli/_trip_generation.py`
- Test: `tests/integration/cli/test_generate.py`
- Test: `tests/test_trip_generation.py`

**Interfaces:**
- Produces: `GenerationPreview` dataclass with candidate counts, selected counts, selected duration,
  `TimelinePlan`, `OutputCanvas`, output path, upload intent, and music policy.
- Adds: `run_pipeline_and_generate(..., dry_run: bool = False)`; dry-run retains the existing return
  tuple but returns the planned output path without creating it.
- Adds: `handle_trip_generation(..., dry_run: bool = False)`.

- [ ] **Step 1: Replace the old no-connect dry-run test with a failing planning test**

```python
def test_cli_dry_run_connects_selects_and_writes_no_artifact(tmp_path):
    result = runner.invoke(main, ["generate", "--year", "2025", "--dry-run", "--quiet"])
    assert result.exit_code == 0
    assert "Selected" in result.output
    assert "Estimated final duration" in result.output
    assert not list(tmp_path.glob("*.mp4"))
```

The Immich boundary returns complete asset metadata; final generation, upload, music, notification,
and FFmpeg assembly boundaries raise if called.

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/integration/cli/test_generate.py -k dry_run -q`

Expected: current CLI returns before connecting or selecting.

- [ ] **Step 3: Add `GenerationPreview` and move the dry-run branch**

Remove the early return before `SyncImmichClient`. Run people/date/content discovery and the normal
analysis/selection/timeline planning. Return before `generate_memory()`. Print one stable summary
through `_generation_preview.py`; do not claim the planned output path exists.

- [ ] **Step 4: Add a failing trip dry-run test**

```python
def test_trip_dry_run_uses_detected_trip_without_generation():
    handle_trip_generation(..., dry_run=True)
    assert preview.location_name == "coastal-trip"
    assert preview.photo_candidates == 12
    generate_memory.assert_not_called()
```

- [ ] **Step 5: Apply the same boundary to trip generation**

Thread `dry_run` through trip choice, geofiltering, and pipeline planning. Each selected trip in
`--all-trips` prints a separate preview and creates no artifact.

- [ ] **Step 6: Run Task 4 tests and commit**

Run: `.venv/bin/pytest tests/integration/cli/test_generate.py tests/test_trip_generation.py tests/test_trip_photo_gps.py -q`

Commit: `feat: make generate dry-run validate selection`

---

### Task 5: Report optional PANNs semantics accurately

**Files:**
- Modify: `src/immich_memories/audio/content_analyzer.py`
- Modify: `src/immich_memories/preflight.py`
- Modify: `src/immich_memories/cli/config_cmd.py`
- Modify: `docs-site/docs/reference/config-reference.md`
- Modify: `docs-site/docs/deploy/installation/uv-pip.md`
- Modify: `docs-site/docs/create/pipeline/audio-and-music.md`
- Test: `tests/test_preflight.py`
- Test: `tests/test_audio.py`

**Interfaces:**
- Produces: `check_audio_content(config) -> CheckResult`.
- Adds: `AudioContentAnalyzer.backend_status() -> Literal["panns", "energy"]` after lazy check.

- [ ] **Step 1: Add failing preflight status tests**

```python
def test_audio_preflight_warns_when_panns_requested_but_extra_absent(config):
    config.audio_content.enabled = True
    config.audio_content.use_panns = True
    result = check_audio_content(config)
    assert result.status is CheckStatus.WARNING
    assert "energy-only fallback" in result.message


def test_audio_preflight_skips_when_disabled(config):
    config.audio_content.enabled = False
    assert check_audio_content(config).status is CheckStatus.SKIPPED
```

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/test_preflight.py tests/test_audio.py -q`

- [ ] **Step 3: Implement dependency-only preflight and one startup notice**

Use `importlib.util.find_spec()` in preflight so checking status does not instantiate Torch or
download a model. Runtime continues to lazily initialize PANNs. Log the selected backend once per
analyzer instance; preserve energy fallback on import/model errors.

- [ ] **Step 4: Correct and expand audio-ML documentation**

Document that `audio-ml` adds Torch/PANNs, what labels it protects, what energy fallback can and
cannot do, and both exact install commands. Keep the extra optional and do not change base
dependencies.

- [ ] **Step 5: Run Task 5 tests and metadata verification, then commit**

Run: `.venv/bin/pytest tests/test_preflight.py tests/test_audio.py -q`

Run: `.venv/bin/python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); assert {'torch>=2.0','panns-inference>=0.1'} <= set(d['project']['optional-dependencies']['audio-ml'])"`

Commit: `docs: expose optional semantic audio status`

---

### Task 6: Persist notification health and enforce a quiet cooldown

**Files:**
- Create: `src/immich_memories/cache/migration_v15.py`
- Create: `src/immich_memories/automation/notification_state.py`
- Modify: `src/immich_memories/cache/versions.py`
- Modify: `src/immich_memories/cache/database.py`
- Modify: `src/immich_memories/config_models.py`
- Modify: `src/immich_memories/automation/notifications.py`
- Modify: `src/immich_memories/automation/runner.py`
- Modify: `src/immich_memories/automation/state_store.py`
- Modify: `src/immich_memories/cli/_pipeline_runner.py`
- Modify: `src/immich_memories/cli/auto_cmd.py`
- Modify: `src/immich_memories/scheduling/daemon.py`
- Modify: `src/immich_memories/preflight.py`
- Modify: `src/immich_memories/ui/app.py`
- Modify: `docs-site/docs/reference/config-reference.md`
- Test: `tests/test_notifications.py`
- Test: `tests/test_auto_runner.py`
- Test: `tests/test_health.py`
- Test: `tests/test_operational_phases.py`

**Interfaces:**
- Migration v15 creates singleton `notification_health` fields: `last_attempt_at`,
  `last_success_at`, `last_failure_at`, `failure_category`, and `failure_message`.
- Produces: `NotificationHealth` and `NotificationStateStore` with `get()`, `is_cooling_down()`,
  `record_success()`, and `record_failure(category, message)`.
- Extends: `NotificationConfig.attach_thumbnail: bool = False` and
  `NotificationConfig.cooldown_hours: int = 24`.
- Extends: `notify_job_complete(..., db_path: Path | None = None,
  attach_thumbnail: bool = False, cooldown_hours: int = 24,
  bypass_cooldown: bool = False) -> bool`.
- Extends: `AutomationStatus.notification_health` and its `to_dict()` payload.

- [ ] **Step 1: Add a failing migration/state-store test**

```python
def test_notification_failure_is_sanitized_and_opens_cooldown(tmp_path):
    store = NotificationStateStore(tmp_path / "cache.db")
    store.record_failure("quota", "HTTP 429 from https://secret.example/topic")
    health = store.get()
    assert health.failure_category == "quota"
    assert "https://" not in (health.failure_message or "")
    assert store.is_cooling_down(hours=24)
```

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/test_notifications.py -q`

- [ ] **Step 3: Add schema v15 and typed notification state**

Create a single-row table with `id INTEGER PRIMARY KEY CHECK (id = 1)`. Store UTC ISO timestamps.
Map failures to bounded generic messages such as `provider quota exceeded` or
`notification transport failed` before persistence; never pass raw exceptions, response bodies, or
configured URLs to the store. A later success ends the active cooldown but retains the last failure
timestamp/category as historical health evidence.

- [ ] **Step 4: Add failing delivery/cooldown tests**

```python
def test_failed_delivery_suppresses_normal_retry_during_cooldown(tmp_path):
    assert notify_job_complete(..., db_path=tmp_path / "cache.db") is False
    assert notify_job_complete(..., db_path=tmp_path / "cache.db") is False
    assert apprise_instance.notify.call_count == 1


def test_explicit_test_notification_bypasses_cooldown(tmp_path):
    send_test_notification(urls, db_path=tmp_path / "cache.db")
    assert apprise_instance.notify.called
```

- [ ] **Step 5: Implement delivery policy and opt-in thumbnail**

Check cooldown before thumbnail extraction or Apprise construction. On `False`, record category
`delivery_failed`; on caught transport exception, classify a sanitized `quota` category when the
exception/status contains 429, otherwise `transport`. Record success and clear active failure.
Extract/attach a thumbnail only when `attach_thumbnail` is true.

All manual, smart-auto, and legacy scheduled call sites pass database path and notification config.
`auto test-notification` passes `bypass_cooldown=True`.

- [ ] **Step 6: Add failing status/preflight/health projections**

```python
def test_automation_status_includes_notification_warning(config):
    payload = AutoRunner(config).status().to_dict()
    assert payload["notification_health"]["cooldown_active"] is True


async def test_readiness_exposes_notification_warning_without_failing_ready(config):
    response = await _readiness_handler(MagicMock())
    assert response.status_code == 200
    assert json.loads(response.body)["notification_health"]["status"] == "warning"
```

- [ ] **Step 7: Project optional health through all status surfaces**

Add notification state to `AutomationStatus`, `auto status`, detailed `/health`, and
`check_notifications()`. A configured active failure is `WARNING`; disabled/unconfigured is
`SKIPPED`; a recent success is `OK`. Read failures remain non-fatal and never change readiness.

- [ ] **Step 8: Update notification documentation**

Document text-only default, `attach_thumbnail`, 24-hour cooldown, test bypass, and where health is
visible. Do not claim a provider quota can be inspected without sending.

- [ ] **Step 9: Run Task 6 tests and commit**

Run: `.venv/bin/pytest tests/test_notifications.py tests/test_auto_runner.py tests/test_health.py tests/test_operational_phases.py tests/test_preflight.py -q`

Commit: `fix: make notification failures quiet and visible`

---

### Task 7: Full verification and real-library smoke checks

**Files:**
- Modify: `docs/reviews/2026-08-11-launch-readiness-audit.md`

**Interfaces:**
- Consumes all six completed behaviors.
- Produces evidence in the saved launch assessment; no new runtime interface.

- [ ] **Step 1: Run formatting, lint, and type checks**

Run: `.venv/bin/ruff format --check src tests`

Run: `.venv/bin/ruff check src tests`

Run: `.venv/bin/mypy src`

- [ ] **Step 2: Run the full automated suite**

Run: `.venv/bin/pytest -q`

Expected: zero failures. Pre-existing platform skips are recorded, not converted to failures.

- [ ] **Step 3: Run read-only local capability checks**

Run: `.venv/bin/immich-memories preflight --verbose`

Expected: Immich v3 resolves successfully; absent LLM/PANNs and notification cooldown are explicit
optional warnings, with no credential values in output.

- [ ] **Step 4: Run a real-library dry-run**

Run: `.venv/bin/immich-memories generate --memory-type person_spotlight --person "Sam Dumont" --year 2026 --duration 60 --resolution 720p --dry-run --quiet`

Expected: discovery and selection counts plus a timeline/canvas summary; no new video, upload,
notification, or scheduler state change.

- [ ] **Step 5: Generate one bounded local smoke artifact**

Run the same 60-second flow at 720p without `--dry-run`, with upload disabled in configuration and
notifications disabled by an environment override. Verify with ffprobe that duration is at most
61.0 seconds and canvas is 1280x720. Inspect sampled frames containing a 4:3/portrait photo for one
stable aspect-fit window and no second blur/crop.

- [ ] **Step 6: Record evidence and commit**

Append exact test totals, preflight states, dry-run counts, artifact duration/resolution, and visual
inspection result to the launch assessment.

Commit: `docs: verify launch quality follow-ups`

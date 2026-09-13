# P0 Encoding, Artifact Validation, and Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the requested codec/HDR/hardware settings truthful, persist a valid video before optional work, and retry failed delivery without rerendering.

**Architecture:** Resolve one immutable encoding plan before assembly and pass it through final video, clip, and title encoding. Probe the finished artifact against that plan before atomically publishing it. Track artifact completion separately from Immich delivery so optional music can fall back to the valid base render and upload can remain pending.

**Tech Stack:** Python dataclasses/StrEnum, FFmpeg/ffprobe, SQLite, Pydantic v2, pytest, real tiny-media integration tests.

## Global Constraints

- H.264 requests produce H.264, H.265 requests produce HEVC, and ProRes requests produce ProRes.
- Disabling hardware selects a software encoder for the same codec.
- A missing or failed hardware encoder falls back to software for the same codec only.
- H.264 output is SDR; HDR H.264 configuration fails before rendering.
- H.265 may preserve HDR; explicit SDR tone-maps HDR input.
- ProRes is SDR for this release; explicit HDR ProRes fails before rendering.
- A file is published only after ffprobe proves it is non-empty, decodable, and matches the plan.
- Optional generated music failure keeps the validated silent/original-audio render and records a warning.
- Upload failure does not invalidate or delete a rendered video.
- The next daily `auto run` retries one pending delivery before selecting new generation work.
- Every task follows RED → GREEN → REFACTOR.

---

## File structure

- Create `src/immich_memories/processing/encoding_plan.py`: codec/HDR/hardware resolution.
- Create `src/immich_memories/processing/output_contract.py`: ffprobe model and validation.
- Modify `src/immich_memories/config_models.py`: explicit HDR mode.
- Modify `src/immich_memories/processing/assembly_config.py`: carry one plan.
- Modify `src/immich_memories/processing/hardware.py`: ProRes software mapping and typed capabilities.
- Modify `src/immich_memories/processing/assembly_engine.py`, `clip_encoder.py`, and `title_inserter.py`: consume the plan.
- Modify `src/immich_memories/titles/encoding.py` and its callers: consume the same codec policy.
- Modify `src/immich_memories/generate_settings.py`: resolve the plan once.
- Modify `src/immich_memories/generate_music.py`: atomic music replacement.
- Modify `src/immich_memories/generate.py`: artifact-first completion and warnings.
- Modify `src/immich_memories/cache/database.py`: additive delivery-state migration.
- Modify `src/immich_memories/tracking/models.py`, `run_database.py`, and `run_tracker.py`: delivery state.
- Modify `src/immich_memories/automation/runner.py`: pending-delivery retry.
- Modify `src/immich_memories/ui/pages/_step4_generate.py`, `_step4_music.py`, and `_step4_upload.py`: same lifecycle.
- Create `tests/test_encoding_plan.py`, `tests/test_output_contract.py`, and `tests/test_delivery_retry.py`.
- Modify processing, generation, tracking, music, upload, HDR, and UI tests named below.

### Task 1: Resolve one explicit encoding contract

**Files:**
- Create: `src/immich_memories/processing/encoding_plan.py`
- Modify: `src/immich_memories/config_models.py`
- Modify: `src/immich_memories/processing/hardware.py`
- Create: `tests/test_encoding_plan.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_hardware_detection.py`

**Interfaces:**
- Produces: `OutputCodec.H264`, `.H265`, `.PRORES`.
- Produces: `HdrMode.AUTO`, `.SDR`, `.HDR`.
- Produces: immutable `EncodingPlan(codec, encoder, encoder_args, hdr, pixel_format, container)`.
- Produces: `resolve_encoding_plan(request, capabilities, input_has_hdr) -> EncodingPlan`.
- Extends: `OutputConfig.hdr_mode: HdrMode = HdrMode.AUTO`.

- [ ] **Step 1: Write the codec/HDR/hardware matrix tests**

```python
@pytest.mark.parametrize(
    ("codec", "hardware", "backend", "expected_encoder"),
    [
        ("h264", True, "apple", "h264_videotoolbox"),
        ("h264", False, "apple", "libx264"),
        ("h265", True, "apple", "hevc_videotoolbox"),
        ("h265", False, "apple", "libx265"),
        ("prores", True, "apple", "prores_ks"),
        ("prores", False, "apple", "prores_ks"),
    ],
)
def test_encoder_never_changes_requested_codec(codec, hardware, backend, expected_encoder) -> None:
    plan = resolve_encoding_plan(request(codec, hardware), capabilities(backend), False)
    assert plan.encoder == expected_encoder
```

Add these policy cases:

- H.264 + HDR input + auto resolves SDR with a tone-map requirement.
- H.264 + explicit HDR raises `UnsupportedEncodingCombination`.
- H.265 + HDR input + auto resolves HDR.
- H.265 + explicit SDR resolves SDR with a tone-map requirement.
- ProRes + auto resolves SDR; ProRes + explicit HDR raises.
- Unsupported hardware encoding falls back to the matching software encoder.

- [ ] **Step 2: Run focused tests**

Run: `uv run pytest tests/test_encoding_plan.py tests/test_config.py tests/test_hardware_detection.py -q`

Expected: FAIL because the current helper chooses HEVC independently of `output.codec`.

- [ ] **Step 3: Implement exhaustive plan resolution**

```python
@dataclass(frozen=True)
class EncodingRequest:
    codec: OutputCodec
    hdr_mode: HdrMode
    hardware_enabled: bool
    preset: Literal["fast", "balanced", "quality"]
    crf: int
    container: Literal["mp4", "mov"]

@dataclass(frozen=True)
class EncodingPlan:
    codec: OutputCodec
    encoder: str
    encoder_args: tuple[str, ...]
    hdr: bool
    tone_map_to_sdr: bool
    pixel_format: str
    container: str
```

Use `get_ffmpeg_encoder()` only after resolving the requested codec. Extend that helper with
an explicit ProRes software case returning `prores_ks`; never route an unrecognized codec to
libx265. Validate compatible container/codec pairs: ProRes requires MOV, H.264/H.265 accept
MP4 or MOV.

- [ ] **Step 4: Run the matrix and commit**

Run: `uv run pytest tests/test_encoding_plan.py tests/test_config.py tests/test_hardware_detection.py -q`

Expected: PASS.

```bash
git add src/immich_memories/processing/encoding_plan.py src/immich_memories/config_models.py src/immich_memories/processing/hardware.py tests/test_encoding_plan.py tests/test_config.py tests/test_hardware_detection.py
git commit -m "fix: resolve one truthful encoding contract"
```

### Task 2: Carry the encoding plan through assembly and title clips

**Files:**
- Modify: `src/immich_memories/processing/assembly_config.py`
- Modify: `src/immich_memories/processing/assembly_engine.py`
- Modify: `src/immich_memories/processing/clip_encoder.py`
- Modify: `src/immich_memories/processing/title_inserter.py`
- Modify: `src/immich_memories/processing/hdr_utilities.py`
- Modify: `src/immich_memories/titles/encoding.py`
- Modify: `src/immich_memories/titles/renderer_ffmpeg.py`
- Modify: `src/immich_memories/titles/video_encoding.py`
- Modify: `src/immich_memories/titles/map_animation.py`
- Modify: `src/immich_memories/titles/taichi_video.py`
- Modify: `src/immich_memories/titles/ending_service.py`
- Modify: `src/immich_memories/titles/globe_video.py`
- Modify: `src/immich_memories/generate_settings.py`
- Modify: `tests/test_assembler_unit.py`
- Modify: `tests/test_title_hdr.py`
- Modify: `tests/test_titles.py`
- Modify: `tests/test_hdr_conversion.py`

**Interfaces:**
- Replaces: `AssemblySettings.output_codec` and implicit `_get_gpu_encoder_args()` calls with required `AssemblySettings.encoding_plan`.
- Produces: `title_encoder_args(plan) -> list[str]` for title/video helper call sites.

- [ ] **Step 1: Write command-construction regressions**

For H.264/software, assert final assembly, extracted clips, generated title clips, and ending
screens all contain `-c:v libx264` and never contain `hevc`, `libx265`, or `hvc1`. Repeat with
H.265/hardware and ProRes/software expectations. For H.264 with HDR input, assert the filter
graph contains the existing zscale/tonemap SDR conversion and SDR color tags.

- [ ] **Step 2: Run assembly/title command tests**

Run:

```bash
uv run pytest tests/test_assembler_unit.py tests/test_title_hdr.py tests/test_titles.py tests/test_hdr_conversion.py -q
```

Expected: FAIL because processing code and title helpers independently choose HEVC.

- [ ] **Step 3: Make `EncodingPlan` the only final-output encoder source**

Resolve the plan in `_build_assembly_settings()` after input HDR inspection and hardware
detection. Store it on `AssemblySettings`. Replace every processing-layer
`_get_gpu_encoder_args()` call with plan data. Keep HDR filter construction in
`hdr_utilities.py`, but remove encoder selection from that module.

Change title helper signatures from `_get_gpu_encoder_args(hdr: bool)` to
`get_title_encoder_args(plan: EncodingPlan)`. Thread the plan from `TitleInserter` to renderer,
map, ending, globe, and Taichi title paths. Standalone title-generation commands construct an
explicit H.264/SDR plan rather than relying on platform detection. Derive intermediate suffixes
from the plan: `.mov` for ProRes and the requested container for H.264/H.265, so title and content
clips remain concat-compatible.

- [ ] **Step 4: Prove the obsolete selector is gone from final-output paths**

Run:

```bash
rg -n '_get_gpu_encoder_args' src/immich_memories/processing src/immich_memories/titles
uv run pytest tests/test_assembler_unit.py tests/test_title_hdr.py tests/test_titles.py tests/test_hdr_conversion.py tests/test_output_quality.py -q
```

Expected: `rg` returns no matches; tests PASS.

- [ ] **Step 5: Commit end-to-end plan propagation**

```bash
git add src/immich_memories/processing/assembly_config.py src/immich_memories/processing/assembly_engine.py src/immich_memories/processing/clip_encoder.py src/immich_memories/processing/title_inserter.py src/immich_memories/processing/hdr_utilities.py src/immich_memories/titles/encoding.py src/immich_memories/titles/renderer_ffmpeg.py src/immich_memories/titles/video_encoding.py src/immich_memories/titles/map_animation.py src/immich_memories/titles/taichi_video.py src/immich_memories/titles/ending_service.py src/immich_memories/titles/globe_video.py src/immich_memories/generate_settings.py tests/test_assembler_unit.py tests/test_title_hdr.py tests/test_titles.py tests/test_hdr_conversion.py tests/test_output_quality.py
git commit -m "fix: honor encoding plan across every render path"
```

### Task 3: Probe and atomically publish the finished artifact

**Files:**
- Create: `src/immich_memories/processing/output_contract.py`
- Modify: `src/immich_memories/generate.py`
- Create: `tests/test_output_contract.py`
- Modify: `tests/test_generate.py`
- Modify: `tests/integration/processing/test_encoding_real.py`

**Interfaces:**
- Produces: `OutputProbe(codec, duration_seconds, size_bytes, pixel_format, color_transfer)`.
- Produces: `probe_output(path) -> OutputProbe`.
- Produces: `validate_output(path, plan) -> OutputProbe` or `InvalidOutputArtifact`.
- Produces: `publish_validated_output(staged_path, final_path, plan) -> OutputProbe`.

- [ ] **Step 1: Write validation and atomicity tests**

```python
def test_codec_mismatch_is_rejected(tmp_path, h264_plan, probe_runner) -> None:
    staged = write_nonempty(tmp_path / "memory.assembling.mp4")
    probe_runner.returns(codec="hevc", duration=12.0, size=4096)
    with pytest.raises(InvalidOutputArtifact, match="expected h264, got hevc"):
        publish_validated_output(staged, tmp_path / "memory.mp4", h264_plan)
    assert not (tmp_path / "memory.mp4").exists()

def test_valid_output_is_atomically_published(tmp_path, h264_plan, probe_runner) -> None:
    staged = write_nonempty(tmp_path / "memory.assembling.mp4")
    probe_runner.returns(codec="h264", duration=12.0, size=4096, color_transfer="bt709")
    publish_validated_output(staged, tmp_path / "memory.mp4", h264_plan)
    assert (tmp_path / "memory.mp4").exists()
```

Add zero-byte, missing video stream, zero duration, ffprobe failure, HDR tag mismatch, and
ProRes codec-name cases.

- [ ] **Step 2: Run output-contract tests**

Run: `uv run pytest tests/test_output_contract.py tests/test_generate.py -q`

Expected: FAIL because generation trusts the assembler path without final contract validation.

- [ ] **Step 3: Implement one JSON ffprobe and atomic replace**

Probe `stream=codec_name,pix_fmt,color_transfer,color_primaries` plus
`format=duration,size` in one command. Accept `hevc` for H.265 and `prores` for ProRes. Require
positive duration/size and expected HDR/SDR transfer metadata. Assemble to a sibling staged
filename, validate it, `os.replace()` it to the final filename, then fsync the containing
directory where supported.

- [ ] **Step 4: Run unit and real tiny-encoding tests**

Run:

```bash
uv run pytest tests/test_output_contract.py tests/test_generate.py -q
uv run pytest tests/integration/processing/test_encoding_real.py -q -m integration
```

Expected: PASS; real tests use generated sub-second color clips and ffprobe the result.

- [ ] **Step 5: Commit validated publication**

```bash
git add src/immich_memories/processing/output_contract.py src/immich_memories/generate.py tests/test_output_contract.py tests/test_generate.py tests/integration/processing/test_encoding_real.py
git commit -m "fix: validate artifacts before publishing"
```

### Task 4: Make optional music truly optional

**Files:**
- Modify: `src/immich_memories/generate_music.py`
- Modify: `src/immich_memories/generate_settings.py`
- Modify: `src/immich_memories/generate.py`
- Modify: `src/immich_memories/ui/pages/_step4_music.py`
- Modify: `tests/test_music_pipeline.py`
- Modify: `tests/test_generate.py`

**Interfaces:**
- Produces: `MusicPhaseResult(applied: bool, warning: str | None)`.
- Extends: run metadata warnings through the tracking changes in Task 5.

- [ ] **Step 1: Write fallback and replacement tests**

```python
def test_music_failure_keeps_valid_base_video(generation, valid_base, mixer) -> None:
    mixer.side_effect = RuntimeError("music backend unavailable")
    result = generation.run()
    assert result.path.read_bytes() == valid_base
    assert result.warnings == ["Optional music failed: music backend unavailable"]

def test_invalid_music_mix_never_replaces_base_video(generation, valid_base, probe_runner) -> None:
    probe_runner.reject_next("missing audio/video stream")
    result = generation.run()
    assert result.path.read_bytes() == valid_base
    assert result.music.applied is False
```

- [ ] **Step 2: Run music and generation tests**

Run: `uv run pytest tests/test_music_pipeline.py tests/test_generate.py -q`

Expected: FAIL because `apply_music_file()` exceptions fail the whole generation and UI
deletes the base before proving the mixed replacement.

- [ ] **Step 3: Mix to a staged sibling and validate before replace**

`_run_music_phase()` catches sanitized optional-music errors, closes the phase with one error,
and returns `MusicPhaseResult`. `apply_music_file()` writes a staged file, validates it against
the same encoding plan, and atomically replaces the base only when valid. The UI path uses the
same helper and never unlinks `result_path` first.

- [ ] **Step 4: Run music, generation, and UI helper tests**

Run: `uv run pytest tests/test_music_pipeline.py tests/test_generate.py tests/test_sidebar_completion.py -q`

Expected: PASS.

- [ ] **Step 5: Commit safe optional music**

```bash
git add src/immich_memories/generate_music.py src/immich_memories/generate_settings.py src/immich_memories/generate.py src/immich_memories/ui/pages/_step4_music.py tests/test_music_pipeline.py tests/test_generate.py tests/test_sidebar_completion.py
git commit -m "fix: preserve valid video when optional music fails"
```

### Task 5: Track artifact and delivery as separate states

**Files:**
- Modify: `src/immich_memories/cache/database.py`
- Modify: `src/immich_memories/tracking/models.py`
- Modify: `src/immich_memories/tracking/run_database.py`
- Modify: `src/immich_memories/tracking/run_tracker.py`
- Create: `tests/test_delivery_retry.py`
- Modify: `tests/test_run_tracker.py`
- Modify: `tests/test_run_database_fk.py`

**Interfaces:**
- Produces: `DeliveryStatus.NOT_REQUESTED`, `.PENDING`, `.DELIVERED`.
- Extends: `RunMetadata` with `delivery_status`, `delivery_attempts`, `delivery_error`, `immich_asset_id`, and `warnings`.
- Produces: `RunTracker.complete_artifact(output_path, probe, warnings)`.
- Produces: `RunTracker.mark_delivery_pending(error)` and `mark_delivered(asset_id)`.
- Produces: `RunDatabase.get_oldest_pending_delivery(source) -> RunMetadata | None`.

- [ ] **Step 1: Write lifecycle and migration tests**

```python
def test_upload_failure_does_not_change_completed_artifact(tracker, output_path, probe) -> None:
    tracker.complete_artifact(output_path, probe, warnings=[])
    tracker.mark_delivery_pending("Immich timed out")
    run = tracker.db.get_run(tracker.run_id)
    assert run.status == "completed"
    assert run.output_path == str(output_path)
    assert run.delivery_status is DeliveryStatus.PENDING
    assert run.delivery_attempts == 1

def test_pending_query_requires_existing_output(db) -> None:
    assert db.get_oldest_pending_delivery(source="auto") == expected_run
```

- [ ] **Step 2: Run tracking tests**

Run: `uv run pytest tests/test_delivery_retry.py tests/test_run_tracker.py tests/test_run_database_fk.py -q`

Expected: FAIL because run status currently conflates artifact and upload completion.

- [ ] **Step 3: Add schema migration 11 and tracking methods**

Increment the schema after automation migration 10 and add columns:

```sql
ALTER TABLE pipeline_runs ADD COLUMN delivery_status TEXT NOT NULL DEFAULT 'not_requested';
ALTER TABLE pipeline_runs ADD COLUMN delivery_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE pipeline_runs ADD COLUMN delivery_error TEXT;
ALTER TABLE pipeline_runs ADD COLUMN immich_asset_id TEXT;
ALTER TABLE pipeline_runs ADD COLUMN warnings_json TEXT NOT NULL DEFAULT '[]';
```

Only completed runs with `delivery_status='pending'`, a non-null output path, and an existing
file qualify for retry. Missing files are skipped with an actionable warning; they are not
silently marked delivered.

- [ ] **Step 4: Complete the artifact before attempting upload**

In `generate.py`, call `complete_artifact()` after final validation and optional-music fallback.
Then attempt upload. On success call `mark_delivered()`. On failure call
`mark_delivery_pending()`, preserve the file, and raise a delivery-specific `GenerationError`
so automation reports nonzero while retaining the completed run.

- [ ] **Step 5: Run lifecycle tests and commit**

Run: `uv run pytest tests/test_delivery_retry.py tests/test_run_tracker.py tests/test_run_database_fk.py tests/test_generate.py -q`

Expected: PASS.

```bash
git add src/immich_memories/cache/database.py src/immich_memories/tracking/models.py src/immich_memories/tracking/run_database.py src/immich_memories/tracking/run_tracker.py src/immich_memories/generate.py tests/test_delivery_retry.py tests/test_run_tracker.py tests/test_run_database_fk.py tests/test_generate.py
git commit -m "feat: separate artifact and delivery state"
```

### Task 6: Retry pending auto delivery before generating again

**Files:**
- Modify: `src/immich_memories/automation/models.py`
- Modify: `src/immich_memories/automation/runner.py`
- Modify: `src/immich_memories/cli/auto_cmd.py`
- Modify: `tests/test_delivery_retry.py`
- Modify: `tests/test_auto_runner.py`

**Interfaces:**
- Produces: `AutoAction.DELIVERY_RETRY` and `.GENERATION`.
- Extends: `AutoRunResult.action`.
- Produces: `AutoRunner.retry_pending_delivery() -> AutoRunResult | None`.

- [ ] **Step 1: Write daily retry ordering tests**

```python
def test_pending_delivery_is_retried_before_suggest(runner, pending_run) -> None:
    runner.uploader.upload_memory.return_value = {"asset_id": "asset-123", "album_id": None}
    result = runner.run_one()
    assert result.action is AutoAction.DELIVERY_RETRY
    assert result.outcome is AutoOutcome.COMPLETED
    runner.suggest.assert_not_called()

def test_failed_retry_stops_the_daily_invocation(runner, pending_run) -> None:
    runner.uploader.upload_memory.side_effect = ImmichAPIError("timeout")
    result = runner.run_one()
    assert result.outcome is AutoOutcome.FAILED
    assert pending_run.delivery_attempts == 2
    runner.suggest.assert_not_called()
```

- [ ] **Step 2: Run retry and runner tests**

Run: `uv run pytest tests/test_delivery_retry.py tests/test_auto_runner.py -q`

Expected: FAIL because automation does not inspect pending delivery.

- [ ] **Step 3: Implement one bounded retry action**

After attempt creation and preflight, but before cooldown/candidate selection, fetch the oldest
pending `source=auto` run. Upload its existing file using the version-aware client. Finish the
automation attempt with the same run ID and action. Whether upload succeeds or fails, return
immediately; never select a generation candidate in the same invocation.

- [ ] **Step 4: Run automation/delivery tests and commit**

Run: `uv run pytest tests/test_delivery_retry.py tests/test_auto_runner.py tests/test_auto_status.py -q`

Expected: PASS; status shows pending delivery count and oldest pending run.

```bash
git add src/immich_memories/automation/models.py src/immich_memories/automation/runner.py src/immich_memories/cli/auto_cmd.py tests/test_delivery_retry.py tests/test_auto_runner.py tests/test_auto_status.py
git commit -m "fix: retry pending delivery before new automation"
```

### Task 7: Align UI generation with the same lifecycle

**Files:**
- Modify: `src/immich_memories/ui/pages/_step4_generate.py`
- Modify: `src/immich_memories/ui/pages/_step4_music.py`
- Modify: `src/immich_memories/ui/pages/_step4_upload.py`
- Modify: `src/immich_memories/ui/state.py`
- Modify: `tests/test_sidebar_completion.py`
- Modify: `tests/test_ui_state.py`

**Interfaces:**
- Consumes: the original Step 4 `RunTracker` through assembly, music, and upload.
- Produces: UI state fields `generation_warning` and `delivery_status`.

- [ ] **Step 1: Write one-run and pending-upload UI tests**

Patch `RunTracker` and assert a Step 4 generation constructs it once, starts one run, completes
one artifact, and passes the same tracker object to music/upload. Make upload fail and assert
the completion screen still links the video, labels delivery `Pending`, and does not emit a
foreign-key warning.

- [ ] **Step 2: Run UI lifecycle tests**

Run: `uv run pytest tests/test_sidebar_completion.py tests/test_ui_state.py tests/test_run_database_fk.py -q`

Expected: FAIL because music creates phase state disconnected from the original tracked run and
upload failure is only a transient notification.

- [ ] **Step 3: Thread the tracker and durable result through Step 4**

Create the tracker in `_step4_generate.py`, keep it in the scoped generation call rather than a
global singleton, and pass it to music/upload helpers. Mirror CLI semantics: publish/complete
artifact first, optional music warning remains visible, delivery success/pending is stored and
rendered on the completion screen.

- [ ] **Step 4: Run UI lifecycle tests and commit**

Run: `uv run pytest tests/test_sidebar_completion.py tests/test_ui_state.py tests/test_run_database_fk.py tests/test_music_pipeline.py -q`

Expected: PASS.

```bash
git add src/immich_memories/ui/pages/_step4_generate.py src/immich_memories/ui/pages/_step4_music.py src/immich_memories/ui/pages/_step4_upload.py src/immich_memories/ui/state.py tests/test_sidebar_completion.py tests/test_ui_state.py tests/test_run_database_fk.py tests/test_music_pipeline.py
git commit -m "fix: align UI artifact and delivery lifecycle"
```

### Task 8: Verify codec truth and recoverable delivery

**Files:**
- Modify only through the owning task when verification exposes a defect.

**Interfaces:**
- Produces: green P0 encoding/delivery checkpoint consumed by release gates.

- [ ] **Step 1: Run focused unit suites**

```bash
uv run pytest tests/test_encoding_plan.py tests/test_output_contract.py tests/test_delivery_retry.py tests/test_generate.py tests/test_music_pipeline.py tests/test_assembler_unit.py tests/test_title_hdr.py tests/test_hdr_conversion.py tests/test_run_tracker.py tests/test_auto_runner.py -q
```

Expected: PASS.

- [ ] **Step 2: Run real codec matrix**

```bash
uv run pytest tests/integration/processing/test_encoding_real.py -q -m integration
```

Expected: available H.264 software, H.265 software, and ProRes software cases produce a tiny
video whose ffprobe codec matches the request. Hardware cases run only when capability
detection proves that exact encoder is available; absence is an explicit skip.

- [ ] **Step 3: Run static checks**

```bash
uv run ruff check src/immich_memories/processing src/immich_memories/generate.py src/immich_memories/generate_settings.py src/immich_memories/generate_music.py src/immich_memories/tracking src/immich_memories/automation tests/test_encoding_plan.py tests/test_output_contract.py tests/test_delivery_retry.py
uv run ruff format --check src/immich_memories/processing src/immich_memories/generate.py src/immich_memories/generate_settings.py src/immich_memories/generate_music.py src/immich_memories/tracking src/immich_memories/automation tests/test_encoding_plan.py tests/test_output_contract.py tests/test_delivery_retry.py
uv run mypy src/immich_memories/processing src/immich_memories/tracking src/immich_memories/automation
uv run lint-imports
```

Expected: every command exits zero.

- [ ] **Step 4: Route corrections through RED tests**

If verification exposes a defect, return to the owning task, add the smallest failing
regression test, and use that task's explicit commit. Do not create a catch-all commit.

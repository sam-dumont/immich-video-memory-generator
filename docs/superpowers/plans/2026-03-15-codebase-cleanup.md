# Codebase Cleanup Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the codebase from AI-scaffolded to genuinely maintainable — fix structural issues, add real integration tests, simplify config, and prevent regression via updated CLAUDE.md rules.

**Architecture:** Replace mixin-based class hierarchies with composed services using Protocol contracts. Add real FFmpeg integration tests. Simplify user-facing config to ~20 options with advanced overrides. Harden CLAUDE.md to prevent AI-specific smells from recurring.

**Tech Stack:** Python 3.11+, pytest, mypy (Protocols), FFmpeg, Pydantic, Make

**Spec:** `docs/superpowers/specs/2026-03-15-codebase-cleanup-design.md`

---

## Chunk 1: CLAUDE.md Hardening (Section 6)

### Task 1: Update CLAUDE.md — Replace mixin recommendations with composition rules

**Files:**
- Modify: `CLAUDE.md:129-143`

- [ ] **Step 1: Replace "Splitting Large Files" section**

Replace lines 129-143 in `CLAUDE.md` with:

```markdown
### Splitting Large Files

- Do not add new files over 500 lines — split proactively
- Split along **cohesion boundaries**, not arbitrary line counts
- Extract a service class with a Protocol contract for its dependencies
- The new module should be independently testable and importable
- Do NOT create mixins — use composition with constructor injection
- Do NOT create re-export shims for a single consumer — update import sites directly
- If you can't find a natural split, the file may genuinely be one cohesive unit.
  In that case, look for helper utilities to extract, not arbitrary method groups.
- When splitting, search for all imports of the moved symbols and update them
- After any structural changes, update `ARCHITECTURE.md` to reflect new layout
```

- [ ] **Step 2: Verify no other mixin/re-export references remain in CLAUDE.md**

Run: `grep -n "mixin\|re-export\|shim" CLAUDE.md`
Expected: Zero results (or only the "Do NOT" rules)

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "refactor(claude-md): replace mixin/re-export recommendations with composition rules"
```

### Task 2: Add Anti-Patterns section to CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (add after line ~143, before "Documentation Freshness")

- [ ] **Step 1: Add the Anti-Patterns section**

Insert before the "Documentation Freshness" section:

```markdown
### Anti-Patterns (DO NOT)

**Docstrings:**
- Do NOT write docstrings that restate the function signature
- Do NOT docstring private/internal functions unless behavior is non-obvious
- Public API functions MUST have docstrings explaining behavior, not just restating the signature

**Abstractions:**
- Do NOT create re-export shims for a single consumer — import from the source
- Do NOT split files purely for line count — splits must follow cohesion boundaries
- Do NOT use mixins — use composition with Protocol contracts
- If you need state from another module, inject it via constructor

**Tests:**
- Do NOT test dataclass field access, property getters, or Python arithmetic
- Do NOT mock more than 3 boundaries in a single test — if you need more, the code under test has too many dependencies
- Every mock MUST have a `# WHY:` comment explaining what external boundary it replaces
- Integration tests (`make test-integration`) must exist for any FFmpeg pipeline change

**Comments:**
- Do NOT write comments that describe what the next line does
- DO write comments that explain WHY something non-obvious is done
- Do NOT leave "extracted from X to stay within 500-line limit" comments

**Config:**
- New config options MUST have a sane default
- User-facing options go in Tier 1 (~20 options). Everything else is Tier 2 (advanced)
- Do NOT add migration/compat shims for renamed fields — deprecate, document, remove
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): add anti-patterns section to prevent AI code smells"
```

### Task 3: Add Self-Critique Protocol to CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (add at the end)

- [ ] **Step 1: Add the Self-Critique Protocol section**

Append to `CLAUDE.md`:

```markdown
### Self-Critique Protocol

After completing any major feature or refactor (>5 files changed), audit your own work:

1. `make ci` passes (baseline)
2. Skeptic review:
   - Would a senior engineer reject any of these patterns?
   - Are there docstrings that add no information?
   - Are there abstractions that serve no consumer?
   - Are tests testing behavior or testing mocks?
   - Are file splits following cohesion or line count?
3. AI hallmark check:
   - Unnecessary type annotations on obvious code?
   - Over-structured solutions (registry pattern for 3 items)?
   - Verbose module-level docstrings with ASCII art?
   - Helper functions called from exactly one place?
4. `make critique` — automated checks for common smells
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): add self-critique protocol for post-implementation review"
```

### Task 4: Add `make critique` target

**Files:**
- Modify: `Makefile`

- [ ] **Step 1: Add the critique target to Makefile**

Add after the `ci` target:

```makefile
critique:  ## Run self-critique checks for AI code smells
	@echo "=== AI Smell Audit ==="
	@echo "Checking for mechanical split comments..."
	@! grep -rn "to stay within" src/ || (echo "FAIL: Fix these splits" && exit 1)
	@echo "Checking for single-test files..."
	@grep -rc "def test_" tests/ | awk -F: '$$2<=1 {found=1; print "LOW-VALUE: " $$1} END {if(!found) print "OK"}'
	@echo "Checking for wildcard re-exports (outside __init__)..."
	@! grep -rn "from .* import \*" src/ --include="*.py" | grep -v __init__ || (echo "WARN: Wildcard re-exports found" && exit 1)
	@echo "Self-critique complete."
```

Also add `critique` to the `.PHONY` line.

- [ ] **Step 2: Run `make critique` to verify it works**

Run: `make critique`
Expected: Will likely flag existing issues (mechanical split comments, wildcard re-exports in `assembly.py`). That's expected — they'll be fixed in later sections.

- [ ] **Step 3: Update CLAUDE.md commands section to include critique**

Add to the commands list:

```bash
# Self-critique check for AI code smells
make critique
```

- [ ] **Step 4: Commit**

```bash
git add Makefile CLAUDE.md
git commit -m "feat(quality): add make critique target for AI smell detection"
```

---

## Chunk 2: Config Simplification (Section 3)

### Task 5: Add `clip_style` enum and parameter mapping

**Files:**
- Modify: `src/immich_memories/config_models.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing test for clip_style**

```python
def test_clip_style_balanced_sets_defaults():
    """clip_style: balanced should set all 5 duration params."""
    from immich_memories.config_models import AnalysisConfig
    config = AnalysisConfig(clip_style="balanced")
    assert config.optimal_clip_duration == 5.0
    assert config.max_optimal_duration == 10.0
    assert config.target_extraction_ratio == 0.4
    assert config.max_segment_duration == 15.0
    assert config.min_segment_duration == 2.0

def test_clip_style_fast_cuts():
    config = AnalysisConfig(clip_style="fast-cuts")
    assert config.optimal_clip_duration == 3.0

def test_clip_style_override():
    """Explicit values override clip_style defaults."""
    config = AnalysisConfig(clip_style="balanced", optimal_clip_duration=7.0)
    assert config.optimal_clip_duration == 7.0
    assert config.max_optimal_duration == 10.0  # rest from balanced
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_clip_style_balanced_sets_defaults -v`

- [ ] **Step 3: Implement clip_style in AnalysisConfig**

Add to `AnalysisConfig`:
- `clip_style: Literal["fast-cuts", "balanced", "long-cuts"] | None = None`
- A `@model_validator(mode="before")` that expands `clip_style` into the 5 params, but only for params not explicitly provided

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -k clip_style -v`

- [ ] **Step 5: Commit**

```bash
git add src/immich_memories/config_models.py tests/test_config.py
git commit -m "feat(config): add clip_style enum mapping to 5 duration params"
```

### Task 6: Remove deprecated migration code

**Files:**
- Modify: `src/immich_memories/config_models.py`
- Modify: `src/immich_memories/config_models_extra.py`

- [ ] **Step 1: Delete `migrate_old_fields` validator from LLMConfig**

Remove the `@model_validator` that handles `ollama_url`, `openai_api_key`, etc.

- [ ] **Step 2: Delete `use_yamnet` alias from AudioContentConfig**

Remove the `Field(alias=...)` and any alias handling.

- [ ] **Step 3: Run `make check` to verify nothing breaks**

Run: `make check`
Expected: PASS (no code uses the old field names)

- [ ] **Step 4: Commit**

```bash
git add src/immich_memories/config_models.py src/immich_memories/config_models_extra.py
git commit -m "refactor(config): remove deprecated field migrations and aliases"
```

### Task 7: Add `config check` CLI command

**Files:**
- Modify: `src/immich_memories/cli/config_cmd.py`
- Test: `tests/test_cli.py` (or relevant CLI test file)

- [ ] **Step 1: Write failing test**

```python
def test_config_check_valid(tmp_path):
    """config check on a valid config should exit 0."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("immich:\n  url: http://localhost\n  api_key: test\n")
    result = runner.invoke(cli, ["config", "check", "--config", str(config_file)])
    assert result.exit_code == 0
```

- [ ] **Step 2: Implement the command**

Add `config check` subcommand that loads config via Pydantic, catches `ValidationError`, and prints human-readable errors pointing users to new field names.

- [ ] **Step 3: Run test, verify pass**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(cli): add config check command for validation and migration hints"
```

### Task 8: Update docs for Tier 1/Tier 2 config

**Files:**
- Modify: `docs-site/docs/configuration/config-file.md`
- Create: `docs-site/docs/configuration/advanced.md`

- [ ] **Step 1: Simplify config-file.md to show only Tier 1 options**

The quickstart config example should show only ~15-20 options (immich URL/key, output path/orientation/resolution, LLM, music provider).

- [ ] **Step 2: Create advanced.md with all Tier 2 options**

Document all 90+ advanced options with their defaults, grouped by section.

- [ ] **Step 3: Run `make docs-build` to verify**

- [ ] **Step 4: Commit**

```bash
git commit -m "docs(config): split into Tier 1 quickstart and Tier 2 advanced reference"
```

---

## Chunk 3: VideoAssembler Composition Refactor (Section 1, Part 1)

This is the largest chunk. It's broken into sub-tasks per service extraction.

### Task 9: Extract FFmpegProber service

**Files:**
- Create: `src/immich_memories/processing/ffmpeg_prober.py`
- Modify: `src/immich_memories/processing/assembler_probing.py` (will be deleted after)
- Modify: `src/immich_memories/processing/video_assembler.py`
- Test: `tests/test_ffmpeg_prober.py`

- [ ] **Step 1: Define the VideoProber Protocol**

Create `src/immich_memories/processing/ffmpeg_prober.py`:

```python
from __future__ import annotations
from pathlib import Path
from typing import Protocol

class VideoProber(Protocol):
    """Interface for video file probing capabilities."""
    def probe_resolution(self, path: Path) -> tuple[int, int]: ...
    def probe_framerate(self, path: Path) -> float: ...
    def has_audio_stream(self, path: Path) -> bool: ...
    def probe_duration(self, path: Path) -> float: ...
    def probe_codec(self, path: Path) -> str | None: ...
```

- [ ] **Step 2: Write failing test for FFmpegProber**

```python
def test_ffmpeg_prober_satisfies_protocol():
    """FFmpegProber must satisfy the VideoProber protocol."""
    from immich_memories.processing.ffmpeg_prober import FFmpegProber, VideoProber
    prober: VideoProber = FFmpegProber(settings=mock_settings)  # type: ignore
    assert isinstance(prober, FFmpegProber)
```

- [ ] **Step 3: Move probing methods from AssemblerProbingMixin to FFmpegProber class**

Move all methods from `assembler_probing.py` into the new `FFmpegProber` class. Change `self.settings` references to use the injected `settings` parameter.

- [ ] **Step 4: Update VideoAssembler to use composed FFmpegProber**

In `video_assembler.py`, replace `AssemblerProbingMixin` inheritance with:
```python
self.prober = FFmpegProber(self.settings)
```

Update all `self._probe_*` calls to `self.prober.probe_*`.

- [ ] **Step 5: Update mock patch targets in tests**

Find all tests that patch `AssemblerProbingMixin` methods and update to new paths.

Run: `grep -rn "assembler_probing\|AssemblerProbingMixin" tests/`

- [ ] **Step 6: Run `make check`**

- [ ] **Step 7: Delete `assembler_probing.py` if no remaining references**

- [ ] **Step 8: Commit**

```bash
git commit -m "refactor(processing): extract FFmpegProber from AssemblerProbingMixin"
```

### Task 10: Extract ClipEncoder service

**Files:**
- Create: `src/immich_memories/processing/clip_encoder.py`
- Create: `src/immich_memories/processing/_encoding_filters.py` (if needed for 500-line split)
- Modify: `src/immich_memories/processing/video_assembler.py`
- Delete: `src/immich_memories/processing/assembler_encoding.py`
- Delete: relevant parts of `src/immich_memories/processing/assembler_helpers.py`

- [ ] **Step 1: Write failing test for ClipEncoder**

Test that `ClipEncoder` can be constructed with a `VideoProber` dependency and `AssemblySettings`.

- [ ] **Step 2: Move encoding + filter-building methods to ClipEncoder**

`ClipEncoder` takes `settings` and `prober: VideoProber` in constructor. Methods that build FFmpeg filter chains move here.

- [ ] **Step 3: If combined size > 500 lines, extract `_encoding_filters.py`**

Natural split: filter construction (pure functions) vs encode execution (subprocess calls).

- [ ] **Step 4: Update VideoAssembler**

Replace `AssemblerEncodingMixin` + relevant `AssemblerHelpersMixin` methods with:
```python
self.encoder = ClipEncoder(self.settings, self.prober)
```

- [ ] **Step 5: Update test patch targets, run `make check`**

- [ ] **Step 6: Commit**

```bash
git commit -m "refactor(processing): extract ClipEncoder from encoding/helpers mixins"
```

### Task 11: Extract TransitionRenderer service

**Files:**
- Create: `src/immich_memories/processing/transition_renderer.py`
- Delete: `src/immich_memories/processing/assembler_transitions.py`
- Delete: `src/immich_memories/processing/assembler_transition_render.py`

- [ ] **Step 1-6: Same pattern as Tasks 9-10**

Merge `assembler_transitions.py` + `assembler_transition_render.py` into `TransitionRenderer`. Takes `settings` and `prober: VideoProber`.

- [ ] **Step 7: Commit**

```bash
git commit -m "refactor(processing): extract TransitionRenderer from transition mixins"
```

### Task 12: Extract AudioMixer, TitleInserter, ConcatBuilder, AssemblyStrategy

**Files:** Multiple creates/deletes following the same pattern.

- [ ] **Step 1: AudioMixer** — from `assembler_audio.py`
- [ ] **Step 2: TitleInserter** — from `assembler_titles.py` + `assembler_trip_mixin.py` (absorbs the 77-line trip mixin)
- [ ] **Step 3: ConcatBuilder** — from `assembler_concat.py` + `assembler_batch.py` + `assembler_scalable.py`
- [ ] **Step 4: AssemblyStrategy** — from `assembler_strategies.py`
- [ ] **Step 5: Rewrite VideoAssembler as orchestrator (~200 lines)**

Wire all services together, run the pipeline.

- [ ] **Step 6: Run `make check`, update ARCHITECTURE.md**

- [ ] **Step 7: Commit**

```bash
git commit -m "refactor(processing): complete VideoAssembler composition — 12 mixins → 7 services"
```

### Task 13: Remove mixin mypy suppressions

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Remove all `attr-defined` suppressions for deleted mixin files**

- [ ] **Step 2: Run `make typecheck` — fix any new errors**

- [ ] **Step 3: Commit**

```bash
git commit -m "fix(mypy): remove attr-defined suppressions for eliminated mixin files"
```

---

## Chunk 4: Other Class Hierarchy Decompositions (Section 1, Parts 2-4)

### Task 14: Decompose SmartPipeline (4 mixins → composed services)

Follow the same pattern as Tasks 9-13 for:
- `pipeline_analysis.py` → `ClipAnalyzer`
- `pipeline_refinement.py` → `ClipRefiner`
- `candidate_generation.py` → `CandidateGenerator`
- `pipeline_preview.py` → `PreviewBuilder`

Commit: `refactor(analysis): decompose SmartPipeline — 4 mixins → 4 services`

### Task 15: Decompose ImmichClient (5 mixins → composed services)

- `client_all_assets.py` + `client_asset.py` → `AssetFetcher`
- `client_person.py` → `PersonFetcher`
- `client_album.py` → `AlbumManager`
- `client_search.py` → `MetadataSearcher`

Commit: `refactor(api): decompose ImmichClient — 5 mixins → 4 services`

### Task 16: Decompose TitleScreenGenerator (3 mixins → composed services)

- `rendering_mixin.py` → `TitleRenderer`
- `trip_mixin.py` → `TripMapRenderer`
- `ending_mixin.py` → `EndingRenderer`

Commit: `refactor(titles): decompose TitleScreenGenerator — 3 mixins → 3 services`

### Task 17: Update ARCHITECTURE.md

**Files:**
- Modify: `ARCHITECTURE.md`

Document the new composition pattern: how services are wired, the Protocol contracts, dependency injection approach.

Commit: `docs: update ARCHITECTURE.md for composition pattern`

---

## Chunk 5: mypy Audit + Test Quality (Section 4)

### Task 18: mypy suppression audit

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: List all remaining mypy override blocks**

Run: `grep -c "tool.mypy.overrides" pyproject.toml`

- [ ] **Step 2: Categorize each as KEEP (third-party stubs) or FIX (our code)**

- [ ] **Step 3: Remove fixable suppressions, add types where needed**

Use `TypedDict` for LLM JSON responses, proper return types for dynamic code.

- [ ] **Step 4: Run `make typecheck`, fix errors**

- [ ] **Step 5: Commit**

```bash
git commit -m "fix(mypy): reduce suppressions from N to M — only third-party boundaries remain"
```

### Task 19: Test quality audit — delete trivial tests

**Files:**
- Modify: multiple test files

- [ ] **Step 1: Find trivial tests**

Run: `grep -rn "def test_.*duration\|def test_.*midpoint\|def test_.*contains_time\|def test_.*to_dict" tests/`

Identify tests that test Python arithmetic or dataclass field access.

- [ ] **Step 2: Delete identified trivial tests (30-50)**

- [ ] **Step 3: Run `make test` to verify remaining tests pass**

- [ ] **Step 4: Commit**

```bash
git commit -m "test: remove trivial tests that verify Python arithmetic, not behavior"
```

### Task 20: Test quality audit — add meaningful tests + WHY comments

- [ ] **Step 1: Add edge case tests**

Examples:
- What happens when scene detection returns zero scenes?
- Scoring with a clip that has no audio?
- Assembly with all clips having the same score?
- Trip detection with zero GPS-tagged assets?

- [ ] **Step 2: Add `# WHY:` comments to all existing mocks**

Run: `grep -rn "mock.patch\|@patch\|Mock()" tests/ | head -30` to find all mocks, add WHY comments.

- [ ] **Step 3: Commit**

```bash
git commit -m "test: add behavioral edge case tests, add WHY comments to all mocks"
```

---

## Chunk 6: Integration Tests (Section 2)

### Task 21: Create integration test fixtures via FFmpeg

**Files:**
- Create: `tests/integration/conftest.py`

- [ ] **Step 1: Write fixture generator**

```python
import subprocess
import pytest
from pathlib import Path

@pytest.fixture(scope="session")
def test_clip_720p(tmp_path_factory) -> Path:
    """Generate a 3-second 720p H.264 test clip with AAC audio."""
    out = tmp_path_factory.mktemp("fixtures") / "test_720p.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-c:a", "aac", "-b:a", "64k", "-shortest", str(out),
    ], check=True, capture_output=True)
    return out
```

Similar fixtures for `test_clip_720p_b` (different seed), `test_music_short`.

- [ ] **Step 2: Commit**

```bash
git commit -m "test: add FFmpeg-based integration test fixture generator"
```

### Task 22: Write integration tests

**Files:**
- Create: `tests/integration/test_assembly_integration.py`

- [ ] **Step 1: test_single_clip_assembly**
- [ ] **Step 2: test_crossfade_transition**
- [ ] **Step 3: test_audio_ducking**
- [ ] **Step 4: test_title_screen_pil**

Each test: assemble real clips → ffprobe output → assert properties.

- [ ] **Step 5: Add `make test-integration` target to Makefile**

```makefile
test-integration:  ## Run integration tests (requires FFmpeg)
	uv run pytest tests/integration/ -v -m integration
```

- [ ] **Step 6: Update pytest config to skip integration by default**

In `pyproject.toml`:
```toml
[tool.pytest.ini_options]
markers = ["integration: requires FFmpeg (deselect with '-m not integration')"]
addopts = "-m 'not integration'"
```

- [ ] **Step 7: Commit**

```bash
git commit -m "test: add real FFmpeg integration tests for assembly pipeline"
```

---

## Chunk 7: AI Smell Cleanup (Section 5)

### Task 23: Docstring cull

- [ ] **Step 1: Configure interrogate to exclude private methods**

In `pyproject.toml`, add `-i -e __init__` flags to interrogate config.

- [ ] **Step 2: Delete docstrings that restate function signatures**

Focus on `src/immich_memories/processing/`, `src/immich_memories/analysis/`, `src/immich_memories/titles/`.

- [ ] **Step 3: Run `make docstring-coverage` to verify ≥80% still passes**

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor: remove redundant docstrings that restate function signatures"
```

### Task 24: Re-export shim cleanup

- [ ] **Step 1: Delete `assembly.py` wildcard re-export**

Update all import sites (search: `from immich_memories.processing.assembly import`).

- [ ] **Step 2: Split `mixer.py` — keep implementation, remove re-exports**

- [ ] **Step 3: Run `make check`**

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor: remove wildcard re-export shims, import from source modules"
```

### Task 25: Minor fixes

- [ ] **Step 1: Fix `storage_secret`** — generate randomly, store in config dir
- [ ] **Step 2: Fix `_kill_port_holders`** — gate behind `IMMICH_MEMORIES_KILL_PORT=1` env var
- [ ] **Step 3: Fix `_compute_spread_km`** — O(n) bounding box approximation
- [ ] **Step 4: Delete "extracted from X to stay within" comments**

Run: `grep -rn "to stay within" src/`

- [ ] **Step 5: Comment audit** — delete what-comments, keep why-comments

- [ ] **Step 6: Commit**

```bash
git commit -m "refactor: fix security, performance, and AI smell issues"
```

### Task 26: Final verification

- [ ] **Step 1: Run `make ci`**
- [ ] **Step 2: Run `make critique`** — should pass clean
- [ ] **Step 3: Run `make test-integration`** — all integration tests pass
- [ ] **Step 4: Verify mypy suppression count**

Run: `grep -c "tool.mypy.overrides" pyproject.toml`
Expected: ≤14

- [ ] **Step 5: Final commit if any remaining changes**

```bash
git commit -m "chore: final cleanup pass — all quality gates green"
```

---

## Execution Summary

| Chunk | Tasks | Est. Hours |
|-------|-------|-----------|
| 1. CLAUDE.md hardening | 1-4 | 2 |
| 2. Config simplification | 5-8 | 4-6 |
| 3. VideoAssembler composition | 9-13 | 12-16 |
| 4. Other hierarchy decomposition | 14-17 | 12-16 |
| 5. mypy + test quality | 18-20 | 6-8 |
| 6. Integration tests | 21-22 | 4-6 |
| 7. AI smell cleanup | 23-26 | 3-4 |
| **Total** | **26 tasks** | **43-58** |

Each chunk is a separate PR. Chunks 3-4 can be further split into per-class PRs.

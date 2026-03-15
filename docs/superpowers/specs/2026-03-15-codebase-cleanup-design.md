# Codebase Cleanup: From AI Scaffolding to Maintainable Software

## Context

A critical audit of the codebase identified structural and design issues that undermine long-term maintainability and credibility. The project has strong automated quality gates (15+ CI checks, 1058 tests, pre-commit hooks) but the _design_ underneath has problems that automated tools can't catch:

- Mechanical file splits driven by a 500-line rule, not cohesion
- Mixin-based class hierarchies with no type contracts (12 mixins for VideoAssembler)
- Zero integration tests that run actual FFmpeg
- 113 config options overwhelming for users
- AI-generated code smells (verbose docstrings, re-export shims, trivial tests)
- A hardcoded NiceGUI `storage_secret`

**Goal:** Make the codebase defensible against a skeptical senior engineer's review, and maintainable for years. Not cosmetic — structural.

**Branch:** Work starts from `feat/globe-animation-privacy-mode` which already has cognitive complexity reduction (84→27), diff-cover, jscpd, import-linter, and complexipy gates.

---

## Section 1: VideoAssembler Decomposition

### Problem

`VideoAssembler` inherits from 12 mixins totaling ~4300 lines. Every mixin freely accesses `self.settings`, `self._face_cache`, and calls methods from other mixins. No Protocols, no interfaces, no type contracts. mypy has `attr-defined` disabled for all mixin files because it can't verify cross-mixin attribute access.

`assembler_trip_mixin.py` (77 lines) exists solely because `assembler_titles.py` was at 576 lines and needed to be under 500.

### Fix

Replace implicit mixin inheritance with explicit composition using Protocol contracts.

**Before:**
```python
class VideoAssembler(
    AssemblerHelpersMixin,
    AssemblerProbingMixin,
    AssemblerEncodingMixin,
    AssemblerTransitionMixin,
    AssemblerTransitionRenderMixin,
    AssemblerAudioMixin,
    AssemblerConcatMixin,
    AssemblerStrategyMixin,
    AssemblerScalableMixin,
    AssemblerBatchMixin,
    AssemblerTitleMixin,  # inherits AssemblerTripMixin
):
```

**After:**
```python
class VideoAssembler:
    def __init__(self, settings: AssemblySettings, ...):
        self.prober = FFmpegProber(settings)
        self.encoder = ClipEncoder(settings, self.prober)
        self.transitions = TransitionRenderer(settings, self.prober)
        self.audio = AudioMixer(settings)
        self.titles = TitleInserter(settings)
        self.concat = ConcatBuilder(settings, self.prober)
        self.strategy = AssemblyStrategy(settings)
```

Each extracted service:
- Defines a `Protocol` for its dependencies (what it needs from others)
- Receives dependencies via constructor injection
- Is independently testable without mocking the entire assembler
- Stays under 500 lines naturally because it's a real cohesive unit

**Grouping rationale:**

| Service | Source mixins | Cohesion principle |
|---------|-------------|-------------------|
| `FFmpegProber` | probing | "Ask questions about video files" |
| `ClipEncoder` | encoding, helpers (filter building) | "Turn a clip spec into an encoded file" |
| `TransitionRenderer` | transitions, transition_render | "Render transitions between clips" |
| `AudioMixer` | audio | "Mix music with video audio" |
| `TitleInserter` | titles, trip_mixin | "Generate and insert title/divider clips" |
| `ConcatBuilder` | concat, batch, scalable | "Concatenate clips into final output" |
| `AssemblyStrategy` | strategies | "Decide assembly approach based on clip count" |

`VideoAssembler` becomes a ~200-line orchestrator that wires services together and runs the pipeline.

### Example Protocol contract

```python
from typing import Protocol

class VideoProber(Protocol):
    """What services need from ffprobe capabilities."""
    def probe_resolution(self, path: Path) -> tuple[int, int]: ...
    def probe_framerate(self, path: Path) -> float: ...
    def has_audio_stream(self, path: Path) -> bool: ...
    def probe_duration(self, path: Path) -> float: ...

class ClipEncoder:
    """Encodes individual clips with filters applied."""
    def __init__(self, settings: AssemblySettings, prober: VideoProber) -> None:
        self.settings = settings
        self.prober = prober
    # ... encoding methods
```

Runtime `Protocol` (structural subtyping) — no need to inherit, just satisfy the interface. This lets mypy verify the contracts without runtime overhead.

### Same treatment for other mixin hierarchies

**SmartPipeline** (~2500 lines across 5 files):

| Service | Source | Cohesion |
|---------|--------|----------|
| `ClipAnalyzer` | pipeline_analysis | "Score and analyze individual clips" |
| `ClipRefiner` | pipeline_refinement | "Select and refine the best clips" |
| `CandidateGenerator` | candidate_generation | "Generate clip candidates from raw video" |
| `PreviewBuilder` | preview_mixin | "Generate clip preview thumbnails" |

**ImmichClient** (~1300 lines across 5 files):

| Service | Source | Cohesion |
|---------|--------|----------|
| `AssetFetcher` | client_all_assets, client_asset | "Fetch and download assets" |
| `PersonFetcher` | client_person | "Fetch person/face data" |
| `AlbumManager` | client_album | "Create/manage albums + upload" |
| `MetadataSearcher` | client_search | "Search and filter by metadata" |

**TitleScreenGenerator** (~1050 lines across 3 files):

| Service | Source | Cohesion |
|---------|--------|----------|
| `TitleRenderer` | rendering mixin | "Render title frames to video" |
| `TripMapRenderer` | trip_map_mixin | "Render map-based title frames" |
| `EndingRenderer` | ending mixin | "Render ending/credits screens" |

**Smaller mixin hierarchies (OUT OF SCOPE for this cleanup):**

The following also use mixins but are smaller and less entangled. They can be refactored in a follow-up:
- `cache/database_migrations.py` + `database_queries.py`
- `tracking/run_queries.py` + `active_jobs_mixin.py`
- `analysis/unified_analyzer.py` with `SegmentScoringMixin` + `CandidateGenerationMixin`
- `titles/renderer_taichi.py` with `TaichiParticlesMixin` + `TaichiTextMixin`
- `titles/renderer_pil.py` with `TextRenderingMixin`

Their mypy `attr-defined` suppressions will remain until the follow-up. This adjusts the Section 4 mypy target to ~12-14 overrides (not 8-10).

### Size risk: ClipEncoder

`assembler_helpers.py` (484 lines) + `assembler_encoding.py` (488 lines) combined is ~970 lines. After removing `self` state boilerplate and extracting filter building as a standalone utility module (`_encoding_filters.py`), `ClipEncoder` should fit in ~400 lines with filters in ~300 lines. If it doesn't, the split is along a natural boundary (filter construction vs encode execution).

### Mock patch targets

Existing tests that patch mixin methods (e.g., `patch("immich_memories.processing.assembler_probing.AssemblerProbingMixin._probe_framerate")`) will break when methods move to composed services. Before starting, audit all `unittest.mock.patch` target strings that reference mixin modules and catalog them. Update patch targets as part of the refactor, not as a separate pass.

### Rollback strategy

- All work on a dedicated branch (`refactor/composition`)
- Each section is a separate PR with all tests passing
- Intermediate merge-worthy states: Section 1 can be merged per-class (VideoAssembler first, then SmartPipeline, etc.)
- If time runs out, any merged PR is an improvement — the refactor is not all-or-nothing

### Verification

- All existing tests pass (public API import paths unchanged)
- Mock patch targets updated to new module paths
- mypy `attr-defined` suppressions removed for refactored classes
- Each service is independently importable and testable
- `import-linter` rules updated for new module structure
- `ARCHITECTURE.md` updated to reflect composition pattern
- `make ci` passes

### Estimated effort: 24-32 hours (VideoAssembler 12-16h, SmartPipeline 6-8h, ImmichClient 3-4h, TitleScreenGenerator 3-4h)

---

## Section 2: Real Integration Tests

### Problem

1058 tests, zero run actual FFmpeg. Every subprocess call is mocked. The test suite proves code doesn't crash when mocks return expected values, not that the video pipeline actually works.

### Fix

A small integration test suite that exercises real FFmpeg on tiny test clips.

**Test fixtures** (generated via FFmpeg during test setup, not committed):

Fixtures are created by a `conftest.py` helper that generates them once per test session via FFmpeg (testsrc2 + sine audio). This avoids committing binary files and guarantees the fixtures match the test requirements.

- `test_clip_720p.mp4` — 3-second 720p H.264 clip with AAC audio (testsrc2 + sine)
- `test_clip_720p_b.mp4` — different 3-second clip (different testsrc2 seed)
- `test_clip_hdr.mp4` — 3-second 720p HEVC 10-bit with BT.2020/HLG metadata (for HDR passthrough test)
- `test_music_short.mp3` — 5-second sine wave encoded as MP3

**Integration tests** (in `tests/integration/`):

| Test | What it verifies | Why mocks can't catch this |
|------|-----------------|--------------------------|
| `test_single_clip_assembly` | Assemble one clip → valid output (ffprobe: has video, has audio, duration > 0) | FFmpeg flag compatibility, codec selection |
| `test_crossfade_transition` | Two clips with crossfade → correct output duration, no corruption | The encoder non-determinism issue from the 9-attempt saga |
| `test_audio_ducking` | Clip + music → output has mixed audio track | Filter graph wiring for audio streams |
| `test_title_screen_pil` | PIL renderer → valid video segment | Font rendering, frame encoding |
| `test_hdr_passthrough` | HDR metadata preserved in output | Color primary detection and passthrough |

**Infrastructure:**
- Marked `@pytest.mark.integration`
- Skipped by default in `make test` (add `-m "not integration"`)
- Run via `make test-integration`
- CI runs them in a separate job that has FFmpeg installed
- Each test creates output in `tmp_path`, ffprobes the result, asserts on properties
- No network access needed — all fixtures are local

### Verification

- `make test` still fast (skips integration)
- `make test-integration` runs the 5 tests, all pass
- CI job `test-integration` passes on Linux runner with FFmpeg

### Estimated effort: 4-6 hours

---

## Section 3: Config Simplification

### Problem

113 options across 15 Pydantic models. 5 interrelated clip duration parameters. A `migrate_old_fields` validator. A `use_yamnet` → `use_panns` alias. First-time users face a wall of options.

### Fix

Two-tier config: simple surface for users, full power underneath.

**Tier 1 — User-facing config (~15-20 options):**

What `immich-memories init` generates and what the quickstart docs show:

```yaml
immich:
  url: "https://immich.example.com"
  api_key: "your-key"

output:
  path: ~/memories
  orientation: landscape
  resolution: 1080p

llm:
  base_url: "http://localhost:11434/v1"
  model: "qwen2.5-vl"

music:
  provider: ace-step
  url: "http://localhost:8000"
```

**Tier 2 — Advanced (stays in Pydantic models, not in default config):**

All 90+ remaining options keep their current defaults. Power users add them to `config.yaml` and Pydantic picks them up. Documented on a dedicated "Advanced Configuration" docs page.

**Clip duration simplification:**

Replace 5 interrelated params (`optimal_clip_duration`, `max_optimal_duration`, `target_extraction_ratio`, `max_segment_duration`, `min_segment_duration`) with:

```yaml
output:
  clip_style: balanced  # balanced | long-cuts | fast-cuts
```

Each style sets all 5 internally. Power users can still override individual values in Tier 2.

**Clip style parameter mapping:**

| Style | `optimal_clip_duration` | `max_optimal_duration` | `target_extraction_ratio` | `max_segment_duration` | `min_segment_duration` |
|-------|------------------------|----------------------|--------------------------|----------------------|----------------------|
| `fast-cuts` | 3.0 | 6.0 | 0.3 | 8.0 | 1.5 |
| `balanced` | 5.0 | 10.0 | 0.4 | 15.0 | 2.0 |
| `long-cuts` | 8.0 | 15.0 | 0.5 | 25.0 | 3.0 |

Individual overrides still work — if a user sets `clip_style: balanced` AND `optimal_clip_duration: 7.0`, the explicit value wins.

**Cleanup:**
- Delete `migrate_old_fields` validator — ship migration note in CHANGELOG. Users with old field names (`ollama_url`, `openai_api_key`) will get a clear Pydantic validation error pointing them to the new field names.
- Delete `use_yamnet` alias — the rename to `use_panns` is done. Same: clear error message on startup if old field name is present.
- Update `immich-memories init` to generate Tier 1 only
- Move advanced docs to `docs-site/docs/configuration/advanced.md`
- Add a `immich-memories config check` command that validates config and warns about deprecated field names (soft migration path)

### Verification

- `immich-memories init` generates a clean, short config
- `immich-memories config check` on an old config shows clear warnings
- Pydantic accepts all Tier 2 fields when explicitly added
- Docs quickstart shows only Tier 1
- `make ci` passes

### Estimated effort: 4-6 hours

---

## Section 4: mypy Suppression Audit + Test Quality Pass

### Problem

24 mypy override blocks — some legitimate (no stubs for cv2, taichi, torch), some papering over the mixin pattern. Tests include trivial ones that inflate count without testing behavior.

### Fix

**mypy pass (after Section 1 lands):**

- **Remove:** All `attr-defined` suppressions from former mixin files (root cause eliminated by composition)
- **Keep:** Suppressions for third-party libs with genuinely missing stubs (cv2, taichi, torch, panns_inference, nicegui internals)
- **Fix:** Where possible, add proper types (e.g., `TypedDict` for LLM JSON responses)
- **Target:** From 24 overrides to ~12-14 (third-party boundaries + smaller mixin hierarchies deferred to follow-up)

**Test quality pass:**

- **Delete:** Tests that verify dataclass field access, property getters, or Python arithmetic (e.g., `test_duration` that checks `end - start`)
- **Replace with:** Behavioral tests covering real edge cases (zero scenes from detection, clip with no audio, scoring with all-equal inputs)
- **Audit mock depth:** Flag tests mocking >3 boundaries. If a test needs 4+ mocks, the code under test has too many dependencies — fix the code, not the test
- **Add `# WHY:` comments** to all remaining mocks explaining what external boundary they replace

**Count target:** Not about hitting a number. Delete 30-50 trivial tests, add 20-30 meaningful ones. Net count may drop slightly — that's fine.

### Verification

- `make typecheck` passes with fewer suppressions
- No test tests Python arithmetic
- Every mock has a `# WHY:` comment
- `make ci` passes

### Estimated effort: 6-8 hours (mypy 2-3h after Section 1, tests 4-5h)

---

## Section 5: AI Smell Cleanup

### Problem

Verbose docstrings on trivial functions, re-export shims with one consumer, comments that describe what the next line does. These signal "AI-generated, never edited" to any experienced reader.

### Fix

**Sweep 1: Docstring cull**

Rule: If the docstring restates the function signature, delete it.

```python
# DELETE:
def _parse_ffmpeg_time(time_str: str) -> float:
    """Parse FFmpeg time string (HH:MM:SS.ms) to seconds."""

# KEEP:
def _compute_spread_km(assets: list[Asset]) -> float:
    """Max pairwise distance across all GPS-tagged assets.

    O(n²) — only called per-trip, not per-asset.
    """
```

Configure `interrogate` to exclude private methods and `__init__` (`-e __init__ -i` flags). Public API docstrings remain mandatory at ≥80%.

**Sweep 2: Re-export shim cleanup**

- Delete `assembly.py` (7 lines of wildcard re-exports, 1 consumer) — update import site
- Split `mixer.py` (hybrid implementation + re-exports) — implementation stays, re-exports go
- Keep `config.py` re-exports — legitimate public API facade with multiple consumers

**Sweep 3: Comment audit**

- Delete comments that describe what the next line does
- Keep comments that explain why
- Delete "extracted from X to stay within 500-line limit" comments — if the split isn't natural, Section 1 already fixed the design

**Sweep 4: Minor fixes**

- `storage_secret` in `app.py:329` → generate randomly on first run, store in config dir
- `_kill_port_holders` → gate behind `IMMICH_MEMORIES_KILL_PORT=1` env var (set in Docker entrypoint, off by default). In dev mode, print a warning and ask the user to kill the process manually or set the env var.
- `_compute_spread_km` → O(n²) → O(n) using bounding box max-distance approximation (max lat/lon spread via haversine on the 4 extreme points). Acceptable accuracy tradeoff: bounding box overestimates by up to ~41% vs true max pairwise, but the function is used for "local vs cross-country" classification where precision doesn't matter

### Verification

- `interrogate ≥80%` still passes (with updated config)
- No wildcard re-export shims remain (except config.py)
- `grep -r "to stay within" src/` returns zero results
- `make ci` passes

### Estimated effort: 3-4 hours

---

## Section 6: CLAUDE.md Hardening + Self-Critique Protocol

### Problem

Current CLAUDE.md prevents some problems (TDD, 500-line limit, conventional commits) but doesn't prevent the AI-specific smells that this cleanup addresses. Worse, it actively recommends the patterns we're removing — lines 129-143 suggest mixins and re-export shims as splitting strategies. Without updated rules, the next AI session re-introduces the same patterns.

### Fix

**New CLAUDE.md section: "Anti-Patterns (DO NOT)"**

```markdown
### Docstrings
- Do NOT write docstrings that restate the function signature
- Do NOT docstring private functions unless behavior is non-obvious
- Public API functions MUST have docstrings explaining behavior, not signature

### Abstractions
- Do NOT create re-export shims for a single consumer — import from the source
- Do NOT split files purely for line count — splits must follow cohesion boundaries
- Do NOT use mixins — use composition with Protocol contracts
- If you need state from another module, inject it via constructor

### Tests
- Do NOT test dataclass field access, property getters, or Python arithmetic
- Do NOT mock more than 3 boundaries in a single test
- Every mock MUST have a # WHY: comment explaining what boundary it replaces
- Integration tests (make test-integration) must exist for any FFmpeg pipeline change

### Comments
- Do NOT write comments that describe what the next line does
- DO write comments that explain WHY something non-obvious is done

### Config
- New config options MUST have a sane default
- User-facing options go in Tier 1 (~20 options). Everything else is Tier 2
- Do NOT add migration/compat shims for renamed fields
```

**New CLAUDE.md section: "Self-Critique Protocol"**

```markdown
## Self-Critique Protocol

After completing any major feature or refactor (>5 files changed), audit your own work:

1. `make ci` passes (baseline)
2. Skeptic review:
   - Would a senior engineer mass-reject any of these patterns?
   - Are there docstrings that add no information?
   - Are there abstractions that serve no consumer?
   - Are tests testing behavior or testing mocks?
   - Are file splits following cohesion or line count?
3. AI hallmark check:
   - Unnecessary type annotations on obvious code?
   - Over-structured solutions (registry for 3 items)?
   - Verbose module-level docstrings with ASCII art?
   - Helper functions called from exactly one place?
4. `make critique` — automated checks for common smells
```

**New Makefile target: `critique`**

```makefile
critique:  ## Run self-critique checks for AI code smells
	@echo "=== AI Smell Audit ==="
	@echo "Checking for mechanical split comments..."
	@! grep -rn "to stay within" src/ || (echo "FAIL: Fix these splits" && exit 1)
	@echo "Checking for single-test files..."
	@grep -rc "def test_" tests/ | awk -F: '$$2<=1 {found=1; print "LOW-VALUE: " $$1} END {if(!found) print "OK"}'
	@echo "Checking for wildcard re-exports..."
	@! grep -rn "from .* import \*" src/ --include="*.py" | grep -v __init__ || (echo "WARN: Wildcard re-exports found" && exit 1)
	@echo "Self-critique complete."
```

**Also: Remove contradicting CLAUDE.md sections**

The existing "Splitting Large Files" section (lines ~129-143) recommends mixins and re-export shims. Replace it with:

```markdown
## Splitting Large Files

When a file exceeds 500 lines, split along **cohesion boundaries**, not arbitrary line counts:
- Extract a service class with a Protocol contract for its dependencies
- The new module should be independently testable and importable
- Do NOT create mixins — use composition
- Do NOT create re-export shims — update import sites directly
- If you can't find a natural split, the file may genuinely be one cohesive unit.
  In that case, look for helper utilities to extract, not arbitrary method groups.
```

### Verification

- `make critique` passes after cleanup
- CLAUDE.md anti-patterns section exists
- Old "Splitting Large Files" section replaced (no mixin/re-export recommendations)
- `ARCHITECTURE.md` updated to describe composition pattern
- New AI sessions follow the updated rules

### Estimated effort: 2 hours

---

## Execution Order

Sections have dependencies:

```
Section 6 (CLAUDE.md)          — do FIRST (but use "no NEW mixins" during refactor)
Section 3 (Config)             — design clip_style mapping before Section 1 starts
Section 1 (Composition)        — biggest structural change
Section 4 (mypy + tests)       — depends on Section 1 (mixin suppressions go away)
Section 2 (Integration tests)  — AFTER Section 1 lands (avoids merge conflicts)
Section 5 (AI smells)          — do LAST, final polish
```

**Recommended order:** 6 → 3 → 1 → 4 → 2 → 5

**Why this order:**
- Section 6 first: establishes rules, but phrases mixin ban as "no NEW mixins" until refactor completes
- Section 3 before Section 1: the `clip_style` abstraction affects `AssemblySettings` — design it first so new composed services accept the simplified config from the start
- Section 2 after Section 1: integration tests call `VideoAssembler.assemble()` — if written during the composition refactor, merge conflicts are guaranteed
- Section 5 last: pure polish, no structural dependencies

**Each section is a separate PR.** Each PR must have all tests passing before merge. Section 1 can be split into sub-PRs per class hierarchy (VideoAssembler → SmartPipeline → ImmichClient → TitleScreenGenerator).

## Total Estimated Effort

| Section | Hours |
|---------|-------|
| 1. Composition refactor (4 class hierarchies) | 24-32 |
| 2. Integration tests | 4-6 |
| 3. Config simplification | 4-6 |
| 4. mypy + test quality | 6-8 |
| 5. AI smell cleanup | 3-4 |
| 6. CLAUDE.md hardening | 2 |
| **Total** | **43-58 hours** |

## Success Criteria

A skeptical senior engineer reviewing the codebase should NOT be able to say:
- "These are just arbitrary file splits" (Section 1 fixes)
- "These tests test nothing" (Sections 2, 4 fix)
- "This config is overwhelming" (Section 3 fixes)
- "This was obviously AI-generated" (Section 5 fixes)
- "The type system is bypassed" (Section 4 fixes)
- "The same smells will come back next session" (Section 6 fixes)

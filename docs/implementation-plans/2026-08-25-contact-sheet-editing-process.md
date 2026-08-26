# Contact-Sheet Editing Process Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current score-, quota-, and absorber-driven selector with a visual editing
process that sees the complete eligible corpus, banks every decision, minimizes model calls, and
produces one conserved chronological cut across every public surface.

**Architecture:** A long-lived feature trunk holds the replacement while `main` continues to ship
the current selector. Inside that trunk, one core flow owns source eligibility, a reusable visual
atlas, Insight, Cull/record shots, Selects, Structure, projection, Fine Cut, intended duration, and
the trace. Every decision-bearing pass receives the exact contact-sheet JPEG bytes recorded in its
provenance. Rendering consumes the final ordered membership and may not change it.

**Tech Stack:** Python 3.12/3.13, frozen Pydantic models, Pillow, FFmpeg/ffprobe, provider-neutral
multimodal LLM requests, SQLite-backed judgement cache, pytest through Makefile targets, NiceGUI,
Click, GitHub Actions.

**Spec:** `docs/designs/2026-08-25-contact-sheet-editing-process.md`

## Global Constraints

- Run `make dev` before any other Make target in a fresh worktree.
- Use one RED → GREEN → REFACTOR cycle at a time. Tests exercise public behavior; no more than
  three mocked boundaries per test, and every mock carries a `# WHY:` comment.
- Run focused tests with `make test-one T="..."`, then `make test`, `make critique`, and `make ci`
  before every commit.
- Do not tune product rules to June. June, April, and August are private acceptance corpora; the
  synthetic suite must prove the same decision shapes under unrelated topics and label swaps.
- Contact sheets are the model input, not merely a diagnostic. Hash, write, attach, cache, and trace
  the same encoded JPEG bytes.
- A video filmstrip is composed locally inside one tile. It costs no additional model request.
- A logical pass is not necessarily a model request. Reuse one visual atlas, pack complete groups,
  bank answers, and fuse only independent namespaces over identical evidence.
- Start at one sheet per request. Increase that limit only after a provider probe proves tile
  conservation and decision quality. Never infer image limits from a provider name.
- Reject-only actions fail open. Refusal, truncation, unknown IDs, incomplete partitions, or request
  failure cannot create an unnamed loss. Any mechanical preview carries `!!` and is invalid for an
  owner verdict.
- Preserve chronology in every contract. Sets may be used for validation, never as the stored order.
- Keep all private contact sheets, traces, and media outside Git and GitHub.
- Rewrite the existing Structure modules in place. PR #768 is evidence only; do not cherry-pick its
  implementation wholesale.
- Do not remove legacy machinery until the replacement owns the same public surface and all real
  gates pass.

## Branch and PR Strategy

```text
origin/main
  └─ feature/764-editorial-selection          # long-lived replacement trunk
       ├─ ci/764-feature-trunk
       ├─ feat/764-visual-foundation
       ├─ feat/764-insight-cull
       ├─ feat/764-selects
       ├─ refactor/764-structure-projection
       ├─ feat/764-fine-cut
       └─ refactor/764-editorial-cutover
```

- Bootstrap `feature/764-editorial-selection` from current `origin/main` with only the approved
  design, this plan, and CI support for feature-trunk PRs. Open an umbrella draft PR from the feature
  trunk to `main`; it stays unmergeable until final owner acceptance.
- Every production slice branches from the latest feature trunk and targets its PR back to
  `feature/764-editorial-selection`. Use squash merge for slice PRs.
- Short-lived slice branches rebase on the current feature trunk before their final gate. The
  published feature trunk itself merges `origin/main` forward and is never rebased beneath active
  slices.
- Each slice PR uses a conventional title, includes `Refs #764`, and stays below roughly 300 changed
  lines when cohesion permits. Do not use `Fixes #764` until the final cutover PR.
- A slice merges into the feature trunk only after focused tests, `make critique`, `make ci`, its
  warning-free trace, and the owner-visible contact-sheet gate described below.
- The final feature-trunk → `main` PR contains no new editorial behavior. It only syncs `main`,
  resolves integration drift, reruns all gates, and records the final owner verdict.

## Shared Public Contracts

Create immutable records in `src/immich_memories/analysis/editorial_contracts.py`. Add records only
when their first consumer lands; the complete target contract is:

```python
@dataclass(frozen=True)
class DecisionProvenance:
    pass_name: str
    pass_version: str
    schema_version: str
    model_identity: str
    input_ids: tuple[str, ...]
    sheet_hashes: tuple[str, ...]
    request_key: str
    cache_hit: bool

@dataclass(frozen=True)
class InsightEvidence:
    observation: str
    episode_ids: tuple[str, ...]
    asset_ids: tuple[str, ...]

@dataclass(frozen=True)
class PeriodInsight:
    thesis: str | None
    evidence: tuple[InsightEvidence, ...]
    tensions: tuple[str, ...]
    recurring_threads: tuple[str, ...]
    unavailable_reason: str | None
    revision: int
    provenance: DecisionProvenance

@dataclass(frozen=True)
class EditorialCandidate:
    asset_id: str
    taken_at: datetime
    media_kind: Literal["photo", "video", "live_photo"]
    favourite: bool
    source: Asset
    proposed_segment: tuple[float, float] | None
    shippable_duration: float
    grounded_annotations: tuple[str, ...]

@dataclass(frozen=True)
class SelectedVisual:
    asset_id: str
    moment_id: str
    episode_id: str
    intended_segment: tuple[float, float] | None
    intended_duration: float
    render_options: tuple[Literal["still", "motion"], ...]

@dataclass(frozen=True)
class EditorialSelectionResult:
    selected: tuple[SelectedVisual, ...]
    insight: PeriodInsight | None
    trace: Trace
    warnings: tuple[str, ...]
    conservation: ConservationCheck
```

The only final public orchestration API is:

```python
def prepare_editorial_source(
    request: EditorialSelectionRequest,
    dependencies: EditorialDependencies,
) -> PreparedEditorialSource: ...

def select_editorial_cut(
    prepared: PreparedEditorialSource,
    dependencies: EditorialDependencies,
) -> EditorialSelectionResult: ...

def run_editorial_selection(
    request: EditorialSelectionRequest,
    dependencies: EditorialDependencies,
) -> EditorialSelectionResult:
    return select_editorial_cut(prepare_editorial_source(request, dependencies), dependencies)
```

The split form exists only so UI owner exclusions can be recorded between retrieval and Pass 0. It
is still one flow: the UI cannot select, score, suppress, or rebuild membership itself.

---

## Task 0: Bootstrap CI for the Feature Trunk

**Branch:** `ci/764-feature-trunk`, based on `feature/764-editorial-selection`

**Files:**

- Modify: `.github/workflows/ci.yml:4-5, 229-263`
- Modify: `.github/workflows/benchmark.yml:31-43`
- Modify: `Makefile:464-548`

- [ ] Run `make dev`, then capture the current failure mode without changing files:

```bash
make dev
make -n integration-coverage-for-diff DIFF_BASE=origin/feature/764-editorial-selection
```

Expected before implementation: the printed Git diff still contains `origin/main`.

- [ ] Make the diff base injectable once, with `main` as the safe default:

```make
DIFF_BASE ?= origin/main

diff-cover:
	uv run pytest --cov=src/immich_memories --cov-branch --cov-report=xml -q
	uvx diff-cover coverage.xml --compare-branch=$(DIFF_BASE) --fail-under=80
```

Use `$(DIFF_BASE)` in `diff-cover-local`, `integration-coverage-for-diff`, and `diff-cover-ci`,
including line-count and changed-path Git diffs.

- [ ] Allow CI and benchmark pull-request workflows to run when the PR base is the feature trunk:

```yaml
on:
  pull_request:
    branches: [main, feature/764-editorial-selection]
```

- [ ] Pass the actual PR base to every diff-aware target and report command:

```yaml
env:
  DIFF_BASE: origin/${{ github.base_ref }}
run: |
  make integration-coverage-for-diff
  make diff-cover-ci
```

- [ ] Verify the resolved commands, then the repository gate:

```bash
make -n integration-coverage-for-diff DIFF_BASE=origin/feature/764-editorial-selection
make ci
```

Expected: no hard-coded `origin/main` appears in the diff commands when `DIFF_BASE` is supplied;
all CI checks pass.

- [ ] Commit and merge this bootstrap into the feature trunk:

```bash
git add Makefile .github/workflows/ci.yml .github/workflows/benchmark.yml
git commit -m "ci(selection): support feature-trunk pull requests (#764)"
```

**Gate:** Open a deliberately empty test PR against the feature trunk or inspect this PR's
`pull_request` run. Quality, test matrix, commitlint, and diff coverage must all start and compare
against `origin/feature/764-editorial-selection`.

---

## Task 1: Establish Contracts, Chronological Trace, and Conservation

**Branch:** `feat/764-visual-foundation`

**Files:**

- Create: `src/immich_memories/analysis/editorial_contracts.py`
- Modify: `src/immich_memories/analysis/selection_trace.py:34-116, 172-269`
- Create: `tests/test_editorial_contracts.py`
- Create: `tests/test_editorial_trace.py`
- Modify: `tests/test_selection_accountability.py`

- [ ] RED: add one public behavior test proving chronological IDs survive and every input has one
  fate:

```python
def test_editorial_pass_keeps_chronology_and_conserves_every_input() -> None:
    trace = Trace()
    trace.record_editorial_pass(
        PassTrace(
            name="cull",
            input_ids=("late", "early", "middle"),
            kept_ids=("late", "middle"),
            rejected=(TraceDecision("early", "unusable exposure"),),
            unresolved=(),
            duration_before=12.0,
            duration_after=8.0,
            provenance=_provenance(("late", "early", "middle")),
        )
    )

    payload = trace.as_dict()
    assert payload["editorial_passes"][0]["kept_ids"] == ["late", "middle"]
    assert payload["editorial_passes"][0]["conservation"]["valid"] is True
```

Run `make test-one T="tests/test_editorial_trace.py"`; expect failure because the editorial trace
API does not exist.

- [ ] GREEN: add frozen tuple-based `DecisionProvenance`, `TraceDecision`, `PassTrace`,
  `RequestTrace`, and `ConservationCheck`. Extend the existing `Trace`; do not create a second
  ledger. Preserve the legacy `record()` adapter for the old selector until Task 14.

- [ ] RED/GREEN: add a test where one input is both kept and rejected and one disappears. The pass
  must produce `valid=False`, list duplicate and missing IDs, and add a `!! conservation failure`
  warning.

- [ ] RED/GREEN: assert Markdown emits exactly `showing 12 of N; full list in JSON` when decisions
  are abbreviated, while JSON remains complete.

- [ ] REFACTOR: keep serialization in `selection_trace.py`; keep immutable cross-pass values in
  `editorial_contracts.py`. Avoid a registry or generic event bus.

- [ ] Run:

```bash
make test-one T="tests/test_editorial_contracts.py tests/test_editorial_trace.py tests/test_selection_accountability.py"
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/editorial_contracts.py src/immich_memories/analysis/selection_trace.py tests/test_editorial_contracts.py tests/test_editorial_trace.py tests/test_selection_accountability.py
git commit -m "feat(selection): account for every editorial decision (#764)"
```

---

## Task 2: Build One Reusable Visual Atlas

**Files:**

- Create: `src/immich_memories/analysis/visual_atlas.py`
- Create: `src/immich_memories/analysis/contact_sheets.py`
- Modify: `src/immich_memories/processing/frame_sampling.py:29-136`
- Modify: `src/immich_memories/analysis/moment_reading.py:35-178, 293-359`
- Modify: `src/immich_memories/analysis/thumbnail_prefetch.py:38-128`
- Create: `tests/test_visual_atlas.py`
- Create: `tests/test_contact_sheets.py`
- Modify: `tests/test_frame_extraction.py`
- Modify: `tests/test_moment_reading.py`

- [ ] RED: create generated red/green/blue frames and assert a video becomes one chronological
  filmstrip tile, with no requester involved:

```python
def test_video_tile_is_one_locally_composed_chronological_filmstrip(tmp_path: Path) -> None:
    source = fake_video_source(tmp_path, colors=("red", "green", "blue"))
    atlas = build_visual_atlas((source,), frame_cache_dir=tmp_path / "frames")

    tile = atlas.tile_for(source.asset.id)
    assert tile.kind == "filmstrip"
    assert tile.frame_count == 3
    assert tile.sha256 == sha256(tile.jpeg_bytes).hexdigest()
```

Run `make test-one T="tests/test_visual_atlas.py"`; expect import failure.

- [ ] GREEN: add `AtlasSource`, `AtlasTile`, and `VisualAtlas`. Photos use cached/server preview
  JPEGs. Videos and Live Photo motion options call a new cached segment-aware sampler:

```python
def sample_segment_frames(
    video: Path,
    *,
    start_time: float,
    end_time: float,
    count: int,
    width: int,
    cache_dir: Path | None,
) -> tuple[Path, ...]: ...
```

Its cache identity includes source path metadata, segment bounds, count, width, and a render
version. Move useful `.part`, zero-byte, and short-video fallback behavior from
`scripts/contact_sheet.py:118-182` into this core seam; the core must not import the script.

- [ ] RED/GREEN: prove a changed segment or render version invalidates cached filmstrip frames.

- [ ] RED: assert one encoded page's bytes are identical across hash, disk, request attachment,
  and trace:

```python
page = build_contact_sheets(tiles, scope_id="episode-1", output_dir=tmp_path)[0]
assert page.path.read_bytes() == page.jpeg_bytes
assert sha256(page.jpeg_bytes).hexdigest() == page.sha256
assert page.tile_refs == tuple(TileRef(i + 1, tile.entity_id) for i, tile in enumerate(tiles))
```

- [ ] GREEN: generalize `sheet_layout`, `sheets_of`, and `tile_sheet` from
  `moment_reading.py`. `ContactSheetPage` holds `sheet_id`, path, exact JPEG bytes, SHA-256, ordered
  `TileRef`s, and layout version. Keep the current 120-tile/2100px safety bounds and global tile
  numbering.

- [ ] REFACTOR: adapt `moment_reading.py` to the shared sheet builder without changing its legacy
  behavior. This proves reuse before the new editor depends on it.

- [ ] Run focused tests and FFmpeg coverage:

```bash
make test-one T="tests/test_visual_atlas.py tests/test_contact_sheets.py tests/test_frame_extraction.py tests/test_moment_reading.py"
make test-integration-processing
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/visual_atlas.py src/immich_memories/analysis/contact_sheets.py src/immich_memories/processing/frame_sampling.py src/immich_memories/analysis/moment_reading.py src/immich_memories/analysis/thumbnail_prefetch.py tests/test_visual_atlas.py tests/test_contact_sheets.py tests/test_frame_extraction.py tests/test_moment_reading.py
git commit -m "feat(selection): build a reusable visual atlas (#764)"
```

---

## Task 3: Pack, Attach, Cache, and Trace Visual Requests

**Files:**

- Create: `src/immich_memories/analysis/visual_request_planner.py`
- Create: `src/immich_memories/analysis/editorial_gateway.py`
- Modify: `src/immich_memories/analysis/llm_query.py:138-193, 228-445`
- Modify: `src/immich_memories/cache/judgment_cache.py:40-110`
- Modify: `src/immich_memories/analysis/selection_trace.py`
- Create: `tests/test_visual_request_planner.py`
- Create: `tests/test_editorial_gateway.py`
- Modify: `tests/test_llm_query.py`
- Modify: `tests/test_judgment_cache.py`

- [ ] RED: prove planning conserves complete groups and defaults to one sheet per request:

```python
def test_request_plan_conserves_groups_without_guessing_provider_limits() -> None:
    plans = plan_visual_requests(groups=(group("a"), group("b")), limits=VisionRequestLimits())
    assert tuple(group_id for plan in plans for group_id in plan.group_ids) == ("a", "b")
    assert all(len(plan.pages) == 1 for plan in plans)
```

- [ ] GREEN: add pure `VisionRequestLimits`, `SheetGroup`, `VisualRequestPlan`, and
  `plan_visual_requests()`. Oversized groups get explicit numbered continuations; a group is never
  silently split across unrelated requests.

- [ ] RED/GREEN: pass a page into `EditorialGateway.ask()` and decode the outgoing request payload.
  Assert its image SHA-256 equals the page and request-trace hashes exactly.

- [ ] GREEN: define one provider-neutral seam:

```python
class EditorialGateway(Protocol):
    def ask(self, request: VisualEditorialRequest) -> BankedVisualAnswer: ...
```

`VisualEditorialRequest` contains pass/prompt/schema versions, ordered IDs, exact pages, grounded
annotations, upstream decision versions, and request limits. It returns raw complete text plus
provenance; pass-specific parsers own semantics.

- [ ] RED/GREEN: add `VisualJudgmentIdentity` and `VisualJudgmentCache` without changing the legacy
  text-cache meaning. Independently test invalidation for pixels, page order, annotations, model,
  prompt/schema/render/layout version, and upstream insight version. Exclude API keys.

- [ ] RED/GREEN: make request trace record planned calls, actual calls, cache hits, attached pages,
  tile count, provider/model identity, and every retry. A cache hit records original provenance and
  current reuse.

- [ ] Probe before increasing call packing. Against generated sheets and the configured model,
  compare `image_detail=low` with `auto`/`high`, then one versus multiple pages per request. Require
  exact tile accounting and equivalent decisions. Record the result outside Git. Keep production
  defaults at one JPEG if the probe is inconclusive.

- [ ] Run:

```bash
make test-one T="tests/test_visual_request_planner.py tests/test_editorial_gateway.py tests/test_llm_query.py tests/test_judgment_cache.py tests/test_editorial_trace.py"
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/visual_request_planner.py src/immich_memories/analysis/editorial_gateway.py src/immich_memories/analysis/llm_query.py src/immich_memories/cache/judgment_cache.py src/immich_memories/analysis/selection_trace.py tests/test_visual_request_planner.py tests/test_editorial_gateway.py tests/test_llm_query.py tests/test_judgment_cache.py tests/test_editorial_trace.py
git commit -m "feat(selection): bank visual editorial requests (#764)"
```

**Slice gate:** The attached provider payload, saved sheet, and trace hash are byte-identical. The
trace reports the measured request count and no tile disappears under packing.

---

## Task 4: Prepare the Complete Source-Eligible Corpus

**Branch:** `feat/764-insight-cull`

**Files:**

- Create: `src/immich_memories/analysis/selection_flow.py`
- Modify: `src/immich_memories/analysis/source_filter.py:68-113`
- Modify: `src/immich_memories/analysis/moment_grouping.py:66-138`
- Modify: `src/immich_memories/analysis/source_quality.py`
- Create: `tests/test_selection_source.py`
- Modify: `tests/test_moment_grouping.py`
- Modify: `tests/test_pool_stages_are_on_the_record.py`

- [ ] RED: feed a pregnancy-test image, a screenshot, a short clip, and an owner-excluded asset
  through `prepare_editorial_source()`. Assert only the explicit owner exclusion leaves before
  Pass 0; the other signals are annotations:

```python
def test_only_source_scope_and_owner_exclusions_apply_before_pass_zero() -> None:
    prepared = prepare_editorial_source(request_with_four_assets(), fake_dependencies())
    assert prepared.candidate_ids == ("pregnancy-test", "screenshot", "short-clip")
    assert prepared.excluded_ids == ("owner-excluded",)
    assert prepared.trace.story_of("pregnancy-test").first_pass == "pass-0"
```

- [ ] GREEN: implement `SourceScope`, `EditorialSelectionRequest`, `EditorialDependencies`,
  `PreparedEditorialSource`, `prepare_editorial_source()`, and a normalized `EditorialCandidate`
  adapter for raw `Asset`/`VideoClipInfo`. Eligibility is date/library scope, supported media, and
  explicit hard owner exclusions only.

- [ ] RED/GREEN: expose pure chronological `build_episode_groups()` and `build_moment_groups()`
  over already-eligible candidates. Stable group IDs derive from ordered asset IDs and grouping
  version. Move no winners here.

- [ ] GREEN: retain re-encode, resolution, duration, blur, burst, inferred subject, and similarity
  findings as grounded evidence. Do not call legacy subject quota, thumbnail winner, burst winner,
  photo-vs-motion suppression, or density shortlist in the new flow.

- [ ] Run:

```bash
make test-one T="tests/test_selection_source.py tests/test_moment_grouping.py tests/test_pool_stages_are_on_the_record.py"
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/selection_flow.py src/immich_memories/analysis/source_filter.py src/immich_memories/analysis/moment_grouping.py src/immich_memories/analysis/source_quality.py tests/test_selection_source.py tests/test_moment_grouping.py tests/test_pool_stages_are_on_the_record.py
git commit -m "feat(selection): start from the complete eligible corpus (#764)"
```

---

## Task 5: Read Episodes and Form a Provisional Period Insight

**Files:**

- Create: `src/immich_memories/analysis/period_insight_answer.py`
- Create: `src/immich_memories/analysis/period_insight.py`
- Create: `tests/test_period_insight_answer.py`
- Create: `tests/test_period_insight.py`
- Create: `tests/test_period_insight_generalisation.py`

- [ ] RED: assert strict parsing accepts the final complete balanced object, maps displayed numbers
  to stable IDs, and rejects an auto-closed truncated object.

```python
def test_truncated_episode_answer_cannot_select_representatives() -> None:
    raw = '{"episode_id":"day-1","representative_tiles":[1,2]'
    assert read_episode_answer(raw, episode_id="day-1", tile_map={1: "a", 2: "b"}) is None
```

- [ ] GREEN: implement strict `read_episode_answer()` and `read_period_answer()`. Do not reuse
  `_parse_json_object_text()` from `llm_response_parser.py` because it repairs truncation.

- [ ] RED/GREEN: build chronological episode sheets from every prepared candidate, request short
  `EpisodeReading`s and reasoned representative IDs, then build the period sheet from every episode
  reading and its representatives. A representative decision makes the period wall legible; it
  does not cull the episode.

- [ ] RED/GREEN: if any required visual evidence is unreadable, return `PeriodInsight(thesis=None,
  unavailable_reason=...)`, retain all assets for Cull, and add `!!`. Never invent representative
  IDs mechanically and present them as editorial.

- [ ] RED/GREEN: the generalisation matrix swaps cycling/live-show labels while preserving pixels
  and evidence. The shape of the insight evidence must remain stable; tests must not assert exact
  prose.

- [ ] Add Pass 0 to `selection_flow.py`, bank its answer, and record actual call count. At this
  point the new flow is observation-only and has no public caller.

- [ ] Run:

```bash
make test-one T="tests/test_period_insight_answer.py tests/test_period_insight.py tests/test_period_insight_generalisation.py tests/test_editorial_trace.py"
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/period_insight_answer.py src/immich_memories/analysis/period_insight.py src/immich_memories/analysis/selection_flow.py tests/test_period_insight_answer.py tests/test_period_insight.py tests/test_period_insight_generalisation.py
git commit -m "feat(selection): read the period before making cuts (#764)"
```

---

## Task 6: Fuse Cull and the Record-Shot Lane into Episode Scans

**Files:**

- Create: `src/immich_memories/analysis/cull_answer.py`
- Create: `src/immich_memories/analysis/selection_cull.py`
- Modify: `src/immich_memories/analysis/period_insight.py`
- Modify: `src/immich_memories/analysis/subject_policy.py:28-177, 215-274`
- Modify: `src/immich_memories/analysis/selection_flow.py`
- Create: `tests/test_cull_answer.py`
- Create: `tests/test_selection_cull.py`
- Modify: `tests/test_subject_policy.py`

- [ ] RED: one episode response contains independent `episode_reading`, `record_shots`, and
  `cull_rejects` namespaces. A record/reject collision invalidates only the rejection:

```python
def test_record_mark_wins_a_namespace_collision_without_discarding_siblings() -> None:
    answer = read_episode_scan_answer(raw_collision(), tile_map={1: "test", 2: "blur"})
    assert answer.record_shots[0].asset_id == "test"
    assert answer.cull_rejects == (CullDecision("blur", "unusable motion blur"),)
    assert answer.warnings == ("!! cull reject conflicted with record-shot mark: test",)
```

- [ ] GREEN: version the episode-scan schema and request identity. Ask record-shot function first;
  Cull may reject only clearly unusable non-record items. Subject labels, repetition, relative
  weakness, and thesis relevance are invalid Cull reasons.

- [ ] RED/GREEN: freeze `episode-scan-v3` at one physical page per request with unique pack-local
  tile aliases. Scopes exactly partition that page. A future multi-page request bumps the pass,
  prompt, and schema instead of reinterpreting v3 aliases.

- [ ] RED/GREEN: normalize duplicate snapshots with favourite OR semantics. Preserve a complete,
  aligned, source-owned `LivePhotoRenderingFamily` with a versioned manifest hash and propagate its
  reference to every admitted member. Owner exclusions trim aligned entries. Incomplete,
  contradictory, or cross-moment manifests warn and create no family; never elect a carrier or a
  render mode here.

- [ ] RED/GREEN: refusal, timeout, invalid IDs, and truncated JSON reject nothing. Valid sibling
  namespaces remain banked. More than 75% rejection adds `!! possible over-cull` but does not
  restore by score.

- [ ] RED/GREEN: convert `classify_subject()` to evidence-only use in the new path. Screenshots,
  documents, and the pregnancy-test fixture reach the visual request. Legacy quota functions stay
  callable only for the old selector until Task 13.

- [ ] Add `run_cull()`:

```python
def run_cull(
    episodes: Sequence[EpisodeSheet],
    *,
    requester: EditorialGateway,
    trace: Trace,
) -> CullPassResult: ...
```

Return chronological survivors, independent `RecordShotMark`s, decisions, warnings, and
provenance. Add it after Pass 0 in `selection_flow.py`.

- [ ] Run:

```bash
make test-one T="tests/test_cull_answer.py tests/test_selection_cull.py tests/test_subject_policy.py tests/test_selection_source.py tests/test_editorial_trace.py"
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/cull_answer.py src/immich_memories/analysis/selection_cull.py src/immich_memories/analysis/period_insight.py src/immich_memories/analysis/subject_policy.py src/immich_memories/analysis/selection_flow.py tests/test_cull_answer.py tests/test_selection_cull.py tests/test_subject_policy.py
git commit -m "feat(selection): cull visibly and preserve record shots (#764)"
```

**Slice gate:** The private Pass 0/1 sheet is judgeable and chronological; every source-eligible
asset appears; the pregnancy test is marked as a record shot; refusal/truncation rejects nothing;
the trace reports episode-scan packs rather than one request per asset.

---

## Task 7: Select Representatives with Visual Moment Battles

**Branch:** `feat/764-selects`

**Files:**

- Create: `src/immich_memories/analysis/selects_answer.py`
- Create: `src/immich_memories/analysis/selection_selects.py`
- Modify: `src/immich_memories/analysis/moment_grouping.py`
- Modify: `src/immich_memories/analysis/favourite_law.py`
- Modify: `src/immich_memories/analysis/selection_flow.py`
- Create: `tests/test_selects_answer.py`
- Create: `tests/test_selection_selects.py`
- Modify: `tests/test_favourite_law.py`
- Modify: `tests/test_same_moment.py`

- [ ] RED: prove favorite construction semantics:

```python
def test_one_favourite_wins_its_moment_and_record_shot_stays_separate() -> None:
    result = run_selects((group("plain", "star", "record"),), record_shots=(mark("record"),), requester=never_called(), trace=Trace())
    assert result.selects[0].selected_asset_id == "star"
    assert result.record_shots[0].asset_id == "record"
```

- [ ] GREEN: a single favorite auto-wins. If several favorites collide, build a battle containing
  only those favorites. Favorites outside the winning occasion are not globally immune.

- [ ] RED/GREEN: treat one `rendering_family_id` as one still-or-motion option inside its moment.
  Select exactly one asset for that family/moment and preserve its family reference. A lone exact
  favourite wins; multiple favourites in the family battle only one another. If RECORD and
  favourite annotations land on different family members, they must not silently become two final
  outputs: preserve RECORD as a sidecar and resolve one editorial representative explicitly.
- [ ] RED/GREEN: a selected non-favourite member retains the same family option on merit. Family
  membership never makes its siblings Cull-immune and never depends on the legacy enriched carrier.

- [ ] RED/GREEN: for ordinary groups, pack many complete separated battles per request. The answer
  is exactly one `MomentSelect` (`selected` or `no_peak`) per group plus at most one reason-specific
  alternate. Unknown, duplicate, cross-group, or incomplete decisions leave that group unresolved
  and add `!!`; scalar score never selects a fallback.

- [ ] RED/GREEN: five repeated events in cycling and live-music fixtures produce decisions from
  visible contribution, not a fixed count. Swapping topic labels preserves the decision shape.

- [ ] Add:

```python
def run_selects(
    groups: Sequence[MomentGroup],
    *,
    record_shots: Sequence[RecordShotMark],
    requester: EditorialGateway,
    trace: Trace,
) -> SelectsPassResult: ...
```

Every selected representative exposes its actual shippable duration. Record shots stay in a
sidecar lane and are added to the workprint without competing for the aesthetic slot.

- [ ] Stop membership-changing dedup, burst winners, photo-vs-motion suppression, and post-select
  scalar dedup in the new flow. Keep their similarity calculations as grounded annotations and
  leave old functions present for the legacy selector until Task 14.

- [ ] Run:

```bash
make test-one T="tests/test_selects_answer.py tests/test_selection_selects.py tests/test_favourite_law.py tests/test_same_moment.py tests/test_content_dedup.py tests/test_photo_burst_dedup.py tests/test_moment_suppression.py"
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/selects_answer.py src/immich_memories/analysis/selection_selects.py src/immich_memories/analysis/moment_grouping.py src/immich_memories/analysis/favourite_law.py src/immich_memories/analysis/selection_flow.py tests/test_selects_answer.py tests/test_selection_selects.py tests/test_favourite_law.py tests/test_same_moment.py
git commit -m "feat(selection): choose peaks with visual moment battles (#764)"
```

---

## Task 8: Move Expensive Analysis behind Selects

**Files:**

- Modify: `src/immich_memories/analysis/smart_pipeline.py:269-390`
- Modify: `src/immich_memories/photos/photo_pipeline.py:63-138, 648-721`
- Modify: `src/immich_memories/analysis/llm_response_parser.py:227-284`
- Modify: `src/immich_memories/analysis/selection_flow.py`
- Create: `tests/test_selected_visual_analysis.py`
- Modify: `tests/test_pipeline_integration.py`

- [ ] RED: script a corpus of 20 assets resolving to four representatives and one alternate. Assert
  content/deep analysis runs only for those five visuals after Pass 2, while every source asset was
  visible in Pass 0/1.

- [ ] GREEN: add `analyze_selected_visuals()` after Selects. Materialize selected video segments,
  compute actual shippable durations, and attach objective motion/audio/content evidence to the
  selected visual contracts. Do not call `_enhance_with_llm()` once per discarded photo.

- [ ] RED/GREEN: when selected media cannot be analyzed or rendered into its promised segment,
  leave the editorial result unresolved with `!!`; do not silently substitute the next score.

- [ ] REFACTOR: use the atlas's frame sampler instead of the uncached
  `ContentAnalyzer.extract_frames()` path where the evidence is identical.

- [ ] Run:

```bash
make test-one T="tests/test_selected_visual_analysis.py tests/test_pipeline_integration.py tests/test_frame_extraction.py"
make test-integration-processing
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/smart_pipeline.py src/immich_memories/photos/photo_pipeline.py src/immich_memories/analysis/llm_response_parser.py src/immich_memories/analysis/selection_flow.py tests/test_selected_visual_analysis.py tests/test_pipeline_integration.py
git commit -m "refactor(selection): analyze the visuals that reach the cut (#764)"
```

**Slice gate:** The Pass 2 sheet exposes complete moment battles, correct favorite behavior, the
pregnancy-test sidecar, bounded alternates, actual durations, and measured request counts. Judge
representatives only—not the final narrative cut yet.

---

## Task 9: Rewrite Structure as a Visual Reject-Only Rough Cut

**Branch:** `refactor/764-structure-projection`

**Files:**

- Rewrite: `src/immich_memories/analysis/selection_structure.py:69-663`
- Rewrite: `src/immich_memories/analysis/structure_answer.py:157-298`
- Modify: `src/immich_memories/analysis/selection_flow.py`
- Rewrite/adapt: `tests/test_selection_structure.py`
- Modify: `tests/test_structure_answer.py`

- [ ] RED: feed a fully fitting workprint and assert Structure still receives its exact sheet.
  Unnamed moments survive; only complete named rejects act.

```python
def test_structure_runs_when_cut_already_fits_and_only_named_rejects_act() -> None:
    result = run_structure(workprint("a", "b", "c", seconds=40), insight=insight(), target_duration=60, requester=scripted_reject("b"), trace=Trace())
    assert result.kept_ids == ("a", "c")
    assert result.rejected == (StructureDecision("b", "duplicates the same contribution"),)
```

- [ ] GREEN: replace `_ask()` text tables with a chronological selected workprint sheet containing
  record shots, Pass 2 reasons, actual durations, and current insight. Remove the already-fits
  bypass, global rank, `_shipped_est`, per-moment cap, and release-to-fit logic.

- [ ] RED/GREEN: if the initial valid rough cut is above 110%, ask for the smallest additional
  named sacrifice set. Apply a complete set atomically. A failed/incomplete continuation leaves
  the valid overlong cut unchanged and adds `!! unresolved envelope`.

- [ ] RED/GREEN: rejecting the last surviving visual of an episode or the last record/favorite
  representative requires an explicit protected-occasion reason. Visual weakness, repetition, and
  duration alone are invalid protected-occasion reasons.

- [ ] RED/GREEN: assert chronology is stored, even when sacrifice importance order is deliberately
  non-chronological. There is no runtime prefix/tail interpretation.

- [ ] Keep only the safe #768 ideas with fresh tests: balanced complete-object parsing,
  reject-only application, chronological IDs, reason ledger, and no invented fallback order.

- [ ] Run:

```bash
make test-one T="tests/test_selection_structure.py tests/test_structure_answer.py tests/test_editorial_trace.py"
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/selection_structure.py src/immich_memories/analysis/structure_answer.py src/immich_memories/analysis/selection_flow.py tests/test_selection_structure.py tests/test_structure_answer.py
git commit -m "refactor(selection): make structure a visual rough cut (#764)"
```

---

## Task 10: Project and Revise the Insight Once

**Files:**

- Create: `src/immich_memories/analysis/projection_answer.py`
- Create: `src/immich_memories/analysis/selection_projection.py`
- Modify: `src/immich_memories/analysis/selection_flow.py`
- Create: `tests/test_selection_projection.py`

- [ ] RED: a revised insight replays Structure exactly once over the complete original Pass 2
  workprint, including an item rejected by the first Structure answer.

```python
def test_revised_insight_replays_structure_once_over_complete_workprint() -> None:
    result = select_with_script(first_reject="b", projection="revise", replay_keep="b")
    assert result.structure_calls == 2
    assert result.structure_inputs[1] == ("a", "b", "c")
    assert result.selected_ids == ("a", "b", "c")
```

- [ ] GREEN: implement `project_insight()` returning confirm, revise, or discard with evidence. If
  no provisional thesis exists, skip the model call and record the skip.

- [ ] RED/GREEN: enforce a single revision and a single replay. A second revision request is
  invalid and fail-open. Cache identity includes prior insight, complete workprint, Structure
  decisions, and sheet hashes.

- [ ] RED/GREEN: bank before/after evidence in the same trace. First Structure decisions are
  evidence, never vetoes during replay.

- [ ] Run:

```bash
make test-one T="tests/test_selection_projection.py tests/test_selection_structure.py tests/test_editorial_trace.py"
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/projection_answer.py src/immich_memories/analysis/selection_projection.py src/immich_memories/analysis/selection_flow.py tests/test_selection_projection.py
git commit -m "feat(selection): revise the thesis once against the cut (#764)"
```

**Slice gate:** Only now judge the June Structure cut. It must receive the pregnancy-test record
shot, actual Pass 2 representatives, favorites, durations, and insight. Two professional races may
not both survive merely for being separate events; the Brussels Tour setup/action/payoff sequence
may survive if its three contributions earn the time. No topic count is asserted.

---

## Task 11: Judge the Whole Cut and Perform One Bounded Repair

**Branch:** `feat/764-fine-cut`

**Files:**

- Create: `src/immich_memories/analysis/fine_cut_answer.py`
- Create: `src/immich_memories/analysis/selection_fine_cut.py`
- Modify: `src/immich_memories/analysis/selection_flow.py`
- Create: `tests/test_fine_cut_answer.py`
- Create: `tests/test_selection_fine_cut.py`
- Adapt: `tests/test_selection_review.py`
- Adapt: `tests/test_review_parsing.py`
- Adapt: `tests/test_review_drops_are_applied.py`

- [ ] RED: Fine Cut sees one complete chronological cut sheet, current insight, Structure reasons,
  record-shot marks, and one banked alternate per eligible moment. It returns a strict partition.

- [ ] GREEN: implement:

```python
def run_fine_cut(
    structure: StructurePassResult,
    *,
    insight: PeriodInsight | None,
    alternates: Mapping[str, tuple[str, ...]],
    record_shots: Sequence[RecordShotMark],
    requester: EditorialGateway,
    trace: Trace,
) -> FineCutResult: ...
```

Retain the useful strict partition and fail-open parser semantics from `selection_review.py`, but
replace its text-only question and post-hoc favorite vetoes.

- [ ] RED/GREEN: a reject reason such as “missing establishing context” may inspect one banked
  alternate from the same moment. The replacement gets one visual judgment. A rejected moment or
  occasion cannot return through a sibling; reopening requires the already-bounded projection
  replay.

- [ ] RED/GREEN: a shorter strong cut remains short. Fine Cut cannot call backfill, score-ranked
  replenish, stabilise, numeric judge, or an open-ended repair loop.

- [ ] RED/GREEN: failure, refusal, or truncation rejects nothing further and adds `!!`; complete
  named rejects before a truncated tail may act only when the complete partition contract still
  validates.

- [ ] Probe the combined Projection + provisional Fine Cut request against separate calls over the
  same sheet. Enable fusion only if conservation and decisions match. If projection revises,
  discard the provisional Fine Cut response, replay Structure, and run Fine Cut once on the changed
  workprint. Record planned and actual call counts.

- [ ] Run:

```bash
make test-one T="tests/test_fine_cut_answer.py tests/test_selection_fine_cut.py tests/test_selection_review.py tests/test_review_parsing.py tests/test_review_drops_are_applied.py tests/test_the_review_makes_the_cut.py"
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/fine_cut_answer.py src/immich_memories/analysis/selection_fine_cut.py src/immich_memories/analysis/selection_flow.py tests/test_fine_cut_answer.py tests/test_selection_fine_cut.py tests/test_selection_review.py tests/test_review_parsing.py tests/test_review_drops_are_applied.py
git commit -m "feat(selection): judge the complete visual cut (#764)"
```

---

## Task 12: Make Rendering a Conserved Implementation Detail

**Files:**

- Modify: `src/immich_memories/analysis/motion_rendering.py:20-76`
- Modify: `src/immich_memories/analysis/live_photo_pipeline.py:28-120`
- Modify: `src/immich_memories/generate_clips.py:96-184`
- Modify: `src/immich_memories/generate_timeline.py:78-129`
- Modify: `src/immich_memories/generate.py`
- Create: `tests/test_render_membership.py`
- Modify: `tests/test_rendering_last.py`
- Modify: `tests/test_motion_rendering.py`
- Modify: `tests/test_timeline_budget.py`
- Add: `tests/integration/assembly/test_editorial_membership.py`

- [ ] RED: select photo/video/Live Photo visuals in a known order and assert render planning chooses
  a mode from the selected asset's immutable `LivePhotoRenderingFamily` without changing membership.

```python
def test_render_plan_preserves_ordered_editorial_membership() -> None:
    selected = (visual("photo"), visual("live", modes=("still", "motion")), visual("video"))
    plan = plan_renderings(selected)
    assert tuple(item.asset_id for item in plan) == ("photo", "live", "video")
```

- [ ] GREEN: resolve still versus stitched motion only after Fine Cut from the preserved ordered,
  aligned family manifest. Emit the selected asset identity for either mode, remove legacy
  `_motion_by_carrier`, and never independently align sorted still/video/trim/shutter arrays.
- [ ] RED/GREEN: pre-render stages carry per-mode duration options or explicit mode-neutral bounds;
  a single intended duration must not secretly choose motion. Validate the final chosen mode's
  duration contract during render planning.
- [ ] RED/GREEN: the visual atlas eventually places a Live Photo still and bounded local motion
  evidence in the same tile and existing request. It adds no LLM call and never claims motion was
  judged when motion pixels were unavailable.

- [ ] RED/GREEN: remove membership changes from `_sample_for_minimum_duration()`. Budget adjustment
  may trim intended segments proportionally within their contracts; it cannot add, drop, reorder,
  or substitute visuals.

- [ ] RED/GREEN: make extraction failure fatal to the run. After extraction and after assembly,
  compare ordered selected IDs with ordered produced IDs. Missing, extra, or reordered IDs create a
  conservation failure and abort; never ship a partial output.

- [ ] Add a real FFmpeg `testsrc` integration proving ordered membership and an injected extraction
  failure proving no partial output is accepted.

- [ ] Run:

```bash
make test-one T="tests/test_render_membership.py tests/test_rendering_last.py tests/test_motion_rendering.py tests/test_timeline_budget.py"
make test-integration-assembly
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/analysis/motion_rendering.py src/immich_memories/analysis/live_photo_pipeline.py src/immich_memories/generate_clips.py src/immich_memories/generate_timeline.py src/immich_memories/generate.py tests/test_render_membership.py tests/test_rendering_last.py tests/test_motion_rendering.py tests/test_timeline_budget.py tests/integration/assembly/test_editorial_membership.py
git commit -m "fix(selection): preserve the cut through rendering (#764)"
```

**Slice gate:** Review the complete June, April, and August cuts. Each is chronological,
warning-free, conserved, and near its requested duration without filler. June includes the
pregnancy test, excludes the generic selfie unless it earns a role, and resolves repeated races
from their contribution—not a cycling cap.

---

## Task 13: Converge CLI, UI, Automation, Dry-Run, and Contact Sheets

**Branch:** `refactor/764-editorial-cutover`

**Files:**

- Modify: `src/immich_memories/cli/_pipeline_runner.py:134-178, 263-313, 316-568`
- Modify: `src/immich_memories/cli/_candidate_pool.py:27-215`
- Modify: `src/immich_memories/cli/generate.py:243-289, 370-602`
- Modify: `src/immich_memories/cli/_album_generation.py:30-148`
- Modify: `src/immich_memories/cli/_trip_generation.py:147-317`
- Modify: `src/immich_memories/ui/pages/step2_loading.py:41-234`
- Modify: `src/immich_memories/ui/pages/clip_pipeline.py:183-370`
- Modify: `src/immich_memories/ui/state.py:65-113, 279-281`
- Modify: `src/immich_memories/ui/pages/step3_options.py`
- Modify: `src/immich_memories/ui/pages/step4_export.py`
- Modify: `src/immich_memories/ui/pages/_step4_generate.py`
- Modify: `src/immich_memories/automation/runner.py:503-580`
- Modify: `scripts/contact_sheet.py:35-72, 197-303`
- Create: `tests/test_selection_surface_parity.py`
- Modify: `tests/test_no_render.py`
- Modify: `tests/test_clip_pipeline_ui.py`
- Modify: `tests/test_auto_runner.py`
- Modify: `tests/test_contact_sheet.py`

- [ ] RED: dependency-inject one scripted gateway and run CLI, UI adapter, automation, dry-run, and
  no-render over the same fake source. Assert exact equality of source IDs, owner exclusions, pass
  order, ordered final IDs, intended segments/durations, warnings, request count, and trace
  fingerprint.

- [ ] GREEN: make all surfaces call only `prepare_editorial_source()` and
  `select_editorial_cut()`. Source scopes cover date range, album, and trip. The core imports no UI
  or CLI package, preserving `pyproject.toml` import-linter contracts.

- [ ] RED/GREEN: UI Step 2 checkboxes become explicit owner exclusions before Pass 0. Persist the
  typed `EditorialSelectionResult` in UI state. Steps 3 and 4 consume its unified ordered visual
  list directly; never reconstruct selected photos against a video-only source list.

- [ ] RED/GREEN: automation dry-run invokes the real CLI with rendering, upload, delivery, and
  notifications disabled. It reports success only after selection succeeds.

- [ ] RED/GREEN: `--dry-run` and `--no-render` share full editorial semantics. Keep `--no-render`
  as a compatibility alias if needed. If a cheap inventory-only operation remains useful, give it
  a different explicit name; it is never gate evidence.

- [ ] Replace `scripts/contact_sheet.py` private SmartPipeline monkeypatching with the public
  no-render outcome and trace. Preserve the private-media warning and sweep output layout.

- [ ] Run:

```bash
make test-one T="tests/test_selection_surface_parity.py tests/test_no_render.py tests/test_clip_pipeline_ui.py tests/test_auto_runner.py tests/test_contact_sheet.py tests/test_surface_parity.py"
make test-integration-pipeline
make test-integration-cli
make e2e
make test
make critique
make ci
```

- [ ] Commit:

```bash
git add src/immich_memories/cli src/immich_memories/ui src/immich_memories/automation/runner.py scripts/contact_sheet.py tests/test_selection_surface_parity.py tests/test_no_render.py tests/test_clip_pipeline_ui.py tests/test_auto_runner.py tests/test_contact_sheet.py tests/test_surface_parity.py
git commit -m "refactor(selection): route every surface through one editor (#764)"
```

---

## Task 14: Delete Superseded Selectors and Migrate Configuration

**Files:**

- Delete after replacement tests pass: `src/immich_memories/analysis/arithmetic_funnel.py`
- Delete after replacement tests pass: `src/immich_memories/analysis/selection_review.py`
- Delete or reduce: `src/immich_memories/analysis/selection_quality.py`
- Delete or reduce: `src/immich_memories/analysis/clip_backfill.py`
- Delete or reduce: `src/immich_memories/analysis/clip_refiner.py`
- Delete or reduce: `src/immich_memories/analysis/photo_look.py`
- Modify: `src/immich_memories/analysis/smart_pipeline.py`
- Modify: `src/immich_memories/analysis/subject_policy.py`
- Modify: `src/immich_memories/config_models_analysis.py`
- Modify: `src/immich_memories/config_models_render.py`
- Modify: `src/immich_memories/config_loader.py:73-121`
- Modify: `src/immich_memories/config_presets.py`
- Modify: `ARCHITECTURE.md`
- Modify: `docs-site/docs/create/pipeline/pipeline-overview.md`
- Modify: `docs-site/docs/create/pipeline/clip-selection-scoring.md`
- Modify: `docs-site/docs/create/pipeline/photo-support.md`
- Modify: `docs-site/docs/create/cli/generate.md`
- Modify: relevant pages under `docs-site/docs/create/web-ui/`
- Modify: `docs-site/docs/reference/config-reference.md`

- [ ] RED: replace obsolete behavior tests with assertions that the new public flow cannot import
  or call arithmetic rank, quota, backfill, numeric judge, text Structure/Review, photo cap, or
  timeline membership sampling.

- [ ] GREEN: delete each legacy membership changer only after its replacement coverage is green:

  - arithmetic funnel after Structure convergence and honest unresolved-envelope behavior;
  - subject quotas after Cull evidence and cross-surface parity;
  - `let_the_favourite_win()` after construction-time favorite tests;
  - membership-changing dedup after visual Selects/Fine Cut reasons and conservation;
  - backfill after shorter-strong-cut and bounded-alternate tests;
  - numeric judge/stabilize/photo look after Fine Cut owns final visible rejection;
  - legacy trace adapters after every public path emits complete editorial pass/request records.

  Keep source similarity evidence, Live Photo component normalization, and non-membership segment
  trimming.

- [ ] RED/GREEN: removed nested Pydantic fields currently disappear silently. Emit explicit removed
  field warnings before deleting them; do not add compatibility aliases or reinterpret old ratios
  as new editorial policy.

- [ ] Update CLI/config generated references, pipeline docs, UI docs, and `ARCHITECTURE.md`. Explain
  the pass order, minimum-call packing, dry-run semantics, cache invalidation, owner exclusions,
  and the fact that shorter strong cuts beat filler.

- [ ] Run:

```bash
make docs-cli
make docs-cli-check
make docs-config-check
make docs-build
make docs-check
make test-integration-processing
make test-integration-pipeline
make test-integration-cli
make e2e
make test
make critique
make ci
```

- [ ] Commit deletion and docs as separate reviewable commits, each after `make ci`:

```bash
git commit -m "refactor(selection): remove superseded membership paths (#764)"
git commit -m "docs(selection): document the contact-sheet editor (#764)"
```

---

## Task 15: Final Acceptance and Cutover

**Branch:** `feature/764-editorial-selection`, after all slice PRs are squash-merged

- [ ] Merge current `origin/main` into the feature trunk. Resolve conflicts by preserving the new
  single-flow contracts; never resurrect a legacy membership stage merely to make a test pass.

- [ ] Run synthetic acceptance tests covering:

  - 1, 2, and 3 useful repetitions under short, medium, and long envelopes;
  - five repetitions in an unrelated domain;
  - topic-label swaps preserving decision shape;
  - low-aesthetic record shot survival and composition;
  - favorite auto-win and protected-occasion omission;
  - refusal/truncation fail-open behavior;
  - one projection replay maximum;
  - complete chronological pass/request trace and conservation;
  - exact CLI/UI/automation/dry-run parity;
  - real FFmpeg membership/order conservation and fatal extraction failure.

- [ ] Run a fresh private output directory for the feature-trunk SHA:

```bash
make dev
make contact-sheets SPEC=/absolute/private/764-gates.json OUT=/absolute/private/cutover-<sha>
```

The private spec contains June plus accepted April and August controls. Never commit or attach the
media, sheets, or traces.

- [ ] Owner acceptance requires all three sheets to be chronological, judgeable, warning-free,
  conserved, and near target without filler. June additionally checks the observed regressions:
  pregnancy-test record shot present, generic selfie absent unless it earns a role, kitten selfie
  allowed if useful, repeated professional races resolved by contribution, and Brussels Tour
  setup/action/payoff allowed to survive when earned.

- [ ] Run final repository gates:

```bash
make test-integration
make test-integration-cli
make e2e
make docs-check
make critique
make ci
make launch-check
```

- [ ] Update the umbrella draft PR body with per-slice PRs, measured cached/uncached model call
  counts, synthetic results, private owner verdicts without private media, configuration removals,
  and rollback instructions. Change it from draft only after owner approval.

- [ ] Merge the final `feature/764-editorial-selection` → `main` PR. This is the only PR that closes
  #764. Keep the previous selector available in Git history; do not add a hidden runtime fallback
  that can silently masquerade as the new editor.

## Final Self-Review Checklist

- [ ] No `TODO`, `TBD`, placeholder parser, provider-name limit guess, or topic-specific quota is in
  production code.
- [ ] Every pass consumes exact pixels and carries input IDs, sheet hashes, schema/model versions,
  cache identity, duration, decisions, and conservation in one trace.
- [ ] The normal uncached call budget is episode-scan packs + one period synthesis + packed Selects
  + one Structure + one Projection/Fine request, with extra calls only for proven continuations,
  one revision replay, or a failed combined-call probe.
- [ ] No per-asset or per-moment LLM loop exists where a packed sheet can preserve judgment quality.
- [ ] No scalar score, quota, deduper, backfill, reviewer, or renderer changes final membership.
- [ ] The feature trunk is the base of every production slice; `main` changes only at final cutover.
- [ ] `make ci`, `make critique`, required integration suites, docs gates, and owner contact-sheet
  gates all pass at the exact final SHA.

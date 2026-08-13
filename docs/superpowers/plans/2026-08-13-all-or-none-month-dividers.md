# All-or-None Month Dividers Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `superpowers:test-driven-development` to execute each task, and `superpowers:verification-before-completion` before claiming success.

**Goal:** Make chronological month dividers an immutable all-or-none decision based on the selected clips, while allowing a bounded duration overrun and preserving the existing trip and year policies.

**Architecture:** Keep `plan_timeline()` as a pre-selection content-budget planner, then add a pure post-selection finalizer that counts the actual selected months and chooses every divider or zero. Thread that finalized `TimelinePlan` through dry-run output, title generation, and final-duration validation so planning and rendering have one source of truth.

**Tech Stack:** Python 3.11+, frozen dataclasses, Click, pytest, FFmpeg/ffprobe, `uv`.

---

## Task 1: Add the two-stage timeline contract

**Files:**

- Modify: `src/immich_memories/processing/timeline_budget.py`
- Modify: `tests/test_timeline_budget.py`

### Step 1: Write the failing six-month all-or-none test

Extend the test helpers so `ending_duration` defaults to the live 4-second value for the new scenarios. Add a test using one clip in each month from February through July and a selected content duration of 48 seconds:

```python
def test_selected_months_receive_the_complete_divider_set() -> None:
    from immich_memories.processing.timeline_budget import (
        finalize_selected_timeline,
        plan_timeline,
    )

    clips = [
        _clip(month.lower(), f"2026-{index:02d}-05")
        for index, month in enumerate(
            ["February", "March", "April", "May", "June", "July"], start=2
        )
    ]
    titles = _titles(ending_duration=4.0)
    preliminary = plan_timeline(clips, titles, 60.0, "person_spotlight")

    final = finalize_selected_timeline(
        preliminary,
        clips,
        selected_duration=48.0,
        title_settings=titles,
        memory_type="person_spotlight",
    )

    assert preliminary.divider_policy == "pending"
    assert preliminary.max_dividers == 0
    assert final.divider_policy == "all"
    assert final.eligible_dividers == 5
    assert final.max_dividers == 5
    assert final.title_budget == pytest.approx(17.5)
    assert final.soft_max_duration == pytest.approx(70.0)
```

This is the primary regression: a result may never contain only the first two of five required dividers.

### Step 2: Run the focused test and confirm RED

Run:

```bash
uv run pytest tests/test_timeline_budget.py::test_selected_months_receive_the_complete_divider_set -q
```

Expected: fail because `finalize_selected_timeline` and the policy fields do not exist.

### Step 3: Add explicit plan state and the pure finalizer

In `timeline_budget.py`:

1. Import `replace` and `Literal`.
2. Add `DividerPolicy = Literal["pending", "all", "none", "capped"]`.
3. Append backward-compatible default fields to `TimelinePlan` so existing positional construction remains valid:

```python
divider_policy: DividerPolicy = "capped"
eligible_dividers: int = 0
soft_max_duration: float | None = None
```

4. Add helpers that identify chronological month mode and count distinct selected months in encounter order. This post-selection count must ignore `month_divider_threshold`; a selected month with one clip is still part of the chronology.
5. Change `plan_timeline()` only for enabled, non-trip month mode:
   - budget opening and ending;
   - reserve zero dividers;
   - set `divider_policy="pending"`;
   - set `soft_max_duration=target + min(10.0, target * 0.20)`;
   - leave trip and year behavior on the existing capped path.
6. Add the pure finalizer:

```python
def finalize_selected_timeline(
    preliminary: TimelinePlan,
    selected_clips: list[Any],
    *,
    selected_duration: float,
    title_settings: Any | None,
    memory_type: str | None,
) -> TimelinePlan:
    """Choose all chronological month dividers or none after selection."""
```

It must return `preliminary` unchanged unless its policy is `"pending"`. For a pending plan:

```text
eligible = max(0, distinct valid selected months - 1)
complete_title_budget = opening + ending + eligible * divider_duration
raw_estimate = selected_duration + complete_title_budget
include_all = raw_estimate <= soft_max_duration
```

Return a `dataclasses.replace()` result with:

- `divider_policy="all"` and `max_dividers=eligible` when the complete set fits;
- `divider_policy="none"` and `max_dividers=0` when it does not;
- `eligible_dividers=eligible` in both cases;
- `title_budget` recomputed from the chosen count;
- the original `content_budget` unchanged, so selection is never run twice or trimmed again.

For one valid month or no valid dates, use policy `"all"` with zero eligible dividers: the complete required set is empty.

### Step 4: Run the primary test and confirm GREEN

Run:

```bash
uv run pytest tests/test_timeline_budget.py::test_selected_months_receive_the_complete_divider_set -q
```

Expected: pass.

### Step 5: Add boundary, threshold, short-duration, and regression tests one at a time

Add and run each test individually before adding the next:

- `test_selected_month_dividers_are_all_or_none_above_soft_maximum`: 53 seconds of content plus six months of titles exceeds 70 seconds, so `eligible_dividers == 5`, `max_dividers == 0`, and policy is `"none"`.
- `test_single_clip_month_is_included_after_selection`: set `month_divider_threshold=2`, provide January with two clips and February with one, and prove finalization still selects the February divider.
- `test_first_selected_month_is_covered_by_opening`: two selected months produce exactly one eligible divider.
- `test_short_memory_scales_soft_overrun`: a 15-second target has an 18-second soft maximum, not 25 seconds.
- Keep `test_trip_location_changes_are_capped_by_the_same_title_budget` passing and assert `divider_policy == "capped"`.
- Add a year-mode assertion proving its existing capped count and budget are unchanged.
- Update pre-selection month assertions to expect base opening/ending budgeting and policy `"pending"`, rather than a partial divider allowance.

Run:

```bash
uv run pytest tests/test_timeline_budget.py -q
```

Expected: all timeline tests pass.

### Step 6: Commit the timeline contract

```bash
git add src/immich_memories/processing/timeline_budget.py tests/test_timeline_budget.py
git commit -m "fix: finalize month dividers after selection"
```

---

## Task 2: Thread the finalized plan through CLI preview and generation

**Files:**

- Modify: `src/immich_memories/cli/_pipeline_runner.py`
- Modify: `src/immich_memories/cli/_generation_preview.py`
- Modify: `tests/integration/cli/test_generate.py`

### Step 1: Write a failing dry-run integration test

In `TestPipelineRunner`, create selected clips with February-through-July `file_created_at` values and 8-second selected segments for 48 seconds total. Run `run_pipeline_and_generate(..., memory_type="person_spotlight", duration=60.0, no_music=True, dry_run=True)` and assert:

```python
output = capsys.readouterr().out
assert "Month dividers: all 5 selected month changes" in output
assert "Title cards: 7 (17.5s)" in output
assert "Estimated final duration: 65.5s" in output
```

Also capture the preliminary `PipelineConfig` and assert it used the base content budget, while no render artifact or music is created.

### Step 2: Run the new integration test and confirm RED

Run with the integration marker enabled:

```bash
uv run pytest -m integration tests/integration/cli/test_generate.py::TestPipelineRunner::test_dry_run_reports_all_selected_month_dividers -q
```

Expected: the preview still receives the preliminary plan and reports zero or a capped count.

### Step 3: Finalize immediately after selection

In `_pipeline_runner.py`:

1. Extract construction of the planning `TitleScreenSettings` from `_configure_timeline()` into a small helper so the same resolved settings can be reused after selection without duplicating config resolution.
2. Change `_configure_timeline()` to return both the preliminary plan and those title settings, or pass the settings into it. Keep its responsibility limited to applying `content_budget` to `PipelineConfig`.
3. Immediately after `pipeline.run_selection(all_candidates)` and the empty-selection guard, compute:

```python
selected_duration = sum(end - start for start, end in clip_segments.values())
timeline_plan = finalize_selected_timeline(
    preliminary_timeline,
    selected_clips,
    selected_duration=selected_duration,
    title_settings=planning_titles,
    memory_type=memory_type,
)
```

4. Move or add logging after finalization. For chronological month mode, log policy, eligible count, chosen count, estimated raw duration, and soft maximum. Do not log the misleading phrase `"2 dividers max"` for this flow.
5. Pass only the finalized plan to `_finish_dry_run()` and `GenerationParams`.

The preliminary plan remains visible only to selection configuration; every downstream consumer gets the finalized plan.

### Step 4: Make dry-run wording describe the decision

In `_generation_preview.py`, add a `month_divider_summary` property or helper based solely on `TimelinePlan`:

```text
all + eligible > 0 -> all N selected month changes
all + eligible == 0 -> none needed (one selected month)
none -> none (complete set would exceed Xs soft maximum)
capped/pending -> no month-policy line
```

Update `print_generation_preview()`:

- emit `Month dividers: ...` when a finalized chronological policy is present;
- change the generic title line to `Title cards: N (Xs)` so it no longer describes all-or-none state as a cap;
- continue using `selected_duration + final title_budget` for the estimate.

### Step 5: Run focused CLI tests and confirm GREEN

Run:

```bash
uv run pytest -m integration tests/integration/cli/test_generate.py::TestPipelineRunner -q
uv run pytest tests/test_cli_smoke.py tests/test_storage_cli_coverage.py -q
```

Expected: pipeline integration and CLI smoke tests pass; dry-run produces no artifact and reports music as disabled.

### Step 6: Commit CLI plan propagation

```bash
git add src/immich_memories/cli/_pipeline_runner.py src/immich_memories/cli/_generation_preview.py tests/integration/cli/test_generate.py
git commit -m "fix: propagate finalized month divider plan"
```

---

## Task 3: Make rendering and duration validation obey the final plan

**Files:**

- Modify: `src/immich_memories/processing/title_inserter.py`
- Modify: `src/immich_memories/generate_timeline.py`
- Modify: `tests/integration/assembly/test_assembly_core.py`
- Modify: `tests/test_generate.py`

### Step 1: Write a failing renderer test for a one-clip month

Add an assembly integration test with three chronological months, a deliberately high `month_divider_threshold`, and a finalized `max_dividers=2`. Assert the generator is called for the second and third months, but not for the first month covered by the opening:

```python
title_settings = TitleScreenSettings(
    show_month_dividers=True,
    month_divider_threshold=99,
    max_dividers=2,
)

paths = inserter.generate_month_dividers(clips, generator, title_settings, None)

assert set(paths) == {(2026, 6), (2026, 7)}
```

Each selected month has only one clip. This proves the renderer does not reapply a threshold after planning.

### Step 2: Run the renderer test and confirm RED

Run:

```bash
uv run pytest -m integration tests/integration/assembly/test_assembly_core.py::TestTitleInserter::test_planned_month_dividers_ignore_clip_threshold -q
```

Use the actual containing class name from the file when selecting the test.

Expected: current threshold filtering produces no divider paths.

### Step 3: Remove renderer-side policy decisions for planned month flows

In `generate_month_dividers()`:

- preserve standalone behavior when `_divider_limit(title_settings)` returns `None`;
- when a plan limit is present, treat it as the complete final decision;
- skip the first detected month because the opening covers it;
- generate exactly `month_changes[1 : limit + 1]`;
- do not count clips or consult `month_divider_threshold` in the planned branch.

`build_clips_with_dividers()` already skips the first month and enforces the same integer limit; retain that defensive guard. Confirm `max_dividers=0` generates and inserts no dividers.

### Step 4: Add soft-envelope duration tests

In `tests/test_generate.py`, construct a finalized 60-second month plan with `divider_policy="all"` and `soft_max_duration=70.0`:

- `test_generation_accepts_finalized_month_plan_within_soft_maximum` passes at 65 seconds;
- `test_generation_rejects_finalized_month_plan_above_soft_maximum` fails above the soft maximum plus the existing one-second technical tolerance;
- keep the existing no-plan behavior: 61.0 seconds passes and 61.1 seconds fails for a 60-second target;
- add a `"none"` policy test proving it retains the normal target-plus-one validation and does not get an unused 70-second allowance.

### Step 5: Update final-duration validation

In `validate_final_duration()` choose the allowed runtime from the finalized plan only when all month dividers were intentionally included:

```python
limit = target + 1.0
plan = params.timeline_plan
if (
    plan is not None
    and plan.divider_policy == "all"
    and plan.eligible_dividers > 0
    and plan.soft_max_duration is not None
):
    limit = plan.soft_max_duration + 1.0
```

Use `limit` in the error message. The extra one second is the existing mux/frame-rounding tolerance; planning itself must still require the raw estimate to be at or below the exact soft maximum.

### Step 6: Run renderer and generation tests

Run:

```bash
uv run pytest -m integration tests/integration/assembly/test_assembly_core.py -q
uv run pytest tests/test_generate.py tests/test_timeline_budget.py -q
```

Expected: all pass. Trip location cards and year dividers remain capped by their old policy.

### Step 7: Commit renderer enforcement

```bash
git add src/immich_memories/processing/title_inserter.py src/immich_memories/generate_timeline.py tests/integration/assembly/test_assembly_core.py tests/test_generate.py
git commit -m "fix: render finalized divider policy exactly"
```

---

## Task 4: Verify the complete behavior and render Emile without music

**Files:**

- Modify: `docs/reviews/2026-08-11-launch-readiness-audit.md`
- Test: `tests/test_timeline_budget.py`
- Test: `tests/test_generate.py`
- Test: `tests/integration/cli/test_generate.py`
- Test: `tests/integration/assembly/test_assembly_core.py`
- Verify artifact under: `/Users/sam/Videos/Memories/`

### Step 1: Run formatting and focused regression checks

Run:

```bash
uv run ruff check src/immich_memories/processing/timeline_budget.py src/immich_memories/processing/title_inserter.py src/immich_memories/cli/_pipeline_runner.py src/immich_memories/cli/_generation_preview.py src/immich_memories/generate_timeline.py tests/test_timeline_budget.py tests/test_generate.py tests/integration/cli/test_generate.py tests/integration/assembly/test_assembly_core.py
uv run ruff format --check src/immich_memories/processing/timeline_budget.py src/immich_memories/processing/title_inserter.py src/immich_memories/cli/_pipeline_runner.py src/immich_memories/cli/_generation_preview.py src/immich_memories/generate_timeline.py tests/test_timeline_budget.py tests/test_generate.py tests/integration/cli/test_generate.py tests/integration/assembly/test_assembly_core.py
uv run pytest tests/test_timeline_budget.py tests/test_generate.py tests/test_cli_smoke.py tests/test_storage_cli_coverage.py -q
uv run pytest -m integration tests/integration/cli/test_generate.py tests/integration/assembly/test_assembly_core.py -q
```

Expected: all commands exit zero.

### Step 2: Run the full non-E2E suite

Run:

```bash
uv run pytest -m "not e2e" -q
```

If an unrelated pre-existing failure appears, record the exact test and demonstrate that the focused changed-area suite remains green. Do not hide or rewrite unrelated failures.

### Step 3: Run a real Emile dry-run with music disabled

Use the same person and date range as the earlier Emile launch smoke, preserving its exact CLI arguments from shell history or the launch audit, and include:

```text
--dry-run --no-music
```

Confirm the output says either all selected month changes or none. For the known selection it should report all, not a partial cap. Record selected content duration, eligible divider count, final title budget, estimate, and soft maximum.

### Step 4: Render the real Emile smoke with intermediates retained

Run the corresponding real-library command with:

```text
--no-music --keep-intermediates
```

Use a new output name containing `emile-month-dividers-20260813`; do not overwrite the prior 51.77-second artifact. Keep upload and notifications disabled exactly as in the earlier smoke run.

Expected:

- no music generation path is invoked;
- the opening covers the first selected month;
- every subsequent selected month has a generated divider, or no month divider exists at all;
- no arbitrary prefix of month dividers exists;
- photo frames retain one aspect-fit/Ken Burns transform rather than stacked scaling effects.

### Step 5: Inspect the artifact and retained title screens

Run `ffprobe` against the exact new path and record:

- duration at or below 70.0 seconds for the 60-second request;
- 1280x720 landscape output;
- H.264 video and AAC audio stream/container compatibility. An AAC stream may be silent or content audio; `--no-music` means no generated soundtrack, not no audio stream.

Inspect the retained month-divider intermediates and representative output frames:

- count matches every selected month after the opening month;
- June and July are present when selected;
- no divider exists for the opening month;
- a 4:3 photo is letterboxed/cropped according to the one resolved scale mode without a second visible zoom stack.

If the selection legitimately exceeds the envelope and chooses none, verify zero divider intermediates and preserve that all-or-none result rather than forcing a partial set.

### Step 6: Record evidence in the launch audit

Append a dated subsection to `docs/reviews/2026-08-11-launch-readiness-audit.md` containing:

- root cause and implemented policy;
- focused/full-suite command results;
- dry-run policy line;
- exact output artifact path;
- ffprobe duration/codecs/resolution;
- observed divider count and selected months;
- confirmation that smoke used `--no-music` and production music configuration was not changed;
- confirmation that Somme trip behavior is covered by unchanged regression tests and was not re-rendered unless a regression requires it.

### Step 7: Run completion verification and commit evidence

Re-run the smallest commands that directly prove every modified behavior after the audit edit, then inspect `git diff --check` and `git status --short`.

```bash
git add docs/reviews/2026-08-11-launch-readiness-audit.md
git commit -m "docs: record month divider smoke verification"
```

Do not add or remove the unrelated untracked `MagicMock/` path.

---

## Completion Gate

Before reporting completion, verify all of the following from fresh command output:

- chronological month flow has a post-selection `"all"` or `"none"` plan, never a partial cap;
- a one-clip selected month receives a divider when the full set fits;
- first selected month is represented only by the opening;
- a 60-second request uses an exact 70-second planning maximum;
- final validation accepts the intentional bounded overrun and rejects values beyond its technical tolerance;
- dry-run describes the same plan used by rendering;
- trip and year regression tests remain unchanged and green;
- real Emile smoke uses `--no-music`, renders all selected month changes or none, and passes ffprobe checks;
- no stacked photo-scaling regression is visible in the inspected smoke frames;
- unrelated `MagicMock/` remains untouched.

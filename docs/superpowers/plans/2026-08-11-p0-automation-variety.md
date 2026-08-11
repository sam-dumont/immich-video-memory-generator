# P0 Automation, Variety, and State Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the daily smart automation bounded, truthful, varied, and isolated from test data.

**Architecture:** Add typed automation outcomes and durable attempt records around the existing pipeline. Candidate categories are explicit and pass through an exhaustive request adapter. Successful completion is proven by a matching new run and artifact, while cross-run variety is enforced before scoring selects one candidate.

**Tech Stack:** Python dataclasses/StrEnum, Click, Pydantic v2, SQLite, subprocess, pytest.

## Global Constraints

- `immich-memories auto run` remains the single daily entry point.
- At most one generation subprocess runs per invocation.
- `skipped` and `dry_run` exit zero; `failed` exits nonzero.
- Only completed `source=auto` runs affect cooldown and variety.
- Monthly automation considers only the latest completed month.
- The same category cannot run consecutively or exceed two of six recent auto runs.
- Unknown candidate categories fail before generation.
- Existing production rows and files are never automatically deleted.
- The installed LaunchAgent remains unloaded.
- Every task follows RED → GREEN → REFACTOR.

---

## File structure

- Create `src/immich_memories/automation/models.py`: outcome, attempt, process, and selection value objects.
- Create `src/immich_memories/automation/state_store.py`: durable automation-attempt queries only.
- Create `src/immich_memories/automation/variety.py`: pure cadence/rotation filtering.
- Create `src/immich_memories/automation/generation_request.py`: exhaustive candidate-to-CLI adapter.
- Modify `src/immich_memories/automation/candidates.py`: explicit candidate category.
- Modify `src/immich_memories/automation/runner.py`: orchestration using the new interfaces.
- Modify `src/immich_memories/automation/calendar_detectors.py`: latest-month behavior and categories.
- Modify `src/immich_memories/automation/event_detectors.py`: distinct activity-burst category.
- Modify `src/immich_memories/cache/database.py`: additive schema migration.
- Modify `src/immich_memories/tracking/models.py`: persist memory category.
- Modify `src/immich_memories/tracking/run_database.py`: source/category/identity queries.
- Modify `src/immich_memories/generate.py`: accept automation identity in `GenerationParams`.
- Modify `src/immich_memories/cli/generate.py`: hidden automation context and exact trip range.
- Modify `src/immich_memories/cli/_pipeline_runner.py`: carry source, key, and category.
- Modify `src/immich_memories/cli/_trip_generation.py`: exact range selection.
- Modify `src/immich_memories/cli/auto_cmd.py`: typed exits, JSON, history, and status.
- Modify `tests/conftest.py`: mandatory temporary user paths.
- Create `tests/test_test_state_isolation.py`.
- Create `tests/test_automation_state.py`.
- Create `tests/test_automation_variety.py`.
- Create `tests/test_generation_request.py`.
- Modify `tests/test_auto_runner.py`, `tests/test_candidate_scorer.py`,
  `tests/test_calendar_detectors.py`, `tests/test_event_detectors.py`,
  `tests/test_run_database_fk.py`, and `tests/test_run_tracker.py`.

### Task 1: Isolate every pytest process from user state

**Files:**
- Modify: `tests/conftest.py`
- Create: `tests/test_test_state_isolation.py`

**Interfaces:**
- Consumes: Pydantic environment variables `IMMICH_MEMORIES_CACHE__DATABASE`, `IMMICH_MEMORIES_CACHE__DIRECTORY`, and `IMMICH_MEMORIES_OUTPUT__DIRECTORY`.
- Produces: `pytest_configure()`/`pytest_unconfigure()` hooks that install and remove one validated session temporary root before test collection.
- Produces: autouse fixture `isolated_user_paths() -> Path` yielding that session root and resetting cached settings per test.

- [ ] **Step 1: Write the failing isolation test**

```python
def test_default_config_uses_pytest_paths(isolated_user_paths: Path) -> None:
    config = Config()
    assert config.cache.database_path.is_relative_to(isolated_user_paths)
    assert config.cache.cache_path.is_relative_to(isolated_user_paths)
    assert config.output.output_path.is_relative_to(isolated_user_paths)
```

- [ ] **Step 2: Run the test and confirm it exposes the real home paths**

Run: `uv run pytest tests/test_test_state_isolation.py -q`

Expected: FAIL because `Config()` resolves at least one path under the normal home directory.

- [ ] **Step 3: Install isolation before collection and reset settings per test**

```python
_TEST_ROOT: Path | None = None
_TEST_ENV_KEYS = {
    "IMMICH_MEMORIES_CACHE__DATABASE": "cache.db",
    "IMMICH_MEMORIES_CACHE__DIRECTORY": "cache",
    "IMMICH_MEMORIES_OUTPUT__DIRECTORY": "output",
}
_ORIGINAL_TEST_ENV: dict[str, str | None] = {}

def pytest_configure(config: pytest.Config) -> None:
    global _TEST_ROOT
    _TEST_ROOT = Path(tempfile.mkdtemp(prefix="immich-memories-pytest-"))
    for key, relative in _TEST_ENV_KEYS.items():
        _ORIGINAL_TEST_ENV[key] = os.environ.get(key)
        os.environ[key] = str(_TEST_ROOT / relative)

@pytest.fixture(autouse=True)
def isolated_user_paths() -> Iterator[Path]:
    assert _TEST_ROOT is not None
    config_loader._config = None
    yield _TEST_ROOT
    config_loader._config = None
```

In `pytest_unconfigure()`, restore each original environment value, validate that `_TEST_ROOT`
has the `immich-memories-pytest-` basename prefix and lives under `tempfile.gettempdir()`, then
remove only that root with `shutil.rmtree()`. Add a fixture-finalizer assertion that the three
resolved paths never equal the normal `~/.immich-memories` or `~/Videos/Memories` paths.

- [ ] **Step 4: Prove isolated configuration and representative DB tests pass**

Run: `uv run pytest tests/test_test_state_isolation.py tests/test_run_database_fk.py tests/test_auto_runner.py -q`

Expected: PASS and no database/output appears in the normal user paths.

- [ ] **Step 5: Commit the isolation guard**

```bash
git add tests/conftest.py tests/test_test_state_isolation.py
git commit -m "test: isolate pytest from user state"
```

### Task 2: Add durable automation attempt state and memory category

**Files:**
- Create: `src/immich_memories/automation/models.py`
- Create: `src/immich_memories/automation/state_store.py`
- Modify: `src/immich_memories/cache/database.py`
- Modify: `src/immich_memories/tracking/models.py`
- Modify: `src/immich_memories/tracking/run_database.py`
- Create: `tests/test_automation_state.py`
- Modify: `tests/test_run_database_fk.py`

**Interfaces:**
- Produces: `AutoOutcome`, `AutomationAttempt`, `AutoRunResult`, `ProcessResult`.
- Produces: `AutomationStateStore.start_attempt()`, `finish_attempt()`, `get_last_attempt()`.
- Produces: `RunDatabase.list_runs(limit=50, offset=0, person_name=None, status=None, source=None)` and `get_completed_run_by_identity(memory_key, source, created_after)`.

- [ ] **Step 1: Write migration and round-trip tests**

```python
def test_automation_attempt_round_trip(tmp_path: Path) -> None:
    store = AutomationStateStore(tmp_path / "cache.db")
    attempt = store.start_attempt(reason="daily wake")
    store.finish_attempt(attempt.id, AutoOutcome.SKIPPED, reason="cooldown")
    saved = store.get_last_attempt()
    assert saved is not None
    assert saved.outcome is AutoOutcome.SKIPPED
    assert saved.reason == "cooldown"

def test_completed_identity_filters_source_and_time(db: RunDatabase) -> None:
    assert db.get_completed_run_by_identity("trip:key", "auto", started_after) == expected
```

- [ ] **Step 2: Run the new tests and confirm schema/interface failures**

Run: `uv run pytest tests/test_automation_state.py tests/test_run_database_fk.py -q`

Expected: FAIL because schema version 10, automation models, and identity queries do not exist.

- [ ] **Step 3: Add schema migration 10**

Increment `SCHEMA_VERSION` from 9 to 10 and add `_migration_v10_automation_state()`:

```sql
ALTER TABLE pipeline_runs ADD COLUMN memory_category TEXT;

CREATE TABLE automation_attempts (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT NOT NULL,
    reason TEXT NOT NULL,
    candidate_category TEXT,
    memory_type TEXT,
    memory_key TEXT,
    run_id TEXT,
    error TEXT
);

CREATE INDEX idx_auto_attempts_started ON automation_attempts(started_at DESC);
```

The same migration adds
`ALTER TABLE pipeline_runs ADD COLUMN memory_people_json TEXT NOT NULL DEFAULT '[]';` so variety
can use exact person identities for single- and multi-person automation.

Use an application-generated attempt ID. Do not use `INSERT OR REPLACE`; terminal updates must
preserve the original start row.

- [ ] **Step 4: Implement typed models and focused persistence**

```python
class AutoOutcome(StrEnum):
    RUNNING = "running"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass(frozen=True)
class AutoRunResult:
    outcome: AutoOutcome
    reason: str
    candidate: MemoryCandidate | None = None
    run_id: str | None = None
    output_path: Path | None = None
    error: str | None = None

@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str

@dataclass(frozen=True)
class AutomationAttempt:
    id: str
    started_at: datetime
    finished_at: datetime | None
    outcome: AutoOutcome
    reason: str
    candidate_category: str | None = None
    memory_type: str | None = None
    memory_key: str | None = None
    run_id: str | None = None
    error: str | None = None
```

Add `memory_category: str | None` and `memory_people: tuple[str, ...]` to `RunMetadata`, its JSON
mapping, SQLite mapping, and save query. Normalize people with Unicode casefold plus collapsed
whitespace before persistence. Add the source filter and completed-identity query to
`RunDatabase` using parameterized SQL.

- [ ] **Step 5: Run state and migration tests**

Run: `uv run pytest tests/test_automation_state.py tests/test_run_database_fk.py tests/test_run_tracker.py -q`

Expected: PASS.

- [ ] **Step 6: Commit durable orchestration state**

```bash
git add src/immich_memories/automation/models.py src/immich_memories/automation/state_store.py src/immich_memories/cache/database.py src/immich_memories/tracking/models.py src/immich_memories/tracking/run_database.py tests/test_automation_state.py tests/test_run_database_fk.py tests/test_run_tracker.py
git commit -m "feat: persist automation attempts and identity"
```

### Task 3: Give every detector an explicit category

**Files:**
- Modify: `src/immich_memories/automation/candidates.py`
- Modify: `src/immich_memories/automation/calendar_detectors.py`
- Modify: `src/immich_memories/automation/event_detectors.py`
- Modify: `tests/test_auto_runner.py`
- Modify: `tests/test_candidate_scorer.py`
- Modify: `tests/test_calendar_detectors.py`
- Modify: `tests/test_event_detectors.py`
- Modify: `tests/integration/automation/test_auto_suggest.py`

**Interfaces:**
- Produces: `CandidateCategory` StrEnum and required `MemoryCandidate.category`.
- Consumes: existing `memory_type` remains the rendering preset.

- [ ] **Step 1: Add failing category coverage tests**

Add `assert result[0].category is CandidateCategory.MONTHLY_REVIEW` to
`TestMonthlyDetector.test_produces_candidates_for_ungenerated_months`. Add
`assert c.category is CandidateCategory.ACTIVITY_BURST` to
`TestActivityBurstDetector.test_detects_burst_months`, and
`assert c.category is CandidateCategory.TRIP` to
`TestTripDetector.test_produces_candidates_for_trips`. Update the existing candidate factories
in `test_auto_runner.py` and `test_candidate_scorer.py` with their exact category.

- [ ] **Step 2: Run detector tests and verify the missing field failure**

Run: `uv run pytest tests/test_auto_runner.py tests/test_candidate_scorer.py tests/test_calendar_detectors.py tests/test_event_detectors.py tests/integration/automation/test_auto_suggest.py -q`

Expected: FAIL because `MemoryCandidate` has no category.

- [ ] **Step 3: Add the category enum and update every constructor**

```python
class CandidateCategory(StrEnum):
    MONTHLY_REVIEW = "monthly_review"
    ACTIVITY_BURST = "activity_burst"
    YEAR_IN_REVIEW = "year_in_review"
    PERSON_SPOTLIGHT = "person_spotlight"
    BIRTHDAY = "birthday"
    MULTI_PERSON = "multi_person"
    ON_THIS_DAY = "on_this_day"
    TRIP = "trip"
```

Make `category` required rather than defaulting from `memory_type`; this forces new detectors
to choose intentionally. Birthday candidates use `BIRTHDAY` even though they render through
`person_spotlight`.

- [ ] **Step 4: Run detector and candidate serialization tests**

Run: `uv run pytest tests/test_auto_runner.py tests/test_candidate_scorer.py tests/test_calendar_detectors.py tests/test_event_detectors.py tests/integration/automation/test_auto_suggest.py -q`

Expected: PASS, and JSON/table output includes category.

- [ ] **Step 5: Commit explicit candidate identity**

```bash
git add src/immich_memories/automation/candidates.py src/immich_memories/automation/calendar_detectors.py src/immich_memories/automation/event_detectors.py tests/test_auto_runner.py tests/test_candidate_scorer.py tests/test_calendar_detectors.py tests/test_event_detectors.py tests/integration/automation/test_auto_suggest.py
git commit -m "feat: classify automation candidates explicitly"
```

### Task 4: Enforce monthly cadence and cross-run variety

**Files:**
- Create: `src/immich_memories/automation/variety.py`
- Create: `tests/test_automation_variety.py`
- Modify: `src/immich_memories/automation/calendar_detectors.py`
- Modify: `src/immich_memories/automation/runner.py`

**Interfaces:**
- Consumes: candidates, last six completed auto runs, and `today`.
- Produces: `VarietyDecision(eligible, rejected)` where rejected entries contain candidate and exact rule.

- [ ] **Step 1: Write hard-guardrail tests**

```python
def test_same_category_cannot_repeat() -> None:
    decision = apply_variety_rules([monthly], [completed("monthly_review")], today)
    assert decision.eligible == []
    assert decision.rejected[0].rule == "same_category_as_previous"

def test_category_cannot_exceed_two_of_six() -> None:
    history = completed_categories("trip", "monthly_review", "trip", "birthday", "person_spotlight", "on_this_day")
    decision = apply_variety_rules([trip], history, today)
    assert decision.eligible == []
    assert decision.rejected[0].rule == "category_limit_two_of_six"

def test_monthly_detector_only_proposes_latest_completed_month() -> None:
    candidates = MonthlyDetector().detect(asset_counts_for_six_months, [], set(), config, today)
    assert [(c.date_range_start.year, c.date_range_start.month) for c in candidates] == [(2026, 7)]
```

- [ ] **Step 2: Run the focused tests and confirm current backlog behavior fails**

Run: `uv run pytest tests/test_automation_variety.py -q`

Expected: FAIL because the detector emits six months and no cross-run gate exists.

- [ ] **Step 3: Implement pure variety filtering**

```python
@dataclass(frozen=True)
class RejectedCandidate:
    candidate: MemoryCandidate
    rule: str

@dataclass(frozen=True)
class VarietyDecision:
    eligible: list[MemoryCandidate]
    rejected: list[RejectedCandidate]

def apply_variety_rules(
    candidates: list[MemoryCandidate],
    recent_auto_runs: list[RunMetadata],
    today: date,
) -> VarietyDecision:
    previous = recent_auto_runs[0] if recent_auto_runs else None
    category_counts = Counter(run.memory_category for run in recent_auto_runs)
    eligible: list[MemoryCandidate] = []
    rejected: list[RejectedCandidate] = []
    for candidate in candidates:
        rule = rejection_rule(candidate, previous, category_counts, recent_auto_runs, today)
        if rule is None:
            eligible.append(candidate)
        else:
            rejected.append(RejectedCandidate(candidate, rule))
    return VarietyDecision(eligible=eligible, rejected=rejected)
```

Implement `rejection_rule()` with four explicit checks in this order: same category as the
previous run, category count already two in the six-run window, monthly review already
completed in `today`'s calendar month, and normalized person identity present in either of the
last two person-bearing runs. Return the stable rule strings asserted by the tests. If every
candidate is rejected, return an empty eligible list; do not fall back to the original
candidates.

- [ ] **Step 4: Apply variety before final ranking in `AutoRunner.suggest()`**

Fetch the last six completed `source=auto` runs. Preserve rejection reasons on the runner for
JSON/dry-run/status output. Activity burst remains eligible independently of monthly review.

- [ ] **Step 5: Run variety and existing scorer tests**

Run: `uv run pytest tests/test_automation_variety.py tests/test_auto_runner.py tests/test_scoring.py -q`

Expected: PASS.

- [ ] **Step 6: Commit hard variety rules**

```bash
git add src/immich_memories/automation/variety.py src/immich_memories/automation/calendar_detectors.py src/immich_memories/automation/runner.py tests/test_automation_variety.py tests/test_auto_runner.py
git commit -m "feat: enforce automation variety and monthly cadence"
```

### Task 5: Build exhaustive generation requests and exact trip selection

**Files:**
- Create: `src/immich_memories/automation/generation_request.py`
- Create: `tests/test_generation_request.py`
- Modify: `src/immich_memories/cli/generate.py`
- Modify: `src/immich_memories/cli/_pipeline_runner.py`
- Modify: `src/immich_memories/cli/_trip_generation.py`
- Modify: `src/immich_memories/generate.py`
- Modify: `tests/integration/cli/test_generate.py`

**Interfaces:**
- Produces: `GenerationRequest.from_candidate(candidate, upload) -> GenerationRequest`.
- Produces: `GenerationRequest.to_argv() -> list[str]`.
- Extends: `GenerationParams.source`, `memory_key_override`, `memory_category`, and
  `memory_people: tuple[str, ...]`.

- [ ] **Step 1: Write exhaustive adapter tests**

```python
def test_trip_request_uses_exact_range(trip_candidate: MemoryCandidate) -> None:
    argv = GenerationRequest.from_candidate(trip_candidate, upload=False).to_argv()
    start_index = argv.index("--start")
    assert argv[start_index : start_index + 4] == [
        "--start", "2026-05-03", "--end", "2026-05-11"
    ]
    assert "--source=auto" in argv
    assert f"--memory-key={trip_candidate.memory_key}" in argv

def test_unknown_category_fails_before_subprocess(candidate: MemoryCandidate) -> None:
    candidate = replace(candidate, category=cast(CandidateCategory, "unknown"))
    with pytest.raises(ValueError, match="Unsupported automation category"):
        GenerationRequest.from_candidate(candidate, upload=False)
```

Add one expected argv test per category.

- [ ] **Step 2: Run adapter tests and verify current fall-through behavior**

Run: `uv run pytest tests/test_generation_request.py -q`

Expected: FAIL because the typed adapter and hidden automation arguments do not exist.

- [ ] **Step 3: Implement the immutable request and hidden CLI context**

```python
@dataclass(frozen=True)
class GenerationRequest:
    memory_type: str
    category: CandidateCategory
    memory_key: str
    start: date
    end: date
    people: tuple[str, ...] = ()
    upload: bool = False
```

Use `match category` with an explicit case for every enum member and a final error. Add hidden
Click options `--source`, `--memory-key`, and `--memory-category`; default manual invocations
to `source=manual`.

- [ ] **Step 4: Carry identity into the pipeline tracker**

Add fields to `GenerationParams`, carry them through `run_pipeline_and_generate()`, prefer
`memory_key_override` in `_build_memory_key()`, and pass source/category/people into
`RunTracker.start_run()`.

- [ ] **Step 5: Select trip by exact start/end**

Extend `handle_trip_generation()` with `requested_start: date | None` and
`requested_end: date | None`. When present, select exactly one detected trip whose dates match;
no match raises `click.ClickException` and exits nonzero. Manual index/month/near-date behavior
remains unchanged.

- [ ] **Step 6: Run adapter, CLI, trip, and tracker tests**

Run: `uv run pytest tests/test_generation_request.py tests/integration/cli/test_generate.py tests/test_run_tracker.py -q`

Expected: PASS.

- [ ] **Step 7: Commit exact generation identity**

```bash
git add src/immich_memories/automation/generation_request.py src/immich_memories/cli/generate.py src/immich_memories/cli/_pipeline_runner.py src/immich_memories/cli/_trip_generation.py src/immich_memories/generate.py tests/test_generation_request.py tests/integration/cli/test_generate.py tests/test_run_tracker.py
git commit -m "fix: map automation candidates to exact generation requests"
```

### Task 6: Return typed outcomes and verify the exact output

**Files:**
- Modify: `src/immich_memories/automation/runner.py`
- Modify: `src/immich_memories/cli/auto_cmd.py`
- Modify: `tests/test_auto_runner.py`
- Modify: `tests/test_cli_smoke.py`

**Interfaces:**
- Consumes: `GenerationRequest`, `AutomationStateStore`, and identity query from Tasks 2 and 5.
- Produces: `AutoRunner.run_one(force=False, cooldown_hours=None, upload=False, dry_run=False) -> AutoRunResult`.
- Produces: `_execute_generate(argv) -> ProcessResult` with stdout/stderr and return code.
- Extends: `AutoRunner(config, execute: Callable[[list[str]], ProcessResult] | None = None)` for
  deterministic process-boundary tests; production defaults to the subprocess adapter.

- [ ] **Step 1: Write outcome and stale-output regression tests**

```python
def test_failed_process_is_failed_not_noop(runner) -> None:
    runner.execute = lambda argv: ProcessResult(7, "root cause on stdout", "")
    result = runner.run_one()
    assert result.outcome is AutoOutcome.FAILED
    assert "root cause on stdout" in result.error

def test_exit_zero_without_matching_new_run_is_failure(runner) -> None:
    seed_old_completed_run(runner.db, memory_key="other:key")
    runner.execute = lambda argv: ProcessResult(0, "", "")
    result = runner.run_one()
    assert result.outcome is AutoOutcome.FAILED
    assert result.output_path is None
```

Also test cooldown/no candidate/dry run as `SKIPPED`/`DRY_RUN`, and a matching new run with an
existing output as `COMPLETED`.

- [ ] **Step 2: Run focused tests and confirm `Path | None` ambiguity**

Run: `uv run pytest tests/test_auto_runner.py -q`

Expected: FAIL because current outcomes collapse to `None` and stale output is accepted.

- [ ] **Step 3: Implement typed orchestration**

Start an attempt before preflight. Store the start timestamp, chosen candidate, terminal
outcome, matching run ID, and sanitized error. Capture bounded tails from both stdout and
stderr. Timeout returns `FAILED` with a specific two-hour timeout message.

- [ ] **Step 4: Map outcomes to CLI output and exit codes**

Human output includes outcome and reason. `--quiet` emits one JSON object containing outcome,
reason, candidate key, run ID, and output path. A failed result calls `ctx.exit(1)` after
writing its error to stderr; skipped and dry-run exit zero.

- [ ] **Step 5: Run runner and CLI tests**

Run: `uv run pytest tests/test_auto_runner.py tests/test_cli_smoke.py -q`

Expected: PASS.

- [ ] **Step 6: Commit truthful outcomes**

```bash
git add src/immich_memories/automation/runner.py src/immich_memories/cli/auto_cmd.py tests/test_auto_runner.py tests/test_cli_smoke.py
git commit -m "fix: make automation outcomes truthful"
```

### Task 7: Scope cooldown/history to real automation and expose status

**Files:**
- Modify: `src/immich_memories/automation/runner.py`
- Modify: `src/immich_memories/cli/auto_cmd.py`
- Modify: `tests/test_auto_runner.py`
- Create: `tests/test_auto_status.py`

**Interfaces:**
- Consumes: `RunDatabase.list_runs(source="auto")` and `AutomationStateStore.get_last_attempt()`.
- Produces: `auto status --json` stable status object.

- [ ] **Step 1: Write cooldown/history/status regression tests**

```python
def test_manual_completion_does_not_trigger_auto_cooldown(runner) -> None:
    seed_completed_run(runner.db, source="manual", created_at=datetime.now())
    assert runner.is_within_cooldown(24) is False

def test_status_reports_last_attempt_and_rotation(cli_runner, config) -> None:
    result = cli_runner.invoke(main, ["auto", "status", "--json"], obj={"config": config})
    payload = json.loads(result.output)
    assert payload["last_attempt"]["outcome"] == "failed"
    assert payload["recent_categories"] == ["trip", "birthday"]
```

- [ ] **Step 2: Run focused tests and verify history/cooldown leakage**

Run: `uv run pytest tests/test_auto_runner.py tests/test_auto_status.py -q`

Expected: FAIL because cooldown uses any completed run and status does not exist.

- [ ] **Step 3: Implement source-scoped queries and status output**

Query source in SQL before applying `LIMIT`; do not filter an already-limited Python list.
Status includes installed scheduler detection where read-only detection is possible, last
attempt, last completed auto run, cooldown, recent categories, and rejection reasons.

- [ ] **Step 4: Run automation unit and live-suggest tests**

Run: `uv run pytest tests/test_auto_runner.py tests/test_auto_status.py tests/integration/automation/test_auto_suggest.py -q`

Expected: unit tests PASS; live tests PASS when their explicit Immich environment is present
and otherwise retain their existing external-environment skip.

- [ ] **Step 5: Commit scoped history and status**

```bash
git add src/immich_memories/automation/runner.py src/immich_memories/cli/auto_cmd.py tests/test_auto_runner.py tests/test_auto_status.py
git commit -m "feat: report scoped automation status"
```

### Task 8: Verify the complete automation slice

**Files:**
- Modify only files needed for failures found by these commands.

**Interfaces:**
- Produces: green P0 automation checkpoint consumed by the Immich compatibility plan.

- [ ] **Step 1: Run all automation and tracking tests**

Run: `uv run pytest tests/test_auto_runner.py tests/test_auto_status.py tests/test_automation_state.py tests/test_automation_variety.py tests/test_generation_request.py tests/test_run_database_fk.py tests/test_run_tracker.py -q`

Expected: PASS.

- [ ] **Step 2: Run static checks on touched code**

```bash
uv run ruff check src/immich_memories/automation src/immich_memories/cli/auto_cmd.py src/immich_memories/tracking tests/test_auto_runner.py tests/test_auto_status.py tests/test_automation_state.py tests/test_automation_variety.py tests/test_generation_request.py
uv run ruff format --check src/immich_memories/automation src/immich_memories/cli/auto_cmd.py src/immich_memories/tracking tests/test_auto_runner.py tests/test_auto_status.py tests/test_automation_state.py tests/test_automation_variety.py tests/test_generation_request.py
uv run mypy src/immich_memories/automation src/immich_memories/tracking
uv run lint-imports
```

Expected: every command exits zero.

- [ ] **Step 3: Perform a non-mutating real dry run**

Run: `uv run immich-memories auto run --dry-run`

Expected: one candidate or a healthy skip is explained; no subprocess starts and no video is
created. Do not reactivate the LaunchAgent.

- [ ] **Step 4: Route any correction back through its owning task**

If verification exposes a defect, return to that task's failing test, make it RED for the
specific defect, and use that task's explicit staging list and commit. Do not create a broad
verification commit.

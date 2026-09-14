"""The automation runner's eligible memories and its reasons for passing over others."""

import asyncio
from typing import Any

from nicegui import run, ui

from immich_memories.automation.models import AutomationAttempt, AutoOutcome
from immich_memories.automation.runner import (
    AutomationAlreadyRunningError,
    AutoRunner,
    StartedAutoRun,
)
from immich_memories.automation.state_store import AutomationStateStore
from immich_memories.security import sanitize_error_message
from immich_memories.tracking import RunDatabase
from immich_memories.ui.components import im_card

# What the attempt history records for a run somebody started from this page, so
# `auto status` and /health tell a browser click apart from the nightly wake.
SUGGESTION_REASON = "web suggestion"

# asyncio keeps only a weak reference to a bare task, so a generation nobody
# awaits can be collected mid-render. These are the strong ones.
_running: set[asyncio.Task[Any]] = set()

_RULES = {
    "same_category_as_previous": "The previous automatic memory used this category.",
    "category_limit_two_of_six": "This category already appears twice in the last six memories.",
    "monthly_review_already_completed_this_month": "A monthly review already finished this month.",
    "person_in_last_two_person_runs": "These people appear in the last two people memories.",
}


def _start(config) -> StartedAutoRun | None:
    try:
        return AutoRunner(config).start_one(reason=SUGGESTION_REASON)
    except AutomationAlreadyRunningError:
        return None


def _submit(started: StartedAutoRun, *, candidate_key: str, dry_run: bool) -> None:
    """Execute the accepted decision off the page, the way the HTTP trigger does.

    A generation blocks for up to two hours. Awaiting it here would hold a worker
    and leave the page with nothing to show but one static line.
    """
    task = asyncio.create_task(
        asyncio.to_thread(started.execute, candidate_key=candidate_key, dry_run=dry_run)
    )
    _running.add(task)
    task.add_done_callback(_running.discard)


def _read_attempt(config, attempt_id: str) -> AutomationAttempt | None:
    return AutomationStateStore(config.cache.database_path).get_attempt(attempt_id)


def _run_id_for(config, attempt: AutomationAttempt) -> str | None:
    """The run to open: the one the attempt recorded, else the one it started.

    A failed generation finishes with no run id on the attempt, but the child
    opened its run record before it failed, and that is where its output lives.
    """
    if attempt.run_id:
        return attempt.run_id
    record = RunDatabase(config.cache.database_path).get_run_by_automation_attempt(attempt.id)
    return record.run_id if record else None


def _finished_text(attempt: AutomationAttempt) -> str:
    if attempt.outcome is AutoOutcome.DRY_RUN:
        return "Eligible. No video was generated."
    headline = f"{attempt.outcome.value.replace('_', ' ').capitalize()}: {attempt.reason}"
    if attempt.error and attempt.error != attempt.reason:
        return f"{headline}\n{attempt.error}"
    return headline


class SuggestionsPage:
    """Own one page's controls while discovery and generation run off the UI thread."""

    def __init__(self, config) -> None:
        self.config = config
        ui.label("Memories automation would make next, using the same rules as auto suggest.")
        ui.label(
            "Running a suggestion creates a video on the server. "
            + (
                "Automatic upload to Immich is enabled."
                if self.config.automation.upload_to_immich
                else "Automatic upload to Immich is off."
            )
        ).classes("text-sm")
        self.refresh = ui.button("Refresh suggestions")
        self.result_label = ui.label().classes("whitespace-pre-wrap")
        self.result_actions = ui.row()
        self.content = ui.column().classes("w-full")
        self.attempt_id: str | None = None
        self.loading = False
        self.watch = ui.timer(1.0, self._follow, active=False)

        self.refresh.on_click(self._load)
        ui.timer(0.1, self._load, once=True)

    def _busy(self, message: str) -> None:
        self.refresh.disable()
        self.result_actions.clear()
        self.content.props("inert")
        self.result_label.set_text(message)

    def _settle(self, message: str) -> None:
        self.result_label.set_text(message)
        self.refresh.enable()
        self.content.props(remove="inert")

    async def _execute(self, key: str, *, dry_run: bool) -> None:
        self._busy("Checking eligibility…" if dry_run else "Starting on the server…")
        started = await run.io_bound(_start, self.config)
        if started is None:
            self._settle("Automation is already running. Open Runs to follow it.")
            return
        self.attempt_id = started.attempt.id
        _submit(started, candidate_key=key, dry_run=dry_run)
        self.watch.activate()

    async def _follow(self) -> None:
        if self.attempt_id is None:
            return
        attempt = await run.io_bound(_read_attempt, self.config, self.attempt_id)
        if attempt is None:
            return
        if attempt.outcome is AutoOutcome.RUNNING:
            phase = attempt.last_phase.label.lower() if attempt.last_phase else "starting"
            self.result_label.set_text(
                f"Running on the server: {phase}. It continues if you leave this page."
            )
            return
        self.watch.deactivate()
        self.attempt_id = None
        if run_id := await run.io_bound(_run_id_for, self.config, attempt):
            with self.result_actions:
                ui.link("Open run", f"/runs?run_id={run_id}")
        self._settle(_finished_text(attempt))

    async def _load(self) -> None:
        # The first load is a timer and the button is another entry point, so two
        # can overlap; the second would clear an empty column and append a duplicate.
        if self.loading:
            return
        self.loading = True
        self.refresh.disable()
        self.content.clear()
        self.result_label.set_text("Looking through the library…")
        runner = AutoRunner(self.config)
        try:
            candidates = await run.io_bound(runner.suggest, limit=20) or []
            self.result_label.set_text("")
            with self.content:
                if runner.last_suggest_status.error:
                    ui.label(f"Discovery failed: {runner.last_suggest_status.error}")
                elif not candidates:
                    ui.label("No eligible suggestions right now.")
                for candidate in candidates:
                    _candidate_card(candidate, self._execute)
                _skipped_candidates(runner)
        except Exception as exc:
            self.result_label.set_text(
                f"Could not load suggestions: {sanitize_error_message(str(exc))}"
            )
        finally:
            self.loading = False
            self.refresh.enable()


def _candidate_card(candidate, execute) -> None:
    with im_card().classes("w-full suggestion-card"):
        ui.label(candidate.reason).classes("text-lg font-semibold")
        if candidate.person_names:
            ui.label(", ".join(candidate.person_names)).classes("font-medium")
        ui.label(
            f"{candidate.date_range_start} to {candidate.date_range_end} · {candidate.asset_count} pictures · {candidate.category.value.replace('_', ' ')}"
        )
        with ui.expansion("Candidate key").classes("w-full"):
            ui.label(candidate.memory_key).classes("break-all font-mono text-xs")
        with ui.row():
            ui.button(
                "Check eligibility",
                on_click=lambda key=candidate.memory_key: execute(key, dry_run=True),
            )
            ui.button(
                "Run this suggestion",
                on_click=lambda key=candidate.memory_key: execute(key, dry_run=False),
            )


def _skipped_candidates(runner: AutoRunner) -> None:
    with ui.expansion("Why other suggestions were skipped").classes("w-full"):
        rejected = runner.last_variety_decision.rejected
        if not rejected and not runner.last_backoff_skips:
            ui.label("No suggestions were rejected by variety or failure backoff.")
        for item in rejected:
            ui.label(
                f"{item.candidate.reason}: {_RULES.get(item.rule, item.rule.replace('_', ' '))}"
            )
        for key, reason in runner.last_backoff_skips.items():
            ui.label(f"{key}: {reason}").classes("break-all")

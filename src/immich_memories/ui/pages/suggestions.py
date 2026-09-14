"""The automation runner's eligible memories and its reasons for passing over others."""

from nicegui import run, ui

from immich_memories.automation.models import AutoOutcome
from immich_memories.automation.runner import AutoRunner
from immich_memories.security import sanitize_error_message
from immich_memories.ui.components import im_card

_RULES = {
    "same_category_as_previous": "The previous automatic memory used this category.",
    "category_limit_two_of_six": "This category already appears twice in the last six memories.",
    "monthly_review_already_completed_this_month": "A monthly review already finished this month.",
    "person_in_last_two_person_runs": "These people appear in the last two people memories.",
}


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

        self.refresh.on_click(self._load)
        ui.timer(0.1, self._load, once=True)

    async def _execute(self, key: str, *, dry_run: bool) -> None:
        self.refresh.disable()
        self.result_actions.clear()
        self.content.props("inert")
        self.result_label.set_text(
            "Checking eligibility…"
            if dry_run
            else "Generating on the server… The result will appear in Runs."
        )
        try:
            result = await run.io_bound(
                AutoRunner(self.config).run_one, candidate_key=key, dry_run=dry_run
            )
            if result is None:
                return
            self.result_label.set_text(
                "Eligible. No video was generated."
                if result.outcome is AutoOutcome.DRY_RUN
                else f"{result.outcome.value.replace('_', ' ').capitalize()}: {result.reason}"
            )
            if result.run_id:
                with self.result_actions:
                    ui.link("Open run", f"/runs?run_id={result.run_id}")
        finally:
            self.refresh.enable()
            self.content.props(remove="inert")

    async def _load(self) -> None:
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

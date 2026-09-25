"""Run history reads the same database, run index and storyboard as the terminal."""

from urllib.parse import urlencode

from nicegui import ui

from immich_memories.config import get_config
from immich_memories.operations.auto_output import output_log_path
from immich_memories.operations.run_index import attempt_dir_for_run
from immich_memories.operations.storyboard import read_storyboard, storyboard_lines
from immich_memories.tracking import RunDatabase
from immich_memories.ui.components import im_card
from immich_memories.ui.i18n import N_, tr, tr_options
from immich_memories.ui.pages.memory_brief import MEMORY_TYPE_LABELS

_PAGE_SIZE = 20


def _run_details(db: RunDatabase, run_id: str) -> None:
    record = db.get_run(run_id)
    ui.link(tr("Back to runs"), "/runs")
    if record is None:
        ui.label(tr("Run not found. It may have been removed."))
        return
    config = get_config()
    ui.label(record.run_id).classes("text-lg font-semibold")
    ui.label(
        f"{tr(record.status).capitalize()} · {record.created_at:%Y-%m-%d %H:%M} · {record.source}"
    )
    ui.label(
        tr(
            "{clips_selected} pictures selected · {total_duration_seconds:.1f}s recorded run time",
            clips_selected=record.clips_selected,
            total_duration_seconds=record.total_duration_seconds,
        )
    )
    if record.output_path:
        ui.label(tr("Saved to: {output_path}", output_path=record.output_path)).classes("break-all")
    ui.label(tr("Immich delivery: {value}", value=record.delivery_status.value.replace("_", " ")))
    for warning in record.warnings:
        ui.label(warning).classes("text-sm")
    for phase in record.phases:
        ui.label(
            tr(
                "{value}: {duration_seconds:.1f}s",
                value=phase.phase_name.replace("_", " "),
                duration_seconds=phase.duration_seconds,
            )
        )
        for error in phase.errors:
            ui.label(str(error)).classes("whitespace-pre-wrap break-all text-sm")
    attempt = attempt_dir_for_run(config.cache.cache_path, record.run_id)
    board = read_storyboard(attempt) if attempt else None
    if board:
        with ui.expansion(tr("Read the cut"), value=True).classes("w-full"):
            ui.label(board.thesis)
            ui.label(board.summary_label)
            ui.label("\n".join(storyboard_lines(board))).classes(
                "whitespace-pre-wrap font-mono text-sm"
            )
    else:
        ui.label(tr("No saved cut is available for this run."))
    if record.automation_attempt_id:
        # `generate --automation-attempt-id` accepts any string, and only an id
        # automation itself opened addresses a transcript.
        try:
            log = output_log_path(config.cache.cache_path, record.automation_attempt_id)
        except ValueError:
            log = None
        if log is not None and log.is_file():
            ui.button(tr("Download child output"), on_click=lambda: ui.download.file(log))
        else:
            ui.label(tr("No child output was retained for this run."))


def render_runs(run_id: str | None = None, status: str = "all", offset: int = 0) -> None:
    """List twenty durable runs at a time; details survive navigation and server restarts."""
    db = RunDatabase(get_config().cache.database_path)
    if run_id:
        _run_details(db, run_id)
        return
    ui.label(
        tr("Manual and automatic runs, including failures. Open a run to read its cut and timings.")
    )
    offset = max(0, offset)
    statuses = [
        N_("all"),
        N_("completed"),
        N_("failed"),
        N_("running"),
        N_("cancelled"),
        N_("interrupted"),
    ]
    status = status if status in statuses else "all"
    ui.select(
        tr_options(statuses),
        value=status,
        label=tr("Status"),
        on_change=lambda e: ui.navigate.to("/runs?" + urlencode({"status": e.value})),
    )
    records = db.list_runs(
        limit=_PAGE_SIZE + 1, offset=offset, status=None if status == "all" else status
    )
    if not records:
        ui.label(tr("No runs match this view. Make a memory from Memory or Suggestions."))
    for record in records[:_PAGE_SIZE]:
        with im_card().classes("w-full run-row"):
            ui.link(record.run_id, "/runs?" + urlencode({"run_id": record.run_id}))
            ui.label(f"{record.created_at:%Y-%m-%d %H:%M} · {tr(record.status)} · {record.source}")
            ui.label(tr(MEMORY_TYPE_LABELS.get(record.memory_type or "custom", "Memory")))
            if record.date_range_start and record.date_range_end:
                ui.label(
                    tr(
                        "{date_range_start} to {date_range_end}",
                        date_range_start=record.date_range_start,
                        date_range_end=record.date_range_end,
                    )
                )
    with ui.row():
        ui.button(
            tr("Previous runs"),
            on_click=lambda: ui.navigate.to(
                "/runs?" + urlencode({"status": status, "offset": max(0, offset - _PAGE_SIZE)})
            ),
        ).set_enabled(offset > 0)
        ui.button(
            tr("Next runs"),
            on_click=lambda: ui.navigate.to(
                "/runs?" + urlencode({"status": status, "offset": offset + _PAGE_SIZE})
            ),
        ).set_enabled(len(records) > _PAGE_SIZE)
        ui.button(tr("Refresh runs"), on_click=ui.navigate.reload)

"""Run history reads the same database, run index and storyboard as the terminal."""

from urllib.parse import urlencode

from nicegui import ui

from immich_memories.config import get_config
from immich_memories.operations.auto_output import output_log_path
from immich_memories.operations.run_index import attempt_dir_for_run
from immich_memories.operations.storyboard import read_storyboard, storyboard_lines
from immich_memories.tracking import RunDatabase
from immich_memories.ui.components import im_card

_PAGE_SIZE = 20


def _run_details(db: RunDatabase, run_id: str) -> None:
    record = db.get_run(run_id)
    ui.link("Back to runs", "/runs")
    if record is None:
        ui.label("Run not found. It may have been removed.")
        return
    config = get_config()
    ui.label(record.run_id).classes("text-lg font-semibold")
    ui.label(f"{record.status.capitalize()} · {record.created_at:%Y-%m-%d %H:%M} · {record.source}")
    ui.label(
        f"{record.clips_selected} pictures selected · {record.total_duration_seconds:.1f}s recorded run time"
    )
    if record.output_path:
        ui.label(f"Saved to: {record.output_path}").classes("break-all")
    ui.label(f"Immich delivery: {record.delivery_status.value.replace('_', ' ')}")
    for warning in record.warnings:
        ui.label(warning).classes("text-sm")
    for phase in record.phases:
        ui.label(f"{phase.phase_name.replace('_', ' ')}: {phase.duration_seconds:.1f}s")
        for error in phase.errors:
            ui.label(str(error)).classes("whitespace-pre-wrap break-all text-sm")
    attempt = attempt_dir_for_run(config.cache.cache_path, record.run_id)
    board = read_storyboard(attempt) if attempt else None
    if board:
        with ui.expansion("Read the cut", value=True).classes("w-full"):
            ui.label(board.thesis)
            ui.label(board.summary_label)
            ui.label("\n".join(storyboard_lines(board))).classes(
                "whitespace-pre-wrap font-mono text-sm"
            )
    else:
        ui.label("No saved cut is available for this run.")
    if record.automation_attempt_id:
        log = output_log_path(config.cache.cache_path, record.automation_attempt_id)
        if log.is_file():
            ui.button("Download child output", on_click=lambda: ui.download.file(log))
        else:
            ui.label("No child output was retained for this run.")


def render_runs(run_id: str | None = None, status: str = "all", offset: int = 0) -> None:
    """List twenty durable runs at a time; details survive navigation and server restarts."""
    db = RunDatabase(get_config().cache.database_path)
    if run_id:
        _run_details(db, run_id)
        return
    ui.label(
        "Manual and automatic runs, including failures. Open a run to read its cut and timings."
    )
    offset = max(0, offset)
    statuses = ["all", "completed", "failed", "running", "cancelled", "interrupted"]
    status = status if status in statuses else "all"
    ui.select(
        statuses,
        value=status,
        label="Status",
        on_change=lambda e: ui.navigate.to("/runs?" + urlencode({"status": e.value})),
    )
    records = db.list_runs(
        limit=_PAGE_SIZE + 1, offset=offset, status=None if status == "all" else status
    )
    if not records:
        ui.label("No runs match this view. Make a memory from Memory or Suggestions.")
    for record in records[:_PAGE_SIZE]:
        with im_card().classes("w-full run-row"):
            ui.link(record.run_id, "/runs?" + urlencode({"run_id": record.run_id}))
            ui.label(f"{record.created_at:%Y-%m-%d %H:%M} · {record.status} · {record.source}")
            ui.label((record.memory_type or "Custom memory").replace("_", " "))
            if record.date_range_start and record.date_range_end:
                ui.label(f"{record.date_range_start} to {record.date_range_end}")
    with ui.row():
        ui.button(
            "Previous runs",
            on_click=lambda: ui.navigate.to(
                "/runs?" + urlencode({"status": status, "offset": max(0, offset - _PAGE_SIZE)})
            ),
        ).set_enabled(offset > 0)
        ui.button(
            "Next runs",
            on_click=lambda: ui.navigate.to(
                "/runs?" + urlencode({"status": status, "offset": offset + _PAGE_SIZE})
            ),
        ).set_enabled(len(records) > _PAGE_SIZE)
        ui.button("Refresh runs", on_click=ui.navigate.reload)

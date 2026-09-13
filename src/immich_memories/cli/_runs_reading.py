"""Reading a finished cut from the terminal: the storyboard, and one picture's fate.

Both commands resolve a run the same way the web UI does after a cut: the run id
leads to the attempt directory through the run index, and the attempt directory
holds the plan, the render projection and the selection trace the run wrote.
"""

from __future__ import annotations

import shutil
import sys
import textwrap
from pathlib import Path

import click

from immich_memories.analysis.selection_trace import ClipStory, Trace
from immich_memories.cli._helpers import console, print_error
from immich_memories.operations.reader_words import stage_words
from immich_memories.operations.run_index import attempt_dir_for_run
from immich_memories.operations.storyboard import (
    TRACE_FILE,
    Storyboard,
    read_storyboard,
    storyboard_lines,
)


class RunNotFound(LookupError):
    """The run id names nothing this cache can read."""


def resolve_attempt(cache_dir: Path, db, run_id: str | None) -> tuple[str, Path]:
    """The run id and attempt directory to read, from an id, a prefix, a path, or the latest run.

    `db` is the run database (`RunDatabase`); it is only asked when the argument
    is not already a directory on disk.
    """
    if run_id and Path(run_id).is_dir():
        return Path(run_id).name, Path(run_id)
    resolved = _resolve_run_id(db, run_id)
    attempt = attempt_dir_for_run(cache_dir, resolved)
    if attempt is None:
        raise RunNotFound(
            f"Run {resolved} left no cut to read: it was made before this version, "
            "stopped before selection, or its cache directory is gone."
        )
    return resolved, attempt


def _resolve_run_id(db, run_id: str | None) -> str:
    if run_id and db.get_run(run_id):
        return run_id
    recent = db.list_runs(limit=100, status=None if run_id else "completed")
    if run_id:
        matches = [run.run_id for run in recent if run.run_id.startswith(run_id)]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise RunNotFound(f"Ambiguous run id {run_id}: matches {', '.join(matches)}")
        raise RunNotFound(f"Run not found: {run_id}")
    if not recent:
        raise RunNotFound("No completed run yet: make a memory first.")
    return recent[0].run_id


def storyboard_text(run_id: str, board: Storyboard | None) -> str:
    """The storyboard as the terminal prints it."""
    if board is None or not board.shots:
        return f"Run {run_id} has no storyboard: its attempt directory holds no plan."
    lines = [f"Run {run_id}: {board.summary_label}"]
    if board.thesis:
        lines.append(f"  {board.thesis}")
    lines.append("")
    lines.extend(storyboard_lines(board))
    return "\n".join(lines)


def read_trace(attempt_dir: Path) -> Trace | None:
    """The decision log the run wrote beside its plan, or None when it left none."""
    import json

    path = Path(attempt_dir) / TRACE_FILE
    if not path.is_file():
        return None
    return Trace.from_dict(json.loads(path.read_text()))


def why_text(
    asset_id: str, story: ClipStory, board: Storyboard | None, width: int | None = None
) -> str:
    """One picture's fate, in the order the editor decided it, in reader words.

    Long reasons wrap at the terminal width (or the width given), indented under
    their line, so a paragraph of judgement stays readable in a narrow window.
    """
    columns = width or shutil.get_terminal_size((100, 20)).columns
    shot = next((s for s in (board.shots if board else ()) if s.asset_id == asset_id), None)
    lines = [f"{asset_id}: {story.facts}".rstrip(": ")]
    if story.survived:
        lines.append(
            _wrapped(f"passed {', '.join(stage_words(s) for s in story.survived)}", columns)
        )
    if story.dropped_at:
        reason = f": {story.reason}" if story.reason else ""
        lines.append(_wrapped(f"left out at {stage_words(story.dropped_at)}{reason}", columns))
    if story.admitted_at:
        lines.append(_wrapped(f"kept at {stage_words(story.admitted_at)}", columns))
    if shot is not None:
        lines.append(
            _wrapped(
                f"in the cut at {shot.timecode}, {shot.day}, story: {shot.story_title}"
                + (f", because {shot.reason}" if shot.reason else ""),
                columns,
            )
        )
    elif story.shipped:
        lines.append("  in the cut")
    elif not story.dropped_at and not story.survived:
        lines.append("  never reached the editor: not in this run's pool")
    return "\n".join(lines)


def _wrapped(text: str, columns: int) -> str:
    return textwrap.fill(
        text, width=max(columns, 20), initial_indent="  ", subsequent_indent="    "
    )


def register_reading_commands(runs: click.Group) -> None:
    """`runs story` and `runs why`: the terminal reads a finished cut."""

    @runs.command("story")
    @click.argument("run_id", required=False)
    def runs_story(run_id: str | None) -> None:
        """Print the cut of a run in the order it plays: day, kind, length, story, reason.

        With no RUN_ID the most recent completed run is read. A run id prefix
        works, and so does the path of an attempt directory.
        """
        from immich_memories.config import get_config
        from immich_memories.tracking import RunDatabase

        config = get_config()
        db = RunDatabase(db_path=config.cache.database_path)
        try:
            resolved, attempt = resolve_attempt(config.cache.cache_path, db, run_id)
        except RunNotFound as exc:
            print_error(str(exc))
            sys.exit(1)
        console.print(storyboard_text(resolved, read_storyboard(attempt)), highlight=False)

    @runs.command("why")
    @click.argument("asset_id")
    @click.option("--run", "run_id", default=None, help="Run id or prefix (default: latest)")
    def runs_why(asset_id: str, run_id: str | None) -> None:
        """Say what a run decided about one picture: where it passed, where it was dropped, and why."""
        from immich_memories.config import get_config
        from immich_memories.tracking import RunDatabase

        config = get_config()
        db = RunDatabase(db_path=config.cache.database_path)
        try:
            resolved, attempt = resolve_attempt(config.cache.cache_path, db, run_id)
        except RunNotFound as exc:
            print_error(str(exc))
            sys.exit(1)
        trace = read_trace(attempt)
        if trace is None:
            print_error(f"Run {resolved} left no decision log; it predates this version.")
            sys.exit(1)
        console.print(
            why_text(asset_id, trace.story_of(asset_id), read_storyboard(attempt)), highlight=False
        )

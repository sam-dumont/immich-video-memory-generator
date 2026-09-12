"""A long stage publishes numbers and the pictures it just finished, not only a sentence."""

from __future__ import annotations

import json
from pathlib import Path

from immich_memories.operations.cut_progress import (
    PROGRESS_FILE,
    RECENT_ASSET_LIMIT,
    StageProgress,
    StageProgressWriter,
    read_stage_progress,
)


def test_a_published_stage_reads_back_as_numbers_and_recent_pictures(tmp_path: Path) -> None:
    writer = StageProgressWriter(lambda: tmp_path)
    writer.note_asset("asset-1")
    writer.note_asset("asset-2")

    writer.publish("previews", 2, 10)

    progress = read_stage_progress(tmp_path)
    assert progress == StageProgress("previews", 2, 10, ("asset-1", "asset-2"))
    assert progress.fraction == 0.2
    # One formatter for the sentence, so a reader can match it against the row.
    assert progress.stage_label == "Preparing previews: 2/10"


def test_the_picture_window_is_bounded_however_long_the_run(tmp_path: Path) -> None:
    """A library of a hundred thousand writes the same few hundred bytes as one of ten."""
    writer = StageProgressWriter(lambda: tmp_path)
    for index in range(RECENT_ASSET_LIMIT * 40):
        writer.note_asset(f"asset-{index}")

    writer.publish("previews", 480, 10_793)

    progress = read_stage_progress(tmp_path)
    assert len(progress.recent_asset_ids) == RECENT_ASSET_LIMIT
    assert progress.recent_asset_ids[-1] == f"asset-{RECENT_ASSET_LIMIT * 40 - 1}"
    assert len(json.dumps(progress.recent_asset_ids)) < 1000


def test_noting_pictures_never_writes_and_publishing_overwrites(tmp_path: Path) -> None:
    """The per-asset path stays in memory; the file is a snapshot, never a log."""
    writer = StageProgressWriter(lambda: tmp_path)
    writer.note_asset("asset-1")

    assert read_stage_progress(tmp_path) is None

    writer.publish("previews", 1, 10)
    writer.publish("pixels", 4, 10)

    assert read_stage_progress(tmp_path).label == "pixels"
    assert len(list(tmp_path.glob("*"))) == 1


def test_a_total_that_says_nothing_leaves_the_bar_indeterminate(tmp_path: Path) -> None:
    writer = StageProgressWriter(lambda: tmp_path)
    writer.publish("previews", 0, 0)

    assert read_stage_progress(tmp_path).fraction is None


def test_a_missing_or_unreadable_record_is_simply_no_progress(tmp_path: Path) -> None:
    assert read_stage_progress(None) is None
    assert read_stage_progress(tmp_path) is None

    (tmp_path / PROGRESS_FILE).write_text("{not json")
    assert read_stage_progress(tmp_path) is None

    (tmp_path / PROGRESS_FILE).write_text(json.dumps({"label": "previews"}))
    assert read_stage_progress(tmp_path) is None


def test_a_publish_that_cannot_write_does_not_take_the_cut_down(tmp_path: Path) -> None:
    """A display file is never worth failing a run that is otherwise fine."""
    writer = StageProgressWriter(lambda: tmp_path / "gone" / "deeper")
    (tmp_path / "gone").write_text("a file where a directory would have to be")

    writer.publish("previews", 1, 10)

"""An honest empty period ends calmly: nothing worth a film is an answer, not an error."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.analysis.smart_pipeline import PipelineResult
from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange
from tests.conftest import make_clip


def _run_with_nothing_kept(tmp_path, date_range: DateRange, candidates: list) -> None:
    from immich_memories.cli._pipeline_runner import run_pipeline_and_generate

    result = PipelineResult(selected_clips=[], clip_segments={}, errors=[])
    config = Config(
        cache={"database": str(tmp_path / "analysis.db"), "directory": str(tmp_path / "cache")}
    )
    with (
        # WHY: stands in for clip conversion, so the pool is exactly `candidates`.
        patch("immich_memories.generate_clips.assets_to_clips", return_value=candidates),
        # WHY: the selection pipeline; it reads the pool and keeps nothing.
        patch("immich_memories.analysis.editorial_runtime.build_smart_pipeline") as pipeline_type,
        # WHY: the FFmpeg render, which must never be reached here.
        patch("immich_memories.generate.generate_memory") as generate,
    ):
        pipeline_type.return_value.run_editorial_source.return_value = (candidates, result)
        pipeline_type.return_value.last_deep_analysis_count = 0
        try:
            run_pipeline_and_generate(
                assets=[c.asset for c in candidates],
                client=MagicMock(),
                config=config,
                progress=MagicMock(),
                duration=60.0,
                transition="cut",
                music=None,
                no_music=True,
                output_path=tmp_path / "memory.mp4",
                memory_type="monthly_highlights",
                person_names=[],
                date_range=date_range,
                upload_to_immich=False,
                album=None,
            )
        finally:
            generate.assert_not_called()


FEBRUARY = DateRange(start=datetime(2019, 2, 1), end=datetime(2019, 2, 28, 23, 59, 59))


def test_a_month_the_editor_keeps_nothing_from_says_so_and_exits_zero(tmp_path, capsys):
    candidates = [make_clip("floor-1", duration=5.0), make_clip("floor-2", duration=5.0)]

    with pytest.raises(SystemExit) as stopped:
        _run_with_nothing_kept(tmp_path, FEBRUARY, candidates)

    said = capsys.readouterr().out
    assert stopped.value.code == 0
    assert "Nothing worth a film in February 2019" in said
    assert "Error" not in said


def test_an_empty_pool_is_still_an_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as stopped:
        _run_with_nothing_kept(tmp_path, FEBRUARY, [])

    assert stopped.value.code == 1
    assert "Error" in capsys.readouterr().out

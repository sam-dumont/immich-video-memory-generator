"""A stage's measured rate survives the disk handoff to both surfaces."""

from immich_memories.analysis.editorial_projection import EditorialStageReporter
from immich_memories.analysis.progress import ProgressTracker
from immich_memories.cli._pipeline_runner import _SourceProgressReporter
from immich_memories.operations.cut_progress import StageUpdate
from immich_memories.operations.editorial_attempt import EditorialAttempt, read_editorial_attempt
from immich_memories.ui.pages.memory_run import live_progress_of
from tests.test_surface_parity import CountingDisplay


def test_estimate_uses_only_current_stage_and_survives_reload(tmp_path, monkeypatch):
    now = 100.0
    monkeypatch.setattr("time.monotonic", lambda: now)
    display = CountingDisplay()
    reporter = EditorialStageReporter(ProgressTracker(), _SourceProgressReporter(display, 0))
    with EditorialAttempt(tmp_path, request={}) as attempt:
        reporter(attempt.stage(StageUpdate("previews", "analysis", 2, 10)))
        assert "left" not in display.description
        now += 12
        reporter(attempt.stage(StageUpdate("previews", "analysis", 4, 10)))
        reloaded = live_progress_of(read_editorial_attempt(attempt.directory))
        assert reloaded.remaining_seconds == 36
        assert reloaded.remaining_label == "~36s left in this stage"
        assert reloaded.remaining_label in display.description
        # Same total, different work: no preview rate leaks into captions.
        reporter(attempt.stage(StageUpdate("captions", "analysis", 4, 10)))
        assert "left" not in display.description
        now += 60
        reporter(attempt.stage(StageUpdate("captions", "analysis", 5, 10)))
        assert "~5m left in this stage" in display.description
        # A repeated update retains its saved estimate; it is not new measured work.
        now += 20
        reporter(attempt.stage(StageUpdate("captions", "analysis", 5, 10)))
        assert "~5m left in this stage" in display.description
        reporter(attempt.stage(StageUpdate("captions", "analysis", 1, 10)))
        assert "left" not in display.description
        now += 10
        reporter(attempt.stage(StageUpdate("captions", "analysis", 10, 10)))
        assert "left" not in display.description
        reporter(attempt.stage(StageUpdate("Reading the story")))
        assert "left" not in display.description

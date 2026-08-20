"""Tests for sidebar step completion logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories.tracking import DeliveryStatus
from immich_memories.ui.app import _is_step_complete
from immich_memories.ui.state import AppState


class TestIsStepComplete:
    """Verify step completion checks against AppState fields."""

    def test_step1_incomplete_when_no_config(self) -> None:
        state = AppState()
        assert _is_step_complete(state, 1) is False

    def test_step1_incomplete_when_config_but_no_date_range(self) -> None:
        state = AppState()
        state.config = object()  # type: ignore[assignment]
        assert _is_step_complete(state, 1) is False

    def test_step1_complete_when_config_and_date_range_set(self) -> None:
        state = AppState()
        state.config = object()  # type: ignore[assignment]
        state.date_range = object()  # type: ignore[assignment]
        assert _is_step_complete(state, 1) is True

    def test_step2_incomplete_when_no_clips_selected(self) -> None:
        state = AppState()
        assert _is_step_complete(state, 2) is False

    def test_step2_complete_when_clips_selected(self) -> None:
        state = AppState()
        state.selected_clip_ids = {"clip-1", "clip-2"}
        assert _is_step_complete(state, 2) is True

    def test_step3_incomplete_when_no_options(self) -> None:
        state = AppState()
        assert _is_step_complete(state, 3) is False

    def test_step3_complete_when_options_set(self) -> None:
        state = AppState()
        state.generation_options = {"resolution": "1080p"}
        assert _is_step_complete(state, 3) is True

    @pytest.mark.parametrize("step", [4, 5, 0, -1])
    def test_other_steps_always_incomplete(self, step: int) -> None:
        state = AppState()
        assert _is_step_complete(state, step) is False


class _Element:
    def classes(self, *_args, **_kwargs):
        return self

    def style(self, *_args, **_kwargs):
        return self


def test_step4_rerender_keeps_warning_delivery_truth_and_video_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returning to Step 4 must not turn durable outcomes back into transient toasts."""
    from immich_memories.ui.pages import step4_export

    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated-video")
    warning = "Optional music failed: backend unavailable"
    state = AppState(
        output_path=output_path,
        generation_warning=warning,
        delivery_status=DeliveryStatus.PENDING,
    )
    labels: list[str] = []
    shown_videos: list[Path] = []

    monkeypatch.setattr(step4_export, "im_section_header", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        step4_export.ui,
        "label",
        lambda value, **_kwargs: (labels.append(value), _Element())[1],
    )
    monkeypatch.setattr(
        step4_export.ui,
        "video",
        lambda value, **_kwargs: (shown_videos.append(value), _Element())[1],
    )

    step4_export._render_existing_result(state)

    assert f"Saved to: {output_path}" in labels
    assert warning in labels
    assert "Immich delivery: Pending" in labels
    # The element is handed the file, not a pre-registered URL: NiceGUI owns
    # the route's lifetime that way.
    assert shown_videos == [output_path]

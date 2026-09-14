"""Tests for sidebar step completion logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories.tracking import DeliveryStatus
from immich_memories.ui.state import AppState


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

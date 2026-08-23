"""The wizard's music comes from the shared phase, with the run's mute windows.

The UI used to run its own music implementation, which is how it lost mute
windows (#479), beat-fit cadence and the bundled fallback while the CLI kept
them. The windows are the part that cannot be recovered on the UI side: the
assembly engine is the only place the final sequence and its transitions
coexist, so they have to travel out of it on PreparedGeneration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams, PreparedGeneration
from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec
from immich_memories.processing.output_contract import OutputProbe
from immich_memories.tracking import RunTracker
from immich_memories.ui.state import AppState
from tests.conftest import make_clip

_WINDOWS = [(3.0, 4.2), (11.5, 12.0)]


def _plan() -> EncodingPlan:
    return EncodingPlan(
        codec=OutputCodec.H264,
        encoder="libx264",
        encoder_args=("-c:v", "libx264"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
    )


def _probe() -> OutputProbe:
    return OutputProbe(
        codec="h264",
        container="mp4",
        duration_seconds=42.5,
        size_bytes=4096,
        pixel_format="yuv420p",
        color_transfer="bt709",
        color_primaries="bt709",
        width=1920,
        height=1080,
        decoded_frames=1020,
    )


@pytest.mark.asyncio
async def test_the_wizard_mixes_with_the_runs_mute_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A window the engine computed has to reach the mixer the wizard drives."""
    from immich_memories.ui.pages import _step4_generate as step4

    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"assembled")
    music_file = tmp_path / "track.mp3"
    music_file.write_bytes(b"audio")
    db_path = tmp_path / "runs.db"
    config = Config(cache={"database": str(db_path)})

    prepared = PreparedGeneration(
        path=output_path,
        encoding_plan=_plan(),
        assembly_clips=(),
        clips_analyzed=2,
        clips_selected=2,
        music_mute_windows=_WINDOWS,
    )
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=output_path,
        config=config,
        upload_enabled=False,
        music_path=music_file,
        no_music=False,
    )
    tracker = RunTracker("ui-music-windows", db_path=db_path, capture_system=False)
    tracker.start_run(source="manual")
    state = AppState(config=config, generation_options={"music_source": "Upload file"})

    captured: dict = {}

    def spy(video_path, music_path, output_path, config):  # noqa: ARG001
        captured["windows"] = config.mute_windows
        raise RuntimeError("stop after capture")

    async def io_bound(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    # WHY: mixing runs real ffmpeg; the contract under test is which windows reach it.
    monkeypatch.setattr("immich_memories.audio.mixer.mix_audio_with_ducking", spy)
    monkeypatch.setattr(step4, "validate_output", lambda _path, _encoding_plan: _probe())
    monkeypatch.setattr(step4.run, "io_bound", io_bound)

    await step4.finalize_ui_generation(
        state, params, prepared, tracker, progress_bar=object(), status_label=object()
    )

    assert captured.get("windows") == _WINDOWS


@pytest.mark.asyncio
async def test_choosing_no_music_never_reaches_the_music_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "None" is a choice, not an absence — the wizard must not fall back to a track.

    The shared phase resolves a bundled track when nothing else is available, so
    the wizard's "None" has to be carried as `no_music` rather than as a missing
    path, or picking silence would start producing music.
    """
    from immich_memories.ui.pages import _step4_generate as step4

    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"assembled")
    db_path = tmp_path / "runs.db"
    config = Config(cache={"database": str(db_path)})

    prepared = PreparedGeneration(
        path=output_path,
        encoding_plan=_plan(),
        assembly_clips=(),
        clips_analyzed=1,
        clips_selected=1,
        music_mute_windows=_WINDOWS,
    )
    params = GenerationParams(
        clips=[make_clip("clip-1")], output_path=output_path, config=config, upload_enabled=False
    )
    tracker = RunTracker("ui-music-none", db_path=db_path, capture_system=False)
    tracker.start_run(source="manual")
    state = AppState(config=config, generation_options={"music_source": "None"})

    ran = []

    def spy(*_args, **_kwargs):
        ran.append(True)

    # WHY: the shared phase is the boundary under inspection; it must not be entered.
    monkeypatch.setattr("immich_memories.generate_settings._run_music_phase", spy)
    monkeypatch.setattr(step4, "validate_output", lambda _path, _encoding_plan: _probe())

    await step4.finalize_ui_generation(
        state, params, prepared, tracker, progress_bar=object(), status_label=object()
    )

    assert ran == []


def test_the_wizard_hands_its_music_choice_to_the_pipeline(tmp_path: Path) -> None:
    """The choice has to reach params, or the shared phase decides without it."""
    from immich_memories.ui.pages._step4_generate import _uploaded_music_path

    gen_options = {"music_source": "Upload file", "music_file": b"an-mp3"}

    path = _uploaded_music_path(gen_options, tmp_path)

    assert path is not None
    assert path.read_bytes() == b"an-mp3"
    assert _uploaded_music_path({"music_source": "None"}, tmp_path) is None
    assert _uploaded_music_path({"music_source": "AI Generated"}, tmp_path) is None

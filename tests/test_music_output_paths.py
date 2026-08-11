"""Container-preserving music output path regressions."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest


class _RunTracker:
    def start_phase(self, _name: str, _total: int) -> None:
        pass

    def complete_phase(self, *, items_processed: int) -> None:
        del items_processed


class _Progress:
    value = 0.0


class _Status:
    def set_text(self, _text: str) -> None:
        pass


def test_apply_music_file_preserves_mov_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shared music application must mix through memory.with_music.mov."""
    from immich_memories.generate_music import apply_music_file

    video_path = tmp_path / "memory.mov"
    music_path = tmp_path / "music.wav"
    video_path.write_bytes(b"video")
    music_path.write_bytes(b"music")

    def write_output_name(
        *,
        video_path: Path,
        music_path: Path,
        output_path: Path,
        config: object,
    ) -> None:
        del video_path, music_path, config
        output_path.write_text(output_path.name)

    monkeypatch.setattr(
        "immich_memories.audio.mixer.mix_audio_with_ducking",
        write_output_name,
    )

    apply_music_file(video_path, music_path, volume=0.5)

    assert video_path.read_text() == "memory.with_music.mov"


@pytest.mark.asyncio
@pytest.mark.parametrize("use_four_stems", [False, True], ids=["full-mix", "four-stem"])
async def test_ai_music_preserves_mov_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_four_stems: bool,
) -> None:
    """Generated full-mix and four-stem paths must keep the MOV container."""
    from immich_memories.ui.pages._step4_music import apply_ai_music

    result_path = tmp_path / "memory.mov"
    result_path.write_bytes(b"video")
    stems = None
    if use_four_stems:
        stems = SimpleNamespace(
            drums=tmp_path / "drums.wav",
            bass=tmp_path / "bass.wav",
            vocals=tmp_path / "vocals.wav",
            other=tmp_path / "other.wav",
        )
    selected_music = SimpleNamespace(full_mix=tmp_path / "music.wav", stems=stems)
    music_result = SimpleNamespace(
        versions=[selected_music],
        selected=selected_music,
        cleanup_unselected=lambda: None,
    )
    monkeypatch.setattr(
        "immich_memories.ui.state.get_app_state",
        lambda: SimpleNamespace(music_preview_result=music_result),
    )

    def write_full_mix(**kwargs: object) -> None:
        output_path = cast(Path, kwargs["output_path"])
        output_path.write_text(f"full-mix:{output_path.name}")

    def write_four_stem(**kwargs: object) -> None:
        output_path = cast(Path, kwargs["output_path"])
        output_path.write_text(f"four-stem:{output_path.name}")

    async def io_bound(callback: Callable[..., object], **kwargs: object) -> object:
        return callback(**kwargs)

    monkeypatch.setattr(
        "immich_memories.audio.mixer.mix_audio_with_ducking",
        write_full_mix,
    )
    monkeypatch.setattr(
        "immich_memories.audio.mixer_helpers.mix_audio_with_4stem_ducking",
        write_four_stem,
    )
    monkeypatch.setattr(
        "immich_memories.ui.pages._step4_music.run.io_bound",
        io_bound,
    )

    await apply_ai_music(
        result_path,
        assembly_clips=[],
        gen_options={"music_volume": 0.5},
        config=object(),
        run_output_dir=tmp_path,
        run_tracker=_RunTracker(),
        progress_bar=_Progress(),
        status_label=_Status(),
    )

    mixer = "four-stem" if use_four_stems else "full-mix"
    assert result_path.read_text() == f"{mixer}:memory.with_music.mov"


@pytest.mark.asyncio
async def test_uploaded_music_preserves_mov_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uploaded music must mix through memory.with_music.mov."""
    from immich_memories.ui.pages._step4_music import apply_uploaded_music

    result_path = tmp_path / "memory.mov"
    result_path.write_bytes(b"video")

    def write_uploaded_mix(**kwargs: object) -> None:
        output_path = cast(Path, kwargs["output_path"])
        output_path.write_text(f"uploaded:{output_path.name}")

    async def io_bound(callback: Callable[..., object], **kwargs: object) -> object:
        return callback(**kwargs)

    monkeypatch.setattr(
        "immich_memories.audio.mixer.mix_audio_with_ducking",
        write_uploaded_mix,
    )
    monkeypatch.setattr(
        "immich_memories.ui.pages._step4_music.run.io_bound",
        io_bound,
    )

    await apply_uploaded_music(
        result_path,
        gen_options={"music_file": b"uploaded", "music_volume": 0.5},
        run_tracker=_RunTracker(),
        progress_bar=_Progress(),
        status_label=_Status(),
    )

    assert result_path.read_text() == "uploaded:memory.with_music.mov"


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg and ffprobe are required",
)
def test_real_prores_music_mix_stays_mov(tmp_path: Path) -> None:
    """Real mixing must preserve ProRes video and add AAC audio in the MOV output."""
    from immich_memories.generate_music import apply_music_file

    video_path = tmp_path / "memory.mov"
    music_path = tmp_path / "music.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=10:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "1",
            "-pix_fmt",
            "yuv422p10le",
            "-c:a",
            "pcm_s16le",
            str(video_path),
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=4",
            "-c:a",
            "pcm_s16le",
            str(music_path),
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )

    apply_music_file(video_path, music_path, volume=0.5)

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name",
            "-of",
            "json",
            str(video_path),
        ],
        capture_output=True,
        check=True,
        text=True,
        timeout=30,
    )
    streams = json.loads(probe.stdout)["streams"]
    assert video_path.suffix == ".mov"
    assert {stream["codec_type"]: stream["codec_name"] for stream in streams} == {
        "video": "prores",
        "audio": "aac",
    }
    assert not (tmp_path / "memory.with_music.mp4").exists()

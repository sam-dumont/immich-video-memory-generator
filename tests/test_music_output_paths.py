"""Container-preserving music output path regressions."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams
from tests.conftest import make_clip


def _h264_output_plan():
    from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec

    return EncodingPlan(
        codec=OutputCodec.H264,
        encoder="libx264",
        encoder_args=("-c:v", "libx264"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
    )


def _prores_output_plan():
    from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec

    return EncodingPlan(
        codec=OutputCodec.PRORES,
        encoder="prores_ks",
        encoder_args=("-c:v", "prores_ks"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv422p10le",
        container="mov",
    )


def _publish_fake_music_mix(video_path: Path, encoding_plan: object) -> None:
    """Emulate successful validated publication for path-only unit tests."""
    container = encoding_plan.container
    video_path.with_suffix(f".with_music.{container}").replace(video_path)


def _final_probe_payload(*, codec: str = "h264") -> dict[str, object]:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": codec,
                "pix_fmt": "yuv420p",
                "color_transfer": "bt709",
                "color_primaries": "bt709",
                "width": 1920,
                "height": 1080,
                "nb_read_frames": "360",
            }
        ],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "12.0",
            "size": "4096",
            "tags": {"major_brand": "isom"},
        },
    }


def test_music_mix_drifting_from_base_identity_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from immich_memories.generate_music import apply_music_file
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import InvalidOutputArtifact, OutputProbe

    video = tmp_path / "memory.mp4"
    music = tmp_path / "music.wav"
    video.write_bytes(b"validated-h264-base")
    music.write_bytes(b"music")
    # WHY: probing shells out to ffprobe; publish_validated_output resolves it in output_contract.
    monkeypatch.setattr(
        "immich_memories.processing.output_contract.probe_output",
        MagicMock(
            return_value=OutputProbe(
                codec="hevc",
                container="mp4",
                duration_seconds=5.0,
                size_bytes=1024,
                pixel_format="yuv420p",
                color_transfer="bt709",
                color_primaries="bt709",
                width=1920,
                height=1080,
                decoded_frames=120,
            )
        ),
    )
    plan = _h264_output_plan()

    def write_mix(*, output_path: Path, **_kwargs: object) -> None:
        output_path.write_bytes(b"drifted-hevc-mix")

    monkeypatch.setattr(
        "immich_memories.audio.mixer.mix_audio_with_ducking",
        write_mix,
    )
    monkeypatch.setattr(
        "immich_memories.generate_music._require_audio_stream",
        lambda _path: None,
    )
    monkeypatch.setattr(
        output_contract.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps(_final_probe_payload(codec="hevc")), ""
        ),
    )

    with pytest.raises(InvalidOutputArtifact, match="expected h264, got hevc"):
        apply_music_file(video, music, volume=0.5, encoding_plan=plan)

    assert video.read_bytes() == b"validated-h264-base"


def test_music_publication_requires_positive_decoded_audio_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A staged mix is publishable only after one decoded-audio frame count."""
    from immich_memories.filename_builder import build_music_output_path
    from immich_memories.generate_music import publish_music_mix
    from immich_memories.processing.output_contract import OutputProbe

    video = tmp_path / "memory.mp4"
    video.write_bytes(b"validated-base")
    build_music_output_path(video).write_bytes(b"staged-mix")
    commands: list[list[str]] = []
    probe_kwargs: list[dict[str, object]] = []

    def audio_probe(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        probe_kwargs.append(kwargs)
        frame_count = "24" if "-count_frames" in command else "N/A"
        payload = {"streams": [{"codec_type": "audio", "nb_read_frames": frame_count}]}
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    expected = OutputProbe(
        codec="h264",
        container="mp4",
        duration_seconds=5.0,
        size_bytes=1024,
        pixel_format="yuv420p",
        color_transfer="bt709",
        color_primaries="bt709",
        width=1920,
        height=1080,
        decoded_frames=120,
    )
    monkeypatch.setattr("immich_memories.generate_music.subprocess.run", audio_probe)
    monkeypatch.setattr(
        "immich_memories.generate_music.publish_validated_output",
        MagicMock(return_value=expected),
    )

    result = publish_music_mix(video, _h264_output_plan())

    assert result == expected
    assert len(commands) == 1
    assert probe_kwargs == [
        {
            "capture_output": True,
            "text": True,
            "timeout": 15 * 60,
            "check": False,
        }
    ]


@pytest.mark.parametrize(
    ("payload", "returncode", "stderr"),
    [
        ({"streams": [{"codec_type": "audio", "nb_read_frames": "12"}]}, 1, ""),
        ({"streams": [], "format": {}}, 0, ""),
        ({"streams": [{"codec_type": "audio", "nb_read_frames": "N/A"}]}, 0, ""),
        ({"streams": [{"codec_type": "audio", "nb_read_frames": "0"}]}, 0, ""),
        ({"streams": [{"codec_type": "audio", "nb_read_frames": "wat"}]}, 0, ""),
        ({"streams": [{"codec_type": "audio", "nb_read_frames": True}]}, 0, ""),
        (
            {"streams": [{"codec_type": "audio", "nb_read_frames": "12"}]},
            0,
            "decode error",
        ),
    ],
    ids=["nonzero", "missing", "na", "zero", "malformed", "boolean", "stderr"],
)
def test_music_publication_rejects_unproven_audio_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
    returncode: int,
    stderr: str,
) -> None:
    """Missing, malformed, or errored audio decode evidence never publishes."""
    from immich_memories.filename_builder import build_music_output_path
    from immich_memories.generate_music import publish_music_mix
    from immich_memories.processing.output_contract import InvalidOutputArtifact

    video = tmp_path / "memory.mp4"
    video.write_bytes(b"validated-base")
    build_music_output_path(video).write_bytes(b"staged-mix")
    monkeypatch.setattr(
        "immich_memories.generate_music.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            returncode,
            json.dumps(payload),
            stderr,
        ),
    )
    monkeypatch.setattr(
        "immich_memories.generate_music.publish_validated_output",
        lambda _staged_path, _final_path, _plan: None,
    )

    with pytest.raises(InvalidOutputArtifact):
        publish_music_mix(video, _h264_output_plan())

    assert video.read_bytes() == b"validated-base"


def _prores_plan():
    from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec

    return EncodingPlan(
        codec=OutputCodec.PRORES,
        encoder="prores_ks",
        encoder_args=("-profile:v", "3"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv422p10le",
        container="mov",
    )


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
    monkeypatch.setattr(
        "immich_memories.generate_music.publish_music_mix",
        _publish_fake_music_mix,
    )

    apply_music_file(
        video_path,
        music_path,
        volume=0.5,
        encoding_plan=_prores_output_plan(),
    )

    assert video_path.read_text() == "memory.with_music.mov"


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
            "-vf",
            "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709",
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

    apply_music_file(
        video_path,
        music_path,
        volume=0.5,
        encoding_plan=_prores_output_plan(),
    )

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


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg and ffprobe are required",
)
def test_real_music_mix_without_audio_never_replaces_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A video-only staged artifact is not a successful music mix."""
    from immich_memories.generate_music import apply_music_file
    from immich_memories.processing.output_contract import InvalidOutputArtifact

    video_path = tmp_path / "memory.mp4"
    music_path = tmp_path / "music.wav"
    music_path.write_bytes(b"unused")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=10:duration=1",
            "-vf",
            "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(video_path),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    original = video_path.read_bytes()

    def copy_video_only(*, output_path: Path, **_kwargs: object) -> None:
        output_path.write_bytes(original)

    monkeypatch.setattr(
        "immich_memories.audio.mixer.mix_audio_with_ducking",
        copy_video_only,
    )

    with pytest.raises(InvalidOutputArtifact, match="missing audio stream"):
        apply_music_file(
            video_path,
            music_path,
            volume=0.5,
            encoding_plan=_h264_output_plan(),
        )

    assert video_path.read_bytes() == original


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg and ffprobe are required",
)
def test_real_truncated_aac_fails_decoded_audio_proof(tmp_path: Path) -> None:
    """A truncated AAC artifact cannot satisfy the full-decode music contract."""
    from immich_memories.generate_music import _require_audio_stream
    from immich_memories.processing.output_contract import InvalidOutputArtifact

    audio_path = tmp_path / "music.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:a",
            "aac",
            audio_path,
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    encoded = audio_path.read_bytes()
    audio_path.write_bytes(encoded[: len(encoded) // 2])

    with pytest.raises(InvalidOutputArtifact):
        _require_audio_stream(audio_path)


class TestApplyMusicFileAtomic:
    """Shared music publication keeps the validated base until proof succeeds."""

    def test_replaces_video_with_mixed_output(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from immich_memories.generate_music import apply_music_file
        from immich_memories.processing import output_contract

        video = tmp_path / "output.mp4"
        music = tmp_path / "music.wav"
        video.write_bytes(b"original video")
        music.write_bytes(b"music data")
        monkeypatch.setattr(
            output_contract.subprocess,
            "run",
            lambda command, **_kwargs: subprocess.CompletedProcess(
                command, 0, json.dumps(_final_probe_payload()), ""
            ),
        )

        def fake_mix(*, output_path: Path, **_kwargs: object) -> None:
            output_path.write_bytes(b"mixed video")

        monkeypatch.setattr(
            "immich_memories.audio.mixer.mix_audio_with_ducking",
            fake_mix,
        )
        monkeypatch.setattr(
            "immich_memories.generate_music._require_audio_stream",
            lambda _path: None,
        )
        apply_music_file(video, music, volume=0.8, encoding_plan=_h264_output_plan())

        assert video.read_bytes() == b"mixed video"
        assert not (tmp_path / "output.with_music.mp4").exists()

    def test_does_not_unlink_original_before_swap(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from immich_memories.generate_music import apply_music_file
        from immich_memories.processing import output_contract

        video = tmp_path / "output.mp4"
        music = tmp_path / "music.wav"
        video.write_bytes(b"original video")
        music.write_bytes(b"music data")
        monkeypatch.setattr(
            output_contract.subprocess,
            "run",
            lambda command, **_kwargs: subprocess.CompletedProcess(
                command, 0, json.dumps(_final_probe_payload()), ""
            ),
        )
        unlink_calls: list[Path] = []
        original_unlink = Path.unlink

        def tracking_unlink(self: Path, missing_ok: bool = False) -> None:
            unlink_calls.append(self)
            original_unlink(self, missing_ok=missing_ok)

        def fake_mix(*, output_path: Path, **_kwargs: object) -> None:
            output_path.write_bytes(b"mixed video")

        with patch.object(Path, "unlink", tracking_unlink):
            monkeypatch.setattr(
                "immich_memories.audio.mixer.mix_audio_with_ducking",
                fake_mix,
            )
            monkeypatch.setattr(
                "immich_memories.generate_music._require_audio_stream",
                lambda _path: None,
            )
            apply_music_file(video, music, volume=0.8, encoding_plan=_h264_output_plan())

        assert video not in unlink_calls

    def test_invalid_mix_never_replaces_valid_base(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from immich_memories.generate_music import apply_music_file
        from immich_memories.processing.output_contract import InvalidOutputArtifact

        video = tmp_path / "memory.mp4"
        music = tmp_path / "music.wav"
        video.write_bytes(b"validated-base")
        music.write_bytes(b"music")

        def write_invalid_mix(*, output_path: Path, **_kwargs: object) -> None:
            output_path.write_bytes(b"invalid-mix")

        monkeypatch.setattr(
            "immich_memories.audio.mixer.mix_audio_with_ducking",
            write_invalid_mix,
        )
        monkeypatch.setattr(
            "immich_memories.generate_music.publish_validated_output",
            MagicMock(side_effect=InvalidOutputArtifact("missing audio/video stream")),
        )
        monkeypatch.setattr(
            "immich_memories.generate_music._require_audio_stream",
            lambda _path: None,
        )

        with pytest.raises(InvalidOutputArtifact, match="missing audio/video stream"):
            apply_music_file(
                video,
                music,
                volume=0.8,
                encoding_plan=_h264_output_plan(),
            )

        assert video.read_bytes() == b"validated-base"
        assert not (tmp_path / "memory.with_music.mp4").exists()

    def test_validation_failure_survives_inner_stage_cleanup_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Stage cleanup cannot replace the validation failure or its chained cause."""
        from immich_memories.generate_music import apply_music_file, optional_music_warning
        from immich_memories.processing.output_contract import InvalidOutputArtifact

        video = tmp_path / "memory.mp4"
        music = tmp_path / "music.wav"
        staged = tmp_path / "memory.with_music.mp4"
        video.write_bytes(b"validated-base")
        music.write_bytes(b"music")
        config = Config()
        configured_value = "validation-secret-482"
        config.musicgen.api_key = configured_value
        validation_cause = RuntimeError("decoded video evidence unavailable")
        validation_error = InvalidOutputArtifact("invalid mix from validation-secret-482")
        validation_error.__cause__ = validation_cause

        def write_invalid_mix(*, output_path: Path, **_kwargs: object) -> None:
            output_path.write_bytes(b"invalid-mix")

        real_unlink = Path.unlink

        def fail_stage_cleanup(path: Path, missing_ok: bool = False) -> None:
            if path == staged and path.exists():
                raise OSError("cleanup leaked cleanup-secret-917")
            real_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(
            "immich_memories.audio.mixer.mix_audio_with_ducking",
            write_invalid_mix,
        )
        monkeypatch.setattr(
            "immich_memories.generate_music.publish_validated_output",
            MagicMock(side_effect=validation_error),
        )
        monkeypatch.setattr(
            "immich_memories.generate_music._require_audio_stream",
            lambda _path: None,
        )
        monkeypatch.setattr(Path, "unlink", fail_stage_cleanup)
        caplog.set_level("DEBUG")

        with pytest.raises(InvalidOutputArtifact) as caught:
            apply_music_file(video, music, volume=0.5, encoding_plan=_h264_output_plan())

        assert caught.value is validation_error
        assert caught.value.__cause__ is validation_cause
        assert optional_music_warning(caught.value, config) == (
            "Optional music failed: invalid mix from ***"
        )
        assert video.read_bytes() == b"validated-base"
        assert "Music stage cleanup failed; preserving the primary phase outcome" in caplog.text
        assert "cleanup-secret-917" not in caplog.text


def test_music_failure_keeps_valid_base_and_returns_sanitized_warning(tmp_path: Path) -> None:
    from immich_memories import generate_music
    from immich_memories.generate_settings import _run_music_phase

    base_video = tmp_path / "memory.mp4"
    base_video.write_bytes(b"validated-base")
    music_file = tmp_path / "music.wav"
    music_file.write_bytes(b"music")
    params = GenerationParams(
        clips=[],
        output_path=base_video,
        config=Config(),
        music_path=music_file,
    )
    tracker = MagicMock()

    with patch(
        "immich_memories.generate_music.apply_music_file",
        side_effect=RuntimeError("music backend unavailable"),
    ):
        result = _run_music_phase(
            params,
            [],
            base_video,
            tmp_path,
            tracker,
            encoding_plan=_h264_output_plan(),
        )

    warning = "Optional music failed: music backend unavailable"
    assert type(result) is generate_music.MusicPhaseResult
    assert result == generate_music.MusicPhaseResult(applied=False, warning=warning)
    assert base_video.read_bytes() == b"validated-base"
    tracker.complete_phase.assert_called_once_with(
        items_processed=0,
        errors=[{"error": warning}],
    )


def test_music_resolution_failure_is_optional_and_sanitized(tmp_path: Path) -> None:
    from immich_memories import generate_music
    from immich_memories.generate_settings import _run_music_phase

    base_video = tmp_path / "memory.mp4"
    base_video.write_bytes(b"validated-base")
    config = Config()
    config.musicgen.enabled = True
    config.musicgen.api_key = "top-secret"
    params = GenerationParams(clips=[], output_path=base_video, config=config)
    tracker = MagicMock()

    with patch(
        "immich_memories.generate_music.resolve_music",
        side_effect=RuntimeError("api_key=top-secret backend unavailable"),
    ):
        result = _run_music_phase(
            params,
            [],
            base_video,
            tmp_path,
            tracker,
            encoding_plan=_h264_output_plan(),
        )

    warning = "Optional music failed: api_key=*** backend unavailable"
    assert type(result) is generate_music.MusicPhaseResult
    assert result == generate_music.MusicPhaseResult(applied=False, warning=warning)
    assert base_video.read_bytes() == b"validated-base"
    tracker.start_phase.assert_called_once_with("music", 1)
    tracker.complete_phase.assert_called_once_with(
        items_processed=0,
        errors=[{"error": warning}],
    )


def test_optional_music_logs_never_include_raw_backend_secret(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Raw backend tracebacks must not bypass the optional boundary sanitizer."""
    from immich_memories.generate_settings import _run_music_phase

    base_video = tmp_path / "memory.mp4"
    base_video.write_bytes(b"validated-base")
    config = Config()
    config.musicgen.enabled = True
    config.musicgen.api_key = "top-secret"
    params = GenerationParams(clips=[], output_path=base_video, config=config)
    tracker = MagicMock()
    caplog.set_level("DEBUG")

    with patch(
        "immich_memories.audio.music_generator.generate_music_for_video",
        new_callable=AsyncMock,
        side_effect=RuntimeError("backend rejected top-secret"),
    ):
        result = _run_music_phase(
            params,
            [],
            base_video,
            tmp_path,
            tracker,
            encoding_plan=_h264_output_plan(),
        )

    # A generator failure no longer ends the phase — it falls through to a
    # bundled track (#422) — but the substitution is still reported (#515), so
    # the sanitized warning has to reach both the log and the phase result.
    assert "Optional music failed: backend rejected ***" in caplog.text
    assert "top-secret" not in caplog.text
    assert result.warning is not None
    assert "top-secret" not in result.warning


def test_music_phase_passes_exact_encoding_plan_to_publication(tmp_path: Path) -> None:
    from immich_memories import generate_music
    from immich_memories.generate_settings import _run_music_phase

    base_video = tmp_path / "memory.mp4"
    base_video.write_bytes(b"validated-base")
    music_file = tmp_path / "music.wav"
    music_file.write_bytes(b"music")
    plan = _h264_output_plan()
    params = GenerationParams(
        clips=[],
        output_path=base_video,
        config=Config(),
        music_path=music_file,
    )
    tracker = MagicMock()

    with patch("immich_memories.generate_music.apply_music_file") as apply_music:
        result = _run_music_phase(
            params,
            [],
            base_video,
            tmp_path,
            tracker,
            encoding_plan=plan,
        )

    assert result == generate_music.MusicPhaseResult(applied=True)
    apply_music.assert_called_once_with(
        base_video, music_file, params.music_volume, plan, mute_windows=None
    )
    tracker.complete_phase.assert_called_once_with(items_processed=1)


def test_optional_music_failure_preserves_base_and_uploads_valid_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from immich_memories import generate as generate_module
    from immich_memories.generate import generate_memory
    from immich_memories.processing import output_contract
    from immich_memories.processing.assembly_config import AssemblyClip, AssemblySettings

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-video")
    assembly_clip = AssemblyClip(path=source, duration=5.0, asset_id="clip-1")
    music_file = tmp_path / "music.wav"
    music_file.write_bytes(b"music")
    config = Config(
        cache={"directory": str(tmp_path / "cache"), "database": str(tmp_path / "runs.db")}
    )
    progress: list[tuple[str, str]] = []
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=tmp_path / "memory.mp4",
        config=config,
        client=MagicMock(),
        music_path=music_file,
        upload_enabled=True,
        progress_callback=lambda phase, _pct, message: progress.append((phase, message)),
    )
    plan = _h264_output_plan()

    class Assembler:
        def assemble_with_titles(
            self,
            _clips: object,
            output_path: Path,
            _progress_callback: object,
            **_kwargs: object,
        ) -> Path:
            output_path.write_bytes(b"validated-base")
            return output_path

    monkeypatch.setattr(
        output_contract.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps(_final_probe_payload()), ""
        ),
    )
    tracker = MagicMock()
    uploaded: list[bytes] = []

    def upload(_client: object, video_path: Path, _album: object) -> dict[str, str]:
        uploaded.append(video_path.read_bytes())
        return {"asset_id": "asset-1"}

    with (
        patch("immich_memories.tracking.RunTracker", return_value=tracker),
        patch("immich_memories.cache.video_cache.VideoDownloadCache", return_value=MagicMock()),
        patch.object(generate_module, "_extract_clips", return_value=[assembly_clip]),
        patch.object(
            generate_module,
            "_build_assembly_settings",
            return_value=AssemblySettings(encoding_plan=plan),
        ),
        patch.object(generate_module, "_create_assembler", return_value=Assembler()),
        patch(
            "immich_memories.generate_music.apply_music_file",
            side_effect=RuntimeError("music backend unavailable"),
        ),
        patch("immich_memories.generate_delivery._upload_to_immich", side_effect=upload),
        patch.object(generate_module, "_cleanup_temp_clips"),
    ):
        result = generate_memory(params)

    warning = "Optional music failed: music backend unavailable"
    assert result.read_bytes() == b"validated-base"
    assert uploaded == [b"validated-base"]
    assert ("music", warning) in progress
    tracker.complete_artifact.assert_called_once()
    assert tracker.complete_artifact.call_args.args[2] == [warning]
    tracker.mark_delivered.assert_called_once_with("asset-1")
    tracker.fail_run.assert_not_called()


def test_no_music_skips_core_music_phase_entirely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UI-owned None music must not even enter core optional-music resolution."""
    from immich_memories import generate as generate_module
    from immich_memories.generate import generate_memory
    from immich_memories.processing import output_contract
    from immich_memories.processing.assembly_config import AssemblyClip, AssemblySettings

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-video")
    assembly_clip = AssemblyClip(path=source, duration=5.0, asset_id="clip-1")
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=tmp_path / "memory.mp4",
        config=Config(
            cache={"directory": str(tmp_path / "cache"), "database": str(tmp_path / "runs.db")}
        ),
        no_music=True,
    )
    plan = _h264_output_plan()

    class Assembler:
        def assemble_with_titles(
            self,
            _clips: object,
            output_path: Path,
            _progress_callback: object,
            **_kwargs: object,
        ) -> Path:
            output_path.write_bytes(b"validated-base")
            return output_path

    monkeypatch.setattr(
        output_contract.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps(_final_probe_payload()), ""
        ),
    )
    with (
        patch("immich_memories.tracking.RunTracker", return_value=MagicMock()),
        patch("immich_memories.cache.video_cache.VideoDownloadCache", return_value=MagicMock()),
        patch.object(generate_module, "_extract_clips", return_value=[assembly_clip]),
        patch.object(
            generate_module,
            "_build_assembly_settings",
            return_value=AssemblySettings(encoding_plan=plan),
        ),
        patch.object(generate_module, "_create_assembler", return_value=Assembler()),
        patch.object(generate_module, "_run_music_phase") as music_phase,
        patch.object(generate_module, "_cleanup_temp_clips"),
    ):
        generate_memory(params)

    music_phase.assert_not_called()

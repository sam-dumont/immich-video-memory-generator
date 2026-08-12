"""Mutation-sensitive runtime encoder fallback tests."""

from __future__ import annotations

import subprocess
import traceback
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from immich_memories.processing.assembly_config import AssemblyClip, AssemblySettings
from immich_memories.processing.encoding_errors import (
    EncoderFallbackError,
    StreamingEncoderFinishError,
    StreamingEncoderStartError,
    StreamingEncoderWriteError,
)
from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec


def _hardware_plan(codec: OutputCodec = OutputCodec.H264) -> EncodingPlan:
    encoder = "h264_videotoolbox" if codec is OutputCodec.H264 else "hevc_videotoolbox"
    return EncodingPlan(
        codec=codec,
        encoder=encoder,
        encoder_args=("-q:v", "0", "-allow_sw", "1"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
        preset="fast",
        crf=31,
    )


def _clip_encoder(plan: EncodingPlan):
    from immich_memories.processing.clip_encoder import ClipEncoder

    prober = MagicMock()
    prober.has_audio_stream.return_value = False
    prober.probe_framerate.return_value = 30.0
    return ClipEncoder(AssemblySettings(encoding_plan=plan), prober, lambda _path: None)


def _clip(tmp_path: Path) -> AssemblyClip:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"source")
    return AssemblyClip(path=path, duration=1.0)


def test_clip_encoder_nonzero_hardware_exit_retries_once_with_same_codec_quality(
    tmp_path: Path,
) -> None:
    """A broken advertised encoder gets one quality-preserving software attempt."""
    output = tmp_path / "memory.mp4"

    def run(command: list[str], **_kwargs: object) -> MagicMock:
        if command[command.index("-c:v") + 1] == "h264_videotoolbox":
            output.write_bytes(b"partial-hardware")
            return MagicMock(returncode=1, stderr="hardware failed")
        assert not output.exists()
        output.write_bytes(b"complete-software")
        return MagicMock(returncode=0, stderr="")

    with (
        patch("immich_memories.processing.clip_encoder._detect_hdr_type", return_value=None),
        patch("immich_memories.processing.clip_encoder.subprocess.run", side_effect=run) as invoke,
    ):
        _clip_encoder(_hardware_plan()).encode_single_clip(_clip(tmp_path), output)

    commands = [call.args[0] for call in invoke.call_args_list]
    assert [command[command.index("-c:v") + 1] for command in commands] == [
        "h264_videotoolbox",
        "libx264",
    ]
    assert commands[1][commands[1].index("-preset") + 1] == "veryfast"
    assert commands[1][commands[1].index("-crf") + 1] == "31"
    assert output.read_bytes() == b"complete-software"


@pytest.mark.parametrize(
    "start_failure",
    [
        pytest.param(OSError("advertised encoder cannot start"), id="oserror"),
        pytest.param(
            subprocess.TimeoutExpired(["ffmpeg"], timeout=30),
            id="timeout",
        ),
        pytest.param(StreamingEncoderStartError("typed start failure"), id="typed"),
    ],
)
def test_streaming_encoder_start_failure_retries_once_with_same_h265_plan(
    tmp_path: Path, start_failure: BaseException
) -> None:
    """Only an encoder-start failure activates the same-codec streaming retry."""
    from immich_memories.processing.streaming_assembler import assemble_streaming

    plans: list[EncodingPlan] = []

    class Encoder:
        def __init__(self, *_args: object, encoding_plan: EncodingPlan, **_kwargs: object) -> None:
            self.plan = encoding_plan
            plans.append(encoding_plan)

        def start(self) -> None:
            if self.plan.encoder == "hevc_videotoolbox":
                raise start_failure

        def finish(self) -> None:
            pass

    with (
        patch("immich_memories.processing.streaming_assembler.StreamingEncoder", Encoder),
        patch("immich_memories.processing.streaming_assembler._encode_clip_sequence"),
    ):
        assemble_streaming(
            clips=[SimpleNamespace(duration=1.0, path=tmp_path / "clip.mp4")],
            transitions=[],
            output_path=tmp_path / "memory.mp4",
            width=16,
            height=16,
            fps=1,
            encoding_plan=_hardware_plan(OutputCodec.H265),
        )

    assert [plan.encoder for plan in plans] == ["hevc_videotoolbox", "libx265"]
    assert all(plan.codec is OutputCodec.H265 for plan in plans)
    assert all(plan.container == "mp4" for plan in plans)
    assert plans[1].encoder_args == ("-preset", "veryfast", "-crf", "31")


def test_streaming_encoder_translates_only_its_pipe_write_failure(tmp_path: Path) -> None:
    """The encoder write boundary gives pipe failures a narrow retryable type."""
    from immich_memories.processing.streaming_assembler import StreamingEncoder

    stdin = MagicMock()
    stdin.write.side_effect = BrokenPipeError("raw pipe detail")
    encoder = StreamingEncoder(tmp_path / "memory.mp4", 16, 16, 1, encoding_plan=_hardware_plan())
    encoder._proc = SimpleNamespace(stdin=stdin)

    with pytest.raises(StreamingEncoderWriteError, match="stopped accepting frames") as raised:
        encoder.write_frame(np.zeros((16, 16, 3), dtype=np.uint8))

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_streaming_typed_write_failure_cleans_partial_and_retries_once(tmp_path: Path) -> None:
    """A typed hardware pipe failure gets one clean software render."""
    from immich_memories.processing.streaming_assembler import assemble_streaming

    output = tmp_path / "memory.mp4"
    audio_work_dir = tmp_path / "audio"
    audio_work_dir.mkdir()
    unrelated_audio = audio_work_dir / "keep.wav"
    unrelated_audio.write_bytes(b"user-owned")
    other_clip_audio = audio_work_dir / "clip_1_audio.wav"
    other_clip_audio.write_bytes(b"not-owned-by-current-attempt")
    plans: list[EncodingPlan] = []
    progress: list[tuple[int, int]] = []
    aborted: list[str] = []

    class Encoder:
        def __init__(self, *_args: object, encoding_plan: EncodingPlan, **_kwargs: object) -> None:
            self.plan = encoding_plan
            plans.append(encoding_plan)

        def start(self) -> None:
            pass

        def finish(self) -> None:
            pass

        def abort(self) -> None:
            aborted.append(self.plan.encoder)

    def encode(*args: object, **_kwargs: object) -> None:
        encoder = args[2]
        assert isinstance(encoder, Encoder)
        if encoder.plan.encoder == "h264_videotoolbox":
            output.write_bytes(b"partial-hardware")
            (audio_work_dir / "clip_0_audio.wav").write_bytes(b"partial-audio")
            raise StreamingEncoderWriteError("encoder stopped accepting frames")
        assert not output.exists()
        assert not (audio_work_dir / "clip_0_audio.wav").exists()
        assert unrelated_audio.read_bytes() == b"user-owned"
        assert other_clip_audio.read_bytes() == b"not-owned-by-current-attempt"
        output.write_bytes(b"complete-software")

    with (
        patch("immich_memories.processing.streaming_assembler.StreamingEncoder", Encoder),
        patch(
            "immich_memories.processing.streaming_assembler._encode_clip_sequence",
            side_effect=encode,
        ),
    ):
        assemble_streaming(
            clips=[SimpleNamespace(duration=1.0, path=tmp_path / "clip.mp4")],
            transitions=[],
            output_path=output,
            width=16,
            height=16,
            fps=1,
            encoding_plan=_hardware_plan(),
            progress_callback=lambda done, total: progress.append((done, total)),
            audio_work_dir=audio_work_dir,
        )

    assert [plan.encoder for plan in plans] == ["h264_videotoolbox", "libx264"]
    assert aborted == ["h264_videotoolbox"]
    assert output.read_bytes() == b"complete-software"
    assert unrelated_audio.read_bytes() == b"user-owned"
    assert other_clip_audio.read_bytes() == b"not-owned-by-current-attempt"
    assert progress == [(1, 1)]


def test_streaming_encoder_nonzero_finish_is_typed_and_hides_raw_stderr(tmp_path: Path) -> None:
    """FFmpeg exit diagnostics stay controlled at the encoder finish boundary."""
    from immich_memories.processing.streaming_assembler import StreamingEncoder

    proc = MagicMock()
    proc.returncode = 1
    proc.stderr.read.return_value = b"backend leaked configured-secret"
    encoder = StreamingEncoder(tmp_path / "memory.mp4", 16, 16, 1, encoding_plan=_hardware_plan())
    encoder._proc = proc

    with pytest.raises(StreamingEncoderFinishError, match="exit 1") as raised:
        encoder.finish()

    assert "configured-secret" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_streaming_encoder_close_failure_does_not_block_draining_stderr(tmp_path: Path) -> None:
    """A failed stdin close returns to the retry boundary without a blocking read."""
    from immich_memories.processing.streaming_assembler import StreamingEncoder

    proc = MagicMock()
    proc.stdin.close.side_effect = BrokenPipeError("encoder already exited")
    encoder = StreamingEncoder(tmp_path / "memory.mp4", 16, 16, 1, encoding_plan=_hardware_plan())
    encoder._proc = proc

    with pytest.raises(StreamingEncoderFinishError, match="BrokenPipeError") as raised:
        encoder.finish()

    proc.stderr.read.assert_not_called()
    proc.wait.assert_not_called()
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_streaming_typed_finish_failure_cleans_partial_and_retries_once(tmp_path: Path) -> None:
    """A typed hardware finalization failure gets one fresh software render."""
    from immich_memories.processing.streaming_assembler import assemble_streaming

    output = tmp_path / "memory.mp4"
    plans: list[EncodingPlan] = []

    class Encoder:
        def __init__(self, *_args: object, encoding_plan: EncodingPlan, **_kwargs: object) -> None:
            self.plan = encoding_plan
            plans.append(encoding_plan)

        def start(self) -> None:
            pass

        def finish(self) -> None:
            if self.plan.encoder == "hevc_videotoolbox":
                raise StreamingEncoderFinishError("hardware exit 1")

    def encode(*args: object, **_kwargs: object) -> None:
        encoder = args[2]
        assert isinstance(encoder, Encoder)
        if encoder.plan.encoder == "libx265":
            assert not output.exists()
            output.write_bytes(b"complete-software")
        else:
            output.write_bytes(b"partial-hardware")

    with (
        patch("immich_memories.processing.streaming_assembler.StreamingEncoder", Encoder),
        patch(
            "immich_memories.processing.streaming_assembler._encode_clip_sequence",
            side_effect=encode,
        ),
    ):
        assemble_streaming(
            clips=[SimpleNamespace(duration=1.0, path=tmp_path / "clip.mp4")],
            transitions=[],
            output_path=output,
            width=16,
            height=16,
            fps=1,
            encoding_plan=_hardware_plan(OutputCodec.H265),
        )

    assert [plan.encoder for plan in plans] == ["hevc_videotoolbox", "libx265"]
    assert output.read_bytes() == b"complete-software"


def test_preview_callback_broken_pipe_is_not_misclassified_for_fallback(tmp_path: Path) -> None:
    """A consumer callback pipe failure is unrelated to the encoder pipe."""
    from immich_memories.processing.streaming_assembler import assemble_streaming

    plans: list[EncodingPlan] = []

    class Encoder:
        def __init__(self, *_args: object, encoding_plan: EncodingPlan, **_kwargs: object) -> None:
            plans.append(encoding_plan)

        def start(self) -> None:
            pass

        def finish(self) -> None:
            pass

        def write_frame(self, _frame: np.ndarray) -> None:
            pass

    def disconnected_preview(_jpeg: bytes) -> None:
        raise BrokenPipeError("preview consumer disconnected")

    with (
        patch("immich_memories.processing.streaming_assembler.StreamingEncoder", Encoder),
        patch(
            "immich_memories.processing.streaming_assembler._make_decoder",
            return_value=iter([np.zeros((16, 16, 3), dtype=np.uint8)]),
        ),
        pytest.raises(BrokenPipeError, match="preview consumer"),
    ):
        assemble_streaming(
            clips=[SimpleNamespace(duration=1.0, path=tmp_path / "clip.mp4")],
            transitions=[],
            output_path=tmp_path / "memory.mp4",
            width=16,
            height=16,
            fps=1,
            encoding_plan=_hardware_plan(),
            frame_preview_callback=disconnected_preview,
        )

    assert [plan.encoder for plan in plans] == ["h264_videotoolbox"]


@pytest.mark.parametrize(
    "pipeline_error",
    [
        pytest.param(RuntimeError("decoder filter failed"), id="decoder-filter-runtime"),
        pytest.param(GeneratorExit("assembly cancelled"), id="cancellation"),
    ],
)
def test_unrelated_streaming_failure_is_never_retried(
    tmp_path: Path, pipeline_error: BaseException
) -> None:
    """Decoder/filter errors and cancellation escape without changing encoder."""
    from immich_memories.processing.streaming_assembler import assemble_streaming

    plans: list[EncodingPlan] = []

    class Encoder:
        def __init__(self, *_args: object, encoding_plan: EncodingPlan, **_kwargs: object) -> None:
            plans.append(encoding_plan)

        def start(self) -> None:
            pass

        def finish(self) -> None:
            pass

    output = tmp_path / "memory.mp4"

    def fail_pipeline(*_args: object, **_kwargs: object) -> None:
        output.write_bytes(b"partial-unrelated")
        raise pipeline_error

    with (
        patch("immich_memories.processing.streaming_assembler.StreamingEncoder", Encoder),
        patch(
            "immich_memories.processing.streaming_assembler._encode_clip_sequence",
            side_effect=fail_pipeline,
        ),
        pytest.raises(type(pipeline_error)),
    ):
        assemble_streaming(
            clips=[SimpleNamespace(duration=1.0, path=tmp_path / "clip.mp4")],
            transitions=[],
            output_path=output,
            width=16,
            height=16,
            fps=1,
            encoding_plan=_hardware_plan(),
        )

    assert [plan.encoder for plan in plans] == ["h264_videotoolbox"]
    assert not output.exists()


def test_unrelated_streaming_failure_survives_partial_cleanup_error(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Best-effort cleanup cannot replace an unrelated pipeline exception."""
    from immich_memories.processing.streaming_assembler import assemble_streaming

    class Encoder:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            pass

        def finish(self) -> None:
            pass

    output = tmp_path / "memory.mp4"
    original_unlink = Path.unlink

    def fail_output_cleanup(path: Path, *, missing_ok: bool = False) -> None:
        if path == output:
            raise OSError("configured-secret cleanup failure")
        original_unlink(path, missing_ok=missing_ok)

    with (
        patch("immich_memories.processing.streaming_assembler.StreamingEncoder", Encoder),
        patch(
            "immich_memories.processing.streaming_assembler._encode_clip_sequence",
            side_effect=RuntimeError("decoder filter failed"),
        ),
        patch.object(Path, "unlink", autospec=True, side_effect=fail_output_cleanup),
        pytest.raises(RuntimeError, match="decoder filter failed") as raised,
    ):
        assemble_streaming(
            clips=[SimpleNamespace(duration=1.0, path=tmp_path / "clip.mp4")],
            transitions=[],
            output_path=output,
            width=16,
            height=16,
            fps=1,
            encoding_plan=_hardware_plan(),
        )

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "configured-secret" not in caplog.text


def test_failed_audio_cleanup_prevents_retry_without_leaking_error(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stale attempt WAV fails closed instead of contaminating software output."""
    from immich_memories.processing.encoding_errors import StreamingEncoderCleanupError
    from immich_memories.processing.streaming_assembler import assemble_streaming

    audio_work_dir = tmp_path / "audio"
    audio_work_dir.mkdir()
    attempt_audio = audio_work_dir / "clip_0_audio.wav"
    plans: list[EncodingPlan] = []

    class Encoder:
        def __init__(self, *_args: object, encoding_plan: EncodingPlan, **_kwargs: object) -> None:
            plans.append(encoding_plan)

        def start(self) -> None:
            pass

        def finish(self) -> None:
            pass

    def fail_hardware(*_args: object, **_kwargs: object) -> None:
        attempt_audio.write_bytes(b"partial-audio")
        raise StreamingEncoderWriteError("hardware stopped")

    original_unlink = Path.unlink

    def fail_attempt_audio_cleanup(path: Path, *, missing_ok: bool = False) -> None:
        if path == attempt_audio:
            raise OSError("configured-secret cleanup failure")
        original_unlink(path, missing_ok=missing_ok)

    with (
        patch("immich_memories.processing.streaming_assembler.StreamingEncoder", Encoder),
        patch(
            "immich_memories.processing.streaming_assembler._encode_clip_sequence",
            side_effect=fail_hardware,
        ),
        patch.object(Path, "unlink", autospec=True, side_effect=fail_attempt_audio_cleanup),
        pytest.raises(StreamingEncoderCleanupError) as raised,
    ):
        assemble_streaming(
            clips=[SimpleNamespace(duration=1.0, path=tmp_path / "clip.mp4")],
            transitions=[],
            output_path=tmp_path / "memory.mp4",
            width=16,
            height=16,
            fps=1,
            encoding_plan=_hardware_plan(),
            audio_work_dir=audio_work_dir,
        )

    assert [plan.encoder for plan in plans] == ["h264_videotoolbox"]
    assert attempt_audio.exists()
    assert "configured-secret" not in str(raised.value)
    assert "configured-secret" not in caplog.text
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_clip_fallback_failure_is_controlled_secret_free_and_removes_partial(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Dual encoder failure exposes useful types, never raw backend diagnostics."""
    output = tmp_path / "memory.mp4"

    def run(command: list[str], **_kwargs: object) -> MagicMock:
        if command[command.index("-c:v") + 1] == "h264_videotoolbox":
            output.write_bytes(b"partial-hardware")
            return MagicMock(returncode=70, stderr="configured-secret hardware")
        output.write_bytes(b"partial-software")
        raise OSError("configured-secret software")

    with (
        patch("immich_memories.processing.clip_encoder._detect_hdr_type", return_value=None),
        patch("immich_memories.processing.clip_encoder.subprocess.run", side_effect=run) as invoke,
        pytest.raises(EncoderFallbackError) as raised,
    ):
        _clip_encoder(_hardware_plan()).encode_single_clip(_clip(tmp_path), output)

    message = str(raised.value)
    assert "h264_videotoolbox" in message
    assert "libx264" in message
    assert "exit 70" in message
    assert "OSError" in message
    assert "configured-secret" not in message
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "configured-secret" not in "".join(traceback.format_exception(raised.value))
    assert "configured-secret" not in caplog.text
    assert not output.exists()
    assert invoke.call_count == 2


def test_streaming_fallback_failure_is_controlled_secret_free_and_bounded(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed software retry has no raw nested exception or partial output."""
    from immich_memories.processing.streaming_assembler import assemble_streaming

    output = tmp_path / "memory.mp4"
    plans: list[EncodingPlan] = []

    class Encoder:
        def __init__(self, *_args: object, encoding_plan: EncodingPlan, **_kwargs: object) -> None:
            self.plan = encoding_plan
            plans.append(encoding_plan)

        def start(self) -> None:
            pass

        def finish(self) -> None:
            if self.plan.encoder == "libx264":
                raise StreamingEncoderFinishError("configured-secret software finish", exit_code=75)

    def encode(*args: object, **_kwargs: object) -> None:
        encoder = args[2]
        assert isinstance(encoder, Encoder)
        output.write_bytes(f"partial-{encoder.plan.encoder}".encode())
        if encoder.plan.encoder == "h264_videotoolbox":
            raise StreamingEncoderWriteError("configured-secret hardware write")

    with (
        patch("immich_memories.processing.streaming_assembler.StreamingEncoder", Encoder),
        patch(
            "immich_memories.processing.streaming_assembler._encode_clip_sequence",
            side_effect=encode,
        ),
        pytest.raises(EncoderFallbackError) as raised,
    ):
        assemble_streaming(
            clips=[SimpleNamespace(duration=1.0, path=tmp_path / "clip.mp4")],
            transitions=[],
            output_path=output,
            width=16,
            height=16,
            fps=1,
            encoding_plan=_hardware_plan(),
        )

    message = str(raised.value)
    assert "StreamingEncoderWriteError" in message
    assert "StreamingEncoderFinishError" in message
    assert "exit 75" in message
    assert "configured-secret" not in message
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "configured-secret" not in "".join(traceback.format_exception(raised.value))
    assert "configured-secret" not in caplog.text
    assert not output.exists()
    assert [plan.encoder for plan in plans] == ["h264_videotoolbox", "libx264"]


def test_clip_h265_spawn_failure_preserves_mov_pq_pixfmt_and_quality(tmp_path: Path) -> None:
    """Early H.265 hardware failure cannot drift any resolved output property."""
    plan = replace(
        _hardware_plan(OutputCodec.H265),
        target_transfer=HdrTransfer.PQ,
        pixel_format="p010le",
        container="mov",
    )
    output = tmp_path / "memory.mov"

    with (
        patch("immich_memories.processing.clip_encoder._detect_hdr_type", return_value="pq"),
        patch("immich_memories.processing.clip_encoder.subprocess.run") as invoke,
    ):
        invoke.side_effect = [
            FileNotFoundError("advertised hevc encoder unavailable"),
            MagicMock(returncode=0, stderr=""),
        ]
        _clip_encoder(plan).encode_single_clip(_clip(tmp_path), output)

    commands = [call.args[0] for call in invoke.call_args_list]
    assert [command[command.index("-c:v") + 1] for command in commands] == [
        "hevc_videotoolbox",
        "libx265",
    ]
    assert all(command[-1].endswith("memory.mov") for command in commands)
    assert all(command[command.index("-pix_fmt") + 1] == "p010le" for command in commands)
    assert all(command[command.index("-color_trc") + 1] == "smpte2084" for command in commands)
    assert commands[1][commands[1].index("-preset") + 1] == "veryfast"
    assert commands[1][commands[1].index("-crf") + 1] == "31"


def test_clip_hardware_timeout_retries_once(tmp_path: Path) -> None:
    """A bounded hardware timeout activates one software retry."""
    with (
        patch("immich_memories.processing.clip_encoder._detect_hdr_type", return_value=None),
        patch("immich_memories.processing.clip_encoder.subprocess.run") as invoke,
    ):
        invoke.side_effect = [
            subprocess.TimeoutExpired(["ffmpeg"], timeout=1800),
            MagicMock(returncode=0, stderr=""),
        ]
        _clip_encoder(_hardware_plan()).encode_single_clip(
            _clip(tmp_path), tmp_path / "timeout.mp4"
        )
    assert invoke.call_count == 2


def test_clip_arbitrary_runtime_error_does_not_retry(tmp_path: Path) -> None:
    """An unrelated caller failure cannot be mistaken for an encoder failure."""
    with (
        patch("immich_memories.processing.clip_encoder._detect_hdr_type", return_value=None),
        patch(
            "immich_memories.processing.clip_encoder.subprocess.run",
            side_effect=RuntimeError("unrelated caller failure"),
        ) as invoke,
        pytest.raises(RuntimeError, match="unrelated caller"),
    ):
        _clip_encoder(_hardware_plan()).encode_single_clip(
            _clip(tmp_path), tmp_path / "runtime.mp4"
        )
    assert invoke.call_count == 1


def test_clip_spawn_exception_and_failed_retry_keep_both_controlled_diagnostics(
    tmp_path: Path,
) -> None:
    """The primary exception classification survives through a failed retry."""
    with (
        patch("immich_memories.processing.clip_encoder._detect_hdr_type", return_value=None),
        patch("immich_memories.processing.clip_encoder.subprocess.run") as invoke,
        pytest.raises(EncoderFallbackError) as raised,
    ):
        invoke.side_effect = [
            subprocess.TimeoutExpired(["ffmpeg", "configured-secret"], timeout=1800),
            MagicMock(returncode=75, stderr="configured-secret fallback"),
        ]
        _clip_encoder(_hardware_plan()).encode_single_clip(_clip(tmp_path), tmp_path / "failed.mp4")

    assert "TimeoutExpired" in str(raised.value)
    assert "exit 75" in str(raised.value)
    assert "configured-secret" not in str(raised.value)
    assert raised.value.__context__ is None
    assert invoke.call_count == 2

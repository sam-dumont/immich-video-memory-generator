"""Unit tests for the VideoAssembler.

Tests the assembler through its public API (assemble()) by mocking at
external boundaries (FFmpeg subprocess) rather than internal methods.
Renaming internal methods should not break these tests.

For real FFmpeg integration tests, see tests/integration/test_assembly_real.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    TransitionType,
)
from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec


def _software_h264_plan() -> EncodingPlan:
    return EncodingPlan(
        codec=OutputCodec.H264,
        encoder="libx264",
        encoder_args=("-preset", "medium", "-crf", "18"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
    )


def _tone_map_h264_plan() -> EncodingPlan:
    plan = _software_h264_plan()
    return EncodingPlan(
        codec=plan.codec,
        encoder=plan.encoder,
        encoder_args=plan.encoder_args,
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=True,
        pixel_format=plan.pixel_format,
        container=plan.container,
    )


def _software_h265_hdr_plan() -> EncodingPlan:
    return EncodingPlan(
        codec=OutputCodec.H265,
        encoder="libx265",
        encoder_args=("-preset", "medium", "-crf", "18"),
        target_transfer=HdrTransfer.HLG,
        tone_map_to_sdr=False,
        pixel_format="yuv420p10le",
        container="mp4",
    )


def _software_h265_pq_plan() -> EncodingPlan:
    return EncodingPlan(
        codec=OutputCodec.H265,
        encoder="libx265",
        encoder_args=("-preset", "medium", "-crf", "18"),
        target_transfer=HdrTransfer.PQ,
        tone_map_to_sdr=False,
        pixel_format="yuv420p10le",
        container="mp4",
    )


def _software_prores_plan() -> EncodingPlan:
    return EncodingPlan(
        codec=OutputCodec.PRORES,
        encoder="prores_ks",
        encoder_args=("-profile:v", "3"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv422p10le",
        container="mov",
    )


def _make_assembly_clip(
    tmp_path: Path, name: str = "clip.mp4", duration: float = 5.0
) -> AssemblyClip:
    """Create a temporary file and wrap it as an AssemblyClip."""
    clip_path = tmp_path / name
    clip_path.write_bytes(b"\x00" * 1024)
    return AssemblyClip(path=clip_path, duration=duration)


def _make_assembler(settings: AssemblySettings | None = None):
    """Create a VideoAssembler with explicit defaults (no config singleton needed)."""
    from immich_memories.processing.video_assembler import VideoAssembler

    return VideoAssembler(settings or AssemblySettings())


def _write_mock_encoded(_clip, destination, *, target_resolution):
    assert target_resolution is not None
    destination.write_bytes(b"encoded-under-plan")


def test_preview_builds_an_explicit_crf_28_plan(tmp_path: Path) -> None:
    """Preview quality must be carried by its actual encoder plan."""
    from immich_memories.config_loader import Config
    from immich_memories.processing.video_assembler import create_preview

    clip = _make_assembly_clip(tmp_path, duration=2.0)
    output = tmp_path / "preview.mp4"

    with patch("immich_memories.processing.video_assembler.VideoAssembler") as assembler_cls:
        assembler_cls.return_value.assemble.return_value = output
        create_preview([clip], output, config=Config())

    settings = assembler_cls.call_args.args[0]
    args = settings.encoding_plan.encoder_args
    assert args[args.index("-crf") + 1] == "28"


class TestVideoAssemblerIntegration:
    """Integration tests for VideoAssembler through public API."""

    def test_assemble_empty_clips_raises(self):
        """assemble() with empty clips raises ValueError."""
        assembler = _make_assembler()
        with pytest.raises(ValueError, match="No clips provided"):
            assembler.assemble([], Path("/tmp/out.mp4"))

    def test_single_clip_encodes_file(self, tmp_path):
        """Single clip without music still encodes under the final plan."""
        assembler = _make_assembler(AssemblySettings(music_path=None))
        clip = _make_assembly_clip(tmp_path, "input.mp4")
        output = tmp_path / "output.mp4"

        with patch.object(assembler.encoder, "encode_single_clip", side_effect=_write_mock_encoded):
            result = assembler.assemble([clip], output)
        assert result == output
        assert output.exists()
        assert output.read_bytes() == b"encoded-under-plan"

    def test_single_clip_public_path_encodes_with_resolved_plan(self, tmp_path):
        """A source container/codec cannot bypass the resolved final-output plan."""
        assembler = _make_assembler(
            AssemblySettings(encoding_plan=_software_prores_plan(), music_path=None)
        )
        clip = _make_assembly_clip(tmp_path, "input.mp4")
        output = tmp_path / "output.mov"

        def write_encoded(_clip, destination, *, target_resolution):
            assert target_resolution == (1280, 720)
            destination.write_bytes(b"encoded-as-prores")

        with patch.object(
            assembler.encoder,
            "encode_single_clip",
            side_effect=write_encoded,
        ) as encode:
            result = assembler.assemble([clip], output)

        assert result == output
        encode.assert_called_once()
        assert output.read_bytes() == b"encoded-as-prores"

    def test_settings_propagate(self):
        """Assembly settings are accessible on the assembler."""
        assembler = _make_assembler(
            AssemblySettings(
                transition=TransitionType.CUT,
                output_crf=22,
            )
        )
        assert assembler.settings.transition == TransitionType.CUT
        assert assembler.settings.output_crf == 22

    def test_single_clip_with_music_triggers_music_flow(self, tmp_path):
        """Single clip with music_path set takes the music branch, not copy."""
        music_file = tmp_path / "music.mp3"
        music_file.write_bytes(b"\x00" * 512)

        assembler = _make_assembler(AssemblySettings(music_path=music_file))
        clip = _make_assembly_clip(tmp_path, "input.mp4")
        output = tmp_path / "output.mp4"

        # WHY: mock subprocess.run — test verifies graceful fallback when FFmpeg fails,
        # without requiring FFmpeg to be installed in the test environment
        mock_result = MagicMock(returncode=1, stderr="mock ffmpeg failure")
        with (
            patch.object(assembler.encoder, "encode_single_clip", side_effect=_write_mock_encoded),
            patch(
                "immich_memories.processing.audio_mixer_service.subprocess.run",
                return_value=mock_result,
            ),
        ):
            result = assembler.assemble([clip], output)

        # Music add fails gracefully; assembler falls back to original file
        assert isinstance(result, Path)

    def test_assemble_returns_path(self, tmp_path):
        """assemble() returns a Path on success."""
        assembler = _make_assembler(AssemblySettings(music_path=None))
        clip = _make_assembly_clip(tmp_path, "single.mp4")
        output = tmp_path / "result.mp4"

        with patch.object(assembler.encoder, "encode_single_clip", side_effect=_write_mock_encoded):
            result = assembler.assemble([clip], output)
        assert isinstance(result, Path)

    def test_standalone_constructor_crf_builds_the_encoder_plan(self):
        """The convenience CRF must reach the encoder contract, not dead metadata."""
        from immich_memories.processing.video_assembler import VideoAssembler

        assembler = VideoAssembler(
            output_crf=28,
            default_transition_duration=0.8,
        )

        args = assembler.settings.encoding_plan.encoder_args
        assert args[args.index("-crf") + 1] == "28"
        assert assembler.settings.transition_duration == 0.8

    def test_assemble_idempotent_single_clip(self, tmp_path):
        """Assembling the same single clip twice produces identical output."""
        assembler = _make_assembler(AssemblySettings(music_path=None))
        clip = _make_assembly_clip(tmp_path, "input.mp4")

        out1 = tmp_path / "out1.mp4"
        out2 = tmp_path / "out2.mp4"
        with patch.object(assembler.encoder, "encode_single_clip", side_effect=_write_mock_encoded):
            assembler.assemble([clip], out1)
            assembler.assemble([clip], out2)

        assert out1.read_bytes() == out2.read_bytes()

    def test_assemble_does_not_modify_input(self, tmp_path):
        """Assembling does not modify the source clip file."""
        assembler = _make_assembler(AssemblySettings(music_path=None))
        clip = _make_assembly_clip(tmp_path, "input.mp4")
        original_bytes = clip.path.read_bytes()

        with patch.object(assembler.encoder, "encode_single_clip", side_effect=_write_mock_encoded):
            assembler.assemble([clip], tmp_path / "output.mp4")

        assert clip.path.read_bytes() == original_bytes

    def test_missing_output_parent_raises(self, tmp_path):
        """Assembler raises when output parent directory does not exist."""
        assembler = _make_assembler(AssemblySettings(music_path=None))
        clip = _make_assembly_clip(tmp_path, "input.mp4")
        output = tmp_path / "nonexistent" / "output.mp4"

        with (
            patch.object(assembler.encoder, "encode_single_clip", side_effect=_write_mock_encoded),
            pytest.raises(FileNotFoundError),
        ):
            assembler.assemble([clip], output)


def test_final_assembly_command_uses_the_resolved_software_h264_plan(tmp_path: Path) -> None:
    """The final FFmpeg command must not select a different codec downstream."""
    from immich_memories.processing.clip_encoder import ClipEncoder
    from immich_memories.processing.ffmpeg_runner import AssemblyContext

    settings = AssemblySettings(encoding_plan=_software_h264_plan(), preserve_hdr=False)
    prober = MagicMock()
    prober.estimate_duration.return_value = 5.0
    encoder = ClipEncoder(settings, prober, lambda _path: None)
    clip = _make_assembly_clip(tmp_path)
    context = AssemblyContext(
        target_w=1920,
        target_h=1080,
        pix_fmt="yuv420p",
        hdr_type="sdr",
        clip_hdr_types=[None],
        clip_primaries=[None],
        colorspace_filter="",
        target_fps=30,
        fade_duration=0.5,
    )

    with patch("immich_memories.processing.clip_encoder._run_ffmpeg_with_progress") as run:
        encoder.run_ffmpeg_assembly(
            ["-i", str(clip.path)],
            "[0:v]null[vout];[0:a]anull[aout]",
            "[vout]",
            "[aout]",
            tmp_path / "memory.mp4",
            [clip],
            context,
        )

    command = run.call_args.args[0]
    codec = command[command.index("-c:v") + 1]
    assert codec == "libx264"
    assert all(token not in {"hevc_videotoolbox", "hevc_nvenc", "libx265"} for token in command)
    assert command[command.index("-colorspace") + 1] == "bt709"
    assert command[command.index("-color_primaries") + 1] == "bt709"
    assert command[command.index("-color_trc") + 1] == "bt709"


def test_single_clip_reencode_uses_the_same_software_h264_plan(tmp_path: Path) -> None:
    """Clip normalization must remain concat-compatible with the final output."""
    from immich_memories.processing.clip_encoder import ClipEncoder

    settings = AssemblySettings(encoding_plan=_software_h264_plan(), preserve_hdr=False)
    prober = MagicMock()
    prober.has_audio_stream.return_value = False
    prober.probe_framerate.return_value = 30.0
    encoder = ClipEncoder(settings, prober, lambda _path: None)
    clip = _make_assembly_clip(tmp_path)

    with (
        patch("immich_memories.processing.clip_encoder._detect_hdr_type", return_value=None),
        patch("immich_memories.processing.clip_encoder.subprocess.run") as run,
    ):
        run.return_value = MagicMock(returncode=0, stderr="")
        encoder.encode_single_clip(clip, tmp_path / "normalized.mp4", (1920, 1080))

    command = run.call_args.args[0]
    assert command[command.index("-c:v") + 1] == "libx264"
    assert "hevc_videotoolbox" not in command
    assert "libx265" not in command


def test_frame_accurate_trim_uses_the_same_software_h264_plan(tmp_path: Path) -> None:
    """A re-encoded trim must remain concat-compatible with every other clip."""
    from immich_memories.processing.clip_encoder import ClipEncoder

    settings = AssemblySettings(encoding_plan=_software_h264_plan(), preserve_hdr=False)
    encoder = ClipEncoder(settings, MagicMock(), lambda _path: None)
    clip = _make_assembly_clip(tmp_path)

    with (
        patch("immich_memories.processing.clip_encoder._detect_hdr_type", return_value=None),
        patch("immich_memories.processing.clip_encoder.subprocess.run") as run,
    ):
        run.return_value = MagicMock(returncode=0, stderr="")
        encoder.trim_segment_reencode(clip.path, tmp_path / "trimmed.mp4", 1.0, 2.0)

    command = run.call_args.args[0]
    assert command[command.index("-c:v") + 1] == "libx264"
    assert "hevc_videotoolbox" not in command
    assert "libx265" not in command


def test_frame_accurate_trim_converts_hlg_pixels_to_the_pq_plan(tmp_path: Path) -> None:
    """Frame-accurate trims must transform pixels before applying target tags."""
    from immich_memories.processing.clip_encoder import ClipEncoder

    plan = _software_h265_pq_plan()
    encoder = ClipEncoder(AssemblySettings(encoding_plan=plan), MagicMock(), lambda _path: None)
    clip = _make_assembly_clip(tmp_path)

    with (
        patch("immich_memories.processing.clip_encoder._detect_hdr_type", return_value="hlg"),
        patch(
            "immich_memories.processing.hdr_utilities._check_zscale_available",
            return_value=True,
        ),
        patch("immich_memories.processing.clip_encoder.subprocess.run") as run,
    ):
        run.return_value = MagicMock(returncode=0, stderr="")
        encoder.trim_segment_reencode(clip.path, tmp_path / "trimmed.mp4", 1.0, 2.0)

    command = run.call_args.args[0]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "zscale=tin=arib-std-b67:t=smpte2084" in filter_graph
    assert "format=yuv420p10le" in filter_graph


def test_frame_accurate_trim_silence_fallback_keeps_plan_conversion(tmp_path: Path) -> None:
    """Losing source audio cannot also lose the video transfer transform."""
    from immich_memories.processing.clip_encoder import ClipEncoder

    plan = _software_h265_pq_plan()
    encoder = ClipEncoder(AssemblySettings(encoding_plan=plan), MagicMock(), lambda _path: None)
    clip = _make_assembly_clip(tmp_path)

    with (
        patch("immich_memories.processing.clip_encoder._detect_hdr_type", return_value="hlg"),
        patch(
            "immich_memories.processing.hdr_utilities._check_zscale_available",
            return_value=True,
        ),
        patch("immich_memories.processing.clip_encoder.subprocess.run") as run,
    ):
        run.side_effect = [
            MagicMock(returncode=1, stderr="missing audio"),
            MagicMock(returncode=0, stderr=""),
        ]
        encoder.trim_segment_reencode(clip.path, tmp_path / "trimmed.mp4", 1.0, 2.0)

    fallback = run.call_args_list[1].args[0]
    filter_graph = fallback[fallback.index("-filter_complex") + 1]
    assert "zscale=tin=arib-std-b67:t=smpte2084" in filter_graph
    assert "format=yuv420p10le" in filter_graph


def test_streaming_assembly_uses_the_same_software_h264_plan(tmp_path: Path) -> None:
    """The scalable path must pass the resolved plan to its FFmpeg encoder."""
    from immich_memories.processing.assembly_engine import AssemblyEngine

    plan = _software_h264_plan()
    settings = AssemblySettings(
        encoding_plan=plan,
        preserve_hdr=False,
        auto_resolution=False,
        target_resolution=(1920, 1080),
    )
    prober = MagicMock()
    prober.detect_max_framerate.return_value = 30
    clips = [
        _make_assembly_clip(tmp_path, "one.mp4"),
        _make_assembly_clip(tmp_path, "two.mp4"),
    ]
    engine = AssemblyEngine(settings, prober, MagicMock(), MagicMock())

    with patch("immich_memories.processing.assembly_engine.streaming_assemble_full") as assemble:
        engine.assemble_scalable(clips, tmp_path / "memory.mp4")

    assert assemble.call_args.kwargs["encoding_plan"] is plan


def test_explicit_hdr_plan_converts_all_sdr_streaming_input(tmp_path: Path) -> None:
    """Source detection cannot override an explicitly requested HDR output."""
    from immich_memories.processing.assembly_engine import AssemblyEngine

    settings = AssemblySettings(
        encoding_plan=_software_h265_hdr_plan(),
        auto_resolution=False,
        target_resolution=(1920, 1080),
    )
    prober = MagicMock()
    prober.detect_max_framerate.return_value = 30
    clips = [
        _make_assembly_clip(tmp_path, "one.mp4"),
        _make_assembly_clip(tmp_path, "two.mp4"),
    ]
    engine = AssemblyEngine(settings, prober, MagicMock(), MagicMock())

    with (
        patch(
            "immich_memories.processing.assembly_engine._get_clip_hdr_types",
            return_value=[None, None],
        ),
        patch("immich_memories.processing.assembly_engine.streaming_assemble_full") as assemble,
    ):
        engine.assemble_scalable(clips, tmp_path / "memory.mp4")

    assert assemble.call_args.kwargs["encoding_plan"].target_transfer is HdrTransfer.HLG


def test_single_hlg_clip_is_tone_mapped_for_h264_output(tmp_path: Path) -> None:
    """The single-clip shortcut must apply the same HDR-to-SDR policy."""
    from immich_memories.processing.clip_encoder import ClipEncoder

    settings = AssemblySettings(encoding_plan=_tone_map_h264_plan(), preserve_hdr=False)
    prober = MagicMock()
    prober.has_audio_stream.return_value = False
    prober.probe_framerate.return_value = 30.0
    encoder = ClipEncoder(settings, prober, lambda _path: None)
    clip = _make_assembly_clip(tmp_path)

    with (
        patch("immich_memories.processing.clip_encoder._detect_hdr_type", return_value="hlg"),
        patch(
            "immich_memories.processing.hdr_utilities._check_zscale_available",
            return_value=True,
        ),
        patch("immich_memories.processing.clip_encoder.subprocess.run") as run,
    ):
        run.return_value = MagicMock(returncode=0, stderr="")
        encoder.encode_single_clip(clip, tmp_path / "normalized.mp4", (1920, 1080))

    command = run.call_args.args[0]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "zscale=t=linear" in filter_graph
    assert "tonemap=" in filter_graph
    assert "zscale=t=bt709" in filter_graph


@pytest.mark.parametrize(
    "plan",
    [_tone_map_h264_plan(), _software_h264_plan()],
    ids=["resolved-tone-map", "defensive-source-detection"],
)
def test_single_hdr_clip_fails_when_required_tone_map_is_unavailable(
    tmp_path: Path,
    plan: EncodingPlan,
) -> None:
    """The single-clip path must not label unconverted HDR pixels as SDR."""
    from immich_memories.processing.clip_encoder import ClipEncoder
    from immich_memories.processing.hdr_utilities import RequiredColorConversionUnavailable

    settings = AssemblySettings(encoding_plan=plan, preserve_hdr=False)
    prober = MagicMock()
    prober.has_audio_stream.return_value = False
    prober.probe_framerate.return_value = 30.0
    encoder = ClipEncoder(settings, prober, lambda _path: None)
    clip = _make_assembly_clip(tmp_path)

    with (
        patch("immich_memories.processing.clip_encoder._detect_hdr_type", return_value="hlg"),
        patch(
            "immich_memories.processing.hdr_utilities._check_zscale_available",
            return_value=False,
        ),
        patch("immich_memories.processing.clip_encoder.subprocess.run") as run,
        pytest.raises(RequiredColorConversionUnavailable),
    ):
        run.return_value = MagicMock(returncode=0, stderr="")
        encoder.encode_single_clip(clip, tmp_path / "normalized.mp4", (1920, 1080))


def test_generation_settings_resolve_one_software_h265_plan(tmp_path: Path) -> None:
    """Generation must resolve the requested codec once before assembly starts."""
    from immich_memories.config_loader import Config
    from immich_memories.generate import GenerationParams
    from immich_memories.generate_settings import _build_assembly_settings
    from immich_memories.processing.encoding_plan import HdrMode

    config = Config()
    config.hardware.enabled = False
    config.output.codec = "h265"
    config.output.hdr_mode = HdrMode.SDR
    params = GenerationParams(clips=[], output_path=tmp_path / "memory.mp4", config=config)

    settings = _build_assembly_settings(params, [])

    assert settings.encoding_plan.codec is OutputCodec.H265
    assert settings.encoding_plan.encoder == "libx265"
    assert settings.encoding_plan.hdr is False


def test_generation_settings_preserve_exact_pq_transfer(tmp_path: Path) -> None:
    """The source transfer is resolved once instead of collapsing PQ to a bool."""
    from immich_memories.config_loader import Config
    from immich_memories.generate import GenerationParams
    from immich_memories.generate_settings import _build_assembly_settings
    from immich_memories.processing.encoding_plan import HdrMode

    config = Config()
    config.hardware.enabled = False
    config.output.codec = "h265"
    config.output.hdr_mode = HdrMode.AUTO
    clip = _make_assembly_clip(tmp_path)
    params = GenerationParams(clips=[], output_path=tmp_path / "memory.mp4", config=config)

    with patch(
        "immich_memories.processing.hdr_utilities._detect_hdr_type",
        return_value="pq",
    ):
        settings = _build_assembly_settings(params, [clip])

    assert settings.encoding_plan.target_transfer is HdrTransfer.PQ
    assert settings.encoding_plan.container == "mp4"
    assert settings.output_codec == "h265"

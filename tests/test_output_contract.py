"""Tests for validated, atomic publication of final video artifacts."""

from __future__ import annotations

import errno
import json
import subprocess
from pathlib import Path

import pytest

from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec


def _h264_plan():
    return EncodingPlan(
        codec=OutputCodec.H264,
        encoder="libx264",
        encoder_args=("-c:v", "libx264"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
    )


def _h264_nv12_plan():
    return EncodingPlan(
        codec=OutputCodec.H264,
        encoder="h264_videotoolbox",
        encoder_args=("-c:v", "h264_videotoolbox"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="nv12",
        container="mp4",
    )


def _h265_hdr_plan():
    return EncodingPlan(
        codec=OutputCodec.H265,
        encoder="libx265",
        encoder_args=("-c:v", "libx265"),
        target_transfer=HdrTransfer.HLG,
        tone_map_to_sdr=False,
        pixel_format="yuv420p10le",
        container="mp4",
    )


def _h265_pq_plan():
    return EncodingPlan(
        codec=OutputCodec.H265,
        encoder="libx265",
        encoder_args=("-c:v", "libx265"),
        target_transfer=HdrTransfer.PQ,
        tone_map_to_sdr=False,
        pixel_format="yuv420p10le",
        container="mp4",
    )


def _h265_hardware_hdr_plan():
    return EncodingPlan(
        codec=OutputCodec.H265,
        encoder="hevc_videotoolbox",
        encoder_args=("-c:v", "hevc_videotoolbox"),
        target_transfer=HdrTransfer.HLG,
        tone_map_to_sdr=False,
        pixel_format="p010le",
        container="mp4",
    )


def _prores_plan():
    return EncodingPlan(
        codec=OutputCodec.PRORES,
        encoder="prores_ks",
        encoder_args=("-c:v", "prores_ks"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv422p10le",
        container="mov",
    )


def _probe_payload(**overrides: object) -> dict[str, object]:
    stream = {
        "codec_type": "video",
        "codec_name": "h264",
        "pix_fmt": "yuv420p",
        "color_transfer": "bt709",
        "color_primaries": "bt709",
        "width": 1920,
        "height": 1080,
        "nb_read_frames": "360",
    }
    format_data = {
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "duration": "12.0",
        "size": "4096",
        "tags": {"major_brand": "isom"},
    }
    stream.update(overrides.pop("stream", {}))
    format_data.update(overrides.pop("format", {}))
    assert not overrides
    return {"streams": [stream], "format": format_data}


def _install_probe(monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]) -> list[list[str]]:
    from immich_memories.processing import output_contract

    calls: list[list[str]] = []

    def run_probe(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(output_contract.subprocess, "run", run_probe)
    return calls


def test_probe_output_reads_the_video_contract_in_one_json_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second probe or omitted metadata would make publication internally inconsistent."""
    from immich_memories.processing.output_contract import OutputProbe, probe_output

    output = tmp_path / "memory.mp4"
    output.write_bytes(b"encoded-video")
    calls = _install_probe(monkeypatch, _probe_payload())

    assert probe_output(output) == OutputProbe(
        codec="h264",
        container="mp4",
        duration_seconds=12.0,
        size_bytes=4096,
        pixel_format="yuv420p",
        color_transfer="bt709",
        color_primaries="bt709",
        width=1920,
        height=1080,
        decoded_frames=360,
    )
    assert len(calls) == 1
    assert "-count_frames" in calls[0]
    assert "-of" in calls[0] and "json" in calls[0]
    entries = calls[0][calls[0].index("-show_entries") + 1]
    assert (
        "stream=codec_type,codec_name,pix_fmt,color_transfer,color_primaries,width,height,"
        "nb_read_frames" in entries
    )
    assert "format=format_name,duration,size:format_tags=major_brand" in entries


def test_codec_mismatch_keeps_the_previous_published_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A codec-family regression must not overwrite an already-valid memory."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import (
        InvalidOutputArtifact,
        publish_validated_output,
    )

    staged = tmp_path / "memory.assembling.mp4"
    final = tmp_path / "memory.mp4"
    staged.write_bytes(b"new-but-wrong-codec")
    final.write_bytes(b"previous-valid-memory")
    _install_probe(
        monkeypatch,
        _probe_payload(stream={"codec_name": "hevc"}),
    )
    replace_calls: list[tuple[Path, Path]] = []

    def track_replace(source: Path, destination: Path) -> None:
        replace_calls.append((source, destination))

    monkeypatch.setattr(output_contract.os, "replace", track_replace)

    with pytest.raises(InvalidOutputArtifact, match="expected h264, got hevc"):
        publish_validated_output(staged, final, _h264_plan())

    assert replace_calls == []
    assert final.read_bytes() == b"previous-valid-memory"
    assert staged.exists()


def test_valid_output_is_replaced_and_parent_directory_is_synced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful return must mean both the rename and its directory entry were flushed."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import publish_validated_output

    staged = tmp_path / "memory.assembling.mp4"
    final = tmp_path / "memory.mp4"
    staged.write_bytes(b"validated-video")
    _install_probe(monkeypatch, _probe_payload())
    synced: list[int] = []
    replace_calls: list[tuple[Path, Path]] = []
    real_replace = output_contract.os.replace

    def track_replace(source: Path, destination: Path) -> None:
        replace_calls.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(output_contract.os, "fsync", synced.append)
    monkeypatch.setattr(output_contract.os, "replace", track_replace)

    probe = publish_validated_output(staged, final, _h264_plan())

    assert probe.codec == "h264"
    assert final.read_bytes() == b"validated-video"
    assert not staged.exists()
    assert replace_calls == [(staged, final)]
    assert len(synced) == 1


def test_zero_byte_output_is_rejected_before_publication(tmp_path: Path) -> None:
    """An encoder-created filename is not evidence that any media was written."""
    from immich_memories.processing.output_contract import (
        InvalidOutputArtifact,
        publish_validated_output,
    )

    staged = tmp_path / "memory.assembling.mp4"
    final = tmp_path / "memory.mp4"
    staged.touch()

    with pytest.raises(InvalidOutputArtifact, match="empty"):
        publish_validated_output(staged, final, _h264_plan())

    assert staged.exists()
    assert not final.exists()


def test_missing_video_stream_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid container containing no video cannot be delivered as a memory."""
    from immich_memories.processing.output_contract import InvalidOutputArtifact, validate_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"audio-only-container")
    payload = _probe_payload()
    payload["streams"] = []
    _install_probe(monkeypatch, payload)

    with pytest.raises(InvalidOutputArtifact, match="missing video stream"):
        validate_output(staged, _h264_plan())


def test_ffprobe_failure_is_reported_as_an_invalid_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tool failure must remain a validation failure, not a JSON implementation traceback."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import InvalidOutputArtifact, probe_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"corrupt-container")

    def fail_probe(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "Invalid data found")

    monkeypatch.setattr(output_contract.subprocess, "run", fail_probe)

    with pytest.raises(InvalidOutputArtifact, match="ffprobe failed"):
        probe_output(staged)


def test_ffprobe_decode_errors_are_rejected_even_with_a_zero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ffprobe reports truncated-frame errors on stderr while still returning zero."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import InvalidOutputArtifact, probe_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"partially-decodable-container")

    def damaged_probe(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(_probe_payload(stream={"nb_read_frames": "44"})),
            "Invalid NAL unit; partial file",
        )

    monkeypatch.setattr(output_contract.subprocess, "run", damaged_probe)

    with pytest.raises(InvalidOutputArtifact, match="decode errors"):
        probe_output(staged)


def test_zero_duration_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A container with metadata but no playable timeline is not a finished video."""
    from immich_memories.processing.output_contract import InvalidOutputArtifact, validate_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"header-only-container")
    _install_probe(monkeypatch, _probe_payload(format={"duration": "0"}))

    with pytest.raises(InvalidOutputArtifact, match="positive duration"):
        validate_output(staged, _h264_plan())


def test_zero_decoded_frames_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Container duration is not enough when ffprobe cannot decode a single video frame."""
    from immich_memories.processing.output_contract import InvalidOutputArtifact, validate_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"header-without-decodable-frames")
    _install_probe(monkeypatch, _probe_payload(stream={"nb_read_frames": "0"}))

    with pytest.raises(InvalidOutputArtifact, match="positive decoded frame count"):
        validate_output(staged, _h264_plan())


def test_zero_reported_container_size_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The filesystem and demuxer must both agree that the artifact contains bytes."""
    from immich_memories.processing.output_contract import InvalidOutputArtifact, validate_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"nonempty-placeholder")
    _install_probe(monkeypatch, _probe_payload(format={"size": "0"}))

    with pytest.raises(InvalidOutputArtifact, match="positive size"):
        validate_output(staged, _h264_plan())


def test_missing_resolution_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A nominal video stream without dimensions is not decodable output."""
    from immich_memories.processing.output_contract import InvalidOutputArtifact, validate_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"invalid-video-stream")
    _install_probe(monkeypatch, _probe_payload(stream={"width": 0, "height": 0}))

    with pytest.raises(InvalidOutputArtifact, match="positive resolution"):
        validate_output(staged, _h264_plan())


def test_container_mismatch_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A MOV muxed under an MP4 filename does not satisfy the requested delivery contract."""
    from immich_memories.processing.output_contract import InvalidOutputArtifact, validate_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"quicktime-container")
    _install_probe(
        monkeypatch,
        _probe_payload(format={"tags": {"major_brand": "qt  "}}),
    )

    with pytest.raises(InvalidOutputArtifact, match="expected mp4, got mov"):
        validate_output(staged, _h264_plan())


def test_pixel_format_mismatch_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The final stream must retain the bit depth/chroma contract resolved before rendering."""
    from immich_memories.processing.output_contract import InvalidOutputArtifact, validate_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"wrong-pixel-format")
    _install_probe(monkeypatch, _probe_payload(stream={"pix_fmt": "yuv420p10le"}))

    with pytest.raises(InvalidOutputArtifact, match="expected yuv420p, got yuv420p10le"):
        validate_output(staged, _h264_plan())


def test_p010_encoder_input_accepts_the_equivalent_decoded_10_bit_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VideoToolbox p010le output is exposed by ffprobe as planar yuv420p10le."""
    from immich_memories.processing.output_contract import validate_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"hevc-10-bit-video")
    _install_probe(
        monkeypatch,
        _probe_payload(
            stream={
                "codec_name": "hevc",
                "pix_fmt": "yuv420p10le",
                "color_transfer": "arib-std-b67",
                "color_primaries": "bt2020",
            }
        ),
    )

    probe = validate_output(staged, _h265_hardware_hdr_plan())

    assert probe.pixel_format == "yuv420p10le"


def test_nv12_encoder_input_accepts_the_equivalent_decoded_planar_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hardware NV12 output is exposed by ffprobe as planar yuv420p."""
    from immich_memories.processing.output_contract import validate_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"h264-hardware-video")
    _install_probe(monkeypatch, _probe_payload(stream={"pix_fmt": "yuv420p"}))

    probe = validate_output(staged, _h264_nv12_plan())

    assert probe.pixel_format == "yuv420p"


def test_sdr_plan_rejects_hdr_transfer_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An SDR request must not silently publish a stream still tagged as HLG."""
    from immich_memories.processing.output_contract import InvalidOutputArtifact, validate_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"h264-tagged-hlg")
    _install_probe(
        monkeypatch,
        _probe_payload(
            stream={
                "color_transfer": "arib-std-b67",
                "color_primaries": "bt2020",
            }
        ),
    )

    with pytest.raises(InvalidOutputArtifact, match="expected SDR transfer bt709"):
        validate_output(staged, _h264_plan())


def test_hdr_plan_rejects_sdr_transfer_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An HDR request must not silently publish an SDR-tagged HEVC stream."""
    from immich_memories.processing.output_contract import InvalidOutputArtifact, validate_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"hevc-tagged-sdr")
    _install_probe(
        monkeypatch,
        _probe_payload(
            stream={"codec_name": "hevc", "pix_fmt": "yuv420p10le"},
        ),
    )

    with pytest.raises(InvalidOutputArtifact, match="expected HLG transfer arib-std-b67"):
        validate_output(staged, _h265_hdr_plan())


def test_hdr_plan_rejects_non_bt2020_primaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HLG transfer alone is insufficient when the stream lost its wide-gamut primaries."""
    from immich_memories.processing.output_contract import InvalidOutputArtifact, validate_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"hevc-wrong-primaries")
    _install_probe(
        monkeypatch,
        _probe_payload(
            stream={
                "codec_name": "hevc",
                "pix_fmt": "yuv420p10le",
                "color_transfer": "arib-std-b67",
                "color_primaries": "bt709",
            },
        ),
    )

    with pytest.raises(InvalidOutputArtifact, match="expected HDR primaries bt2020"):
        validate_output(staged, _h265_hdr_plan())


def test_hlg_plan_rejects_pq_transfer_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HDR is not one bucket: an HLG plan must not silently publish PQ output."""
    from immich_memories.processing.output_contract import InvalidOutputArtifact, validate_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"hevc-pq-under-hlg-plan")
    _install_probe(
        monkeypatch,
        _probe_payload(
            stream={
                "codec_name": "hevc",
                "pix_fmt": "yuv420p10le",
                "color_transfer": "smpte2084",
                "color_primaries": "bt2020",
            },
        ),
    )

    with pytest.raises(
        InvalidOutputArtifact,
        match="expected HLG transfer arib-std-b67, got smpte2084",
    ):
        validate_output(staged, _h265_hdr_plan())


def test_sdr_plan_rejects_non_bt709_primaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tone-mapped output must carry the matching BT.709 gamut metadata."""
    from immich_memories.processing.output_contract import InvalidOutputArtifact, validate_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"h264-wrong-primaries")
    _install_probe(monkeypatch, _probe_payload(stream={"color_primaries": "bt2020"}))

    with pytest.raises(InvalidOutputArtifact, match="expected SDR primaries bt709"):
        validate_output(staged, _h264_plan())


def test_malformed_ffprobe_json_is_reported_as_an_invalid_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful process exit with unusable metadata still fails the contract cleanly."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import InvalidOutputArtifact, probe_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"broken-metadata")

    def malformed_probe(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, "not-json", "")

    monkeypatch.setattr(output_contract.subprocess, "run", malformed_probe)

    with pytest.raises(InvalidOutputArtifact, match="invalid ffprobe metadata"):
        probe_output(staged)


def test_incomplete_ffprobe_metadata_is_reported_as_an_invalid_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing required typed fields must not leak a parser KeyError to callers."""
    from immich_memories.processing.output_contract import InvalidOutputArtifact, probe_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"incomplete-metadata")
    payload = _probe_payload()
    del payload["streams"][0]["pix_fmt"]
    _install_probe(monkeypatch, payload)

    with pytest.raises(InvalidOutputArtifact, match="invalid ffprobe metadata"):
        probe_output(staged)


def test_missing_decoded_frame_evidence_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A probe without frame-count evidence cannot certify decodability."""
    from immich_memories.processing.output_contract import InvalidOutputArtifact, probe_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"metadata-without-frame-count")
    payload = _probe_payload()
    del payload["streams"][0]["nb_read_frames"]
    _install_probe(monkeypatch, payload)

    with pytest.raises(InvalidOutputArtifact, match="missing decoded frame evidence"):
        probe_output(staged)


def test_long_memory_full_decode_has_a_fifteen_minute_budget_without_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid long memory must not inherit the old metadata-only 30-second timeout."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import probe_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"ten-minute-memory")
    observed_timeouts: list[object] = []

    def simulated_long_probe(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        timeout = kwargs["timeout"]
        observed_timeouts.append(timeout)
        if not isinstance(timeout, int) or timeout < 90:
            raise subprocess.TimeoutExpired(command, timeout)
        payload = _probe_payload(
            stream={"nb_read_frames": "18000"},
            format={"duration": "600.0"},
        )
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(output_contract.subprocess, "run", simulated_long_probe)

    probe = probe_output(staged)

    assert probe.duration_seconds == 600.0
    assert observed_timeouts == [15 * 60]


def test_ffprobe_timeout_is_reported_as_an_invalid_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wedged probe cannot leave the caller treating an unverified file as valid."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import InvalidOutputArtifact, probe_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"slow-container")

    def timeout_probe(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(output_contract.subprocess, "run", timeout_probe)

    with pytest.raises(InvalidOutputArtifact, match="ffprobe failed"):
        probe_output(staged)


def test_missing_output_is_reported_as_an_invalid_artifact(tmp_path: Path) -> None:
    """An assembler returning a path is not proof that it created the path."""
    from immich_memories.processing.output_contract import InvalidOutputArtifact, validate_output

    missing = tmp_path / "memory.assembling.mp4"

    with pytest.raises(InvalidOutputArtifact, match="does not exist"):
        validate_output(missing, _h264_plan())


def test_publication_rejects_a_staged_file_from_another_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cross-directory replacement cannot provide the promised sibling-file atomicity."""
    from immich_memories.processing.output_contract import (
        InvalidOutputArtifact,
        publish_validated_output,
    )

    stage_dir = tmp_path / "staging"
    final_dir = tmp_path / "published"
    stage_dir.mkdir()
    final_dir.mkdir()
    staged = stage_dir / "memory.assembling.mp4"
    final = final_dir / "memory.mp4"
    staged.write_bytes(b"valid-but-not-sibling")
    _install_probe(monkeypatch, _probe_payload())

    with pytest.raises(InvalidOutputArtifact, match="staged sibling"):
        publish_validated_output(staged, final, _h264_plan())

    assert staged.exists()
    assert not final.exists()


def test_publication_rejects_identical_staged_and_final_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomic publication requires two distinct sibling directory entries."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import (
        InvalidOutputArtifact,
        publish_validated_output,
    )

    path = tmp_path / "memory.mp4"
    path.write_bytes(b"valid-video")
    _install_probe(monkeypatch, _probe_payload())
    replace_calls: list[tuple[Path, Path]] = []

    def track_replace(source: Path, destination: Path) -> None:
        replace_calls.append((source, destination))

    monkeypatch.setattr(output_contract.os, "replace", track_replace)

    with pytest.raises(InvalidOutputArtifact, match="distinct staged sibling"):
        publish_validated_output(path, path, _h264_plan())

    assert replace_calls == []
    assert path.read_bytes() == b"valid-video"


def test_prores_ffprobe_codec_name_and_mov_brand_are_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FFmpeg encoder name prores_ks is reported by ffprobe as codec_name=prores."""
    from immich_memories.processing.output_contract import validate_output

    staged = tmp_path / "memory.assembling.mov"
    staged.write_bytes(b"prores-video")
    _install_probe(
        monkeypatch,
        _probe_payload(
            stream={"codec_name": "prores", "pix_fmt": "yuv422p10le"},
            format={"tags": {"major_brand": "qt  "}},
        ),
    )

    probe = validate_output(staged, _prores_plan())

    assert probe.codec == "prores"
    assert probe.container == "mov"


def test_hevc_hlg_output_matching_the_plan_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive HDR path accepts the names and tags emitted by ffprobe."""
    from immich_memories.processing.output_contract import validate_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"hevc-hlg-video")
    _install_probe(
        monkeypatch,
        _probe_payload(
            stream={
                "codec_name": "hevc",
                "pix_fmt": "yuv420p10le",
                "color_transfer": "arib-std-b67",
                "color_primaries": "bt2020",
            }
        ),
    )

    probe = validate_output(staged, _h265_hdr_plan())

    assert probe.codec == "hevc"
    assert probe.color_transfer == "arib-std-b67"


def test_hevc_pq_output_matching_the_plan_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact PQ branch accepts SMPTE ST 2084 and rejects transfer-family collapse."""
    from immich_memories.processing.output_contract import validate_output

    staged = tmp_path / "memory.assembling.mp4"
    staged.write_bytes(b"hevc-pq-video")
    _install_probe(
        monkeypatch,
        _probe_payload(
            stream={
                "codec_name": "hevc",
                "pix_fmt": "yuv420p10le",
                "color_transfer": "smpte2084",
                "color_primaries": "bt2020",
            }
        ),
    )

    probe = validate_output(staged, _h265_pq_plan())

    assert probe.codec == "hevc"
    assert probe.color_transfer == "smpte2084"


def test_publication_rejects_a_final_suffix_that_disagrees_with_the_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid MOV must not be delivered under a misleading .mp4 filename."""
    from immich_memories.processing.output_contract import (
        InvalidOutputArtifact,
        publish_validated_output,
    )

    staged = tmp_path / "memory.assembling.mov"
    final = tmp_path / "memory.mp4"
    staged.write_bytes(b"prores-video")
    _install_probe(
        monkeypatch,
        _probe_payload(
            stream={"codec_name": "prores", "pix_fmt": "yuv422p10le"},
            format={"tags": {"major_brand": "qt  "}},
        ),
    )

    with pytest.raises(InvalidOutputArtifact, match="final suffix"):
        publish_validated_output(staged, final, _prores_plan())

    assert staged.exists()
    assert not final.exists()


def test_unsupported_directory_fsync_does_not_undo_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Platforms without directory fsync still get atomic replacement semantics."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import publish_validated_output

    staged = tmp_path / "memory.assembling.mp4"
    final = tmp_path / "memory.mp4"
    staged.write_bytes(b"validated-video")
    _install_probe(monkeypatch, _probe_payload())

    def unsupported_fsync(_fd: int) -> None:
        raise OSError(errno.EINVAL, "directory fsync is unsupported")

    monkeypatch.setattr(output_contract.os, "fsync", unsupported_fsync)

    probe = publish_validated_output(staged, final, _h264_plan())

    assert probe.codec == "h264"
    assert final.read_bytes() == b"validated-video"


def test_unsupported_directory_open_does_not_undo_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Some platforms reject opening directories before fsync is attempted."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import publish_validated_output

    staged = tmp_path / "memory.assembling.mp4"
    final = tmp_path / "memory.mp4"
    staged.write_bytes(b"validated-video")
    _install_probe(monkeypatch, _probe_payload())

    def unsupported_open(_path: Path, _flags: int) -> int:
        raise OSError(errno.EINVAL, "directory open is unsupported")

    monkeypatch.setattr(output_contract.os, "open", unsupported_open)

    probe = publish_validated_output(staged, final, _h264_plan())

    assert probe.codec == "h264"
    assert final.read_bytes() == b"validated-video"

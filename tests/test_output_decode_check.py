"""The finished film is read in two steps, and the slow one is bounded by the render."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec
from tests.output_tools_fake import (
    is_decode_check,
    output_tools,
    progress_file_of,
    write_decode_progress,
)


def _h264_plan() -> EncodingPlan:
    return EncodingPlan(
        codec=OutputCodec.H264,
        encoder="libx264",
        encoder_args=("-c:v", "libx264"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
    )


def _payload(*, duration: str = "12.0", codec: str = "h264") -> dict[str, object]:
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
                "nb_frames": "360",
            }
        ],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": duration,
            "size": "4096",
            "tags": {"major_brand": "isom"},
        },
    }


@pytest.fixture
def staged(tmp_path: Path) -> Path:
    path = tmp_path / "memory.assembling.mp4"
    path.write_bytes(b"encoded-video")
    return path


def test_metadata_is_read_without_decoding_before_the_decode_check(
    staged: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading the container is instant; only the second step decodes the stream."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import validate_output

    calls: list[tuple[list[str], dict[str, object]]] = []
    # WHY: ffprobe and ffmpeg are the external processes the contract shells out to.
    monkeypatch.setattr(output_contract.subprocess, "run", output_tools(_payload(), calls=calls))

    probe = validate_output(staged, _h264_plan())

    commands = [command for command, _ in calls]
    assert [command[0] for command in commands] == ["ffprobe", "ffmpeg"]
    metadata_probe = commands[0]
    assert "-count_frames" not in metadata_probe
    entries = metadata_probe[metadata_probe.index("-show_entries") + 1]
    assert "nb_frames" in entries
    assert "format=format_name,duration,size:format_tags=major_brand" in entries
    assert is_decode_check(commands[1])
    assert commands[1][commands[1].index("-map") + 1] == "0:v:0"
    assert probe.decoded_frames == 360


def test_a_plan_mismatch_fails_before_any_decoding(
    staged: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wrong codec is known from the container; decoding hours of video first is waste."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import InvalidOutputArtifact, validate_output

    calls: list[tuple[list[str], dict[str, object]]] = []
    # WHY: ffprobe and ffmpeg are the external processes the contract shells out to.
    monkeypatch.setattr(
        output_contract.subprocess, "run", output_tools(_payload(codec="hevc"), calls=calls)
    )

    with pytest.raises(InvalidOutputArtifact, match="expected h264, got hevc"):
        validate_output(staged, _h264_plan())

    assert [command[0] for command, _ in calls] == ["ffprobe"]


def _decode_timeouts(calls: list[tuple[list[str], dict[str, object]]]) -> list[object]:
    return [kwargs["timeout"] for command, kwargs in calls if is_decode_check(command)]


@pytest.mark.parametrize(
    ("encode_seconds", "duration", "expected_budget"),
    [
        (38_946.0, "4486.3", 38_946.0),
        (120.0, "60.0", 15 * 60),
        (None, "60.0", 15 * 60),
        (None, "4486.3", 4486.3 * 4),
    ],
    ids=["encode-time", "encode-floor", "unknown-floor", "unknown-long-film"],
)
def test_the_decode_budget_is_the_render_encode_time_or_derived_from_duration(
    staged: Path,
    monkeypatch: pytest.MonkeyPatch,
    encode_seconds: float | None,
    duration: str,
    expected_budget: float,
) -> None:
    """A 75-minute film that took 11 hours to encode gets 11 hours to decode, not 15 minutes."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import DecodeCheck, validate_output

    calls: list[tuple[list[str], dict[str, object]]] = []
    # WHY: ffprobe and ffmpeg are the external processes the contract shells out to.
    monkeypatch.setattr(
        output_contract.subprocess, "run", output_tools(_payload(duration=duration), calls=calls)
    )

    validate_output(staged, _h264_plan(), DecodeCheck(encode_seconds=encode_seconds))

    assert _decode_timeouts(calls) == [pytest.approx(expected_budget)]


def test_a_decode_check_past_the_encode_time_fails_and_keeps_the_staged_film(
    tmp_path: Path, staged: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The render is not thrown away: the error says why and where the file is."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import (
        DecodeCheck,
        InvalidOutputArtifact,
        publish_validated_output,
    )

    answer = output_tools(_payload(duration="4486.3"))

    def decode_never_finishes(command: list[str], **kwargs: object):
        if is_decode_check(command):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return answer(command, **kwargs)

    # WHY: ffmpeg is the external process; a real 11-hour decode is what this stands in for.
    monkeypatch.setattr(output_contract.subprocess, "run", decode_never_finishes)
    final = tmp_path / "memory.mp4"

    with pytest.raises(InvalidOutputArtifact) as caught:
        publish_validated_output(
            staged, final, _h264_plan(), decode_check=DecodeCheck(encode_seconds=38_946.0)
        )

    message = str(caught.value)
    assert "the decode check did not finish within the render's own encode time" in message
    assert "10:49:06" in message
    assert str(staged) in message
    assert staged.read_bytes() == b"encoded-video"
    assert not final.exists()


def test_a_decode_check_without_encode_time_names_its_own_budget(
    staged: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A film rendered elsewhere is not blamed on an encode time this machine never had."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import InvalidOutputArtifact, validate_output

    answer = output_tools(_payload(duration="600.0"))

    def decode_never_finishes(command: list[str], **kwargs: object):
        if is_decode_check(command):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return answer(command, **kwargs)

    # WHY: ffmpeg is the external process; the timeout is what a stalled decode raises.
    monkeypatch.setattr(output_contract.subprocess, "run", decode_never_finishes)

    with pytest.raises(InvalidOutputArtifact, match=r"did not finish within its 40:00 budget"):
        validate_output(staged, _h264_plan())


def test_decode_errors_still_fail_the_film(staged: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A truncated stream decodes with errors on stderr and a zero exit; that is not a film."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import InvalidOutputArtifact, validate_output

    # WHY: ffmpeg is the external process that reports damaged frames on stderr.
    monkeypatch.setattr(
        output_contract.subprocess,
        "run",
        output_tools(_payload(), decoded_frames=44, decode_stderr="Invalid NAL unit size"),
    )

    with pytest.raises(InvalidOutputArtifact, match="decode errors"):
        validate_output(staged, _h264_plan())

    assert staged.exists()


def test_an_unfinished_decode_is_not_frame_evidence(
    staged: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ffmpeg writes progress=end only once it has read the whole stream."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import InvalidOutputArtifact, validate_output

    answer = output_tools(_payload())

    def decode_stops_early(command: list[str], **kwargs: object):
        if is_decode_check(command):
            write_decode_progress(command, 120, finished=False)
            return subprocess.CompletedProcess(command, 0, "", "")
        return answer(command, **kwargs)

    # WHY: ffmpeg is the external process whose progress file is the frame evidence.
    monkeypatch.setattr(output_contract.subprocess, "run", decode_stops_early)

    with pytest.raises(InvalidOutputArtifact, match="missing decoded frame evidence"):
        validate_output(staged, _h264_plan())


def test_a_long_decode_check_reports_how_far_it_got(
    staged: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Minutes of silence after the encode read as a hang; a line per interval does not."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import DecodeCheck, validate_output

    answer = output_tools(_payload(duration="4486.3"))
    heard: list[str] = []
    reported = threading.Event()

    def slow_decode(command: list[str], **kwargs: object):
        if not is_decode_check(command):
            return answer(command, **kwargs)
        write_decode_progress(command, 22_500, finished=False, out_time_us=754_000_000)
        assert reported.wait(timeout=5), "no progress was reported while decoding"
        write_decode_progress(command, 134_589)
        return subprocess.CompletedProcess(command, 0, "", "")

    expected = "Checking the finished film: 12:34 of 1:14:46 decoded"

    def hear(message: str) -> None:
        heard.append(message)
        if message == expected:
            reported.set()

    # WHY: ffmpeg is the external process; this one only finishes once progress was heard.
    monkeypatch.setattr(output_contract.subprocess, "run", slow_decode)
    # WHY: the real interval is a minute; the test cannot wait that long for one line.
    monkeypatch.setattr(output_contract, "_DECODE_PROGRESS_INTERVAL_SECONDS", 0.01)
    caplog.set_level("INFO", logger=output_contract.__name__)

    probe = validate_output(staged, _h264_plan(), DecodeCheck(progress=hear))

    assert probe.decoded_frames == 134_589
    assert expected in heard
    assert expected in caplog.text


def test_an_eleven_hour_progress_file_is_read_from_its_tail(
    staged: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hundreds of progress blocks later, the last one is still the frame evidence."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import validate_output

    answer = output_tools(_payload())

    def long_decode(command: list[str], **kwargs: object):
        if not is_decode_check(command):
            return answer(command, **kwargs)
        blocks = [
            f"frame={n * 900}\nout_time_us={n * 30_000_000}\nprogress=continue\n"
            for n in range(1, 1320)
        ]
        blocks.append("frame=1188900\nout_time_us=39630000000\nprogress=end\n")
        progress_file_of(command).write_text("".join(blocks), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    # WHY: ffmpeg is the external process; this is the file an eleven-hour decode leaves.
    monkeypatch.setattr(output_contract.subprocess, "run", long_decode)

    assert validate_output(staged, _h264_plan()).decoded_frames == 1_188_900


def test_a_frame_count_that_disagrees_with_the_container_is_logged_not_fatal(
    staged: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Edit lists can make the two counts differ; a false failure is what this check must not add."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import validate_output

    # WHY: ffprobe and ffmpeg are the external processes that read the film.
    monkeypatch.setattr(
        output_contract.subprocess, "run", output_tools(_payload(), decoded_frames=358)
    )
    caplog.set_level("INFO", logger=output_contract.__name__)

    probe = validate_output(staged, _h264_plan())

    assert probe.decoded_frames == 358
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == [
        "Checked the finished film in 0:00: 358 frames decoded, the container lists 360"
    ]


def test_a_finished_decode_check_logs_its_frames_and_time(
    staged: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import validate_output

    # WHY: ffprobe and ffmpeg are the external processes that read the film.
    monkeypatch.setattr(output_contract.subprocess, "run", output_tools(_payload()))
    caplog.set_level("INFO", logger=output_contract.__name__)

    validate_output(staged, _h264_plan())

    assert "Checked the finished film in 0:00: 360 frames decoded" in caplog.messages


def _decodes(calls: list[tuple[list[str], dict[str, object]]]) -> int:
    return sum(1 for command, _ in calls if is_decode_check(command))


def test_the_metadata_check_decodes_nothing(staged: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import check_output

    calls: list[tuple[list[str], dict[str, object]]] = []
    # WHY: ffprobe and ffmpeg are the external processes the contract shells out to.
    monkeypatch.setattr(output_contract.subprocess, "run", output_tools(_payload(), calls=calls))

    probe = check_output(staged, _h264_plan())

    assert _decodes(calls) == 0
    assert probe.decoded_frames is None
    assert probe.duration_seconds == 12.0


def test_a_checked_film_that_has_not_changed_is_not_decoded_again(
    staged: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import validate_output

    calls: list[tuple[list[str], dict[str, object]]] = []
    # WHY: ffprobe and ffmpeg are the external processes the contract shells out to.
    monkeypatch.setattr(output_contract.subprocess, "run", output_tools(_payload(), calls=calls))
    checked = validate_output(staged, _h264_plan())
    moved = staged.with_name("memory.mp4")
    staged.rename(moved)

    again = validate_output(moved, _h264_plan(), verified=checked)

    assert _decodes(calls) == 1
    assert [command[0] for command, _ in calls] == ["ffprobe", "ffmpeg", "ffprobe"]
    assert again.decoded_frames == checked.decoded_frames == 360


def test_a_checked_film_that_changed_is_decoded_again(
    staged: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import validate_output

    calls: list[tuple[list[str], dict[str, object]]] = []
    # WHY: ffprobe and ffmpeg are the external processes the contract shells out to.
    monkeypatch.setattr(output_contract.subprocess, "run", output_tools(_payload(), calls=calls))
    checked = validate_output(staged, _h264_plan())
    staged.write_bytes(b"re-encoded by something else")

    validate_output(staged, _h264_plan(), verified=checked)

    assert _decodes(calls) == 2


def test_a_film_written_to_during_its_decode_is_not_passed(
    staged: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recorded stamp must describe the bytes that were decoded."""
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import InvalidOutputArtifact, validate_output

    answer = output_tools(_payload())

    def decode_while_written(command: list[str], **kwargs: object):
        if is_decode_check(command):
            staged.write_bytes(b"encoded-video, still being written")
        return answer(command, **kwargs)

    # WHY: ffmpeg is the external process; the write stands in for a second writer.
    monkeypatch.setattr(output_contract.subprocess, "run", decode_while_written)

    with pytest.raises(InvalidOutputArtifact, match="changed while it was being checked"):
        validate_output(staged, _h264_plan())

"""A merged Live Photo burst is encoded under a plan, not a hardcoded encoder (#504).

The merge runs at download time, before the run's own EncodingPlan exists, so it
used to hardcode libx264 (libx265 for HDR) with no quality setting and no
hardware encoder. On a hardware-encode machine the bursts were the only
software-encoded clips in the memory.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from immich_memories.processing.hardware import HWAccelBackend, HWAccelCapabilities
from immich_memories.processing.live_photo_merger import build_merge_command


def _apple() -> HWAccelCapabilities:
    return HWAccelCapabilities(
        backend=HWAccelBackend.APPLE,
        supports_h264_encode=True,
        supports_h265_encode=True,
    )


def _merge_command(
    *,
    is_hdr: bool = False,
    hardware_enabled: bool = True,
    capabilities: HWAccelCapabilities | None = None,
) -> list[str]:
    caps = capabilities if capabilities is not None else HWAccelCapabilities()
    # WHY: build_merge_command probes the file and the host encoder; a.mov never exists.
    with (
        # WHY: the audio ffprobe boundary.
        patch(
            "immich_memories.processing.live_photo_merger.probe_clip_has_audio",
            return_value=False,
        ),
        # WHY: the HDR ffprobe boundary.
        patch(
            "immich_memories.processing.live_photo_merger._detect_clip_hdr",
            return_value=is_hdr,
        ),
        # WHY: the FFmpeg encoder probe, so the assertions do not depend on the
        # machine running the tests.
        patch(
            "immich_memories.processing.live_photo_merger.detect_hardware_acceleration",
            return_value=caps,
        ),
    ):
        return build_merge_command(
            [Path("a.mov")],
            [(0.0, 1.0)],
            Path("out.mp4"),
            hardware_enabled=hardware_enabled,
        )


def _encoder(cmd: list[str]) -> str:
    return cmd[cmd.index("-c:v") + 1]


def test_the_burst_is_encoded_at_a_stated_quality() -> None:
    """No CRF meant the encoder's own default, which is not the one the rest of
    the run uses and is not written down anywhere."""
    cmd = _merge_command()

    assert "-crf" in cmd
    assert cmd[cmd.index("-crf") + 1] == "18"


def test_a_hardware_machine_encodes_the_burst_in_hardware() -> None:
    """Bursts were the only software-encoded clips on a hardware-encode setup."""
    assert _encoder(_merge_command(capabilities=_apple())) == "h264_videotoolbox"


def test_disabling_hardware_is_still_honoured() -> None:
    """The probe is process-wide, but the user's switch still decides."""
    assert _encoder(_merge_command(capabilities=_apple(), hardware_enabled=False)) == "libx264"


def test_no_hardware_available_falls_back_to_software() -> None:
    assert _encoder(_merge_command()) == "libx264"


@pytest.mark.parametrize("hardware_enabled", [True, False])
def test_an_hdr_burst_keeps_its_hlg_metadata_and_ten_bits(hardware_enabled: bool) -> None:
    """The merged file is an intermediate the assembler still has to read as HDR.
    Losing the transfer here would tone-map the burst before anything chose to."""
    cmd = _merge_command(is_hdr=True, capabilities=_apple(), hardware_enabled=hardware_enabled)

    assert cmd[cmd.index("-color_trc") + 1] == "arib-std-b67"
    assert cmd[cmd.index("-color_primaries") + 1] == "bt2020"
    assert cmd[cmd.index("-colorspace") + 1] == "bt2020nc"
    assert "10" in cmd[cmd.index("-pix_fmt") + 1]
    # HEVC in MP4 needs hvc1 rather than hev1 to play on Apple devices.
    assert cmd[cmd.index("-tag:v") + 1] == "hvc1"


def test_an_hdr_burst_uses_the_hardware_hevc_encoder() -> None:
    assert _encoder(_merge_command(is_hdr=True, capabilities=_apple())) == "hevc_videotoolbox"


def test_the_download_path_carries_the_setting_into_the_merge() -> None:
    """The plan is only worth resolving if the run's switch reaches it: the merge
    happens under the downloader, several hops from where the config is read."""
    from immich_memories.generate_downloads import _try_merge_burst

    clips = [Path("a.mov"), Path("b.mov")]
    trims = [(0.0, 1.0), (0.0, 1.0)]

    # WHY: _try_merge_burst validates, builds, and runs FFmpeg; a.mov and b.mov are names only.
    with (
        # WHY: the ffprobe validation of files that do not exist on disk.
        patch(
            "immich_memories.processing.live_photo_merger.filter_valid_clips",
            return_value=(clips, trims),
        ),
        # WHY: the unit under test is what gets asked for, not the command itself.
        patch(
            "immich_memories.processing.live_photo_merger.build_merge_command",
            return_value=["ffmpeg"],
        ) as build,
        # WHY: replaces the FFmpeg run.
        patch("immich_memories.generate_downloads.subprocess.run"),
    ):
        _try_merge_burst(clips, trims, Path("out.mp4"), hardware_enabled=False)

    assert build.call_args.kwargs["hardware_enabled"] is False

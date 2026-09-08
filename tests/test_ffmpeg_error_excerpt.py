"""FFmpeg process helpers: the failure excerpt and the version banner."""

from __future__ import annotations

import pytest

from immich_memories.processing.ffmpeg_runner import (
    _parse_ffmpeg_major,
    ffmpeg_error_excerpt,
)


def test_progress_ticker_and_deprecation_notice_are_dropped() -> None:
    stderr = (
        "-filter_complex_script is deprecated, use -/filter_complex g.txt instead\n"
        "Input #0, wav, from 'a.wav':\n"
        "[aac @ 0x1] Error while encoding: Invalid argument\n"
        "size=    3072KiB time=00:02:24.04 bitrate= 174.7kbits/s speed=28.8x    \r"
        "size=    5632KiB time=00:04:15.91 bitrate= 180.3kbits/s speed=  32x    \r"
    )
    assert ffmpeg_error_excerpt(stderr) == (
        "Input #0, wav, from 'a.wav':\n[aac @ 0x1] Error while encoding: Invalid argument"
    )


def test_only_progress_falls_back_to_the_raw_tail() -> None:
    stderr = "size=1KiB time=00:00:01.00\rsize=2KiB time=00:00:02.00\r"
    assert ffmpeg_error_excerpt(stderr) == stderr.strip()


def test_excerpt_keeps_the_last_lines_only() -> None:
    stderr = "\n".join(f"line {i}" for i in range(20))
    assert ffmpeg_error_excerpt(stderr, max_lines=3) == "line 17\nline 18\nline 19"


@pytest.mark.parametrize(
    ("banner", "major"),
    [
        ("ffmpeg version 9.0.1 Copyright (c) 2000-2026 the FFmpeg developers", 9),
        ("ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023 the FFmpeg developers", 6),
        ("ffmpeg version n7.1.1 Copyright (c) 2000-2025 the FFmpeg developers", 7),
        ("ffmpeg version 4.4.2-0ubuntu0.22.04.1 Copyright (c) 2000-2021", 4),
        ("ffmpeg version N-118000-g0123abcd Copyright (c) 2000-2025", 99),
        ("bash: ffmpeg: command not found", 0),
    ],
)
def test_major_version_is_read_from_the_banner(banner: str, major: int) -> None:
    assert _parse_ffmpeg_major(banner) == major

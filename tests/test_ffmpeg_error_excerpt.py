"""The FFmpeg failure excerpt keeps the cause, not the progress ticker."""

from __future__ import annotations

from immich_memories.processing.ffmpeg_runner import ffmpeg_error_excerpt


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

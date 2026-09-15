"""Fit selected intervals without placing a new cut inside detected speech."""

from __future__ import annotations


def safe_end(carrier: dict, seconds: float, *, expand: bool = False) -> float:
    """Keep the start fixed; move an end in speech to the nearest allowed side."""
    start = carrier.get("start_time", 0.0)
    end = start + seconds
    ranges = carrier.get("speech_regions", [])
    ordered = ranges if expand else reversed(ranges)
    for left, right in ordered:
        if left < end < right:
            end = right if expand else left
    return end


def set_duration(carrier: dict, seconds: float) -> None:
    carrier["seconds"] = round(seconds, 6)
    if "start_time" in carrier or "end_time" in carrier:
        carrier["end_time"] = carrier.get("start_time", 0.0) + carrier["seconds"]


def minimum_duration(carrier: dict, minimum: float) -> float:
    """An utterance crossing the minimum hold is indivisible until its next pause."""
    duration = carrier.get("seconds", minimum)
    held = min(duration, minimum)
    return min(
        duration,
        safe_end(carrier, held, expand=True) - carrier.get("start_time", 0.0),
    )

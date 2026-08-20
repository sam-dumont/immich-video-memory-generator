"""Short-form presets: one flag for the vertical 15/30/60/90s formats.

`--duration 30 --orientation portrait` already worked. The preset exists so the
supported short-form lengths are discoverable and so vertical is the default
shape for them, which is what Reels/Shorts/TikTok actually take.
"""

from __future__ import annotations

import pytest

from immich_memories.cli.generate import SHORT_FORM_SECONDS, resolve_short_form


def test_the_preset_sets_the_duration() -> None:
    assert resolve_short_form("30", duration=None, orientation="landscape").duration == 30


def test_the_preset_turns_the_video_vertical() -> None:
    """Short-form is a vertical format; that is the point of the preset."""
    resolved = resolve_short_form("30", duration=None, orientation="landscape")

    assert resolved.orientation == "portrait"


def test_an_explicit_orientation_beats_the_preset() -> None:
    """Square short-form is a real thing; the preset must not overrule a request."""
    resolved = resolve_short_form(
        "30", duration=None, orientation="square", orientation_was_given=True
    )

    assert resolved.orientation == "square"


def test_an_explicit_duration_beats_the_preset() -> None:
    resolved = resolve_short_form("30", duration=45, orientation="landscape")

    assert resolved.duration == 45


def test_without_the_preset_nothing_changes() -> None:
    resolved = resolve_short_form(None, duration=None, orientation="landscape")

    assert resolved.duration is None
    assert resolved.orientation == "landscape"


@pytest.mark.parametrize("seconds", SHORT_FORM_SECONDS)
def test_every_advertised_length_resolves(seconds: str) -> None:
    assert resolve_short_form(seconds, duration=None, orientation="landscape").duration == int(
        seconds
    )

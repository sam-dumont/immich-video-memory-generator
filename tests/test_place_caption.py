"""Place names in the per-clip caption (#313, reworked in #464/#465).

`AssemblyClip.location_name` is already populated from Immich EXIF as
"City, Country". Escaping is mandatory: a colon separates drawtext's own
options, so an unescaped place name does not merely look wrong, it fails the
filter graph. Timeline semantics (dedupe, date wording) live in
test_caption_timeline.py.
"""

from __future__ import annotations

from pathlib import Path

from immich_memories.processing.assembly_config import AssemblyClip
from immich_memories.processing.clip_caption import (
    ClipCaption,
    caption_filters,
    captions_for_timeline,
)


def _clip(date: str | None = "2026-01-05", place: str | None = None) -> AssemblyClip:
    return AssemblyClip(
        path=Path("/x.mp4"), duration=4.0, date=date, asset_id="x", location_name=place
    )


def _place_filter(text: str) -> str:
    (only,) = caption_filters(ClipCaption(place=text), 1920, 1080)
    return only


class TestPlaceInTheCaption:
    def test_place_is_shown_when_asked_for(self):
        (caption,) = captions_for_timeline([_clip(place="Paris, France")], place=True)

        assert caption.place == "Paris, France"

    def test_place_is_left_out_unless_asked_for(self):
        (caption,) = captions_for_timeline([_clip(place="Paris, France")])

        assert caption.place == ""

    def test_a_clip_without_a_place_still_captions_its_date(self):
        (caption,) = captions_for_timeline([_clip(place=None)], place=True)

        assert caption.place == "" and caption.date != ""

    def test_place_alone_when_the_clip_has_no_date(self):
        (caption,) = captions_for_timeline([_clip(date=None, place="Paris, France")], place=True)

        assert caption.place == "Paris, France" and caption.date == ""


class TestEscaping:
    """drawtext parses its own options out of the value; a raw colon breaks it."""

    def test_a_colon_is_escaped(self):
        assert "\\:" in _place_filter("Tel Aviv: Yafo")

    def test_an_apostrophe_survives_as_a_typographic_one(self):
        """Measured: drawtext drops an ASCII apostrophe however it is escaped,
        rendering "LAquila". U+2019 is the only form that reaches the screen."""
        out = _place_filter("L'Aquila")

        assert "L\u2019Aquila" in out
        assert "L'Aquila" not in out

    def test_a_backslash_is_escaped_before_anything_else(self):
        """Escaping backslash last would double-escape what came before it."""
        assert "\\\\" in _place_filter("a\\b")

"""Place names in the per-clip caption (#313).

`AssemblyClip.location_name` is already populated from Immich EXIF as
"City, Country" (generate_clips.py:173) and nothing displayed it. Adding it
makes escaping mandatory: a colon separates drawtext's own options, so an
unescaped place name does not merely look wrong, it fails the filter graph.
"""

from __future__ import annotations

from pathlib import Path

from immich_memories.processing.assembly_config import AssemblyClip
from immich_memories.processing.clip_caption import caption_filter, caption_for


def _clip(date: str | None = "2026-01-05", place: str | None = None) -> AssemblyClip:
    return AssemblyClip(
        path=Path("/x.mp4"), duration=4.0, date=date, asset_id="x", location_name=place
    )


class TestPlaceInTheCaption:
    def test_place_is_shown_when_asked_for(self):
        assert "Paris" in caption_for(_clip(place="Paris, France"), place=True)

    def test_place_and_date_read_as_one_line(self):
        caption = caption_for(_clip(place="Paris, France"), place=True)

        assert "Paris, France" in caption
        assert "5 Jan 2026" in caption

    def test_place_is_left_out_unless_asked_for(self):
        assert caption_for(_clip(place="Paris, France")) == "5 Jan 2026"

    def test_a_clip_without_a_place_still_captions_its_date(self):
        assert caption_for(_clip(place=None), place=True) == "5 Jan 2026"

    def test_place_alone_when_the_clip_has_no_date(self):
        assert caption_for(_clip(date=None, place="Paris, France"), place=True) == "Paris, France"


class TestEscaping:
    """drawtext parses its own options out of the value; a raw colon breaks it."""

    def test_a_colon_is_escaped(self):
        assert "\\:" in caption_filter("Tel Aviv: Yafo", 1920, 1080)

    def test_an_apostrophe_survives_as_a_typographic_one(self):
        """Measured: drawtext drops an ASCII apostrophe however it is escaped,
        rendering "LAquila". U+2019 is the only form that reaches the screen."""
        out = caption_filter("L'Aquila", 1920, 1080)

        assert "L\u2019Aquila" in out
        assert "L'Aquila" not in out

    def test_a_backslash_is_escaped_before_anything_else(self):
        """Escaping backslash last would double-escape what came before it."""
        assert "\\\\" in caption_filter("a\\b", 1920, 1080)

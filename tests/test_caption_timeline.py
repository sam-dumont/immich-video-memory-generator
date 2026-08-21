"""Timeline-aware captions (#465): the place shows on change, the date wording
follows the memory's own span."""

from __future__ import annotations

from pathlib import Path

from immich_memories.processing.assembly_config import AssemblyClip
from immich_memories.processing.clip_caption import captions_for_timeline


def _clip(date: str | None, place: str | None = None) -> AssemblyClip:
    return AssemblyClip(
        path=Path("/x.mp4"),
        duration=4.0,
        date=date,
        asset_id=f"a-{date}-{place}",
        location_name=place,
    )


class TestPlaceShowsOnChange:
    def test_a_repeated_place_is_shown_once(self):
        clips = [
            _clip("2025-08-02", "Nice, France"),
            _clip("2025-08-03", "Nice, France"),
            _clip("2025-08-04", "Nice, France"),
        ]

        captions = captions_for_timeline(clips, place=True)

        assert captions[0].place == "Nice, France"
        assert captions[1].place == ""
        assert captions[2].place == ""

    def test_a_place_change_shows_again(self):
        clips = [
            _clip("2025-08-02", "Nice, France"),
            _clip("2025-08-10", "Jette, Belgium"),
            _clip("2025-08-11", "Jette, Belgium"),
        ]

        captions = captions_for_timeline(clips, place=True)

        assert [c.place for c in captions] == ["Nice, France", "Jette, Belgium", ""]

    def test_a_clip_without_a_place_does_not_reset_the_run(self):
        """EXIF gaps are common inside one event; an unknown place between two
        captions of the same place must not make the third repeat it."""
        clips = [
            _clip("2025-08-02", "Nice, France"),
            _clip("2025-08-03", None),
            _clip("2025-08-04", "Nice, France"),
        ]

        captions = captions_for_timeline(clips, place=True)

        assert [c.place for c in captions] == ["Nice, France", "", ""]

    def test_place_off_means_no_places(self):
        captions = captions_for_timeline([_clip("2025-08-02", "Nice, France")], place=False)

        assert captions[0].place == ""


class TestDateWordingFollowsTheSpan:
    """ "10 Aug 2025" inside an August-2025 memory restates the video's own
    premise; "Sunday 10" carries the actual information (#465)."""

    def test_a_single_month_span_uses_weekday_and_day(self):
        clips = [_clip("2025-08-02"), _clip("2025-08-10"), _clip("2025-08-29")]

        captions = captions_for_timeline(clips)

        assert captions[0].date == "Saturday 2"
        assert captions[1].date == "Sunday 10"
        assert captions[2].date == "Friday 29"

    def test_a_multi_month_span_within_a_year_adds_the_month(self):
        clips = [_clip("2025-06-21"), _clip("2025-08-10")]

        captions = captions_for_timeline(clips)

        assert captions[0].date == "21 Jun"
        assert captions[1].date == "10 Aug"

    def test_a_multi_year_span_keeps_the_full_date(self):
        clips = [_clip("2024-12-31"), _clip("2025-01-01")]

        captions = captions_for_timeline(clips)

        assert captions[0].date == "31 Dec 2024"
        assert captions[1].date == "1 Jan 2025"

    def test_a_clip_without_a_date_gets_no_date_caption(self):
        captions = captions_for_timeline([_clip(None), _clip("2025-08-10")])

        assert captions[0].date == ""
        assert captions[1].date == "Sunday 10"


class TestCaptionFilters:
    """#464: title typography, a readable size, place left / date right, and a
    scrim so white text survives a bright subject."""

    def _filters(self, caption=None, w=1920, h=1080, **kw):
        from immich_memories.processing.clip_caption import ClipCaption, caption_filters

        if caption is None:
            caption = ClipCaption(place="Nice, France", date="Sunday 10")
        return caption_filters(caption, w, h, **kw)

    def test_place_draws_left_and_date_draws_right(self):
        place_f, date_f = self._filters()

        assert "Nice, France" in place_f and place_f.endswith(":x=43")
        assert "Sunday 10" in date_f and "x=w-tw-43" in date_f

    def test_an_empty_side_is_not_drawn(self):
        from immich_memories.processing.clip_caption import ClipCaption

        only_date = self._filters(ClipCaption(date="Sunday 10"))

        assert len(only_date) == 1
        assert "Sunday 10" in only_date[0]

    def test_nothing_to_say_draws_nothing(self):
        from immich_memories.processing.clip_caption import ClipCaption

        assert self._filters(ClipCaption()) == []

    def test_the_title_font_is_used_when_available(self):
        filters = self._filters(font_path="/fonts/outfit.ttf")

        assert all("fontfile='/fonts/outfit.ttf'" in f for f in filters)

    def test_a_scrim_keeps_text_legible_over_bright_content(self):
        for f in self._filters():
            assert "box=1" in f and "boxcolor=black@" in f

    def test_the_caption_is_readable_at_1080p(self):
        """0.028 of the short side read as fine print; captions are content."""
        import re

        (date_f,) = self._filters(
            caption=__import__(
                "immich_memories.processing.clip_caption", fromlist=["ClipCaption"]
            ).ClipCaption(date="Sunday 10")
        )

        assert int(re.search(r"fontsize=(\d+)", date_f).group(1)) >= 40

    def test_portrait_keeps_both_sides_above_the_action_rail(self):
        place_f, date_f = self._filters(w=1080, h=1920)

        for f in (place_f, date_f):
            assert "y=h-th-307" in f

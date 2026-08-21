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

        assert captions[0].date == "21 June"
        assert captions[1].date == "10 August"

    def test_a_multi_year_span_keeps_the_full_date(self):
        clips = [_clip("2024-12-31"), _clip("2025-01-01")]

        captions = captions_for_timeline(clips)

        assert captions[0].date == "31 December 2024"
        assert captions[1].date == "1 January 2025"

    def test_a_clip_without_a_date_gets_no_date_caption(self):
        captions = captions_for_timeline([_clip(None), _clip("2025-08-10")])

        assert captions[0].date == ""
        assert captions[1].date == "Sunday 10"

    def test_the_wording_follows_the_locale(self):
        """The server's locale, not the developer's: a French library says
        Dimanche, and the multi-month form uses the French month name."""
        captions = captions_for_timeline([_clip("2025-08-10")], locale_code="fr")

        assert captions[0].date == "Dimanche 10"

        juin, aout = captions_for_timeline(
            [_clip("2025-06-21"), _clip("2025-08-10")], locale_code="fr"
        )
        assert juin.date == "21 Juin"
        assert aout.date == "10 Août"


class TestCaptionFilters:
    """#464 v4 (proof-sheet approved): title-font bold uppercase, place TOP-left,
    date BOTTOM-right, corners hugged identically in both orientations, heavy
    outline for bright content, constant y so nothing drifts with descenders."""

    def _filters(self, caption=None, w=1920, h=1080, **kw):
        from immich_memories.processing.clip_caption import ClipCaption, caption_filters

        if caption is None:
            caption = ClipCaption(place="Jette, Belgium", date="Thursday 14")
        return caption_filters(caption, w, h, **kw)

    def test_place_top_left_and_date_bottom_right(self):
        place_f, date_f = self._filters()

        # 1080 short side: inset = round(1080*0.055) = 59, line = round(67*1.05) = 70
        assert place_f.endswith(":x=59:y=59")
        assert date_f.endswith(":x=w-tw-59:y=h-59-70")

    def test_the_text_is_uppercase(self):
        place_f, date_f = self._filters()

        assert "JETTE, BELGIUM" in place_f
        assert "THURSDAY 14" in date_f

    def test_portrait_uses_the_same_corner_insets_as_landscape(self):
        """Review decision: portrait mirrors landscape — close to each corner."""
        land = self._filters(w=1920, h=1080)
        port = self._filters(w=1080, h=1920)

        assert land[0].endswith(":x=59:y=59") and port[0].endswith(":x=59:y=59")
        assert land[1].endswith(":x=w-tw-59:y=h-59-70")
        assert port[1].endswith(":x=w-tw-59:y=h-59-70")

    def test_an_empty_side_is_not_drawn(self):
        from immich_memories.processing.clip_caption import ClipCaption

        only_date = self._filters(ClipCaption(date="Sunday 10"))

        assert len(only_date) == 1
        assert "SUNDAY 10" in only_date[0]

    def test_nothing_to_say_draws_nothing(self):
        from immich_memories.processing.clip_caption import ClipCaption

        assert self._filters(ClipCaption()) == []

    def test_the_title_font_is_used_when_available(self):
        filters = self._filters(font_path="/fonts/outfit.ttf")

        assert all("fontfile='/fonts/outfit.ttf'" in f for f in filters)

    def test_a_dark_outline_keeps_text_legible_over_bright_content(self):
        """Measured on the proof sheet: a shadow alone vanishes on white."""
        for f in self._filters():
            assert "borderw=" in f and "bordercolor=black@" in f

    def test_the_caption_reads_at_title_scale(self):
        import re

        (date_f,) = self._filters(
            caption=__import__(
                "immich_memories.processing.clip_caption", fromlist=["ClipCaption"]
            ).ClipCaption(date="Sunday 10")
        )

        assert int(re.search(r"fontsize=(\d+)", date_f).group(1)) >= 60

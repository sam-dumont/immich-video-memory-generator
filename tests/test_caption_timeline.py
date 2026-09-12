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
    def test_missing_gps_keeps_a_named_place_and_unknown_home_keeps_country(self):
        from immich_memories.analysis.familiar_places import PlaceHistory
        from immich_memories.generate_captions import apply_location_captions

        clips = [_clip("2025-08-01", "Brussels, Belgium")]
        prepared = apply_location_captions(clips, PlaceHistory([]))

        assert captions_for_timeline(prepared, place=True)[0].place == "Brussels, Belgium"

    def test_hidden_home_resets_place_but_missing_metadata_does_not(self):
        from datetime import date

        from immich_memories.analysis.familiar_places import PlaceHistory, PlaceObservation
        from immich_memories.generate_captions import apply_location_captions

        # The test home is the Royal Palace in Brussels, a public landmark.
        home = (50.843, 4.362)
        history = PlaceHistory([PlaceObservation(*home, date(2024, 1, 1), "Belgium")])
        clips = [
            _clip("2025-08-01", "De Haan, Belgium"),
            _clip("2025-08-02", "Brussels, Belgium"),
            _clip("2025-08-03", "De Haan, Belgium"),
            _clip("2025-08-03", None),
            _clip("2025-08-03", "De Haan, Belgium"),
            _clip("2025-08-04", "Nice, France"),
        ]
        clips[1].latitude, clips[1].longitude = home
        prepared = apply_location_captions(clips, history, home=home)

        assert clips[1].location_name == "Brussels, Belgium", "maps keep the original name"
        assert [c.place for c in captions_for_timeline(prepared, place=True)] == [
            "De Haan",
            "",
            "De Haan",
            "",
            "",
            "Nice, France",
        ]

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

    def test_repeated_dates_stay_silent_across_metadata_gaps(self):
        clips = [_clip("2025-08-10"), _clip(None), _clip("2025-08-10"), _clip("2025-08-11")]

        assert [c.date for c in captions_for_timeline(clips)] == ["Sunday 10", "", "", "Monday 11"]

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
    """Readable translucent context, with consistent corners in both orientations."""

    def _filters(self, caption=None, w=1920, h=1080, **kw):
        from immich_memories.processing.clip_caption import ClipCaption, caption_filters

        if caption is None:
            caption = ClipCaption(place="Jette, Belgium", date="Thursday 14")
        return caption_filters(caption, w, h, **kw)

    def test_place_top_left_and_date_bottom_right(self):
        place_f, date_f = self._filters()

        # 1080 short side: inset = round(1080*0.055) = 59, line = round(48*1.05) = 50
        assert place_f.endswith(":x=59:y=59")
        assert date_f.endswith(":x=w-tw-59:y=h-59-50")

    def test_the_text_is_uppercase(self):
        place_f, date_f = self._filters()

        assert "JETTE, BELGIUM" in place_f
        assert "THURSDAY 14" in date_f

    def test_portrait_uses_the_same_corner_insets_as_landscape(self):
        """Review decision: portrait mirrors landscape — close to each corner."""
        land = self._filters(w=1920, h=1080)
        port = self._filters(w=1080, h=1920)

        assert land[0].endswith(":x=59:y=59") and port[0].endswith(":x=59:y=59")
        assert land[1].endswith(":x=w-tw-59:y=h-59-50")
        assert port[1].endswith(":x=w-tw-59:y=h-59-50")

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

    def test_the_caption_is_smaller_than_a_title(self):
        import re

        (date_f,) = self._filters(
            caption=__import__(
                "immich_memories.processing.clip_caption", fromlist=["ClipCaption"]
            ).ClipCaption(date="Sunday 10")
        )

        assert int(re.search(r"fontsize=(\d+)", date_f).group(1)) == 48

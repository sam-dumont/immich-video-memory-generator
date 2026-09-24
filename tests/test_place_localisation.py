"""A French film says Chypre, not Cyprus."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from immich_memories.i18n_places import localise_country, localise_place, place_label


class TestCountryNames:
    """CLDR territory names, offline, with everything unknown left alone."""

    @pytest.mark.parametrize(
        ("english", "locale", "expected"),
        [
            ("Cyprus", "fr", "Chypre"),
            ("Belgium", "nl", "België"),
            ("Italy", "fr", "Italie"),
            ("Cyprus", "en", "Cyprus"),
            ("Middle Earth", "fr", "Middle Earth"),
            ("Cyprus", "not-a-locale", "Cyprus"),
        ],
    )
    def test_names(self, english: str, locale: str, expected: str) -> None:
        assert localise_country(english, locale) == expected

    def test_only_the_country_part_of_a_label_moves(self) -> None:
        assert localise_place("Nicosia, Cyprus", "fr") == "Nicosia, Chypre"

    def test_a_bare_country_still_moves(self) -> None:
        assert localise_place("Cyprus", "fr") == "Chypre"

    def test_nothing_stays_nothing(self) -> None:
        assert localise_place(None, "fr") is None

    def test_a_label_is_city_then_localised_country(self) -> None:
        assert place_label("Nicosia", "Cyprus", "fr") == "Nicosia, Chypre"
        assert place_label(None, "Cyprus", "fr") == "Chypre"
        assert place_label("Nicosia", None, "fr") == "Nicosia"
        assert place_label(None, None, "fr") is None


class TestTheFilmReadsTheseNames:
    """The four places a viewer meets a country name."""

    def test_the_trip_title_localises_the_country(self) -> None:
        from immich_memories.titles._trip_titles import generate_trip_title

        title = generate_trip_title("Cyprus", date(2024, 7, 1), date(2024, 7, 10), locale="fr")

        assert "À CHYPRE" in title

    @pytest.mark.parametrize(
        ("place", "expected"),
        [
            # A ratchet render read "DEUX SEMAINES À ITALIE" (#1101).
            ("Italy", "DEUX SEMAINES EN ITALIE"),
            ("Iran", "DEUX SEMAINES EN IRAN"),
            ("Portugal", "DEUX SEMAINES AU PORTUGAL"),
            ("Mexico", "DEUX SEMAINES AU MEXIQUE"),
            ("United States", "DEUX SEMAINES AUX ÉTATS-UNIS"),
            ("Netherlands", "DEUX SEMAINES AUX PAYS-BAS"),
            ("Cyprus", "DEUX SEMAINES À CHYPRE"),
            ("Lisbon, Portugal", "DEUX SEMAINES À LISBON, PORTUGAL"),
        ],
    )
    def test_a_french_trip_title_takes_the_country_s_own_preposition(
        self, place: str, expected: str
    ) -> None:
        from immich_memories.titles._trip_titles import generate_trip_title

        title = generate_trip_title(place, date(2024, 7, 1), date(2024, 7, 14), locale="fr")

        assert title == f"{expected}, JUILLET 2024"

    def test_an_english_trip_title_keeps_in(self) -> None:
        from immich_memories.titles._trip_titles import generate_trip_title

        title = generate_trip_title("Italy", date(2024, 7, 1), date(2024, 7, 14), locale="en")

        assert title == "TWO WEEKS IN ITALY, JULY 2024"

    def test_a_clip_overlay_localises_the_country_and_still_drops_home(self) -> None:
        from immich_memories.analysis.familiar_places import PlaceHistory, PlaceObservation
        from immich_memories.generate_captions import apply_location_captions
        from immich_memories.processing.assembly_config import AssemblyClip

        # A public landmark abroad, and a synthetic home a long way from it.
        away = AssemblyClip(
            path=Path("/x/a.mp4"),
            duration=3.0,
            latitude=35.17,
            longitude=33.36,
            location_name="Nicosia, Cyprus",
        )
        at_home = AssemblyClip(
            path=Path("/x/b.mp4"),
            duration=3.0,
            latitude=48.86,
            longitude=2.35,
            location_name="Lyon, France",
        )
        history = PlaceHistory([PlaceObservation(48.86, 2.35, date(2024, 1, 1), "France")])

        captioned = apply_location_captions(
            [away, at_home], history, home=(48.86, 2.35), locale="fr"
        )

        assert captioned[0].caption_location_name == "Nicosia, Chypre"
        assert captioned[1].caption_location_name == ""

    def test_map_pins_carry_localised_names(self) -> None:
        from immich_memories.generate_privacy import extract_trip_pins
        from immich_memories.processing.assembly_config import AssemblyClip

        clips = [
            AssemblyClip(
                path=Path("/x/a.mp4"),
                duration=3.0,
                latitude=35.17,
                longitude=33.36,
                location_name="Nicosia, Cyprus",
            )
        ]

        _locations, names = extract_trip_pins(clips, locale="fr")

        assert names == ["Nicosia, Chypre"]

    def test_a_location_card_is_titled_in_the_film_s_language(self) -> None:
        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_divider_planner import TitleDividerPlanner

        asked: list[str] = []

        class _Generator:
            def generate_location_card_screen(self, name, lat=None, lon=None):
                asked.append(name)
                return type("Screen", (), {"path": Path("card.mp4")})()

        planner = TitleDividerPlanner(
            _Generator(),  # type: ignore[arg-type]
            TitleScreenSettings(locale="fr"),
        )
        planner.make_location_card_clip("Nicosia, Cyprus", {})

        assert asked == ["Nicosia, Chypre"]


class TestGeocodedPlaceNames:
    """One question per distinct place on the cut, kept on disk, never fatal."""

    def test_one_reading_serves_every_clip_at_the_same_place(self, tmp_path: Path) -> None:
        from immich_memories.analysis.place_name_cache import PlaceNameCache

        asked: list[tuple[float, float]] = []

        def read(latitude: float, longitude: float) -> str:
            asked.append((latitude, longitude))
            return "Nicosie, Chypre"

        cache = PlaceNameCache(tmp_path, "fr", read)
        # Two points about 300 m apart: one place once rounded.
        first = cache.name_for(35.1712, 33.3634, "Nicosia, Cyprus")
        second = cache.name_for(35.1729, 33.3648, "Nicosia, Cyprus")

        assert (first, second) == ("Nicosie, Chypre", "Nicosie, Chypre")
        assert asked == [(35.17, 33.36)]

    def test_the_next_run_reads_the_answer_off_disk(self, tmp_path: Path) -> None:
        from immich_memories.analysis.place_name_cache import PlaceNameCache

        warm = PlaceNameCache(tmp_path, "fr", lambda *_a: "Nicosie, Chypre")
        warm.name_for(35.17, 33.36, None)
        warm.flush()

        cold = PlaceNameCache(tmp_path, "fr", None)

        assert cold.name_for(35.17, 33.36, "Nicosia, Cyprus") == "Nicosie, Chypre"

    def test_another_language_is_a_different_question(self, tmp_path: Path) -> None:
        from immich_memories.analysis.place_name_cache import PlaceNameCache

        french = PlaceNameCache(tmp_path, "fr", lambda *_a: "Nicosie, Chypre")
        french.name_for(35.17, 33.36, None)
        french.flush()

        english = PlaceNameCache(tmp_path, "en", None)

        assert english.name_for(35.17, 33.36, "Nicosia, Cyprus") == "Nicosia, Cyprus"

    def test_a_geocoder_that_fails_leaves_the_stored_name(self, tmp_path: Path) -> None:
        from immich_memories.analysis.place_name_cache import PlaceNameCache

        def die(*_args: float) -> str:
            raise RuntimeError("service unavailable")

        cache = PlaceNameCache(tmp_path, "fr", die)

        assert cache.name_for(35.17, 33.36, "Nicosia, Cyprus") == "Nicosia, Cyprus"
        # And it stops asking: one outage must not cost one call per place.
        assert cache.name_for(48.86, 2.35, "Paris, France") == "Paris, France"


class TestTheNominatimReader:
    """The reader built when the switch is on: what it asks, what it returns."""

    def _reader(self, monkeypatch: pytest.MonkeyPatch, raw: dict):
        asked: dict = {}

        class _Answer:
            def __init__(self) -> None:
                self.raw = raw

        class _Geolocator:
            def reverse(self, query, **kwargs):
                asked["query"] = query
                asked.update(kwargs)
                return _Answer() if raw else None

        # WHY: Nominatim is the outside host, and RateLimiter would hold the test
        # for a second per call. Both are replaced; the address parsing is ours.
        monkeypatch.setattr("geopy.geocoders.Nominatim", lambda **_k: _Geolocator())
        monkeypatch.setattr("geopy.extra.rate_limiter.RateLimiter", lambda call, **_k: call)
        from immich_memories.analysis.place_name_cache import nominatim_place_reader

        return nominatim_place_reader("fr"), asked

    def test_it_asks_in_the_films_language_at_town_zoom(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        read, asked = self._reader(
            monkeypatch, {"address": {"town": "Nicosie", "country": "Chypre"}}
        )

        assert read(35.17, 33.36) == "Nicosie, Chypre"
        assert asked["language"] == "fr"
        assert asked["zoom"] == 14

    def test_a_place_with_only_a_country_still_answers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        read, _asked = self._reader(monkeypatch, {"address": {"country": "Chypre"}})

        assert read(35.17, 33.36) == "Chypre"

    def test_a_coordinate_nobody_can_name_answers_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        read, _asked = self._reader(monkeypatch, {})

        assert read(0.0, 0.0) is None


class TestThePlaceNameStore:
    """What the store does with a file it cannot use, and one it cannot write."""

    def test_an_answerless_reading_leaves_the_stored_name(self, tmp_path: Path) -> None:
        from immich_memories.analysis.place_name_cache import PlaceNameCache

        cache = PlaceNameCache(tmp_path, "fr", lambda *_a: None)

        assert cache.name_for(35.17, 33.36, "Nicosia, Cyprus") == "Nicosia, Cyprus"
        cache.flush()  # nothing was learned, so nothing is written
        assert not (tmp_path / "place-names").exists()

    def test_a_store_from_another_schema_is_ignored(self, tmp_path: Path) -> None:
        from immich_memories.analysis.place_name_cache import PlaceNameCache

        warm = PlaceNameCache(tmp_path, "fr", lambda *_a: "Nicosie, Chypre")
        warm.name_for(35.17, 33.36, None)
        warm.flush()
        stored = next((tmp_path / "place-names").glob("*.json"))
        stored.write_text('{"schema_version": 99, "names": {"35.17,33.36": "Nowhere"}}')

        cold = PlaceNameCache(tmp_path, "fr", None)

        assert cold.name_for(35.17, 33.36, "Nicosia, Cyprus") == "Nicosia, Cyprus"

    def test_a_store_it_cannot_write_is_not_a_render_failure(self, tmp_path: Path) -> None:
        from immich_memories.analysis.place_name_cache import PlaceNameCache

        blocked = tmp_path / "blocked"
        blocked.write_text("this is a file, not a directory")
        cache = PlaceNameCache(blocked, "fr", lambda *_a: "Nicosie, Chypre")
        cache.name_for(35.17, 33.36, None)

        cache.flush()  # must not raise


class TestGeocodingReachesTheCut:
    """`prepare_location_captions` is where the switch turns into better names."""

    def _params(self, tmp_path: Path, geocoding: bool):
        from immich_memories.config import Config
        from immich_memories.generate import GenerationParams

        config = Config()
        config.network.geocoding = geocoding
        config.cache.directory = str(tmp_path)
        config.title_screens.locale = "fr"
        config.trips.homebase_latitude = 48.86
        config.trips.homebase_longitude = 2.35
        return GenerationParams(
            clips=[],
            output_path=tmp_path / "out.mp4",
            config=config,
            add_place_overlay=True,
        )

    def _clip(self):
        from immich_memories.processing.assembly_config import AssemblyClip

        return AssemblyClip(
            path=Path("/x/a.mp4"),
            duration=3.0,
            latitude=35.17,
            longitude=33.36,
            location_name="Nicosia, Cyprus",
        )

    def test_off_means_the_stored_name_translated_and_nothing_asked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # WHY: Nominatim is the outside host; a reader that raises proves the
        # default path never builds one.
        monkeypatch.setattr(
            "immich_memories.analysis.place_name_cache.nominatim_place_reader",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not geocode")),
        )
        from immich_memories.generate_captions import prepare_location_captions

        captioned = prepare_location_captions(self._params(tmp_path, False), [self._clip()])

        assert captioned[0].caption_location_name == "Nicosia, Chypre"

    def test_on_means_the_geocoder_names_the_place(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # WHY: same host, answering this time, so the cut carries its town name.
        monkeypatch.setattr(
            "immich_memories.analysis.place_name_cache.nominatim_place_reader",
            lambda *_a, **_k: lambda *_p: "Nicosie, Chypre",
        )
        from immich_memories.generate_captions import prepare_location_captions

        captioned = prepare_location_captions(self._params(tmp_path, True), [self._clip()])

        assert captioned[0].caption_location_name == "Nicosie, Chypre"

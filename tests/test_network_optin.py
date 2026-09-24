"""Nothing outside the user's own servers is contacted unless the config says so."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from immich_memories.api.models import Asset, AssetType, ExifInfo
from immich_memories.config import Config


def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str = "") -> Config:
    # WHY: the loader resolves paths and defaults against the user's home, so a
    # real ~/.immich-memories would decide what this test sees.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    path = tmp_path / "config.yaml"
    path.write_text(body)
    return Config.from_yaml(path)


def _asset(day: int, lat: float, lon: float, city: str, country: str) -> Asset:
    stamp = datetime(2024, 6, day, 12, 0, tzinfo=UTC)
    return Asset(
        id=f"asset-{day}",
        type=AssetType.IMAGE,
        fileCreatedAt=stamp,
        fileModifiedAt=stamp,
        updatedAt=stamp,
        exifInfo=ExifInfo(latitude=lat, longitude=lon, city=city, country=country),
    )


# A public landmark, well away from anybody's home: the Colosseum.
_ROME = [_asset(day, 41.89, 12.49, "Rome", "Italy") for day in (10, 11, 12, 13)]
_PARIS = (48.86, 2.35)


def _refuse(*_args: object, **_kwargs: object) -> str | None:
    raise AssertionError("this run must not have contacted an outside host")


def _offline(*_args: object, **_kwargs: object) -> str | None:
    raise ValueError("no route to host")


class TestNetworkSwitches:
    """The three opt-in switches and their defaults."""

    def test_every_switch_is_off_in_a_fresh_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        network = _config(tmp_path, monkeypatch).network
        assert not network.geocoding
        assert not network.map_tiles

    def test_the_section_is_tier_one(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = _config(tmp_path, monkeypatch, "network:\n  geocoding: true\n")
        assert config.network.geocoding is True


class TestTripNamingStaysOffline:
    """A default run names its trips from EXIF and asks nobody."""

    def test_no_geocoder_means_the_name_comes_from_exif(self) -> None:
        from immich_memories.analysis.trip_detection import detect_trips

        trips = detect_trips(_ROME, *_PARIS)

        assert [trip.location_name for trip in trips] == ["Rome, Italy"]

    def test_an_injected_geocoder_names_the_trip(self) -> None:
        from immich_memories.analysis.trip_detection import detect_trips

        trips = detect_trips(_ROME, *_PARIS, geocoder=lambda *_a, **_k: "Lazio, Italy")

        assert [trip.location_name for trip in trips] == ["Lazio, Italy"]

    def test_the_switch_decides_whether_a_geocoder_exists(self) -> None:
        from immich_memories.analysis.trip_detection import geocoder_for

        assert geocoder_for(enabled=False) is None
        assert geocoder_for(enabled=True) is not None

    def test_year_discovery_passes_nothing_through_when_it_is_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # WHY: Nominatim is the outside host under test; this stands in for it so
        # a regression that reaches the network fails loudly instead of quietly.
        monkeypatch.setattr("immich_memories.analysis.trip_detection.reverse_geocode", _refuse)
        from immich_memories.analysis.trip_discovery import discover_year_trips
        from immich_memories.config_models_automation import TripsConfig

        class _Client:
            def get_assets_for_date_range(self, _range: object) -> list[Asset]:
                return _ROME

        config = TripsConfig(homebase_latitude=_PARIS[0], homebase_longitude=_PARIS[1])
        trips = discover_year_trips(_Client(), config, 2024)  # type: ignore[arg-type]

        assert [trip.location_name for trip in trips] == ["Rome, Italy"]


class TestPreflightNamesTheHosts:
    """Preflight says which outside hosts a run may reach, and nothing when none."""

    def test_silent_when_every_switch_is_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from immich_memories.preflight_network import outside_call_checks

        assert outside_call_checks(_config(tmp_path, monkeypatch)) == []

    def test_one_row_per_enabled_switch_naming_its_host(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from immich_memories.preflight_network import outside_call_checks

        config = _config(
            tmp_path,
            monkeypatch,
            "network:\n  geocoding: true\n  map_tiles: true\n",
        )
        messages = [check.message for check in outside_call_checks(config)]

        assert len(messages) == 2
        # Exact rows, not substring probes: a bare host substring cannot stand in
        # for a URL check (CodeQL py/incomplete-url-substring-sanitization).
        assert (
            "nominatim.openstreetmap.org will be contacted for trip names and "
            "place names in the film's language"
        ) in messages
        assert (
            "server.arcgisonline.com will be contacted for the trip fly-over, "
            "the static map and location cards"
        ) in messages


class TestTitleFontsComeFromTheWheel:
    """A fresh install renders titles with the bundled family and no CDN."""

    def test_the_kernel_renderer_finds_the_bundled_montserrat(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # WHY: httpx is the only way out of this process; a client that raises
        # proves the bundled file is found without any host.
        monkeypatch.setattr("httpx.Client", _refuse)
        monkeypatch.setenv("HOME", str(tmp_path))
        from immich_memories.titles.kernels import _get_system_font

        path = Path(_get_system_font("Montserrat"))

        assert path.exists()
        assert path.parent.parent.name == "bundled_fonts"

    def test_the_map_renderer_finds_the_bundled_montserrat(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # WHY: same transport, same reason: the map's labels used to download it.
        monkeypatch.setattr("httpx.Client", _refuse)
        monkeypatch.setenv("HOME", str(tmp_path))
        from immich_memories.titles.map_renderer import _get_font

        font = _get_font(24, bold=True)

        assert Path(getattr(font, "path", "")).parent.parent.name == "bundled_fonts"

    def test_a_script_the_install_lacks_renders_without_a_download(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # WHY: httpx.get is how `titles fonts --install` fetches; a render must never reach it.
        monkeypatch.setattr("httpx.get", _refuse)
        monkeypatch.setenv("IMMICH_MEMORIES_FONTS_DIR", str(tmp_path / "empty"))
        from PIL import Image, ImageDraw

        from immich_memories.titles.font_chain import title_font
        from immich_memories.titles.fonts import bundled_font_path

        font = title_font(bundled_font_path("Montserrat", "Bold"), 40, bold=True)
        ImageDraw.Draw(Image.new("L", (400, 80))).text((0, 0), "Crète · ירושלים", font=font)

    def test_bundled_weights_are_reported_exactly(self) -> None:
        from immich_memories.titles.fonts import bundled_font_path

        assert bundled_font_path("Montserrat", "Bold") is not None
        assert bundled_font_path("Outfit", "Bold") is None
        assert bundled_font_path("Helvetica", "Regular") is None


class TestMapTilesAreOptIn:
    """With tiles off a trip opens on its title card and location cards go plain."""

    def _title_settings(self, map_tiles: bool):
        from immich_memories.processing.assembly_config import TitleScreenSettings

        return TitleScreenSettings(
            memory_type="trip",
            map_tiles=map_tiles,
            trip_locations=[(41.89, 12.49)],
            trip_title_text="A WEEK IN ROME, SUMMER 2024",
        )

    def test_the_trip_opens_on_a_card_carrying_the_trip_title(self) -> None:
        from immich_memories.generate_settings import apply_map_tile_policy

        settings = apply_map_tile_policy(self._title_settings(map_tiles=False))

        assert settings.trip_locations is None
        assert settings.title_override == "A WEEK IN ROME, SUMMER 2024"

    def test_the_fly_over_survives_when_tiles_are_allowed(self) -> None:
        from immich_memories.generate_settings import apply_map_tile_policy

        settings = apply_map_tile_policy(self._title_settings(map_tiles=True))

        assert settings.trip_locations == [(41.89, 12.49)]
        assert settings.title_override is None

    def test_a_location_card_asks_for_no_map_when_tiles_are_off(self) -> None:
        from immich_memories.processing.title_divider_planner import TitleDividerPlanner

        asked: list[tuple[float | None, float | None]] = []

        class _Generator:
            def generate_location_card_screen(self, name, lat=None, lon=None):
                asked.append((lat, lon))
                return type("Screen", (), {"path": Path("card.mp4")})()

        planner = TitleDividerPlanner(
            _Generator(),  # type: ignore[arg-type]
            self._title_settings(map_tiles=False),
        )
        planner.make_location_card_clip("Rome", {}, lat=41.89, lon=12.49)

        assert asked == [(None, None)]

    def test_the_fly_over_degrades_instead_of_raising_when_tiles_refuse(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # WHY: server.arcgisonline.com is the outside host; this stands in for a
        # box that cannot reach it, which used to take the whole render down.
        monkeypatch.setattr(
            "immich_memories.titles.map_animation._CachedStaticMap.render", _offline
        )
        from immich_memories.titles.map_animation import _render_satellite

        frame = _render_satellite(41.89, 12.49, 9.0, 64, 48)

        assert frame.size == (64, 48)


class TestFontFallbacksWithoutTheCdn:
    """What a lookup does when the wheel does not carry the family."""

    def test_the_users_own_font_directory_is_the_second_stop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        family = tmp_path / ".immich-memories" / "fonts" / "Inter"
        family.mkdir(parents=True)
        (family / "Inter-Medium.ttf").write_bytes(b"\x00\x01\x00\x00")
        from immich_memories.titles.kernels import _get_system_font

        assert _get_system_font("Inter") == str(family / "Inter-Medium.ttf")

    def test_nothing_anywhere_still_returns_a_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr("immich_memories.titles.kernels._SYSTEM_FONTS", [])
        from immich_memories.titles.kernels import _get_system_font

        assert _get_system_font("Nonesuch") == "Arial"

    def test_the_sdf_renderer_takes_the_bundled_family_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        from immich_memories.titles.sdf_font import find_font

        found = find_font("Montserrat", "bold")

        assert found is not None
        assert found.parent.parent.name == "bundled_fonts"

    def test_the_static_map_greys_out_when_the_tiles_refuse(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # WHY: server.arcgisonline.com again, through staticmap's own client.
        monkeypatch.setattr("immich_memories.titles.map_renderer.StaticMap.render", _offline)
        from immich_memories.titles.map_renderer import _render_base_map

        image, sm = _render_base_map([(41.89, 12.49)], 64, 48)

        assert image.size == (64, 48)
        assert sm is None

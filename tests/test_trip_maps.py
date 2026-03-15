"""Tests for trip map rendering and trip title generation."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
from PIL import Image


class TestGenerateTripTitle:
    """Trip title text generation for map overview frames."""

    def test_two_weeks_single_month(self):
        """14-day trip within July → month name, not season."""
        from immich_memories.titles._trip_titles import generate_trip_title

        result = generate_trip_title(
            location_name="Île d'Oléron, France",
            start_date=date(2025, 7, 5),
            end_date=date(2025, 7, 19),
        )
        assert result == "TWO WEEKS IN ÎLE D'OLÉRON, FRANCE, JULY 2025"

    def test_two_weeks_cross_month_uses_season(self):
        """14-day trip spanning Jul-Aug → season name."""
        from immich_memories.titles._trip_titles import generate_trip_title

        result = generate_trip_title(
            location_name="Île d'Oléron, France",
            start_date=date(2025, 7, 20),
            end_date=date(2025, 8, 3),
        )
        assert result == "TWO WEEKS IN ÎLE D'OLÉRON, FRANCE, SUMMER 2025"

    def test_weekend_trip(self):
        """2-day trip in a single month → month name, not season."""
        from immich_memories.titles._trip_titles import generate_trip_title

        result = generate_trip_title(
            location_name="Barcelona, Spain",
            start_date=date(2025, 3, 8),
            end_date=date(2025, 3, 10),
        )
        assert result == "A WEEKEND IN BARCELONA, SPAIN, MARCH 2025"

    def test_ten_days_winter(self):
        """10-day trip in December → '10 DAYS IN ..., DECEMBER 2024'."""
        from immich_memories.titles._trip_titles import generate_trip_title

        result = generate_trip_title(
            location_name="Lanzarote, Spain",
            start_date=date(2024, 12, 20),
            end_date=date(2024, 12, 30),
        )
        assert result == "10 DAYS IN LANZAROTE, SPAIN, DECEMBER 2024"

    def test_one_week(self):
        """7-day trip → 'A WEEK IN ...'."""
        from immich_memories.titles._trip_titles import generate_trip_title

        result = generate_trip_title(
            location_name="London, UK",
            start_date=date(2025, 10, 1),
            end_date=date(2025, 10, 8),
        )
        assert result == "A WEEK IN LONDON, UK, OCTOBER 2025"


class TestRenderTripMapFrame:
    """Map frame rendering with staticmap + PIL."""

    def test_returns_image_with_correct_dimensions(self):
        """render_trip_map_frame returns PIL Image at requested resolution."""
        from immich_memories.titles.map_renderer import render_trip_map_frame

        locations = [(41.39, 2.17), (48.86, 2.35)]  # Barcelona, Paris

        # Mock staticmap to avoid network calls
        mock_map_img = Image.new("RGB", (1920, 1080), color=(200, 220, 240))
        with patch("immich_memories.titles.map_renderer.StaticMap") as mock_sm:
            mock_sm.return_value.render.return_value = mock_map_img
            result = render_trip_map_frame(
                locations=locations,
                title_text="TWO WEEKS IN SPAIN",
                width=1920,
                height=1080,
            )

        assert isinstance(result, Image.Image)
        assert result.size == (1920, 1080)

    def test_returns_numpy_array_for_gpu(self):
        """render_trip_map_array returns numpy float32 array for Taichi pipeline."""
        from immich_memories.titles.map_renderer import render_trip_map_array

        locations = [(41.39, 2.17)]
        mock_map_img = Image.new("RGB", (1920, 1080), color=(200, 220, 240))

        with patch("immich_memories.titles.map_renderer.StaticMap") as mock_sm:
            mock_sm.return_value.render.return_value = mock_map_img
            result = render_trip_map_array(
                locations=locations,
                width=1920,
                height=1080,
            )

        assert isinstance(result, np.ndarray)
        assert result.shape == (1080, 1920, 3)
        assert result.dtype == np.float32
        # Values should be normalized 0-1
        assert result.max() <= 1.0

    def test_single_location_renders(self):
        """Single location should still produce a valid map."""
        from immich_memories.titles.map_renderer import render_trip_map_frame

        mock_map_img = Image.new("RGB", (1920, 1080), color=(200, 220, 240))
        with patch("immich_memories.titles.map_renderer.StaticMap") as mock_sm:
            mock_sm.return_value.render.return_value = mock_map_img
            result = render_trip_map_frame(
                locations=[(41.39, 2.17)],
                title_text="A WEEK IN BARCELONA",
                width=1920,
                height=1080,
            )

        assert result.size == (1920, 1080)

    def test_portrait_mode(self):
        """Portrait resolution (1080x1920) should render correctly."""
        from immich_memories.titles.map_renderer import render_trip_map_frame

        mock_map_img = Image.new("RGB", (1080, 1920), color=(200, 220, 240))
        with patch("immich_memories.titles.map_renderer.StaticMap") as mock_sm:
            mock_sm.return_value.render.return_value = mock_map_img
            result = render_trip_map_frame(
                locations=[(41.39, 2.17), (48.86, 2.35)],
                title_text="A WEEK IN SPAIN",
                width=1080,
                height=1920,
            )

        assert result.size == (1080, 1920)

    def test_portrait_numpy_array(self):
        """Portrait mode numpy array should have correct shape."""
        from immich_memories.titles.map_renderer import render_trip_map_array

        mock_map_img = Image.new("RGB", (1080, 1920), color=(200, 220, 240))
        with patch("immich_memories.titles.map_renderer.StaticMap") as mock_sm:
            mock_sm.return_value.render.return_value = mock_map_img
            result = render_trip_map_array(
                locations=[(41.39, 2.17)],
                width=1080,
                height=1920,
            )

        assert result.shape == (1920, 1080, 3)

    def test_location_card_portrait(self):
        """Location interstitial card renders in portrait."""
        from immich_memories.titles.map_renderer import render_location_card

        result = render_location_card("Barcelona", width=1080, height=1920)
        assert result.size == (1080, 1920)

    def test_location_card_landscape(self):
        """Location interstitial card renders in landscape."""
        from immich_memories.titles.map_renderer import render_location_card

        result = render_location_card("Île d'Oléron", width=1920, height=1080)
        assert result.size == (1920, 1080)


class TestFontAutoDownload:
    """Font auto-download ensures Montserrat is available."""

    def test_get_font_downloads_if_missing(self):
        """_get_font should auto-download Montserrat if not cached."""
        from unittest.mock import patch as mock_patch

        from immich_memories.titles.map_renderer import _get_font

        # Mock the font cache to not exist, and mock download
        with (
            mock_patch("immich_memories.titles.map_renderer._ensure_montserrat") as mock_ensure,
        ):
            mock_ensure.return_value = True
            font = _get_font(48, bold=True)
            mock_ensure.assert_called_once()
            assert font is not None


class TestMultilineTextWrapping:
    """Text wrapping for map video titles."""

    def test_comma_split_preferred(self):
        """Titles with commas should split at the comma first."""
        from immich_memories.titles.taichi_text import split_title_lines

        lines = split_title_lines("TWO WEEKS IN SPAIN, SUMMER 2025", max_chars=25)
        assert lines == ["TWO WEEKS IN SPAIN,", "SUMMER 2025"]

    def test_word_wrap_fallback(self):
        """Titles without commas word-wrap normally."""
        from immich_memories.titles.taichi_text import split_title_lines

        lines = split_title_lines("TWO WEEKS IN SPAIN", max_chars=12)
        assert lines == ["TWO WEEKS IN", "SPAIN"]

    def test_short_title_single_line(self):
        """Short titles stay on one line."""
        from immich_memories.titles.taichi_text import split_title_lines

        lines = split_title_lines("A WEEK IN PARIS", max_chars=30)
        assert lines == ["A WEEK IN PARIS"]


class TestLocationCardWithMap:
    """Location cards render with satellite map background."""

    def test_location_card_with_coordinates_uses_map(self):
        """When lat/lon provided, location card uses map background (not plain dark)."""
        from immich_memories.titles.map_renderer import render_location_card

        mock_map_img = Image.new("RGB", (1920, 1080), color=(200, 220, 240))
        with patch("immich_memories.titles.map_renderer.StaticMap") as mock_sm:
            mock_sm.return_value.render.return_value = mock_map_img
            result = render_location_card("Barcelona", width=1920, height=1080, lat=41.39, lon=2.17)

        assert result.size == (1920, 1080)
        # Should NOT be plain dark (30, 30, 35) — map background is lighter
        center_pixel = result.getpixel((960, 540))
        assert center_pixel != (30, 30, 35)

    def test_location_card_without_coordinates_falls_back(self):
        """Without lat/lon, location card uses dark background."""
        from immich_memories.titles.map_renderer import render_location_card

        result = render_location_card("Unknown Place", width=1920, height=1080)
        assert result.size == (1920, 1080)


class TestEquirectangularMap:
    """Equirectangular map fetching for globe projection texture."""

    def test_renders_wide_area_map_array(self):
        """render_equirectangular_map returns float32 array for globe texture."""
        from immich_memories.titles.map_renderer import render_equirectangular_map

        mock_map_img = Image.new("RGB", (720, 360), color=(100, 150, 200))
        with patch("immich_memories.titles.map_renderer.StaticMap") as mock_sm:
            mock_sm.return_value.render.return_value = mock_map_img
            result = render_equirectangular_map(
                center_lat=45.0,
                center_lon=2.0,
                width=720,
                height=360,
            )

        assert isinstance(result, np.ndarray)
        assert result.shape == (360, 720, 3)
        assert result.dtype == np.float32
        assert 0.0 <= result.max() <= 1.0

    def test_fallback_on_tile_error(self):
        """Returns solid dark array if tile fetch fails."""
        from immich_memories.titles.map_renderer import render_equirectangular_map

        with patch("immich_memories.titles.map_renderer.StaticMap") as mock_sm:
            mock_sm.return_value.render.side_effect = Exception("Network error")
            result = render_equirectangular_map(
                center_lat=45.0,
                center_lon=2.0,
                width=720,
                height=360,
            )

        assert result.shape == (360, 720, 3)
        assert result.max() < 0.3  # Dark fallback


class TestMapTitleRatioConsistency:
    """Font size should be consistent across orientations."""

    def test_portrait_and_landscape_target_same_font_size(self):
        """The map_title_ratio formula produces same absolute px in both orientations."""
        # Landscape 1920x1080
        landscape_ratio = 0.135 * min(1920, 1080) / 1080
        landscape_px = int(1080 * landscape_ratio)

        # Portrait 1080x1920
        portrait_ratio = 0.135 * min(1080, 1920) / 1920
        portrait_px = int(1920 * portrait_ratio)

        assert landscape_px == portrait_px


class TestLocationDividerInsertion:
    """Location-based clip dividers for trip memories."""

    def test_inserts_divider_on_location_change(self):
        """Clips with locations >30km apart get a divider between them."""
        from pathlib import Path

        from immich_memories.processing.assembler_trip_mixin import AssemblerTripMixin

        clips = [
            self._make_clip("clip1.mp4", lat=41.39, lon=2.17, name="Barcelona"),
            self._make_clip("clip2.mp4", lat=41.39, lon=2.17, name="Barcelona"),
            # Paris is >800km from Barcelona → should trigger divider
            self._make_clip("clip3.mp4", lat=48.86, lon=2.35, name="Paris"),
        ]

        mock_gen = MagicMock()
        mock_gen.generate_location_card_screen.return_value = MagicMock(
            path=Path("/tmp/location_Paris.mp4")
        )
        mock_settings = MagicMock()
        mock_settings.month_divider_duration = 2.0

        result = AssemblerTripMixin()._build_clips_with_location_dividers(
            clips, mock_gen, mock_settings, None
        )

        # 3 content clips + 1 location divider = 4
        assert len(result) == 4
        assert result[2].is_title_screen  # divider before Paris
        assert result[2].asset_id == "location_Paris"

    def test_no_divider_for_same_location(self):
        """Clips at the same location should not get dividers."""
        from immich_memories.processing.assembler_trip_mixin import AssemblerTripMixin

        clips = [
            self._make_clip("clip1.mp4", lat=41.39, lon=2.17, name="Barcelona"),
            self._make_clip("clip2.mp4", lat=41.40, lon=2.18, name="Barcelona"),
        ]

        result = AssemblerTripMixin()._build_clips_with_location_dividers(
            clips, MagicMock(), MagicMock(), None
        )

        assert len(result) == 2  # No dividers

    def test_clips_without_gps_pass_through(self):
        """Clips without GPS should pass through without dividers."""
        from pathlib import Path

        from immich_memories.processing.assembler_trip_mixin import AssemblerTripMixin

        clips = [
            self._make_clip("clip1.mp4", lat=41.39, lon=2.17, name="Barcelona"),
            self._make_clip("clip2.mp4"),  # No GPS
            self._make_clip("clip3.mp4", lat=48.86, lon=2.35, name="Paris"),
        ]

        mock_gen = MagicMock()
        mock_gen.generate_location_card_screen.return_value = MagicMock(
            path=Path("/tmp/location_Paris.mp4")
        )
        mock_settings = MagicMock()
        mock_settings.month_divider_duration = 2.0

        result = AssemblerTripMixin()._build_clips_with_location_dividers(
            clips, mock_gen, mock_settings, None
        )

        # clip1, clip2 (no GPS), divider, clip3 = 4
        assert len(result) == 4

    @staticmethod
    def _make_clip(
        path: str = "test.mp4",
        lat: float | None = None,
        lon: float | None = None,
        name: str | None = None,
    ):
        from pathlib import Path as P

        from immich_memories.processing.assembly_config import AssemblyClip

        return AssemblyClip(
            path=P(path),
            duration=5.0,
            latitude=lat,
            longitude=lon,
            location_name=name,
        )


class TestLocationDiversityScoring:
    """Trip clip selection should favor diverse locations."""

    def test_clips_from_new_locations_get_bonus(self):
        """Clips from underrepresented locations should score higher."""
        from immich_memories.analysis.trip_scoring import location_diversity_bonus

        selected = [
            {"location_name": "Barcelona", "latitude": 41.39, "longitude": 2.17},
            {"location_name": "Barcelona", "latitude": 41.39, "longitude": 2.17},
        ]
        # New location should get a bonus
        bonus = location_diversity_bonus(
            candidate_location="Paris",
            candidate_lat=48.86,
            candidate_lon=2.35,
            selected_locations=selected,
        )
        assert bonus > 0

    def test_clips_from_existing_locations_get_no_bonus(self):
        """Clips from already-selected locations should get zero bonus."""
        from immich_memories.analysis.trip_scoring import location_diversity_bonus

        selected = [
            {"location_name": "Barcelona", "latitude": 41.39, "longitude": 2.17},
        ]
        bonus = location_diversity_bonus(
            candidate_location="Barcelona",
            candidate_lat=41.40,
            candidate_lon=2.18,
            selected_locations=selected,
        )
        assert bonus == 0.0

    def test_empty_selection_gives_bonus(self):
        """First clip always gets a bonus (new location by definition)."""
        from immich_memories.analysis.trip_scoring import location_diversity_bonus

        bonus = location_diversity_bonus(
            candidate_location="Barcelona",
            candidate_lat=41.39,
            candidate_lon=2.17,
            selected_locations=[],
        )
        assert bonus > 0

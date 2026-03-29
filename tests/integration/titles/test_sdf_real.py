"""Real SDF font atlas generation and text measurement tests.

Generates actual SDF atlases from font files using FreeType and
verifies atlas structure, glyph coverage, and text measurement.

Run: make test-integration-titles
"""

from __future__ import annotations

import numpy as np
import pytest

from immich_memories.titles.sdf_font import FREETYPE_AVAILABLE, SDFFontAtlas, find_font

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not FREETYPE_AVAILABLE, reason="freetype-py not installed"),
]


@pytest.fixture(scope="module")
def font_path():
    """Find a usable font, skip if none available."""
    path = find_font("Montserrat", "regular")
    if path is None:
        path = find_font("Helvetica", "regular")
    if path is None:
        path = find_font("Arial", "regular")
    if path is None:
        pytest.skip("No usable font found on this system")
    return path


@pytest.fixture(scope="module")
def atlas(font_path):
    """Generate a real SDF atlas — shared across tests in this module."""
    from immich_memories.titles.sdf_atlas_gen import generate_sdf_atlas

    return generate_sdf_atlas(font_path, font_size=48, charset="ABCDEFGHabcdefgh 0123456789.,!")


class TestGenerateSDFAtlas:
    def test_returns_atlas(self, atlas):
        assert isinstance(atlas, SDFFontAtlas)

    def test_texture_is_2d_uint8(self, atlas):
        assert atlas.texture.ndim == 2
        assert atlas.texture.dtype == np.uint8

    def test_texture_has_content(self, atlas):
        # WHY: an all-zero atlas means glyph rendering failed silently
        assert atlas.texture.max() > 0, "Atlas texture is all zeros"

    def test_font_metrics_positive(self, atlas):
        assert atlas.font_size == 48
        assert atlas.line_height > 0
        assert atlas.ascender > 0


class TestGlyphCoverage:
    def test_covers_requested_chars(self, atlas):
        requested = set("ABCDEFGHabcdefgh 0123456789.,!")
        covered = set(atlas.glyphs.keys())
        missing = requested - covered
        assert not missing, f"Atlas missing glyphs: {missing}"

    def test_glyph_metrics_valid(self, atlas):
        for char, metrics in atlas.glyphs.items():
            assert metrics.atlas_width >= 0, f"Glyph '{char}' has negative width"
            assert metrics.atlas_height >= 0, f"Glyph '{char}' has negative height"
            # Space may have zero advance_x on some fonts, but printable chars should not
            if char not in (" ",):
                assert metrics.advance_x > 0, f"Glyph '{char}' has zero advance"


class TestFindFont:
    def test_find_font_returns_path(self, font_path):
        """find_font should resolve to an existing file."""
        assert font_path.exists()
        assert font_path.suffix in (".ttf", ".otf", ".ttc")


class TestMeasureText:
    def test_positive_dimensions(self, atlas):
        from immich_memories.titles.sdf_font_rendering import measure_text

        width, height = measure_text("Hello", atlas, scale=1.0)
        assert width > 0
        assert height > 0

    def test_longer_text_is_wider(self, atlas):
        from immich_memories.titles.sdf_font_rendering import measure_text

        short_w, _ = measure_text("Hi", atlas, scale=1.0)
        long_w, _ = measure_text("Hello World 123", atlas, scale=1.0)
        assert long_w > short_w, "Longer text should be wider"

    def test_scale_affects_width(self, atlas):
        from immich_memories.titles.sdf_font_rendering import measure_text

        w1, h1 = measure_text("Test", atlas, scale=1.0)
        w2, h2 = measure_text("Test", atlas, scale=2.0)
        assert w2 > w1, "Doubled scale should produce wider text"
        assert h2 > h1, "Doubled scale should produce taller text"

    def test_empty_text_zero_width(self, atlas):
        from immich_memories.titles.sdf_font_rendering import measure_text

        width, height = measure_text("", atlas, scale=1.0)
        assert width == 0.0

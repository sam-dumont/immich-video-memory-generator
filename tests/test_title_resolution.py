"""Tests for title screen resolution override (bug #188).

When output_resolution is 720p, title screens should render at 720x1280,
not at the hardcoded 1080x1920 that the tier-based lookup returns.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from immich_memories.processing.title_inserter import TitleInserter
from immich_memories.titles.generator import TitleScreenConfig


def _fake_title_settings(**overrides) -> SimpleNamespace:
    """Minimal title_settings stub with required attributes."""
    defaults = {
        "title_duration": 3.5,
        "month_divider_duration": 2.0,
        "ending_duration": 4.0,
        "locale": "en",
        "style_mode": "auto",
        "show_month_dividers": True,
        "month_divider_threshold": 2,
        "title_override": None,
        "subtitle_override": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestTitleScreenConfigResolutionOverride:
    """TitleScreenConfig.output_resolution should use explicit pixel overrides when set."""

    def test_explicit_pixel_overrides_returned(self):
        config = TitleScreenConfig(
            orientation="portrait",
            resolution="1080p",
            resolution_width=720,
            resolution_height=1280,
        )
        assert config.output_resolution == (720, 1280)

    def test_without_overrides_falls_back_to_tier_lookup(self):
        config = TitleScreenConfig(
            orientation="portrait",
            resolution="1080p",
        )
        assert config.output_resolution == (1080, 1920)


class TestBuildTitleConfigResolution:
    """_build_title_config must pass actual pixel dims, not tier-based guesses."""

    def test_720p_portrait_gets_exact_pixels(self):
        # WHY: mock prober — it probes video files via FFmpeg subprocess
        inserter = TitleInserter(settings=MagicMock(), prober=MagicMock())
        config = inserter._build_title_config(
            title_settings=_fake_title_settings(),
            target_w=720,
            target_h=1280,
            fps=30,
            hdr=False,
        )
        assert config.output_resolution == (720, 1280)

    def test_1080p_landscape_gets_exact_pixels(self):
        # WHY: mock prober — it probes video files via FFmpeg subprocess
        inserter = TitleInserter(settings=MagicMock(), prober=MagicMock())
        config = inserter._build_title_config(
            title_settings=_fake_title_settings(),
            target_w=1920,
            target_h=1080,
            fps=30,
            hdr=True,
        )
        assert config.output_resolution == (1920, 1080)

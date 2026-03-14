"""Tests for post-pipeline LLM title generation helper."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from immich_memories.timeperiod import DateRange
from immich_memories.ui.state import AppState
from tests.conftest import make_clip


def _make_date_range(
    start: datetime = datetime(2024, 7, 1, tzinfo=UTC),
    end: datetime = datetime(2024, 7, 14, tzinfo=UTC),
) -> DateRange:
    return DateRange(start=start, end=end)


class TestCollectClipDescriptions:
    """Extract LLM descriptions from analysis cache."""

    def test_returns_empty_when_no_cache(self):
        from immich_memories.ui.pages.pipeline_title import _collect_clip_descriptions

        state = AppState()
        state.analysis_cache = None
        assert _collect_clip_descriptions(state) == []

    def test_returns_empty_when_no_selected_clips(self):
        from immich_memories.ui.pages.pipeline_title import _collect_clip_descriptions

        state = AppState()
        state.analysis_cache = MagicMock()
        state.selected_clip_ids = set()
        assert _collect_clip_descriptions(state) == []

    def test_returns_descriptions_from_best_segment(self):
        from immich_memories.ui.pages.pipeline_title import _collect_clip_descriptions

        state = AppState()
        clip = make_clip("a1")
        state.clips = [clip]
        state.selected_clip_ids = {"a1"}

        seg = MagicMock()
        seg.llm_description = "kids playing on beach"

        analysis = MagicMock()
        analysis.segments = [seg]
        analysis.get_best_segment.return_value = seg

        state.analysis_cache = MagicMock()
        state.analysis_cache.get_analysis.return_value = analysis

        result = _collect_clip_descriptions(state)
        assert result == ["kids playing on beach"]

    def test_skips_clip_with_no_analysis(self):
        from immich_memories.ui.pages.pipeline_title import _collect_clip_descriptions

        state = AppState()
        state.clips = [make_clip("a1"), make_clip("a2")]
        state.selected_clip_ids = {"a1", "a2"}

        state.analysis_cache = MagicMock()
        state.analysis_cache.get_analysis.return_value = None

        result = _collect_clip_descriptions(state)
        assert result == []


class TestGenerateTitleAfterPipeline:
    """Wire generate_title_with_llm into AppState."""

    @pytest.mark.asyncio
    async def test_skips_when_no_llm_model(self):
        from immich_memories.ui.pages.pipeline_title import generate_title_after_pipeline

        state = AppState()
        state.date_range = _make_date_range()

        with (
            patch(
                "immich_memories.ui.pages.pipeline_title.get_config", return_value=MagicMock()
            ) as mock_cfg,
            patch(
                "immich_memories.ui.pages.pipeline_title.generate_title_with_llm",
                new_callable=AsyncMock,
            ) as mock_llm,
        ):
            mock_cfg.return_value.title_llm = None
            mock_cfg.return_value.llm.model = ""
            await generate_title_after_pipeline(state)

        mock_llm.assert_not_called()
        assert state.title_suggestion_title is None

    @pytest.mark.asyncio
    async def test_skips_when_no_date_range(self):
        from immich_memories.ui.pages.pipeline_title import generate_title_after_pipeline

        state = AppState()
        state.date_range = None

        with (
            patch(
                "immich_memories.ui.pages.pipeline_title.get_config", return_value=MagicMock()
            ) as mock_cfg,
            patch(
                "immich_memories.ui.pages.pipeline_title.generate_title_with_llm",
                new_callable=AsyncMock,
            ) as mock_llm,
        ):
            mock_cfg.return_value.llm.model = "omlx"
            await generate_title_after_pipeline(state)

        mock_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_stores_suggestion_in_state(self):
        from immich_memories.titles.llm_titles import TitleSuggestion
        from immich_memories.ui.pages.pipeline_title import generate_title_after_pipeline

        state = AppState()
        state.date_range = _make_date_range()
        state.memory_type = "year"
        state.analysis_cache = None

        suggestion = TitleSuggestion(
            title="Summer 2024",
            subtitle="Memories from July",
            trip_type=None,
            map_mode=None,
        )

        with (
            patch(
                "immich_memories.ui.pages.pipeline_title.get_config", return_value=MagicMock()
            ) as mock_cfg,
            patch(
                "immich_memories.ui.pages.pipeline_title.generate_title_with_llm",
                new_callable=AsyncMock,
                return_value=suggestion,
            ),
        ):
            cfg = mock_cfg.return_value
            cfg.llm.model = "omlx"
            cfg.title_screens.locale = "en"
            await generate_title_after_pipeline(state)

        assert state.title_suggestion_title == "Summer 2024"
        assert state.title_suggestion_subtitle == "Memories from July"
        assert state.title_suggestion_trip_type is None
        assert state.title_suggestion_map_mode is None

    @pytest.mark.asyncio
    async def test_graceful_on_llm_failure(self):
        """State stays None if LLM raises unexpectedly."""
        from immich_memories.ui.pages.pipeline_title import generate_title_after_pipeline

        state = AppState()
        state.date_range = _make_date_range()
        state.memory_type = "year"
        state.analysis_cache = None

        with (
            patch(
                "immich_memories.ui.pages.pipeline_title.get_config", return_value=MagicMock()
            ) as mock_cfg,
            patch(
                "immich_memories.ui.pages.pipeline_title.generate_title_with_llm",
                new_callable=AsyncMock,
                side_effect=RuntimeError("unexpected"),
            ),
        ):
            cfg = mock_cfg.return_value
            cfg.llm.model = "omlx"
            cfg.title_screens.locale = "en"
            # Must not raise
            await generate_title_after_pipeline(state)

        assert state.title_suggestion_title is None

    @pytest.mark.asyncio
    async def test_person_names_from_selected_person(self):
        """Person names are extracted from state.selected_person."""
        from immich_memories.titles.llm_titles import TitleSuggestion
        from immich_memories.ui.pages.pipeline_title import generate_title_after_pipeline

        state = AppState()
        state.date_range = _make_date_range()
        state.memory_type = "person"
        state.analysis_cache = None

        person = MagicMock()
        person.name = "Alice"
        state.selected_person = person

        suggestion = TitleSuggestion(title="Alice Through the Years")

        with (
            patch(
                "immich_memories.ui.pages.pipeline_title.get_config", return_value=MagicMock()
            ) as mock_cfg,
            patch(
                "immich_memories.ui.pages.pipeline_title.generate_title_with_llm",
                new_callable=AsyncMock,
                return_value=suggestion,
            ) as mock_llm,
        ):
            cfg = mock_cfg.return_value
            cfg.llm.model = "omlx"
            cfg.title_screens.locale = "en"
            await generate_title_after_pipeline(state)

        call_kwargs = mock_llm.call_args.kwargs
        assert "Alice" in call_kwargs["person_names"]

    @pytest.mark.asyncio
    async def test_trip_type_stores_map_mode(self):
        """Trip-type suggestion sets trip_type and map_mode in state."""
        from immich_memories.titles.llm_titles import TitleSuggestion
        from immich_memories.ui.pages.pipeline_title import generate_title_after_pipeline

        state = AppState()
        state.date_range = _make_date_range()
        state.memory_type = "trip"
        state.clips = []
        state.analysis_cache = None

        suggestion = TitleSuggestion(
            title="A Week in Bretagne",
            trip_type="multi_base",
            map_mode="excursions",
        )

        with (
            patch(
                "immich_memories.ui.pages.pipeline_title.get_config", return_value=MagicMock()
            ) as mock_cfg,
            patch(
                "immich_memories.ui.pages.pipeline_title.generate_title_with_llm",
                new_callable=AsyncMock,
                return_value=suggestion,
            ),
        ):
            cfg = mock_cfg.return_value
            cfg.title_llm = None
            cfg.llm.model = "omlx"
            cfg.title_screens.locale = "en"
            await generate_title_after_pipeline(state)

        assert state.title_suggestion_trip_type == "multi_base"
        assert state.title_suggestion_map_mode == "excursions"

"""Tests for LLM title generation and response parsing."""

from __future__ import annotations

import pytest


class TestParseTitleResponse:
    """Parse LLM JSON response into TitleSuggestion."""

    def test_parses_valid_json(self):
        from immich_memories.titles.llm_titles import TitleSuggestion, parse_title_response

        raw = '{"title": "A Week in Bretagne", "subtitle": "From Brasparts to Frehel", "trip_type": "multi_base", "map_mode": "excursions", "map_mode_reason": "Two bases"}'
        result = parse_title_response(raw)
        assert isinstance(result, TitleSuggestion)
        assert result.title == "A Week in Bretagne"
        assert result.subtitle == "From Brasparts to Frehel"
        assert result.trip_type == "multi_base"
        assert result.map_mode == "excursions"

    def test_strips_markdown_code_block(self):
        from immich_memories.titles.llm_titles import parse_title_response

        raw = '```json\n{"title": "Summer 2024", "subtitle": null, "trip_type": null, "map_mode": null, "map_mode_reason": null}\n```'
        result = parse_title_response(raw)
        assert result is not None
        assert result.title == "Summer 2024"

    def test_rejects_invalid_trip_type(self):
        from immich_memories.titles.llm_titles import parse_title_response

        raw = '{"title": "Trip", "subtitle": null, "trip_type": "invalid_type", "map_mode": null, "map_mode_reason": null}'
        result = parse_title_response(raw)
        assert result is not None
        assert result.trip_type is None

    def test_truncates_long_title(self):
        from immich_memories.titles.llm_titles import parse_title_response

        raw = (
            '{"title": "'
            + "A" * 200
            + '", "subtitle": null, "trip_type": null, "map_mode": null, "map_mode_reason": null}'
        )
        result = parse_title_response(raw)
        assert result is not None
        assert len(result.title) <= 80

    def test_returns_none_on_malformed_json(self):
        from immich_memories.titles.llm_titles import parse_title_response

        assert parse_title_response("not json at all") is None
        assert parse_title_response("") is None

    def test_returns_none_on_missing_title(self):
        from immich_memories.titles.llm_titles import parse_title_response

        raw = '{"subtitle": "no title field"}'
        assert parse_title_response(raw) is None


class TestBuildTitlePrompt:
    """Build context-rich prompt for the LLM."""

    def test_trip_prompt_includes_bases_and_descriptions(self):
        from immich_memories.titles.llm_titles import build_title_prompt

        prompt = build_title_prompt(
            memory_type="trip",
            locale="en",
            start_date="2023-09-23",
            end_date="2023-09-29",
            duration_days=7,
            daily_locations=[
                "2023-09-23: Brasparts (48.30, -3.96)",
                "2023-09-24: Camaret (48.28, -4.59)",
                "2023-09-27: Frehel (48.69, -2.34)",
            ],
            country="France",
            clip_descriptions=["hiking along cliffs", "sunset over bay"],
            smart_objects=["person", "beach"],
        )
        assert "trip" in prompt.lower()
        assert "Brasparts" in prompt
        assert "French" not in prompt  # locale=en → English
        assert "hiking along cliffs" in prompt

    def test_person_prompt_includes_names(self):
        from immich_memories.titles.llm_titles import build_title_prompt

        prompt = build_title_prompt(
            memory_type="person",
            locale="fr",
            start_date="2019-01-01",
            end_date="2025-12-31",
            duration_days=2556,
            person_names=["Alice", "Emile"],
            clip_descriptions=["playing in park", "birthday party"],
        )
        assert "Alice" in prompt
        assert "Emile" in prompt
        assert "French" in prompt

    def test_includes_rules(self):
        from immich_memories.titles.llm_titles import build_title_prompt

        prompt = build_title_prompt(
            memory_type="year",
            locale="en",
            start_date="2024-01-01",
            end_date="2024-12-31",
            duration_days=366,
        )
        assert "RULES" in prompt
        assert "weekend" in prompt.lower()  # constraint about weekend usage


class TestGenerateTitleWithLlm:
    """End-to-end: build prompt, query LLM, parse response."""

    @pytest.mark.asyncio
    async def test_returns_title_suggestion_on_success(self):
        from unittest.mock import AsyncMock, patch

        from immich_memories.config_models import LLMConfig
        from immich_memories.titles.llm_titles import TitleSuggestion, generate_title_with_llm

        config = LLMConfig(
            provider="openai-compatible", base_url="http://localhost:8080/v1", model="omlx"
        )
        llm_response = '{"title": "Summer in Crete", "subtitle": "Chania to Sitia", "trip_type": "multi_base", "map_mode": "excursions", "map_mode_reason": "Two bases"}'

        with patch(
            "immich_memories.titles.llm_titles.query_llm",
            new_callable=AsyncMock,
            return_value=llm_response,
        ):
            result = await generate_title_with_llm(
                memory_type="trip",
                locale="en",
                start_date="2019-07-04",
                end_date="2019-07-14",
                duration_days=11,
                daily_locations=[
                    "2019-07-04: Platanos (35.50, 23.96)",
                    "2019-07-10: Sitia (35.19, 26.10)",
                ],
                country="Greece",
                llm_config=config,
            )

        assert isinstance(result, TitleSuggestion)
        assert result.title == "Summer in Crete"
        assert result.map_mode == "excursions"

    @pytest.mark.asyncio
    async def test_returns_none_on_llm_failure(self):
        from unittest.mock import AsyncMock, patch

        import httpx

        from immich_memories.config_models import LLMConfig
        from immich_memories.titles.llm_titles import generate_title_with_llm

        config = LLMConfig(
            provider="openai-compatible", base_url="http://localhost:8080/v1", model="omlx"
        )

        with patch(
            "immich_memories.titles.llm_titles.query_llm",
            new_callable=AsyncMock,
            side_effect=httpx.HTTPError("timeout"),
        ):
            result = await generate_title_with_llm(
                memory_type="year",
                locale="en",
                start_date="2024-01-01",
                end_date="2024-12-31",
                duration_days=366,
                llm_config=config,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_config(self):
        from immich_memories.titles.llm_titles import generate_title_with_llm

        result = await generate_title_with_llm(
            memory_type="year",
            locale="en",
            start_date="2024-01-01",
            end_date="2024-12-31",
            duration_days=366,
            llm_config=None,
        )
        assert result is None

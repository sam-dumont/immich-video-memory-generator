"""The LLM holistic pass (#468): the set reviewed as a whole — redundancy,
coherence, feel — with each clip's raw description, not pre-digested stats."""

from __future__ import annotations

from unittest.mock import patch

from immich_memories.analysis.selection_review import review_selection
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.config_models import LLMConfig
from tests.conftest import make_clip


def _member(asset_id: str, description: str, score: float = 0.7) -> ClipWithSegment:
    clip = make_clip(asset_id, duration=10.0)
    clip.llm_description = description
    clip.llm_emotion = "joy"
    return ClipWithSegment(clip=clip, start_time=0.0, end_time=5.0, score=score)


def _selection() -> list[ClipWithSegment]:
    return [
        _member("a-1", "Child blows out birthday candles"),
        _member("a-2", "Child blows out birthday candles again, same table"),
        _member("a-3", "Family walk on the beach at sunset"),
    ]


class TestReviewSelection:
    def test_llm_named_drops_are_returned_as_asset_ids(self):
        # WHY: the LLM is the external boundary; the contract is prompt in, JSON out
        with patch(
            "immich_memories.analysis.selection_review._ask",
            return_value='{"drop": [{"index": 2, "reason": "duplicate of clip 1"}]}',
        ):
            drops = review_selection(_selection(), LLMConfig())

        assert drops == ["a-2"]

    def test_the_prompt_carries_the_raw_descriptions(self):
        """Raw data to the LLM, not summaries — it finds the patterns."""
        # WHY: the LLM call is the external boundary; we inspect the prompt
        with patch(
            "immich_memories.analysis.selection_review._ask", return_value='{"drop": []}'
        ) as ask:
            review_selection(_selection(), LLMConfig())

        prompt = ask.call_args[0][0]
        assert "birthday candles" in prompt and "beach at sunset" in prompt

    def test_an_llm_failure_never_breaks_selection(self):
        # WHY: the LLM call is the external boundary
        with patch(
            "immich_memories.analysis.selection_review._ask",
            side_effect=RuntimeError("model gone"),
        ):
            assert review_selection(_selection(), LLMConfig()) == []

    def test_garbage_output_drops_nothing(self):
        # WHY: the LLM call is the external boundary
        with patch(
            "immich_memories.analysis.selection_review._ask",
            return_value="sure! here are my thoughts...",
        ):
            assert review_selection(_selection(), LLMConfig()) == []

    def test_the_llm_cannot_gut_the_video(self):
        """A cap keeps an overeager model from dropping half the memory."""
        # WHY: the LLM call is the external boundary
        with patch(
            "immich_memories.analysis.selection_review._ask",
            return_value='{"drop": [{"index": 1}, {"index": 2}, {"index": 3}]}',
        ):
            drops = review_selection(_selection(), LLMConfig())

        assert len(drops) <= 1  # 20% of 3, floored, min 1

"""The LLM holistic pass (#468): the set reviewed as a whole — redundancy,
coherence, feel — with each clip's raw description, not pre-digested stats."""

from __future__ import annotations

from unittest.mock import patch

from immich_memories.analysis.selection_review import review_selection
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.config_models_llm import LLMConfig
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
            return_value='{"keep": [1, 3], "cut": [{"index": 2, "reason": "duplicate of 1"}]}',
        ):
            drops = review_selection(_selection(), LLMConfig()).drops

        assert drops == ["a-2"]

    def test_the_prompt_carries_the_raw_descriptions(self):
        """Raw data to the LLM, not summaries — it finds the patterns."""
        # WHY: the LLM call is the external boundary; we inspect the prompt
        with patch(
            "immich_memories.analysis.selection_review._ask",
            return_value='{"keep": [1, 2, 3], "cut": []}',
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
            assert review_selection(_selection(), LLMConfig()).drops == []

    def test_garbage_output_drops_nothing(self):
        # WHY: the LLM call is the external boundary
        with patch(
            "immich_memories.analysis.selection_review._ask",
            return_value="sure! here are my thoughts...",
        ):
            assert review_selection(_selection(), LLMConfig()).drops == []

    def test_a_cut_that_keeps_nothing_is_not_an_edit(self):
        """What used to be a 20% cap (#764).

        The cap existed because the pass vetoed a finished cut and an
        overeager model could gut it two clips at a time. The pass now MAKES
        the cut, so a cap would be the pipeline overruling the only judgment
        in it. The one answer still refused is the one that leaves no memory
        at all.
        """
        # WHY: the LLM call is the external boundary
        with patch(
            "immich_memories.analysis.selection_review._ask",
            return_value='{"keep": [], "cut": [{"index": 1}, {"index": 2}, {"index": 3}]}',
        ):
            drops = review_selection(_selection(), LLMConfig()).drops

        assert drops == []


class TestTheClipLineCarriesRealFields:
    """Found by the #475 investigation: _clip_line read `date`, `location_name`
    and `transcript` off VideoClipInfo, which has none of them — three of the
    fields the prompt claims to send were silently absent. Place in particular
    is the evidence a "does this set hang together" question needs."""

    def test_date_and_place_reach_the_prompt(self):
        from datetime import UTC, datetime

        from immich_memories.api.models import ExifInfo

        member = _member("a-1", "Kids on the sand")
        member.clip.asset.file_created_at = datetime(2025, 8, 14, tzinfo=UTC)
        member.clip.asset.exif_info = ExifInfo(city="Jette", country="Belgium")
        selection = [member, _member("a-2", "Ferry crossing"), _member("a-3", "Dinner")]

        # WHY: the LLM call is the external boundary; we inspect the prompt
        with patch(
            "immich_memories.analysis.selection_review._ask",
            return_value='{"keep": [1, 2, 3], "cut": []}',
        ) as ask:
            review_selection(selection, LLMConfig())

        prompt = ask.call_args[0][0]
        assert "2025-08-14" in prompt
        assert "Jette, Belgium" in prompt

    def test_a_clip_without_exif_still_renders(self):
        # WHY: the LLM call is the external boundary; we inspect the prompt
        with patch(
            "immich_memories.analysis.selection_review._ask",
            return_value='{"keep": [1, 2, 3], "cut": []}',
        ) as ask:
            review_selection(_selection(), LLMConfig())

        assert "Clip 1:" in ask.call_args[0][0]


class TestReviewThinksWhenTheServerCan:
    """The holistic review is a judgement call — it reasons when allowed to."""

    def test_review_request_carries_the_thinking_switch(self):
        from unittest.mock import AsyncMock, MagicMock

        # WHY: the LLM server is the external boundary; the assertion is on
        # the request the review sends it, through the real query layer.
        response = AsyncMock()
        response.status_code = 200
        response.json = MagicMock(
            return_value={
                "choices": [
                    {
                        "message": {"content": '{"keep": [1, 2, 3], "cut": []}'},
                        "finish_reason": "stop",
                    }
                ]
            }
        )
        response.raise_for_status = lambda: None

        config = LLMConfig(provider="openai-compatible", model="qwen", thinking=True)
        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=response) as mock_post:
            review_selection(_selection(), config)

        assert mock_post.call_args[1]["json"]["chat_template_kwargs"] == {"enable_thinking": True}


class TestSilenceIsNotApproval:
    """Five outcomes all returned [] and only one of them said anything.

    A rendered year recap came back with 38 clips and no drop lines, which
    reads as "the model looked and approved". It had returned an empty
    string. The pass is fail-open by design so a broken model cannot gut a
    memory, and that is exactly what makes the two indistinguishable.
    """

    def test_a_reviewed_and_approved_cut_says_so(self, caplog):
        import logging

        # WHY: the LLM is the external boundary; here it approves the set.
        with (
            patch(
                "immich_memories.analysis.selection_review._ask",
                return_value='{"keep": [1, 2, 3], "cut": []}',
            ),
            caplog.at_level(logging.INFO, logger="immich_memories.analysis.selection_review"),
        ):
            drops = review_selection(_selection(), LLMConfig()).drops

        assert drops == []
        assert any(
            record.levelno == logging.INFO and "nothing to drop" in record.message
            for record in caplog.records
        ), f"an approved cut said nothing: {[r.message for r in caplog.records]}"

    def test_a_review_that_could_not_run_warns(self, caplog):
        """An unreachable or silent model is a degraded run, not a verdict."""
        import logging

        # WHY: the LLM is the external boundary; here it answers with nothing.
        with (
            patch("immich_memories.analysis.selection_review._ask", return_value=""),
            caplog.at_level(logging.DEBUG, logger="immich_memories.analysis.selection_review"),
        ):
            drops = review_selection(_selection(), LLMConfig()).drops

        assert drops == []
        assert any(record.levelno >= logging.WARNING for record in caplog.records), (
            f"a review that never ran looked like approval: {[r.message for r in caplog.records]}"
        )

    def test_an_answer_that_will_not_parse_warns(self, caplog):
        """Braces the model could not fill are a failure, not an approval."""
        import logging

        # WHY: the LLM is the external boundary; here its answer is malformed.
        with (
            patch(
                "immich_memories.analysis.selection_review._ask",
                return_value='here you go: {"drop": [oops}',
            ),
            caplog.at_level(logging.DEBUG, logger="immich_memories.analysis.selection_review"),
        ):
            drops = review_selection(_selection(), LLMConfig()).drops

        assert drops == []
        assert any(record.levelno >= logging.WARNING for record in caplog.records)

    def test_a_cut_too_small_to_judge_as_a_set_is_not_a_failure(self, caplog):
        """Two clips are not a set, and saying so is not worth a warning."""
        import logging

        with caplog.at_level(logging.DEBUG, logger="immich_memories.analysis.selection_review"):
            drops = review_selection(_selection()[:2], LLMConfig()).drops

        assert drops == []
        assert not any(record.levelno >= logging.WARNING for record in caplog.records)
        assert any("too few" in record.message for record in caplog.records)

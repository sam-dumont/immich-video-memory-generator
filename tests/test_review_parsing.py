r"""The review has to find the verdict in what the model actually says.

Enabling server-side thinking made the model reason in the CONTENT channel on
long prompts. Two things then broke at once, and fail-open hid both:

- max_tokens=500 (the query_llm default, never overridden here) truncated the
  answer before the verdict existed;
- the parser took re.search(r"\{.*\}", DOTALL) — first brace to last — and the
  model quotes the prompt's own format spec while reasoning, so the match was
  literally {"drop": [{"index": <clip number>, ...}]}.

json.loads choked on <clip number>, the broad catch returned [], and the
review reported "nothing to drop" on every call.

Fixtures are real captured responses, not hand-written approximations.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from immich_memories.analysis.selection_review import review_selection

_FIXTURES = Path(__file__).parent / "fixtures"


def _member(asset_id: str, index: int):
    from datetime import UTC, datetime

    asset = SimpleNamespace(
        id=asset_id,
        is_favorite=False,
        file_created_at=datetime(2021, 8, index + 1, 10, tzinfo=UTC),
        exif_info=None,
        people=[],
    )
    return SimpleNamespace(
        clip=SimpleNamespace(asset=asset, llm_description=f"clip {index}"),
        start_time=0.0,
        end_time=4.0,
        score=0.5,
        analyzed=True,
    )


def _selection(n: int = 14):
    return [_member(f"asset-{i}", i) for i in range(1, n + 1)]


def _config():
    return SimpleNamespace(model="qwen", thinking=True)


def test_a_verdict_buried_in_reasoning_prose_is_found() -> None:
    r"""The real failure: 6916 chars of prose quoting the prompt's format spec.

    re.search(r"\{.*\}", DOTALL) grabs {"drop": [{"index": <number>, ...}]} —
    the template echo — and json.loads chokes on <number>. The verdict, when
    the model gets far enough to produce one, is elsewhere in the answer.
    """
    answer = (_FIXTURES / "review_prose_then_verdict.txt").read_text()
    assert '"index": <number>' in answer, "fixture no longer carries the echo"

    # WHY: the LLM server is the external boundary; this is its real reply.
    with patch("immich_memories.analysis.selection_review._ask", return_value=answer):
        drops = review_selection(_selection(), _config()).drops

    # this particular capture never reached a verdict, so no drops — but the
    # parser must not have been fooled into treating the echo as one either
    assert drops == []


def test_a_verdict_after_prose_is_read() -> None:
    """A response that reasons aloud and then answers must be read."""
    keep = list(range(1, 14))
    answer = (
        "Here is a thinking process:\n\n1. The format is "
        '{"keep": [<clip numbers>], "cut": [{"index": <number>, "reason": "<short reason>"}]}\n'
        "2. Clip 14 is a plain portrait.\n\n"
        f'Final answer:\n{{"keep": {keep}, '
        '"cut": [{"index": 14, "reason": "plain portrait"}]}'
    )

    # WHY: the LLM server is the external boundary.
    with patch("immich_memories.analysis.selection_review._ask", return_value=answer):
        drops = review_selection(_selection(), _config()).drops

    assert drops == ["asset-14"], f"the template echo beat the real verdict: {drops}"


def test_an_answer_in_the_retired_drop_shape_is_not_a_cut(caplog) -> None:
    """A real capture from before the pass answered with the cut (#764).

    Kept as a capture rather than rewritten into the new shape: the point is
    that a well-formed answer to the OLD question is not silently read as an
    answer to the new one. It names one clip to drop and says nothing about
    the other thirteen, so it cannot be a cut — and a pass that guessed the
    rest would be inventing thirteen verdicts.
    """
    import logging

    answer = (_FIXTURES / "review_reasoning_complete.txt").read_text()
    assert '"drop"' in answer, "fixture no longer carries the retired shape"

    # WHY: the LLM server is the external boundary; this is its real reply.
    with (
        patch("immich_memories.analysis.selection_review._ask", return_value=answer),
        caplog.at_level(logging.WARNING, logger="immich_memories.analysis.selection_review"),
    ):
        drops = review_selection(_selection(), _config()).drops

    assert drops == []
    assert any(record.levelno >= logging.WARNING for record in caplog.records)


def test_the_prompt_template_echo_is_not_mistaken_for_a_verdict(caplog) -> None:
    """The truncated response: reasoning prose quoting the format spec, no verdict.

    It must produce no drops AND say so — a review that could not read the
    answer is not a review that approved the cut.
    """
    import logging

    answer = (_FIXTURES / "review_reasoning_truncated.txt").read_text()
    assert "<clip number>" in answer, "fixture no longer carries the echo"

    # WHY: the LLM server is the external boundary; this is its real reply.
    with (
        patch("immich_memories.analysis.selection_review._ask", return_value=answer),
        caplog.at_level(logging.WARNING, logger="immich_memories.analysis.selection_review"),
    ):
        drops = review_selection(_selection(), _config()).drops

    assert drops == []
    assert any(r.levelno >= logging.WARNING for r in caplog.records), (
        "an unreadable answer was indistinguishable from an approved cut"
    )

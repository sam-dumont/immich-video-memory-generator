"""The special-day ask looks with fast eyes, then reasons about what it saw.

Measured on 14 real candidate days: a single vision call said "special" to all
of them, and one vision call that also reasoned truncated on 6 of 14 even at
4000 tokens, because the reasoning leaks into the content and stops parsing.
Splitting the two is the only shape that discriminates.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from immich_memories.analysis.special_day import ask_if_special

_VERDICT = '{"special": true, "title": "Pace and Light", "subtitle": "", "what": ""}'


def _asset(hour: int = 10) -> SimpleNamespace:
    return SimpleNamespace(
        file_created_at=datetime(2021, 4, 4, hour, tzinfo=UTC),
        exif_info=SimpleNamespace(city="Someplace", country="Belgium"),
        people=[],
    )


def _recorder(calls: list):
    async def _record(prompt, _config, **kwargs):
        calls.append((prompt, kwargs))
        return (
            "1. a cake with candles\n2. people around a table" if kwargs.get("images") else _VERDICT
        )

    return _record


def test_the_pictures_are_read_first_then_reasoned_about_as_text() -> None:
    """Two calls, never one: images suppress thinking by API invariant."""
    calls: list = []
    asset = _asset()

    # WHY: the LLM server is the external boundary; both steps go through it.
    with patch("immich_memories.analysis.llm_query.query_llm", new=_recorder(calls)):
        ask_if_special(
            [asset],
            llm_config=SimpleNamespace(thinking=True),
            thumbnails=[(asset, b"jpeg-bytes")],
        )

    assert len(calls) == 2, f"expected a look then a judgement, got {len(calls)} call(s)"
    assert calls[0][1].get("images"), "step 1 has to actually see the pictures"
    assert not calls[0][1].get("thinking"), "step 1 is the fast tier"
    assert calls[1][1].get("thinking") is True, "step 2 is the judgement call"
    assert not calls[1][1].get("images"), "step 2 is text-only or it truncates"


def test_what_the_eyes_saw_is_what_the_judgement_reads() -> None:
    """A look that changes nothing about the prompt is a look for nothing."""
    calls: list = []
    asset = _asset()

    # WHY: the LLM server is the external boundary; both steps go through it.
    with patch("immich_memories.analysis.llm_query.query_llm", new=_recorder(calls)):
        ask_if_special(
            [asset],
            llm_config=SimpleNamespace(thinking=True),
            thumbnails=[(asset, b"jpeg-bytes")],
        )

    judgement_prompt = calls[1][0]
    assert "a cake with candles" in judgement_prompt


def test_a_server_that_cannot_reason_keeps_todays_behaviour() -> None:
    """llm.thinking gates this. Without it nothing changes for anybody."""
    calls: list = []
    asset = _asset()

    # WHY: the LLM server is the external boundary.
    with patch("immich_memories.analysis.llm_query.query_llm", new=_recorder(calls)):
        ask_if_special(
            [asset],
            llm_config=SimpleNamespace(thinking=False),
            thumbnails=[(asset, b"jpeg-bytes")],
        )

    assert len(calls) == 1, "no reasoning server means the old single vision call"
    assert calls[0][1].get("images"), "and it still sees the day"


def test_the_lines_the_judgement_read_are_recoverable_afterwards(caplog) -> None:
    """A day that comes back ordinary has to be diagnosable without a rerun."""
    import logging

    calls: list = []
    asset = _asset()

    # WHY: the LLM server is the external boundary.
    with (
        patch("immich_memories.analysis.llm_query.query_llm", new=_recorder(calls)),
        caplog.at_level(logging.DEBUG, logger="immich_memories.analysis.special_day"),
    ):
        ask_if_special(
            [asset],
            llm_config=SimpleNamespace(thinking=True),
            thumbnails=[(asset, b"jpeg-bytes")],
        )

    assert any("a cake with candles" in record.message for record in caplog.records)


def test_a_look_that_fails_still_gets_a_judgement() -> None:
    """An unreachable model on step one is not a verdict of ordinary."""
    asset = _asset()
    calls: list = []

    async def _blind(prompt, _config, **kwargs):
        if kwargs.get("images"):
            raise OSError("no route to host")
        calls.append((prompt, kwargs))
        return _VERDICT

    # WHY: the LLM server is the external boundary; here its vision tier is down.
    with patch("immich_memories.analysis.llm_query.query_llm", new=_blind):
        verdict = ask_if_special(
            [asset],
            llm_config=SimpleNamespace(thinking=True),
            thumbnails=[(asset, b"jpeg-bytes")],
        )

    assert len(calls) == 1, "the judgement still runs, on times and places alone"
    assert verdict.special is True

"""A model that cannot answer is not a verdict. A bug in our code is not a model.

The fail-open catch is load-bearing — an unreachable LLM must not turn every
day into "ordinary". But it swallowed our own mistakes too: a keyword argument
that did not match a callee's signature came back as a quiet special=False, and
in a real scan that shape means zero special days found, for every day, with
nothing in the log to say why.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from immich_memories.analysis.special_day import ask_if_special

_VERDICT = '{"special": true, "title": "Pace and Light", "subtitle": "", "what": ""}'


def _asset(hour: int = 10) -> SimpleNamespace:
    return SimpleNamespace(
        file_created_at=datetime(2021, 4, 4, hour, tzinfo=UTC),
        exif_info=SimpleNamespace(city="Someplace", country="Belgium"),
        people=[],
    )


def test_a_bug_in_our_own_code_stops_the_scan_loudly() -> None:
    """The exact incident: a signature mismatch read as "not special"."""
    # WHY: the ask path is the boundary; here our own call into it is wrong.
    with (
        patch(
            "immich_memories.analysis.special_day._ask",
            side_effect=TypeError("_capture() got an unexpected keyword argument 'thinking'"),
        ),
        pytest.raises(TypeError, match="unexpected keyword argument"),
    ):
        ask_if_special([_asset()], llm_config=SimpleNamespace())


def test_a_model_that_cannot_be_reached_is_still_quietly_not_a_verdict() -> None:
    """The fail-open path stays exactly as it was for transport failures."""
    # WHY: the LLM server is the external boundary; here it is unreachable.
    with patch(
        "immich_memories.analysis.special_day._ask", side_effect=OSError("no route to host")
    ):
        verdict = ask_if_special([_asset()], llm_config=SimpleNamespace())

    assert verdict.special is False


def test_a_bug_during_the_look_stops_the_scan_too() -> None:
    """Step one is our code as much as step two is."""
    asset = _asset()

    async def _broken(_prompt, _config, **kwargs):
        if kwargs.get("images"):
            raise AttributeError("'NoneType' object has no attribute 'name'")
        return _VERDICT

    # WHY: the LLM server is the external boundary; the bug is on our side of it.
    with (
        patch("immich_memories.analysis.llm_query.query_llm", new=_broken),
        pytest.raises(AttributeError, match="has no attribute"),
    ):
        ask_if_special(
            [asset],
            llm_config=SimpleNamespace(thinking=True),
            thumbnails=[(asset, b"jpeg-bytes")],
        )


def test_the_bug_is_logged_before_it_is_raised(caplog) -> None:
    """Loud in the log as well as loud in the traceback: an upstream catch
    somewhere later must not be able to erase the diagnosis."""
    import logging

    # WHY: the ask path is the boundary; here our own call into it is wrong.
    with (
        patch("immich_memories.analysis.special_day._ask", side_effect=TypeError("boom")),
        caplog.at_level(logging.ERROR, logger="immich_memories.analysis.special_day"),
        pytest.raises(TypeError),
    ):
        ask_if_special([_asset()], llm_config=SimpleNamespace())

    assert any(record.levelno >= logging.ERROR for record in caplog.records)


def test_the_selection_review_also_stops_on_our_own_bug() -> None:
    """The same fail-open catch, the same swallowed mistake, the same fix.

    Found live: changing _ask's signature made every review return "nothing to
    drop" instead of raising. A cut that nothing was dropped from reads as a
    cut nothing was wrong with.
    """
    from immich_memories.analysis.selection_review import review_selection

    members = [_asset() for _ in range(4)]
    selection = [
        SimpleNamespace(
            clip=SimpleNamespace(
                asset=SimpleNamespace(id=f"a{i}", is_favorite=False, **vars(m)),
                llm_description="something",
            ),
            start_time=0.0,
            end_time=4.0,
            score=0.5,
            analyzed=True,
        )
        for i, m in enumerate(members)
    ]

    # WHY: the ask path is the boundary; here our own call into it is wrong.
    with (
        patch(
            "immich_memories.analysis.selection_review._ask",
            side_effect=TypeError("unexpected keyword argument"),
        ),
        pytest.raises(TypeError, match="unexpected keyword"),
    ):
        review_selection(selection, SimpleNamespace(model="m", thinking=True))


def test_the_selection_review_still_survives_an_unreachable_model() -> None:
    """And the fail-open it exists for is untouched."""
    from immich_memories.analysis.selection_review import review_selection

    members = [_asset() for _ in range(4)]
    selection = [
        SimpleNamespace(
            clip=SimpleNamespace(
                asset=SimpleNamespace(id=f"a{i}", is_favorite=False, **vars(m)),
                llm_description="something",
            ),
            start_time=0.0,
            end_time=4.0,
            score=0.5,
            analyzed=True,
        )
        for i, m in enumerate(members)
    ]

    # WHY: the LLM server is the external boundary; here it is unreachable.
    with patch("immich_memories.analysis.selection_review._ask", side_effect=OSError("no route")):
        assert review_selection(selection, SimpleNamespace(model="m", thinking=True)) == []

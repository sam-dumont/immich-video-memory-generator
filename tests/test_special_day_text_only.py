"""The special-day scan reads text, and only text.

A day the caption bank has not been over used to fall back to sampled frames.
That was the last place in the pipeline where a reader was handed pixels, and
it is also where most of one library's polluted catalogue rows came from. What
replaces it is the text the library already holds about the day, and an honest
"not judged" when there is not enough of it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from immich_memories.analysis.special_day import PROMPT_VERSION, ask_if_special


def _asset(hour: int, *, described: bool = True, **extra) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"a-{hour}",
        file_created_at=datetime(2024, 8, 9, hour, tzinfo=UTC),
        exif_info=SimpleNamespace(
            city="Somewhere", country="Belgium", latitude=None, longitude=None
        ),
        people=[],
        llm_description="a table laid for lunch" if described else None,
        **extra,
    )


def _a_day(**kwargs) -> list[SimpleNamespace]:
    return [_asset(hour, **kwargs) for hour in range(9, 18)]


def _recorder(calls: list, reply: str):
    async def _record(prompt, _config, **kwargs):
        calls.append((prompt, kwargs))
        return reply

    return _record


_VERDICT = '{"special": true, "title": "Music by the lake", "subtitle": "", "what": "a festival"}'


def test_a_day_the_captions_do_not_cover_is_still_never_sent_a_picture() -> None:
    """The fallback to sampled frames is gone; the facts go on their own."""
    calls: list = []

    # WHY: the LLM server is the external boundary; the request built for it is the subject.
    with patch("immich_memories.analysis.llm_query.query_llm", new=_recorder(calls, _VERDICT)):
        ask_if_special(_a_day(), llm_config=SimpleNamespace())

    assert len(calls) == 1
    assert not calls[0][1].get("images"), "the scan must never hand a reader pixels"
    assert PROMPT_VERSION in calls[0][0]


def test_a_day_with_nothing_written_about_it_is_left_unjudged() -> None:
    """Bare clock times are not evidence of an ordinary day, and never were.

    Asked anyway, the reader answers from the calendar date: that is the guess
    that filled a real catalogue with pleasant afternoons.
    """
    bare = [
        SimpleNamespace(
            id=f"a-{hour}",
            file_created_at=datetime(2024, 8, 9, hour, tzinfo=UTC),
            exif_info=None,
            people=[],
            llm_description=None,
        )
        for hour in range(9, 18)
    ]
    calls: list = []

    # WHY: the LLM server is the external boundary; here it must not be reached at all.
    with patch("immich_memories.analysis.llm_query.query_llm", new=_recorder(calls, _VERDICT)):
        verdict = ask_if_special(bare, llm_config=SimpleNamespace())

    assert calls == [], "a day with nothing to read is not worth a live call"
    assert verdict.judged is False
    assert verdict.special is False


def test_one_stray_geotag_is_not_a_day_worth_reading() -> None:
    """The bar is three lines that say something, not one."""
    day = _a_day(described=False)
    for asset in day[1:]:
        asset.exif_info = None
    calls: list = []

    # WHY: the LLM server is the external boundary; here it must not be reached at all.
    with patch("immich_memories.analysis.llm_query.query_llm", new=_recorder(calls, _VERDICT)):
        verdict = ask_if_special(day, llm_config=SimpleNamespace())

    assert calls == []
    assert verdict.judged is False

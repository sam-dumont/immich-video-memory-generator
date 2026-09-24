"""A custom date range with no written subject is a film of its window, not of boilerplate."""

from datetime import datetime

from immich_memories.analysis.editorial_block_votes import (
    WORTH_CRITERION_V44,
    worth_criterion_v44,
)
from immich_memories.analysis.editorial_intent import build_editorial_intent
from immich_memories.analysis.editorial_product_brief import build_editorial_brief
from immich_memories.timeperiod import DateRange

_WINDOW = (DateRange(datetime(2024, 3, 10), datetime(2024, 5, 20, 23, 59, 59)),)


def _intent(base=None):
    brief = build_editorial_brief("custom", _WINDOW, base=base)
    return build_editorial_intent("custom", _WINDOW, brief=brief)


def test_a_plain_custom_range_asks_its_questions_about_the_window():
    intent = _intent()

    assert intent.subject is None
    assert "binding subject" not in intent.prompt_block()
    assert "2024-03-10..2024-05-20" in intent.prompt_block()
    assert "requested subject" not in intent.prompt_block() + intent.story_prompt_block()
    assert worth_criterion_v44("custom", intent.subject) == (WORTH_CRITERION_V44, "")


def test_a_written_subject_still_binds_the_custom_film():
    intent = _intent("Follow the red bicycle across the spring.")

    assert intent.subject == "Follow the red bicycle across the spring."
    assert "binding subject: Follow the red bicycle" in intent.prompt_block()
    assert "requested subject" in intent.story_prompt_block()
    assert worth_criterion_v44("custom", intent.subject)[1] == "subject-v1"

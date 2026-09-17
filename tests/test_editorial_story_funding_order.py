"""The order stories are funded in, when there are more of them than the film has slots."""

from immich_memories.analysis.editorial_story_planner import funding_order
from immich_memories.analysis.editorial_story_slots import allocate_slots


def story(key, *, day, weight="major", gate="remarkable", moments=1, favourites=0):
    return {
        "key": key,
        "weight": weight,
        "gate": gate,
        "first_day": day,
        "seen": {"days": 1, "moments": moments, "pictures": moments, "favourites": favourites},
    }


def test_a_star_does_not_buy_its_story_a_slot_the_earlier_story_had():
    """28 remarkable stories, 19 slots: the year's first story is funded although a December one
    holds five of the owner's stars."""
    year = [story(f"S{n:02d}", day=f"2024-{n // 3 + 1:02d}-{n % 3 + 1:02d}") for n in range(27)]
    starred = story("S27", day="2024-12-20", favourites=5)

    ordered = funding_order([starred, *year])
    granted = allocate_slots(ordered, 19, dict.fromkeys((s["key"] for s in ordered), 1))

    funded = {key for key, count in granted.items() if count}
    assert granted["S00"] == 1
    assert funded == {f"S{n:02d}" for n in range(19)}  # the nineteen earliest, stars or not


def test_equal_stories_fund_in_time_order():
    stories = [
        story("june", day="2024-06-02", favourites=4),
        story("february", day="2024-02-11"),
        story("september", day="2024-09-30", favourites=1),
    ]

    assert [s["key"] for s in funding_order(stories)] == ["february", "june", "september"]


def test_the_weight_word_and_the_gate_still_come_before_chronology():
    stories = [
        story("december-minor", day="2024-12-01", weight="minor"),
        story("november-background", day="2024-11-01", gate="background"),
        story("october-major", day="2024-10-01"),
    ]

    assert [s["key"] for s in funding_order(stories)] == [
        "october-major",
        "november-background",
        "december-minor",
    ]

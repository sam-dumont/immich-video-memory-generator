"""A rules story is a week at home or a whole trip away, and its grant covers its span."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from immich_memories.analysis.editorial_home_radius import HOME_RADIUS_KM, home_of, near_home_of
from immich_memories.analysis.editorial_rule_reader import RuleStructureReader
from immich_memories.analysis.editorial_story_shortlist import (
    DepictedChoice,
    shortlist_story_moments,
)
from immich_memories.analysis.editorial_story_weighing import consecutive_runs

HOME = (50.8468, 4.3525)
AWAY = (41.9028, 12.4964)


def _reader(days, *, place, home=HOME):
    """One moment a day for `days` days, all at `place`, with a homebase at `home`."""
    assets, moments, gps = {}, {}, {}
    for offset in range(days):
        asset_id = f"a{offset:03}"
        assets[asset_id] = SimpleNamespace(
            id=asset_id,
            file_created_at=datetime(2024, 6, 3) + timedelta(days=offset, hours=9),
            is_favorite=False,
            is_video=False,
            exif_info=SimpleNamespace(city="somewhere"),
            people=[],
        )
        moments[f"M{offset:03}"] = (asset_id,)
        gps[asset_id] = place
    source = SimpleNamespace(
        assets=assets,
        moment_asset_ids=moments,
        gps=gps,
        annotations={},
        audience_annotations={},
        config=SimpleNamespace(
            trips=SimpleNamespace(homebase_latitude=home[0], homebase_longitude=home[1])
        ),
        intent=SimpleNamespace(product="monthly_highlights"),
    )
    return RuleStructureReader(source)


def _day_of(reader, episode):
    asset_id = reader.source.moment_asset_ids[episode.moments[0]][0]
    return reader.source.assets[asset_id].file_created_at.date().isoformat()


def _stories_of(reader):
    episodes = reader._day_episodes([])
    hints = {e.key: {"day": _day_of(reader, e)} for e in episodes}
    return reader._stories(episodes, hints), hints


def test_thirty_days_at_home_become_one_story_a_week_not_one_story_a_month():
    stories, hints = _stories_of(_reader(30, place=HOME))
    weeks = []
    for story in stories:
        days = sorted(date.fromisoformat(hints[k]["day"]) for k in story["episodes"])
        weeks.append({d.isocalendar()[:2] for d in days})
    assert len(stories) == 5
    assert all(len(w) == 1 for w in weeks)


def test_thirty_days_away_stay_one_story_because_a_trip_is_one_story():
    stories, _ = _stories_of(_reader(30, place=AWAY))
    assert len(stories) == 1


def test_an_unset_homebase_is_unknown_so_home_days_still_chunk_by_week():
    stories, _ = _stories_of(_reader(30, place=AWAY, home=(0.0, 0.0)))
    assert len(stories) == 5


def test_a_gap_of_more_than_a_day_still_starts_a_new_story():
    reader = _reader(3, place=HOME)
    # the third day moves a fortnight out, inside no run of the first two
    late = reader.source.assets["a002"]
    late.file_created_at = late.file_created_at + timedelta(days=14)
    stories, _ = _stories_of(reader)
    assert len(stories) == 2


def test_two_trips_either_side_of_a_week_at_home_are_two_stories():
    """A day at home ends a trip: otherwise every away day of a summer is one story."""
    reader = _reader(21, place=HOME)
    for offset in (*range(0, 5), *range(14, 21)):
        reader.source.gps[f"a{offset:03}"] = AWAY
    episodes = reader._day_episodes([])
    hints = {e.key: {"day": _day_of(reader, e)} for e in episodes}
    away_episode = {
        e.key: reader.source.gps[reader.source.moment_asset_ids[e.moments[0]][0]] == AWAY
        for e in episodes
    }
    stories = reader._stories(episodes, hints)
    away_sizes = sorted(
        len(s["episodes"]) for s in stories if all(away_episode[k] for k in s["episodes"])
    )
    assert away_sizes == [5, 7]


def test_a_day_episode_with_no_dated_unit_does_not_break_the_run():
    reader = _reader(3, place=HOME)
    episodes = reader._day_episodes([])
    hints = {e.key: {"day": ""} for e in episodes}
    hints[episodes[0].key]["day"] = "2024-06-03"
    assert len(reader._stories(episodes, hints)) == 1


def test_model_mode_day_runs_are_untouched_by_the_rules_chunking():
    """`consecutive_runs` is the model reader's grouping; only the rules reader chunks."""
    days = {f"R{n:03}": (date(2024, 6, 3) + timedelta(days=n)).isoformat() for n in range(30)}
    assert consecutive_runs(list(days), days.get) == [list(days)]


@pytest.mark.parametrize(
    ("home", "point", "expected"),
    [(HOME, HOME, True), (HOME, AWAY, False), (None, AWAY, None), (HOME, None, None)],
)
def test_near_home_answers_unknown_rather_than_guessing(home, point, expected):
    assert near_home_of(home, [point] if point else []) is expected


def test_the_default_homebase_is_not_a_coordinate():
    """(0, 0) is the unset default, not Null Island: an install that never named a
    home must read as unknown, never as 'every happening is away'."""
    assert home_of(SimpleNamespace(homebase_latitude=0.0, homebase_longitude=0.0)) is None
    assert home_of(SimpleNamespace(homebase_latitude=HOME[0], homebase_longitude=HOME[1])) == HOME
    assert HOME_RADIUS_KM == 10.0


def test_a_story_richer_in_favourites_than_its_shortlist_is_sampled_across_its_span():
    choices = [
        DepictedChoice(
            key=f"c{n:03}",
            episode="S001",
            taken=(datetime(2024, 6, 1) + timedelta(days=n)).isoformat(),
            content="capture group",
            primary=f"a{n:03}",
        )
        for n in range(120)
    ]
    picked = shortlist_story_moments(choices, 5, starred=lambda _c: True, life=lambda _a: False)
    assert len(picked) == 15
    assert picked[0] is choices[0]
    assert picked[-1] is choices[-1]
    # the earliest fifteen would all fall inside the story's first fortnight
    assert picked[-1].taken > choices[14].taken

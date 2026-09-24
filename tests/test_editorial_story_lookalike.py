"""Inside one story, a further frame is kept only if it does not look like one already kept.

The look is the preview hash ingest cached: no tier sends the pair's pixels to a model.
"""

import hashlib
import json
from collections import Counter
from datetime import date, timedelta

from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_planner import plan_structure
from tests.editorial_film_fixtures import Day, FilmJudge, film_source, home_days, trip_days

MAY = (date(2030, 5, 1), date(2030, 5, 31))


def _film(tmp_path):
    """A five-day trip among twenty home days: the trip is the only story with depth."""
    days = [
        *home_days(date(2030, 5, 1), 8),
        *trip_days(date(2030, 5, 10), 5),
        *home_days(date(2030, 5, 19), 12),
    ]
    return film_source(tmp_path, days, seconds=60, span=MAY)


def _apart(asset_id):
    """A cached preview hash of its own: far from every other picture's."""
    return hashlib.sha256(asset_id.encode()).hexdigest()[:16]


def _alike(_asset_id):
    return "0f0f0f0f0f0f0f0f"


class TwinOf:
    """Every picture hashes apart, except one that caches the same preview hash as another."""

    def __init__(self, first, repeat):
        self.first, self.repeat = first, repeat

    def __call__(self, asset_id):
        return _apart(self.first if asset_id == self.repeat else asset_id)


def _run(source, thumbnail_hash, *, model=True):
    return plan_structure(
        source,
        StructurePlannerPorts(
            judge=FilmJudge(),
            thumbnail_hash=thumbnail_hash,
            observe_picture=(
                (lambda asset_id: {"status": "available", "description": asset_id})
                if model
                else None
            ),
        ),
    ).plan


def _trip(plan):
    row = next(row for row in plan["story"]["episodes"] if row.get("kind") == "trip")
    carried = sorted(
        c["asset_id"] for c in plan["carriers"] if c["story_episode"] == row["episode"]
    )
    return row, carried


def _lookalike_record(source):
    record = json.loads(next(source.artifact_dir.rglob("story-selection.private.json")).read_text())
    return record["lookalike"]


def test_a_second_frame_that_looks_like_the_first_is_replaced_by_a_distinct_moment(tmp_path):
    baseline_row, baseline = _trip(_run(_film(tmp_path / "plain"), _apart))
    first, repeat = baseline[0], baseline[1]

    source = _film(tmp_path / "checked")
    plan = _run(source, TwinOf(first, repeat))

    row, carried = _trip(plan)
    assert first in carried
    assert repeat not in carried
    assert len(carried) == len(baseline) == baseline_row["granted"]
    record = _lookalike_record(source)
    assert [(r["asset_id"], r["repeats"]) for r in record["refused"]] == [(repeat, first)]
    assert record["checks"] <= record["limit"] == 2 * plan["story"]["slots"]


def test_the_check_alone_never_leaves_the_film_short(tmp_path):
    plain = _run(_film(tmp_path / "plain"), _apart)
    source = _film(tmp_path / "all-alike")
    plan = _run(source, _alike)

    _row, carried = _trip(plan)
    assert len(plan["carriers"]) == len(plain["carriers"])
    record = _lookalike_record(source)
    assert record["refused"], "every further trip frame looked alike"
    assert {r["asset_id"] for r in record["readmitted"]} <= set(carried)


def test_a_film_with_a_model_answers_the_look_the_same_way_a_film_without_one_does(tmp_path):
    with_model = _film(tmp_path / "model")
    without = _film(tmp_path / "rules")
    _run(with_model, _alike)
    _run(without, _alike, model=False)

    assert _lookalike_record(with_model)["status"] == "asked"
    assert _lookalike_record(with_model)["refused"] == _lookalike_record(without)["refused"]


def _afternoon(tmp_path):
    """One dense afternoon: two capture groups of six pictures, half a minute apart."""
    day = Day(date(2030, 5, 12), "Birthday afternoon in the garden", moments=2)
    return film_source(
        tmp_path, [day], seconds=40, span=MAY, pictures=6, picture_gap=timedelta(seconds=30)
    )


def test_a_one_occasion_film_spends_its_free_slots_as_depth_inside_its_moments(tmp_path):
    source = _afternoon(tmp_path)
    plan = _run(source, _apart)

    assert plan["story"]["slots"] == 10
    assert len(plan["carriers"]) == 10  # a moment admitted as depth earns its own rungs too
    per_group = Counter(c["asset_id"].rsplit("-", 1)[0] for c in plan["carriers"])
    assert sorted(per_group.values()) == [5, 5]
    record = _lookalike_record(source)
    assert record["depth"]["added"] == 8


def _group_alike(asset_id):
    """Every frame of one capture group caches its group's hash; the two groups differ."""
    return _apart(asset_id.rsplit("-", 1)[0])


def test_depth_takes_only_frames_that_look_different(tmp_path):
    source = _afternoon(tmp_path)
    plan = _run(source, _group_alike)

    assert len(plan["carriers"]) == 2
    record = _lookalike_record(source)
    assert record["depth"]["added"] == 0
    # One frame per capture group: the rest cached the same preview hash as the one kept.
    assert sorted(c["asset_id"].rsplit("-", 1)[0] for c in plan["carriers"]) == [
        "d000-m0",
        "d000-m1",
    ]

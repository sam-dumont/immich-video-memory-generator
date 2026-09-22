"""Inside one story, a further frame is kept only if it does not look like one already kept."""

import json
from collections import Counter
from datetime import date, timedelta

from immich_memories.analysis.editorial_story_lookalike import hash_pair_relation, hash_then_model
from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_planner import plan_structure
from immich_memories.analysis.selection_same_picture import SamePicturePairDecision
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


def _never(*_pair):
    return False


def _always(*_pair):
    return True


class PairAnswers:
    """The visual similarity answer for a pair, and the picture observations it needs."""

    def __init__(self, alike):
        self.alike = alike
        self.asked = []

    def observe(self, asset_id):
        # WHY: stands in for the vision model's own-picture observation of one carrier.
        return {"status": "available", "description": f"A clothed person, {asset_id}."}

    def confirm(self, pairs, _records, corroborating_distances=None):
        # WHY: stands in for the two-arrangement visual similarity answer of the reader.
        self.asked.extend(pairs)
        return (
            tuple(SamePicturePairDecision(a, b, self.alike(a, b)) for a, b in pairs),
            {"scope": "test"},
        )


def _run(source, answers):
    return plan_structure(
        source,
        StructurePlannerPorts(
            judge=FilmJudge(),
            thumbnail_hash=lambda _: None,
            rank=lambda _query, documents: dict.fromkeys(range(len(documents)), 1.0),
            reranker_identity={"endpoint": "test://local", "model": "controlled-ranker"},
            observe_picture=answers.observe,
            confirm_episode_pairs=answers.confirm,
            confirm_story_pairs=answers.confirm,
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
    baseline_row, baseline = _trip(_run(_film(tmp_path / "plain"), PairAnswers(_never)))
    first, repeat = baseline[0], baseline[1]

    source = _film(tmp_path / "checked")
    answers = PairAnswers(lambda a, b: {a, b} == {first, repeat})
    plan = _run(source, answers)

    row, carried = _trip(plan)
    assert first in carried
    assert repeat not in carried
    assert len(carried) == len(baseline) == baseline_row["granted"]
    record = _lookalike_record(source)
    assert [(r["asset_id"], r["repeats"]) for r in record["refused"]] == [(repeat, first)]
    assert record["checks"] <= record["limit"] == 2 * plan["story"]["slots"]
    assert all({a, b} <= set(baseline) | set(carried) for a, b in answers.asked)


def test_the_check_alone_never_leaves_the_film_short(tmp_path):
    plain = _run(_film(tmp_path / "plain"), PairAnswers(_never))
    source = _film(tmp_path / "all-alike")
    plan = _run(source, PairAnswers(_always))

    _row, carried = _trip(plan)
    assert len(plan["carriers"]) == len(plain["carriers"])
    record = _lookalike_record(source)
    assert record["refused"], "every further trip frame looked alike"
    assert {r["asset_id"] for r in record["readmitted"]} <= set(carried)


def _afternoon(tmp_path):
    """One dense afternoon: two capture groups of six pictures, half a minute apart."""
    day = Day(date(2030, 5, 12), "Birthday afternoon in the garden", moments=2)
    return film_source(
        tmp_path, [day], seconds=40, span=MAY, pictures=6, picture_gap=timedelta(seconds=30)
    )


def test_a_one_occasion_film_spends_its_free_slots_as_depth_inside_its_moments(tmp_path):
    source = _afternoon(tmp_path)
    plan = _run(source, PairAnswers(_never))

    assert plan["story"]["slots"] == 10
    assert len(plan["carriers"]) == 10  # a moment admitted as depth earns its own rungs too
    per_group = Counter(c["asset_id"].rsplit("-", 1)[0] for c in plan["carriers"])
    assert sorted(per_group.values()) == [5, 5]
    record = _lookalike_record(source)
    assert record["depth"]["added"] == 8


def test_depth_takes_only_frames_that_look_different(tmp_path):
    source = _afternoon(tmp_path)
    plan = _run(source, PairAnswers(_always))

    assert len(plan["carriers"]) == 2
    record = _lookalike_record(source)
    assert record["depth"]["added"] == 0
    refused = {row["asset_id"] for row in record["depth"]["refused"]}
    assert refused, "every further frame of the afternoon looked alike"
    assert not refused & {c["asset_id"] for c in plan["carriers"]}


def _frame(asset_id):
    return {
        "asset_id": asset_id,
        "taken": "2030-05-12T10:00:00+00:00",
        "story_episode": "one-afternoon",
        "kind": "still",
    }


class _Reader:
    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def __call__(self, candidate, keeper):
        # WHY: stands in for the reader that would look at both pictures.
        self.asked.append((candidate["asset_id"], keeper["asset_id"]))
        return self.answer


def test_a_pair_the_cached_previews_call_alike_costs_the_model_nothing():
    reader = _Reader(False)
    same = dict.fromkeys(("a", "b"), "0f0f0f0f0f0f0f0f")

    relation = hash_then_model(hash_pair_relation(same.get), reader)

    assert relation(_frame("a"), _frame("b")) is True
    assert reader.asked == []


def test_a_pair_the_cached_previews_do_not_settle_is_still_the_model_s_question():
    reader = _Reader(True)
    apart = {"a": "0000000000000000", "b": "ffffffffffffffff"}

    relation = hash_then_model(hash_pair_relation(apart.get), reader)

    assert relation(_frame("a"), _frame("b")) is True
    assert reader.asked == [("a", "b")]

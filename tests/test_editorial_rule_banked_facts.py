"""The rules draft reads what a model already answered, and still asks nothing.

Every assertion goes through the draft's public surface: the capture-group picker that decides
which picture carries a moment and which pictures are offered at all.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from immich_memories.analysis.editorial_block_votes import standing_row_name
from immich_memories.analysis.editorial_rule_banked_facts import (
    NO_BANKED_FACTS,
    banked_leaders,
    banked_weak,
    open_banked_facts,
    standing_with_bank,
    withheld_by_bank,
)
from immich_memories.analysis.editorial_rule_quality import rule_representative_rank
from immich_memories.analysis.editorial_story_shortlist import _capture_group_moments

CONTRACT = "the editorial contract"
PERIOD = "June 2023"
IDENTITY = "reader-a"


def _asset(asset_id, *, minute=0, favourite=False, people=()):
    return SimpleNamespace(
        id=asset_id,
        is_favorite=favourite,
        is_video=False,
        people=list(people),
        faces=[],
        file_created_at=datetime(2023, 6, 1, 10, minute, tzinfo=UTC),
        exif_info=None,
    )


def _units(ids, *, favourites=()):
    return [
        {
            "asset_id": a,
            "moment": "M01",
            "taken": f"2023-06-01T10:0{n}:00",
            "favourite": a in favourites,
        }
        for n, a in enumerate(ids)
    ]


def _draft(assets, *, banked, favourites=()):
    """The draft's offer for one moment: the primary first, then its alternatives."""
    ids = tuple(assets)

    def starred(asset_id: str) -> bool:
        return asset_id in favourites

    choices = _capture_group_moments(
        _units(ids, favourites=favourites),
        quality=lambda _a: 1.0,
        rank=rule_representative_rank(
            assets,
            {},
            {},
            weak=banked_weak(banked, ids, favourite=starred),
            leads=banked_leaders(banked, ("M01",)),
        ),
        withhold=withheld_by_bank(banked, favourite=starred),
    )
    return [choices[0].primary, *choices[0].alternatives]


class _Banked:
    """A bank stub standing in for banks this test does not need to build on disk."""

    def __init__(self, *, standing=(), refused=(), representatives=(), culled=()):
        self._standing = dict(standing)
        self._refused = frozenset(refused)
        self._representatives = tuple(representatives)
        self._culled = frozenset(culled)

    def standing_of(self, asset_id):
        return self._standing.get(asset_id)

    def refused_for_audience(self, asset_id):
        return asset_id in self._refused

    def episode_representatives(self, episode_key):
        return self._representatives

    def culled(self, asset_id):
        return asset_id in self._culled

    def record_owning(self, episode_key):
        return ()


def test_a_library_nothing_has_read_draws_the_draft_it_always_drew():
    assets = {
        "first": _asset("first", minute=0),
        "best": _asset("best", minute=1, people=["a", "b"]),
        "last": _asset("last", minute=2),
    }

    cold = _capture_group_moments(
        _units(("first", "best", "last")),
        quality=lambda _a: 1.0,
        rank=rule_representative_rank(assets, {}, {}),
    )

    assert _draft(assets, banked=NO_BANKED_FACTS) == [cold[0].primary, *cold[0].alternatives]


def test_a_picture_a_model_refused_standing_does_not_carry_its_moment():
    assets = {"weak": _asset("weak", minute=0, people=["a", "b"]), "next": _asset("next", minute=1)}

    offered = _draft(assets, banked=_Banked(standing={"weak": 0}))

    assert offered[0] == "next"


def test_the_owner_s_favourite_keeps_its_moment_against_a_banked_refusal():
    assets = {
        "star": _asset("star", minute=0, favourite=True),
        "other": _asset("other", minute=1, people=["a", "b"]),
    }

    offered = _draft(
        assets,
        banked=_Banked(standing={"star": 0}, refused=("star",), culled=("star",)),
        favourites=("star",),
    )

    assert offered[0] == "star"


def test_a_picture_refused_for_the_audience_is_not_offered_at_all():
    assets = {
        "held": _asset("held", minute=0, people=["a", "b"]),
        "clean": _asset("clean", minute=1),
    }

    assert _draft(assets, banked=_Banked(refused=("held",))) == ["clean"]


def test_a_culled_picture_is_not_offered_at_all():
    assets = {"culled": _asset("culled", minute=0, people=["a"]), "kept": _asset("kept", minute=1)}

    assert _draft(assets, banked=_Banked(culled=("culled",))) == ["kept"]


def test_a_moment_whose_every_picture_was_refused_keeps_them_for_the_gates():
    assets = {"one": _asset("one", minute=0), "two": _asset("two", minute=1)}

    assert sorted(_draft(assets, banked=_Banked(refused=("one", "two")))) == ["one", "two"]


def test_a_banked_reading_s_representative_leads_its_episode():
    assets = {
        "named": _asset("named", minute=0),
        "livelier": _asset("livelier", minute=1, people=["a", "b"]),
    }

    offered = _draft(assets, banked=_Banked(representatives=("named",)))

    assert offered[0] == "named"


def test_the_rules_standing_answer_stands_where_nothing_was_banked():
    standing = standing_with_bank(
        lambda _asset: 1, _Banked(standing={"answered": 0}), favourite=lambda _a: False
    )

    assert (standing("answered"), standing("unanswered")) == (0, 1)


def _write_standing_bank(path: Path, rows: dict[str, int], *, identity=IDENTITY, motion=""):
    names = {
        standing_row_name(
            row, contract=CONTRACT, period_label=PERIOD, identity=identity, motion_identity=motion
        ): {"votes": votes, "why": ""}
        for row, votes in rows.items()
    }
    path.write_text(json.dumps({"rows": names}))


def _open(tmp_path, **overrides):
    return open_banked_facts(
        **{
            "bank_dir": tmp_path,
            "attempts_dir": None,
            "store_path": None,
            "audience": "family",
            "model_identity": IDENTITY,
            "contract": CONTRACT,
            "period_label": PERIOD,
            "motion_identity": "",
            "rows_of": {},
            "episode_cards": {},
            **overrides,
        }
    )


def test_a_standing_answer_is_read_back_under_the_name_the_asking_side_wrote_it_under(tmp_path):
    _write_standing_bank(tmp_path / "picture-stands.private.json", {"a row about a picture": 2})

    banked = _open(tmp_path, rows_of={"pic": "a row about a picture"})

    assert banked.standing_of("pic") == 0


def test_a_standing_answer_from_another_reader_is_not_read_as_this_one_s(tmp_path):
    _write_standing_bank(
        tmp_path / "picture-stands.private.json", {"a row about a picture": 2}, identity="reader-b"
    )

    banked = _open(tmp_path, rows_of={"pic": "a row about a picture"})

    assert banked.standing_of("pic") is None


def test_a_missing_standing_bank_answers_nothing(tmp_path):
    banked = _open(tmp_path, rows_of={"pic": "a row about a picture"})

    assert banked.standing_of("pic") is None


def _write_cut(attempts: Path, name: str, *, audience: str, verdicts: dict[str, str]):
    attempt = attempts / name
    attempt.mkdir(parents=True)
    (attempt / "plan.private.json").write_text(
        json.dumps(
            {
                "shareability": {
                    "audience": audience,
                    "verdicts": {a: {"verdict": v} for a, v in verdicts.items()},
                }
            }
        )
    )


def test_a_picture_an_earlier_cut_refused_for_this_audience_is_refused_again(tmp_path):
    _write_cut(
        tmp_path / "attempts",
        "01",
        audience="family",
        verdicts={"held": "do_not_show", "fine": "share"},
    )

    banked = _open(tmp_path, attempts_dir=tmp_path / "attempts")

    assert (banked.refused_for_audience("held"), banked.refused_for_audience("fine")) == (
        True,
        False,
    )


def test_a_refusal_cast_for_another_audience_is_not_carried_over(tmp_path):
    _write_cut(tmp_path / "attempts", "01", audience="sendable", verdicts={"held": "family_only"})

    banked = _open(tmp_path, attempts_dir=tmp_path / "attempts")

    assert banked.refused_for_audience("held") is False


def _write_readings(store: Path, rows):
    connection = sqlite3.connect(store)
    connection.execute(
        "CREATE TABLE editorial_episode_readings (group_id TEXT, producer_key TEXT, "
        "evidence_key TEXT, full_asset_ids TEXT, what_happened TEXT, representatives TEXT, "
        "cull_decisions TEXT, answered_at TEXT)"
    )
    connection.executemany(
        "INSERT INTO editorial_episode_readings VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
    connection.commit()
    connection.close()


@pytest.fixture
def read_store(tmp_path):
    store = tmp_path / "annotations.sqlite"
    _write_readings(
        store,
        [
            (
                "G01",
                "a-model-that-is-not-the-rules-reader",
                "E01",
                '["lead","dropped"]',
                "something happened",
                '[{"asset_id":"lead","reason":"carries it"}]',
                '[{"asset_id":"dropped","bucket":"alike"}]',
                "2026-09-01",
            )
        ],
    )
    return store


def test_a_reading_by_another_producer_still_names_this_episode_s_representative(
    tmp_path, read_store
):
    cards = {"M01": SimpleNamespace(episode_id="G01", evidence_key="E01")}

    banked = _open(tmp_path, store_path=read_store, episode_cards=cards)

    assert banked.episode_representatives("M01") == ("lead",)


def test_a_reading_s_culled_picture_is_culled_for_the_draft_too(tmp_path, read_store):
    cards = {"M01": SimpleNamespace(episode_id="G01", evidence_key="E01")}

    banked = _open(tmp_path, store_path=read_store, episode_cards=cards)

    assert (banked.culled("dropped"), banked.culled("lead")) == (True, False)


def test_a_run_is_not_handed_its_own_reading_back_as_a_banked_one(tmp_path, read_store):
    cards = {"M01": SimpleNamespace(episode_id="G01", evidence_key="E01")}

    banked = _open(
        tmp_path,
        store_path=read_store,
        episode_cards=cards,
        own_producers=frozenset({"a-model-that-is-not-the-rules-reader"}),
    )

    assert (banked.episode_representatives("M01"), banked.culled("dropped")) == ((), False)


def test_a_reading_of_other_evidence_is_not_this_episode_s_reading(tmp_path, read_store):
    cards = {"M01": SimpleNamespace(episode_id="G01", evidence_key="the-pictures-changed")}

    banked = _open(tmp_path, store_path=read_store, episode_cards=cards)

    assert banked.episode_representatives("M01") == ()


def test_nothing_owns_an_episode_s_record_until_the_bank_carries_the_field(tmp_path, read_store):
    cards = {"M01": SimpleNamespace(episode_id="G01", evidence_key="E01")}

    banked = _open(tmp_path, store_path=read_store, episode_cards=cards)

    assert banked.record_owning("M01") == ()
